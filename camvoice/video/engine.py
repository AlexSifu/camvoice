"""Hilo de render: reloj, compositor, cámara virtual y vista previa.

Un solo hilo produce fotogramas a cadencia fija. No hay más hilos de video: la
UI no participa en la generación, solo lee la última vista previa publicada, y
el audio vive en su propio hilo. Con eso se cumple el requisito de "no crear
hilos sin control": la aplicación tiene exactamente tres (UI, audio, render).

Política ante errores: un fallo de la cámara virtual no detiene el motor. Se
cierra la salida, se publica el mensaje y se sigue generando fotogramas
(visibles en la vista previa) reintentando reabrir con espera creciente. Un
fallo al leer la caché reutiliza el último fotograma bueno en lugar de emitir
negro, porque una imagen congelada es menos alarmante en una videollamada que
una pantalla en negro.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, replace

import cv2
import numpy as np

from ..audio.gate import VoiceState
from ..config import AppConfig
from ..output.sink import FrameSink, NullSink, SinkError, VirtualCamSink
from .cache import ClipCache, ClipCacheError, bgr_to_nv12, nv12_to_bgr, to_display_bgr
from .composer import ComposeInfo, FrameComposer, RenderState
from .director import Director, DirectorInfo, DirectorState, GestureResult, VoiceInput
from .effects import EffectChain, limit_opencv_threads

__all__ = ["RenderStatus", "RenderEngine", "Pacer", "hires_timer"]


# --------------------------------------------------------------------------- #
# Reloj
# --------------------------------------------------------------------------- #


class hires_timer:  # noqa: N801 - se usa como gestor de contexto
    """Sube la resolución del temporizador de Windows a 1 ms mientras dure.

    Python 3.11+ ya usa temporizadores de alta resolución en `time.sleep`, pero
    esto cubre versiones anteriores y entornos donde otra librería haya bajado
    la resolución. En cualquier otra plataforma no hace nada.
    """

    def __init__(self, period_ms: int = 1) -> None:
        self.period_ms = period_ms
        self._winmm = None

    def __enter__(self) -> "hires_timer":
        try:
            import ctypes

            self._winmm = ctypes.WinDLL("winmm")  # type: ignore[attr-defined]
            self._winmm.timeBeginPeriod(self.period_ms)
        except Exception:
            self._winmm = None
        return self

    def __exit__(self, *exc) -> None:
        if self._winmm is not None:
            try:
                self._winmm.timeEndPeriod(self.period_ms)
            except Exception:
                pass
            self._winmm = None


class Pacer:
    """Reloj de cadencia fija con corrección de deriva.

    El objetivo se calcula por acumulación (`next += dt`) y no por "dormir dt",
    de modo que el error de cada espera no se suma: a lo largo de 30 minutos la
    cadencia media se mantiene exacta. Si el sistema se retrasa más de
    `resync_factor` periodos se reancla el reloj y se cuenta un salto, en lugar
    de intentar recuperar emitiendo una ráfaga de fotogramas.
    """

    def __init__(self, fps: float, *, resync_factor: float = 3.0) -> None:
        self.dt = 1.0 / max(1e-6, fps)
        self.resync_factor = resync_factor
        self.late_frames = 0
        self.resyncs = 0
        self.worst_late_s = 0.0
        self.reset()

    def reset(self) -> None:
        # El plazo del primer fotograma es un periodo completo: si se anclara en
        # "ahora", la primera espera se contaría siempre como tardía.
        self._next = time.perf_counter() + self.dt

    def wait(self) -> float:
        """Espera hasta el siguiente instante. Devuelve el retraso en segundos."""
        now = time.perf_counter()
        remaining = self._next - now
        if remaining > 0:
            time.sleep(remaining)
            late = 0.0
        else:
            late = -remaining
            self.late_frames += 1
            self.worst_late_s = max(self.worst_late_s, late)
            if late > self.dt * self.resync_factor:
                self._next = now
                self.resyncs += 1
        self._next += self.dt
        return late


# --------------------------------------------------------------------------- #
# Estado publicado
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RenderStatus:
    running: bool = False
    state: RenderState | DirectorState = RenderState.SILENT
    target: VoiceState = VoiceState.SILENT
    frames_sent: int = 0
    transitions: int = 0
    fps_effective: float = 0.0
    """Fotogramas realmente emitidos por segundo de reloj."""

    fps_capacity: float = 0.0
    """FPS que el motor podría sostener si no tuviera que esperar al reloj."""

    late_frames: int = 0
    resyncs: int = 0
    worst_frame_ms: float = 0.0
    avg_frame_ms: float = 0.0
    sink_name: str = "-"
    sink_device: str = ""
    virtual_output: bool = False
    effects: str = "ninguno"
    error: str = ""
    warning: str = ""

    # --- Modo director (vacíos/cero en modo clásico) ---
    mode: str = "classic"
    clip: str = ""
    """Clip que se está emitiendo."""

    gesture: str = ""
    pending_gesture: str = ""
    gestures_available: tuple[str, ...] = ()
    gesture_latency_ms: float = -1.0
    """Última latencia medida petición -> primer fotograma del gesto (-1 = ninguna)."""

    gesture_latency_max_ms: float = -1.0
    jumps: int = 0
    morphs: int = 0
    degraded_events: int = 0
    voice_age_ms: float = -1.0
    """Antigüedad del dato de voz usado en el último fotograma: el retardo
    entre el final de la trama de audio analizada y el render (-1 = desconocido)."""

    mouth_target: float = 0.0
    micro_pause: bool = False
    video_paused: bool = False
    """Pausa manual: la salida repite la última imagen válida. Se indica en la
    interfaz local; la imagen emitida no lleva marcas."""

    anchor_transitions: int = 0
    fallback_transitions: int = 0
    segment_changes: int = 0
    interrupted_transitions: int = 0
    discontinuities: int = 0
    """Fotogramas no consecutivos fuera de una transición (debe ser 0)."""


def _to_voice_input(value) -> tuple[VoiceInput, float]:  # noqa: ANN001
    """Acepta VoiceState, VoiceInput o VoiceStatus. Devuelve (entrada, t_monotonic)."""
    if isinstance(value, VoiceInput):
        return value, 0.0
    if isinstance(value, VoiceState):
        return VoiceInput(value, 1.0 if value is VoiceState.SPEAKING else 0.0), 0.0
    state = getattr(value, "state", VoiceState.SILENT)
    default = 1.0 if state is VoiceState.SPEAKING else 0.0
    mouth = float(getattr(value, "mouth_target", default))
    pause = bool(getattr(value, "micro_pause", False))
    return VoiceInput(state, mouth, pause), float(getattr(value, "t_monotonic", 0.0) or 0.0)


# --------------------------------------------------------------------------- #


class RenderEngine:
    STATS_WINDOW = 60
    """Fotogramas sobre los que se promedian las métricas publicadas."""

    def __init__(
        self,
        cfg: AppConfig,
        speaking: ClipCache,
        silent: ClipCache,
        voice_state: "callable[[], VoiceState | VoiceInput]",  # noqa: F821
        *,
        sink: FrameSink | None = None,
        director: Director | None = None,
    ) -> None:
        """`voice_state` puede devolver VoiceState (modo clásico, compatibilidad),
        VoiceInput o directamente el VoiceStatus del hilo de audio.

        Con `director` se usa el modo director; sin él, el compositor clásico.
        """
        self.cfg = cfg
        self.speaking_cache = speaking
        self.silent_cache = silent
        self._voice_state = voice_state
        self._external_sink = sink

        self.director = director
        self._voice_age_ms = -1.0
        self._mouth_target = 0.0
        self._micro_pause = False
        self.composer = FrameComposer(cfg.video, speaking, silent)
        self.effects = EffectChain(cfg.effects)
        self.pacer = Pacer(cfg.video.fps)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sink: FrameSink | None = None
        self._sink_lock = threading.Lock()
        self._want_virtual = bool(cfg.output.enabled)
        self._sink_dirty = True

        self._status_lock = threading.Lock()
        self._status = RenderStatus()

        self._preview_lock = threading.Lock()
        self._preview: np.ndarray | None = None
        self._preview_due = 0.0

        self._durations: list[float] = []
        self._frame_times: deque[float] = deque(maxlen=self.STATS_WINDOW)
        self._last_good: np.ndarray | None = None
        self._sink_error_until = 0.0
        self._sink_attempts = 0

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
        self._sink_dirty = True
        self._thread = threading.Thread(target=self._run, name="camvoice-render", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        self._close_sink()
        self._publish(running=False)

    def status(self) -> RenderStatus:
        with self._status_lock:
            return self._status

    def latest_preview(self) -> np.ndarray | None:
        """Último fotograma listo para mostrar (BGR, reducido). None si no hay."""
        with self._preview_lock:
            return None if self._preview is None else self._preview.copy()

    def set_virtual_output(self, enabled: bool) -> None:
        """Activa o desactiva la cámara virtual sin detener el motor."""
        if enabled == self._want_virtual:
            return
        self._want_virtual = bool(enabled)
        self.cfg.output.enabled = bool(enabled)
        self._sink_dirty = True

    # ------------------------------------------------------------------ #
    # Hilo
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        # Un solo hilo de OpenCV mientras rendericemos: a 640x480 no mejora el
        # tiempo de pared y triplica la CPU consumida. Ver effects.RENDER_THREADS.
        hilos_previos = limit_opencv_threads()
        with hires_timer(1):
            self.pacer.reset()
            self._publish(running=True, error="")
            try:
                while not self._stop.is_set():
                    self._tick()
                    self.pacer.wait()
            finally:
                self._close_sink()
                self._publish(running=False)
                limit_opencv_threads(hilos_previos)

    def _tick(self) -> None:
        started = time.perf_counter()

        if self._sink_dirty:
            self._apply_sink()

        try:
            voice, t_voice = _to_voice_input(self._voice_state())
        except Exception:
            voice, t_voice = VoiceInput(), 0.0

        frame, info = self._compose(voice)
        frame = self._apply_effects(frame)
        # Salida limpia: nada se dibuja sobre la imagen emitida. Los estados
        # (silencio, habla, gesto, pausa) solo se muestran en la ventana local
        # y en la bandeja.
        self._emit(frame)
        self._voice_age_ms = (time.perf_counter() - t_voice) * 1000.0 if t_voice > 0 else -1.0
        self._mouth_target = voice.mouth
        self._micro_pause = voice.pause
        self._update_preview(frame, started)

        finished = time.perf_counter()
        self._frame_times.append(finished)
        elapsed_ms = (finished - started) * 1000.0
        self._durations.append(elapsed_ms)
        if len(self._durations) > self.STATS_WINDOW:
            del self._durations[: -self.STATS_WINDOW]
        self._publish_stats(info)

    # ------------------------------------------------------------------ #
    # Gestos (seguros desde el hilo de la UI)
    # ------------------------------------------------------------------ #

    def request_gesture(self, name: str) -> GestureResult:
        if self.director is None:
            return GestureResult(
                False,
                "Los gestos necesitan el modo de reproducción «director» "
                "(pestaña Videos).",
            )
        return self.director.request_gesture(name)

    def cancel_gesture(self) -> None:
        if self.director is not None:
            self.director.cancel_gesture()

    def pause_video(self) -> None:
        if self.director is not None:
            self.director.pause_video()

    def resume_video(self) -> None:
        if self.director is not None:
            self.director.resume_video()

    def toggle_video_pause(self) -> bool:
        """Alterna la pausa manual (solo modo director). True si queda en pausa."""
        return self.director.toggle_video_pause() if self.director is not None else False

    @property
    def video_paused(self) -> bool:
        return self.director is not None and self.director.video_paused

    def available_gestures(self) -> list[str]:
        return self.director.available_gestures() if self.director is not None else []

    def _compose(self, voice: VoiceInput) -> tuple[np.ndarray, ComposeInfo | DirectorInfo]:
        if self.director is not None:
            # El director gestiona sus propios fallos de lectura (estado DEGRADED).
            frame, dinfo = self.director.step(voice)
            self._last_good = frame
            if dinfo.message:
                self._publish(warning=dinfo.message)
            return frame, dinfo
        try:
            frame, info = self.composer.step(voice.state)
            self._last_good = frame
            return frame, info
        except (ClipCacheError, OSError, ValueError) as exc:
            # La caché dejó de ser legible (archivo borrado, disco extraíble).
            # Se congela la última imagen buena en vez de emitir negro.
            self._publish(
                warning=(
                    "No se pudo leer la caché del clip; se mantiene la última "
                    f"imagen. Vuelve a preparar los clips. Detalle: {exc}"
                )
            )
            if self._last_good is None:
                self._last_good = np.zeros(self.speaking_cache.shape, dtype=np.uint8)
            info = ComposeInfo(
                state=self.composer.state,
                target=self.composer.target,
                progress=0.0,
                speaking_index=-1,
                silent_index=-1,
                speaking_rewound=False,
                blended=False,
            )
            return self._last_good, info

    # ------------------------------------------------------------------ #
    # Efectos
    # ------------------------------------------------------------------ #

    def _apply_effects(self, frame: np.ndarray) -> np.ndarray:
        """Aplica la cadena de efectos, convirtiendo de formato si hace falta.

        Con el formato interno NV12 hay que ir y volver a BGR, porque un
        desenfoque aplicado sobre el plano UV entrelazado mezclaría las
        componentes U y V entre sí y produciría manchas de color. Son unos 2 ms
        a 640x480, frente a los 66,7 ms de presupuesto a 15 FPS.

        Si no hay ningún efecto activo no se convierte nada: se devuelve la
        misma vista del `memmap` y el coste es cero.
        """
        if not self.effects.active:
            return frame
        try:
            if self.cfg.video.pixel_format == "nv12":
                bgr = nv12_to_bgr(frame)
                return bgr_to_nv12(self.effects.apply(bgr))
            return self.effects.apply(frame)
        except Exception as exc:  # pragma: no cover - defensivo
            # Un efecto mal parametrizado no debe cortar la emisión: mejor sale
            # el fotograma sin efectos que no sale nada.
            self._publish(warning=f"Se omitieron los efectos en este fotograma: {exc}")
            return frame

    # ------------------------------------------------------------------ #
    # Salida
    # ------------------------------------------------------------------ #

    def _apply_sink(self) -> None:
        self._sink_dirty = False
        self._close_sink()
        if self._external_sink is not None:
            self._set_sink(self._external_sink)
            return
        if not self._want_virtual:
            self._set_sink(NullSink())
            self._publish(virtual_output=False, error="")
            return
        try:
            sink = VirtualCamSink(
                self.cfg.output,
                self.cfg.video.width,
                self.cfg.video.height,
                self.cfg.video.fps,
                self.cfg.video.pixel_format,
            )
        except SinkError as exc:
            self._set_sink(NullSink())
            self._publish(virtual_output=False, error=str(exc))
            self._schedule_retry()
            return
        self._set_sink(sink)
        self._sink_attempts = 0
        self._publish(virtual_output=True, error="")

    def _set_sink(self, sink: FrameSink) -> None:
        with self._sink_lock:
            self._sink = sink
        self._publish(
            sink_name=getattr(sink, "name", "-"),
            sink_device=getattr(sink, "device", ""),
        )

    def _close_sink(self) -> None:
        with self._sink_lock:
            sink, self._sink = self._sink, None
        if sink is not None and sink is not self._external_sink:
            try:
                sink.close()
            except Exception:
                pass

    def _schedule_retry(self) -> None:
        self._sink_attempts += 1
        delay = min(30.0, self.cfg.output.reopen_backoff_s * self._sink_attempts)
        self._sink_error_until = time.perf_counter() + delay

    def _emit(self, frame: np.ndarray) -> None:
        with self._sink_lock:
            sink = self._sink
        if sink is None:
            return
        try:
            sink.send(frame)
        except SinkError as exc:
            self._publish(virtual_output=False, error=str(exc))
            self._set_sink(NullSink())
            self._schedule_retry()
            return

        # Reintento periódico cuando la salida está caída pero se desea activa.
        if (
            self._want_virtual
            and isinstance(sink, NullSink)
            and self._external_sink is None
            and time.perf_counter() >= self._sink_error_until
        ):
            self._sink_dirty = True

    # ------------------------------------------------------------------ #
    # Vista previa
    # ------------------------------------------------------------------ #

    def _update_preview(self, frame: np.ndarray, now: float) -> None:
        cfg = self.cfg.video
        if cfg.preview_fps <= 0 or now < self._preview_due:
            return
        self._preview_due = now + 1.0 / cfg.preview_fps
        try:
            bgr = to_display_bgr(frame, cfg.pixel_format)
            if cfg.preview_max_width and cfg.width > cfg.preview_max_width:
                scale = cfg.preview_max_width / cfg.width
                bgr = cv2.resize(
                    bgr,
                    (cfg.preview_max_width, max(2, int(round(cfg.height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                bgr = bgr.copy()
        except Exception:
            return
        with self._preview_lock:
            self._preview = bgr

    # ------------------------------------------------------------------ #

    def _publish_stats(self, info: ComposeInfo | DirectorInfo) -> None:
        durations = self._durations
        avg = sum(durations) / len(durations) if durations else 0.0
        worst = max(durations) if durations else 0.0
        with self._sink_lock:
            sink = self._sink
        frames = getattr(sink, "frames", 0) if sink is not None else 0

        # FPS efectivos = fotogramas emitidos por segundo de reloj, que es lo
        # que ve la aplicación receptora. Distinto de la capacidad del motor.
        effective = 0.0
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            if span > 0:
                effective = (len(self._frame_times) - 1) / span

        extra: dict = {}
        d = self.director
        if d is not None:
            lat = list(d.stats.gesture_latencies_ms)
            extra = dict(
                mode="director",
                clip=info.clip,
                gesture=info.gesture,
                pending_gesture=info.pending_gesture,
                gestures_available=tuple(d.available_gestures()),
                gesture_latency_ms=lat[-1] if lat else -1.0,
                gesture_latency_max_ms=max(lat) if lat else -1.0,
                jumps=d.stats.jumps,
                morphs=d.stats.morphs,
                degraded_events=d.stats.degraded_events,
                video_paused=d.video_paused,
                anchor_transitions=d.stats.anchor_transitions,
                fallback_transitions=d.stats.fallback_transitions,
                segment_changes=d.stats.segment_changes,
                interrupted_transitions=d.stats.interrupted_transitions,
                discontinuities=d.stats.discontinuities,
            )
        self._publish(
            state=info.state,
            target=info.target,
            frames_sent=frames,
            transitions=(d.transitions if d is not None else self.composer.transitions),
            voice_age_ms=self._voice_age_ms,
            mouth_target=self._mouth_target,
            micro_pause=self._micro_pause,
            **extra,
            fps_effective=effective,
            fps_capacity=(1000.0 / avg) if avg > 0 else 0.0,
            late_frames=self.pacer.late_frames,
            resyncs=self.pacer.resyncs,
            avg_frame_ms=avg,
            worst_frame_ms=worst,
            effects=self.effects.describe(),
        )

    def _publish(self, **changes) -> None:
        with self._status_lock:
            self._status = replace(self._status, **changes)

