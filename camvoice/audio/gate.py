"""Histéresis temporal: de decisiones por trama a un estado estable de voz.

Un VAD por trama parpadea: entre palabras, en consonantes sordas y con ruido de
fondo alterna voz/silencio varias veces por segundo. Conmutar el video con eso
sería inservible. Esta puerta convierte ese tren de bits en dos estados
estables, con cinco controles independientes:

  attack_ms          voz acumulada necesaria para abrir
  pause_tolerance_ms hueco corto que no reinicia la acumulación de ataque
  release_ms         silencio continuo necesario para cerrar (hangover)
  min_speaking_ms    permanencia mínima abierto
  min_silent_ms      permanencia mínima cerrado

La puerta es pura y determinista: solo depende de la secuencia de booleanos que
recibe y avanza su reloj en pasos de `frame_ms`. Eso la hace comprobable sin
micrófono (ver tests/test_gate.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import VadConfig

__all__ = ["VoiceState", "GateSnapshot", "SpeechGate"]


class VoiceState(Enum):
    SILENT = "SILENT"
    SPEAKING = "SPEAKING"


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    state: VoiceState
    changed: bool
    """True solo en la trama en la que ocurre el cambio."""

    time_in_state_ms: int
    voiced_run_ms: int
    silence_run_ms: int
    transitions: int


class SpeechGate:
    MIN_ATTACK_DUTY = 0.6
    """Fracción mínima de voz dentro de la ventana de acumulación del ataque.

    Es la defensa contra el patrón que produce el ruido de fondo: positivos
    sueltos y espaciados. Con una tolerancia a pausas generosa, esos positivos
    acabarían sumando `attack_ms` por acumulación lenta y la puerta se abriría
    sola. Exigir que al menos el 60 % de la ventana sea voz distingue "dos
    sílabas separadas por un hueco" (duty alto) de "ruido que da positivo una
    trama de cada cinco" (duty bajo), sin pedirle nada al usuario.
    """

    def __init__(self, cfg: VadConfig, frame_ms: int | None = None) -> None:
        if frame_ms is None:
            frame_ms = cfg.frame_ms
        if frame_ms <= 0:
            raise ValueError("frame_ms debe ser positivo.")
        self.cfg = cfg
        self.frame_ms = int(frame_ms)
        self.reset()

    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        self.state = VoiceState.SILENT
        self.transitions = 0
        self._time_in_state_ms = 0
        self._voiced_run_ms = 0
        self._silence_run_ms = 0
        self._gap_ms = 0
        self._attack_window_ms = 0
        self._elapsed_ms = 0

    # ------------------------------------------------------------------ #

    @property
    def expected_latency_ms(self) -> int:
        """Latencia de apertura en el mejor caso (voz continua)."""
        return self.cfg.attack_ms

    def update(self, voiced: bool) -> GateSnapshot:
        """Avanza la puerta una trama. Devuelve el estado resultante."""
        self._elapsed_ms += self.frame_ms
        self._time_in_state_ms += self.frame_ms
        changed = False

        if self.state is VoiceState.SILENT:
            changed = self._update_silent(voiced)
        else:
            changed = self._update_speaking(voiced)

        return GateSnapshot(
            state=self.state,
            changed=changed,
            time_in_state_ms=self._time_in_state_ms,
            voiced_run_ms=self._voiced_run_ms,
            silence_run_ms=self._silence_run_ms,
            transitions=self.transitions,
        )

    # ------------------------------------------------------------------ #

    def _update_silent(self, voiced: bool) -> bool:
        cfg = self.cfg
        if voiced:
            self._voiced_run_ms += self.frame_ms
            self._gap_ms = 0
            if self._attack_window_ms == 0:
                self._attack_window_ms = self.frame_ms
            else:
                self._attack_window_ms += self.frame_ms
        else:
            self._gap_ms += self.frame_ms
            if self._voiced_run_ms:
                self._attack_window_ms += self.frame_ms
            if self._gap_ms > cfg.pause_tolerance_ms:
                # Hueco demasiado largo: la acumulación de ataque se descarta.
                self._voiced_run_ms = 0
                self._attack_window_ms = 0
                self._gap_ms = 0

        if (
            self._voiced_run_ms >= cfg.attack_ms
            and self._time_in_state_ms >= cfg.min_silent_ms
            and self._attack_duty() >= self.MIN_ATTACK_DUTY
        ):
            self._enter(VoiceState.SPEAKING)
            return True
        return False

    def _attack_duty(self) -> float:
        """Proporción de voz dentro de la ventana de acumulación actual."""
        if self._attack_window_ms <= 0:
            return 1.0
        return self._voiced_run_ms / self._attack_window_ms

    def _update_speaking(self, voiced: bool) -> bool:
        cfg = self.cfg
        if voiced:
            self._silence_run_ms = 0
            self._voiced_run_ms += self.frame_ms
        else:
            self._silence_run_ms += self.frame_ms

        if (
            self._silence_run_ms >= cfg.release_ms
            and self._time_in_state_ms >= cfg.min_speaking_ms
        ):
            self._enter(VoiceState.SILENT)
            return True
        return False

    def _enter(self, state: VoiceState) -> None:
        self.state = state
        self.transitions += 1
        self._time_in_state_ms = 0
        self._voiced_run_ms = 0
        self._silence_run_ms = 0
        self._gap_ms = 0
        self._attack_window_ms = 0
