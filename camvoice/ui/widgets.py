"""Widgets reutilizables y hoja de estilo de la interfaz.

Separado de `app.py` para que la ventana se lea como lo que hace y no como un
muro de llamadas a `setStyleSheet`.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

__all__ = ["PercentSlider", "SectionTitle", "Card", "STYLESHEET", "state_badge_style"]


# --------------------------------------------------------------------------- #
# Estilo
# --------------------------------------------------------------------------- #

COLORS = {
    "bg": "#13161a",
    "surface": "#1b1f25",
    "surface_alt": "#22272e",
    "border": "#2d343d",
    "text": "#e6edf3",
    "text_dim": "#8b949e",
    "accent": "#2f81f7",
    "accent_dim": "#1f6feb",
    "ok": "#3fb950",
    "warn": "#d29922",
    "err": "#f85149",
}

STYLESHEET = f"""
QWidget {{
    background: {COLORS["bg"]};
    color: {COLORS["text"]};
    font-size: 13px;
}}
QLabel#dim {{ color: {COLORS["text_dim"]}; }}
QLabel#value {{
    color: {COLORS["text"]};
    font-family: Consolas, "Cascadia Mono", monospace;
    background: {COLORS["surface_alt"]};
    border-radius: 4px;
    padding: 2px 6px;
}}
QLabel#sectionTitle {{
    font-size: 12px;
    font-weight: 600;
    color: {COLORS["text_dim"]};
    letter-spacing: 1px;
}}

QFrame#card, QWidget#card {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}

QTabWidget::pane {{
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    background: {COLORS["surface"]};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {COLORS["text_dim"]};
    padding: 8px 14px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    background: {COLORS["surface"]};
    color: {COLORS["text"]};
    border-color: {COLORS["border"]};
    border-bottom-color: {COLORS["surface"]};
}}
QTabBar::tab:hover:!selected {{ color: {COLORS["text"]}; }}

QPushButton {{
    background: {COLORS["surface_alt"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {COLORS["accent"]}; }}
QPushButton:disabled {{ color: #5a626b; border-color: #23282f; }}
QPushButton#primary {{
    background: {COLORS["accent_dim"]};
    border-color: {COLORS["accent"]};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {COLORS["accent"]}; }}
QPushButton#primary:disabled {{ background: #1d2630; border-color: #243040; }}
QPushButton#link {{
    background: transparent;
    border: none;
    color: {COLORS["accent"]};
    padding: 2px 4px;
    text-align: left;
}}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {COLORS["surface_alt"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 6px 8px;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {COLORS["accent"]};
}}
QComboBox QAbstractItemView {{
    background: {COLORS["surface_alt"]};
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["accent_dim"]};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {COLORS["border"]};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {COLORS["accent"]};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {COLORS["text"]};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {COLORS["accent"]}; }}

QProgressBar {{
    background: {COLORS["surface_alt"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    text-align: center;
    height: 18px;
}}
QProgressBar::chunk {{ background: {COLORS["accent"]}; border-radius: 5px; }}

QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {COLORS["border"]};
    border-radius: 4px;
    background: {COLORS["surface_alt"]};
}}
QCheckBox::indicator:checked {{
    background: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLORS["border"]}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLORS["text_dim"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QStatusBar {{ color: {COLORS["text_dim"]}; }}
QToolTip {{
    background: {COLORS["surface_alt"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["border"]};
    padding: 5px;
}}
"""

STATE_COLORS = {
    "SILENT": ("Silencio", "#1d2b3a", "#9fc5e8"),
    "SPEAKING": ("Voz detectada", "#14301f", "#56d364"),
    "TRANSITION": ("Transición", "#332a12", "#e3b341"),
    "-": ("Detenido", COLORS["surface_alt"], COLORS["text_dim"]),
}


def state_badge_style(key: str) -> tuple[str, str]:
    """Devuelve (texto, hoja de estilo) para el indicador de estado."""
    texto, fondo, color = STATE_COLORS.get(key, STATE_COLORS["-"])
    estilo = (
        f"background:{fondo}; color:{color}; border-radius:8px;"
        "font-size:18px; font-weight:600; letter-spacing:0.5px;"
    )
    return texto, estilo


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #


class SectionTitle(QLabel):
    def __init__(self, texto: str) -> None:
        super().__init__(texto.upper())
        self.setObjectName("sectionTitle")


class Card(QWidget):
    """Contenedor con fondo y borde, para agrupar controles relacionados."""

    def __init__(self, titulo: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(14, 12, 14, 12)
        self.layout_.setSpacing(8)
        if titulo:
            self.layout_.addWidget(SectionTitle(titulo))

    def add(self, widget: QWidget) -> QWidget:
        self.layout_.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:  # noqa: ANN001
        self.layout_.addLayout(layout)


class PercentSlider(QWidget):
    """Deslizador con etiqueta, valor y botón de reinicio.

    El valor se muestra siempre: un deslizador sin número obliga a adivinar, y
    aquí los porcentajes importan porque son reproducibles entre sesiones.
    """

    changed = Signal(float)

    def __init__(
        self,
        etiqueta: str,
        *,
        minimo: float = 0.0,
        maximo: float = 100.0,
        defecto: float = 0.0,
        sufijo: str = "%",
        decimales: int = 0,
        ayuda: str = "",
    ) -> None:
        super().__init__()
        self._escala = 10 ** decimales
        self._decimales = decimales
        self._sufijo = sufijo
        self._defecto = defecto

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(3)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(6)
        titulo = QLabel(etiqueta)
        if ayuda:
            titulo.setToolTip(ayuda)
            self.setToolTip(ayuda)
        cabecera.addWidget(titulo)
        cabecera.addStretch(1)

        self.valor_label = QLabel()
        self.valor_label.setObjectName("value")
        self.valor_label.setMinimumWidth(56)
        self.valor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        cabecera.addWidget(self.valor_label)

        self._reset = QPushButton("↺")
        self._reset.setObjectName("link")
        self._reset.setFixedWidth(22)
        self._reset.setToolTip(f"Volver a {defecto:g}{sufijo}")
        self._reset.clicked.connect(lambda: self.set_value(self._defecto))
        cabecera.addWidget(self._reset)
        raiz.addLayout(cabecera)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(minimo * self._escala), int(maximo * self._escala))
        self.slider.setValue(int(defecto * self._escala))
        self.slider.valueChanged.connect(self._on_slider)
        raiz.addWidget(self.slider)

        self._refresh_label()

    # ------------------------------------------------------------------ #

    def _on_slider(self, _bruto: int) -> None:
        self._refresh_label()
        self.changed.emit(self.value())

    def _refresh_label(self) -> None:
        self.valor_label.setText(
            f"{self.value():.{self._decimales}f}{self._sufijo}"
        )

    def value(self) -> float:
        return self.slider.value() / self._escala

    def set_value(self, valor: float) -> None:
        self.slider.setValue(int(round(valor * self._escala)))

    def set_silently(self, valor: float) -> None:
        """Cambia el valor sin emitir la señal, para cargar la configuración."""
        anterior = self.slider.blockSignals(True)
        self.slider.setValue(int(round(valor * self._escala)))
        self.slider.blockSignals(anterior)
        self._refresh_label()

    def bind(self, callback: Callable[[float], None]) -> None:
        self.changed.connect(callback)


def labelled_row(texto: str, widget: QWidget, *, ayuda: str = "") -> QWidget:
    """Fila con etiqueta a la izquierda y control a la derecha."""
    fila = QWidget()
    layout = QHBoxLayout(fila)
    layout.setContentsMargins(0, 0, 0, 0)
    etiqueta = QLabel(texto)
    etiqueta.setMinimumWidth(110)
    if ayuda:
        etiqueta.setToolTip(ayuda)
        widget.setToolTip(ayuda)
    layout.addWidget(etiqueta)
    layout.addWidget(widget, 1)
    return fila


def combo(opciones: list[tuple[str, object]], *, ayuda: str = "") -> QComboBox:
    caja = QComboBox()
    for texto, dato in opciones:
        caja.addItem(texto, dato)
    if ayuda:
        caja.setToolTip(ayuda)
    return caja


def spin_double(minimo: float, maximo: float, paso: float, valor: float) -> QDoubleSpinBox:
    caja = QDoubleSpinBox()
    caja.setRange(minimo, maximo)
    caja.setSingleStep(paso)
    caja.setValue(valor)
    return caja
