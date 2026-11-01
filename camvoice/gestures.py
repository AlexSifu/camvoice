"""Catálogo de gestos y atajos de teclado (lógica pura, sin Qt ni Win32).

Los gestos son clips cortos que empiezan y terminan en la pose neutra (ver
docs/GUIA_GRABACION_Y_GESTOS.md). El director los reproduce a petición y luego
vuelve al grupo que corresponda al estado de voz **en ese momento**.

Atajos: se registran con RegisterHotKey (ver ui/hotkeys.py), que no espía el
teclado: Windows solo avisa cuando se pulsa exactamente esa combinación. Para
no chocar con nada, las reglas de `validate_hotkey` rechazan:

- Ctrl+Alt+tecla imprimible: en teclado español Ctrl+Alt equivale a AltGr, y
  registrar Ctrl+Alt+2 robaría la «@» al escribir.
- Ctrl+letra y Ctrl+Mayús+letra: son los atajos de Teams/Zoom/Office
  (Ctrl+Mayús+M silencia en Teams, Ctrl+Mayús+O apaga la cámara, Ctrl+E busca).
- La tecla Windows: Windows se reserva casi todas esas combinaciones.
- Alt+F4 y Alt+Tab.

Por defecto se usan Ctrl+Alt+F1…F8 (F12 cancela), que ninguna de esas
aplicaciones usa.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GESTURES",
    "CANCEL",
    "PAUSE",
    "SPEAKING_SUFFIX",
    "speaking_variant",
    "base_gesture",
    "is_known_gesture",
    "is_assignable_gesture",
    "GESTURE_LABELS",
    "DEFAULT_HOTKEYS",
    "Hotkey",
    "HotkeyError",
    "parse_hotkey",
    "validate_hotkey",
    "gesture_label",
]

GESTURES: tuple[str, ...] = (
    "saludar",
    "sonreir",
    "asentir",
    "negar",
    "mirar_arriba",
    "mirar_izquierda",
    "mirar_derecha",
    "reir",
)
"""Gestos reconocidos, en el orden en que se muestran. `reir` es opcional."""

CANCEL = "neutro"
"""Nombre reservado: cancela el gesto en curso y vuelve a la pose neutra."""

PAUSE = "pausa"
"""Nombre reservado: alterna la pausa manual del video (imagen congelada)."""

SPEAKING_SUFFIX = "_hablando"
"""Variante opcional de un gesto grabada hablando (p. ej. `asentir_hablando`).
Si existe, se usa cuando el gesto se pide mientras se habla; si no, el
director espera como mucho 200 ms a una micro-pausa antes de lanzarlo."""


def speaking_variant(name: str) -> str:
    return name + SPEAKING_SUFFIX


def base_gesture(name: str) -> str:
    return name[: -len(SPEAKING_SUFFIX)] if name.endswith(SPEAKING_SUFFIX) else name


def is_known_gesture(name: str) -> bool:
    """Gesto del catálogo o su variante `_hablando`."""
    return base_gesture(name) in GESTURES


def is_assignable_gesture(name: str) -> bool:
    """Nombre válido en la biblioteca: gesto del catálogo, escena grabada a
    petición (gesto o frase de `scenes.SCENES`) o la variante `_hablando` de
    cualquiera de ellos."""
    from .scenes import scene_by_slug

    if is_known_gesture(name):
        return True
    scene = scene_by_slug(base_gesture(name))
    return scene is not None and scene.is_action

GESTURE_LABELS: dict[str, str] = {
    "saludar": "Saludar",
    "sonreir": "Sonreír",
    "asentir": "Asentir",
    "negar": "Negar",
    "mirar_arriba": "Mirar arriba",
    "mirar_izquierda": "Mirar a la izquierda",
    "mirar_derecha": "Mirar a la derecha",
    "reir": "Reír",
    CANCEL: "Cancelar gesto",
    PAUSE: "Pausar / reanudar video",
}

DEFAULT_HOTKEYS: dict[str, str] = {
    "saludar": "Ctrl+Alt+F1",
    "sonreir": "Ctrl+Alt+F2",
    "asentir": "Ctrl+Alt+F3",
    "negar": "Ctrl+Alt+F4",
    "mirar_arriba": "Ctrl+Alt+F5",
    "mirar_izquierda": "Ctrl+Alt+F6",
    "mirar_derecha": "Ctrl+Alt+F7",
    "reir": "Ctrl+Alt+F8",
}


def gesture_label(name: str) -> str:
    from .scenes import scene_by_slug

    if name.endswith(SPEAKING_SUFFIX) and base_gesture(name) != name:
        return f"{gesture_label(base_gesture(name))} (hablando)"
    if name in GESTURE_LABELS:
        return GESTURE_LABELS[name]
    scene = scene_by_slug(name)
    if scene is not None:
        return scene.label
    return name.replace("_", " ").capitalize()


# --------------------------------------------------------------------------- #
# Atajos
# --------------------------------------------------------------------------- #

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_MOD_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "mayus": MOD_SHIFT,
    "mayús": MOD_SHIFT,
    "win": MOD_WIN,
}

_SPECIAL_KEYS = {
    "pause": 0x13,
    "pausa": 0x13,
    "insert": 0x2D,
    "home": 0x24,
    "inicio": 0x24,
    "end": 0x23,
    "fin": 0x23,
    "pageup": 0x21,
    "repag": 0x21,
    "pagedown": 0x22,
    "avpag": 0x22,
    "scrolllock": 0x91,
}


class HotkeyError(ValueError):
    """Combinación de teclas no válida o desaconsejada."""


@dataclass(frozen=True, slots=True)
class Hotkey:
    modifiers: int
    vk: int
    text: str
    """Forma canónica, p. ej. «Ctrl+Alt+F1»."""

    @property
    def printable(self) -> bool:
        """La tecla produce un carácter (letra, dígito o teclado numérico)."""
        return 0x30 <= self.vk <= 0x5A or 0x60 <= self.vk <= 0x69

    @property
    def main_block(self) -> bool:
        """Letra o dígito del bloque principal (lo que AltGr convierte en @ # €).
        El teclado numérico no produce caracteres con AltGr."""
        return 0x30 <= self.vk <= 0x5A


def _key_vk(token: str) -> tuple[int, str]:
    t = token.strip()
    low = t.lower()
    if low.startswith("f") and low[1:].isdigit():
        n = int(low[1:])
        if 1 <= n <= 24:
            return 0x6F + n, f"F{n}"
    if low.startswith("num") and low[3:].isdigit() and len(low) == 4:
        return 0x60 + int(low[3]), f"Num{low[3]}"
    if len(t) == 1 and t.isascii() and t.isalnum():
        return ord(t.upper()), t.upper()
    if low in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[low], t[:1].upper() + t[1:]
    raise HotkeyError(f"Tecla no reconocida: «{token}».")


def parse_hotkey(text: str) -> Hotkey:
    """Convierte «Ctrl+Alt+F1» en (modificadores, código de tecla virtual)."""
    tokens = [t for t in (text or "").replace(" ", "").split("+") if t]
    if not tokens:
        raise HotkeyError("El atajo está vacío.")
    mods = 0
    for tok in tokens[:-1]:
        bit = _MOD_NAMES.get(tok.lower())
        if bit is None:
            raise HotkeyError(f"Modificador no reconocido: «{tok}».")
        mods |= bit
    vk, key_name = _key_vk(tokens[-1])
    parts = []
    for bit, name in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win")):
        if mods & bit:
            parts.append(name)
    parts.append(key_name)
    return Hotkey(mods, vk, "+".join(parts))


def validate_hotkey(text: str) -> Hotkey:
    """Analiza y aplica las reglas anti-colisión. Lanza HotkeyError si no vale."""
    hk = parse_hotkey(text)
    m = hk.modifiers
    if m & MOD_WIN:
        raise HotkeyError(f"{hk.text}: Windows reserva las combinaciones con la tecla Windows.")
    if hk.main_block and (m & MOD_CONTROL) and (m & MOD_ALT):
        raise HotkeyError(
            f"{hk.text}: Ctrl+Alt equivale a AltGr en el teclado español y "
            "robaría caracteres como @ # € al escribir. Usa Ctrl+Alt+F1…F12."
        )
    if hk.printable and (m & MOD_CONTROL) and not (m & MOD_ALT):
        raise HotkeyError(
            f"{hk.text}: Ctrl+letra y Ctrl+Mayús+letra son atajos de Teams, "
            "Zoom y Office (silenciar, cámara, buscar). Usa Ctrl+Alt+F1…F12."
        )
    if hk.printable and m in (0, MOD_SHIFT, MOD_ALT, MOD_ALT | MOD_SHIFT):
        raise HotkeyError(f"{hk.text}: interferiría al escribir. Añade Ctrl+Alt y usa una tecla F.")
    if hk.vk == 0x73 and m == MOD_ALT:
        raise HotkeyError("Alt+F4 cierra ventanas.")
    is_high_f = 0x7C <= hk.vk <= 0x87  # F13-F24 no existen en teclados normales
    if m == 0 and not (is_high_f or hk.vk == 0x13):
        raise HotkeyError(f"{hk.text}: añade al menos un modificador (Ctrl+Alt).")
    return hk
