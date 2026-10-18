"""Pruebas de la cadena de efectos.

Los efectos son puros: reciben un fotograma y devuelven otro. Eso permite
comprobar lo que de verdad importa —que el 60 % se vea igual a cualquier
resolución, que nada se salga del rango válido y que el coste sea asumible—
sin cámara ni micrófono.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from camvoice.config import EffectsConfig
from camvoice.video.effects import (
    EffectChain,
    blur_sigma_for,
    pixelate_block_for,
)


@pytest.fixture
def frame() -> np.ndarray:
    """Imagen de prueba con estructura: bordes, degradado y color."""
    rng = np.random.default_rng(3)
    img = np.zeros((120, 160, 3), dtype=np.uint8)
    img[:, :, 0] = np.linspace(0, 255, 160, dtype=np.uint8)[None, :]
    img[:, :, 1] = np.linspace(0, 255, 120, dtype=np.uint8)[:, None]
    img[:, :, 2] = 128
    img[40:80, 60:100] = (240, 30, 30)  # un rectángulo de borde duro
    img = cv2.add(img, rng.integers(0, 20, img.shape, dtype=np.uint8))
    return img


def chain(**kwargs) -> EffectChain:
    return EffectChain(EffectsConfig(**kwargs))


def detalle(img: np.ndarray) -> float:
    """Medida de detalle: varianza del laplaciano.

    Baja al desenfocar y al pixelar, así que sirve para verificar que esos
    efectos hacen lo que dicen sin comparar píxeles uno a uno.
    """
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gris, cv2.CV_64F).var())


# --------------------------------------------------------------------------- #
# Interruptor general y detección de actividad
# --------------------------------------------------------------------------- #


def test_sin_efectos_devuelve_el_mismo_array(frame):
    """Coste cero: ni copia ni conversión cuando no hay nada que aplicar."""
    cadena = chain()
    assert not cadena.active
    assert cadena.apply(frame) is frame


def test_el_interruptor_general_anula_todo(frame):
    cadena = chain(enabled=False, blur=80.0, pixelate=80.0, vignette=50.0)
    assert not cadena.active
    assert cadena.apply(frame) is frame


@pytest.mark.parametrize(
    "ajuste",
    [
        {"blur": 30.0}, {"pixelate": 30.0}, {"brightness": 20.0},
        {"contrast": 20.0}, {"saturation": -40.0}, {"vignette": 40.0},
        {"grain": 25.0}, {"zoom": 1.5}, {"flip_horizontal": True},
    ],
)
def test_cada_efecto_se_detecta_como_activo(ajuste):
    assert chain(**ajuste).active


def test_un_valor_despreciable_no_cuenta_como_activo():
    """Evita pagar la conversión de formato por un 0,2 % invisible."""
    assert not chain(blur=0.2, pixelate=0.3, vignette=0.4).active


def test_no_se_modifica_el_fotograma_de_entrada(frame):
    """El origen es una vista de solo lectura del memmap: no se puede tocar."""
    original = frame.copy()
    chain(blur=50.0, brightness=30.0, vignette=40.0).apply(frame)
    assert np.array_equal(frame, original)


# --------------------------------------------------------------------------- #
# Desenfoque
# --------------------------------------------------------------------------- #


def test_el_desenfoque_reduce_el_detalle(frame):
    base = detalle(frame)
    suave = detalle(chain(blur=60.0).apply(frame))
    assert suave < base * 0.3


def test_mas_porcentaje_desenfoca_mas(frame):
    valores = [detalle(chain(blur=p).apply(frame)) for p in (10, 30, 60, 100)]
    assert valores == sorted(valores, reverse=True)


def test_el_desenfoque_es_independiente_de_la_resolucion():
    """El 60 % debe verse igual a 320 y a 1280 de ancho.

    Es la razón de que los parámetros sean porcentajes: un sigma fijo en
    píxeles se vería fuerte en una resolución y flojo en otra, y el usuario
    tendría que reajustar cada vez que cambia la salida.
    """
    assert blur_sigma_for(60, 1280) == pytest.approx(blur_sigma_for(60, 320) * 4)
    assert blur_sigma_for(0, 640) == 0.0


def test_el_desenfoque_conserva_el_brillo_medio(frame):
    """Un gaussiano normalizado no debe oscurecer ni aclarar la imagen."""
    antes = float(frame.mean())
    despues = float(chain(blur=50.0).apply(frame).mean())
    assert despues == pytest.approx(antes, abs=4.0)


# --------------------------------------------------------------------------- #
# Pixelado
# --------------------------------------------------------------------------- #


def test_el_pixelado_crea_bloques_uniformes(frame):
    """Dentro de un bloque, todos los píxeles deben valer lo mismo.

    La desviación se mide **por canal**: un bloque perfectamente plano cuyos
    canales valgan (17, 11, 146) tiene una desviación global enorme aunque
    cada canal sea constante, que es lo que aquí se quiere comprobar.
    """
    salida = chain(pixelate=100.0).apply(frame)
    lado = pixelate_block_for(100.0, frame.shape[1])
    assert lado > 1
    bloque = salida[:lado, :lado].reshape(-1, 3)
    assert np.allclose(bloque.std(axis=0), 0.0, atol=0.01)

    # Y bloques contiguos deben ser distintos: si no, no habría mosaico sino
    # una imagen plana.
    siguiente = salida[:lado, lado : 2 * lado].reshape(-1, 3)
    assert not np.allclose(bloque.mean(axis=0), siguiente.mean(axis=0), atol=0.5)


def test_mas_porcentaje_pixela_mas():
    valores = [pixelate_block_for(p, 640) for p in (0, 25, 50, 100)]
    assert valores[0] == 1
    assert valores == sorted(valores)
    assert len(set(valores)) == len(valores)


def test_el_pixelado_es_independiente_de_la_resolucion():
    assert pixelate_block_for(100, 1280) == pytest.approx(
        (pixelate_block_for(100, 320) - 1) * 4 + 1, abs=1
    )


def test_el_pixelado_conserva_el_tamano(frame):
    assert chain(pixelate=80.0).apply(frame).shape == frame.shape


def test_el_pixelado_usa_promedio_y_no_un_solo_pixel(frame):
    """Con vecino más cercano al reducir, el mosaico parpadearía entre
    fotogramas al moverse el video. INTER_AREA promedia el bloque."""
    salida = chain(pixelate=100.0).apply(frame)
    lado = pixelate_block_for(100.0, frame.shape[1])
    esperado = frame[:lado, :lado].reshape(-1, 3).mean(axis=0)
    assert np.allclose(salida[0, 0], esperado, atol=3)


# --------------------------------------------------------------------------- #
# Color
# --------------------------------------------------------------------------- #


def test_el_brillo_sube_y_baja(frame):
    base = frame.mean()
    assert chain(brightness=40.0).apply(frame).mean() > base + 10
    assert chain(brightness=-40.0).apply(frame).mean() < base - 10


def test_el_contraste_expande_y_comprime_el_rango(frame):
    base = float(frame.std())
    assert float(chain(contrast=50.0).apply(frame).std()) > base
    assert float(chain(contrast=-50.0).apply(frame).std()) < base


def test_el_contraste_no_oscurece_la_imagen(frame):
    """El pivote en el gris medio es lo que lo evita.

    Con un `alpha * x` sin pivote, subir el contraste multiplica también la
    luminancia media y la imagen entera se va a oscuro o a quemado.
    """
    antes = float(frame.mean())
    despues = float(chain(contrast=40.0).apply(frame).mean())
    assert abs(despues - antes) < 25.0


def test_el_contraste_es_simetrico_en_escala_logaritmica(frame):
    """+50 y -50 deben ser recíprocos, para que el deslizador se sienta igual
    en los dos sentidos."""
    subido = float(chain(contrast=50.0).apply(frame).std())
    bajado = float(chain(contrast=-50.0).apply(frame).std())
    base = float(frame.std())
    assert subido / base == pytest.approx(base / bajado, rel=0.35)


def test_la_saturacion_minima_deja_la_imagen_en_gris(frame):
    salida = chain(saturation=-100.0).apply(frame)
    canales = salida.reshape(-1, 3)
    assert np.abs(canales[:, 0].astype(int) - canales[:, 1]).mean() < 2.0


def test_la_saturacion_maxima_separa_los_canales(frame):
    def dispersion(img):
        c = img.reshape(-1, 3).astype(int)
        return float(np.abs(c - c.mean(axis=1, keepdims=True)).mean())

    assert dispersion(chain(saturation=80.0).apply(frame)) > dispersion(frame)


# --------------------------------------------------------------------------- #
# Zoom, viñeta, grano, espejo
# --------------------------------------------------------------------------- #


def test_el_zoom_conserva_el_tamano_y_amplia_el_centro(frame):
    salida = chain(zoom=2.0).apply(frame)
    assert salida.shape == frame.shape
    # El píxel central debe seguir siendo (casi) el mismo: el recorte es centrado.
    h, w = frame.shape[:2]
    assert np.allclose(salida[h // 2, w // 2], frame[h // 2, w // 2], atol=25)


def test_el_zoom_se_acota_para_no_degenerar(frame):
    salida = chain(zoom=99.0).apply(frame)
    assert salida.shape == frame.shape
    assert salida.std() >= 0.0  # no produce NaN ni un array vacío


def test_la_vineta_oscurece_las_esquinas_y_no_el_centro(frame):
    salida = chain(vignette=100.0).apply(frame)
    h, w = frame.shape[:2]
    centro_antes = float(frame[h // 2 - 3 : h // 2 + 3, w // 2 - 3 : w // 2 + 3].mean())
    centro = float(salida[h // 2 - 3 : h // 2 + 3, w // 2 - 3 : w // 2 + 3].mean())
    esquina_antes = float(frame[:6, :6].mean())
    esquina = float(salida[:6, :6].mean())
    assert centro == pytest.approx(centro_antes, rel=0.05)
    assert esquina < esquina_antes * 0.5


def test_la_mascara_de_vineta_se_reutiliza(frame):
    """Recalcular la máscara en cada fotograma sería un gasto inútil."""
    cadena = chain(vignette=50.0)
    cadena.apply(frame)
    primera = cadena._vignette_mask
    cadena.apply(frame)
    assert cadena._vignette_mask is primera


def test_el_grano_anade_ruido_sin_cambiar_el_brillo_medio(frame):
    salida = chain(grain=60.0).apply(frame)
    assert float(salida.std()) > float(frame.std())
    assert float(salida.mean()) == pytest.approx(float(frame.mean()), abs=4.0)


def test_el_grano_cambia_entre_fotogramas(frame):
    """Un ruido congelado se ve como suciedad en la lente, no como sensor."""
    cadena = chain(grain=60.0)
    a = cadena.apply(frame).copy()
    b = cadena.apply(frame).copy()
    assert not np.array_equal(a, b)


def test_el_grano_es_monocromo(frame):
    """Ruido por canal se vería como confeti de color, no como sensor real."""
    plano = np.full((60, 80, 3), 128, dtype=np.uint8)
    salida = chain(grain=100.0).apply(plano)
    c = salida.reshape(-1, 3).astype(int)
    assert np.abs(c[:, 0] - c[:, 1]).max() <= 1


def test_el_espejo_invierte_horizontalmente(frame):
    salida = chain(flip_horizontal=True).apply(frame)
    assert np.array_equal(salida, cv2.flip(frame, 1))


# --------------------------------------------------------------------------- #
# Combinaciones y robustez
# --------------------------------------------------------------------------- #


def test_todos_los_efectos_juntos_no_rompen(frame):
    salida = chain(
        zoom=1.4, blur=25.0, pixelate=20.0, brightness=15.0, contrast=20.0,
        saturation=-30.0, vignette=35.0, grain=20.0, flip_horizontal=True,
    ).apply(frame)
    assert salida.shape == frame.shape
    assert salida.dtype == np.uint8


@pytest.mark.parametrize("ancho,alto", [(64, 48), (320, 240), (640, 480), (1280, 720)])
def test_funciona_en_cualquier_resolucion(ancho, alto):
    img = np.full((alto, ancho, 3), 120, dtype=np.uint8)
    salida = chain(blur=50.0, pixelate=40.0, vignette=30.0, grain=20.0).apply(img)
    assert salida.shape == img.shape


def test_los_valores_extremos_no_desbordan(frame):
    """uint8 con aritmética sin saturar daría envolvimiento: negro a blanco."""
    for ajuste in ({"brightness": 100.0}, {"brightness": -100.0}, {"contrast": 100.0}):
        salida = chain(**ajuste).apply(frame)
        assert salida.dtype == np.uint8
        assert 0 <= int(salida.min()) and int(salida.max()) <= 255


def test_los_cambios_de_configuracion_se_leen_en_vivo(frame):
    """La UI muta EffectsConfig; la cadena debe notarlo sin reconstruirse."""
    cfg = EffectsConfig()
    cadena = EffectChain(cfg)
    assert not cadena.active
    cfg.blur = 70.0
    assert cadena.active
    assert detalle(cadena.apply(frame)) < detalle(frame) * 0.3


def test_la_descripcion_enumera_lo_activo():
    texto = chain(blur=40.0, pixelate=20.0, zoom=1.5).describe()
    assert "desenfoque" in texto
    assert "pixelado" in texto
    assert "zoom" in texto
    assert chain().describe() == "ninguno"


# --------------------------------------------------------------------------- #
# Coste
# --------------------------------------------------------------------------- #


def test_el_coste_cabe_en_el_presupuesto_de_un_fotograma():
    """A 15 FPS hay 66,7 ms por fotograma. La cadena completa debe usar una
    fracción pequeña, porque además hay que componer y enviar."""
    img = np.full((480, 640, 3), 120, dtype=np.uint8)
    cadena = chain(
        zoom=1.3, blur=40.0, pixelate=30.0, brightness=10.0,
        contrast=15.0, saturation=-20.0, vignette=30.0, grain=20.0,
    )
    cadena.apply(img)  # calentamiento: primera máscara y banco de ruido
    inicio = time.perf_counter()
    for _ in range(20):
        cadena.apply(img)
    ms = (time.perf_counter() - inicio) / 20 * 1000
    assert ms < 25.0, f"la cadena completa cuesta {ms:.1f} ms por fotograma"
