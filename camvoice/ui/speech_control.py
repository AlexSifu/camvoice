"""Tarjeta «Control de habla»: modo, permiso manual, atajo y diagnóstico.

Muestra por separado la **voz física detectada** (VAD + puerta) y el **habla
autorizada** (lo que de verdad llega al motor), más el estado leído de Teams
con su antigüedad. En modo manual mantiene un aviso persistente: CamVoice no
puede verificar el mute real de Teams/Meet.

La tarjeta no decide nada: solo refleja `SpeechAuthorization` y reenvía las
acciones del usuario a la ventana principal (que las pasa a la sesión).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

from ..config import SpeechControlConfig
from ..gestures import HotkeyError, validate_hotkey
from ..meeting import MeetingState
from ..permission import (
    MODE_LABELS,
    MODE_MANUAL,
    MODE_TEAMS_AUTO,
    MODES,
    SpeechAuthorization,
    describe_meeting,
)
from .widgets import Card, combo, labelled_row

__all__ = ["SpeechControlCard", "AUTHORIZE_TEXT", "BLOCK_TEXT"]

AUTHORIZE_TEXT = "Autorizar habla"
BLOCK_TEXT = "Bloquear habla"

_STYLE_ALLOWED = "background:#1f3a24; color:#7ee787; padding:6px; border-radius:6px; font-weight:600;"
_STYLE_BLOCKED = "background:#3a1f22; color:#ffb3b8; padding:6px; border-radius:6px; font-weight:600;"
_STYLE_WARNING = "background:#332a12; color:#e3b341; padding:6px; border-radius:6px;"
_STYLE_INFO = "color:#8b949e; font-size:11px;"


class SpeechControlCard(Card):
    def __init__(
        self,
        cfg: SpeechControlConfig,
        *,
        on_mode: Callable[[str], None],
        on_toggle: Callable[[], None],
        on_hotkey: Callable[[str], None],
    ) -> None:
        super().__init__("control de habla")
        self.cfg = cfg
        self._on_mode = on_mode
        self._on_toggle = on_toggle
        self._on_hotkey = on_hotkey
        self._loading = True

        self.mode_combo = combo(
            [(MODE_LABELS[m], m) for m in MODES],
            ayuda=(
                "Automático (Teams): el vídeo de habla solo sale cuando CamVoice lee que el "
                "micrófono de la reunión de Teams está activo y además hay voz local.\n"
                "Manual: tú autorizas o bloqueas el habla; CamVoice no verifica el mute de "
                "Teams ni de Meet. Un mute confirmado de Teams prevalece igualmente."
            ),
        )
        idx = self.mode_combo.findData(cfg.mode if cfg.mode in MODES else MODE_TEAMS_AUTO)
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.add(labelled_row("Control de habla", self.mode_combo))

        fila = QHBoxLayout()
        fila.setSpacing(8)
        self.toggle_button = QPushButton(AUTHORIZE_TEXT)
        self.toggle_button.setMinimumHeight(34)
        self.toggle_button.setToolTip(
            "Solo en modo Manual. No abre el micrófono de Teams: solo permite o veta "
            "que CamVoice elija vídeo de habla cuando detecta tu voz."
        )
        self.toggle_button.clicked.connect(lambda: self._on_toggle())
        fila.addWidget(self.toggle_button, 2)
        self.hotkey_edit = QLineEdit(cfg.manual_hotkey)
        self.hotkey_edit.setPlaceholderText("Atajo global (p. ej. Ctrl+Alt+F10)")
        self.hotkey_edit.setToolTip(
            "Alterna Autorizar/Bloquear desde cualquier aplicación. Mismas reglas que los "
            "atajos de gestos; no puede repetir uno de ellos. Vacío = sin atajo."
        )
        self.hotkey_edit.editingFinished.connect(self._hotkey_changed)
        fila.addWidget(self.hotkey_edit, 2)
        self.add_layout(fila)

        self.state_label = QLabel()
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setWordWrap(True)
        self.add(self.state_label)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(_STYLE_INFO)
        self.add(self.detail_label)

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(_STYLE_WARNING)
        self.warning_label.setVisible(False)
        self.add(self.warning_label)

        self._loading = False
        self._last_key: tuple = ()
        self.update_status(None, physical_speaking=False, running=False)

    # ------------------------------------------------------------------ #

    def _mode_changed(self, _index: int) -> None:
        if self._loading:
            return
        mode = self.mode_combo.currentData()
        if mode in MODES:
            self._on_mode(mode)

    def _hotkey_changed(self) -> None:
        if self._loading:
            return
        texto = self.hotkey_edit.text().strip()
        if texto:
            try:
                texto = validate_hotkey(texto).text
            except HotkeyError as exc:
                self.hotkey_edit.setToolTip(str(exc))
                self.hotkey_edit.setStyleSheet("border:1px solid #f85149;")
                return
        self.hotkey_edit.setStyleSheet("")
        self.hotkey_edit.setText(texto)
        self._on_hotkey(texto)

    def set_mode_silently(self, mode: str) -> None:
        idx = self.mode_combo.findData(mode)
        if idx >= 0 and idx != self.mode_combo.currentIndex():
            self._loading = True
            self.mode_combo.setCurrentIndex(idx)
            self._loading = False

    # ------------------------------------------------------------------ #

    def update_status(
        self,
        auth: SpeechAuthorization | None,
        *,
        physical_speaking: bool,
        running: bool,
    ) -> None:
        """Refresca etiquetas y botón; solo toca los widgets si algo cambió."""
        if auth is None:
            key = ("none", running)
            if key == self._last_key:
                return
            self._last_key = key
            self.state_label.setText("Habla: BLOQUEADA (sin sesión)")
            self.state_label.setStyleSheet(_STYLE_BLOCKED)
            self.detail_label.setText("")
            self.warning_label.setVisible(False)
            self.toggle_button.setEnabled(False)
            self.toggle_button.setText(AUTHORIZE_TEXT)
            return

        manual = auth.mode == MODE_MANUAL
        meeting = auth.meeting
        age_bucket = int(meeting.age_ms() // 500) if meeting.t_monotonic > 0 else -1
        key = (
            auth.allowed, auth.reason, auth.mode, auth.manual_allowed, meeting.state,
            meeting.detail, physical_speaking, running, age_bucket,
        )
        if key == self._last_key:
            return
        self._last_key = key

        self.state_label.setText(f"Habla: {auth.label} — {auth.reason}")
        self.state_label.setStyleSheet(_STYLE_ALLOWED if auth.allowed else _STYLE_BLOCKED)

        fisica = "sí" if physical_speaking else "no"
        autorizada = "sí" if (auth.allowed and physical_speaking) else "no"
        lineas = [
            f"Voz física detectada: {fisica}  ·  Habla autorizada: {autorizada}",
            describe_meeting(meeting),
        ]
        if not manual and meeting.state is MeetingState.UNKNOWN:
            lineas.append(
                "Modo automático bloqueado mientras no se pueda leer el micrófono de Teams. "
                "Si Teams no expone el botón, usa el control Manual (no verifica el mute)."
            )
        elif not manual and meeting.state is MeetingState.NO_MEETING:
            lineas.append("Sin reunión de Teams visible: el habla queda bloqueada en modo automático.")
        self.detail_label.setText("\n".join(lineas))

        if auth.warning:
            self.warning_label.setText("⚠ " + auth.warning)
            self.warning_label.setVisible(True)
        else:
            self.warning_label.setVisible(False)

        self.toggle_button.setEnabled(manual and running)
        self.toggle_button.setText(BLOCK_TEXT if auth.manual_allowed else AUTHORIZE_TEXT)
        if manual and meeting.state is MeetingState.MUTED:
            self.toggle_button.setToolTip(
                "Teams está silenciado: ese estado prevalece y no se puede anular desde CamVoice."
            )
        elif not manual:
            self.toggle_button.setToolTip("Solo en modo Manual.")
        else:
            self.toggle_button.setToolTip(
                "No abre el micrófono de Teams: solo permite o veta el vídeo de habla."
            )
