"""Interfaz: pestaña Biblioteca, botones de cámara y galería de escenas.

Pruebas SIMULADAS en Qt offscreen, con una carpeta temporal de 62 videos
sintéticos diminutos (no la carpeta real del usuario).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 no está instalado")

from PySide6.QtWidgets import QApplication  # noqa: E402

from camvoice.config import AppConfig  # noqa: E402
from camvoice.scenes import EXPECTED_COUNT, SCENES, SKIPPED  # noqa: E402

from .test_scenes_import import write_video  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def roll(tmp_path_factory) -> Path:
    """Carpeta tipo Camera Roll: 62 numeradas + omitida + ruido."""
    root = tmp_path_factory.mktemp("roll")
    for s in SCENES:
        write_video(root / f"{s.number}.mp4", seconds=1.2 if s.is_action else 3.2)
    write_video(root / "50.mp4", seconds=1.0)
    (root / "foto.jpg").write_bytes(b"\xff\xd8")
    (root / "notas.txt").write_text("x")
    return root


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    c = AppConfig()
    c.video.library_manifest = str(tmp_path / "lib" / "manifest.json")
    return c


@pytest.fixture
def tab(qt_app, cfg):
    from camvoice.ui.library_tab import LibraryTab

    avisos = []
    t = LibraryTab(cfg, on_imported=lambda: avisos.append(1), pick_folder=lambda s: s)
    t.avisos = avisos
    yield t
    t.shutdown()
    t.deleteLater()


def _opts(tab, folder, **kw):
    o = tab.options()
    o.update(folder=folder, prepare=False, **kw)
    return o


def _import(tab, folder):
    result = tab.pipeline(lambda d, t, m: None, lambda: False, _opts(tab, folder))
    tab._apply_result(result, 0.1)
    return result


# --------------------------------------------------------------------------- #
# Biblioteca
# --------------------------------------------------------------------------- #


def test_contador_inicial_y_omitidas(tab):
    assert not tab.has_library
    texto = tab.counter_text()
    assert texto.startswith(f"0/{EXPECTED_COUNT} importadas")
    for n in SKIPPED:
        assert str(n) in texto
    assert tab.table.rowCount() == EXPECTED_COUNT


def test_opciones_avanzadas_plegadas(tab):
    assert tab.advanced.isCheckable() and not tab.advanced.isChecked()
    assert tab.caches_check.isChecked(), "preparar cachés va activado por defecto"
    assert not tab.copy_check.isChecked(), "por defecto se referencia, no se copia"


def test_detectar_marca_las_62(tab, roll):
    tab.folder_edit.setText(str(roll))
    scan = tab.detect()
    assert scan is not None
    assert len(scan.entries) == EXPECTED_COUNT
    assert all(r.path for r in tab.rows.values())
    assert "ignorados" in tab.scan_info.text()
    assert not tab.has_library, "detectar no importa nada"


def test_detectar_carpeta_inexistente_no_rompe(tab, tmp_path):
    tab.folder_edit.setText(str(tmp_path / "no-existe"))
    assert tab.detect() is None
    assert tab.scan_info.text()


def test_detectar_marca_las_que_faltan(tab, tmp_path):
    parcial = tmp_path / "parcial"
    write_video(parcial / "1.mp4")
    write_video(parcial / "2.mp4")
    tab.folder_edit.setText(str(parcial))
    tab.detect()
    from camvoice.ui.library_tab import MISSING

    assert tab.rows[3].status == MISSING
    assert tab.rows[1].status != MISSING


def test_filtros_y_busqueda(tab):
    total = len(tab.visible_numbers())
    assert total == EXPECTED_COUNT
    tab.search.setText("1")
    tab.apply_filters()
    visibles = tab.visible_numbers()
    assert 0 < len(visibles) < total
    tab.search.setText("")
    idx = next(i for i in range(tab.category_filter.count()) if tab.category_filter.itemData(i))
    tab.category_filter.setCurrentIndex(idx)
    tab.apply_filters()
    cat = tab.category_filter.itemData(idx)
    assert all(s.category == cat for s in SCENES if s.number in tab.visible_numbers())


def test_validar_e_importar_62(tab, roll, cfg):
    avance = []
    result = tab.pipeline(lambda d, t, m: avance.append(m), lambda: False, _opts(tab, roll))
    assert any(m.startswith("Fase 1/2 · Validación") for m in avance)
    assert any(m.startswith("Fase 2/2 · Importación") for m in avance)
    tab._apply_result(result, 0.1)
    assert tab.imported_count == EXPECTED_COUNT
    assert tab.counter_text().startswith(f"{EXPECTED_COUNT}/{EXPECTED_COUNT} importadas")
    assert tab.avisos, "debe avisar a la ventana para recargar clips y gestos"
    assert Path(cfg.video.library_manifest).is_file()
    assert "manifiesto" in tab.report.toPlainText()


def test_cancelar_la_validacion(tab, roll):
    from camvoice.video.importer import ImportCancelled

    with pytest.raises((ImportCancelled, Exception)) as exc:
        tab.pipeline(lambda d, t, m: None, lambda: True, _opts(tab, roll, force=True))
    assert "ancel" in type(exc.value).__name__ or "ancel" in str(exc.value)
    assert tab.imported_count == 0


def test_deshacer_importacion(tab, roll):
    _import(tab, roll)
    assert tab.has_library
    tab.rollback()
    assert not tab.has_library
    assert "Deshacer" in tab.report.toPlainText()


def test_la_pestana_reabre_con_lo_importado(qt_app, cfg, roll):
    from camvoice.ui.library_tab import LibraryTab

    a = LibraryTab(cfg)
    _import(a, roll)
    a.shutdown()
    b = LibraryTab(cfg)
    assert b.imported_count == EXPECTED_COUNT
    assert b.folder_edit.text() == str(roll)
    b.shutdown()


def test_ensayos_bloqueados_sin_preparar(tab, roll):
    _import(tab, roll)
    tab._update_buttons()
    assert all(not b.isEnabled() for b in tab.trial_buttons.values())


def test_sin_tocar_widgets_desde_el_hilo(tab, roll):
    """options() se lee antes; pipeline con opts no debe leer los widgets."""
    opts = _opts(tab, roll)
    tab.folder_edit.setText("Z:/otra/carpeta")
    result = tab.pipeline(lambda d, t, m: None, lambda: False, opts)
    assert len(result["scan"].entries) == EXPECTED_COUNT


# --------------------------------------------------------------------------- #
# Ventana: botones de cámara e indicación de vídeo pregrabado
# --------------------------------------------------------------------------- #


@pytest.fixture
def window(qt_app, tmp_path):
    from camvoice.ui.app import MainWindow

    c = AppConfig()
    c.video.library_manifest = str(tmp_path / "lib" / "manifest.json")
    w = MainWindow(c, config_file=tmp_path / "config.json")
    yield w
    w.library_tab.shutdown()
    w.session.close()
    w.timer.stop()
    w.deleteLater()


def test_botones_de_camara(window):
    assert "vista previa" in window.preview_button.text().lower()
    assert "cámara virtual" in window.start_button.text().lower()
    assert "Detener" in window.stop_button.text()
    window._set_running(False)
    assert "detenida" in window.camera_status.text()
    assert window.preview_button.isEnabled() and window.start_button.isEnabled()
    assert not window.stop_button.isEnabled()


def test_estado_de_camara_indica_video_pregrabado(window):
    window._preview_only = True
    window._set_camera_status(True, virtual=False)
    assert "VÍDEO PREGRABADO" in window.camera_status.text()
    assert "no emite" in window.camera_status.text()
    window._preview_only = False
    window._set_camera_status(True, virtual=True, device="CamVoice")
    assert "EMITIENDO VÍDEO PREGRABADO" in window.camera_status.text()


def test_vista_previa_restaura_la_salida(window, monkeypatch, qt_app):
    """La vista previa desactiva la cámara virtual solo mientras dura: si la
    preparación falla (aquí no hay clips reales) se restaura el ajuste."""
    import time

    window.cfg.output.enabled = True
    monkeypatch.setattr(window, "_collect_into_config", lambda: None)
    monkeypatch.setattr(window.cfg, "validate", lambda: [])
    monkeypatch.setattr(window, "_show_message", lambda *a, **k: None)
    window._on_start(preview=True)
    assert window._preview_only is True
    assert window.cfg.output.enabled is False, "la vista previa no debe emitir"
    limite = time.monotonic() + 30
    while window._thread is not None and time.monotonic() < limite:
        qt_app.processEvents()
        time.sleep(0.01)
    assert window._thread is None, "la preparación no terminó"
    assert window.cfg.output.enabled is True
    assert window._preview_only is False and window._saved_output_enabled is None
    assert window.start_button.isEnabled() and window.preview_button.isEnabled()


def test_biblioteca_es_la_pestana_inicial_sin_importar(window):
    assert window.tabs.currentWidget() is window.library_page


def test_ensayo_no_arranca_si_prepara(window):
    window.library_tab._thread = object()  # simula un trabajo en curso
    try:
        assert window.library_tab.busy()
    finally:
        window.library_tab._thread = None


# --------------------------------------------------------------------------- #
# Galería de escenas en la pestaña Gestos
# --------------------------------------------------------------------------- #


@pytest.fixture
def gtab(qt_app, cfg, roll):
    from camvoice.ui.gestures_tab import GesturesTab
    from camvoice.ui.library_tab import LibraryTab

    lt = LibraryTab(cfg)
    _import(lt, roll)
    lt.shutdown()
    hechos = []
    g = GesturesTab(cfg, on_gesture=hechos.append, on_hotkeys_changed=lambda: None)
    g.hechos = hechos
    yield g
    g.deleteLater()


def test_galeria_lista_las_acciones(gtab):
    filas = gtab.gallery_rows()
    acciones = [s for s in SCENES if s.is_action]
    assert len(filas) == len(acciones) > 0
    assert gtab.gallery.rowCount() == len(filas)
    assert gtab.gallery.isVisibleTo(gtab) or not gtab.isVisible()
    notas = {gtab.speaking_note(e) for e in filas}
    assert notas <= {"frase (es habla)", "variante hablando", "sin variante: espera pausa"}
    assert "sin variante: espera pausa" in notas


def test_escenas_sin_atajo_por_defecto(gtab):
    assert gtab.scene_hotkey_edits, "debe haber escenas fuera del catálogo con atajo editable"
    assert all(e.text() == "" for e in gtab.scene_hotkey_edits.values())


def test_colision_entre_escena_y_catalogo(gtab):
    from camvoice.gestures import GESTURES

    usado = next(gtab.hotkey_edits[n].text() for n in GESTURES if gtab.hotkey_edits[n].text())
    nombre, edit = next(iter(gtab.scene_hotkey_edits.items()))
    edit.setText(usado)
    assert gtab.save_hotkeys() is False
    assert gtab.library.gestures[nombre].hotkey == ""


def test_atajo_de_escena_se_guarda(gtab, cfg):
    nombre, edit = next(iter(gtab.scene_hotkey_edits.items()))
    edit.setText("Ctrl+Alt+Shift+F9")
    assert gtab.save_hotkeys() is True
    from camvoice.video.library import load_library

    assert load_library(cfg).library.gestures[nombre].hotkey


def test_probar_lanza_la_accion(gtab):
    nombre, boton = next(iter(gtab.scene_buttons.items()))
    boton.setEnabled(True)
    boton.click()
    assert gtab.hechos == [nombre]


def test_buscar_en_la_galeria(gtab):
    gtab.gallery_search.setText("zzzz-no-existe")
    assert all(gtab.gallery.isRowHidden(r) for r in range(gtab.gallery.rowCount()))
    gtab.gallery_search.setText("")
    assert not any(gtab.gallery.isRowHidden(r) for r in range(gtab.gallery.rowCount()))
