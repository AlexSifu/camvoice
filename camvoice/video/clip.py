"""Posición de reproducción de un clip en bucle.

Separado del compositor para poder probarlo solo: no toca píxeles, solo lleva
la cuenta de en qué fotograma vamos.

El modo `pingpong` resuelve la costura del bucle sin pedirle nada al usuario:
al llegar al final se reproduce hacia atrás. Un clip de cara hablando se ve
natural en reversa, y así el salto del último al primer fotograma —que en un
bucle normal es un corte visible— desaparece.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ClipPlayer", "PlayerState"]


@dataclass(frozen=True, slots=True)
class PlayerState:
    index: int
    position: float
    direction: int
    wrapped: bool
    """True en el fotograma en el que se rebota o se reinicia el bucle."""


class ClipPlayer:
    def __init__(
        self,
        frame_count: int,
        *,
        loop_mode: str = "pingpong",
        speed: float = 1.0,
    ) -> None:
        if frame_count <= 0:
            raise ValueError("Un clip debe tener al menos un fotograma.")
        self.frame_count = int(frame_count)
        self.loop_mode = loop_mode
        self.speed = float(speed)
        self.position = 0.0
        self.direction = 1
        self.loops = 0

    # ------------------------------------------------------------------ #

    @property
    def index(self) -> int:
        return min(self.frame_count - 1, max(0, int(round(self.position))))

    @property
    def last(self) -> float:
        return float(self.frame_count - 1)

    def rewind(self) -> None:
        self.position = 0.0
        self.direction = 1

    def seek(self, position: float) -> None:
        self.position = min(self.last, max(0.0, float(position)))

    # ------------------------------------------------------------------ #

    def advance(self, steps: float = 1.0) -> PlayerState:
        """Avanza `steps` fotogramas de salida, escalados por la velocidad."""
        if self.frame_count == 1:
            return PlayerState(0, 0.0, 1, False)

        delta = steps * self.speed
        wrapped = False
        if self.loop_mode == "pingpong":
            wrapped = self._advance_pingpong(delta)
        else:
            wrapped = self._advance_loop(delta)
        return PlayerState(self.index, self.position, self.direction, wrapped)

    def _advance_loop(self, delta: float) -> bool:
        span = float(self.frame_count)
        self.position += delta
        if 0.0 <= self.position < span:
            return False
        self.position %= span
        self.loops += 1
        return True

    def _advance_pingpong(self, delta: float) -> bool:
        last = self.last
        if last <= 0:
            return False
        self.position += delta * self.direction
        wrapped = False
        # Bucle: con velocidades altas el rebote puede cruzar varios extremos.
        for _ in range(8):
            if self.position > last:
                self.position = 2.0 * last - self.position
                self.direction = -1
                self.loops += 1
                wrapped = True
            elif self.position < 0.0:
                self.position = -self.position
                self.direction = 1
                self.loops += 1
                wrapped = True
            else:
                break
        else:  # velocidad absurda: anclar en un extremo en vez de divergir
            self.position = min(last, max(0.0, self.position))
        return wrapped
