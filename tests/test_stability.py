"""Estabilidad: máquina de estados, continuidad por anclas, micro-pausas,
gestos hablando, peticiones simultáneas, pausa manual y migración v1 -> v2.

Todo con clips sintéticos (tests/synth.py) y reloj inyectado: sin cámara ni
micrófono. Los tiempos se cuentan en fotogramas a 15 FPS.
"""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from camvoice.audio.features import MicroPauseDetector
from camvoice.audio.gate import VoiceState
from camvoice.config import CONFIG_VERSION, AppConfig, GesturesConfig
from camvoice.gestures import (
    PAUSE,
    base_gesture,
    gesture_label,
    is_known_gesture,
    speaking_variant,
)
from camvoice.video.director import Director, DirectorState, VoiceInput
from camvoice.video.library import Library

from . import synth
from .synth import FPS, H, FakeClock, standard_set

SIL = VoiceInput(VoiceState.SILENT, 0.0)
TALK = VoiceInput(VoiceState.SPEAKING, 0.7)
MICRO = VoiceInput(VoiceState.SPEAKING, 0.0, pause=True)

_variant_cache: dict = {}


def variant_clip():
    if "v" not in _variant_cache:
        _variant_cache["v"] = synth.director_clip("saludar_hablando", synth.gesture_frames(30, "v"))
    return _variant_cache["v"]


def make(*, clock=None, variant=False, seed=1234, gcfg=None, **cfg_kw) -> Director:
    sil1, sil2, spk, ges = standard_set()
    gestures = dict(ges)
    if variant:
        gestures["saludar_hablando"] = variant_clip()
    return Director(
        synth.cfg(**cfg_kw),
        [sil1, sil2],
        [spk],
        gestures,
        gestures_cfg=gcfg or GesturesConfig(cooldown_ms=0, auto_nod=False),
        clock=clock or FakeClock(),
        seed=seed,
    )


class Tracker:
    """Comprueba la regla de continuidad en cada fotograma emitido."""

    def __init__(self, d: Director) -> None:
        self.d = d
        self.last = None
        self.infos = []
        self.frames = []

    def step(self, voice, clock: FakeClock | None = None, keep=False):
        if clock is not None:
            clock.advance(1.0 / FPS)
        frame, info = self.d.step(voice)
        assert frame.shape == (H * 3 // 2, synth.W)
        assert not info.jumped
        stable = info.state in (DirectorState.SILENT, DirectorState.SPEAKING, DirectorState.GESTURE)
        if stable and not info.blended:
            key = (info.clip, info.frame_index)
            if self.last is not None and self.last[0] == key[0]:
                assert key[1] == self.last[1] + 1, (self.last, key)
            self.last = key
        else:
            self.last = None
        self.infos.append(info)
        if keep:
            self.frames.append(frame.copy())
        return frame, info

    def run(self, voice, n, clock=None, keep=False):
        for _ in range(n):
            self.step(voice, clock, keep)
        return self.infos[-n:]


def until(t: Tracker, voice, pred, limit=200, clock=None) -> int:
    for i in range(limit):
        _, info = t.step(voice, clock)
        if pred(info):
            return i + 1
    raise AssertionError("condición no alcanzada")


# --------------------------------------------------------------------------- #
# Escenarios de voz
# --------------------------------------------------------------------------- #


def test_short_interventions_never_jump():
    """Respuestas cortas («sí», «vale») de 0,3-0,9 s separadas por silencios."""
    d = make()
    t = Tracker(d)
    rng = np.random.default_rng(1)
    for _ in range(60):
        t.run(TALK, int(rng.integers(4, 14)))
        t.run(SIL, int(rng.integers(8, 40)))
    assert d.stats.discontinuities == 0
    assert d.stats.jumps == 0
    assert d.stats.anchor_transitions + d.stats.fallback_transitions == d.stats.transitions
    until(t, SIL, lambda i: i.state is DirectorState.SILENT)


def test_continuous_speech_plays_consecutively_and_rotates_at_anchors():
    d = make()
    t = Tracker(d)
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING)
    infos = t.run(TALK, 60 * FPS)
    assert d.stats.discontinuities == 0
    assert d.stats.segment_changes >= 3  # el clip de 12 s se renueva a lo largo de 1 min
    speaking = sum(i.state is DirectorState.SPEAKING for i in infos)
    assert speaking / len(infos) > 0.85
    # La mayoría de cambios ocurren en un fotograma ancla (no por plazo vencido).
    assert d.stats.anchor_transitions >= d.stats.fallback_transitions


def test_micro_pauses_go_to_closed_mouth_without_leaving_speech_gate():
    d = make()
    t = Tracker(d)
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING)
    t.run(TALK, 30)
    n = until(t, MICRO, lambda i: i.state is DirectorState.SILENT and d.cur.kind == "silent")
    # Espera al ancla (≤ 180 ms) + transición (≤ 400 ms).
    assert n <= round(0.18 * FPS) + round(0.4 * FPS) + 1
    mouths = [float(d.cur.clip.index.mouth[i.frame_index]) for i in t.run(MICRO, 20) if not i.blended]
    assert np.mean(mouths) < 0.35
    # Y vuelve a hablar al terminar la micro-pausa.
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING)
    assert d.stats.discontinuities == 0


def test_voice_change_waits_at_most_anchor_wait_then_transitions():
    d = make()
    t = Tracker(d)
    t.run(SIL, 10)
    n = until(t, TALK, lambda i: i.state is DirectorState.TRANSITION)
    assert n <= round(0.18 * FPS)  # 180 ms -> 3 fotogramas


def test_voice_flicker_shorter_than_anchor_wait_does_not_switch_group():
    d = make()
    t = Tracker(d)
    t.run(SIL, 20)
    before = d.stats.transitions
    # Un solo fotograma de voz: el cambio pendiente se anula si la voz vuelve
    # antes de llegar a un ancla (y en silencio casi todo es ancla, así que a
    # lo sumo arranca un fundido que se reorienta).
    t.step(TALK)
    t.run(SIL, 30)
    assert d.cur.kind == "silent"
    assert d.stats.transitions - before <= 2


# --------------------------------------------------------------------------- #
# Gestos hablando y peticiones simultáneas
# --------------------------------------------------------------------------- #


def test_gesture_during_speech_uses_speaking_variant():
    clock = FakeClock()
    d = make(clock=clock, variant=True)
    t = Tracker(d)
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING, clock=clock)
    assert d.request_gesture("saludar").accepted
    until(t, TALK, lambda i: i.state is DirectorState.GESTURE, clock=clock)
    assert d.cur.clip.name == "saludar_hablando"
    assert d.stats.speaking_variants == 1
    assert max(d.stats.gesture_waits_ms) <= 200.0
    # Al terminar sigue hablando.
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING, clock=clock)
    assert d.stats.gestures_played == 1
    assert d.stats.discontinuities == 0


def test_gesture_during_speech_without_variant_waits_at_most_200ms():
    clock = FakeClock()
    d = make(clock=clock)
    t = Tracker(d)
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING, clock=clock)
    t.run(TALK, 7, clock)
    assert d.request_gesture("saludar").accepted
    n = until(t, TALK, lambda i: i.state in (DirectorState.TRANSITION, DirectorState.GESTURE)
              and d.cur.kind == "gesture", clock=clock)
    assert n <= round(0.2 * FPS)
    assert d.cur.clip.name == "saludar"
    assert d.stats.speaking_variants == 0
    assert max(d.stats.gesture_waits_ms) <= 200.0


def test_gesture_during_speech_starts_at_micro_pause_if_one_comes():
    clock = FakeClock()
    d = make(clock=clock)
    t = Tracker(d)
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING, clock=clock)
    assert d.request_gesture("asentir").accepted
    t.step(TALK, clock)  # aún hablando: espera
    assert d.state is DirectorState.SPEAKING
    _, info = t.step(MICRO, clock)  # micro-pausa: arranca ya
    assert d.cur.kind == "gesture"
    assert info.state is DirectorState.TRANSITION


def test_simultaneous_requests_keep_only_the_latest():
    d = make()
    t = Tracker(d)
    t.run(SIL, 5)
    assert d.request_gesture("saludar").accepted
    r = d.request_gesture("asentir")
    assert r.accepted and "sustituye" in r.message
    until(t, SIL, lambda i: i.state is DirectorState.GESTURE)
    assert d.cur.clip.name == "asentir"
    until(t, SIL, lambda i: i.state is DirectorState.SILENT)
    assert d.stats.gestures_played == 1


def test_requests_from_many_threads_are_safe_and_deterministic_in_outcome():
    d = make()
    t = Tracker(d)
    t.run(SIL, 3)
    barrier = threading.Barrier(8)

    def worker(k):
        barrier.wait()
        for i in range(50):
            d.request_gesture("saludar" if (i + k) % 2 else "asentir")
            if i % 17 == 0:
                d.cancel_gesture()
            if i % 23 == 0:
                d.toggle_video_pause()

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for th in threads:
        th.start()
    for _ in range(300):
        t.step(TALK if (len(t.infos) // 30) % 2 else SIL)
    for th in threads:
        th.join()
    d.resume_video()
    d.cancel_gesture()
    until(t, SIL, lambda i: i.state is DirectorState.SILENT, limit=400)
    assert d.stats.discontinuities == 0


def test_request_during_gesture_runs_after_it_without_passing_through_pool():
    d = make()
    t = Tracker(d)
    t.run(SIL, 5)
    d.request_gesture("saludar")
    until(t, SIL, lambda i: i.state is DirectorState.GESTURE)
    d.request_gesture("asentir")
    names = []
    for _ in range(80):
        _, info = t.step(SIL)
        if info.state is DirectorState.GESTURE:
            names.append(d.cur.clip.name)
        if info.state is DirectorState.SILENT and "asentir" in names:
            break
    assert "asentir" in names
    assert d.stats.gestures_played == 2


# --------------------------------------------------------------------------- #
# Transiciones interrumpidas
# --------------------------------------------------------------------------- #


def test_cancel_during_transition_to_gesture_interrupts_smoothly():
    d = make()
    t = Tracker(d)
    t.run(SIL, 5)
    d.request_gesture("saludar")
    until(t, SIL, lambda i: i.state is DirectorState.TRANSITION and d.cur.kind == "gesture")
    before = d.stats.interrupted_transitions
    d.cancel_gesture()
    _, info = t.step(SIL)
    assert info.state is DirectorState.TRANSITION and d.cur.kind == "silent"
    assert d.stats.interrupted_transitions == before + 1
    until(t, SIL, lambda i: i.state is DirectorState.SILENT)
    assert d.stats.gestures_played == 0


def test_voice_reversal_midway_retargets_from_shown_image():
    d = make()
    t = Tracker(d)
    t.run(SIL, 10)
    until(t, TALK, lambda i: i.state is DirectorState.TRANSITION)
    before = d.stats.interrupted_transitions
    frame_before = t.d._last_good.copy()
    frame, info = t.step(SIL)
    assert info.state is DirectorState.TRANSITION
    assert d.stats.interrupted_transitions == before + 1
    # El primer fotograma del nuevo fundido parte de la imagen mostrada: el
    # cambio entre los dos fotogramas es pequeño (no hay corte).
    diff = np.abs(frame.astype(np.int16) - frame_before.astype(np.int16)).mean()
    assert diff < 20
    until(t, SIL, lambda i: i.state is DirectorState.SILENT)


def test_every_transition_has_at_least_one_blended_frame():
    d = make()
    t = Tracker(d)
    rng = np.random.default_rng(5)
    for _ in range(40):
        t.run(TALK if rng.random() < 0.5 else SIL, int(rng.integers(3, 30)))
    states = [i.state for i in t.infos]
    for a, b, info in zip(states, states[1:], t.infos[1:]):
        if a is not DirectorState.TRANSITION and b is DirectorState.TRANSITION:
            assert info.blended


# --------------------------------------------------------------------------- #
# Pausa manual (VIDEO_PAUSED)
# --------------------------------------------------------------------------- #


def test_pause_freezes_last_valid_frame_and_resumes_to_anchor():
    d = make()
    t = Tracker(d)
    t.run(TALK, 40, keep=True)
    last = t.frames[-1]
    assert d.toggle_video_pause() is True
    assert d.video_paused
    for _ in range(30):
        frame, info = t.step(TALK)
        assert info.state is DirectorState.VIDEO_PAUSED and info.paused
        assert np.array_equal(frame, last)  # congelada, nunca negro
        assert frame[:H].mean() > 20
    r = d.request_gesture("saludar")
    assert not r.accepted and "pausa" in r.message
    assert d.toggle_video_pause() is False
    frame, info = t.step(TALK)
    assert info.state is DirectorState.TRANSITION and info.blended and info.anchor
    diff = np.abs(frame.astype(np.int16) - last.astype(np.int16)).mean()
    assert diff < 20  # reanuda con fundido desde la imagen congelada
    until(t, TALK, lambda i: i.state is DirectorState.SPEAKING)
    assert d.stats.pauses == 1
    assert d.stats.discontinuities == 0


def test_pause_cancels_running_gesture_and_pending_requests():
    d = make()
    t = Tracker(d)
    t.run(SIL, 5)
    d.request_gesture("saludar")
    until(t, SIL, lambda i: i.state is DirectorState.GESTURE)
    d.request_gesture("asentir")
    d.pause_video()
    _, info = t.step(SIL)
    assert info.state is DirectorState.VIDEO_PAUSED
    assert info.gesture == "" and info.pending_gesture == ""
    d.resume_video()
    until(t, SIL, lambda i: i.state is DirectorState.SILENT)
    assert d.stats.gestures_played == 0
    names = {i.clip for i in t.run(SIL, 60)}
    assert "asentir" not in names


def test_pause_before_first_frame_is_not_black():
    d = make()
    d.pause_video()
    frame, info = d.step(SIL)
    assert info.state is DirectorState.VIDEO_PAUSED
    assert frame[:H].mean() > 20


def test_pause_priority_over_degraded_and_gestures():
    """La pausa manda: mientras dura no se procesan gestos ni cambios."""
    d = make()
    t = Tracker(d)
    t.run(SIL, 5)
    d.pause_video()
    t.run(SIL, 3)
    before = (d.stats.transitions, d.stats.gestures_played)
    d.cancel_gesture()
    assert not d.request_gesture("saludar").accepted
    infos = t.run(TALK, 20)
    assert all(i.state is DirectorState.VIDEO_PAUSED for i in infos)
    assert (d.stats.transitions, d.stats.gestures_played) == before  # nada cambia en pausa


# --------------------------------------------------------------------------- #
# Ejecuciones largas
# --------------------------------------------------------------------------- #


def test_long_run_30_simulated_minutes_is_continuous_and_bounded():
    clock = FakeClock()
    d = make(clock=clock, variant=True, gcfg=GesturesConfig(cooldown_ms=800))
    t = Tracker(d)
    rng = np.random.default_rng(11)
    total = 30 * 60 * FPS
    i = 0
    while i < total:
        kind = rng.random()
        n = int(rng.integers(5, 15 * FPS))
        voice = TALK if kind < 0.4 else (MICRO if kind < 0.5 else SIL)
        for _ in range(n):
            clock.advance(1.0 / FPS)
            frame, info = d.step(voice)
            assert not info.jumped
        i += n
        if rng.random() < 0.15:
            d.request_gesture("saludar" if rng.random() < 0.5 else "asentir")
        if rng.random() < 0.02:
            d.toggle_video_pause()
    d.resume_video()
    until(t, SIL, lambda i: i.state is DirectorState.SILENT, limit=400, clock=clock)
    st = d.stats
    assert st.discontinuities == 0
    assert st.jumps == 0
    assert st.gestures_played > 10
    assert len(d._history) <= d.HISTORY
    assert len(st.gesture_latencies_ms) <= 100


def test_same_seed_same_sequence():
    def seq():
        d = make(seed=77)
        out = []
        for k in range(600):
            _, info = d.step(TALK if (k // 50) % 2 else SIL)
            out.append((info.clip, info.frame_index, info.state.value))
        return out

    assert seq() == seq()


# --------------------------------------------------------------------------- #
# Micro-pausas en el audio
# --------------------------------------------------------------------------- #


def test_micropause_detector_threshold_and_reset():
    det = MicroPauseDetector(frame_ms=20, release_ms=200)
    assert not any(det.update(False) for _ in range(9))  # 180 ms
    assert det.update(False)  # 200 ms
    assert det.update(False)
    assert not det.update(True)  # primera trama con voz
    assert not det.paused


@pytest.mark.parametrize("asked,expected", [(50, 150), (150, 150), (230, 230), (999, 250)])
def test_micropause_release_is_clamped_to_150_250(asked, expected):
    assert MicroPauseDetector(10, asked).release_ms == expected


def test_micropause_detector_rejects_bad_frame():
    with pytest.raises(ValueError):
        MicroPauseDetector(0)


# --------------------------------------------------------------------------- #
# Configuración: valores por defecto seguros y migración v1 -> v2
# --------------------------------------------------------------------------- #


def test_defaults_are_safe():
    cfg = AppConfig()
    assert cfg.version == CONFIG_VERSION == 2
    assert cfg.video.transition_morph == "off"
    assert 150 <= cfg.video.anchor_wait_ms <= 200
    assert 150 <= cfg.vad.micropause_release_ms <= 250
    assert cfg.gestures.pause_hotkey == "Ctrl+Alt+F11"
    assert not hasattr(cfg.output, "prerecorded_label")
    # Sin clips elegidos solo se quejan de los clips, no de los valores nuevos.
    assert all("clip" in e for e in cfg.validate())


def test_v1_config_is_migrated(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "version": 1,
        "video": {"transition_morph": "auto", "fps": 15, "anchor_wait_ms": 999},
        "output": {"prerecorded_label": True, "enabled": False},
        "vad": {"micropause_release_ms": 20},
    }), encoding="utf-8")
    cfg = AppConfig.load(path)
    assert cfg.version == 2
    assert cfg.video.transition_morph == "off"
    assert cfg.video.anchor_wait_ms == 200
    assert cfg.vad.micropause_release_ms == 150
    assert cfg.output.enabled is False
    assert not hasattr(cfg.output, "prerecorded_label")


def test_v2_config_keeps_explicit_morph_auto(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2, "video": {"transition_morph": "auto"}}), encoding="utf-8")
    assert AppConfig.load(path).video.transition_morph == "auto"


def test_invalid_new_values_are_dropped_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2, "video": {"anchor_wait_ms": "x"},
                                "vad": {"micropause_release_ms": None}}), encoding="utf-8")
    cfg = AppConfig.load(path)
    assert cfg.video.anchor_wait_ms == 180
    assert cfg.vad.micropause_release_ms == 200


# --------------------------------------------------------------------------- #
# Variantes `_hablando` y gesto reservado de pausa
# --------------------------------------------------------------------------- #


def test_speaking_variant_helpers():
    assert speaking_variant("saludar") == "saludar_hablando"
    assert base_gesture("saludar_hablando") == "saludar"
    assert is_known_gesture("saludar_hablando")
    assert not is_known_gesture("bailar_hablando")
    assert "hablando" in gesture_label("saludar_hablando")
    assert gesture_label(PAUSE)


def test_library_accepts_variants_without_hotkey(tmp_path):
    clip = tmp_path / "v.mp4"
    clip.write_bytes(b"x")
    lib = Library()
    lib.set_gesture("saludar", str(clip))
    lib.set_gesture("saludar_hablando", str(clip))
    assert "saludar_hablando" in lib.active_gestures()
    assert "saludar_hablando" not in lib.hotkeys()
    assert "saludar" in lib.hotkeys()
    again = Library.from_dict(lib.to_dict()) if hasattr(Library, "from_dict") else lib
    assert "saludar_hablando" in again.gestures


def test_variants_are_not_offered_as_gestures():
    d = make(variant=True)
    assert "saludar_hablando" not in d.available_gestures()
    assert not d.request_gesture("saludar_hablando").accepted


def test_cli_assigns_speaking_variant_without_hotkey(tmp_path, capsys):
    from camvoice import cli

    clip = tmp_path / "hola_hablando.mp4"
    clip.write_bytes(b"x")
    assert cli.main(["gestures", "--set", "saludar_hablando", str(clip)]) == 0
    capsys.readouterr()
    assert cli.main(["gestures", "--set", "saludar_hablando", str(clip), "--hotkey", "Ctrl+Alt+F9"]) == 1
    assert "atajo propio" in capsys.readouterr().out
    assert cli.main(["gestures", "--set", "bailar_hablando", str(clip)]) == 1
    cli.main(["gestures"])
    out = capsys.readouterr().out
    assert "Variantes para usar mientras hablas" in out
    assert "Pausar / reanudar video: Ctrl+Alt+F11" in out
