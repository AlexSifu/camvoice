"""Pruebas de la ventana, sin interacción humana.

Qt permite construir y manipular widgets sin que nadie mire, así que lo que se
verifica es lo que de verdad se puede romper al refactorizar: que la ventana se
construya, que las pestañas estén, que los controles escriban en la
configuración y que los que invalidan la caché se bloqueen al arrancar.

Se omiten si no hay PySide6 o si la plataforma no tiene con qué dibujar.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6", reason="PySide6 no está instalado")

from PySide6.QtWidgets import QApplication  # noqa: E402

from camvoice.config import AppConfig  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    # offscreen: dibuja en memoria, sin necesitar un escritorio. Imprescindible
    # para que estas pruebas corran en integración continua.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app, tmp_path):
    from camvoice.ui.app import MainWindow

    cfg = AppConfig()
    ventana = MainWindow(cfg, config_file=tmp_path / "config.json")
    yield ventana
    ventana.session.close()
    ventana.timer.stop()
    ventana.deleteLater()


# --------------------------------------------------------------------------- #
# Estructura
# --------------------------------------------------------------------------- #


def test_la_ventana_se_construye(window):
    assert window.windowTitle()


def test_estan_todas_las_pestanas(window):
    nombres = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert nombres == [
        "Videos", "Grabar", "Gestos", "Biblioteca", "Micrófono", "Detector", "Efectos", "Salida",
    ]


def test_un_clip_grabado_se_asigna_a_la_configuracion(window, tmp_path):
    """La pestaña Grabar entrega el archivo y debe quedar seleccionado."""
    hablando = tmp_path / "hablando.mp4"
    silencio = tmp_path / "silencio.mp4"
    hablando.write_bytes(b"x")
    silencio.write_bytes(b"x")

    window._on_clip_recorded("speaking", str(hablando))
    assert window.cfg.video.speaking_clip == str(hablando)
    assert window.tabs.currentIndex() == 0, "debe volver a la pestaña Videos"

    window._on_clip_recorded("silent", str(silencio))
    assert window.cfg.video.silent_clip == str(silencio)


def test_la_preferencia_de_bandeja_va_y_vuelve(window, tmp_path):
    window.tray_check.setChecked(False)
    window._on_save()
    assert not AppConfig.load(tmp_path / "config.json").ui.minimize_to_tray

    window.tray_check.setChecked(True)
    window._on_save()
    assert AppConfig.load(tmp_path / "config.json").ui.minimize_to_tray


def test_hay_mas_de_una_resolucion_y_cadencia(window):
    """CA-10: la resolución y los FPS deben ser ajustables."""
    assert window.resolution.count() >= 8
    assert window.fps_combo.count() >= 5
    valores = [window.resolution.itemData(i) for i in range(window.resolution.count())]
    assert (640, 480) in valores
    assert (1280, 720) in valores
    # Todas pares: NV12 no admite dimensiones impares.
    for ancho, alto in valores:
        assert ancho % 2 == 0 and alto % 2 == 0, f"{ancho}x{alto} tiene un lado impar"


def test_arranca_sin_efectos_activos(window):
    assert "Ninguno activo" in window.effects_summary.text()


# --------------------------------------------------------------------------- #
# Efectos
# --------------------------------------------------------------------------- #


def test_los_deslizadores_escriben_en_la_configuracion(window):
    window.blur.set_value(45)
    window.pixelate.set_value(20)
    window.grain.set_value(15)
    window.zoom.set_value(1.4)
    window.brightness.set_value(-30)

    e = window.cfg.effects
    assert e.blur == pytest.approx(45)
    assert e.pixelate == pytest.approx(20)
    assert e.grain == pytest.approx(15)
    assert e.zoom == pytest.approx(1.4, abs=0.01)
    assert e.brightness == pytest.approx(-30)


def test_el_resumen_refleja_lo_activo(window):
    window.blur.set_value(50)
    texto = window.effects_summary.text()
    assert "desenfoque" in texto
    assert "50" in texto


def test_un_preajuste_reemplaza_los_valores_anteriores(window):
    """Un preajuste debe dejar un estado limpio, no sumarse al anterior."""
    window.blur.set_value(80)
    window.grain.set_value(60)

    indice = next(
        i for i in range(window.preset_combo.count())
        if window.preset_combo.itemData(i) == "Privacidad alta"
    )
    window.preset_combo.setCurrentIndex(indice)

    e = window.cfg.effects
    assert e.pixelate > 0, "el preajuste no se aplicó"
    assert e.blur == 0.0, "quedó el desenfoque del ajuste anterior"
    assert e.grain == 0.0, "quedó el grano del ajuste anterior"


def test_el_preajuste_mueve_los_deslizadores(window):
    indice = next(
        i for i in range(window.preset_combo.count())
        if window.preset_combo.itemData(i) == "Privacidad media"
    )
    window.preset_combo.setCurrentIndex(indice)
    assert window.blur.value() == pytest.approx(window.cfg.effects.blur)


def test_restablecer_deja_todo_a_cero(window):
    window.blur.set_value(70)
    window.vignette.set_value(40)
    window.zoom.set_value(2.0)
    window._reset_effects()

    e = window.cfg.effects
    assert (e.blur, e.pixelate, e.vignette, e.grain) == (0.0, 0.0, 0.0, 0.0)
    assert e.zoom == pytest.approx(1.0)
    assert "Ninguno activo" in window.effects_summary.text()


def test_el_interruptor_general_de_efectos(window):
    window.blur.set_value(50)
    window.effects_enabled.setChecked(False)
    assert not window.cfg.effects.enabled
    assert "Ninguno activo" in window.effects_summary.text()
    window.effects_enabled.setChecked(True)
    assert window.cfg.effects.enabled


# --------------------------------------------------------------------------- #
# Detector y video
# --------------------------------------------------------------------------- #


def test_los_controles_del_detector_escriben_en_la_configuracion(window):
    window.attack.set_value(200)
    window.release.set_value(600)
    window.snr_on.set_value(14)
    window.gate_db.set_value(-45)

    d = window.cfg.vad
    assert d.attack_ms == 200
    assert d.release_ms == 600
    assert d.snr_on_db == pytest.approx(14)
    assert d.gate_db == pytest.approx(-45)


def test_el_umbral_de_apagado_nunca_supera_al_de_encendido(window):
    """Si se invirtieran, la histéresis de trama dejaría de funcionar."""
    for valor in (3, 9, 25):
        window.snr_on.set_value(valor)
        assert window.cfg.vad.snr_off_db <= window.cfg.vad.snr_on_db


def test_los_controles_de_video_en_caliente(window):
    window.transition.set_value(300)
    window.resume_window.set_value(2500)
    assert window.cfg.video.transition_ms == 300
    assert window.cfg.video.resume_window_ms == 2500


# --------------------------------------------------------------------------- #
# Bloqueo durante la sesión
# --------------------------------------------------------------------------- #


def test_lo_que_invalida_la_cache_se_bloquea_al_arrancar(window):
    """Fingir que un cambio surte efecto es peor que deshabilitarlo."""
    bloqueables = (
        window.speaking_button, window.silent_button, window.resolution,
        window.fps_combo, window.fit_combo, window.mirror_check,
    )
    window._set_running(True)
    assert not any(w.isEnabled() for w in bloqueables)
    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()

    window._set_running(False)
    assert all(w.isEnabled() for w in bloqueables)
    assert window.start_button.isEnabled()


def test_los_efectos_siguen_activos_durante_la_sesion(window):
    """Son el grupo que SÍ se ajusta en caliente: no deben bloquearse."""
    window._set_running(True)
    for widget in (window.blur, window.pixelate, window.grain, window.zoom):
        assert widget.isEnabled()
    assert window.preset_combo.isEnabled()


# --------------------------------------------------------------------------- #
# Guardado
# --------------------------------------------------------------------------- #


def test_guardar_y_recuperar_los_efectos(window, tmp_path):
    window.blur.set_value(35)
    window.pixelate.set_value(15)
    window.vignette.set_value(25)
    window._on_save()

    recargada = AppConfig.load(tmp_path / "config.json")
    assert recargada.effects.blur == pytest.approx(35)
    assert recargada.effects.pixelate == pytest.approx(15)
    assert recargada.effects.vignette == pytest.approx(25)


def test_la_configuracion_guardada_vuelve_a_los_deslizadores(qt_app, tmp_path):
    from camvoice.ui.app import MainWindow

    cfg = AppConfig()
    cfg.effects.blur = 42.0
    cfg.effects.zoom = 1.75
    cfg.vad.attack_ms = 220
    ruta = cfg.save(tmp_path / "config.json")

    ventana = MainWindow(AppConfig.load(ruta), config_file=ruta)
    try:
        assert ventana.blur.value() == pytest.approx(42.0)
        assert ventana.zoom.value() == pytest.approx(1.75, abs=0.01)
        assert ventana.attack.value() == pytest.approx(220)
    finally:
        ventana.session.close()
        ventana.timer.stop()
        ventana.deleteLater()


def test_cargar_la_configuracion_no_dispara_cambios(qt_app, tmp_path):
    """Durante la carga, mover un widget no debe reescribir la configuración.

    Es la razón del indicador `_loading`: sin él, rellenar los deslizadores al
    abrir la ventana disparaba sus señales y sobrescribía con valores a medio
    cargar lo que se acababa de leer del archivo.
    """
    from camvoice.ui.app import MainWindow

    cfg = AppConfig()
    cfg.effects.blur = 33.0
    cfg.vad.release_ms = 777
    ruta = cfg.save(tmp_path / "config.json")

    cargada = AppConfig.load(ruta)
    ventana = MainWindow(cargada, config_file=ruta)
    try:
        assert cargada.effects.blur == pytest.approx(33.0)
        assert cargada.vad.release_ms == 777
    finally:
        ventana.session.close()
        ventana.timer.stop()
        ventana.deleteLater()


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #


def test_el_deslizador_con_decimales_conserva_la_precision(qt_app):
    from camvoice.ui.widgets import PercentSlider

    s = PercentSlider("Zoom", minimo=1.0, maximo=3.0, defecto=1.0, decimales=2)
    s.set_value(1.37)
    assert s.value() == pytest.approx(1.37, abs=0.005)


def test_set_silently_no_emite_senal(qt_app):
    from camvoice.ui.widgets import PercentSlider

    s = PercentSlider("Prueba")
    recibidos: list[float] = []
    s.bind(recibidos.append)

    s.set_silently(50)
    assert not recibidos, "set_silently emitió la señal"
    assert s.value() == pytest.approx(50)

    s.set_value(60)
    assert recibidos == [pytest.approx(60)]


def test_el_boton_de_reinicio_vuelve_al_valor_por_defecto(qt_app):
    from camvoice.ui.widgets import PercentSlider

    s = PercentSlider("Prueba", defecto=25)
    s.set_value(80)
    s._reset.click()
    assert s.value() == pytest.approx(25)


# --------------------------------------------------------------------------- #
# Pestana Grabar: maquetacion y cronometro
# --------------------------------------------------------------------------- #


def test_la_maquetacion_no_se_mueve_con_el_texto_del_guion(window, qt_app):
    """Visto en una captura real: el texto largo del guion empujaba el resto de
    la pagina hasta recortar los botones por la derecha. El bloque del guion
    tiene altura fija justamente para eso."""
    window.resize(1180, 780)
    window.show()
    qt_app.processEvents()
    window.tabs.setCurrentIndex(1)
    qt_app.processEvents()

    rec = window.recorder_tab
    plan = rec._current_plan()

    def geometrias():
        return (
            rec.record_button.geometry(),
            rec.cancel_button.geometry(),
            rec.progress.geometry(),
        )

    base = geometrias()
    for cue in plan.cues():
        rec.cue_title.setText(cue.title)
        rec.cue_detail.setText(cue.detail)
        siguiente = plan.next_cue_at(cue.start_s + 0.01)
        rec.cue_next.setText(siguiente.detail if siguiente else "")
        qt_app.processEvents()
        assert geometrias() == base, f"la maquetacion se movio con: {cue.detail[:40]!r}"


def test_los_botones_caben_en_el_panel(window, qt_app):
    """Sin esto, «Cancelar» quedaba recortado fuera de la vista."""
    window.resize(1180, 780)
    window.show()
    qt_app.processEvents()
    window.tabs.setCurrentIndex(1)
    qt_app.processEvents()

    rec = window.recorder_tab
    assert rec.cancel_button.geometry().right() <= rec.width()
    assert rec.record_button.geometry().left() >= 0


def test_la_cuenta_atras_es_configurable(window):
    rec = window.recorder_tab
    assert rec.countdown.slider.minimum() == 0
    assert rec.countdown.slider.maximum() >= 10
    rec.countdown.set_value(8)
    assert rec.countdown.value() == pytest.approx(8)


def test_cambiar_la_duracion_reparte_el_guion_de_nuevo(window):
    rec = window.recorder_tab
    rec.duration.set_value(8)
    pocas = len(rec._current_plan().reading_phrases())
    rec.duration.set_value(22)
    muchas = len(rec._current_plan().reading_phrases())
    assert pocas < muchas


def test_el_clip_de_silencio_no_muestra_frases(window):
    rec = window.recorder_tab
    rec.kind_combo.setCurrentIndex(1)
    assert rec._current_plan().reading_phrases() == []


# --------------------------------------------------------------------------- #
# Gestos, biblioteca, indicador de video pregrabado
# --------------------------------------------------------------------------- #


@pytest.fixture
def gesture_clip(tmp_path):
    from camvoice.testclips import make_test_clips

    _, sil = make_test_clips(tmp_path / "g", width=64, height=48, fps=10, seconds=1.0)
    return str(sil)


def test_pestana_gestos_tiene_un_boton_por_gesto(window):
    from camvoice.gestures import GESTURES

    tab = window.gestures_tab
    assert set(tab.buttons) == set(GESTURES)
    # Sin sesión en marcha, los botones están desactivados.
    window._refresh()
    assert not any(b.isEnabled() for b in tab.buttons.values())
    assert "Inicia la cámara" in tab.status_label.text()


def test_asignar_clip_de_gesto_lo_guarda_en_el_manifiesto(window, gesture_clip):
    from camvoice.video.library import load_library

    tab = window.gestures_tab
    tab._pick_file = lambda *_: gesture_clip
    assert tab.assign_clip("saludar")
    lib = load_library(window.cfg).library
    assert lib.active_gestures()["saludar"].clip == gesture_clip
    assert tab.clip_labels["saludar"].text().endswith(".mp4")
    assert "saludar" in tab.wanted_hotkeys()


def test_clip_de_gesto_ilegible_no_se_guarda(window, tmp_path):
    malo = tmp_path / "malo.mp4"
    malo.write_bytes(b"no es video")
    tab = window.gestures_tab
    tab._pick_file = lambda *_: str(malo)
    assert not tab.assign_clip("sonreir")
    assert "sonreir" not in tab.library.gestures


def test_atajos_invalidos_no_se_guardan(window, gesture_clip):
    tab = window.gestures_tab
    tab._pick_file = lambda *_: gesture_clip
    tab.assign_clip("saludar")
    tab.hotkey_edits["saludar"].setText("Ctrl+Shift+M")
    assert not tab.save_hotkeys()
    assert "Teams" in tab.form_message.text()
    tab.hotkey_edits["saludar"].setText("Ctrl+Alt+F12")  # choca con cancelar
    assert not tab.save_hotkeys()
    assert "Cancelar gesto" in tab.form_message.text()


def test_atajos_validos_se_guardan_normalizados(window, gesture_clip):
    from camvoice.video.library import load_library

    tab = window.gestures_tab
    tab._pick_file = lambda *_: gesture_clip
    tab.assign_clip("saludar")
    tab.hotkey_edits["saludar"].setText("ctrl+alt+f9")
    assert tab.save_hotkeys()
    assert load_library(window.cfg).library.gestures["saludar"].hotkey == "Ctrl+Alt+F9"
    assert tab.hotkey_edits["saludar"].text() == "Ctrl+Alt+F9"


def test_suspender_atajos_actualiza_la_configuracion(window):
    window.gestures_tab.hotkeys_check.setChecked(False)
    assert window.cfg.gestures.hotkeys_enabled is False
    assert window.hotkeys.suspended
    window.gestures_tab.set_hotkeys_enabled(True)
    assert window.cfg.gestures.hotkeys_enabled is True
    assert not window.hotkeys.suspended


def test_gesto_sin_sesion_avisa_y_no_falla(window):
    window._on_gesture("saludar")
    assert "Inicia la cámara" in window.statusBar().currentMessage()


def test_indicador_de_video_pregrabado_visible_al_correr(window):
    window._set_running(True)
    assert not window.prerecorded_indicator.isHidden()
    assert "PREGRABADO" in window.prerecorded_indicator.text()
    window._set_running(False)
    assert window.prerecorded_indicator.isHidden()


def test_modo_de_reproduccion_se_guarda_y_no_hay_rotulo(window, tmp_path):
    # Requisito de salida limpia: ya no existe la opción de rotular la imagen;
    # el aviso de video pregrabado vive solo en la interfaz local.
    assert not hasattr(window, "label_check")
    window.playback_combo.setCurrentIndex(window.playback_combo.findData("classic"))
    window.normalize_check.setChecked(False)
    window._on_save()
    cfg = AppConfig.load(tmp_path / "config.json")
    assert not hasattr(cfg.output, "prerecorded_label")
    assert cfg.video.playback == "classic"
    assert cfg.video.normalize_clips is False


def test_modo_y_biblioteca_se_bloquean_al_correr(window):
    window._set_running(True)
    assert not window.playback_combo.isEnabled()
    assert not any(b.isEnabled() for b in window.pool_buttons)
    window._set_running(False)
    assert window.playback_combo.isEnabled()


def test_clips_extra_van_al_manifiesto(window, gesture_clip):
    from camvoice.video.library import load_library

    window.add_pool_path("silent", gesture_clip)
    window.add_pool_path("silent", gesture_clip)  # sin duplicar
    assert load_library(window.cfg).library.silent == [gesture_clip]
    assert window.pool_lists["silent"].count() == 1
    window.pool_lists["silent"].setCurrentRow(0)
    window._remove_pool_clip("silent")
    assert load_library(window.cfg).library.silent == []
    assert window.pool_lists["silent"].count() == 0


def test_preajuste_conversacion_cambia_los_tiempos(window):
    combo_ = window.vad_preset
    combo_.setCurrentIndex(combo_.findData("Conversación (respuestas cortas)"))
    assert window.cfg.vad.attack_ms == 80
    assert window.cfg.vad.release_ms == 700
    assert window.attack.value() == pytest.approx(80)


def test_insignias_de_estado_nuevas():
    from camvoice.ui.widgets import state_badge_style

    assert state_badge_style("GESTURE")[0] == "Gesto"
    assert "Degradado" in state_badge_style("DEGRADED")[0]


def test_menu_de_gestos_en_la_bandeja(qt_app):
    from camvoice.ui.tray import TrayController

    pedidos, conmutados = [], []
    tray = TrayController(
        on_show=lambda: None, on_start=lambda: None, on_stop=lambda: None,
        on_quit=lambda: None, on_gesture=pedidos.append,
        on_hotkeys_toggled=conmutados.append,
    )
    textos = [a.text() for a in tray.gestures_menu.actions() if a.text()]
    assert any("Sin gestos" in t for t in textos)

    tray.set_gestures(("saludar", "asentir"), enabled=True, hotkeys={"saludar": "Ctrl+Alt+F1"})
    acciones = {a.text().split("\t")[0]: a for a in tray.gestures_menu.actions() if a.text()}
    assert "Saludar" in acciones and "Cancelar gesto" in acciones
    acciones["Saludar"].trigger()
    acciones["Cancelar gesto"].trigger()
    assert pedidos == ["saludar", "neutro"]

    tray.hotkeys_action.setChecked(False)
    assert conmutados == [False]
    tray.set_hotkeys_checked(True)  # reflejo sin eco
    assert conmutados == [False]
    tray.update_state("GESTURE", running=True)
    tray.hide()


def test_bandeja_ofrece_pausa_y_la_indica(qt_app):
    from camvoice.ui.tray import TrayController

    pedidos = []
    tray = TrayController(
        on_show=lambda: None, on_start=lambda: None, on_stop=lambda: None,
        on_quit=lambda: None, on_gesture=pedidos.append,
        on_hotkeys_toggled=lambda _on: None,
    )
    tray.set_gestures(("saludar",), enabled=True)
    acciones = {a.text().split("\t")[0]: a for a in tray.gestures_menu.actions() if a.text()}
    assert "Pausar / reanudar video" in acciones
    acciones["Pausar / reanudar video"].trigger()
    assert pedidos == ["pausa"]
    tray.update_state("VIDEO_PAUSED", running=True)
    assert "pausa" in tray.icon.toolTip().lower()
    tray.hide()


def test_pestana_gestos_boton_y_atajo_de_pausa(window, tmp_path):
    from camvoice.gestures import PAUSE

    tab = window.gestures_tab
    assert tab.pause_edit.text() == "Ctrl+Alt+F11"
    assert tab.wanted_hotkeys()[PAUSE] == "Ctrl+Alt+F11"
    tab.update_status(running=True, mode="director", available=("saludar",), paused=True)
    assert tab.pause_button.isEnabled()
    assert tab.pause_button.text() == "Reanudar video"
    assert "pausa" in tab.status_label.text().lower()
    assert not any(b.isEnabled() for b in tab.buttons.values())
    tab.update_status(running=True, mode="director", available=("saludar",), paused=False)
    assert tab.pause_button.text() != "Reanudar video"
    tab.pause_edit.setText("ctrl+alt+f10")
    assert tab.save_hotkeys()
    assert window.cfg.gestures.pause_hotkey == "Ctrl+Alt+F10"


def test_pausa_sin_sesion_avisa_y_el_indicador_se_oculta(window):
    window._on_gesture("pausa")
    assert "Inicia la cámara" in window.statusBar().currentMessage()
    assert window.paused_indicator.isHidden()
    window._set_running(True)
    window._set_running(False)
    assert window.paused_indicator.isHidden()


# --------------------------------------------------------------------------- #
# Grabar gestos y clips largos
# --------------------------------------------------------------------------- #


def test_el_selector_de_grabacion_incluye_gestos_y_largos(window):
    from camvoice.gestures import GESTURES

    rec = window.recorder_tab
    claves = [rec.kind_combo.itemData(i) for i in range(rec.kind_combo.count())]
    assert claves[:4] == ["speaking", "silent", "silent_extra", "speaking_extra"]
    assert {f"gesture:{g}" for g in GESTURES} <= set(claves)


def test_el_rango_de_duracion_depende_del_tipo(window):
    rec = window.recorder_tab
    combo = rec.kind_combo
    combo.setCurrentIndex(combo.findData("silent_extra"))
    assert rec.duration.slider.maximum() >= 120
    assert rec._current_plan().duration_s == pytest.approx(90)
    combo.setCurrentIndex(combo.findData("gesture:saludar"))
    assert rec.duration.slider.maximum() <= 6
    plan = rec._current_plan()
    assert plan.kind == "gesture" and plan.gesture == "saludar"
    combo.setCurrentIndex(combo.findData("speaking"))
    assert rec._current_plan().duration_s == pytest.approx(15)


def test_un_gesto_grabado_se_asigna_a_la_biblioteca(window, gesture_clip):
    from camvoice.video.library import load_library

    window._on_clip_recorded("gesture:sonreir", gesture_clip)
    assert load_library(window.cfg).library.gestures["sonreir"].clip == gesture_clip
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Gestos"


def test_un_clip_largo_grabado_se_anade_al_grupo(window, gesture_clip):
    from camvoice.video.library import load_library

    window._on_clip_recorded("silent_extra", gesture_clip)
    window._on_clip_recorded("speaking_extra", gesture_clip)
    lib = load_library(window.cfg).library
    assert lib.silent == [gesture_clip] and lib.speaking == [gesture_clip]
    assert window.pool_lists["silent"].count() == 1


def test_la_silueta_de_referencia_usa_el_clip_en_silencio(window, gesture_clip):
    import numpy as np

    rec = window.recorder_tab
    frame = np.zeros((48, 64, 3), np.uint8)
    assert rec.reference_frame() is None
    window.cfg.video.silent_clip = gesture_clip
    assert rec.reference_frame() is not None
    con = rec.preview_image(frame, guias=True)
    rec.ghost_check.setChecked(False)
    sin = rec.preview_image(frame, guias=True)
    assert con.mean() > sin.mean()
    # Grabando (sin guías) nunca se superpone nada.
    rec.ghost_check.setChecked(True)
    assert rec.preview_image(frame, guias=False) is frame
