"""Orquestación: una `Session` ata audio, clips, render y métricas.

La CLI y la interfaz gráfica usan exactamente esta clase, así que lo que se
mide en consola es lo mismo que corre bajo la ventana. No hay una segunda
versión del flujo escondida en la UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audio.gate import VoiceState
from .audio.pipeline import AudioPipeline, VoiceStatus
from .config import AppConfig, cache_dir
from .metrics import PerfMonitor
from .video.cache import (
    ClipCache,
    ClipCacheError,
    ProgressFn,
    cache_key,
    prepare_clip,
    probe_clip,
    prune_cache,
)
from .video.engine import RenderEngine, RenderStatus

__all__ = ["SessionError", "PreparedClips", "SessionStatus", "Session"]

MIN_USEFUL_SECONDS = 1.5
ASPECT_TOLERANCE = 0.02


class SessionError(RuntimeError):
    """Problema que impide arrancar, con mensaje apto para el usuario."""


@dataclass(slots=True)
class PreparedClips:
    speaking: ClipCache
    silent: ClipCache
    warnings: list[str]

    @property
    def total_mb(self) -> float:
        return (self.speaking.meta.total_bytes + self.silent.meta.total_bytes) / 1e6


@dataclass(frozen=True, slots=True)
class SessionStatus:
    voice: VoiceStatus
    render: RenderStatus
    prepared: bool
    clips_summary: str = ""

    @property
    def headline(self) -> str:
        """Texto de una línea con el estado visible, para la UI y los logs."""
        if not self.render.running:
            return "Detenido"
        return {
            "SILENT": "Silencio",
            "SPEAKING": "Voz detectada",
            "TRANSITION": "Transición",
        }.get(self.render.state.value, self.render.state.value)


# --------------------------------------------------------------------------- #


class Session:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.audio = AudioPipeline(cfg)
        self.engine: RenderEngine | None = None
        self.metrics: PerfMonitor | None = None
        self.clips: PreparedClips | None = None

    # ------------------------------------------------------------------ #
    # Preparación
    # ------------------------------------------------------------------ #

    def prepare(
        self,
        *,
        progress: ProgressFn | None = None,
        force: bool = False,
        load_to_ram: bool = False,
    ) -> PreparedClips:
        """Valida la configuración y construye la caché de los dos clips.

        Es la operación lenta (unos segundos por clip la primera vez) y la única
        que puede tardar: se llama antes de arrancar, nunca durante.
        """
        errors = self.cfg.validate()
        if errors:
            raise SessionError("\n".join(f"- {e}" for e in errors))

        video = self.cfg.video
        warnings = list(self.cfg.warnings())
        warnings += self._compatibility_warnings()

        try:
            speaking = prepare_clip(video.speaking_clip, video, force=force, progress=progress)
            silent = prepare_clip(video.silent_clip, video, force=force, progress=progress)
        except ClipCacheError as exc:
            raise SessionError(str(exc)) from exc

        for label, clip in (("habla", speaking), ("silencio", silent)):
            if clip.meta.duration_s < MIN_USEFUL_SECONDS:
                warnings.append(
                    f"El clip de {label} dura {clip.meta.duration_s:.1f} s: el bucle "
                    "se notará. Lo recomendable son 8-20 s."
                )
            if clip.meta.truncated:
                warnings.append(
                    f"El clip de {label} se recortó a {clip.meta.duration_s:.0f} s por "
                    f"el tope de caché ({video.max_cache_mb} MB)."
                )
            if load_to_ram:
                clip.load_to_ram()
            clip.warm_up()

        # Se liberan cachés de configuraciones anteriores del mismo video.
        prune_cache(
            {cache_key(video.speaking_clip, video), cache_key(video.silent_clip, video)},
            directory=cache_dir(),
        )

        self.clips = PreparedClips(speaking, silent, warnings)
        return self.clips

    def _compatibility_warnings(self) -> list[str]:
        """Avisa de diferencias entre los dos clips antes de procesarlos.

        El plan pide que los videos se graben en las mismas condiciones; esto no
        puede verificarse automáticamente, pero sí lo medible: geometría,
        cadencia y duración.
        """
        video = self.cfg.video
        warns: list[str] = []
        try:
            speaking = probe_clip(video.speaking_clip)
            silent = probe_clip(video.silent_clip)
        except ClipCacheError as exc:
            raise SessionError(str(exc)) from exc

        if (speaking.width, speaking.height) != (silent.width, silent.height):
            warns.append(
                f"Los clips tienen resoluciones distintas ({speaking.width}x{speaking.height} "
                f"y {silent.width}x{silent.height}). Se normalizan, pero el encuadre "
                "puede no coincidir y el corte se notará."
            )
        if speaking.aspect and silent.aspect:
            if abs(speaking.aspect - silent.aspect) / speaking.aspect > ASPECT_TOLERANCE:
                warns.append(
                    "La relación de aspecto de los dos clips no coincide: con el modo "
                    f"«{video.fit}» uno de los dos se recortará o escalará distinto."
                )
            target = video.width / video.height
            if abs(speaking.aspect - target) / target > ASPECT_TOLERANCE:
                detail = {
                    "cover": "se recortarán los bordes para llenar el marco",
                    "contain": "aparecerán barras para no recortar",
                    "stretch": "la imagen se deformará",
                }[video.fit]
                warns.append(
                    f"Los clips son {speaking.aspect:.2f}:1 y la salida {target:.2f}:1; "
                    f"con el modo «{video.fit}» {detail}."
                )
        return warns

    # ------------------------------------------------------------------ #
    # Ejecución
    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        return self.engine is not None and self.engine.running

    def start(self, *, sink=None) -> None:  # noqa: ANN001
        if self.clips is None:
            raise SessionError("Hay que preparar los clips antes de arrancar.")
        if self.running:
            return

        self.audio.start()
        self.engine = RenderEngine(
            self.cfg,
            self.clips.speaking,
            self.clips.silent,
            self._voice_state,
            sink=sink,
        )
        self.engine.start()

        if self.cfg.metrics.enabled:
            self.metrics = PerfMonitor(self.cfg.metrics, self.status)
            self.metrics.start()

    def stop(self) -> None:
        if self.metrics is not None:
            self.metrics.stop()
            self.metrics = None
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        self.audio.stop()

    def close(self) -> None:
        self.stop()
        if self.clips is not None:
            self.clips.speaking.close()
            self.clips.silent.close()
            self.clips = None

    def _voice_state(self) -> VoiceState:
        return self.audio.status().state

    # ------------------------------------------------------------------ #

    def status(self) -> SessionStatus:
        return SessionStatus(
            voice=self.audio.status(),
            render=self.engine.status() if self.engine else RenderStatus(),
            prepared=self.clips is not None,
            clips_summary=(
                f"{self.clips.speaking.describe()}\n{self.clips.silent.describe()}"
                if self.clips
                else ""
            ),
        )

    def latest_preview(self):  # noqa: ANN201
        return self.engine.latest_preview() if self.engine else None

    # ------------------------------------------------------------------ #

    def needs_reprepare(self, previous: AppConfig) -> bool:
        """True si un cambio de configuración invalida la caché de los clips."""
        a, b = previous.video, self.cfg.video
        return any(
            getattr(a, field) != getattr(b, field)
            for field in (
                "speaking_clip",
                "silent_clip",
                "width",
                "height",
                "fps",
                "fit",
                "mirror",
                "pixel_format",
                "letterbox_color",
            )
        )
