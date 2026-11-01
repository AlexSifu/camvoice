"""Transiciones v2: índice con cabeza y brillo, coste de entrada del director,
anclas manuales, frases a petición y salida limpia (sin rótulos).

Todo sintético (caras esquemáticas de testclips.py).
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from camvoice.audio.gate import VoiceState
from camvoice.config import GesturesConfig
from camvoice.video.director import Director, DirectorClip, VoiceInput
from camvoice.video.index import INDEX_VERSION, ClipIndex, _interpolate_head, build_index, luma

from . import synth
from .synth import FPS, H, FakeClock, face_frames, speaking_mouth

SIL = VoiceInput(VoiceState.SILENT, 0.0)
TALK = VoiceInput(VoiceState.SPEAKING, 0.8)


def fresh(name: str, frames) -> DirectorClip:
    """Clip con índice propio (las pruebas lo modifican: no usar standard_set)."""
    return DirectorClip(name, frames, build_index(frames, pixel_format="nv12", height=H, fps=FPS))


@pytest.fixture(scope="module")
def frames_silent():
    return face_frames(90, lambda i: 0.0, "")


@pytest.fixture(scope="module")
def frames_speaking():
    return face_frames(90, speaking_mouth, "")


# --------------------------------------------------------------------------- #
# Índice v2
# --------------------------------------------------------------------------- #


def test_index_v2_has_brightness_and_head(frames_silent, tmp_path):
    assert INDEX_VERSION == 2
    idx = build_index(frames_silent, pixel_format="nv12", height=H, fps=FPS)
    assert idx.brightness.shape == (90,) and idx.head.shape == (90, 3)
    expected = cv2.mean(luma(frames_silent[10], "nv12", H))[0]
    assert idx.brightness[10] == pytest.approx(expected, abs=0.01)
    found = np.isfinite(idx.head[:, 0])
    if found.any():  # Haar puede no reconocer la cara esquemática: entonces NaN
        assert np.all((idx.head[found] >= 0) & (idx.head[found] <= 1))
    path = tmp_path / "x.idx.npz"
    idx.save(path, frame_count=90)
    back = ClipIndex.load(path, frame_count=90)
    assert back is not None
    np.testing.assert_allclose(back.brightness, idx.brightness)
    np.testing.assert_array_equal(np.isnan(back.head), np.isnan(idx.head))


def test_brightness_follows_gain():
    dark = build_index(face_frames(30, lambda i: 0.0, "", gain=0.7), pixel_format="nv12", height=H, fps=FPS)
    bright = build_index(face_frames(30, lambda i: 0.0, "", gain=1.2), pixel_format="nv12", height=H, fps=FPS)
    assert float(bright.brightness.mean()) > float(dark.brightness.mean()) + 10


def test_old_index_version_is_rebuilt(frames_silent, tmp_path):
    idx = build_index(frames_silent, pixel_format="nv12", height=H, fps=FPS)
    path = tmp_path / "x.idx.npz"
    idx.save(path, frame_count=90)
    data = dict(np.load(path))
    data["version"] = np.int32(1)
    np.savez(path, **data)
    assert ClipIndex.load(path, frame_count=90) is None  # se recalculará


def test_head_interpolation_needs_enough_samples():
    assert np.isnan(_interpolate_head({}, 30)).all()
    head = _interpolate_head({0: (0.4, 0.5, 0.2), 30: (0.6, 0.5, 0.2)}, 31)
    assert head[15, 0] == pytest.approx(0.5, abs=1e-3)


# --------------------------------------------------------------------------- #
# Coste de entrada
# --------------------------------------------------------------------------- #


def _director(silent, speaking, gestures=None, **kw) -> Director:
    return Director(
        synth.cfg(), silent, speaking, gestures or {},
        gestures_cfg=GesturesConfig(cooldown_ms=0), clock=FakeClock(), seed=kw.pop("seed", 1), **kw,
    )


def test_entry_prefers_matching_head_position(frames_silent, frames_speaking):
    a, b = fresh("a", frames_silent), fresh("b", frames_silent)
    a.index.head[:] = (0.40, 0.5, 0.3)
    b.index.head[:] = (0.62, 0.5, 0.3)
    spk = fresh("habla", frames_speaking)
    query = np.asarray(a.index.descriptors[5])
    for seed in range(8):
        d = _director([a, b], [spk], seed=seed)
        clip, _ = d._choose_entry("silent", query, q_head=np.array([0.62, 0.5, 0.3], np.float32))
        assert clip is b
        clip, _ = d._choose_entry("silent", query, q_head=np.array([0.40, 0.5, 0.3], np.float32))
        assert clip is a


def test_entry_prefers_matching_brightness(frames_silent, frames_speaking):
    a, b = fresh("a", frames_silent), fresh("b", frames_silent)
    a.index.brightness[:] = 90.0
    b.index.brightness[:] = 140.0
    spk = fresh("habla", frames_speaking)
    query = np.asarray(a.index.descriptors[5])
    for seed in range(8):
        d = _director([a, b], [spk], seed=seed)
        assert d._choose_entry("silent", query, q_luma=138.0)[0] is b
        assert d._choose_entry("silent", query, q_luma=92.0)[0] is a


def test_missing_head_is_ignored(frames_silent, frames_speaking):
    a = fresh("a", frames_silent)  # head NaN o detectada: el término no rompe nada
    a.index.head[:] = np.nan
    d = _director([a], [fresh("habla", frames_speaking)])
    clip, pos = d._choose_entry("silent", np.asarray(a.index.descriptors[0]),
                                q_head=np.array([0.5, 0.5, 0.3], np.float32))
    assert clip is a and 0 <= pos < len(a)


def test_entry_costs_are_recorded(frames_silent, frames_speaking):
    clock = FakeClock()
    d = Director(synth.cfg(), [fresh("s", frames_silent)], [fresh("h", frames_speaking)], {},
                 clock=clock, seed=3)
    for voice in (SIL, TALK, SIL, TALK, SIL):
        for _ in range(3 * FPS):
            clock.advance(1.0 / FPS)
            d.step(voice)
    costs = list(d.stats.entry_costs)
    assert len(costs) >= 3
    assert all(c >= 0 for c in costs)
    # Mismas caras, misma luz: ningún cambio debería ser de riesgo.
    assert d.stats.high_cost_transitions == 0


def test_manual_anchors_override_detector(frames_silent, frames_speaking):
    a = DirectorClip("a", frames_silent, build_index(frames_silent, pixel_format="nv12", height=H, fps=FPS),
                     manual_anchors=(12, 40, 999))
    d = _director([a], [fresh("habla", frames_speaking)])
    anchors = d._anchors[id(a)]
    assert anchors.manual
    assert list(anchors.entries) == [12, 40]  # 999 no existe: se descarta


def test_speaking_action_does_not_wait_for_pause(frames_silent, frames_speaking):
    spk = fresh("habla", frames_speaking)
    one_shot = fresh("frase", frames_speaking[:30])
    gesture = fresh("saludar", synth.gesture_frames(30))
    d = _director([fresh("s", frames_silent)], [spk], {"frase": one_shot, "saludar": gesture},
                  speaking_actions={"frase"})
    clock = d.clock
    for _ in range(2 * FPS):
        clock.advance(1.0 / FPS)
        d.step(TALK)
    assert d.request_gesture("frase").accepted
    clock.advance(1.0 / FPS)
    d.step(TALK)
    assert d._change is None or not d._change.need_pause
    d2 = _director([fresh("s", frames_silent)], [spk], {"saludar": gesture})
    for _ in range(2 * FPS):
        d2.clock.advance(1.0 / FPS)
        d2.step(TALK)
    assert d2.request_gesture("saludar").accepted
    d2.clock.advance(1.0 / FPS)
    d2.step(TALK)
    assert d2._change is not None and d2._change.need_pause


# --------------------------------------------------------------------------- #
# Salida limpia: nada se dibuja sobre la imagen emitida
# --------------------------------------------------------------------------- #


def test_motor_sin_rotulo():
    import camvoice.video.engine as engine

    assert not hasattr(engine, "identify_prerecorded")
    assert not hasattr(engine, "IDENTIFY_TEXT")


def test_config_sin_opcion_de_rotulo_y_claves_antiguas_ignoradas():
    from dataclasses import fields

    from camvoice.config import AppConfig, OutputConfig

    nombres = {f.name for f in fields(OutputConfig)}
    assert "identify_prerecorded" not in nombres and "prerecorded_label" not in nombres
    cfg = AppConfig.from_dict({"output": {"identify_prerecorded": True, "prerecorded_label": True}})
    assert not hasattr(cfg.output, "identify_prerecorded")


def test_cli_rechaza_label():
    from camvoice.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--label"])


def test_sin_dibujo_en_la_ruta_de_salida():
    """Ningún módulo de `camvoice/video/` dibuja texto o figuras en la imagen."""
    from pathlib import Path

    import camvoice.video as pkg

    prohibido = ("putText", "rectangle(", "line(", "circle(", "ellipse(", "polylines(",
                 "fillPoly(", "drawContours(", "arrowedLine(", "copyMakeBorder")
    hallados = []
    for py in Path(pkg.__file__).parent.glob("*.py"):
        texto = py.read_text(encoding="utf-8")
        hallados += [f"{py.name}: {p}" for p in prohibido if f"cv2.{p}" in texto]
    assert hallados == []


def test_fotograma_emitido_es_el_compuesto():
    """El bucle del motor emite exactamente lo que compone el director/compositor
    (con efectos neutros): ningún píxel cambia entre composición y salida."""
    import inspect

    import camvoice.video.engine as engine

    src = inspect.getsource(engine)
    i = src.index("frame = self._apply_effects(frame)")
    tramo = src[i:src.index("self._emit(frame)", i)]
    assert "frame =" not in tramo.split("\n", 1)[1], "no debe modificarse el fotograma tras los efectos"
