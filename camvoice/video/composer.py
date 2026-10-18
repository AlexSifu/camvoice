"""Compositor: estado de voz -> fotograma de salida.

Aquí vive la máquina de estados visual (SILENT / SPEAKING / TRANSITION), y está
deliberadamente separada del hilo, del reloj y de la cámara virtual: recibe un
estado de voz, devuelve un fotograma. Eso permite probar todo el comportamiento
visual —transiciones, rebobinado, continuidad— sin micrófono ni cámara.

El fundido cruzado se calcula con una única interpolación lineal sobre el búfer
del fotograma. Con NV12 esto es exacto: YUV es una transformación afín de RGB,
así que mezclar en YUV y mezclar en RGB dan el mismo resultado.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from ..audio.gate import VoiceState
from ..config import VideoConfig
from .clip import ClipPlayer

__all__ = ["RenderState", "ComposeInfo", "FrameComposer", "ClipSource"]


class RenderState(Enum):
    SILENT = "SILENT"
    SPEAKING = "SPEAKING"
    TRANSITION = "TRANSITION"


@dataclass(frozen=True, slots=True)
class ComposeInfo:
    state: RenderState
    target: VoiceState
    progress: float
    """0..1 dentro de la transición; 0 fuera de ella."""

    speaking_index: int
    silent_index: int
    speaking_rewound: bool
    blended: bool


class ClipSource:
    """Lo mínimo que el compositor necesita de un clip: contar y dar fotogramas."""

    def __init__(self, frames) -> None:  # noqa: ANN001 - acepta ClipCache o lista
        self._frames = frames

    def __len__(self) -> int:
        return len(self._frames)

    def frame(self, index: int) -> np.ndarray:
        if hasattr(self._frames, "frame"):
            return self._frames.frame(index)
        return self._frames[min(max(index, 0), len(self._frames) - 1)]


def _smoothstep(t: float) -> float:
    """Suavizado en los extremos: evita el 'pop' de un fundido lineal."""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


class FrameComposer:
    def __init__(
        self,
        cfg: VideoConfig,
        speaking,  # noqa: ANN001
        silent,  # noqa: ANN001
    ) -> None:
        self.cfg = cfg
        self.speaking = speaking if isinstance(speaking, ClipSource) else ClipSource(speaking)
        self.silent = silent if isinstance(silent, ClipSource) else ClipSource(silent)
        if len(self.speaking) == 0 or len(self.silent) == 0:
            raise ValueError("Ambos clips deben tener al menos un fotograma.")

        self.speaking_player = ClipPlayer(
            len(self.speaking), loop_mode=cfg.loop_mode, speed=cfg.speaking_speed
        )
        self.silent_player = ClipPlayer(
            len(self.silent), loop_mode=cfg.loop_mode, speed=cfg.silent_speed
        )

        self.state = RenderState.SILENT
        self.target = VoiceState.SILENT
        self._from_state = VoiceState.SILENT
        self._to_state = VoiceState.SILENT
        self._step = 0
        self._silent_frames = 0
        self._speaking_rewound = True
        self.transitions = 0

        self._buffer: np.ndarray | None = None

    # ------------------------------------------------------------------ #

    @property
    def transition_steps(self) -> int:
        """Duración de la transición en fotogramas de salida."""
        if self.cfg.transition_ms <= 0:
            return 0
        return max(1, int(round(self.cfg.transition_ms * self.cfg.fps / 1000.0)))

    @property
    def resume_window_frames(self) -> int:
        return max(0, int(round(self.cfg.resume_window_ms * self.cfg.fps / 1000.0)))

    def sync_speeds(self) -> None:
        """Relee las velocidades de la configuración (ajuste en caliente)."""
        self.speaking_player.speed = self.cfg.speaking_speed
        self.silent_player.speed = self.cfg.silent_speed
        self.speaking_player.loop_mode = self.cfg.loop_mode
        self.silent_player.loop_mode = self.cfg.loop_mode

    # ------------------------------------------------------------------ #

    def step(self, voice_state: VoiceState) -> tuple[np.ndarray, ComposeInfo]:
        """Produce el siguiente fotograma de salida para el estado de voz dado."""
        self.sync_speeds()
        self._update_target(voice_state)
        rewound = self._advance_players()
        frame, blended, progress = self._compose()

        info = ComposeInfo(
            state=self.state,
            target=self.target,
            progress=progress,
            speaking_index=self.speaking_player.index,
            silent_index=self.silent_player.index,
            speaking_rewound=rewound,
            blended=blended,
        )
        return frame, info

    # ------------------------------------------------------------------ #

    def _update_target(self, voice_state: VoiceState) -> None:
        if voice_state is self.target:
            return
        self.target = voice_state
        steps = self.transition_steps

        if steps == 0:
            self.state = (
                RenderState.SPEAKING if voice_state is VoiceState.SPEAKING
                else RenderState.SILENT
            )
            self.transitions += 1
            return

        if self.state is RenderState.TRANSITION:
            # Se invierte una transición en curso conservando el progreso, para
            # que no salte: lo que llevábamos fundido se deshace desde ahí.
            self._step = max(0, steps - self._step)
            self._from_state, self._to_state = self._to_state, self._from_state
        else:
            self._from_state = (
                VoiceState.SPEAKING if self.state is RenderState.SPEAKING
                else VoiceState.SILENT
            )
            self._to_state = voice_state
            self._step = 0
            self.state = RenderState.TRANSITION
        self.transitions += 1

    def _advance_players(self) -> bool:
        """Avanza las posiciones. Devuelve True si se rebobinó el clip de habla.

        El clip de silencio avanza siempre, para que parpadeos y respiración
        sigan su curso y la vuelta al silencio no se note.

        El clip de habla solo avanza si está en uso. Tras `resume_window_ms` de
        silencio se rebobina al primer fotograma: así el habla siempre empieza
        desde la pose inicial del clip en vez de desde una boca abierta a medias.
        """
        self.silent_player.advance(1.0)

        speaking_visible = self.state is not RenderState.SILENT
        if speaking_visible:
            self.speaking_player.advance(1.0)
            self._silent_frames = 0
            self._speaking_rewound = False
            return False

        self._silent_frames += 1
        if not self._speaking_rewound and self._silent_frames >= self.resume_window_frames:
            self.speaking_player.rewind()
            self._speaking_rewound = True
            return True
        return False

    def _compose(self) -> tuple[np.ndarray, bool, float]:
        if self.state is RenderState.SILENT:
            return self.silent.frame(self.silent_player.index), False, 0.0
        if self.state is RenderState.SPEAKING:
            return self.speaking.frame(self.speaking_player.index), False, 0.0

        steps = self.transition_steps
        self._step += 1
        raw = self._step / steps if steps else 1.0
        if raw >= 1.0:
            self.state = (
                RenderState.SPEAKING if self._to_state is VoiceState.SPEAKING
                else RenderState.SILENT
            )
            self._step = 0
            frame = (
                self.speaking.frame(self.speaking_player.index)
                if self.state is RenderState.SPEAKING
                else self.silent.frame(self.silent_player.index)
            )
            return frame, False, 1.0

        weight = _smoothstep(raw)
        src = self._frame_for(self._from_state)
        dst = self._frame_for(self._to_state)
        out = self._blend(src, dst, weight)
        return out, True, raw

    def _frame_for(self, state: VoiceState) -> np.ndarray:
        if state is VoiceState.SPEAKING:
            return self.speaking.frame(self.speaking_player.index)
        return self.silent.frame(self.silent_player.index)

    def _blend(self, src: np.ndarray, dst: np.ndarray, weight: float) -> np.ndarray:
        if self._buffer is None or self._buffer.shape != src.shape:
            self._buffer = np.empty_like(src)
        cv2.addWeighted(src, 1.0 - weight, dst, weight, 0.0, dst=self._buffer)
        return self._buffer
