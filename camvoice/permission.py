"""Autorización efectiva de habla: el único punto donde se decide si la voz
física detectada puede convertirse en vídeo de habla.

Se sitúa **después** del VAD y de la `SpeechGate` (que no se tocan) y
**antes** del motor de render / `Director`:

    voz_autorizada   = permission.effective_allowed()
    habla_visual     = voz_autorizada and estado_gate is SPEAKING

Dos modos («Control de habla»):

* **Automático (Teams)** — habla solo si el proveedor de estado de reunión
  publica `UNMUTED` (lectura fresca) y además hay voz local. `MUTED`,
  `NO_MEETING` y `UNKNOWN` bloquean.
* **Manual** — interruptor «Autorizar habla» / «Bloquear habla», con valor
  inicial *Bloquear* en cada arranque y al perder la sesión. No puede
  verificar el mute real de Teams/Meet y lo avisa de forma persistente. Aun
  así, un `MUTED` o `NO_MEETING` **confiables** de Teams prevalecen sobre el
  permiso manual: desde CamVoice nunca se anula un mute confirmado.

El modo manual no abre el micrófono de Teams ni toca el micrófono físico:
solo habilita o veta la selección de vídeo de habla de CamVoice.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .audio.gate import VoiceState
from .meeting import MeetingMuteProvider, MeetingSnapshot, MeetingState, NullMeetingProvider, state_label

__all__ = [
    "MODE_TEAMS_AUTO",
    "MODE_MANUAL",
    "MODES",
    "MODE_LABELS",
    "SPEECH_TOGGLE",
    "SPEECH_TOGGLE_LABEL",
    "SpeechAuthorization",
    "AuthorizedVoice",
    "SpeechPermissionController",
]

MODE_TEAMS_AUTO = "teams_auto"
MODE_MANUAL = "manual"
MODES: tuple[str, ...] = (MODE_TEAMS_AUTO, MODE_MANUAL)
MODE_LABELS: dict[str, str] = {
    MODE_TEAMS_AUTO: "Automático (Teams)",
    MODE_MANUAL: "Manual",
}

SPEECH_TOGGLE = "autorizar_habla"
"""Nombre del atajo global que alterna el permiso manual (no es un gesto)."""

SPEECH_TOGGLE_LABEL = "Autorizar / bloquear habla"

MANUAL_WARNING = "Modo manual: CamVoice no verifica el mute real de Teams/Meet."


@dataclass(frozen=True, slots=True)
class SpeechAuthorization:
    """Decisión actual, con el motivo en texto para la ventana y la bandeja."""

    allowed: bool
    reason: str
    mode: str
    manual_allowed: bool
    meeting: MeetingSnapshot
    warning: str = ""
    """Aviso persistente (modo manual) o vacío."""

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    @property
    def label(self) -> str:
        return "AUTORIZADA" if self.allowed else "BLOQUEADA"


@dataclass(frozen=True, slots=True)
class AuthorizedVoice:
    """Lo que recibe el motor: `VoiceStatus` filtrado por la autorización.

    Conserva los atributos que lee `RenderEngine._to_voice_input` (`state`,
    `mouth_target`, `micro_pause`, `t_monotonic`). Si no hay autorización, el
    estado es SILENT con boca cerrada y sin micro-pausa: una señal de silencio
    coherente, sin tocar el detector (sus métricas siguen en `physical`).
    """

    state: VoiceState
    mouth_target: float
    micro_pause: bool
    t_monotonic: float
    physical_state: VoiceState
    authorized: bool
    reason: str

    @property
    def physical_speaking(self) -> bool:
        return self.physical_state is VoiceState.SPEAKING

    @property
    def visual_speaking(self) -> bool:
        return self.state is VoiceState.SPEAKING


class SpeechPermissionController:
    """Combina modo, permiso manual y estado de la reunión. Seguro entre hilos.

    `evaluate()`/`authorize()` se llaman desde el hilo de render cada
    fotograma: solo leen una instantánea del proveedor (barata) y unos
    cuantos campos; no hacen I/O.
    """

    def __init__(
        self,
        provider: MeetingMuteProvider | None = None,
        *,
        mode: str = MODE_TEAMS_AUTO,
    ) -> None:
        self.provider: MeetingMuteProvider = provider or NullMeetingProvider()
        self._lock = threading.Lock()
        self._mode = mode if mode in MODES else MODE_TEAMS_AUTO
        self._manual_allowed = False
        self._last_allowed = False
        self._last: SpeechAuthorization | None = None
        self.blocked_edges = 0
        """Veces que la autorización pasó de permitida a bloqueada (diagnóstico)."""

    # ------------------------------------------------------------------ #
    # Modo y permiso manual
    # ------------------------------------------------------------------ #

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Cambia el modo. Cualquier cambio vuelve el permiso manual a *Bloquear*:
        cambiar de modo nunca autoriza habla por sí mismo."""
        if mode not in MODES:
            raise ValueError(f"Modo de control de habla desconocido: {mode!r}")
        with self._lock:
            self._mode = mode
            self._manual_allowed = False

    @property
    def manual_allowed(self) -> bool:
        return self._manual_allowed

    def set_manual(self, allowed: bool) -> None:
        with self._lock:
            self._manual_allowed = bool(allowed)

    def toggle_manual(self) -> bool:
        with self._lock:
            self._manual_allowed = not self._manual_allowed
            return self._manual_allowed

    def reset_manual(self) -> None:
        """Vuelve a *Bloquear*: en cada arranque y al perder la sesión."""
        with self._lock:
            self._manual_allowed = False

    # ------------------------------------------------------------------ #
    # Decisión
    # ------------------------------------------------------------------ #

    def evaluate(self) -> SpeechAuthorization:
        with self._lock:
            mode = self._mode
            manual = self._manual_allowed
        meeting = self.provider.snapshot()
        teams_text = meeting.describe()

        if mode == MODE_TEAMS_AUTO:
            if meeting.state is MeetingState.UNMUTED:
                auth = SpeechAuthorization(True, "Teams: micrófono activo", mode, manual, meeting)
            elif meeting.state is MeetingState.MUTED:
                auth = SpeechAuthorization(False, "Teams: micrófono silenciado", mode, manual, meeting)
            elif meeting.state is MeetingState.NO_MEETING:
                auth = SpeechAuthorization(False, teams_text, mode, manual, meeting)
            else:
                auth = SpeechAuthorization(
                    False,
                    "Teams: estado desconocido, habla bloqueada"
                    + (f" ({meeting.detail})" if meeting.detail else ""),
                    mode,
                    manual,
                    meeting,
                )
        else:
            # Manual: un MUTED confirmado de Teams prevalece siempre. Sin
            # reunión de Teams (p. ej. Meet en el navegador, Teams en segundo
            # plano) decide el permiso manual, con aviso persistente.
            if meeting.state is MeetingState.MUTED:
                auth = SpeechAuthorization(
                    False, "Teams: micrófono silenciado (prevalece sobre el permiso manual)",
                    mode, manual, meeting, warning=MANUAL_WARNING,
                )
            elif manual:
                auth = SpeechAuthorization(
                    True, "Permiso manual: habla autorizada (Teams no verificado)",
                    mode, manual, meeting, warning=MANUAL_WARNING,
                )
            else:
                auth = SpeechAuthorization(
                    False, "Permiso manual: habla bloqueada", mode, manual, meeting, warning=MANUAL_WARNING
                )

        with self._lock:
            if self._last_allowed and not auth.allowed:
                self.blocked_edges += 1
            self._last_allowed = auth.allowed
            self._last = auth
        return auth

    def effective_allowed(self) -> bool:
        return self.evaluate().allowed

    @property
    def last(self) -> SpeechAuthorization | None:
        """Última decisión calculada (para la UI, sin volver a evaluar)."""
        return self._last

    def authorize(self, status) -> AuthorizedVoice:  # noqa: ANN001 - VoiceStatus o similar
        """Filtra la instantánea del detector según la autorización actual."""
        auth = self.evaluate()
        physical = getattr(status, "state", VoiceState.SILENT)
        t = float(getattr(status, "t_monotonic", 0.0) or 0.0)
        if auth.allowed:
            return AuthorizedVoice(
                state=physical,
                mouth_target=float(getattr(status, "mouth_target", 0.0)),
                micro_pause=bool(getattr(status, "micro_pause", False)),
                t_monotonic=t,
                physical_state=physical,
                authorized=True,
                reason=auth.reason,
            )
        # Silencio coherente: sin boca abierta ni micro-pausa, con la marca
        # de tiempo real para que el motor no crea que el dato es viejo.
        return AuthorizedVoice(
            state=VoiceState.SILENT,
            mouth_target=0.0,
            micro_pause=False,
            t_monotonic=t or time.perf_counter(),
            physical_state=physical,
            authorized=False,
            reason=auth.reason,
        )


def describe_meeting(meeting: MeetingSnapshot) -> str:
    """Texto corto para la UI: estado + antigüedad de la lectura."""
    text = state_label(meeting.state)
    age = meeting.age_ms()
    if age != float("inf"):
        text += f" · hace {age:.0f} ms" if age < 10_000 else f" · hace {age / 1000:.0f} s"
    if meeting.detail:
        text += f" · {meeting.detail}"
    return text
