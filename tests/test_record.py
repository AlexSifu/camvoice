"""Pruebas del guion de grabación y del grabador.

El guion es datos puros, así que se verifica completo sin cámara: que las
señales cubran todo el clip sin huecos, que el medio segundo inicial de boca
cerrada esté siempre, y que el texto de lectura tenga la mezcla de sonidos que
se pretende.

Lo que necesita cámara física (`list_cameras`, `ClipRecorder`) se prueba solo en
lo comprobable sin hardware: errores limpios y exclusión de nuestra propia
cámara virtual.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from camvoice.record import (
    READING_TEXT,
    READING_TEXT_LONG,
    SILENT_PLAN,
    SPEAKING_PLAN,
    CameraInfo,
    ClipRecorder,
    Cue,
    RecordError,
    RecordingPlan,
    _is_virtual,
    draw_framing_guides,
)


# --------------------------------------------------------------------------- #
# Señales del guion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plan", [SPEAKING_PLAN, SILENT_PLAN])
def test_las_senales_cubren_el_clip_sin_huecos(plan):
    """En cualquier instante debe haber una señal que mostrar."""
    cues = plan.cues()
    assert cues[0].start_s == 0.0
    assert cues[-1].end_s == pytest.approx(plan.duration_s)
    for anterior, siguiente in zip(cues, cues[1:]):
        assert anterior.end_s == pytest.approx(siguiente.start_s)


@pytest.mark.parametrize("plan", [SPEAKING_PLAN, SILENT_PLAN])
def test_cue_at_responde_en_todo_el_rango(plan):
    for t in np.arange(0.0, plan.duration_s, 0.25):
        cue = plan.cue_at(float(t))
        assert isinstance(cue, Cue)
        assert cue.title


@pytest.mark.parametrize("plan", [SPEAKING_PLAN, SILENT_PLAN])
def test_la_primera_senal_pide_boca_cerrada(plan):
    """Es el fotograma al que se rebobina el clip de habla tras un silencio.

    Si en los primeros fotogramas la boca está a medio abrir, cada vez que
    empiece el habla la cara aparecerá con una mueca.
    """
    primera = plan.cues()[0]
    assert primera.start_s == 0.0
    assert "CERRADA" in primera.title.upper()
    assert primera.critical, "debe destacarse: fallar aquí arruina el clip"


def test_el_margen_inicial_cubre_la_transicion():
    """La transición dura 160 ms, unos 2,4 fotogramas a 15 FPS.

    El margen de boca cerrada tiene que durar más que eso para que los
    fotogramas que se ven durante el fundido tengan la boca cerrada.
    """
    transicion_s = 0.160
    for plan in (SPEAKING_PLAN, SILENT_PLAN):
        assert plan.lead_in_s > transicion_s * 2


def test_el_clip_de_habla_acaba_cerrando_la_boca():
    ultima = SPEAKING_PLAN.cues()[-1]
    assert "CIERRA" in ultima.title.upper()
    assert ultima.critical


def test_el_clip_de_silencio_no_pide_hablar():
    texto = " ".join(c.title + " " + c.detail for c in SILENT_PLAN.cues()).lower()
    assert "lee" not in texto
    assert "parpadea" in texto


def test_el_clip_de_silencio_avisa_de_la_reproduccion_inversa():
    """Con bucle de ida y vuelta, la mitad del tiempo se ve al revés."""
    assert any("revés" in c.detail or "cara" in c.detail for c in SILENT_PLAN.cues())


# --------------------------------------------------------------------------- #
# Duración
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("duracion", [8.0, 12.0, 15.0, 20.0, 25.0])
def test_cambiar_la_duracion_mantiene_el_guion_coherente(duracion):
    plan = SPEAKING_PLAN.with_duration(duracion)
    assert plan.duration_s == pytest.approx(duracion)
    cues = plan.cues()
    assert cues[0].start_s == 0.0
    assert cues[-1].end_s == pytest.approx(duracion)
    for anterior, siguiente in zip(cues, cues[1:]):
        assert anterior.end_s <= siguiente.start_s


def test_una_duracion_absurda_se_acota():
    assert SPEAKING_PLAN.with_duration(0.5).duration_s >= 4.0
    assert SPEAKING_PLAN.with_duration(9999).duration_s <= 60.0


def test_un_clip_muy_corto_no_deja_la_lectura_en_negativo():
    """Con 8 s, el margen inicial y la cola no deben solaparse."""
    plan = SPEAKING_PLAN.with_duration(8.0)
    lectura = plan.cues()[1]
    assert lectura.end_s >= lectura.start_s


def test_un_clip_largo_usa_el_texto_extendido():
    assert SPEAKING_PLAN.with_duration(20.0).reading_text == READING_TEXT_LONG
    assert SPEAKING_PLAN.with_duration(12.0).reading_text == READING_TEXT


def test_el_clip_de_silencio_no_tiene_texto_de_lectura():
    assert SILENT_PLAN.with_duration(20.0).reading_text == ""


# --------------------------------------------------------------------------- #
# El texto de lectura
# --------------------------------------------------------------------------- #


def palabras(texto: str) -> list[str]:
    return re.findall(r"[a-záéíóúñü]+", texto.lower())


def test_el_texto_dura_lo_que_tiene_que_durar():
    """Unas 30 palabras: a 150-180 palabras por minuto son 10-12 s, que es la
    ventana de lectura de un clip de 15 s."""
    n = len(palabras(READING_TEXT))
    assert 26 <= n <= 34, f"{n} palabras"


def test_el_texto_tiene_cierres_de_labios_repartidos():
    """p, b y m son el ~8 % de los sonidos del español y son la posición de
    boca más visible. Sin ellos la boca nunca se cerraría al 'hablar'."""
    bilabiales = sum(READING_TEXT.lower().count(c) for c in "pbm")
    assert bilabiales >= 10, f"solo {bilabiales} cierres bilabiales"


def test_el_texto_tiene_labiodentales():
    """La f (labio contra dientes) es una forma de boca distinta a las demás."""
    assert READING_TEXT.lower().count("f") >= 3


def test_el_texto_no_esta_dominado_por_vocales_redondeadas():
    """Grabar diciendo muchas «o» y «u» deja la boca redondeada casi todo el
    tiempo, y choca contra un habla real llena de «a» y «e»."""
    bajo = READING_TEXT.lower()
    abiertas = bajo.count("a") + bajo.count("e")
    redondeadas = bajo.count("o") + bajo.count("u")
    assert abiertas > redondeadas * 1.4, f"{abiertas} abiertas vs {redondeadas} redondeadas"


def test_el_texto_no_contiene_numeros():
    """El ritmo de contar es inconfundible al repetirse en bucle."""
    assert not re.search(r"\d|\buno\b|\bdos\b|\btres\b", READING_TEXT.lower())


# --------------------------------------------------------------------------- #
# Cámaras
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "nombre",
    ["CamVoice Virtual Camera", "Unity Video Capture", "OBS Virtual Camera",
     "camvoice virtual camera"],
)
def test_nuestras_camaras_virtuales_se_excluyen(nombre):
    """Grabar desde la cámara virtual propia crearía un bucle: la aplicación
    emitiría lo que está grabando de sí misma."""
    assert _is_virtual(nombre)


@pytest.mark.parametrize("nombre", ["Integrated Webcam", "Logitech C920", "Iriun Webcam"])
def test_una_camara_real_no_se_excluye(nombre):
    assert not _is_virtual(nombre)


def test_la_etiqueta_de_camara_es_informativa():
    cam = CameraInfo(0, "Integrated Webcam", 1280, 720, 30.0)
    etiqueta = cam.label
    assert "Integrated Webcam" in etiqueta
    assert "1280x720" in etiqueta
    assert "30" in etiqueta


# --------------------------------------------------------------------------- #
# Grabador
# --------------------------------------------------------------------------- #


def test_un_indice_de_camara_inexistente_da_un_error_legible(tmp_path):
    inexistente = CameraInfo(99, "Cámara fantasma", 640, 480, 30.0)
    grabador = ClipRecorder(
        inexistente, SPEAKING_PLAN.with_duration(4.0),
        tmp_path / "x.mp4", countdown_s=0.0,
    )
    grabador.start()
    grabador._thread.join(timeout=30)

    estado = grabador.status()
    assert estado.phase == "error"
    assert "fantasma" in estado.error
    assert "en uso" in estado.error, "el mensaje debe sugerir la causa probable"


def test_cancelar_antes_de_empezar_no_deja_hilos(tmp_path):
    grabador = ClipRecorder(
        CameraInfo(99, "Fantasma", 640, 480, 30.0),
        SPEAKING_PLAN, tmp_path / "x.mp4", countdown_s=5.0,
    )
    grabador.start()
    grabador.cancel()
    assert not grabador.running


def test_el_estado_inicial_es_inactivo(tmp_path):
    grabador = ClipRecorder(
        CameraInfo(0, "X", 640, 480, 30.0), SPEAKING_PLAN, tmp_path / "x.mp4"
    )
    estado = grabador.status()
    assert estado.phase == "idle"
    assert not estado.active
    assert estado.camera_label


# --------------------------------------------------------------------------- #
# Guías de encuadre
# --------------------------------------------------------------------------- #


def test_las_guias_no_modifican_el_fotograma_original():
    frame = np.full((240, 320, 3), 100, dtype=np.uint8)
    original = frame.copy()
    draw_framing_guides(frame)
    assert np.array_equal(frame, original)


def test_las_guias_dibujan_algo():
    frame = np.full((240, 320, 3), 100, dtype=np.uint8)
    salida = draw_framing_guides(frame)
    assert salida.shape == frame.shape
    assert not np.array_equal(salida, frame)


def test_las_guias_funcionan_en_cualquier_tamano():
    for alto, ancho in ((120, 160), (480, 640), (1080, 1920)):
        frame = np.zeros((alto, ancho, 3), dtype=np.uint8)
        assert draw_framing_guides(frame).shape == frame.shape


# --------------------------------------------------------------------------- #
# Deteccion de camaras virtuales por comportamiento
# --------------------------------------------------------------------------- #


class CapturaFalsa:
    """Imita lo justo de cv2.VideoCapture para probar la deteccion."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._i = 0

    def read(self):
        if self._i >= len(self._frames):
            return False, None
        frame = self._frames[self._i]
        self._i += 1
        return (frame is not None), frame


def test_una_fuente_congelada_se_detecta_como_virtual(monkeypatch):
    """Una camara virtual sin productor emite un fotograma de relleno fijo."""
    from camvoice import record

    monkeypatch.setattr(record, "STATIC_PROBE_DELAY_S", 0.0)
    fijo = np.full((48, 64, 3), 120, dtype=np.uint8)
    assert record._looks_static(CapturaFalsa([fijo, fijo.copy()]))


def test_un_sensor_real_no_se_detecta_como_virtual(monkeypatch):
    """Un sensor real nunca entrega dos fotogramas identicos: siempre hay ruido
    de lectura, incluso con la tapa puesta."""
    from camvoice import record

    monkeypatch.setattr(record, "STATIC_PROBE_DELAY_S", 0.0)
    rng = np.random.default_rng(5)
    base = np.full((48, 64, 3), 120, dtype=np.uint8)
    con_ruido = [
        np.clip(base + rng.integers(-2, 3, base.shape), 0, 255).astype(np.uint8)
        for _ in range(2)
    ]
    assert not record._looks_static(CapturaFalsa(con_ruido))


def test_una_fuente_que_no_entrega_nada_se_descarta(monkeypatch):
    from camvoice import record

    monkeypatch.setattr(record, "STATIC_PROBE_DELAY_S", 0.0)
    assert record._looks_static(CapturaFalsa([None]))
    assert record._looks_static(CapturaFalsa([]))


def test_un_solo_bit_distinto_basta_para_considerarla_real(monkeypatch):
    """La prueba es de igualdad exacta: no hay umbral que ajustar."""
    from camvoice import record

    monkeypatch.setattr(record, "STATIC_PROBE_DELAY_S", 0.0)
    a = np.full((48, 64, 3), 120, dtype=np.uint8)
    b = a.copy()
    b[0, 0, 0] = 121
    assert not record._looks_static(CapturaFalsa([a, b]))


def test_los_nombres_del_sistema_excluyen_las_virtuales():
    """La lista es solo para etiquetar, pero no debe sugerir una virtual."""
    from camvoice.record import physical_camera_names

    for nombre in physical_camera_names():
        assert not _is_virtual(nombre), f"se colo una virtual: {nombre}"


# --------------------------------------------------------------------------- #
# Cadencia escrita en el archivo
# --------------------------------------------------------------------------- #


def _grabador(tmp_path, fps_declarados=30.0):
    return ClipRecorder(
        CameraInfo(0, "X", 640, 480, fps_declarados),
        SPEAKING_PLAN, tmp_path / "x.mp4",
    )


def test_sin_medicion_se_usa_la_cadencia_declarada(tmp_path):
    assert _grabador(tmp_path, 30.0)._writer_fps() == pytest.approx(30.0)


def test_la_cadencia_medida_tiene_prioridad(tmp_path):
    """Una webcam que declara 30 FPS puede entregar 10 con poca luz.

    Escribir 30 en la cabecera hace que un clip de 5 s quede de 1,7 s y se
    reproduzca al triple de velocidad: el paso de preparacion remuestrea usando
    los FPS declarados, asi que una cabecera mentirosa llega hasta la salida.
    Medido en este equipo antes de corregirlo.
    """
    g = _grabador(tmp_path, 30.0)
    g._measured_input_fps = 10.1
    assert g._writer_fps() == pytest.approx(10.1)


@pytest.mark.parametrize("absurda", [0.0, 0.4, 500.0, -5.0])
def test_una_medicion_absurda_se_descarta(tmp_path, absurda):
    """Si la medicion sale disparatada es mejor el valor declarado que un
    archivo con una cabecera imposible."""
    g = _grabador(tmp_path, 30.0)
    g._measured_input_fps = absurda
    assert g._writer_fps() == pytest.approx(30.0)


# --------------------------------------------------------------------------- #
# El guion sale por frases
# --------------------------------------------------------------------------- #


def test_el_texto_se_parte_por_la_puntuacion():
    frases = __import__("camvoice.record", fromlist=["x"]).split_phrases(READING_TEXT)
    assert len(frases) >= 3
    assert all(f.strip() for f in frases)
    # Nada se pierde por el camino.
    assert sum(len(f.split()) for f in frases) == len(READING_TEXT.split())


def test_las_frases_cortas_no_cruzan_un_punto():
    """Unir el final de una oracion con el principio de la siguiente produce
    frases que se leen mal: «...la fecha final. Si te parece,»."""
    from camvoice.record import split_phrases

    for frase in split_phrases(READING_TEXT_LONG):
        interior = frase[:-1] if frase.endswith(".") else frase
        assert "." not in interior, f"una frase cruza un punto: {frase!r}"


def test_un_fragmento_de_tres_palabras_no_tiene_turno_propio():
    from camvoice.record import split_phrases

    frases = split_phrases("Primera frase bastante larga de prueba, si, y otra mas larga aun.")
    assert all(len(f.split()) >= 4 for f in frases)


def test_el_guion_de_habla_sale_en_varias_frases():
    """Mostrar el parrafo entero de golpe obliga a buscar por donde se iba."""
    frases = SPEAKING_PLAN.reading_phrases()
    assert len(frases) >= 3
    assert all(frases)


def test_cada_frase_tiene_tiempo_suficiente_para_leerse():
    from camvoice.record import MIN_SEGMENT_S

    for duracion in (8.0, 12.0, 15.0, 20.0, 25.0):
        plan = SPEAKING_PLAN.with_duration(duracion)
        lecturas = [c for c in plan.cues() if c.title.startswith("Lee en voz alta")]
        for cue in lecturas:
            ventana = cue.end_s - cue.start_s
            assert ventana >= MIN_SEGMENT_S * 0.9, (
                f"{duracion}s: la frase {cue.detail[:30]!r} solo dura {ventana:.1f}s"
            )


def test_con_poco_tiempo_hay_menos_frases_pero_legibles():
    """Mejor leer dos frases con calma que ver seis pasar sin poder seguirlas."""
    cortas = len(SPEAKING_PLAN.with_duration(8.0).reading_phrases())
    largas = len(SPEAKING_PLAN.with_duration(20.0).reading_phrases())
    assert cortas < largas


def test_el_tiempo_de_cada_frase_es_proporcional_a_su_longitud():
    plan = SPEAKING_PLAN.with_duration(15.0)
    lecturas = [c for c in plan.cues() if c.title.startswith("Lee en voz alta")]
    mas_larga = max(lecturas, key=lambda c: len(c.detail))
    mas_corta = min(lecturas, key=lambda c: len(c.detail))
    assert (mas_larga.end_s - mas_larga.start_s) > (mas_corta.end_s - mas_corta.start_s)


def test_las_frases_se_numeran_para_saber_cuantas_quedan():
    plan = SPEAKING_PLAN.with_duration(15.0)
    titulos = [c.title for c in plan.cues() if c.title.startswith("Lee en voz alta")]
    assert "1 de" in titulos[0]
    assert str(len(titulos)) in titulos[-1]


def test_el_clip_de_silencio_no_se_parte_en_frases():
    assert SILENT_PLAN.reading_phrases() == []


def test_la_siguiente_senal_permite_anticiparla():
    plan = SPEAKING_PLAN.with_duration(15.0)
    primera = plan.cues()[0]
    siguiente = plan.next_cue_at(primera.start_s + 0.1)
    assert siguiente is not None
    assert siguiente.title.startswith("Lee en voz alta")


def test_al_final_no_hay_siguiente():
    plan = SPEAKING_PLAN.with_duration(15.0)
    assert plan.next_cue_at(plan.duration_s - 0.01) is None


def test_un_texto_vacio_no_rompe_el_guion():
    from camvoice.record import split_phrases

    assert split_phrases("") == []
    assert split_phrases("   ") == []
    vacio = SPEAKING_PLAN.with_duration(15.0).__class__(
        name="X", kind="speaking", duration_s=10.0, lead_in_s=0.6,
        tail_s=1.0, reading_text="",
    )
    cues = vacio.cues()
    assert cues[0].start_s == 0.0
    assert cues[-1].end_s == pytest.approx(10.0)
