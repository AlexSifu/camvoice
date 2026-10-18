"""Hilo de audio: captura -> remuestreo -> VAD -> histéresis -> estado publicado.

El resto de la aplicación no toca audio: lee `AudioPipeline.status()`, que
devuelve una instantánea inmutable. Así el hilo de render nunca se bloquea
esperando al micrófono, y la UI puede refrescar a su propio ritmo.

Recuperación: si el dispositivo desaparece (USB desconectado, cambio de
predeterminado en Windows), el hilo lo detecta, fuerza el estado a SILENT,
publica un mensaje legible y reintenta abrir con espera creciente.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

import numpy as np

from ..config import AudioConfig, AppConfig, VadConfig
from .capture import AudioCapture, AudioError
from .gate import GateSnapshot, SpeechGate, VoiceState
from .resample import Resampler, float_to_int16
from .vad import FrameDecision, build_detector

__all__ = ["VoiceStatus", "AudioPipeline"]

# Campos cuyo cambio obliga a reconstruir el detector; el resto son umbrales
# que el detector lee del mismo objeto VadConfig y surten efecto al instante.
_STRUCTURAL_VAD_FIELDS = ("backend", "frame_ms", "aggressiveness")


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
        self._pending = np.zeros(0, dtype=np.float32)
        self._frames = 0

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
        self._publish(running=False, state=VoiceState.SILENT)

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
                    error=str(exc),
                )
                backoff = self.REOPEN_BACKOFF_S[min(attempt, len(self.REOPEN_BACKOFF_S) - 1)]
                attempt += 1
                if self._stop.wait(backoff):
                    break
            except Exception as exc:  # pragma: no cover - defensivo
                self._teardown()
                self._publish(
                    running=False, state=VoiceState.SILENT,
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
        self._publish(
            running=True,
            error="",
            device_name=capture.device_name,
            samplerate=target,
            resampled_from=capture.samplerate,
            state=VoiceState.SILENT,
            calibrating=True,
        )

    def _build_detector(self, samplerate: int) -> None:
        self._detector = build_detector(self._vad_cfg, samplerate)
        self._rebuild_detector = False

    def _teardown(self) -> None:
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

            block = capture.read_all()
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
            snapshot = self._gate.update(decision.voiced)
            self._frames += 1

        self._pending = self._pending[n * frame_len :]

        if decision is None or snapshot is None:
            return
        capture = self._capture
        self._publish(
            state=snapshot.state,
            voiced=decision.voiced,
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
        )

    # ------------------------------------------------------------------ #

    def _publish(self, **changes) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)
