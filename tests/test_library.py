"""Manifiesto versionado de la biblioteca de clips (video/library.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from camvoice.config import AppConfig
from camvoice.video.library import (
    MANIFEST_VERSION,
    Library,
    load_library,
    manifest_path,
    missing_gesture_message,
    save_library,
)


def make_cfg(tmp_path: Path, manifest: dict | str | None) -> AppConfig:
    cfg = AppConfig()
    for name in ("habla.mp4", "silencio.mp4", "silencio2.mp4", "hola.mp4"):
        (tmp_path / name).write_bytes(b"x")
    cfg.video.speaking_clip = str(tmp_path / "habla.mp4")
    cfg.video.silent_clip = str(tmp_path / "silencio.mp4")
    path = tmp_path / "manifest.json"
    cfg.video.library_manifest = str(path)
    if isinstance(manifest, dict):
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif isinstance(manifest, str):
        path.write_text(manifest, encoding="utf-8")
    return cfg


def test_no_manifest_gives_legacy_pools(tmp_path):
    cfg = make_cfg(tmp_path, None)
    loaded = load_library(cfg)
    assert not loaded.exists and loaded.issues == []
    silent, speaking = loaded.library.pools(cfg)
    assert silent == [cfg.video.silent_clip]
    assert speaking == [cfg.video.speaking_clip]
    assert loaded.library.active_gestures() == {}


def test_default_manifest_location_is_in_app_home(isolated_home):
    cfg = AppConfig()
    assert manifest_path(cfg) == Path(isolated_home) / "library" / "manifest.json"


def test_pools_put_main_clip_first_and_dedupe(tmp_path):
    cfg = make_cfg(tmp_path, {
        "version": 1,
        "silent": [str(tmp_path / "silencio2.mp4"), str(tmp_path / "silencio.mp4"),
                   str(tmp_path / "silencio2.mp4")],
    })
    silent, _ = load_library(cfg).library.pools(cfg)
    assert silent == [cfg.video.silent_clip, str(tmp_path / "silencio2.mp4")]


def test_corrupt_json_falls_back_with_clear_issue(tmp_path):
    cfg = make_cfg(tmp_path, "{ esto no es json")
    loaded = load_library(cfg)
    assert loaded.exists
    assert any("No se pudo leer el manifiesto" in i for i in loaded.issues)
    silent, speaking = loaded.library.pools(cfg)
    assert silent == [cfg.video.silent_clip] and speaking == [cfg.video.speaking_clip]


def test_not_an_object(tmp_path):
    loaded = load_library(make_cfg(tmp_path, "[1, 2]"))
    assert any("no es un objeto" in i for i in loaded.issues)


def test_newer_version_uses_only_main_clips(tmp_path):
    cfg = make_cfg(tmp_path, {
        "version": MANIFEST_VERSION + 1,
        "silent": [str(tmp_path / "silencio2.mp4")],
        "gestures": {"saludar": {"clip": str(tmp_path / "hola.mp4")}},
    })
    loaded = load_library(cfg)
    assert any("más nueva" in i for i in loaded.issues)
    assert loaded.library.pools(cfg)[0] == [cfg.video.silent_clip]
    assert loaded.library.active_gestures() == {}


def test_v0_without_version_is_migrated(tmp_path):
    cfg = make_cfg(tmp_path, {"gestures": {"saludar": str(tmp_path / "hola.mp4")}})
    loaded = load_library(cfg)
    assert loaded.library.version == MANIFEST_VERSION
    assert "saludar" in loaded.library.active_gestures()
    assert loaded.library.gestures["saludar"].hotkey == "Ctrl+Alt+F1"


def test_unknown_reserved_and_invalid_entries_are_reported(tmp_path):
    cfg = make_cfg(tmp_path, {
        "version": 1,
        "silent": "no-es-lista",
        "speaking": [3, str(tmp_path / "habla.mp4")],
        "gestures": {"bailar": "x.mp4", "neutro": "y.mp4", "negar": 5},
    })
    issues = load_library(cfg).issues
    text = "\n".join(issues)
    assert "«silent» debe ser una lista" in text
    assert "no son rutas" in text
    assert "desconocido" in text and "bailar" in text
    assert "reservado" in text
    assert "Entrada inválida" in text


def test_missing_gesture_file_and_bad_or_duplicate_hotkeys(tmp_path):
    cfg = make_cfg(tmp_path, {
        "version": 1,
        "gestures": {
            "saludar": {"clip": str(tmp_path / "hola.mp4"), "hotkey": "Ctrl+Alt+F1"},
            "sonreir": {"clip": str(tmp_path / "hola.mp4"), "hotkey": "Ctrl+Alt+F1"},
            "asentir": {"clip": str(tmp_path / "no-existe.mp4")},
            "negar": {"clip": str(tmp_path / "hola.mp4"), "hotkey": "Ctrl+Shift+M"},
        },
    })
    text = "\n".join(load_library(cfg).issues)
    assert "no se encuentra" in text
    assert "Teams" in text
    assert "está asignado a «Saludar» y a «Sonreír»" in text


def test_disabled_gesture_is_not_active(tmp_path):
    cfg = make_cfg(tmp_path, {
        "version": 1,
        "gestures": {"saludar": {"clip": str(tmp_path / "hola.mp4"), "enabled": False}},
    })
    lib = load_library(cfg).library
    assert "saludar" not in lib.active_gestures()
    assert "saludar" in lib.missing_gestures()


def test_roundtrip_save_and_load(tmp_path):
    cfg = make_cfg(tmp_path, None)
    lib = Library()
    lib.silent = [str(tmp_path / "silencio2.mp4")]
    lib.set_gesture("saludar", str(tmp_path / "hola.mp4"), "Ctrl+Alt+F9")
    path = manifest_path(cfg)
    save_library(lib, path)
    assert not path.with_suffix(".json.tmp").exists()
    again = load_library(cfg)
    assert again.issues == []
    assert again.library.to_dict() == lib.to_dict()
    assert again.library.hotkeys()["saludar"] == "Ctrl+Alt+F9"


def test_set_unknown_gesture_raises():
    with pytest.raises(ValueError):
        Library().set_gesture("bailar", "x.mp4")


def test_missing_gesture_message_names_the_gesture():
    msg = missing_gesture_message("mirar_arriba")
    assert "Mirar arriba" in msg and "No hay clip" in msg
