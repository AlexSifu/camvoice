"""Pestaña «Biblioteca»: detectar, validar, importar y preparar las 62 grabaciones.

Flujo pensado para no tener que abrir una consola:

1. La carpeta de origen viene rellenada (Imágenes\\Camera Roll); «Seleccionar
   carpeta» la cambia y «Detectar 62 grabaciones» lista lo que hay. El
   contador muestra «N/62 importadas» y recuerda los 4 números que se
   omitieron a propósito al grabar (50, 52, 59 y 63), que no son un error.
2. «Validar y preparar vídeos» hace, en un hilo aparte y con barra de
   progreso (fase y número de clip), tiempo transcurrido y «Cancelar»:
   validación (reanudable: lo ya validado no se repite) → importación al
   manifiesto (idempotente; los originales nunca se tocan) → cachés e índices
   del modo director. Al terminar deja un informe.
3. Vista previa: reproduce la escena seleccionada (decodificada en otro hilo
   y reducida) y ensaya transiciones (hablar→callar, callar→hablar,
   gesto→silencio, gesto→habla) con el mismo director del render y un reloj
   simulado, mostrando el par origen/destino, la espera, si hubo fundido de
   reserva y el coste de discontinuidad.

La interfaz no se bloquea: todo lo lento corre en QThread y solo vuelve al
hilo de la interfaz por señales. Las opciones poco habituales (copiar en vez
de referenciar, conservar los clips principales, revalidar todo) están en un
panel «Opciones avanzadas» plegado.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..scenes import CATEGORY_LABELS, EXPECTED_COUNT, SCENES, SKIPPED, Scene
from ..video.importer import (
    STATUS_ERROR,
    STATUS_LABELS,
    STATUS_READY,
    STATUS_REVIEW,
    ScanResult,
    default_source_folder,
    import_library,
    rollback_library,
    scan_folder,
    validate_scan,
)
from ..video.library import load_library, manifest_path
from ..video.trials import SCENARIOS, run_trial
from .widgets import Card

__all__ = ["LibraryTab", "SceneRow"]

NOT_SCANNED = "Sin detectar"
NOT_VALIDATED = "Sin validar"
MISSING = "Falta"
_ROW_COLORS = {
    STATUS_LABELS[STATUS_READY]: "#56d364",
    STATUS_LABELS[STATUS_REVIEW]: "#e3b341",
    STATUS_LABELS[STATUS_ERROR]: "#ff7b72",
    MISSING: "#ff7b72",
}
PREVIEW_WIDTH = 480


@dataclass
class SceneRow:
    scene: Scene
    path: str = ""
    status: str = NOT_SCANNED
    duration_s: float = 0.0
    thumbnail: str = ""
    issues: tuple[str, ...] = ()
    imported: bool = False


# --------------------------------------------------------------------------- #
# Hilos
# --------------------------------------------------------------------------- #


class Cancelled(Exception):
    pass


class _Job(QObject):
    """Ejecuta `fn(progress, cancel)` en un QThread."""

    progress = Signal(int, int, str)
    finished = Signal(object, str)

    def __init__(self, fn: Callable) -> None:  # noqa: ANN001
        super().__init__()
        self._fn = fn
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        try:
            result = self._fn(lambda d, t, m: self.progress.emit(int(d), int(t), str(m)), self.cancelled)
        except Exception as exc:  # noqa: BLE001 - se informa en la interfaz
            name = type(exc).__name__
            if self._cancel or "Cancel" in name:
                self.finished.emit(None, f"Cancelado: {exc}")
            else:
                self.finished.emit(None, f"{exc}")
            return
        self.finished.emit(result, "")


class _Player(QObject):
    """Decodifica un video reducido y emite fotogramas al ritmo del archivo."""

    frame = Signal(QImage)
    finished = Signal(str)

    def __init__(self, path: str, width: int = PREVIEW_WIDTH, max_s: float = 60.0) -> None:
        super().__init__()
        self._path = path
        self._width = width
        self._max_s = max_s
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            self.finished.emit(f"No se puede abrir {Path(self._path).name}.")
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        period = 1.0 / max(1.0, min(60.0, fps))
        t0 = time.perf_counter()
        n = 0
        try:
            while not self._stop:
                ok, bgr = cap.read()
                if not ok or n * period > self._max_s:
                    break
                self.frame.emit(bgr_to_qimage(bgr, self._width))
                n += 1
                delay = t0 + n * period - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
        finally:
            cap.release()
        self.finished.emit("")


def bgr_to_qimage(bgr: np.ndarray, width: int = PREVIEW_WIDTH) -> QImage:
    h, w = bgr.shape[:2]
    if w > width:
        bgr = cv2.resize(bgr, (width, max(1, int(round(h * width / w)))), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    hh, ww = rgb.shape[:2]
    return QImage(rgb.data, ww, hh, 3 * ww, QImage.Format_RGB888).copy()


def _fmt_elapsed(s: float) -> str:
    s = int(s)
    return f"{s // 60:02d}:{s % 60:02d}"


# --------------------------------------------------------------------------- #
# Pestaña
# --------------------------------------------------------------------------- #


class LibraryTab(QWidget):
    def __init__(
        self,
        cfg: AppConfig,
        *,
        on_imported: Callable[[], None] | None = None,
        is_running: Callable[[], bool] | None = None,
        pick_folder: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self._on_imported = on_imported or (lambda: None)
        self._is_running = is_running or (lambda: False)
        self._pick_folder = pick_folder or self._dialog_folder
        self.rows: dict[int, SceneRow] = {s.number: SceneRow(s) for s in SCENES}
        self.scan: ScanResult | None = None
        self.prepared = None  # PreparedClips de la última preparación
        self._prep_session = None
        self._thread: QThread | None = None
        self._job: _Job | None = None
        self._job_kind = ""
        self._player_thread: QThread | None = None
        self._player: _Player | None = None
        self._started_at = 0.0
        self._trial_frames: list[np.ndarray] = []
        self._trial_pos = 0
        self.last_trial = None

        self._elapsed = QTimer(self)
        self._elapsed.setInterval(500)
        self._elapsed.timeout.connect(self._tick_elapsed)
        self._trial_timer = QTimer(self)
        self._trial_timer.timeout.connect(self._show_trial_frame)

        self._build()
        self._load_from_manifest()

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 10, 10, 10)
        layout.setSpacing(10)

        origen = Card("grabaciones")
        fila = QHBoxLayout()
        self.folder_edit = QLineEdit(str(default_source_folder()))
        self.folder_edit.setToolTip("Carpeta con los archivos 1.mp4 … 66.mp4. Nunca se modifican.")
        fila.addWidget(self.folder_edit, 1)
        elegir = QPushButton("Seleccionar carpeta")
        elegir.clicked.connect(self._on_pick_folder)
        fila.addWidget(elegir)
        origen.add_layout(fila)

        fila = QHBoxLayout()
        self.detect_button = QPushButton(f"Detectar {EXPECTED_COUNT} grabaciones")
        self.detect_button.clicked.connect(self.detect)
        fila.addWidget(self.detect_button)
        self.prepare_button = QPushButton("Validar y preparar vídeos")
        self.prepare_button.setObjectName("primary")
        self.prepare_button.setMinimumHeight(36)
        self.prepare_button.setToolTip(
            "Valida cada video, lo importa a la biblioteca y prepara cachés e índices. "
            "Se puede cancelar y reanudar: lo ya hecho no se repite."
        )
        self.prepare_button.clicked.connect(self.start_prepare)
        fila.addWidget(self.prepare_button, 1)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        fila.addWidget(self.cancel_button)
        origen.add_layout(fila)

        self.counter = QLabel()
        self.counter.setStyleSheet("font-size:15px; font-weight:600;")
        origen.add(self.counter)
        self.scan_info = QLabel()
        self.scan_info.setObjectName("dim")
        self.scan_info.setWordWrap(True)
        origen.add(self.scan_info)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        origen.add(self.progress)
        fila = QHBoxLayout()
        self.phase_label = QLabel()
        self.phase_label.setObjectName("dim")
        fila.addWidget(self.phase_label, 1)
        self.elapsed_label = QLabel()
        self.elapsed_label.setObjectName("dim")
        fila.addWidget(self.elapsed_label)
        origen.add_layout(fila)

        self.advanced = QGroupBox("Opciones avanzadas")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        adv = QVBoxLayout(self.advanced)
        self.copy_check = QCheckBox("Copiar los videos a la biblioteca (por defecto se referencian)")
        self.keep_main_check = QCheckBox("Conservar los clips principales actuales (no usar los n.º 1 y 2)")
        self.force_check = QCheckBox("Revalidar todo (ignorar lo ya validado)")
        self.caches_check = QCheckBox("Preparar cachés e índices al terminar")
        self.caches_check.setChecked(True)
        for w in (self.copy_check, self.keep_main_check, self.force_check, self.caches_check):
            adv.addWidget(w)
        self.rollback_button = QPushButton("Deshacer la última importación")
        self.rollback_button.clicked.connect(self.rollback)
        adv.addWidget(self.rollback_button)
        self.advanced.toggled.connect(self._toggle_advanced)
        self._toggle_advanced(False)
        origen.add(self.advanced)
        layout.addWidget(origen)

        tabla = Card("escenas")
        fila = QHBoxLayout()
        self.category_filter = QComboBox()
        self.category_filter.addItem("Todas las categorías", "")
        for key, label in CATEGORY_LABELS.items():
            self.category_filter.addItem(label, key)
        self.category_filter.currentIndexChanged.connect(self.apply_filters)
        fila.addWidget(self.category_filter)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Todos los estados", "")
        for label in (*STATUS_LABELS.values(), MISSING, NOT_VALIDATED, NOT_SCANNED):
            self.status_filter.addItem(label, label)
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        fila.addWidget(self.status_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar número o acción…")
        self.search.textChanged.connect(self.apply_filters)
        fila.addWidget(self.search, 1)
        tabla.add_layout(fila)

        self.table = QTableWidget(len(SCENES), 6)
        self.table.setHorizontalHeaderLabels(["Nº", "Acción", "Tipo", "Duración", "Miniatura", "Estado"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumHeight(300)
        cab = self.table.horizontalHeader()
        cab.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (0, 2, 3, 4, 5):
            cab.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_selection)
        tabla.add(self.table)
        self.row_detail = QLabel()
        self.row_detail.setObjectName("dim")
        self.row_detail.setWordWrap(True)
        tabla.add(self.row_detail)
        layout.addWidget(tabla)

        vista = Card("vista previa y prueba de transiciones")
        self.preview = QLabel("Selecciona una escena y pulsa «Reproducir escena».")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(320, 200)
        self.preview.setStyleSheet(
            "background:#0c0e11; color:#6e7681; border:1px solid #2d343d; border-radius:8px;"
        )
        vista.add(self.preview)
        fila = QHBoxLayout()
        self.play_button = QPushButton("▶ Reproducir escena")
        self.play_button.clicked.connect(self.play_selected)
        fila.addWidget(self.play_button)
        self.stop_play_button = QPushButton("■ Parar")
        self.stop_play_button.clicked.connect(self.stop_playback)
        fila.addWidget(self.stop_play_button)
        fila.addStretch(1)
        vista.add_layout(fila)

        fila = QHBoxLayout()
        fila.addWidget(QLabel("Gesto:"))
        self.trial_gesture = QComboBox()
        self.trial_gesture.setMinimumWidth(180)
        fila.addWidget(self.trial_gesture, 1)
        vista.add_layout(fila)
        fila = QHBoxLayout()
        self.trial_buttons: dict[str, QPushButton] = {}
        for key, label in SCENARIOS.items():
            b = QPushButton(label)
            b.setToolTip("Ensaya este cambio con el director real y un reloj simulado.")
            b.clicked.connect(lambda _=False, k=key: self.run_trial(k))
            self.trial_buttons[key] = b
            fila.addWidget(b)
        vista.add_layout(fila)
        self.trial_label = QLabel(
            "Las pruebas de transición usan los clips preparados: primero «Validar y "
            "preparar vídeos»."
        )
        self.trial_label.setWordWrap(True)
        self.trial_label.setObjectName("dim")
        vista.add(self.trial_label)
        layout.addWidget(vista)

        informe = Card("informe")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumHeight(140)
        self.report.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        informe.add(self.report)
        layout.addWidget(informe)
        layout.addStretch(1)
        self._refresh_table()
        self._update_buttons()

    def _toggle_advanced(self, on: bool) -> None:
        for w in (self.copy_check, self.keep_main_check, self.force_check,
                  self.caches_check, self.rollback_button):
            w.setVisible(bool(on))

    # ------------------------------------------------------------------ #
    # Datos
    # ------------------------------------------------------------------ #

    def _load_from_manifest(self) -> None:
        lib = load_library(self.cfg, check_files=False).library
        if lib.source_folder:
            self.folder_edit.setText(lib.source_folder)
        for number, e in lib.scenes.items():
            row = self.rows.get(number)
            if row is None:
                continue
            row.path = e.source
            row.status = STATUS_LABELS.get(e.status, e.status)
            row.duration_s = e.duration_s
            row.thumbnail = e.thumbnail
            row.issues = tuple(e.issues)
            row.imported = bool(e.working) and e.status != STATUS_ERROR
        self._refresh_table()

    @property
    def imported_count(self) -> int:
        return sum(1 for r in self.rows.values() if r.imported)

    @property
    def has_library(self) -> bool:
        return self.imported_count > 0

    def counter_text(self) -> str:
        omitted = ", ".join(str(n) for n in sorted(SKIPPED))
        return f"{self.imported_count}/{EXPECTED_COUNT} importadas · {len(SKIPPED)} omitidas a propósito ({omitted})"

    def detect(self) -> ScanResult | None:
        folder = Path(self.folder_edit.text().strip()).expanduser()
        try:
            scan = scan_folder(folder)
        except FileNotFoundError as exc:
            self.scan_info.setText(str(exc))
            self.scan_info.setStyleSheet("color:#ff7b72;")
            return None
        self.scan = scan
        self.scan_info.setStyleSheet("")
        self.scan_info.setText(
            f"{scan.summary()} · {len(scan.ignored)} archivos ignorados (sin número o no son video)"
        )
        for number, row in self.rows.items():
            entry = scan.entries.get(number)
            if entry is None:
                row.path, row.status, row.imported = "", MISSING, False
                row.issues = ("No está en la carpeta.",)
                continue
            if entry.ambiguous:
                row.path = str(entry.paths[0])
                row.status = STATUS_LABELS[STATUS_ERROR]
                row.issues = ("Número repetido: " + ", ".join(p.name for p in entry.paths),)
                row.imported = False
                continue
            path = str(entry.paths[0])
            if row.path and Path(row.path) != Path(path):
                row.imported = False
                row.status = NOT_VALIDATED
            elif not row.imported and row.status in (NOT_SCANNED, MISSING):
                row.status = NOT_VALIDATED
            row.path = path
        self._refresh_table()
        self.report.appendPlainText(f"Detección: {scan.summary()}")
        return scan

    def _refresh_table(self) -> None:
        self.counter.setText(self.counter_text())
        for r, scene in enumerate(SCENES):
            row = self.rows[scene.number]
            items = [
                QTableWidgetItem(str(scene.number)),
                QTableWidgetItem(scene.label),
                QTableWidgetItem(CATEGORY_LABELS.get(scene.category, scene.category)),
                QTableWidgetItem(f"{row.duration_s:.1f} s" if row.duration_s else "—"),
            ]
            items[0].setData(Qt.UserRole, scene.number)
            items[1].setToolTip(scene.description)
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)
            thumb = self.table.cellWidget(r, 4)
            if not isinstance(thumb, QLabel):
                thumb = QLabel()
                thumb.setAlignment(Qt.AlignCenter)
                self.table.setCellWidget(r, 4, thumb)
            if thumb.property("src") != row.thumbnail:
                pix = QPixmap(row.thumbnail) if row.thumbnail and Path(row.thumbnail).is_file() else QPixmap()
                if pix.isNull():
                    thumb.setPixmap(QPixmap())
                    thumb.setText("—")
                else:
                    thumb.setPixmap(pix.scaled(80, 45, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                thumb.setProperty("src", row.thumbnail)
            status = QTableWidgetItem(row.status)
            color = _ROW_COLORS.get(row.status)
            if color:
                status.setForeground(_qcolor(color))
            if row.issues:
                status.setToolTip("\n".join(row.issues))
            self.table.setItem(r, 5, status)
            self.table.setRowHeight(r, 50 if row.thumbnail else 28)
        self.apply_filters()

    def apply_filters(self) -> None:
        cat = self.category_filter.currentData() or ""
        st = self.status_filter.currentData() or ""
        q = self.search.text().strip().casefold()
        for r, scene in enumerate(SCENES):
            row = self.rows[scene.number]
            ok = (not cat or scene.category == cat) and (not st or row.status == st)
            if ok and q:
                ok = q in str(scene.number) or q in scene.label.casefold() or q in scene.description.casefold()
            self.table.setRowHidden(r, not ok)

    def visible_numbers(self) -> list[int]:
        return [s.number for r, s in enumerate(SCENES) if not self.table.isRowHidden(r)]

    def selected_row(self) -> SceneRow | None:
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not sel:
            return None
        return self.rows[SCENES[sel[0].row()].number]

    def _on_selection(self) -> None:
        row = self.selected_row()
        if row is None:
            self.row_detail.setText("")
            return
        s = row.scene
        text = f"<b>{s.number}. {s.label}</b> — {s.description}"
        if row.path:
            text += f"<br>{row.path}"
        if row.issues:
            text += "<br>" + "<br>".join(row.issues)
        self.row_detail.setText(text)

    # ------------------------------------------------------------------ #
    # Validar, importar, preparar
    # ------------------------------------------------------------------ #

    def busy(self) -> bool:
        return self._thread is not None

    def _update_buttons(self) -> None:
        busy = self.busy()
        running = self._is_running()
        self.prepare_button.setEnabled(not busy and not running)
        self.detect_button.setEnabled(not busy)
        self.rollback_button.setEnabled(not busy and not running)
        self.cancel_button.setEnabled(busy and self._job_kind != "trial")
        ready = self.prepared is not None and getattr(self.prepared, "mode", "") == "director"
        for key, b in self.trial_buttons.items():
            needs_gesture = key.startswith("gesto")
            b.setEnabled(ready and not busy and (not needs_gesture or self.trial_gesture.count() > 0))

    def options(self) -> dict:
        """Opciones leídas en el hilo de la interfaz (el trabajo no toca widgets)."""
        return {
            "folder": Path(self.folder_edit.text().strip()).expanduser(),
            "copy": self.copy_check.isChecked(),
            "keep_main": self.keep_main_check.isChecked(),
            "force": self.force_check.isChecked(),
            "prepare": self.caches_check.isChecked(),
        }

    def pipeline(self, progress, cancel, opts: dict | None = None):  # noqa: ANN001, ANN201
        """Validación → importación → (opcional) cachés. Corre en el hilo de trabajo.

        Se puede llamar directamente (sin hilo) desde las pruebas."""
        opts = opts or self.options()
        report: list[str] = []
        prepare = opts["prepare"]
        phases = 3 if prepare else 2
        scan = scan_folder(opts["folder"])
        lib_dir = manifest_path(self.cfg).parent

        def phase(k: int, name: str):  # noqa: ANN202
            return lambda d, t, m: progress(d, t, f"Fase {k}/{phases} · {name} · {m}")

        report.append(scan.summary())
        probes = validate_scan(scan, lib_dir, progress=phase(1, "Validación"), cancel=cancel,
                               force=opts["force"])
        counts = {s: 0 for s in STATUS_LABELS}
        for number, info in sorted(probes.items()):
            counts[info.status] = counts.get(info.status, 0) + 1
            if info.status != STATUS_READY or info.issues:
                estado = STATUS_LABELS[info.status] + (" (aviso)" if info.status == STATUS_READY else "")
                report.append(f"  n.º {number}: {estado} — " + "; ".join(info.issues))
        report.append("Validación: " + ", ".join(f"{STATUS_LABELS[s]} {n}" for s, n in counts.items()))
        if cancel():
            raise Cancelled("tras la validación")
        lib, warnings = import_library(
            self.cfg, scan, probes, copy=opts["copy"],
            apply_main_clips=not opts["keep_main"],
            progress=phase(2, "Importación"), cancel=cancel,
        )
        imported = sum(1 for e in lib.scenes.values() if e.status != STATUS_ERROR)
        report.append(f"Importación: {imported}/{EXPECTED_COUNT} · manifiesto {manifest_path(self.cfg)}")
        report += [f"  aviso: {w}" for w in warnings]
        result = {"scan": scan, "probes": probes, "library": lib, "report": report,
                  "clips": None, "session": None}
        if prepare:
            from ..controller import Session

            session = Session(self.cfg)
            try:
                clips = session.prepare(progress=phase(3, "Cachés e índices"), cancel=cancel)
            except Exception:
                session.close()
                raise
            result["clips"], result["session"] = clips, session
            report.append(
                f"Preparación: modo {clips.mode} · {len(clips.silent_pool)} silencio, "
                f"{len(clips.speaking_pool)} habla, {len(clips.gestures)} gestos/frases · "
                f"caché {clips.total_mb:.0f} MB"
            )
            report += [f"  aviso: {w}" for w in clips.warnings]
        return result

    def start_prepare(self) -> None:
        if self.busy() or self._is_running():
            return
        self.report.appendPlainText("— Validar y preparar —")
        opts = self.options()
        self._start_job("prepare", lambda progress, cancel: self.pipeline(progress, cancel, opts))

    def _start_job(self, kind: str, fn) -> None:  # noqa: ANN001
        self._job_kind = kind
        self._thread = QThread(self)
        self._job = _Job(fn)
        self._job.moveToThread(self._thread)
        self._thread.started.connect(self._job.run)
        self._job.progress.connect(self._on_progress)
        self._job.finished.connect(self._on_finished)
        self._started_at = time.perf_counter()
        if kind != "trial":
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)
            self._elapsed.start()
            self._tick_elapsed()
        self._thread.start()
        self._update_buttons()

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.phase_label.setText("Cancelando… (lo ya hecho se conserva)")

    def _on_progress(self, done: int, total: int, text: str) -> None:
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.phase_label.setText(text)

    def _tick_elapsed(self) -> None:
        self.elapsed_label.setText(f"Tiempo: {_fmt_elapsed(time.perf_counter() - self._started_at)}")

    def _finish_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._job = None
        self._elapsed.stop()
        self.progress.setVisible(False)

    def _on_finished(self, result, error: str) -> None:  # noqa: ANN001
        kind = self._job_kind
        elapsed = time.perf_counter() - self._started_at
        self._finish_thread()
        self._job_kind = ""
        if kind == "trial":
            self._on_trial_done(result, error)
        elif error:
            self.phase_label.setText(error)
            self.report.appendPlainText(f"{error}   ({elapsed:.1f} s)")
        else:
            self._apply_result(result, elapsed)
        self._update_buttons()

    def _apply_result(self, result: dict, elapsed: float) -> None:
        if self._prep_session is not None:
            self._prep_session.close()
        self._prep_session = result.get("session")
        self.prepared = result.get("clips")
        self.scan = result["scan"]
        probes = result["probes"]
        lib = result["library"]
        for number, row in self.rows.items():
            e = lib.scenes.get(number)
            info = probes.get(number)
            if e is not None:
                row.path = e.source
                row.status = STATUS_LABELS.get(e.status, e.status)
                row.duration_s = e.duration_s
                row.thumbnail = e.thumbnail
                row.issues = tuple(e.issues)
                row.imported = bool(e.working) and e.status != STATUS_ERROR
            elif info is not None:
                row.status = STATUS_LABELS.get(info.status, info.status)
                row.issues = tuple(info.issues)
            else:
                row.status, row.imported = MISSING, False
        self._refresh_table()
        self._fill_trial_gestures()
        for line in result["report"]:
            self.report.appendPlainText(line)
        self.report.appendPlainText(f"Terminado en {elapsed:.1f} s.")
        self.phase_label.setText(f"Terminado en {_fmt_elapsed(elapsed)} · {self.counter_text()}")
        self._on_imported()

    def rollback(self) -> None:
        if self.busy() or self._is_running():
            return
        msg = rollback_library(self.cfg)
        self.report.appendPlainText(f"Deshacer: {msg}")
        for row in self.rows.values():
            row.imported, row.status, row.thumbnail, row.duration_s = False, NOT_SCANNED, "", 0.0
            row.issues = ()
        self.prepared = None
        if self._prep_session is not None:
            self._prep_session.close()
            self._prep_session = None
        self._load_from_manifest()
        self._fill_trial_gestures()
        self._update_buttons()
        self._on_imported()

    # ------------------------------------------------------------------ #
    # Vista previa
    # ------------------------------------------------------------------ #

    def play_selected(self) -> None:
        row = self.selected_row()
        if row is None or not row.path or not Path(row.path).is_file():
            self.preview.setText("Selecciona una escena detectada.")
            return
        self.play_path(row.path)

    def play_path(self, path: str) -> None:
        self.stop_playback()
        self._trial_timer.stop()
        self._player_thread = QThread(self)
        self._player = _Player(path)
        self._player.moveToThread(self._player_thread)
        self._player_thread.started.connect(self._player.run)
        self._player.frame.connect(self._on_play_frame)
        self._player.finished.connect(self._on_play_finished)
        self._player_thread.start()

    def _on_play_frame(self, img: QImage) -> None:
        self._show_image(img)

    def _on_play_finished(self, error: str) -> None:
        if error:
            self.preview.setText(error)
        self._stop_player_thread()

    def _stop_player_thread(self) -> None:
        if self._player is not None:
            self._player.stop()
        if self._player_thread is not None:
            self._player_thread.quit()
            self._player_thread.wait(3000)
        self._player_thread = None
        self._player = None

    def stop_playback(self) -> None:
        self._stop_player_thread()
        self._trial_timer.stop()

    def _show_image(self, img: QImage) -> None:
        pix = QPixmap.fromImage(img)
        self.preview.setPixmap(
            pix.scaled(self.preview.width(), self.preview.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    # ------------------------------------------------------------------ #
    # Pruebas de transición
    # ------------------------------------------------------------------ #

    def _fill_trial_gestures(self) -> None:
        from ..gestures import gesture_label

        self.trial_gesture.clear()
        clips = self.prepared
        if clips is None:
            return
        actions = set(getattr(clips, "speaking_actions", set()))
        for name in sorted(clips.gestures, key=gesture_label):
            if name in actions or name.endswith("_hablando"):
                continue
            self.trial_gesture.addItem(gesture_label(name), name)

    def run_trial(self, scenario: str) -> None:
        clips = self.prepared
        if self.busy() or clips is None or clips.mode != "director":
            self.trial_label.setText("Primero pulsa «Validar y preparar vídeos».")
            return
        gesture = self.trial_gesture.currentData() or ""
        cfg = self.cfg.video
        seed = int(time.time()) % 10_000

        def fn(progress, cancel):  # noqa: ANN001, ANN202
            return run_trial(
                cfg, clips.silent_pool, clips.speaking_pool, clips.gestures, scenario,
                gesture=gesture, speaking_actions=sorted(clips.speaking_actions),
                seed=seed, cancel=cancel,
            )

        self.trial_label.setText(f"Ensayando «{SCENARIOS[scenario]}»…")
        self._start_job("trial", fn)

    def _on_trial_done(self, rep, error: str) -> None:  # noqa: ANN001
        if error or rep is None:
            self.trial_label.setText(error or "No se pudo ensayar.")
            return
        self.last_trial = rep
        self.trial_label.setText(rep.summary())
        self.report.appendPlainText("Transición: " + rep.summary())
        self.stop_playback()
        self._trial_frames = list(rep.frames)
        self._trial_pos = 0
        if self._trial_frames:
            self._trial_timer.start(max(15, int(1000 / max(1, self.cfg.video.fps))))

    def _show_trial_frame(self) -> None:
        if self._trial_pos >= len(self._trial_frames):
            self._trial_timer.stop()
            return
        self._show_image(bgr_to_qimage(self._trial_frames[self._trial_pos]))
        self._trial_pos += 1

    # ------------------------------------------------------------------ #

    def on_running_changed(self) -> None:
        self._update_buttons()

    def shutdown(self) -> None:
        """Al cerrar la ventana: cancela y espera hilos, libera cachés."""
        if self._job is not None:
            self._job.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10000)
            self._thread = None
        self.stop_playback()
        if self._prep_session is not None:
            self._prep_session.close()
            self._prep_session = None

    def _on_pick_folder(self) -> None:
        ruta = self._pick_folder(self.folder_edit.text())
        if ruta:
            self.folder_edit.setText(ruta)
            self.detect()

    def _dialog_folder(self, inicio: str) -> str:
        return QFileDialog.getExistingDirectory(self, "Carpeta de las grabaciones", inicio)


def _qcolor(hexcolor: str):  # noqa: ANN202
    from PySide6.QtGui import QColor

    return QColor(hexcolor)
