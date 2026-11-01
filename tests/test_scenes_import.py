"""Importación de las grabaciones numeradas (scenes.py, video/importer.py).

Todo es sintético: videos diminutos escritos con OpenCV en `tmp_path`. Nunca
se tocan los videos reales del usuario.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from camvoice.config import AppConfig
from camvoice.scenes import (
    CATALOG_GESTURE_SCENES,
    EXPECTED_COUNT,
    SCENES,
    SKIPPED,
    SPEAKING_ONE_SHOT,
    scene_by_number,
)
from camvoice.video import importer
from camvoice.video.importer import (
    STATUS_ERROR,
    STATUS_READY,
    ImportCancelled,
    import_library,
    probe_video,
    rollback_library,
    scan_folder,
    validate_scan,
)
from camvoice.video.library import backup_path, load_library, manifest_path


def write_video(path: Path, seconds: float = 3.2, fps: int = 10, size=(32, 24), level: int = 120) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened()
    for i in range(int(round(seconds * fps))):
        frame = np.full((h, w, 3), level, np.uint8)
        cv2.circle(frame, (w // 2 + (i % 3) - 1, h // 2), 5, (40, 40, 40), -1)
        vw.write(frame)
    vw.release()
    return path


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def full_folder(tmp_path_factory) -> Path:
    """Carpeta como Camera Roll: las 62 escenas + omitidas + ruido."""
    root = tmp_path_factory.mktemp("camera-roll")
    for s in SCENES:
        secs = 1.2 if s.is_action else 3.2
        write_video(root / f"{s.number}.mp4", seconds=secs)
    write_video(root / "50.mp4", seconds=1.0)  # omitida a propósito: se ignora sin error
    (root / "foto.jpg").write_bytes(b"\xff\xd8 no es video")
    (root / "v_aud_003.mp4").write_bytes(b"x")
    (root / "movimientos.mp4.mp4").write_bytes(b"x")
    (root / "notas.txt").write_text("hola")
    write_video(root / "sub" / "4.mp4", seconds=1.0)  # no recursivo
    return root


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    c = AppConfig()
    old_s, old_h = tmp_path / "viejo_silencio.mp4", tmp_path / "viejo_habla.mp4"
    old_s.write_bytes(b"x")
    old_h.write_bytes(b"x")
    c.video.silent_clip, c.video.speaking_clip = str(old_s), str(old_h)
    c.video.library_manifest = str(tmp_path / "lib" / "manifest.json")
    return c


# --------------------------------------------------------------------------- #
# Tabla de escenas
# --------------------------------------------------------------------------- #


def test_scene_table_is_consistent():
    numbers = [s.number for s in SCENES]
    assert len(SCENES) == EXPECTED_COUNT == 62
    assert len(set(numbers)) == 62
    assert set(numbers) | set(SKIPPED) == set(range(1, 67))
    assert not set(numbers) & set(SKIPPED)
    assert scene_by_number(1).category == "SILENT_BASE"
    assert scene_by_number(2).category == "SPEAKING_BASE"
    for name, n in CATALOG_GESTURE_SCENES.items():
        assert scene_by_number(n).is_action, name


# --------------------------------------------------------------------------- #
# Detección
# --------------------------------------------------------------------------- #


def test_scan_naming_rules(tmp_path):
    for name in ("1.mp4", "02.MP4", "003.mov", "1a.mp4", "7.txt", "x1.mp4", "67.mp4", "52.mkv"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "4.mp4").write_bytes(b"x")
    scan = scan_folder(tmp_path)
    assert sorted(scan.matched) == [1, 2, 3]
    assert set(scan.unexpected) == {52, 67}  # omitida a propósito y sin escena
    ignored = {p.name for p in scan.ignored}
    assert {"1a.mp4", "7.txt", "x1.mp4"} <= ignored
    assert 4 not in scan.entries  # no se entra en subcarpetas


def test_scan_duplicate_number_only_marks_that_entry(tmp_path):
    for name in ("5.mp4", "05.mkv", "6.mp4"):
        (tmp_path / name).write_bytes(b"x")
    scan = scan_folder(tmp_path)
    assert scan.ambiguous == [5]
    assert 5 not in scan.matched and 6 in scan.matched
    assert "ambiguas: 5" in scan.summary()


def test_scan_full_folder_62(full_folder):
    scan = scan_folder(full_folder)
    assert len(scan.matched) == 62
    assert scan.missing == [] and scan.ambiguous == []
    assert set(scan.unexpected) == {50}
    assert "62/62" in scan.summary()


def test_scan_missing_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_folder(tmp_path / "no-existe")


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #


def test_probe_flags_empty_corrupt_and_short(tmp_path):
    empty = tmp_path / "1.mp4"
    empty.write_bytes(b"")
    corrupt = tmp_path / "2.mp4"
    corrupt.write_bytes(np.random.default_rng(0).bytes(4096))
    short = write_video(tmp_path / "3.mp4", seconds=1.0)
    good = write_video(tmp_path / "4.mp4", seconds=3.5)
    assert probe_video(empty).status == STATUS_ERROR
    assert probe_video(corrupt).status == STATUS_ERROR
    s = probe_video(short, scene_by_number(3), thumbs_dir=tmp_path / "thumbs")
    # Corto: se avisa, pero queda listo para usarse (no bloquea).
    assert s.status == STATUS_READY and any("al menos" in i for i in s.issues)
    g = probe_video(good, scene_by_number(4), thumbs_dir=tmp_path / "thumbs")
    assert g.status == STATUS_READY, g.issues
    assert Path(g.thumbnail).is_file() and g.width == 32 and g.duration_s == pytest.approx(3.5, abs=0.2)


def test_validate_resumes_and_cancel_keeps_progress(tmp_path, monkeypatch):
    src = tmp_path / "src"
    for n in (1, 2, 3, 4):
        write_video(src / f"{n}.mp4")
    scan = scan_folder(src)
    lib_dir = tmp_path / "lib"
    calls: list[str] = []
    real = importer.probe_video

    def counting(path, *a, **k):
        calls.append(Path(path).name)
        return real(path, *a, **k)

    monkeypatch.setattr(importer, "probe_video", counting)
    checks = iter([False, False, True])
    with pytest.raises(ImportCancelled):
        validate_scan(scan, lib_dir, cancel=lambda: next(checks, True))
    assert calls == ["1.mp4", "2.mp4"]
    calls.clear()
    out = validate_scan(scan, lib_dir)  # reanuda: solo faltan 3 y 4
    assert calls == ["3.mp4", "4.mp4"]
    assert sorted(out) == [1, 2, 3, 4]
    calls.clear()
    validate_scan(scan, lib_dir)
    assert calls == []  # nada cambió
    (src / "2.mp4").write_bytes(b"")  # cambia el archivo -> se revalida
    out = validate_scan(scan, lib_dir)
    assert calls == ["2.mp4"] and out[2].status == STATUS_ERROR


def test_validate_marks_ambiguous_as_error(tmp_path):
    write_video(tmp_path / "1.mp4")
    write_video(tmp_path / "01.mp4")
    write_video(tmp_path / "2.mp4")
    out = validate_scan(scan_folder(tmp_path), tmp_path / "lib")
    assert out[1].status == STATUS_ERROR and "repetido" in out[1].issues[0]
    assert out[2].status == STATUS_READY


def test_resolution_outlier_warns_but_is_ready(tmp_path):
    write_video(tmp_path / "1.mp4")
    write_video(tmp_path / "2.mp4", size=(48, 24))
    out = validate_scan(scan_folder(tmp_path), tmp_path / "lib")
    assert out[1].status == STATUS_READY
    assert out[2].status == STATUS_READY
    assert any("Resolución" in i for i in out[2].issues)


def test_todo_lo_decodificable_queda_listo_y_activo(cfg, tmp_path):
    """Directiva: cada archivo que se decodifica se usa de inmediato."""
    write_video(tmp_path / "1.mp4", seconds=3.2)
    write_video(tmp_path / "2.mp4", seconds=3.2)
    write_video(tmp_path / "6.mp4", seconds=0.5)            # corto: aviso
    write_video(tmp_path / "9.mp4", seconds=1.2, size=(48, 24))  # otra resolución: aviso
    (tmp_path / "10.mp4").write_bytes(b"")                  # vacío: error
    scan = scan_folder(tmp_path)
    probes = validate_scan(scan, tmp_path / "probe")
    assert {n: p.status for n, p in probes.items()} == {
        1: STATUS_READY, 2: STATUS_READY, 6: STATUS_READY, 9: STATUS_READY, 10: STATUS_ERROR,
    }
    assert probes[6].issues and probes[9].issues
    lib, _ = import_library(cfg, scan, probes)
    for n in (1, 2, 6, 9):
        assert lib.scenes[n].usable and lib.scenes[n].status == STATUS_READY
    assert lib.scenes[10].status == STATUS_ERROR


# --------------------------------------------------------------------------- #
# Importación
# --------------------------------------------------------------------------- #


def _import(cfg, folder, **kw):
    scan = scan_folder(folder)
    probes = validate_scan(scan, manifest_path(cfg).parent)
    return import_library(cfg, scan, probes, **kw)


def test_import_62_reference_mode(cfg, full_folder):
    before = {p.name: digest(p) for p in full_folder.iterdir() if p.is_file()}
    old_silent = cfg.video.silent_clip
    lib, warnings = _import(cfg, full_folder)
    assert len(lib.scenes) == 62
    assert all(e.status != STATUS_ERROR for e in lib.scenes.values())
    assert any("n.º 50" in w and "omitido a propósito" in w for w in warnings)
    # Referencia: el clip de trabajo es el original, que no cambia ni se borra.
    assert all(Path(e.working) == Path(e.source) for e in lib.scenes.values())
    after = {p.name: digest(p) for p in full_folder.iterdir() if p.is_file()}
    assert before == after
    # Clips principales = n.º 1 y 2, y se recuerdan los anteriores.
    assert Path(cfg.video.silent_clip).name == "1.mp4"
    assert Path(cfg.video.speaking_clip).name == "2.mp4"
    assert lib.previous_main["silent_clip"] == old_silent
    # Manifiesto versionado en disco.
    data = json.loads(manifest_path(cfg).read_text(encoding="utf-8"))
    assert data["version"] == 2 and len(data["scenes"]) == 62
    loaded = load_library(cfg)
    assert loaded.issues == [], loaded.issues
    # Gestos: catálogo con atajo; el resto sin atajo global.
    hot = loaded.library.hotkeys()
    for name in CATALOG_GESTURE_SCENES:
        assert name in hot, name
    actions = [s for s in SCENES if s.is_action]
    assert len(loaded.library.active_gestures()) == len(actions)
    assert len(hot) == len(CATALOG_GESTURE_SCENES)


def test_pools_and_speaking_actions_after_import(cfg, full_folder):
    lib, _ = _import(cfg, full_folder)
    silent, speaking = lib.pools(cfg)
    one_shot = {str(Path(full_folder / f"{s.number}.mp4")) for s in SCENES if s.category == SPEAKING_ONE_SHOT}
    assert not one_shot & set(speaking)  # las frases a petición nunca entran en el bucle
    assert Path(silent[0]).name == "1.mp4" and Path(speaking[0]).name == "2.mp4"
    assert len(silent) <= 16 and len(speaking) <= 16
    names = lib.speaking_actions()
    assert len(names) == sum(1 for s in SCENES if s.category == SPEAKING_ONE_SHOT)


def test_import_is_idempotent_and_keeps_user_settings(cfg, full_folder):
    lib, _ = _import(cfg, full_folder)
    first = {n: e.to_dict() for n, e in lib.scenes.items()}
    # El usuario ajusta un atajo, unas anclas y desactiva una escena.
    lib.gestures["saludar"].hotkey = "Ctrl+Alt+S"
    lib.scenes[3].anchors = [5, 9]
    lib.scenes[4].enabled = False
    from camvoice.video.library import save_library

    save_library(lib, manifest_path(cfg))
    lib2, _ = _import(cfg, full_folder)
    assert lib2.gestures["saludar"].hotkey == "Ctrl+Alt+S"
    assert lib2.scenes[3].anchors == [5, 9]
    assert lib2.scenes[4].enabled is False
    second = {n: e.to_dict() for n, e in lib2.scenes.items()}
    for n in first:
        if n not in (3, 4):
            assert first[n] == second[n], n
    assert backup_path(manifest_path(cfg)).is_file()


def test_rollback_restores_previous_state(cfg, full_folder):
    old_silent, old_speaking = cfg.video.silent_clip, cfg.video.speaking_clip
    _import(cfg, full_folder)
    msg = rollback_library(cfg)
    assert "manifiesto actual se guardó" in msg or "retir" in msg or "No había" in msg
    assert cfg.video.silent_clip == old_silent and cfg.video.speaking_clip == old_speaking
    assert not manifest_path(cfg).exists()
    assert list(manifest_path(cfg).parent.glob("manifest.retirado-*.json"))
    assert len(scan_folder(full_folder).matched) == 62  # los originales siguen ahí
    assert rollback_library(cfg) == "No hay ninguna importación que deshacer."


def test_rollback_after_reimport_restores_backup(cfg, full_folder):
    _import(cfg, full_folder)
    lib = load_library(cfg).library
    lib.gestures["asentir"].hotkey = "Ctrl+Alt+N"
    from camvoice.video.library import save_library

    save_library(lib, manifest_path(cfg))
    _import(cfg, full_folder)
    rollback_library(cfg)
    assert load_library(cfg).library.gestures["asentir"].hotkey == "Ctrl+Alt+N"


def test_copy_mode_is_atomic_and_cancellable(cfg, tmp_path):
    src = tmp_path / "src"
    for n in (1, 2, 3):
        write_video(src / f"{n}.mp4")
    before = {p.name: digest(p) for p in src.iterdir()}
    media = manifest_path(cfg).parent / "media"
    scan = scan_folder(src)
    probes = validate_scan(scan, manifest_path(cfg).parent)
    checks = iter([False, False, False, True])
    with pytest.raises(ImportCancelled):
        import_library(cfg, scan, probes, copy=True, cancel=lambda: next(checks, True))
    assert not list(media.glob("*.part"))
    assert not manifest_path(cfg).exists()  # cancelar no deja manifiesto a medias
    lib, _ = import_library(cfg, scan, probes, copy=True)
    assert sorted(p.name for p in media.iterdir()) == ["01.mp4", "02.mp4", "03.mp4"]
    assert all(Path(e.working).parent == media for e in lib.scenes.values())
    assert {p.name: digest(p) for p in src.iterdir()} == before
    # Segunda vez: no vuelve a copiar (mismo tamaño y fecha).
    mtimes = {p.name: p.stat().st_mtime_ns for p in media.iterdir()}
    import_library(cfg, scan, probes, copy=True)
    assert {p.name: p.stat().st_mtime_ns for p in media.iterdir()} == mtimes


def test_import_with_corrupt_entry_marks_only_that_one(cfg, tmp_path):
    src = tmp_path / "src"
    for n in (1, 2, 9):
        write_video(src / f"{n}.mp4", seconds=3.2 if n < 3 else 1.2)
    (src / "11.mp4").write_bytes(b"roto")
    lib, warnings = _import(cfg, src)
    assert lib.scenes[11].status == STATUS_ERROR
    assert lib.scenes[9].status == STATUS_READY
    assert "sonreir" not in load_library(cfg).library.active_gestures()
    assert "asentir" in load_library(cfg).library.active_gestures()
    assert any("no se encontró" in w for w in warnings)  # faltan muchas


def test_import_does_not_change_main_clips_if_base_missing(cfg, tmp_path):
    src = tmp_path / "src"
    write_video(src / "3.mp4")
    old = cfg.video.silent_clip
    _, warnings = _import(cfg, src)
    assert cfg.video.silent_clip == old
    assert any("No se cambian los clips principales" in w for w in warnings)


def test_gitignore_excludes_media_and_library():
    text = Path(__file__).resolve().parents[1].joinpath(".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.mp4", "*.npz"):
        assert pattern in text, pattern
