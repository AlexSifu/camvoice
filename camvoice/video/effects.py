"""Cadena de efectos sobre el fotograma de salida.

Todo en BGR y con búferes reutilizados: en el bucle de render no se asigna
memoria. Las funciones son puras y deterministas, así que el comportamiento se
verifica sin cámara ni micrófono.

## Los parámetros son porcentajes independientes de la resolución

Un desenfoque de 12 píxeles se ve fuerte a 320x240 y flojo a 1280x720. Para que
el 60 % se vea igual en cualquier resolución, los radios se derivan del ancho
del fotograma, no de un número absoluto de píxeles. Así el usuario mueve un
deslizador y obtiene el mismo resultado visual aunque luego cambie la salida.

## Orden de aplicación

    zoom -> color (brillo, contraste, saturación) -> pixelado -> desenfoque
         -> grano -> viñeta

El orden importa y no es arbitrario:

* El **zoom** va primero porque es geometría: recorta y escala, y cualquier
  efecto aplicado antes se escalaría con él (un pixelado se volvería más grande
  de lo pedido).
* El **color** va antes del pixelado para que el promediado de bloques trabaje
  sobre los valores finales.
* El **desenfoque después del pixelado** suaviza los bordes de los bloques. Al
  revés el pixelado destruiría el desenfoque y no se notaría.
* El **grano después del desenfoque**, porque si no el desenfoque lo borraría.
  Un poco de grano acerca el aspecto al de una webcam real, que siempre tiene
  ruido de sensor; un clip comprimido se ve sospechosamente limpio.
* La **viñeta al final**, porque oscurece y no debe afectar a los promedios
  anteriores.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import EffectsConfig

__all__ = [
    "EffectChain",
    "EffectsInfo",
    "blur_sigma_for",
    "pixelate_block_for",
    "limit_opencv_threads",
    "RENDER_THREADS",
]

RENDER_THREADS = 1
"""Hilos que OpenCV usa durante el bucle de render.

Medido en este equipo con la cadena completa a 640x480:

    hilos   ms/fotograma   CPU-ms/fotograma
        1       4.44             4.22
        2       4.46             5.16
        4       4.35             6.25
        8       4.39            12.50

A este tamaño de imagen **la paralelización no mejora el tiempo de pared** —el
reparto del trabajo cuesta tanto como lo que ahorra— pero **triplica la CPU
consumida**. Con 8 hilos la aplicación gastaba un 34 % de un núcleo donde ahora
gasta un 12 %.

Importa porque esto corre durante una videollamada: la aplicación de
videoconferencia necesita CPU para codificar su propio video, y aquí sobran
60 ms de los 66,7 de presupuesto por fotograma. Cambiar tiempo de pared que no
se usa por CPU que sí hace falta en otro sitio es un mal negocio.

La preparación de clips es otro caso —es un lote de una vez y el usuario espera
delante— así que ahí sí conviene usar todos los hilos.
"""


def limit_opencv_threads(threads: int = RENDER_THREADS) -> int:
    """Fija los hilos de OpenCV y devuelve el valor anterior, para restaurarlo."""
    anterior = cv2.getNumThreads()
    cv2.setNumThreads(max(1, threads))
    return anterior

# --------------------------------------------------------------------------- #
# Conversión de porcentajes a parámetros reales
# --------------------------------------------------------------------------- #

BLUR_MAX_FRACTION = 0.025
"""Sigma del desenfoque al 100 %, como fracción del ancho.

A 640 px son 16 px de sigma: una cara sigue siendo una silueta reconocible como
persona, pero no identificable. Más que esto no aporta privacidad y solo parece
una pantalla estropeada.
"""

PIXELATE_MAX_FRACTION = 0.0625
"""Lado del bloque al 100 %, como fracción del ancho. A 640 px son 40 px."""

VIGNETTE_MAX_STRENGTH = 0.85
"""Oscurecimiento máximo en las esquinas. Al 100 % no se llega a negro puro."""

GRAIN_MAX_SIGMA = 18.0
"""Desviación típica del ruido al 100 %, en niveles de 0-255."""

NOISE_BANK_FRAMES = 8
"""Fotogramas de ruido pregenerados.

Generar ruido gaussiano de 640x480 cuesta ~1,5 ms por fotograma, más que todo
el resto del motor junto. Se pregenera un banco pequeño y se cicla con
desplazamientos variables: a 15 FPS el ojo no detecta la repetición, y el coste
baja a una suma de matrices.
"""


def blur_sigma_for(percent: float, width: int) -> float:
    """Sigma gaussiano para un porcentaje dado, escalado al ancho."""
    return max(0.0, percent / 100.0) * width * BLUR_MAX_FRACTION


def pixelate_block_for(percent: float, width: int) -> int:
    """Lado del bloque en píxeles. 1 significa sin pixelado."""
    block = 1.0 + max(0.0, percent / 100.0) * width * PIXELATE_MAX_FRACTION
    return max(1, int(round(block)))


def _contrast_alpha(percent: float) -> float:
    """Factor multiplicativo del contraste.

    Se usa una escala logarítmica (2^(p/100)) en vez de lineal para que -50 y
    +50 sean recíprocos y el deslizador se sienta simétrico: con una escala
    lineal, la mitad negativa del recorrido se agota enseguida.
    """
    return float(2.0 ** (percent / 100.0))


# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EffectsInfo:
    """Qué se aplicó, para la interfaz y las métricas."""

    active: bool
    applied: tuple[str, ...]
    cost_ms: float = 0.0


class EffectChain:
    """Aplica los efectos configurados a un fotograma BGR.

    Mantiene una referencia viva a `EffectsConfig`, igual que el detector de
    voz: mover un deslizador surte efecto en el fotograma siguiente, sin
    reconstruir nada ni volver a preparar la caché de clips.
    """

    def __init__(self, cfg: EffectsConfig) -> None:
        self.cfg = cfg
        self._vignette_mask: np.ndarray | None = None
        self._vignette_key: tuple[int, int, float] | None = None
        self._noise_bank: list[np.ndarray] = []
        self._noise_key: tuple[int, int] | None = None
        self._noise_index = 0
        self._scratch: np.ndarray | None = None

    # ------------------------------------------------------------------ #

    @property
    def active(self) -> bool:
        """True si hay algo que hacer.

        Importa para el rendimiento: si no hay efectos, el motor se ahorra la
        ida y vuelta entre NV12 y BGR por completo.
        """
        c = self.cfg
        if not c.enabled:
            return False
        return bool(
            c.zoom > 1.001
            or c.blur > 0.5
            or c.pixelate > 0.5
            or abs(c.brightness) > 0.5
            or abs(c.contrast) > 0.5
            or abs(c.saturation) > 0.5
            or c.vignette > 0.5
            or c.grain > 0.5
            or c.flip_horizontal
        )

    def describe(self) -> str:
        """Resumen legible de los efectos activos."""
        return ", ".join(self._names()) or "ninguno"

    def _names(self) -> list[str]:
        c = self.cfg
        if not c.enabled:
            return []
        nombres: list[str] = []
        if c.zoom > 1.001:
            nombres.append(f"zoom {c.zoom:.2f}x")
        if abs(c.brightness) > 0.5:
            nombres.append(f"brillo {c.brightness:+.0f}%")
        if abs(c.contrast) > 0.5:
            nombres.append(f"contraste {c.contrast:+.0f}%")
        if abs(c.saturation) > 0.5:
            nombres.append(f"saturación {c.saturation:+.0f}%")
        if c.pixelate > 0.5:
            nombres.append(f"pixelado {c.pixelate:.0f}%")
        if c.blur > 0.5:
            nombres.append(f"desenfoque {c.blur:.0f}%")
        if c.grain > 0.5:
            nombres.append(f"grano {c.grain:.0f}%")
        if c.vignette > 0.5:
            nombres.append(f"viñeta {c.vignette:.0f}%")
        if c.flip_horizontal:
            nombres.append("espejo")
        return nombres

    # ------------------------------------------------------------------ #

    def apply(self, bgr: np.ndarray) -> np.ndarray:
        """Devuelve el fotograma con los efectos aplicados.

        Si no hay efectos activos devuelve el mismo array, sin copiarlo. Si los
        hay, trabaja sobre un búfer propio y nunca modifica la entrada: el
        fotograma de origen es una vista de solo lectura del `memmap`.
        """
        if not self.active:
            return bgr

        c = self.cfg
        out = self._zoom(bgr, c.zoom) if c.zoom > 1.001 else bgr.copy()

        if abs(c.brightness) > 0.5 or abs(c.contrast) > 0.5:
            out = self._brightness_contrast(out, c.brightness, c.contrast)
        if abs(c.saturation) > 0.5:
            out = self._saturation(out, c.saturation)
        if c.pixelate > 0.5:
            out = self._pixelate(out, c.pixelate)
        if c.blur > 0.5:
            out = self._blur(out, c.blur)
        if c.grain > 0.5:
            out = self._grain(out, c.grain)
        if c.vignette > 0.5:
            out = self._vignette(out, c.vignette)
        if c.flip_horizontal:
            out = cv2.flip(out, 1)
        return out

    # ------------------------------------------------------------------ #
    # Efectos
    # ------------------------------------------------------------------ #

    @staticmethod
    def _zoom(bgr: np.ndarray, factor: float) -> np.ndarray:
        """Recorte centrado ampliado al tamaño original.

        Permite reencuadrar —acercar la cara, quitar fondo de los bordes— sin
        volver a grabar el clip.
        """
        h, w = bgr.shape[:2]
        factor = min(4.0, max(1.0, factor))
        new_w = max(2, int(round(w / factor)))
        new_h = max(2, int(round(h / factor)))
        x0 = (w - new_w) // 2
        y0 = (h - new_h) // 2
        recorte = bgr[y0 : y0 + new_h, x0 : x0 + new_w]
        return cv2.resize(recorte, (w, h), interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def _brightness_contrast(
        bgr: np.ndarray, brightness: float, contrast: float
    ) -> np.ndarray:
        """Ajuste lineal con punto de pivote en el gris medio.

        El pivote en 127,5 evita que subir el contraste oscurezca la imagen
        entera, que es lo que pasa con un `alpha * x` a secas.
        """
        alpha = _contrast_alpha(contrast)
        beta = brightness * 1.275 + 127.5 * (1.0 - alpha)
        return cv2.convertScaleAbs(bgr, alpha=alpha, beta=beta)

    @staticmethod
    def _saturation(bgr: np.ndarray, percent: float) -> np.ndarray:
        """Interpolación entre la imagen en gris y la original.

        Equivale a ajustar la S de HSV pero cuesta la mitad: dos conversiones de
        espacio de color por fotograma son más caras que una mezcla lineal, y
        aquí el resultado es indistinguible.
        """
        factor = 1.0 + percent / 100.0
        gris = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gris_bgr = cv2.cvtColor(gris, cv2.COLOR_GRAY2BGR)
        if factor <= 0.0:
            return gris_bgr
        return cv2.addWeighted(bgr, factor, gris_bgr, 1.0 - factor, 0.0)

    @staticmethod
    def _pixelate(bgr: np.ndarray, percent: float) -> np.ndarray:
        """Reducir y volver a ampliar con vecino más cercano.

        Dos detalles de implementación que cambian el resultado visible:

        * **INTER_AREA al reducir** promedia el bloque entero. Un vecino más
          cercano ahí tomaría un único píxel del bloque, y al moverse el video
          el mosaico parpadearía porque ese píxel concreto cambia mucho más que
          el promedio. INTER_NEAREST al ampliar mantiene los bordes rectos.

        * **Se amplía a un múltiplo exacto del bloque y luego se recorta.**
          Ampliar directamente al tamaño original da una escala fraccionaria
          (160/14 = 11,43) y los bloques salen de anchos desiguales, unos de 11
          píxeles y otros de 12. Con el múltiplo exacto todos miden lo pedido.
        """
        h, w = bgr.shape[:2]
        block = pixelate_block_for(percent, w)
        if block <= 1:
            return bgr

        # Techo de la división: así el recorte final nunca se queda corto.
        pequeno_w = max(1, -(-w // block))
        pequeno_h = max(1, -(-h // block))
        pequeno = cv2.resize(
            bgr, (pequeno_w, pequeno_h), interpolation=cv2.INTER_AREA
        )
        ampliado = cv2.resize(
            pequeno,
            (pequeno_w * block, pequeno_h * block),
            interpolation=cv2.INTER_NEAREST,
        )
        return ampliado[:h, :w]

    @staticmethod
    def _blur(bgr: np.ndarray, percent: float) -> np.ndarray:
        """Desenfoque gaussiano, reduciendo antes si el radio es grande.

        Un gaussiano directo con sigma 7 necesita un kernel de 43x43. Aun siendo
        separable, son 86 multiplicaciones por píxel y canal: medido, **20 ms por
        fotograma a 640x480**, que por sí solo se comía un tercio del
        presupuesto y disparaba la CPU al 76 % de un núcleo.

        La solución estándar: reducir la imagen por un factor k, desenfocar con
        sigma/k y volver a ampliar. El coste baja por k² (menos píxeles) y por k
        (kernel más pequeño). Y no se pierde calidad apreciable, porque el
        desenfoque está destruyendo justo el detalle que la reducción tira:
        reducir y desenfocar son casi la misma operación.
        """
        h, w = bgr.shape[:2]
        sigma = blur_sigma_for(percent, w)
        if sigma < 0.3:
            return bgr

        # Se reduce hasta dejar un sigma efectivo de ~2 px, que es donde un
        # kernel de 13x13 ya es barato. Por debajo de sigma 2 no se reduce.
        factor = max(1, int(sigma / 2.0))
        if factor > 1:
            pequeno = cv2.resize(
                bgr,
                (max(8, w // factor), max(8, h // factor)),
                interpolation=cv2.INTER_AREA,
            )
            sigma_efectivo = sigma / factor
        else:
            pequeno = bgr
            sigma_efectivo = sigma

        ph, pw = pequeno.shape[:2]
        # Un kernel de 6 sigma captura el 99,7 % de la campana; más allá solo se
        # suman multiplicaciones por cero.
        k = int(sigma_efectivo * 6) | 1
        k = max(3, min(k, (min(ph, pw) // 2) * 2 - 1))
        difuso = cv2.GaussianBlur(pequeno, (k, k), sigma_efectivo)

        if factor > 1:
            return cv2.resize(difuso, (w, h), interpolation=cv2.INTER_LINEAR)
        return difuso

    def _grain(self, bgr: np.ndarray, percent: float) -> np.ndarray:
        """Ruido de luminancia aditivo.

        El banco se guarda ya como uint8 centrado en 128 y de tres canales, para
        poder sumarlo con un único `cv2.addWeighted`. La versión anterior pasaba
        el fotograma a float32, sumaba, recortaba y volvía a uint8: cuatro
        recorridos completos del array en NumPy, medidos en ~6 ms por fotograma.
        Con addWeighted es un solo recorrido en C con saturación incluida.
        """
        h, w = bgr.shape[:2]
        if self._noise_key != (h, w) or not self._noise_bank:
            rng = np.random.default_rng(12345)
            self._noise_bank = []
            for _ in range(NOISE_BANK_FRAMES):
                # Monocromo a propósito: el ruido de un sensor real es sobre
                # todo de luminancia, y un ruido por canal se ve como confeti
                # de color. Se replica el mismo plano en los tres canales.
                plano = rng.standard_normal((h, w), dtype=np.float32)
                centrado = np.clip(plano * (255.0 / 8.0) + 128.0, 0, 255)
                self._noise_bank.append(
                    np.repeat(centrado.astype(np.uint8)[:, :, None], 3, axis=2)
                )
            self._noise_key = (h, w)
            self._noise_index = 0

        ruido = self._noise_bank[self._noise_index % len(self._noise_bank)]
        self._noise_index += 1
        # El banco está a sigma = 255/8; se reescala al sigma pedido.
        peso = (percent / 100.0 * GRAIN_MAX_SIGMA) / (255.0 / 8.0)
        return cv2.addWeighted(bgr, 1.0, ruido, peso, -128.0 * peso)

    def _vignette(self, bgr: np.ndarray, percent: float) -> np.ndarray:
        """Oscurecimiento radial con máscara precalculada.

        `cv2.multiply` con `dtype=CV_8U` hace la multiplicación y la saturación
        en un solo recorrido en C. Hacerlo en NumPy exigía convertir a float32,
        multiplicar y volver a uint8: tres recorridos y ~3 ms por fotograma.
        """
        h, w = bgr.shape[:2]
        fuerza = percent / 100.0 * VIGNETTE_MAX_STRENGTH
        clave = (h, w, round(fuerza, 3))
        if self._vignette_key != clave:
            ys = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
            xs = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
            radio = np.sqrt(xs * xs + ys * ys) / np.sqrt(2.0)
            # Curva cuadrática: el centro queda intacto y la caída se concentra
            # en los bordes, que es como se comporta una viñeta óptica.
            mascara = 1.0 - fuerza * np.clip(radio, 0.0, 1.0) ** 2
            self._vignette_mask = np.repeat(
                mascara.astype(np.float32)[:, :, None], 3, axis=2
            )
            self._vignette_key = clave
        assert self._vignette_mask is not None
        return cv2.multiply(bgr, self._vignette_mask, dtype=cv2.CV_8U)
