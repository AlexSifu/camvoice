"""Hilo de audio: captura -> remuestreo -> VAD -> histéresis -> estado publicado.

El resto de la aplicación no toca audio: lee `AudioPipeline.status()`, que
devuelve una instantánea inmutable. Así el hilo de render nunca se bloquea
esperando al micrófono, y la UI puede refrescar a su propio ritmo.

Recuperación: si el dispositivo desaparece (USB desconectado, cambio de
predeterminado en Windows), el hilo lo detecta, fuerza el estado a SILENT,
publica un mensaje legible y reintenta abrir con espera creciente.

Eco de la reunión: si hay una referencia de la mezcla de salida (`echo.py`),
cada trama que el VAD marca como voz se contrasta con ella antes de llegar a
la histéresis; las tramas que son con alta probabilidad reproducción remota
captada por el micrófono se vetan. La referencia es opcional y su fallo nunca
afecta a la captura del micrófono.

Diagnóstico opt-in: con la variable de entorno `CAMVOICE_AUDIO_DEBUG=1` se
registran por `logging` («camvoice.audio») dispositivos, backend, estado de la
referencia y cada cambio de clasificación de eco. Nunca se registra audio.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace

import numpy as np

from ..config import AudioConfig, AppConfig, VadConfig
from .capture import AudioCapture, AudioError
from .echo import EchoEstimator, EchoReference, EchoState, find_reference_device, looks_like_system_mix
from .features import MicroPauseDetector, MouthEnvelope
from .gate import GateSnapshot, SpeechGate, VoiceState
from .resample import Resampler, float_to_int16
from .vad import FrameDecision, build_detector

__all__ = ["VoiceStatus", "AudioPipeline"]

# Campos cuyo cambio obliga a reconstruir el detector; el resto son umbrales
# que el detector lee del mismo objeto VadConfig y surten efecto al instante.
_STRUCTURAL_VAD_FIELDS = ("backend", "frame_ms", "aggressiveness")

_log = logging.getLogger("camvoice.audio")
_AUDIO_DEBUG = os.environ.get("CAMVOICE_AUDIO_DEBUG", "").strip() not in ("", "0", "false", "no")
if _AUDIO_DEBUG and not _log.handlers:  # pragma: no cover - solo con la variable
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False


@dataclass(frozen=True, slots=True)
class VoiceStatus:
    """Instantánea del estado del detector, segura de leer desde otro hilo."""

    state: VoiceState = VoiceState.SILENT
    voiced: bool = False
    rms_db: float = -120.0
    noise_floor_db: float = -120.0
    transitions: int = 0
    frames: int = 0
    backend: str = "-"
    calibrating: bool = False
    signal_lost: bool = False
    running: bool = False
    device_name: str = ""
    samplerate: int = 0
    resampled_from: int = 0
    overflows: int = 0
    dropped_blocks: int = 0
    error: str = ""
    expected_latency_ms: int = 0
    mouth_target: float = 0.0
    """Apertura de boca objetivo 0..1 derivada de la energía de la voz.

    Cae a 0 en cuanto la trama deja de ser voz, aunque `state` siga en
    SPEAKING: es lo que permite cerrar la boca en las pausas cortas."""

    micro_pause: bool = False
    """True durante una micro-pausa (>= `micropause_release_ms` sin voz) sin
    haber salido de SPEAKING: boca cerrada y pose de escucha."""

    t_monotonic: float = 0.0
    """`time.perf_counter()` del final de la última trama analizada. Permite
    al motor estimar la antigüedad del dato (latencia audio -> video)."""

    mic_is_system_mix: bool = False
    """El micrófono elegido es una mezcla del sistema o una entrada virtual:
    todo lo que suena en el equipo cuenta como voz. Se avisa, no se cambia."""

    echo_reference: str = ""
    """Dispositivo de referencia de eco en uso, o cadena vacía si no hay."""

    echo_reference_error: str = ""
    """Por qué no hay referencia (sin dispositivo, no se pudo abrir, se perdió)."""

    echo_state: str = EchoState.OFF.value
    """Clasificación de la última trama: off | idle | echo | double | local."""

    echo_lag_ms: int = 0
    echo_coherence: float = 0.0
    echo_vetoes: int = 0
    """Tramas que el VAD dio por voz y se descartaron por ser eco remoto."""

    @property
    def snr_db(self) -> float:
        return self.rms_db - self.noise_floor_db

    @property
    def hint(self) -> str:
        """Aviso accionable, o cadena vacía si no hay nada que decir."""
        if self.error:
            return self.error
        if self.signal_lost:
            return (
                f"El micrófono «{self.device_name}» no entrega señal audible "
                "desde hace varios segundos. Comprueba que no está silenciado "
                "en Windows (Configuración > Sistema > Sonido > Entrada) y que "
                "el volumen de entrada no está a cero."
            )
        if self.mic_is_system_mix:
            return (
                f"El dispositivo «{self.device_name}» es una mezcla del sistema o "
                "una entrada virtual: el audio de los demás participantes se "
                "tomará por tu voz. Elige el micrófono físico en Audio."
            )
        return ""


class AudioPipeline:
    POLL_SLEEP_S = 0.004
    REOPEN_BACKOFF_S = (0.5, 1.0, 2.0, 4.0)

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._audio_cfg: AudioConfig = cfg.audio
        self._vad_cfg: VadConfig = cfg.vad

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._lock = threading.Lock()
        self._status = VoiceStatus()
        self._rebuild_detector = True

        self._capture: AudioCapture | None = None
        self._resampler: Resampler | None = None
        self._detector = None
        self._gate = SpeechGate(self._vad_cfg, self._vad_cfg.frame_ms)
        self._mouth = MouthEnvelope(self._vad_cfg.frame_ms)
        self._pause = MicroPauseDetector(
            self._vad_cfg.frame_ms, getattr(self._vad_cfg, "micropause_release_ms", 200)
        )
        self._pending = np.zeros(0, dtype=np.float32)
        self._frames = 0

        # Referencia de eco (opcional). `_echo_est` existe solo con referencia.
        self._echo_ref: EchoReference | None = None
        self._echo_est: EchoEstimator | None = None
        self._echo_ref_index = 0
        """Índice de trama (eje del micrófono) de la próxima trama de referencia."""
        self._echo_anchored = False
        self._echo_last_state = EchoState.OFF
        self._echo_last_log = 0.0

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._restart.clear()
        self._thread = threading.Thread(target=self._run, name="camvoice-audio", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        self._publish(running=False, state=VoiceState.SILENT, mouth_target=0.0, micro_pause=False)

    def status(self) -> VoiceStatus:
        with self._lock:
            return self._status

    # ------------------------------------------------------------------ #
    # Ajustes en caliente
    # ------------------------------------------------------------------ #

    def apply_vad_changes(self, **changes) -> None:
        """Modifica parámetros del VAD sin reiniciar la captura.

        Los umbrales se aplican al instante porque el detector comparte el mismo
        objeto de configuración. Cambiar backend, ventana o agresividad obliga a
        reconstruirlo, lo que implica una recalibración de ~300 ms del piso de
        ruido; es la única forma honesta de hacerlo.
        """
        structural = False
        for key, value in changes.items():
            if not hasattr(self._vad_cfg, key):
                raise AttributeError(f"Parámetro de VAD desconocido: {key!r}")
            if getattr(self._vad_cfg, key) == value:
                continue
            setattr(self._vad_cfg, key, value)
            if key in _STRUCTURAL_VAD_FIELDS:
                structural = True
        if structural:
            self._rebuild_detector = True

    def set_device(self, index: int | None, name: str = "") -> None:
        """Cambia de micrófono en caliente."""
        self._audio_cfg.device = index
        self._audio_cfg.device_name = name
        self._restart.set()

    # ------------------------------------------------------------------ #
    # Hilo trabajador
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._open()
                attempt = 0
                self._loop()
            except AudioError as exc:
                self._teardown()
                self._publish(
                    running=False,
                    state=VoiceState.SILENT,
                    mouth_target=0.0,
                    error=str(exc),
                )
                backoff = self.REOPEN_BACKOFF_S[min(attempt, len(self.REOPEN_BACKOFF_S) - 1)]
                attempt += 1
                if self._stop.wait(backoff):
                    break
            except Exception as exc:  # pragma: no cover - defensivo
                self._teardown()
                self._publish(
                    running=False, state=VoiceState.SILENT, mouth_target=0.0,
                    error=f"Fallo inesperado en el hilo de audio: {exc}",
                )
                if self._stop.wait(2.0):
                    break
        self._teardown()

    def _open(self) -> None:
        self._teardown()
        capture = AudioCapture(self._audio_cfg)
        capture.start()
        self._capture = capture

        target = self._audio_cfg.target_samplerate
        self._resampler = Resampler(capture.samplerate, target)
        self._pending = np.zeros(0, dtype=np.float32)
        self._build_detector(target)
        self._gate = SpeechGate(self._vad_cfg, self._vad_cfg.frame_ms)
        self._mouth = MouthEnvelope(self._vad_cfg.frame_ms)
        self._pause = MicroPauseDetector(
            self._vad_cfg.frame_ms, getattr(self._vad_cfg, "micropause_release_ms", 200)
        )
        mix = looks_like_system_mix(capture.device_name)
        self._publish(
            running=True,
            error="",
            device_name=capture.device_name,
            samplerate=target,
            resampled_from=capture.samplerate,
            state=VoiceState.SILENT,
            calibrating=True,
            mic_is_system_mix=mix,
        )
        if _AUDIO_DEBUG:
            _log.info(
                "micrófono «%s» (índice %s) a %d Hz -> %d Hz, bloque %d ms, backend VAD %s%s",
                capture.device_name, capture.device_index, capture.samplerate, target,
                self._audio_cfg.block_ms, getattr(self._detector, "name", "?"),
                " [MEZCLA DEL SISTEMA: no es un micrófono]" if mix else "",
            )
        self._open_echo_reference(capture, target)

    def _open_echo_reference(self, capture: AudioCapture, target: int) -> None:
        """Abre la referencia de eco si está configurada y existe. Nunca lanza."""
        self._close_echo_reference()
        setting = getattr(self._audio_cfg, "echo_reference", "auto")
        try:
            device = find_reference_device(setting, exclude_index=capture.device_index)
        except Exception as exc:  # pragma: no cover - defensivo
            device = None
            self._publish(echo_reference_error=f"No se pudo buscar la referencia de eco: {exc}")
        if device is None:
            reason = (
                "referencia de eco desactivada" if (setting or "").lower() in ("", "off")
                else "ninguna entrada contiene la mezcla de salida del sistema "
                "(«Mezcla estéreo»/«Stereo Mix» desactivada o inexistente)"
                if (setting or "").lower() == "auto"
                else f"no se encontró el dispositivo de referencia «{setting}»"
            )
            self._publish(echo_reference="", echo_reference_error=reason, echo_state=EchoState.OFF.value)
            if _AUDIO_DEBUG:
                _log.info("sin referencia de eco: %s", reason)
            return
        try:
            ref = EchoReference(device, self._audio_cfg, target)
            ref.start()
        except Exception as exc:
            self._publish(
                echo_reference="",
                echo_reference_error=f"No se pudo abrir la referencia «{device.name}»: {exc}",
                echo_state=EchoState.OFF.value,
            )
            if _AUDIO_DEBUG:
                _log.info("referencia «%s» no disponible: %s", device.name, exc)
            return
        self._echo_ref = ref
        self._echo_est = EchoEstimator(self._vad_cfg.frame_ms)
        self._echo_ref_index = 0
        self._echo_anchored = False
        self._echo_last_state = EchoState.IDLE
        self._publish(
            echo_reference=device.name,
            echo_reference_error="",
            echo_state=EchoState.IDLE.value,
            echo_lag_ms=0,
            echo_coherence=0.0,
            echo_vetoes=0,
        )
        if _AUDIO_DEBUG:
            _log.info(
                "referencia de eco «%s» (%s, índice %d) a %d Hz -> %d Hz",
                device.name, device.hostapi, device.index, ref.samplerate, target,
            )

    def _close_echo_reference(self) -> None:
        ref, self._echo_ref = self._echo_ref, None
        self._echo_est = None
        if ref is not None:
            try:
                ref.stop()
            except Exception:  # pragma: no cover - defensivo
                pass

    def _drop_echo_reference(self, reason: str) -> None:
        """La referencia dejó de funcionar: se sigue sin ella, sin tocar el micrófono."""
        name = self._echo_ref.name if self._echo_ref is not None else ""
        self._close_echo_reference()
        self._publish(
            echo_reference="",
            echo_reference_error=f"Referencia «{name}» perdida: {reason}" if name else reason,
            echo_state=EchoState.OFF.value,
        )
        if _AUDIO_DEBUG:
            _log.info("referencia de eco «%s» descartada: %s", name, reason)

    def _poll_echo_reference(self, frame_len: int) -> None:
        """Lee la referencia y registra sus niveles en el eje de tramas del micrófono."""
        ref, est = self._echo_ref, self._echo_est
        if ref is None or est is None:
            return
        try:
            if not ref.active:
                self._drop_echo_reference("el dispositivo dejó de entregar audio")
                return
            if ref.error:
                self._drop_echo_reference(ref.error)
                return
            levels = ref.read_levels(frame_len)
        except Exception as exc:
            self._drop_echo_reference(str(exc))
            return
        if not levels:
            return
        if not self._echo_anchored:
            # Primer lote: se alinea con las tramas ya consumidas del micrófono.
            # El desfase residual (latencias de ambos flujos) lo absorbe la
            # búsqueda de retardo del estimador.
            self._echo_ref_index = max(0, self._frames - len(levels))
            self._echo_anchored = True
        else:
            skew = (self._echo_ref_index + len(levels)) - self._frames
            if abs(skew) > est.MAX_LAG // 2:
                # Hueco o desborde en uno de los flujos: se vuelve a anclar en
                # vez de correlacionar contra una referencia obsoleta.
                self._echo_ref_index = max(0, self._frames - len(levels))
                if _AUDIO_DEBUG:
                    _log.info("referencia de eco realineada (desfase %d tramas)", skew)
        for level in levels:
            est.push_ref(self._echo_ref_index, level)
            self._echo_ref_index += 1

    def _build_detector(self, samplerate: int) -> None:
        self._detector = build_detector(self._vad_cfg, samplerate)
        self._rebuild_detector = False
        if self._echo_est is not None and self._echo_est.frame_ms != self._vad_cfg.frame_ms:
            self._echo_est = EchoEstimator(self._vad_cfg.frame_ms)
            self._echo_anchored = False

    def _teardown(self) -> None:
        self._close_echo_reference()
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()
        self._resampler = None
        self._pending = np.zeros(0, dtype=np.float32)

    def _loop(self) -> None:
        assert self._capture is not None and self._resampler is not None
        capture = self._capture
        target = self._audio_cfg.target_samplerate

        while not self._stop.is_set():
            if self._restart.is_set():
                self._restart.clear()
                return  # `_run` reabrirá con la configuración nueva
            if not capture.active:
                raise AudioError(
                    f"Se perdió la conexión con el micrófono «{capture.device_name}». "
                    "Comprueba que sigue conectado."
                )
            if capture.callback_error:
                raise AudioError(f"Error en la captura de audio: {capture.callback_error}")
            if self._rebuild_detector:
                self._build_detector(target)
                self._gate = SpeechGate(self._vad_cfg, self._vad_cfg.frame_ms)
                self._mouth = MouthEnvelope(self._vad_cfg.frame_ms)
                self._pause = MicroPauseDetector(
                    self._vad_cfg.frame_ms, getattr(self._vad_cfg, "micropause_release_ms", 200)
                )

            block = capture.read_all()
            if self._echo_ref is not None:
                # Antes de analizar las tramas nuevas del micrófono, para que
                # la referencia del mismo instante ya esté registrada.
                self._poll_echo_reference(max(1, target * self._vad_cfg.frame_ms // 1000))
            if block.size == 0:
                time.sleep(self.POLL_SLEEP_S)
                continue

            resampled = self._resampler.process(block)
            if resampled.size:
                self._pending = (
                    np.concatenate((self._pending, resampled))
                    if self._pending.size
                    else resampled
                )
            self._consume_frames(target)

    def _consume_frames(self, samplerate: int) -> None:
        frame_len = max(1, samplerate * self._vad_cfg.frame_ms // 1000)
        detector = self._detector
        if detector is None:
            return

        decision: FrameDecision | None = None
        snapshot: GateSnapshot | None = None
        mouth = self._mouth.value
        micro_pause = self._pause.paused
        voiced = False
        echo = None
        n = self._pending.size // frame_len
        if n == 0:
            return

        for i in range(n):
            chunk = self._pending[i * frame_len : (i + 1) * frame_len]
            try:
                decision = detector.process(float_to_int16(chunk))
            except ValueError:
                # Cambió frame_ms a mitad de camino: reconstruir y reintentar.
                self._rebuild_detector = True
                break
            voiced = decision.voiced
            est = self._echo_est
            if est is not None:
                # Voz local = el VAD dice voz Y no es, con evidencia sólida,
                # el audio remoto reproducido por los altavoces. El detector
                # no ve el veto: su piso de ruido y su histéresis siguen
                # midiendo el micrófono tal cual.
                try:
                    echo = est.classify(self._frames, decision.rms_db)
                except Exception as exc:  # pragma: no cover - defensivo
                    self._drop_echo_reference(f"fallo del estimador: {exc}")
                    echo = None
                else:
                    if voiced and echo.veto:
                        voiced = False
                    self._log_echo(echo, decision.rms_db, decision.voiced)
            snapshot = self._gate.update(voiced)
            mouth = self._mouth.update(decision.rms_db, decision.noise_floor_db, voiced)
            micro_pause = self._pause.update(voiced)
            self._frames += 1

        self._pending = self._pending[n * frame_len :]

        if decision is None or snapshot is None:
            return
        capture = self._capture
        est = self._echo_est
        self._publish(
            state=snapshot.state,
            voiced=voiced,
            rms_db=decision.rms_db,
            noise_floor_db=decision.noise_floor_db,
            transitions=snapshot.transitions,
            frames=self._frames,
            backend=decision.backend,
            calibrating=decision.calibrating,
            signal_lost=decision.signal_lost,
            running=True,
            error="",
            overflows=capture.overflows if capture else 0,
            dropped_blocks=capture.dropped_blocks if capture else 0,
            expected_latency_ms=self._gate.expected_latency_ms,
            mouth_target=mouth,
            micro_pause=bool(micro_pause and snapshot.state is VoiceState.SPEAKING),
            t_monotonic=time.perf_counter(),
            echo_state=(echo.state.value if echo is not None else EchoState.OFF.value),
            echo_lag_ms=echo.lag_ms if echo is not None else 0,
            echo_coherence=echo.coherence if echo is not None else 0.0,
            echo_vetoes=est.vetoes if est is not None else self._status.echo_vetoes,
        )

    def _log_echo(self, echo, mic_db: float, vad_voiced: bool) -> None:  # noqa: ANN001
        """Diagnóstico opt-in: cambios de clasificación y resumen cada 2 s."""
        if not _AUDIO_DEBUG:
            return
        now = time.perf_counter()
        changed = echo.state is not self._echo_last_state
        if not changed and now - self._echo_last_log < 2.0:
            return
        self._echo_last_state = echo.state
        self._echo_last_log = now
        est = self._echo_est
        _log.info(
            "eco %-6s vad=%d veto=%d mic=%6.1f dB ref=%6.1f dB lag=%+4d ms coh=%.2f "
            "ganancia=%s residual=%s vetos=%d",
            echo.state.value, int(vad_voiced), int(echo.veto), mic_db, echo.ref_db,
            echo.lag_ms, echo.coherence,
            "n/d" if est is None or est.gain_db is None else f"{est.gain_db:+.1f} dB",
            "n/d" if not np.isfinite(echo.residual_db) else f"{echo.residual_db:+.1f} dB",
            est.vetoes if est is not None else 0,
        )

    # ------------------------------------------------------------------ #

    def _publish(self, **changes) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)
