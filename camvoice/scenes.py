"""Catálogo de escenas grabadas (las 62 tomas numeradas) y sus categorías.

Es la fuente de verdad para importar la carpeta de grabaciones: el número del
archivo (`17.mp4`) decide la escena, su nombre visible, su categoría y la
descripción que se muestra en la interfaz. Lógica pura: sin Qt ni OpenCV.

Categorías y cómo las usa el director:

- ``SILENT_BASE`` / ``SILENT_VARIATION``: grupo de silencio (bucle continuo).
- ``SPEAKING_BASE`` / ``SPEAKING_VARIATION``: grupo de habla (bucle continuo).
- ``SPEAKING_ONE_SHOT``: frase completa a petición; se reproduce **una vez**
  y vuelve al estado que pida la voz. Nunca entra en el bucle de habla.
- ``GESTURE``: gesto a petición; una vez y vuelta (no es una variante
  `_hablando`: se grabó en silencio).

Los números 50, 52, 59 y 63 se omitieron a propósito al grabar; su ausencia
no es un error y no se buscan.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SILENT_BASE",
    "SILENT_VARIATION",
    "SPEAKING_BASE",
    "SPEAKING_VARIATION",
    "SPEAKING_ONE_SHOT",
    "GESTURE",
    "CATEGORIES",
    "CATEGORY_LABELS",
    "Scene",
    "SCENES",
    "SKIPPED",
    "EXPECTED_COUNT",
    "CATALOG_GESTURE_SCENES",
    "scene_by_number",
    "scene_by_slug",
    "scene_label",
    "is_scene_slug",
    "is_loop_category",
    "is_action_category",
]

SILENT_BASE = "SILENT_BASE"
SILENT_VARIATION = "SILENT_VARIATION"
SPEAKING_BASE = "SPEAKING_BASE"
SPEAKING_VARIATION = "SPEAKING_VARIATION"
SPEAKING_ONE_SHOT = "SPEAKING_ONE_SHOT"
GESTURE = "GESTURE"

CATEGORIES: tuple[str, ...] = (
    SILENT_BASE,
    SILENT_VARIATION,
    SPEAKING_BASE,
    SPEAKING_VARIATION,
    SPEAKING_ONE_SHOT,
    GESTURE,
)

CATEGORY_LABELS: dict[str, str] = {
    SILENT_BASE: "Silencio (base)",
    SILENT_VARIATION: "Silencio (variación)",
    SPEAKING_BASE: "Habla (base)",
    SPEAKING_VARIATION: "Habla (variación)",
    SPEAKING_ONE_SHOT: "Frase a petición",
    GESTURE: "Gesto",
}


@dataclass(frozen=True, slots=True)
class Scene:
    number: int
    slug: str
    label: str
    category: str
    description: str

    @property
    def is_silent(self) -> bool:
        return self.category in (SILENT_BASE, SILENT_VARIATION)

    @property
    def is_speaking_loop(self) -> bool:
        return self.category in (SPEAKING_BASE, SPEAKING_VARIATION)

    @property
    def is_action(self) -> bool:
        """Se reproduce una vez a petición (gesto o frase corta)."""
        return self.category in (GESTURE, SPEAKING_ONE_SHOT)


SCENES: tuple[Scene, ...] = (
    Scene(1, 'silencio_neutro', 'Silencio neutro', SILENT_BASE, 'Mirar a cámara, respirar y parpadear con naturalidad; boca cerrada.'),
    Scene(2, 'habla_natural', 'Habla natural', SPEAKING_BASE, 'Hablar de manera natural, mirando al objetivo y sin gestos exagerados.'),
    Scene(3, 'escucha_atenta', 'Escucha atenta', SILENT_VARIATION, 'Escuchar con movimientos pequeños de ojos/cabeza.'),
    Scene(4, 'hablar_sin_manos', 'Hablar sin mover las manos', SPEAKING_VARIATION, 'Hablar con manos apoyadas y mirada a cámara.'),
    Scene(5, 'hablar_movimientos_cabeza', 'Hablar con movimientos de cabeza', SPEAKING_VARIATION, 'Hablar con inclinaciones discretas de cabeza.'),
    Scene(6, 'terminar_explicacion', 'Terminar una explicación', SPEAKING_ONE_SHOT, 'Finalizar frase, cerrar boca y quedar en postura neutra.'),
    Scene(7, 'respuesta_corta', 'Respuesta corta', SPEAKING_ONE_SHOT, 'Pronunciar sí, claro o vale y volver a reposo.'),
    Scene(8, 'frase_corta', 'Frase corta', SPEAKING_ONE_SHOT, 'Decir una frase breve y volver a expresión neutra.'),
    Scene(9, 'asentir_una_vez', 'Asentir una vez', GESTURE, 'Asentir suavemente una vez y volver al centro.'),
    Scene(10, 'asentir_varias_veces', 'Asentir varias veces', GESTURE, 'Asentir dos o tres veces y volver al centro.'),
    Scene(11, 'sonrisa_breve', 'Sonrisa breve', GESTURE, 'Sonreír un instante y relajar la cara.'),
    Scene(12, 'negar_cabeza', 'Negar con la cabeza', GESTURE, 'Negar de forma suave y regresar al frente.'),
    Scene(13, 'sorpresa_leve', 'Sorpresa leve', GESTURE, 'Levantar ligeramente las cejas y relajar.'),
    Scene(14, 'mirar_abajo_volver', 'Mirar abajo y volver', GESTURE, 'Mirar hacia abajo y volver a cámara.'),
    Scene(15, 'mirar_otra_pantalla', 'Mirar a otra pantalla', GESTURE, 'Mirar hacia un costado y regresar.'),
    Scene(16, 'mirar_arriba', 'Mirar hacia arriba', GESTURE, 'Elevar la mirada y volver.'),
    Scene(17, 'saludar_mano', 'Saludar con la mano', GESTURE, 'Levantar mano, saludar y bajar.'),
    Scene(18, 'despedirse_mano', 'Despedirse con la mano', GESTURE, 'Despedirse con una mano y volver.'),
    Scene(19, 'hablar_sonriendo', 'Hablar sonriendo', SPEAKING_VARIATION, 'Hablar con sonrisa moderada.'),
    Scene(20, 'hablar_explicando', 'Hablar explicando', SPEAKING_VARIATION, 'Hablar explicando, con movimientos leves de cabeza y sin manos.'),
    Scene(21, 'hablar_serio', 'Hablar seriamente', SPEAKING_VARIATION, 'Hablar con expresión concentrada.'),
    Scene(22, 'hablar_relajado', 'Hablar relajadamente', SPEAKING_VARIATION, 'Hablar con expresión amistosa y relajada.'),
    Scene(23, 'sonreir_asentir', 'Sonreír y asentir', GESTURE, 'Sonreír y asentir una vez.'),
    Scene(24, 'senal_un_momento', 'Señal de un momento', GESTURE, 'Palma al frente brevemente y bajar la mano.'),
    Scene(25, 'mirar_teclado', 'Mirar al teclado', GESTURE, 'Bajar la vista al teclado y volver.'),
    Scene(26, 'acercarse_pantalla', 'Acercarse a la pantalla', GESTURE, 'Inclinar torso hacia delante y volver.'),
    Scene(27, 'pedir_palabra', 'Pedir la palabra', GESTURE, 'Levantar mano y volver.'),
    Scene(28, 'expresion_pensativa', 'Expresión pensativa', GESTURE, 'Expresión de concentración breve.'),
    Scene(29, 'reir_ligeramente', 'Reír ligeramente', GESTURE, 'Risa breve y discreta y volver.'),
    Scene(30, 'mirar_izquierda', 'Mirar a la izquierda', GESTURE, 'Desviar mirada a izquierda y volver.'),
    Scene(31, 'inclinar_cabeza_izquierda', 'Inclinar cabeza a la izquierda', GESTURE, 'Inclinar suavemente cabeza y volver.'),
    Scene(32, 'inclinar_cabeza_derecha', 'Inclinar cabeza a la derecha', GESTURE, 'Inclinar suavemente cabeza y volver.'),
    Scene(33, 'cejas_comprension', 'Cejas de comprensión', GESTURE, 'Levantar cejas brevemente.'),
    Scene(34, 'fruncir_ceno', 'Fruncir el ceño', GESTURE, 'Fruncir discretamente y relajar.'),
    Scene(35, 'mirar_derecha', 'Mirar a la derecha', GESTURE, 'Mirar a la derecha y volver.'),
    Scene(36, 'entrecerrar_ojos', 'Entrecerrar los ojos', GESTURE, 'Entrecerrar ojos brevemente y relajar.'),
    Scene(37, 'pensar_mano_menton', 'Pensar con mano en mentón', GESTURE, 'Llevar mano al mentón y retirarla.'),
    Scene(38, 'hablar_una_mano', 'Hablar con una mano', SPEAKING_VARIATION, 'Hablar gesticulando discretamente con una mano.'),
    Scene(39, 'hablar_ambas_manos', 'Hablar con ambas manos', SPEAKING_VARIATION, 'Hablar con gestos moderados de ambas manos.'),
    Scene(40, 'hablar_consultando_notas', 'Hablar consultando notas', SPEAKING_VARIATION, 'Hablar, mirar abajo y regresar.'),
    Scene(41, 'hablar_enfatizando', 'Hablar enfatizando', SPEAKING_VARIATION, 'Hablar elevando ligeramente las cejas.'),
    Scene(42, 'levantar_indice', 'Levantar el índice', GESTURE, 'Levantar un dedo índice y bajarlo.'),
    Scene(43, 'mostrar_dos_dedos', 'Mostrar dos dedos', GESTURE, 'Mostrar dos dedos y bajar.'),
    Scene(44, 'mostrar_tres_dedos', 'Mostrar tres dedos', GESTURE, 'Mostrar tres dedos y bajar.'),
    Scene(45, 'senal_ok', 'Señal de OK', GESTURE, 'Hacer señal OK con la mano y volver.'),
    Scene(46, 'palmas_abiertas', 'Ambas palmas abiertas', GESTURE, 'Levantar ambas palmas y bajar.'),
    Scene(47, 'senalar_lado', 'Señalar hacia un lado', GESTURE, 'Señalar a un costado y volver.'),
    Scene(48, 'apoyar_manos', 'Apoyar las manos', GESTURE, 'Apoyar manos en la mesa y quedar neutro.'),
    Scene(49, 'escribir_nota', 'Escribir una nota', GESTURE, 'Mirar abajo, escribir brevemente y volver.'),
    Scene(51, 'acomodar_camisa', 'Acomodar la camisa', GESTURE, 'Acomodar cuello de camisa y volver.'),
    Scene(53, 'inclinarse_adelante', 'Inclinarse hacia adelante', GESTURE, 'Inclinar torso adelante y volver.'),
    Scene(54, 'respirar_relajarse', 'Respirar y relajarse', GESTURE, 'Respirar suavemente y relajar hombros.'),
    Scene(55, 'pulgar_arriba', 'Pulgar arriba', GESTURE, 'Mostrar pulgar arriba y bajar.'),
    Scene(56, 'moverse_izquierda', 'Moverse a la izquierda', GESTURE, 'Acomodarse a la izquierda en la silla y volver.'),
    Scene(57, 'moverse_derecha', 'Moverse a la derecha', GESTURE, 'Acomodarse a la derecha en la silla y volver.'),
    Scene(58, 'echarse_atras', 'Echarse hacia atrás', GESTURE, 'Echarse algo hacia atrás/estirarse y regresar.'),
    Scene(60, 'ajustar_gafas_o_pelo', 'Ajustar gafas o pelo', GESTURE, 'Ajustarse gafas o pelo si corresponde.'),
    Scene(61, 'tocarse_cara', 'Tocarse la cara', GESTURE, 'Tocar brevemente la cara y bajar mano.'),
    Scene(62, 'acomodar_cabello', 'Acomodar el cabello', GESTURE, 'Acomodar cabello y volver.'),
    Scene(64, 'tocarse_oreja', 'Tocarse la oreja', GESTURE, 'Tocar oreja brevemente y volver.'),
    Scene(65, 'cruzar_brazos', 'Cruzar los brazos', GESTURE, 'Cruzar brazos por un momento y regresar.'),
    Scene(66, 'apoyar_menton', 'Apoyar el mentón', GESTURE, 'Apoyar mentón en mano y retirarla.'),
)
"""Las 62 escenas grabadas, en orden de número de archivo."""

SKIPPED: dict[int, str] = {
    50: "Acomodar auriculares",
    52: "Mirar hacia la puerta",
    59: "Beber agua",
    63: "Ajustar los lentes",
}
"""Números omitidos intencionadamente al grabar (no son un error)."""

EXPECTED_COUNT = len(SCENES)

CATALOG_GESTURE_SCENES: dict[str, int] = {
    "saludar": 17,
    "sonreir": 11,
    "asentir": 9,
    "negar": 12,
    "mirar_arriba": 16,
    "mirar_izquierda": 30,
    "mirar_derecha": 35,
    "reir": 29,
}
"""Gestos del catálogo con atajo por defecto (gestures.GESTURES) y la escena
grabada que los representa. Así los atajos Ctrl+Alt+F1…F8 de siempre lanzan
las nuevas grabaciones sin registrar 62 atajos globales."""

_BY_NUMBER = {s.number: s for s in SCENES}
_BY_SLUG = {s.slug: s for s in SCENES}

assert len(_BY_NUMBER) == len(SCENES) == 62, "tabla de escenas inconsistente"
assert len(_BY_SLUG) == len(SCENES), "slugs repetidos"
assert not set(SKIPPED) & set(_BY_NUMBER), "un número omitido figura como escena"


def scene_by_number(number: int) -> Scene | None:
    return _BY_NUMBER.get(int(number))


def scene_by_slug(slug: str) -> Scene | None:
    return _BY_SLUG.get(slug)


def is_scene_slug(name: str) -> bool:
    return name in _BY_SLUG


def scene_label(slug: str) -> str | None:
    s = _BY_SLUG.get(slug)
    return f"{s.label} (n.º {s.number})" if s else None


def is_loop_category(category: str) -> bool:
    return category in (SILENT_BASE, SILENT_VARIATION, SPEAKING_BASE, SPEAKING_VARIATION)


def is_action_category(category: str) -> bool:
    return category in (GESTURE, SPEAKING_ONE_SHOT)
