"""Pestaña de grabación guiada.

Muestra la cámara con guías de encuadre, cuenta atrás, y las señales del guion
en pantalla mientras graba. Para sola al cumplirse la duración.

La idea es que las condiciones que de verdad importan —medio segundo de boca
cerrada al principio, duración en el rango útil, cierre de boca al final— las
imponga el programa en vez de confiar en que el usuario recuerde un documento.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..record import (
    SILENT_PLAN,
    SPEAKING_PLAN,
    CameraInfo,
    ClipRecorder,
    RecordingPlan,
    draw_framing_guides,
    list_cameras,
)
from .widgets import Card, PercentSlider, combo, labelled_row

__all__ = ["RecorderTab"]


def _no_impone_ancho(etiqueta: QLabel) -> QLabel:
    """Hace que la etiqueta no dicte el ancho de su contenedor.

    Una QLabel con ajuste de línea y altura fija pide tanta anchura como haga
    falta para que el texto quepa en ese alto. Si el texto cambia —como el del
    guion, que cambia en cada frase— el panel entero se ensancha y se encoge, y
    las demás etiquetas vuelven a fluir con otro número de líneas. Con la
    política horizontal en `Ignored`, el diseño reparte el ancho y la etiqueta
    se adapta, nunca al revés.
    """
    etiqueta.setSizePolicy(QSizePolicy.Ignored, etiqueta.sizePolicy().verticalPolicy())
    etiqueta.setMinimumWidth(0)
    return etiqueta



class RecorderTab(QWidget):
    """Graba los dos clips sin salir de la aplicación."""

    def __init__(self, on_clip_recorded) -> None:  # noqa: ANN001
        super().__init__()
        self._on_clip_recorded = on_clip_recorded
        self._cameras: list[CameraInfo] = []
        self._recorder: ClipRecorder | None = None
        self._plan: RecordingPlan = SPEAKING_PLAN
        self._build()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(40)

    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(4, 10, 10, 10)
        raiz.setSpacing(10)

        # --- cámara ---------------------------------------------------- #
        fuente = Card("cámara de grabación")
        fila = QHBoxLayout()
        self.camera_combo = QComboBox()
        fila.addWidget(self.camera_combo, 1)
        buscar = QPushButton("Buscar")
        buscar.setToolTip(
            "Abrir cada cámara para ver si responde tarda unos segundos: "
            "DirectShow no ofrece una lista."
        )
        buscar.clicked.connect(self._scan_cameras)
        fila.addWidget(buscar)
        fuente.add_layout(fila)

        nota = QLabel(
            "La cámara virtual de la propia aplicación se excluye de la lista: "
            "grabar desde ella crearía un bucle."
        )
        nota.setWordWrap(True)
        _no_impone_ancho(nota)
        nota.setObjectName("dim")
        fuente.add(nota)
        raiz.addWidget(fuente)

        # --- vista previa ---------------------------------------------- #
        self.preview = QLabel("Pulsa «Buscar» para elegir la cámara.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setMinimumWidth(120)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet(
            "background:#0c0e11; color:#6e7681;"
            "border:1px solid #2d343d; border-radius:8px;"
        )
        raiz.addWidget(self.preview, 1)

        # --- señal del guion ------------------------------------------- #
        #
        # Altura FIJA en los tres elementos. El texto del guion cambia de
        # longitud en cada frase, y si el bloque pudiera crecer empujaría los
        # botones fuera del panel cada vez que saliera una frase larga. Con
        # altura fija la maquetación no se mueve en toda la grabación.
        guion = QWidget()
        guion.setFixedHeight(168)
        caja = QVBoxLayout(guion)
        caja.setContentsMargins(0, 0, 0, 0)
        caja.setSpacing(6)

        self.cue_title = _no_impone_ancho(QLabel("—"))
        self.cue_title.setAlignment(Qt.AlignCenter)
        self.cue_title.setFixedHeight(40)
        self._style_cue(False)
        caja.addWidget(self.cue_title)

        self.cue_detail = _no_impone_ancho(QLabel())
        self.cue_detail.setAlignment(Qt.AlignCenter)
        self.cue_detail.setWordWrap(True)
        self.cue_detail.setFixedHeight(76)
        self.cue_detail.setStyleSheet(
            "font-size:17px; color:#e6edf3; padding:4px 10px;"
        )
        caja.addWidget(self.cue_detail)

        self.cue_next = _no_impone_ancho(QLabel())
        self.cue_next.setAlignment(Qt.AlignCenter)
        self.cue_next.setWordWrap(True)
        self.cue_next.setFixedHeight(38)
        self.cue_next.setStyleSheet("font-size:12px; color:#6e7681;")
        caja.addWidget(self.cue_next)

        raiz.addWidget(guion)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("")
        raiz.addWidget(self.progress)

        # --- qué grabar ------------------------------------------------ #
        ajustes = Card("qué grabar")
        self.kind_combo = combo(
            [("Clip HABLANDO", "speaking"), ("Clip EN SILENCIO", "silent")]
        )
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        ajustes.add(labelled_row("Clip", self.kind_combo))

        self.duration = PercentSlider(
            "Duración", minimo=8, maximo=25, defecto=15, sufijo=" s",
            ayuda="Menos de 8 s y el bucle se nota; más de 20 no aporta "
            "variedad y la caché crece 7 MB por segundo.",
        )
        self.duration.bind(lambda _v: self._on_kind_changed())
        ajustes.add(self.duration)

        self.countdown = PercentSlider(
            "Cuenta atrás", minimo=0, maximo=15, defecto=3, sufijo=" s",
            ayuda="Tiempo para colocarte antes de que empiece a grabar. "
            "A 0 empieza en cuanto pulsas, pero siempre se observa al menos un "
            "segundo para medir la cadencia real de la cámara.",
        )
        ajustes.add(self.countdown)

        self.checklist = _no_impone_ancho(QLabel())
        self.checklist.setWordWrap(True)
        self.checklist.setStyleSheet("color:#8b949e; font-size:11px;")
        ajustes.add(self.checklist)
        raiz.addWidget(ajustes)

        # --- botones --------------------------------------------------- #
        botones = QHBoxLayout()
        self.record_button = QPushButton("●   Grabar")
        self.record_button.setObjectName("primary")
        self.record_button.setMinimumHeight(40)
        self.record_button.clicked.connect(self._on_record)
        self.record_button.setEnabled(False)
        botones.addWidget(self.record_button, 2)

        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setMinimumHeight(40)
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.setEnabled(False)
        botones.addWidget(self.cancel_button, 1)
        raiz.addLayout(botones)

        ayuda = QLabel(
            "El guion completo, con el porqué de cada tiempo, está en "
            "<code>docs/GUION-GRABACION.md</code>. El audio no se graba: la "
            "aplicación nunca lo usa."
        )
        ayuda.setWordWrap(True)
        _no_impone_ancho(ayuda)
        ayuda.setObjectName("dim")
        raiz.addWidget(ayuda)

        self._on_kind_changed()

    # ------------------------------------------------------------------ #

    def _style_cue(self, critico: bool) -> None:
        fondo, color = ("#3a2a12", "#f0b429") if critico else ("#1b1f25", "#e6edf3")
        self.cue_title.setStyleSheet(
            f"background:{fondo}; color:{color}; border-radius:8px;"
            "font-size:16px; font-weight:600; padding:8px;"
        )

    def _current_plan(self) -> RecordingPlan:
        base = SPEAKING_PLAN if self.kind_combo.currentData() == "speaking" else SILENT_PLAN
        return base.with_duration(self.duration.value())

    def _on_kind_changed(self) -> None:
        self._plan = self._current_plan()
        self.checklist.setText(
            "Recuerda:  " + "   ·   ".join(self._plan.checklist)
        )
        if self._recorder is None or not self._recorder.running:
            primera = self._plan.cues()[0]
            self.cue_title.setText(primera.title)
            self.cue_detail.setText(primera.detail)
            self.cue_next.setText("")
            self._style_cue(True)
            frases = self._plan.reading_phrases()
            if frases:
                self.cue_next.setText(f"el guion saldrá en {len(frases)} frases")

    # ------------------------------------------------------------------ #

    def _scan_cameras(self) -> None:
        self.camera_combo.clear()
        self.preview.setText("Buscando cámaras…")
        self.preview.repaint()
        try:
            self._cameras = list_cameras()
        except Exception as exc:
            QMessageBox.warning(self, "Cámaras", f"No se pudo enumerar: {exc}")
            self._cameras = []

        for cam in self._cameras:
            self.camera_combo.addItem(cam.label, cam.index)
        tiene = bool(self._cameras)
        self.record_button.setEnabled(tiene)
        self.preview.setText(
            "Listo para grabar." if tiene
            else "No se encontró ninguna cámara física disponible."
        )

    def _selected_camera(self) -> CameraInfo | None:
        indice = self.camera_combo.currentData()
        return next((c for c in self._cameras if c.index == indice), None)

    # ------------------------------------------------------------------ #

    def _on_record(self) -> None:
        camara = self._selected_camera()
        if camara is None:
            QMessageBox.warning(self, "Grabar", "Elige una cámara primero.")
            return

        self._plan = self._current_plan()
        sugerido = "hablando.mp4" if self._plan.kind == "speaking" else "silencio.mp4"
        ruta, _ = QFileDialog.getSaveFileName(
            self, f"Guardar {self._plan.name}", sugerido, "Video MP4 (*.mp4)"
        )
        if not ruta:
            return

        self._recorder = ClipRecorder(
            camara, self._plan, Path(ruta), countdown_s=self.countdown.value()
        )
        self._recorder.start()
        self.record_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.kind_combo.setEnabled(False)
        self.duration.setEnabled(False)

    def _on_cancel(self) -> None:
        if self._recorder is not None:
            self._recorder.cancel()
        self._finish()

    def _finish(self) -> None:
        self.record_button.setEnabled(bool(self._cameras))
        self.cancel_button.setEnabled(False)
        self.kind_combo.setEnabled(True)
        self.duration.setEnabled(True)
        self.progress.setValue(0)

    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        if self._recorder is None:
            return
        estado = self._recorder.status()

        frame = self._recorder.latest_frame()
        if frame is not None:
            self._show_frame(frame, guias=estado.phase != "recording")

        if estado.phase == "countdown":
            restante = max(1, int(estado.countdown_s + 0.999))
            self.cue_title.setText(f"{restante}…   {estado.cue_title}")
            self.cue_detail.setText(estado.cue_detail)
            self.cue_next.setText("")
            self._style_cue(True)
            self.progress.setValue(0)
            self.progress.setFormat("preparado…")

        elif estado.phase == "recording":
            self.cue_title.setText(estado.cue_title)
            self.cue_detail.setText(estado.cue_detail)
            self._style_cue(estado.cue_critical)

            # La frase siguiente, atenuada: permite anticiparla sin tener que
            # leerla a toda prisa cuando aparece.
            siguiente = self._plan.next_cue_at(estado.elapsed_s)
            if siguiente is not None and siguiente.detail:
                self.cue_next.setText(f"luego:  {siguiente.detail}")
            else:
                self.cue_next.setText("")

            fraccion = estado.elapsed_s / max(0.1, self._plan.duration_s)
            self.progress.setValue(int(min(1.0, fraccion) * 1000))
            self.progress.setFormat(f"{estado.remaining_s:.1f} s restantes")

        elif estado.phase == "done":
            self.cue_next.setText("")
            self.cue_title.setText("Grabación terminada")
            self.cue_detail.setText(
                f"{estado.frames} fotogramas en {estado.elapsed_s:.1f} s "
                f"({estado.measured_fps:.0f} FPS reales)"
            )
            self._style_cue(False)
            self.progress.setValue(1000)
            self.progress.setFormat("completado")
            ruta, kind = estado.output_path, self._plan.kind
            self._recorder = None
            self._finish()
            self._offer_use(Path(ruta), kind, estado.measured_fps)

        elif estado.phase == "error":
            self.cue_next.setText("")
            self.cue_title.setText("Error")
            self.cue_detail.setText(estado.error)
            self._style_cue(True)
            self._recorder = None
            self._finish()
            QMessageBox.critical(self, "Error al grabar", estado.error)

    def _show_frame(self, frame: np.ndarray, *, guias: bool) -> None:
        vista = draw_framing_guides(frame) if guias else frame
        alto, ancho = vista.shape[:2]
        imagen = QImage(
            np.ascontiguousarray(vista).data, ancho, alto, 3 * ancho,
            QImage.Format_BGR888,
        )
        pixmap = QPixmap.fromImage(imagen.copy())
        self.preview.setPixmap(
            pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _offer_use(self, ruta: Path, kind: str, fps_real: float) -> None:
        aviso = ""
        if fps_real and fps_real < 20:
            aviso = (
                f"\n\nLa cámara entregó solo {fps_real:.0f} FPS reales. Suele "
                "pasar con poca luz: la webcam baja la cadencia para exponer "
                "más. Con más luz el clip quedará más fluido."
            )
        etiqueta = "hablando" if kind == "speaking" else "en silencio"
        respuesta = QMessageBox.question(
            self,
            "Grabación terminada",
            f"Guardado en:\n{ruta}\n\n¿Usarlo como clip {etiqueta}?{aviso}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if respuesta == QMessageBox.Yes:
            self._on_clip_recorded(kind, str(ruta))

    # ------------------------------------------------------------------ #

    def shutdown(self) -> None:
        self.timer.stop()
        if self._recorder is not None:
            self._recorder.cancel()
            self._recorder = None
