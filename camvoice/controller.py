"""Orquestación: una `Session` ata audio, clips, render y métricas.

La CLI y la interfaz gráfica usan exactamente esta clase, así que lo que se
mide en consola es lo mismo que corre bajo la ventana. No hay una segunda
versión del flujo escondida en la UI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
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
from .gestures import SPEAKING_SUFFIX, gesture_label
from .meeting import MeetingMuteProvider, default_provider
from .permission import AuthorizedVoice, SpeechAuthorization, SpeechPermissionController
from .video.director import Director, DirectorClip, GestureResult
from .video.engine import RenderEngine, RenderStatus
from .video.index import IndexCancelled, distances, load_or_build_index
from .video.library import LibraryLoad, load_library
from .video.normalize import IDENTITY, Normalization, compute_normalization, sample_frames

__all__ = ["SessionError", "PreparedClips", "SessionStatus", "Session"]

log = logging.getLogger("camvoice.controller")

MIN_USEFUL_SECONDS = 1.5


def _norm(p: str) -> str:
    try:
        return str(Path(p).resolve()).casefold()
    except OSError:
        return str(p).casefold()

ASPECT_TOLERANCE = 0.02


class SessionError(RuntimeError):
    """Problema que impide arrancar, con mensaje apto para el usuario."""


@dataclass(slots=True)
class PreparedClips:
    speaking: ClipCache
    silent: ClipCache
    warnings: list[str]
    mode: str = "classic"
    """Modo efectivo: 'director' o 'classic' (también si el director falló)."""

    silent_pool: list[DirectorClip] = field(default_factory=list)
    speaking_pool: list[DirectorClip] = field(default_factory=list)
    gestures: dict[str, DirectorClip] = field(default_factory=dict)
    extra_caches: list[ClipCache] = field(default_factory=list)
    library: LibraryLoad | None = None
    speaking_actions: set[str] = field(default_factory=set)
    """Frases a petición (SPEAKING_ONE_SHOT): no esperan micro-pausa."""

    @property
    def all_caches(self) -> list[ClipCache]:
        return [self.speaking, self.silent, *self.extra_caches]

    @property
    def total_mb(self) -> float:
        return sum(c.meta.total_bytes for c in self.all_caches) / 1e6


@dataclass(frozen=True, slots=True)
class SessionStatus:
    voice: VoiceStatus
    render: RenderStatus
    prepared: bool
    clips_summary: str = ""
    speech: SpeechAuthorization | None = None
    """Autorización de habla vigente (modo, permiso manual, estado de Teams)."""

    @property
    def physical_speaking(self) -> bool:
        """Voz física detectada por VAD + puerta (independiente de la autorización)."""
        return self.voice.state is VoiceState.SPEAKING

    @property
    def speech_allowed(self) -> bool:
        return self.speech is not None and self.speech.allowed

    @property
    def headline(self) -> str:
        """Texto de una línea con el estado visible, para la UI y los logs."""
        if not self.render.running:
            return "Detenido"
        text = {
            "SILENT": "Silencio",
            "SPEAKING": "Voz detectada",
            "TRANSITION": "Transición",
            "GESTURE": "Gesto",
            "DEGRADED": "Modo degradado",
        }.get(self.render.state.value, self.render.state.value)
        if self.render.gesture:
            text += f": {gesture_label(self.render.gesture)}"
        if self.physical_speaking and not self.speech_allowed:
            text += " (voz bloqueada)"
        return text


# --------------------------------------------------------------------------- #


class Session:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.audio = AudioPipeline(cfg)
        self.engine: RenderEngine | None = None
        self.metrics: PerfMonitor | None = None
        self.clips: PreparedClips | None = None
        # Autorización de habla: estado real del micrófono de Teams (lectura
        # UIA en hilo propio) + permiso manual. Único punto de decisión entre
        # la puerta de voz y el motor (`_voice_input`).
        self.meeting: MeetingMuteProvider = default_provider(
            poll_ms=cfg.speech.teams_poll_ms, unmuted_expiry_ms=cfg.speech.unmuted_expiry_ms
        )
        self.permission = SpeechPermissionController(self.meeting, mode=cfg.speech.mode)
        self._was_authorized = False

    # ------------------------------------------------------------------ #
    # Preparación
    # ------------------------------------------------------------------ #

    def prepare(
        self,
        *,
        progress: ProgressFn | None = None,
        force: bool = False,
        load_to_ram: bool = False,
        cancel: Callable[[], bool] | None = None,
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

        director_mode = video.playback == "director"
        reference: list = []
        if director_mode and video.normalize_clips:
            reference = sample_frames(video.silent_clip, video)

        try:
            silent = prepare_clip(video.silent_clip, video, force=force, progress=progress)
            speaking = self._prepare_one(
                video.speaking_clip, reference, force, progress, warnings, "habla"
            )
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

        clips = PreparedClips(speaking, silent, warnings)
        if director_mode:
            try:
                self._prepare_director(clips, reference, force, progress, cancel, load_to_ram)
            except IndexCancelled as exc:
                raise SessionError("Preparación cancelada.") from exc
            except Exception as exc:  # el modo clásico siempre queda disponible
                warnings.append(
                    f"No se pudo preparar el modo director ({exc}); se usa el modo clásico."
                )
                clips.mode = "classic"

        # Se liberan cachés de configuraciones anteriores (y sus índices).
        prune_cache({c.raw_path.stem for c in clips.all_caches}, directory=cache_dir())

        self.clips = clips
        return self.clips

    # ------------------------------------------------------------------ #

    def _normalization(self, path: str, reference: list) -> Normalization:
        video = self.cfg.video
        if not reference or not video.normalize_clips:
            return IDENTITY
        same = Path(path).resolve() == Path(video.silent_clip).resolve()
        return compute_normalization(path, reference, video, is_reference=same)

    def _prepare_one(self, path, reference, force, progress, warnings, label):  # noqa: ANN001, ANN202
        norm = self._normalization(path, reference)
        if norm.notes and any("demasiado grande" in n or "no se pudieron" in n for n in norm.notes):
            warnings.append(f"Clip de {label} ({Path(path).name}): " + "; ".join(norm.notes) + ".")
        return prepare_clip(
            path,
            self.cfg.video,
            force=force,
            progress=progress,
            transform=None if norm.identity else norm.apply,
            variant=norm.fingerprint,
        )

    def _prepare_director(self, clips, reference, force, progress, cancel, load_to_ram) -> None:  # noqa: ANN001
        video = self.cfg.video
        lib = load_library(self.cfg)
        clips.library = lib
        clips.warnings.extend(lib.issues)
        silent_paths, speaking_paths = lib.library.pools(self.cfg)
        clips.speaking_actions = lib.library.speaking_actions()
        # Anclas marcadas a mano y número de escena, por ruta del clip.
        by_path: dict[str, tuple[int, list[int]]] = {}
        for num, e in lib.library.scenes.items():
            for p in (e.working, e.source):
                if p:
                    by_path[_norm(p)] = (num, list(e.anchors))
        total = len(silent_paths) + len(speaking_paths) + len(lib.library.active_gestures())
        done = [0]

        def step(path: str) -> ProgressFn | None:
            """Progreso con el número de clip y de escena («Clip 7/62 · nº 12»)."""
            done[0] += 1
            num = by_path.get(_norm(path), (None, []))[0]
            prefix = f"Clip {done[0]}/{total}" + (f" · nº {num}" if num is not None else "")
            if progress is None:
                return None
            return lambda a, b, text: progress(a, b, f"{prefix}: {text}")

        def index(cache: ClipCache, label: str, path: str = "") -> DirectorClip:
            idx = load_or_build_index(cache, force=force, cancel=cancel, progress=progress)
            manual = tuple(by_path.get(_norm(path), (None, []))[1]) if path else ()
            return DirectorClip(label, cache, idx, manual_anchors=manual)

        def extra(path: str, label: str) -> ClipCache | None:
            if cancel is not None and cancel():
                raise IndexCancelled("Preparación cancelada.")
            try:
                cache = self._prepare_one(path, reference, force, step(path), clips.warnings, label)
            except ClipCacheError as exc:
                clips.warnings.append(f"Se omite {Path(path).name}: {exc}")
                return None
            if load_to_ram:
                cache.load_to_ram()
            cache.warm_up()
            clips.extra_caches.append(cache)
            return cache

        step(video.silent_clip)
        clips.silent_pool.append(index(clips.silent, Path(video.silent_clip).stem, video.silent_clip))
        for p in silent_paths[1:]:
            cache = extra(p, "silencio")
            if cache is not None:
                clips.silent_pool.append(index(cache, Path(p).stem, p))
        step(video.speaking_clip)
        clips.speaking_pool.append(
            index(clips.speaking, Path(video.speaking_clip).stem, video.speaking_clip)
        )
        for p in speaking_paths[1:]:
            cache = extra(p, "habla")
            if cache is not None:
                clips.speaking_pool.append(index(cache, Path(p).stem, p))

        neutral = clips.silent_pool[0].index
        off_neutral: list[str] = []
        for name, entry in lib.library.active_gestures().items():
            cache = extra(entry.clip, f"gesto «{gesture_label(name)}»")
            if cache is None:
                continue
            g = index(cache, name, entry.clip)
            clips.gestures[name] = g
            # Un gesto debe empezar y terminar en la pose neutra (guía de grabación).
            limit = 4.0 * max(neutral.d_ok, Director.D_OK_FLOOR)
            ends = [
                where
                for where, i in (("empieza", 0), ("termina", g.index.frame_count - 1))
                if float(distances(neutral.descriptors, g.index.descriptors[i]).min()) > limit
            ]
            if ends:
                off_neutral.append(f"{gesture_label(name)} ({' y '.join(ends)})")
        if len(off_neutral) == 1:
            clips.warnings.append(
                f"El gesto «{off_neutral[0]}» no está en la pose neutra al principio o al "
                "final; la transición será más larga. Ver GUIA_GRABACION_Y_GESTOS.md."
            )
        elif off_neutral:
            clips.warnings.append(
                f"{len(off_neutral)} gestos no empiezan o no terminan en la pose neutra "
                "(sus transiciones serán más largas): " + ", ".join(off_neutral) + "."
            )
        missing = lib.library.missing_gestures()
        if missing and lib.exists:
            clips.warnings.append(
                "Gestos sin clip: " + ", ".join(gesture_label(m) for m in missing) + "."
            )
        for c in clips.speaking_pool:
            if c.index.mouth_range < 0.01:
                clips.warnings.append(
                    f"En «{c.name}» no se aprecia movimiento de boca: la boca no podrá "
                    "seguir a la voz con ese clip."
                )
        clips.mode = "director"

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

    def start(self, *, sink=None, voice_source=None) -> None:  # noqa: ANN001
        """Arranca audio y render.

        `voice_source` (opcional) sustituye al micrófono por una función que
        devuelve VoiceInput/VoiceState: lo usan las pruebas de resistencia
        (`camvoice soak`). Con ella no se abre ningún dispositivo de audio.
        """
        if self.clips is None:
            raise SessionError("Hay que preparar los clips antes de arrancar.")
        if self.running:
            return

        director = None
        if self.clips.mode == "director":
            director = Director(
                self.cfg.video,
                self.clips.silent_pool,
                self.clips.speaking_pool,
                self.clips.gestures,
                gestures_cfg=self.cfg.gestures,
                speaking_actions=self.clips.speaking_actions,
            )
        if voice_source is None:
            # Cada arranque empieza con el permiso manual en «Bloquear».
            self.permission.reset_manual()
            self._was_authorized = False
            self.start_meeting_monitor()
            self.audio.start()
        self.engine = RenderEngine(
            self.cfg,
            self.clips.speaking,
            self.clips.silent,
            voice_source or self._voice_input,
            sink=sink,
            director=director,
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
        # Sesión perdida: el permiso manual vuelve a «Bloquear».
        self.permission.reset_manual()
        self._was_authorized = False

    def close(self) -> None:
        self.stop()
        self.stop_meeting_monitor()
        if self.clips is not None:
            for cache in self.clips.all_caches:
                cache.close()
            self.clips = None

    # ------------------------------------------------------------------ #
    # Control de habla
    # ------------------------------------------------------------------ #

    def start_meeting_monitor(self) -> None:
        """Arranca la lectura de Teams (idempotente). La UI puede llamarlo
        antes de iniciar la cámara para mostrar el estado desde el principio."""
        try:
            self.meeting.start()
        except Exception as exc:  # noqa: BLE001 - sin lectura, el modo automático queda en UNKNOWN
            log.warning("No se pudo iniciar la lectura de Teams: %s", exc)

    def stop_meeting_monitor(self) -> None:
        try:
            self.meeting.stop()
        except Exception:  # noqa: BLE001
            pass

    def set_speech_mode(self, mode: str) -> None:
        """Cambia «Control de habla». No autoriza nada por sí mismo: el permiso
        manual vuelve a Bloquear y el habla solo sale si la voz sigue ahí."""
        self.permission.set_mode(mode)
        self.cfg.speech.mode = mode

    def set_manual_speech(self, allowed: bool) -> None:
        self.permission.set_manual(allowed)

    def toggle_manual_speech(self) -> bool:
        return self.permission.toggle_manual()

    def speech_authorization(self) -> SpeechAuthorization:
        """Decisión vigente (para la UI); evalúa con la instantánea actual."""
        return self.permission.evaluate()

    def _voice_state(self) -> VoiceState:
        return self._voice_input().state

    def _voice_input(self) -> AuthorizedVoice:
        """Único punto de autorización: VAD + puerta → permiso → motor.

        Se llama desde el hilo de render en cada fotograma. Si el habla deja
        de estar autorizada mientras suena un clip de habla (variante
        «_hablando» o frase a petición), se cancela ese gesto para que no siga
        simulando conversación; el resto de reglas de cancelación no cambian.
        """
        authorized = self.permission.authorize(self.audio.status())
        if self._was_authorized and not authorized.authorized:
            self._cancel_speaking_gesture()
        self._was_authorized = authorized.authorized
        return authorized

    def _cancel_speaking_gesture(self) -> None:
        engine = self.engine
        if engine is None:
            return
        try:
            gesture = engine.status().gesture
        except Exception:  # noqa: BLE001 - el estado del motor es informativo
            return
        if not gesture:
            return
        actions = self.clips.speaking_actions if self.clips is not None else set()
        if gesture.endswith(SPEAKING_SUFFIX) or gesture in actions:
            log.info("Habla bloqueada durante «%s»: gesto de habla cancelado.", gesture)
            engine.cancel_gesture()

    # ------------------------------------------------------------------ #
    # Gestos
    # ------------------------------------------------------------------ #

    def request_gesture(self, name: str) -> GestureResult:
        if self.engine is None:
            return GestureResult(False, "La emisión no está en marcha.")
        return self.engine.request_gesture(name)

    def cancel_gesture(self) -> None:
        if self.engine is not None:
            self.engine.cancel_gesture()

    def toggle_video_pause(self) -> bool:
        """Pausa/reanuda el video (imagen congelada). True si queda en pausa."""
        if self.engine is None:
            return False
        return self.engine.toggle_video_pause()

    @property
    def video_paused(self) -> bool:
        return self.engine is not None and self.engine.video_paused

    # ------------------------------------------------------------------ #

    def status(self) -> SessionStatus:
        return SessionStatus(
            voice=self.audio.status(),
            render=self.engine.status() if self.engine else RenderStatus(),
            prepared=self.clips is not None,
            clips_summary=(
                "\n".join(c.describe() for c in self.clips.all_caches) if self.clips else ""
            ),
            speech=self.permission.evaluate(),
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
                "playback",
                "library_manifest",
                "normalize_clips",
            )
        )
