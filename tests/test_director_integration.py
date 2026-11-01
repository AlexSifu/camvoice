"""Integración: sesión en modo director con archivos reales, motor y soak corto.

Usa los clips de `make_test_library` (archivos MP4 en tmp_path) y la caché
real; sin micrófono ni cámara virtual (voz sintética y NullSink).
"""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from camvoice.audio.gate import VoiceState
from camvoice.config import AppConfig, VideoConfig
from camvoice.controller import Session, SessionError
from camvoice.output.sink import NullSink
from camvoice.soak import VoiceScript, run_soak, simulate
from camvoice.testclips import make_test_library
from camvoice.video.director import Director, DirectorState, VoiceInput
from camvoice.video.engine import RenderEngine

W, H, FPS = 160, 120, 10


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    out = tmp_path_factory.mktemp("lib")
    return make_test_library(out, width=W, height=H, fps=FPS, seconds=4.0,
                             gestures=("saludar", "asentir"))


def app_cfg(lib, **video) -> AppConfig:
    cfg = AppConfig()
    v = cfg.video
    v.speaking_clip = str(lib["speaking"])
    v.silent_clip = str(lib["silent"])
    v.library_manifest = str(lib["manifest"])
    v.width, v.height, v.fps = W, H, FPS
    v.seed = 99
    for k, val in video.items():
        setattr(v, k, val)
    cfg.output.enabled = False
    return cfg


def test_prepare_director_mode_builds_pools_and_gestures(lib):
    s = Session(app_cfg(lib))
    try:
        clips = s.prepare()
        assert clips.mode == "director"
        assert [c.name for c in clips.silent_pool] == ["silencio-prueba", "silencio-2-prueba"]
        assert len(clips.speaking_pool) == 1
        assert set(clips.gestures) == {"saludar", "asentir"}
        assert clips.speaking_pool[0].index.mouth_range > 0.1
        assert any("Gestos sin clip" in w for w in clips.warnings)
    finally:
        s.close()


def test_silent_main_clip_keeps_legacy_cache_key(lib):
    """El clip de silencio principal (referencia) usa la misma caché que el modo clásico."""
    classic = Session(app_cfg(lib, playback="classic"))
    a = classic.prepare().silent.raw_path
    classic.close()
    director = Session(app_cfg(lib))
    b = director.prepare().silent.raw_path
    director.close()
    assert a == b


def test_second_prepare_reuses_cache_and_index(lib):
    s = Session(app_cfg(lib))
    s.prepare()
    s.close()
    t0 = time.perf_counter()
    msgs = []
    s2 = Session(app_cfg(lib))
    s2.prepare(progress=lambda d, t, m: msgs.append(m))
    s2.close()
    assert any("reutilizada" in m for m in msgs)
    assert not any("Analizando" in m for m in msgs)  # índices cargados de disco
    assert time.perf_counter() - t0 < 30


def test_corrupt_gesture_clip_is_skipped_with_warning(lib, tmp_path):
    import json

    bad = tmp_path / "roto.mp4"
    bad.write_bytes(b"esto no es video")
    manifest = json.loads(lib["manifest"].read_text(encoding="utf-8"))
    manifest["gestures"]["negar"] = {"clip": str(bad)}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    s = Session(app_cfg(lib, library_manifest=str(path)))
    try:
        clips = s.prepare()
        assert clips.mode == "director"
        assert "negar" not in clips.gestures
        assert any("roto.mp4" in w for w in clips.warnings)
    finally:
        s.close()


def test_different_fps_source_is_resampled(lib, tmp_path):
    """Un clip extra grabado a otros FPS se adapta a los FPS de salida."""
    import json

    from camvoice.testclips import make_test_clips

    other_dir = tmp_path / "otro"
    _, sil25 = make_test_clips(other_dir, width=W, height=H, fps=25, seconds=2.0)
    manifest = json.loads(lib["manifest"].read_text(encoding="utf-8"))
    manifest["silent"].append(str(sil25))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    s = Session(app_cfg(lib, library_manifest=str(path)))
    try:
        clips = s.prepare()
        extra = clips.silent_pool[-1]
        assert extra.index.frame_count == pytest.approx(2.0 * FPS, abs=2)
    finally:
        s.close()


def test_director_failure_falls_back_to_classic(lib, monkeypatch):
    import camvoice.controller as ctrl

    def boom(*a, **k):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(ctrl, "load_library", boom)
    s = Session(app_cfg(lib))
    try:
        clips = s.prepare()
        assert clips.mode == "classic"
        assert any("modo clásico" in w for w in clips.warnings)
    finally:
        s.close()


def test_cancel_during_indexing(lib):
    s = Session(app_cfg(lib))
    with pytest.raises(SessionError, match="cancelada"):
        s.prepare(force=True, cancel=lambda: True)
    s.close()


def test_engine_with_director_gesture(lib):
    cfg = app_cfg(lib)
    s = Session(cfg)
    clips = s.prepare()
    director = Director(cfg.video, clips.silent_pool, clips.speaking_pool, clips.gestures,
                        gestures_cfg=cfg.gestures)
    voice = {"v": VoiceInput(VoiceState.SILENT, 0.0)}
    sink = NullSink()
    engine = RenderEngine(cfg, clips.speaking, clips.silent, lambda: voice["v"], sink=sink,
                          director=director)
    engine.start()
    try:
        time.sleep(0.5)
        assert engine.request_gesture("saludar").accepted
        seen = set()
        t_end = time.perf_counter() + 4.0
        while time.perf_counter() < t_end:
            st = engine.status()
            seen.add(st.state.value)
            if st.gesture_latency_ms >= 0:
                break
            time.sleep(0.02)
        voice["v"] = VoiceInput(VoiceState.SPEAKING, 0.7)
        time.sleep(1.5)
        st = engine.status()
    finally:
        engine.stop()
        s.close()
    assert st.mode == "director"
    assert not hasattr(st, "prerecorded_label")  # salida limpia: ya no hay rótulo
    assert st.gesture_latency_ms >= 0
    assert st.gesture_latency_max_ms >= st.gesture_latency_ms
    assert sink.frames > 10
    assert st.state in (DirectorState.SPEAKING, DirectorState.TRANSITION, DirectorState.GESTURE)
    assert "saludar" in st.gestures_available


def test_classic_engine_rejects_gestures_politely(lib):
    cfg = app_cfg(lib, playback="classic")
    s = Session(cfg)
    clips = s.prepare()
    engine = RenderEngine(cfg, clips.speaking, clips.silent, lambda: VoiceState.SILENT, sink=NullSink())
    r = engine.request_gesture("saludar")
    s.close()
    assert not r.accepted and "director" in r.message
    assert engine.available_gestures() == []


def test_output_is_clean_no_label_or_marks(lib):
    """Requisito: la imagen emitida no lleva rótulos, bordes ni marcas de agua.
    Sin efectos, cada fotograma emitido es exactamente el que da el director."""
    import camvoice.video.engine as engine_mod

    assert not hasattr(engine_mod, "draw_prerecorded_label")
    assert not hasattr(engine_mod, "LABEL_TEXT")
    cfg = app_cfg(lib)
    cfg.effects.enabled = False
    s = Session(cfg)
    clips = s.prepare()
    director = Director(cfg.video, clips.silent_pool, clips.speaking_pool, clips.gestures,
                        gestures_cfg=cfg.gestures)
    composed: list[np.ndarray] = []
    real_step = director.step

    def spy(voice):
        frame, info = real_step(voice)
        composed.append(np.array(frame, copy=True))
        return frame, info

    director.step = spy

    class Capture(NullSink):
        def __init__(self):
            super().__init__()
            self.got: list[np.ndarray] = []

        def send(self, frame):
            self.got.append(np.array(frame, copy=True))
            return super().send(frame)

    sink = Capture()
    engine = RenderEngine(cfg, clips.speaking, clips.silent,
                          lambda: VoiceInput(VoiceState.SILENT, 0.0), sink=sink, director=director)
    engine.start()
    try:
        time.sleep(0.8)
    finally:
        engine.stop()
        s.close()
    n = min(len(composed), len(sink.got))
    assert n > 3
    for a_, b_ in zip(composed[:n], sink.got[:n]):
        assert np.array_equal(a_, b_)


def test_engine_pause_freezes_output_and_never_black(lib):
    cfg = app_cfg(lib)
    s = Session(cfg)
    s.prepare()
    s.start(sink=NullSink(), voice_source=lambda: VoiceInput(VoiceState.SILENT, 0.0))
    try:
        time.sleep(0.5)
        assert s.toggle_video_pause() is True
        time.sleep(0.4)
        st = s.status().render
        assert st.video_paused and st.state is DirectorState.VIDEO_PAUSED
        assert s.video_paused
        assert not s.request_gesture("saludar").accepted
        assert s.toggle_video_pause() is False
        time.sleep(0.6)
        st = s.status().render
        assert not st.video_paused
        assert st.state is not DirectorState.VIDEO_PAUSED
        assert st.discontinuities == 0
    finally:
        s.stop()
        s.close()


def test_session_start_with_synthetic_voice_needs_no_microphone(lib):
    cfg = app_cfg(lib)
    s = Session(cfg)
    s.prepare()
    s.start(sink=NullSink(), voice_source=lambda: VoiceInput(VoiceState.SPEAKING, 0.5))
    try:
        time.sleep(1.0)
        st = s.status()
        assert st.render.frames_sent > 3
        assert s.request_gesture("asentir").accepted
    finally:
        s.stop()
        s.close()
    assert s.request_gesture("asentir").accepted is False


# --------------------------------------------------------------------------- #
# Soak reducido
# --------------------------------------------------------------------------- #


def test_voice_script_is_deterministic_and_realistic():
    a, b = VoiceScript(3, 3600), VoiceScript(3, 3600)
    samples = [a.at(t * 0.1) for t in range(36000)]
    assert samples == [b.at(t * 0.1) for t in range(36000)]
    frac = a.speech_fraction(3600)
    assert 0.15 < frac < 0.6
    speaking = [s for s in samples if s.state is VoiceState.SPEAKING]
    assert any(s.mouth == 0.0 for s in speaking)  # pausas dentro de la frase
    assert max(s.mouth for s in speaking) > 0.8
    # Hay respuestas cortas («sí») de menos de un segundo.
    short = [seg for seg in a._segments if seg[2] and seg[1] - seg[0] < 1.0]
    assert short


def test_simulated_long_meeting_has_no_stuck_gestures(lib):
    cfg = app_cfg(lib)
    s = Session(cfg)
    clips = s.prepare()

    def make(clock):
        return Director(cfg.video, clips.silent_pool, clips.speaking_pool, clips.gestures,
                        gestures_cfg=cfg.gestures, clock=clock, seed=5)

    res = simulate(make, 1200.0, fps=FPS, gesture_every_s=30.0, seed=5)
    s.close()
    assert res.steps == 1200 * FPS
    assert res.gestures_requested >= 30
    assert res.gestures_stuck == 0
    assert res.gestures_played >= res.gestures_requested * 0.8
    assert res.state_steps.get("SILENT", 0) > 0 and res.state_steps.get("SPEAKING", 0) > 0
    assert res.longest_frozen_s < 1.0
    assert res.discontinuities == 0
    assert res.jumps == 0
    assert "Reunión simulada" in res.render()


def test_simulated_meeting_with_pauses_freezes_and_resumes(lib):
    cfg = app_cfg(lib)
    s = Session(cfg)
    clips = s.prepare()

    def make(clock):
        return Director(cfg.video, clips.silent_pool, clips.speaking_pool, clips.gestures,
                        gestures_cfg=cfg.gestures, clock=clock, seed=5)

    res = simulate(make, 600.0, fps=FPS, gesture_every_s=20.0, seed=5,
                   pause_every_s=60.0, pause_len_s=4.0)
    s.close()
    assert res.pauses >= 9
    assert res.paused_frames >= res.pauses * 4 * FPS * 0.9
    assert res.pause_frame_changes == 0
    assert res.black_frames == 0
    assert res.discontinuities == 0
    assert res.gestures_stuck == 0
    assert "Pausas manuales" in res.render()


def test_realtime_soak_short(lib):
    cfg = app_cfg(lib)
    s = Session(cfg)
    s.prepare()
    try:
        res = run_soak(s, 4.0, gesture_every_s=1.0, seed=2)
    finally:
        s.close()
    assert res.mode == "director"
    assert res.frames_sent >= 4.0 * FPS * 0.7
    assert res.gestures_requested >= 2
    assert not res.errors
    assert res.discontinuities == 0
    assert "Fotogramas emitidos" in res.render()


def test_cv2_available():
    assert cv2.__version__
