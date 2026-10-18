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
        "Videos", "Grabar", "Micrófono", "Detector", "Efectos", "Salida",
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
