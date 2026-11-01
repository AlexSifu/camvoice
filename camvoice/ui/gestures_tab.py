"""Pestaña «Gestos»: un botón por gesto, su atajo y su clip.

Todo lo que se cambia aquí se guarda en el manifiesto de la biblioteca
(`library/manifest.json`, ver video/library.py), no en config.json: es la
misma fuente que leen la CLI (`camvoice gestures`) y la preparación de clips.

* **Clip**: asignar un clip se guarda al momento. Para que el gesto aparezca
  en la cámara hay que volver a iniciar (cada clip se prepara una vez, con su
  caché e índice).
* **Atajo**: se valida con las mismas reglas que la CLI (nada de AltGr, de
  atajos de Teams ni de la tecla Windows) y se aplica al pulsar «Guardar
  atajos»; si otra aplicación ya usa la combinación, se avisa.
* **Botón**: hace el gesto ya, con el ratón. Útil también para probarlo.
* **Galería de escenas**: una fila por cada grabación de acción importada
  (gestos y frases a petición) con miniatura, descripción, atajo opcional y
  «Probar». Las escenas no traen atajo global por defecto (no se registran 62
  combinaciones): solo las que el usuario asigne, comprobando colisiones con
  los gestos del catálogo, cancelar y pausar. Se marca qué gestos no tienen
  variante «hablando» (si se piden hablando, esperan una micro-pausa).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..gestures import (
    CANCEL,
    DEFAULT_HOTKEYS,
    PAUSE,
    GESTURES,
    HotkeyError,
    gesture_label,
    speaking_variant,
    validate_hotkey,
)
from ..scenes import CATEGORY_LABELS, SPEAKING_ONE_SHOT
from ..video.library import Library, load_library, manifest_path, save_library
from .widgets import Card, PercentSlider

__all__ = ["GesturesTab"]

VIDEO_FILTER = "Videos (*.mp4 *.mov *.avi *.mkv *.webm *.m4v);;Todos los archivos (*.*)"


class GesturesTab(QWidget):
    """Edición de gestos y atajos. No conoce la sesión: usa callbacks."""

    def __init__(
        self,
        cfg: AppConfig,
        *,
        on_gesture: Callable[[str], None],
        on_hotkeys_changed: Callable[[], None],
        on_library_changed: Callable[[], None] | None = None,
        pick_file: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self._on_gesture = on_gesture
        self._on_hotkeys_changed = on_hotkeys_changed
        self._on_library_changed = on_library_changed or (lambda: None)
        self._pick_file = pick_file or self._dialog_pick
        self.library = Library()
        self.issues: list[str] = []
        self.buttons: dict[str, QPushButton] = {}
        self.hotkey_edits: dict[str, QLineEdit] = {}
        self.clip_labels: dict[str, QLabel] = {}
        self.scene_hotkey_edits: dict[str, QLineEdit] = {}
        """Atajo de cada escena de acción importada (no del catálogo)."""
        self.scene_buttons: dict[str, QPushButton] = {}
        self._build()
        self.reload()

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 10, 10, 10)
        layout.setSpacing(10)

        estado = Card("estado")
        self.hotkeys_check = QCheckBox("Atajos de teclado globales activos")
        self.hotkeys_check.setToolTip(
            "Desmárcalo para suspenderlos (por ejemplo, mientras escribes en "
            "otra aplicación que use las mismas teclas). También desde el "
            "icono de la bandeja. Suspender libera las combinaciones."
        )
        self.hotkeys_check.toggled.connect(self._on_hotkeys_toggled)
        estado.add(self.hotkeys_check)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("dim")
        estado.add(self.status_label)
        self.hotkey_status = QLabel()
        self.hotkey_status.setWordWrap(True)
        self.hotkey_status.setStyleSheet("color:#8b949e; font-size:11px;")
        estado.add(self.hotkey_status)
        layout.addWidget(estado)

        tabla = Card("gestos")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for col, titulo in enumerate(("Gesto", "Atajo", "Clip", "")):
            cab = QLabel(f"<b>{titulo}</b>")
            grid.addWidget(cab, 0, col)
        for row, name in enumerate(GESTURES, start=1):
            boton = QPushButton(gesture_label(name))
            boton.setToolTip("Hace el gesto ahora (requiere la cámara en marcha).")
            boton.clicked.connect(lambda _=False, n=name: self._on_gesture(n))
            self.buttons[name] = boton
            grid.addWidget(boton, row, 0)

            edit = QLineEdit()
            edit.setPlaceholderText(DEFAULT_HOTKEYS.get(name, "sin atajo"))
            edit.setMaximumWidth(150)
            self.hotkey_edits[name] = edit
            grid.addWidget(edit, row, 1)

            clip = QLabel()
            clip.setObjectName("dim")
            clip.setWordWrap(True)
            self.clip_labels[name] = clip
            grid.addWidget(clip, row, 2)

            asignar = QPushButton("Clip…")
            asignar.setToolTip("Elegir el video de este gesto (se guarda al momento).")
            asignar.clicked.connect(lambda _=False, n=name: self.assign_clip(n))
            grid.addWidget(asignar, row, 3)

        row = len(GESTURES) + 1
        self.cancel_button = QPushButton(gesture_label(CANCEL))
        self.cancel_button.setToolTip("Interrumpe el gesto en curso y vuelve a la pose neutra.")
        self.cancel_button.clicked.connect(lambda: self._on_gesture(CANCEL))
        grid.addWidget(self.cancel_button, row, 0)
        self.cancel_edit = QLineEdit()
        self.cancel_edit.setMaximumWidth(150)
        grid.addWidget(self.cancel_edit, row, 1)
        row += 1
        self.pause_button = QPushButton(gesture_label(PAUSE))
        self.pause_button.setToolTip(
            "Congela la última imagen emitida (nunca en negro) hasta reanudar. "
            "La ventana y la bandeja indican claramente que está en pausa."
        )
        self.pause_button.clicked.connect(lambda: self._on_gesture(PAUSE))
        grid.addWidget(self.pause_button, row, 0)
        self.pause_edit = QLineEdit()
        self.pause_edit.setMaximumWidth(150)
        grid.addWidget(self.pause_edit, row, 1)
        grid.setColumnStretch(2, 1)
        tabla.add_layout(grid)

        fila = QHBoxLayout()
        guardar = QPushButton("Guardar atajos")
        guardar.setObjectName("primary")
        guardar.clicked.connect(self.save_hotkeys)
        fila.addWidget(guardar)
        defecto = QPushButton("Atajos por defecto")
        defecto.clicked.connect(self.reset_hotkeys)
        fila.addWidget(defecto)
        fila.addStretch(1)
        tabla.add_layout(fila)
        self.form_message = QLabel()
        self.form_message.setWordWrap(True)
        tabla.add(self.form_message)
        layout.addWidget(tabla)

        galeria = Card("galería de escenas grabadas")
        ayuda = QLabel(
            "Gestos y frases importados desde la pestaña <b>Biblioteca</b>. «Probar» "
            "los hace ya (con la cámara en marcha). El atajo es opcional: escribe uno "
            "y pulsa «Guardar atajos»; no se permiten combinaciones repetidas."
        )
        ayuda.setWordWrap(True)
        ayuda.setObjectName("dim")
        galeria.add(ayuda)
        self.gallery_search = QLineEdit()
        self.gallery_search.setPlaceholderText("Buscar por número, nombre o descripción…")
        self.gallery_search.textChanged.connect(self._filter_gallery)
        galeria.add(self.gallery_search)
        # Columnas: miniatura, n.º, acción + descripción (2 líneas), al hablar,
        # atajo, probar. La descripción va bajo el nombre para que quepa en el
        # panel derecho sin barra horizontal.
        self.gallery = QTableWidget(0, 6)
        self.gallery.setHorizontalHeaderLabels(
            ["Miniatura", "Nº", "Acción", "Al hablar", "Atajo", ""]
        )
        self.gallery.verticalHeader().setVisible(False)
        self.gallery.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gallery.setSelectionMode(QAbstractItemView.NoSelection)
        self.gallery.setWordWrap(True)
        self.gallery.setMinimumHeight(260)
        cab = self.gallery.horizontalHeader()
        cab.setSectionResizeMode(2, QHeaderView.Stretch)
        for col in (0, 1, 5):
            cab.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        cab.setSectionResizeMode(3, QHeaderView.Interactive)
        cab.setSectionResizeMode(4, QHeaderView.Interactive)
        self.gallery.setColumnWidth(3, 96)
        self.gallery.setColumnWidth(4, 118)
        galeria.add(self.gallery)
        self.gallery_empty = QLabel(
            "Aún no hay escenas importadas. Ve a la pestaña Biblioteca y pulsa "
            "«Validar y preparar vídeos»."
        )
        self.gallery_empty.setWordWrap(True)
        self.gallery_empty.setObjectName("dim")
        galeria.add(self.gallery_empty)
        layout.addWidget(galeria)

        ajustes = Card("comportamiento")
        self.cooldown = PercentSlider(
            "Pausa antes de repetir", maximo=5000, defecto=800, sufijo=" ms",
            ayuda="Tras terminar un gesto, una nueva petición del mismo gesto "
            "dentro de este tiempo se ignora (evita repetirlo por doble pulsación).",
        )
        self.cooldown.bind(lambda v: setattr(self.cfg.gestures, "cooldown_ms", int(v)))
        ajustes.add(self.cooldown)
        self.auto_nod = QCheckBox("Asentir de vez en cuando mientras callo")
        self.auto_nod.setToolTip(
            "Usa el clip «Asentir», cada 1-2,5 minutos de silencio. Desactivado "
            "por defecto: asentir cuando nadie habla también se nota."
        )
        self.auto_nod.toggled.connect(lambda on: setattr(self.cfg.gestures, "auto_nod", bool(on)))
        ajustes.add(self.auto_nod)
        layout.addWidget(ajustes)

        guia = Card("cómo grabar un gesto")
        texto = QLabel(
            "Cada gesto es un clip de 1,5 a 3 s que <b>empieza y termina en la pose "
            "neutra</b> (boca cerrada, mirada a la cámara), con la misma luz y "
            "encuadre que los clips de habla y silencio. Así se enlaza sin salto. "
            "La pestaña <b>Grabar</b> tiene un plan «Gestos» que guía la toma. "
            "Detalles en docs/GUIA_GRABACION_Y_GESTOS.md."
        )
        texto.setWordWrap(True)
        texto.setObjectName("dim")
        guia.add(texto)
        layout.addWidget(guia)
        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # Datos
    # ------------------------------------------------------------------ #

    def reload(self) -> None:
        """Relee el manifiesto y la configuración y refresca los controles."""
        loaded = load_library(self.cfg)
        self.library = loaded.library
        self.issues = list(loaded.issues)
        g = self.cfg.gestures
        prev = self.hotkeys_check.blockSignals(True)
        self.hotkeys_check.setChecked(g.hotkeys_enabled)
        self.hotkeys_check.blockSignals(prev)
        self.cooldown.set_silently(g.cooldown_ms)
        prev = self.auto_nod.blockSignals(True)
        self.auto_nod.setChecked(g.auto_nod)
        self.auto_nod.blockSignals(prev)
        self.cancel_edit.setText(g.cancel_hotkey)
        self.pause_edit.setText(g.pause_hotkey)
        for name in GESTURES:
            entry = self.library.gestures.get(name)
            self.hotkey_edits[name].setText(
                entry.hotkey if entry and entry.hotkey else DEFAULT_HOTKEYS.get(name, "")
            )
        self._refresh_clips()
        self._rebuild_gallery()
        if self.issues:
            self._form("• " + "\n• ".join(self.issues), "warn")

    def wanted_hotkeys(self) -> dict[str, str]:
        """Atajos a registrar: gestos con clip + cancelar + pausar video."""
        out = dict(self.library.hotkeys())
        if self.cfg.gestures.cancel_hotkey:
            out[CANCEL] = self.cfg.gestures.cancel_hotkey
        if self.cfg.gestures.pause_hotkey:
            out[PAUSE] = self.cfg.gestures.pause_hotkey
        return out

    def assign_clip(self, name: str) -> bool:
        inicio = self.cfg.video.silent_clip or self.cfg.video.speaking_clip or ""
        ruta = self._pick_file(
            f"Clip del gesto «{gesture_label(name)}»",
            str(Path(inicio).parent) if inicio else "",
        )
        if not ruta:
            return False
        return self.set_gesture_clip(name, ruta)

    def set_gesture_clip(self, name: str, ruta: str) -> bool:
        """Asigna `ruta` al gesto (comprobando que se puede leer) y guarda."""
        from ..video.cache import ClipCacheError, probe_clip

        try:
            probe_clip(ruta)
        except ClipCacheError as exc:
            self._form(f"«{gesture_label(name)}»: {exc}", "err")
            return False
        hotkey = self.hotkey_edits[name].text().strip() or None
        self.library.set_gesture(name, ruta, hotkey)
        self.save_library()
        self._refresh_clips()
        self._form(
            f"Clip de «{gesture_label(name)}» guardado. Se usará al volver a iniciar.", "ok"
        )
        self._on_hotkeys_changed()
        return True

    def save_hotkeys(self) -> bool:
        """Valida y guarda todos los atajos. False (y mensaje) si hay errores."""
        errores: list[str] = []
        vistos: dict[tuple[int, int], str] = {}
        nuevos: dict[str, str] = {}
        filas = [(n, self.hotkey_edits[n].text()) for n in GESTURES]
        filas += [(n, e.text()) for n, e in self.scene_hotkey_edits.items()]
        filas.append((CANCEL, self.cancel_edit.text()))
        filas.append((PAUSE, self.pause_edit.text()))
        for name, texto in filas:
            texto = texto.strip()
            if not texto:
                nuevos[name] = ""
                continue
            try:
                hk = validate_hotkey(texto)
            except HotkeyError as exc:
                errores.append(f"{gesture_label(name)}: {exc}")
                continue
            clave = (hk.modifiers, hk.vk)
            if clave in vistos:
                errores.append(
                    f"{hk.text} está asignado a «{self._name(vistos[clave])}» y a "
                    f"«{self._name(name)}»."
                )
                continue
            vistos[clave] = name
            nuevos[name] = hk.text
        if errores:
            self._form("No se guardó nada:\n• " + "\n• ".join(errores), "err")
            return False
        for name in GESTURES:
            entry = self.library.gestures.get(name)
            if entry is not None:
                entry.hotkey = nuevos[name]
            self.hotkey_edits[name].setText(nuevos[name])
        for name, edit in self.scene_hotkey_edits.items():
            entry = self.library.gestures.get(name)
            if entry is not None:
                entry.hotkey = nuevos[name]
            edit.setText(nuevos[name])
        self.cfg.gestures.cancel_hotkey = nuevos[CANCEL]
        self.cancel_edit.setText(nuevos[CANCEL])
        self.cfg.gestures.pause_hotkey = nuevos[PAUSE]
        self.pause_edit.setText(nuevos[PAUSE])
        self.save_library()
        self._form("Atajos guardados.", "ok")
        self._on_hotkeys_changed()
        return True

    def reset_hotkeys(self) -> None:
        for name in GESTURES:
            self.hotkey_edits[name].setText(DEFAULT_HOTKEYS.get(name, ""))
        for edit in self.scene_hotkey_edits.values():
            edit.setText("")  # las escenas no tienen atajo por defecto
        self.cancel_edit.setText("Ctrl+Alt+F12")
        self.pause_edit.setText("Ctrl+Alt+F11")

    # ------------------------------------------------------------------ #
    # Refresco desde la ventana
    # ------------------------------------------------------------------ #

    def update_status(
        self,
        *,
        running: bool,
        mode: str,
        available: tuple[str, ...] | list[str],
        current: str = "",
        latency_ms: float = -1.0,
        hotkeys: str = "",
        paused: bool = False,
    ) -> None:
        if not running:
            texto = "Inicia la cámara para usar los gestos."
        elif mode != "director":
            texto = (
                "La sesión está en modo clásico (dos clips): los gestos requieren "
                "el modo director (pestaña Videos → Reproducción)."
            )
        else:
            texto = "Listo para gestos."
            if current:
                texto = f"Haciendo: {gesture_label(current)}."
            if latency_ms >= 0:
                texto += f"  Último gesto: {latency_ms:.0f} ms desde la petición hasta la imagen."
            if paused:
                texto = "⏸ Vídeo en pausa (imagen congelada). Pulsa «Reanudar video» para seguir."
        self.status_label.setText(texto)
        if hotkeys:
            self.hotkey_status.setText(hotkeys)
        listos = set(available) if running and mode == "director" else set()
        for name, boton in self.buttons.items():
            boton.setEnabled(name in listos)
        self.cancel_button.setEnabled(bool(listos) and not paused)
        director = running and mode == "director"
        self.pause_button.setEnabled(director)
        self.pause_button.setText("Reanudar video" if paused else gesture_label(PAUSE))
        for name, boton in self.scene_buttons.items():
            boton.setEnabled(name in listos and not paused)
        if paused:
            for boton in self.buttons.values():
                boton.setEnabled(False)

    # ------------------------------------------------------------------ #
    # Galería
    # ------------------------------------------------------------------ #

    def _name(self, name: str) -> str:
        scene = self.library.scene_for_gesture(name)
        if scene is not None and name not in GESTURES:
            return f"{scene.label} (n.º {scene.number})"
        return gesture_label(name)

    def gallery_rows(self) -> list:
        """Escenas de acción importadas, por número."""
        return [e for _, e in sorted(self.library.scenes.items()) if e.is_action and e.gesture]

    def speaking_note(self, entry) -> str:  # noqa: ANN001 - SceneEntry
        if entry.category == SPEAKING_ONE_SHOT:
            return "frase (es habla)"
        if speaking_variant(entry.gesture) in self.library.active_gestures():
            return "variante hablando"
        return "sin variante: espera pausa"

    def _rebuild_gallery(self) -> None:
        rows = self.gallery_rows()
        self.scene_hotkey_edits.clear()
        self.scene_buttons.clear()
        self.gallery.setRowCount(0)
        self.gallery.setRowCount(len(rows))
        activos = self.library.active_gestures()
        for r, e in enumerate(rows):
            thumb = QLabel()
            thumb.setAlignment(Qt.AlignCenter)
            pix = QPixmap(e.thumbnail) if e.thumbnail and Path(e.thumbnail).is_file() else QPixmap()
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(80, 45, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                thumb.setText("—")
            self.gallery.setCellWidget(r, 0, thumb)
            num = QTableWidgetItem(str(e.number))
            num.setData(Qt.UserRole, e.gesture)
            self.gallery.setItem(r, 1, num)
            label = QTableWidgetItem(f"{e.label}\n{e.description}" if e.description else e.label)
            label.setToolTip(f"{CATEGORY_LABELS.get(e.category, e.category)} — {e.description}")
            self.gallery.setItem(r, 2, label)
            nota = self.speaking_note(e)
            note_item = QTableWidgetItem(nota)
            if nota.startswith("sin variante"):
                note_item.setToolTip(
                    "Si lo pides mientras hablas, espera una micro-pausa (máx. 200 ms) "
                    "para no mostrar la boca cerrada a mitad de frase."
                )
            self.gallery.setItem(r, 3, note_item)
            entry = self.library.gestures.get(e.gesture)
            if e.gesture in GESTURES:
                # Gesto del catálogo: su atajo se edita en la tabla de arriba.
                fijo = QLabel(f"{self.hotkey_edits[e.gesture].text() or '—'} (arriba)")
                fijo.setObjectName("dim")
                self.gallery.setCellWidget(r, 4, fijo)
            else:
                edit = QLineEdit(entry.hotkey if entry else "")
                edit.setPlaceholderText("sin atajo")
                self.scene_hotkey_edits[e.gesture] = edit
                self.gallery.setCellWidget(r, 4, edit)
            probar = QPushButton("Probar")
            probar.setToolTip("Hace esta acción ahora (requiere la cámara en marcha).")
            probar.setEnabled(False)
            probar.clicked.connect(lambda _=False, n=e.gesture: self._on_gesture(n))
            if e.gesture not in activos:
                probar.setToolTip("Desactivada o sin archivo: no se puede usar.")
            self.scene_buttons[e.gesture] = probar
            self.gallery.setCellWidget(r, 5, probar)
            self.gallery.setRowHeight(r, 62)
        self.gallery.setVisible(bool(rows))
        self.gallery_empty.setVisible(not rows)
        self._filter_gallery(self.gallery_search.text())

    def _filter_gallery(self, texto: str) -> None:
        q = texto.strip().casefold()
        for r in range(self.gallery.rowCount()):
            campos = [self.gallery.item(r, c).text() for c in (1, 2, 3)]
            visible = not q or any(q in c.casefold() for c in campos)
            self.gallery.setRowHidden(r, not visible)

    # ------------------------------------------------------------------ #

    def _refresh_clips(self) -> None:
        activos = self.library.active_gestures()
        for name, etiqueta in self.clip_labels.items():
            entry = self.library.gestures.get(name)
            if name in activos:
                etiqueta.setText(Path(activos[name].clip).name)
                etiqueta.setToolTip(activos[name].clip)
            elif entry and entry.clip:
                etiqueta.setText(f"no se encuentra: {Path(entry.clip).name}")
                etiqueta.setToolTip(entry.clip)
            else:
                etiqueta.setText("sin clip")
                etiqueta.setToolTip("")

    def save_library(self) -> None:
        save_library(self.library, manifest_path(self.cfg))
        self._on_library_changed()

    def _on_hotkeys_toggled(self, on: bool) -> None:
        self.cfg.gestures.hotkeys_enabled = bool(on)
        self._on_hotkeys_changed()

    def set_hotkeys_enabled(self, on: bool) -> None:
        """Desde fuera (bandeja): marca el check, que a su vez avisa."""
        if self.hotkeys_check.isChecked() != bool(on):
            self.hotkeys_check.setChecked(bool(on))

    def _form(self, texto: str, nivel: str) -> None:
        color = {"ok": "#56d364", "warn": "#e3b341", "err": "#ffb3b8"}.get(nivel, "#c9d1d9")
        self.form_message.setStyleSheet(f"color:{color};")
        self.form_message.setText(texto)

    def _dialog_pick(self, titulo: str, inicio: str) -> str:
        ruta, _ = QFileDialog.getOpenFileName(self, titulo, inicio, VIDEO_FILTER)
        return ruta
