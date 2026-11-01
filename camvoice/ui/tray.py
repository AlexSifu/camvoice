"""Icono en el área de notificación, para funcionar en segundo plano.

Al cerrar la ventana la aplicación sigue corriendo con un icono junto al reloj,
como cualquier utilidad de fondo (Slack, Steam, OBS). El icono **cambia de
color con el estado** del detector, así que se ve de un vistazo si está
detectando voz sin abrir la ventana.

## Qué oculta y qué no

Conviene no confundirse: esto quita la ventana de la barra de tareas y del
conmutador Alt+Tab. **No oculta la aplicación del sistema.** Sigue siendo
visible en:

* el **Administrador de tareas** (proceso `camvoice.exe`);
* el propio **icono del área de notificación**, que en Windows 11 puede estar
  desplegado;
* la **lista de cámaras** de la aplicación de videollamada, donde aparece
  «CamVoice Virtual Camera».

Es minimización a bandeja estándar, no evasión. Si alguien mira la lista de
cámaras del programa de videollamada, el nombre está ahí.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

__all__ = ["TrayController", "tray_available", "make_state_icon"]

STATE_COLORS = {
    "SILENT": ("#4a6b8a", "Silencio"),
    "SPEAKING": ("#3fb950", "Voz detectada"),
    "TRANSITION": ("#d29922", "Transición"),
    "GESTURE": ("#a371f7", "Gesto"),
    "DEGRADED": ("#f85149", "Degradado (clip con fallos)"),
    "VIDEO_PAUSED": ("#8b949e", "Vídeo en pausa (imagen congelada)"),
    "-": ("#6e7681", "Detenido"),
}


def tray_available() -> bool:
    return bool(QSystemTrayIcon.isSystemTrayAvailable())


def make_state_icon(state: str, size: int = 32) -> QIcon:
    """Icono generado al vuelo: un círculo del color del estado.

    Se dibuja en código y no se empaqueta un archivo porque así el icono
    *es* el indicador de estado: cambia de color según lo que esté pasando, y
    no hay que mantener cuatro PNG sincronizados con la lógica.
    """
    color_hex, _ = STATE_COLORS.get(state, STATE_COLORS["-"])
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        margen = max(2, size // 10)
        # Anillo exterior oscuro: mantiene el icono visible tanto sobre una
        # barra de tareas clara como sobre una oscura.
        painter.setBrush(QColor("#13161a"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.setBrush(QColor(color_hex))
        painter.drawEllipse(margen, margen, size - 2 * margen, size - 2 * margen)
    finally:
        painter.end()
    return QIcon(pixmap)


class TrayController:
    """Icono de bandeja con menú, sincronizado con el estado de la sesión."""

    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_quit: Callable[[], None],
        on_gesture: Callable[[str], None] | None = None,
        on_hotkeys_toggled: Callable[[bool], None] | None = None,
        on_speech_toggled: Callable[[], None] | None = None,
        parent=None,  # noqa: ANN001
    ) -> None:
        self._on_show = on_show
        self._on_gesture = on_gesture
        self._on_hotkeys_toggled = on_hotkeys_toggled
        self._on_speech_toggled = on_speech_toggled
        self._on_start = on_start
        self._on_stop = on_stop

        self.icon = QSystemTrayIcon(make_state_icon("-"), parent)
        self.icon.setToolTip("Cámara virtual por voz — detenida")

        menu = QMenu(parent)
        self._estado_action = QAction("Detenido", menu)
        self._estado_action.setEnabled(False)
        menu.addAction(self._estado_action)
        menu.addSeparator()

        self._mostrar = QAction("Mostrar ventana", menu)
        self._mostrar.triggered.connect(lambda: on_show())
        menu.addAction(self._mostrar)

        self._iniciar = QAction("Iniciar", menu)
        self._iniciar.triggered.connect(lambda: on_start())
        menu.addAction(self._iniciar)

        self._detener = QAction("Detener", menu)
        self._detener.triggered.connect(lambda: on_stop())
        self._detener.setEnabled(False)
        menu.addAction(self._detener)

        # Gestos: con el ratón desde la bandeja, sin abrir la ventana.
        self.gestures_menu = QMenu("Gestos", menu)
        menu.addMenu(self.gestures_menu)
        self.hotkeys_action = QAction("Atajos de teclado activos", menu)
        self.hotkeys_action.setCheckable(True)
        self.hotkeys_action.setChecked(True)
        self.hotkeys_action.toggled.connect(self._hotkeys_toggled)
        self._gesture_names: tuple[str, ...] = ()
        self.set_gestures((), enabled=False)

        # Control de habla: estado inequívoco y, en modo manual, el interruptor.
        menu.addSeparator()
        self.speech_state_action = QAction("Habla: BLOQUEADA", menu)
        self.speech_state_action.setEnabled(False)
        menu.addAction(self.speech_state_action)
        self.speech_toggle_action = QAction("Autorizar habla (modo manual)", menu)
        self.speech_toggle_action.setEnabled(False)
        self.speech_toggle_action.triggered.connect(lambda _=False: self._speech_toggle())
        menu.addAction(self.speech_toggle_action)
        self._speech_key: tuple = ()

        menu.addSeparator()
        salir = QAction("Salir", menu)
        salir.triggered.connect(lambda: on_quit())
        menu.addAction(salir)

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)
        self._menu = menu
        self._last_state = ""

    # ------------------------------------------------------------------ #

    def _on_activated(self, razon) -> None:  # noqa: ANN001
        # Doble clic o clic normal devuelve la ventana. El clic derecho lo
        # consume el menú contextual, así que no llega aquí.
        if razon in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self._on_show()

    def _hotkeys_toggled(self, on: bool) -> None:
        if self._on_hotkeys_toggled is not None:
            self._on_hotkeys_toggled(bool(on))

    def set_hotkeys_checked(self, on: bool) -> None:
        """Refleja el estado sin volver a avisar (evita el eco)."""
        prev = self.hotkeys_action.blockSignals(True)
        self.hotkeys_action.setChecked(bool(on))
        self.hotkeys_action.blockSignals(prev)

    def set_gestures(self, names, *, enabled: bool, hotkeys: dict[str, str] | None = None) -> None:  # noqa: ANN001
        """Reconstruye el submenú Gestos solo si cambió la lista."""
        from ..gestures import CANCEL, PAUSE, gesture_label

        names = tuple(names)
        clave = (names, bool(enabled), tuple(sorted((hotkeys or {}).items())))
        if getattr(self, "_gestures_key", None) == clave:
            return
        self._gestures_key = clave
        menu = self.gestures_menu
        menu.clear()
        if not names or not enabled:
            vacio = QAction(
                "Sin gestos (inicia en modo director con clips de gesto)" if not names
                else "Inicia la cámara para usar gestos",
                menu,
            )
            vacio.setEnabled(False)
            menu.addAction(vacio)
        else:
            for name in names:
                texto = gesture_label(name)
                if hotkeys and hotkeys.get(name):
                    texto += f"	{hotkeys[name]}"
                accion = QAction(texto, menu)
                accion.triggered.connect(lambda _=False, n=name: self._gesture(n))
                menu.addAction(accion)
            menu.addSeparator()
            cancelar = QAction(gesture_label(CANCEL), menu)
            cancelar.triggered.connect(lambda _=False: self._gesture(CANCEL))
            menu.addAction(cancelar)
        if enabled:
            pausa = QAction(gesture_label(PAUSE), menu)
            pausa.setToolTip("Congela la última imagen; se indica en la ventana y aquí.")
            pausa.triggered.connect(lambda _=False: self._gesture(PAUSE))
            menu.addAction(pausa)
        menu.addSeparator()
        menu.addAction(self.hotkeys_action)
        self._gesture_names = names

    def _gesture(self, name: str) -> None:
        if self._on_gesture is not None:
            self._on_gesture(name)

    def _speech_toggle(self) -> None:
        if self._on_speech_toggled is not None:
            self._on_speech_toggled()

    def update_speech(self, auth, *, running: bool) -> None:  # noqa: ANN001 - SpeechAuthorization | None
        """Refleja la autorización de habla en el menú (solo si cambió)."""
        if auth is None:
            key: tuple = ("none", running)
            texto, toggle_text, toggle_on = "Habla: BLOQUEADA (sin sesión)", "Autorizar habla (modo manual)", False
        else:
            manual = auth.mode == "manual"
            key = (auth.allowed, auth.reason, auth.mode, auth.manual_allowed, running)
            texto = f"Habla: {auth.label} — {auth.reason}"
            toggle_text = "Bloquear habla" if auth.manual_allowed else "Autorizar habla"
            if not manual:
                toggle_text += " (solo en modo manual)"
            toggle_on = manual and running
        if key == self._speech_key:
            return
        self._speech_key = key
        self.speech_state_action.setText(texto)
        self.speech_toggle_action.setText(toggle_text)
        self.speech_toggle_action.setEnabled(toggle_on)

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def notify(self, titulo: str, mensaje: str) -> None:
        """Globo del sistema. Silencioso si el escritorio no los admite."""
        try:
            self.icon.showMessage(titulo, mensaje, make_state_icon("-"), 4000)
        except Exception:
            pass

    def update_state(self, state: str, *, running: bool, detail: str = "") -> None:
        """Refresca icono, tooltip y menú. Solo repinta si el estado cambió."""
        clave = state if running else "-"
        if clave != self._last_state:
            self.icon.setIcon(make_state_icon(clave))
            self._last_state = clave

        _, etiqueta = STATE_COLORS.get(clave, STATE_COLORS["-"])
        self._estado_action.setText(etiqueta)
        habla = self.speech_state_action.text() if running else ""
        self.icon.setToolTip(
            f"Cámara virtual por voz — {etiqueta.lower()}"
            + (f"\n{detail}" if detail else "")
            + (f"\n{habla}" if habla else "")
        )
        self._iniciar.setEnabled(not running)
        self._detener.setEnabled(running)
