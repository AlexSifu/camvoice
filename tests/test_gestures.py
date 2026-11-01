"""Nombres de gestos y reglas de atajos que no chocan con Windows ni Teams."""

from __future__ import annotations

import pytest

from camvoice.gestures import (
    CANCEL,
    DEFAULT_HOTKEYS,
    GESTURES,
    MOD_ALT,
    MOD_CONTROL,
    HotkeyError,
    gesture_label,
    parse_hotkey,
    validate_hotkey,
)


def test_required_gestures_exist():
    for name in ("saludar", "sonreir", "asentir", "negar", "mirar_arriba",
                 "mirar_izquierda", "mirar_derecha", "reir"):
        assert name in GESTURES
    assert CANCEL not in GESTURES


def test_labels_are_human_readable():
    assert gesture_label("sonreir") == "Sonreír"
    assert gesture_label("mirar_arriba") == "Mirar arriba"


def test_defaults_are_valid_and_unique():
    keys = [validate_hotkey(k).text for k in DEFAULT_HOTKEYS.values()]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("text", ["Ctrl+Alt+F1", "ctrl + alt + f12", "Ctrl+Shift+F5", "Alt+F9",
                                  "Ctrl+Alt+Num1", "Pause", "F13", "Ctrl+Alt+PageUp"])
def test_accepted(text):
    validate_hotkey(text)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("Win+G", "Windows"),
        ("Ctrl+Alt+E", "AltGr"),   # € en teclado español
        ("Ctrl+Alt+2", "AltGr"),   # @
        ("Ctrl+Shift+M", "Teams"),  # silenciar en Teams
        ("Ctrl+E", "Teams"),
        ("A", "escribir"),
        ("Shift+A", "escribir"),
        ("Alt+F4", "cierra"),
        ("F5", "modificador"),
    ],
)
def test_rejected(text, fragment):
    with pytest.raises(HotkeyError) as exc:
        validate_hotkey(text)
    assert fragment in str(exc.value)


@pytest.mark.parametrize("text", ["", "Ctrl+Alt+", "Hyper+F1", "Ctrl+Alt+Banana"])
def test_parse_errors(text):
    with pytest.raises(HotkeyError):
        parse_hotkey(text)


def test_parse_normalizes_text_and_modifiers():
    hk = parse_hotkey("alt+ctrl+f3")
    assert hk.modifiers == MOD_CONTROL | MOD_ALT
    assert hk.vk == 0x72
    assert hk.text == "Ctrl+Alt+F3"
