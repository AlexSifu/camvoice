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
        parent=None,  # noqa: ANN001
    ) -> None:
        self._on_show = on_show
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
        self.icon.setToolTip(
            f"Cámara virtual por voz — {etiqueta.lower()}"
            + (f"\n{detail}" if detail else "")
        )
        self._iniciar.setEnabled(not running)
        self._detener.setEnabled(running)
