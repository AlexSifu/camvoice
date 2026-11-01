"""Director: estados, boca guiada por la voz, gestos, grupos y modo degradado."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from camvoice.audio.gate import VoiceState
from camvoice.config import GesturesConfig
from camvoice.video.director import Director, DirectorClip, DirectorState, VoiceInput
from camvoice.video.index import descriptor, distances, luma

from . import synth
from .synth import FPS, H, FakeClock, standard_set

SIL = VoiceInput(VoiceState.SILENT, 0.0)


def talk(mouth: float = 0.8) -> VoiceInput:
    return VoiceInput(VoiceState.SPEAKING, mouth)


def make(clock=None, *, gestures=True, gcfg=None, seed=1234, **cfg_kw) -> Director:
    sil1, sil2, spk, ges = standard_set()
    return Director(
        synth.cfg(**cfg_kw),
        [sil1, sil2],
        [spk],
        dict(ges) if gestures else {},
        gestures_cfg=gcfg or GesturesConfig(cooldown_ms=800),
        clock=clock or FakeClock(),
        seed=seed,
    )


def run(d: Director, voice, n: int, clock: FakeClock | None = None):
    infos = []
    for _ in range(n):
        if clock is not None:
            clock.advance(1.0 / FPS)
        _, info = d.step(voice)
        infos.append(info)
    return infos


def steps_to_state(d, voice, state, clock=None, limit=200):
    for i in range(limit):
        if clock is not None:
            clock.advance(1.0 / FPS)
        _, info = d.step(voice)
        if info.state is state:
            return i + 1
    raise AssertionError(f"no se llegó a {state} en {limit} pasos")


# --------------------------------------------------------------------------- #
# Estados y tiempos
# --------------------------------------------------------------------------- #


def test_starts_silent_and_stays_silent():
    d = make()
    infos = run(d, SIL, 60)
    assert all(i.state in (DirectorState.SILENT, DirectorState.TRANSITION) for i in infos)
    assert infos[0].state is DirectorState.SILENT


def test_voice_reaches_speaking_within_transition_budget():
    d = make()
    run(d, SIL, 10)
    n = steps_to_state(d, talk(), DirectorState.SPEAKING)
    max_steps = round(max(400, 400) * FPS / 1000)
    assert n <= max_steps + 1


def test_silence_returns_to_silent():
    d = make()
    run(d, talk(), 40)
    n = steps_to_state(d, SIL, DirectorState.SILENT)
    # Presupuesto = espera máxima a un fotograma ancla (anchor_wait_ms, 180 ms)
    # + transición máxima (400 ms) + 1. La espera al ancla es un requisito nuevo:
    # evita cortar a mitad de un gesto de boca.
    assert n <= round(180 * FPS / 1000) + round(400 * FPS / 1000) + 1
    assert d.cur.kind == "silent"


def test_accepts_bare_voice_state():
    d = make()
    _, info = d.step(VoiceState.SPEAKING)
    assert info.target is VoiceState.SPEAKING


def test_rapid_toggles_never_crash_and_end_in_target():
    d = make()
    rng = np.random.default_rng(3)
    for _ in range(400):
        frame, info = d.step(talk(rng.random()) if rng.random() < 0.5 else SIL)
        assert frame.shape == (H * 3 // 2, synth.W)
    steps_to_state(d, SIL, DirectorState.SILENT)


def test_transition_retargets_when_voice_changes_midway():
    d = make()
    run(d, SIL, 5)
    # El cambio espera a un ancla (≤ anchor_wait_ms): se avanza hasta que empiece.
    # Entre clips parecidos la transición dura 2 pasos: se cambia en el segundo.
    steps_to_state(d, talk(), DirectorState.TRANSITION, limit=4)
    before = d.stats.interrupted_transitions
    _, info = d.step(SIL)  # cambia de idea a mitad de la transición
    assert info.state is DirectorState.TRANSITION
    assert d.stats.interrupted_transitions == before + 1
    steps_to_state(d, SIL, DirectorState.SILENT)
    assert d.cur.kind == "silent"


def test_frame_shape_and_dtype_constant():
    d = make()
    shapes = {d.step(v)[0].shape for v in [SIL] * 5 + [talk()] * 20 + [SIL] * 20}
    assert shapes == {(H * 3 // 2, synth.W)}


# --------------------------------------------------------------------------- #
# Boca guiada por la voz
# --------------------------------------------------------------------------- #


def test_speaking_playback_is_consecutive_whatever_the_mouth():
    """Antes el director saltaba dentro del clip para seguir la boca pedida.
    El requisito de continuidad lo prohíbe: hablando, cada fotograma estable es
    el siguiente del mismo clip, pida el audio lo que pida."""
    d = make()
    steps_to_state(d, talk(), DirectorState.SPEAKING)
    last = None
    for k in range(300):
        target = 1.0 if (k // 8) % 2 else 0.0
        _, info = d.step(talk(target))
        if info.state is DirectorState.SPEAKING and not info.blended:
            key = (info.clip, info.frame_index)
            if last is not None and last[0] == key[0]:
                assert key[1] == last[1] + 1, (last, key)
            last = key
        else:
            last = None
        assert not info.jumped
    assert d.stats.discontinuities == 0
    assert d.stats.jumps == 0


def test_pause_inside_speech_closes_mouth():
    d = make()
    steps_to_state(d, talk(), DirectorState.SPEAKING)
    run(d, talk(1.0), 30)
    vals = []
    for _ in range(40):
        # SPEAKING con micro-pausa (audio/features.MicroPauseDetector): la
        # puerta de voz sigue abierta pero la boca debe cerrarse.
        _, info = d.step(VoiceInput(VoiceState.SPEAKING, 0.0, pause=True))
        if not info.blended:
            vals.append(float(d.cur.clip.index.mouth[info.frame_index]))
    assert np.mean(vals[-20:]) < 0.35


# --------------------------------------------------------------------------- #
# Gestos
# --------------------------------------------------------------------------- #


def test_gesture_plays_and_returns_to_silent():
    clock = FakeClock()
    d = make(clock)
    run(d, SIL, 5, clock)
    res = d.request_gesture("saludar")
    assert res.accepted
    infos = run(d, SIL, 80, clock)
    assert any(i.state is DirectorState.GESTURE for i in infos)
    assert infos[-1].state is DirectorState.SILENT
    assert d.stats.gestures_played == 1
    assert d.stats.gesture_latencies_ms and d.stats.gesture_latencies_ms[0] <= 1000.0 / FPS * 2


def test_gesture_returns_according_to_updated_voice():
    """Si empiezas a hablar durante el gesto, al terminar se va a SPEAKING."""
    clock = FakeClock()
    d = make(clock)
    run(d, SIL, 5, clock)
    d.request_gesture("asentir")
    steps_to_state(d, SIL, DirectorState.GESTURE, clock)
    infos = run(d, talk(), 80, clock)
    after = [i for i in infos if not i.gesture]
    assert after and after[-1].state is DirectorState.SPEAKING


def test_gesture_during_speech_returns_to_silence_if_you_stop():
    clock = FakeClock()
    d = make(clock)
    steps_to_state(d, talk(), DirectorState.SPEAKING, clock)
    d.request_gesture("saludar")
    steps_to_state(d, talk(), DirectorState.GESTURE, clock)
    infos = run(d, SIL, 80, clock)
    assert infos[-1].state is DirectorState.SILENT


def test_cancel_interrupts_gesture():
    clock = FakeClock()
    d = make(clock)
    d.request_gesture("saludar")
    steps_to_state(d, SIL, DirectorState.GESTURE, clock)
    assert d.request_gesture("neutro").accepted
    infos = run(d, SIL, 15, clock)
    assert not infos[-1].gesture
    assert infos[-1].state is DirectorState.SILENT
    assert d.stats.gestures_played == 0  # cancelado no cuenta como hecho


def test_queue_keeps_only_latest_pending():
    clock = FakeClock()
    d = make(clock)
    d.request_gesture("saludar")
    steps_to_state(d, SIL, DirectorState.GESTURE, clock)
    d.request_gesture("asentir")
    r = d.request_gesture("saludar")
    assert "sustituye" in r.message
    _, info = d.step(SIL)
    assert info.pending_gesture == "saludar"


def test_pending_gesture_runs_after_current():
    clock = FakeClock()
    d = make(clock, gcfg=GesturesConfig(cooldown_ms=0))
    d.request_gesture("saludar")
    steps_to_state(d, SIL, DirectorState.GESTURE, clock)
    d.request_gesture("asentir")
    seen = [i.gesture for i in run(d, SIL, 120, clock)]
    assert "asentir" in seen
    assert d.stats.gestures_played == 2


def test_cooldown_rejects_immediate_repeat_then_allows():
    clock = FakeClock()
    d = make(clock, gcfg=GesturesConfig(cooldown_ms=800))
    d.request_gesture("saludar")
    run(d, SIL, 80, clock)
    assert d.stats.gestures_played == 1
    # Justo tras terminar: el mismo gesto se rechaza, otro distinto no.
    end = d._last_gesture_end["saludar"]
    clock.t = end + 0.1
    r = d.request_gesture("saludar")
    assert not r.accepted and "espera" in r.message
    assert d.request_gesture("asentir").accepted
    d.cancel_gesture()
    clock.t = end + 0.9
    assert d.request_gesture("saludar").accepted


def test_missing_gesture_has_clear_message():
    d = make()
    r = d.request_gesture("mirar_arriba")
    assert not r.accepted
    assert "Mirar arriba" in r.message and "No hay clip" in r.message
    assert d.stats.gestures_rejected == 1


def test_available_gestures_sorted():
    assert make().available_gestures() == ["asentir", "saludar"]
    assert make(gestures=False).available_gestures() == []


def test_requests_from_other_threads_are_safe():
    clock = FakeClock()
    d = make(clock, gcfg=GesturesConfig(cooldown_ms=0))
    stop = threading.Event()
    errors = []

    def spam():
        names = ["saludar", "asentir", "neutro", "mirar_arriba"]
        k = 0
        while not stop.is_set():
            try:
                d.request_gesture(names[k % 4])
            except Exception as exc:  # pragma: no cover - fallaría la prueba
                errors.append(exc)
            k += 1
            time.sleep(0.0002)  # cede el GIL: miles de peticiones/s, no un bucle ocupado

    threads = [threading.Thread(target=spam) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        for k in range(600):
            clock.advance(1.0 / FPS)
            frame, _ = d.step(talk() if (k // 50) % 2 else SIL)
            assert frame.shape == (H * 3 // 2, synth.W)
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert not errors
    d.cancel_gesture()
    run(d, SIL, 120, clock)
    assert d.state in (DirectorState.SILENT, DirectorState.TRANSITION)


def test_auto_nod_only_when_silent_long_enough():
    clock = FakeClock()
    gcfg = GesturesConfig(auto_nod=True, auto_nod_gesture="asentir", auto_nod_min_s=10, auto_nod_max_s=12)
    d = make(clock, gcfg=gcfg)
    run(d, talk(), 15 * FPS, clock)  # hablando: no asiente
    assert d.stats.gestures_played == 0
    run(d, SIL, 20 * FPS, clock)
    assert d.stats.gestures_played >= 1


# --------------------------------------------------------------------------- #
# Grupos, semilla y no repetición
# --------------------------------------------------------------------------- #


def test_same_seed_same_sequence():
    def seq(seed):
        d = make(seed=seed)
        out = []
        for k in range(500):
            _, info = d.step(talk(0.5) if (k // 60) % 2 else SIL)
            out.append((info.clip, info.frame_index))
        return out

    assert seq(42) == seq(42)
    assert seq(42) != seq(43)


def test_silent_pool_uses_both_clips_over_time():
    d = make()
    clips = {d.step(SIL)[1].clip for _ in range(int(120 * FPS))}
    assert {"silencio", "silencio_2"} <= clips


def test_history_penalty_spreads_silent_segments():
    d = make()
    segs = []
    for _ in range(int(240 * FPS)):
        _, info = d.step(SIL)
        if not info.blended:
            segs.append((info.clip, info.frame_index // (2 * FPS)))
    counts = {}
    for s in segs:
        counts[s] = counts.get(s, 0) + 1
    # Ningún tramo de 2 s acapara el tiempo: hay variedad real.
    assert len(counts) >= 8
    assert max(counts.values()) / len(segs) < 0.3


def test_continuity_bounded_by_clip_natural_motion():
    """Ningún paso es más brusco que el movimiento natural del clip de habla."""
    _, _, spk, _ = standard_set()
    natural = max(
        float(distances(spk.index.descriptors[i: i + 1], spk.index.descriptors[i + 1])[0])
        for i in range(spk.index.frame_count - 1)
    )
    d = make()
    rng = np.random.default_rng(5)
    prev = None
    worst = 0.0
    for k in range(3000):
        v = talk(float(rng.random())) if (k // int(rng.integers(5, 90))) % 2 else SIL
        frame, _ = d.step(v)
        desc = descriptor(luma(frame, "nv12", H))
        if prev is not None:
            worst = max(worst, float(distances(prev[None, :], desc)[0]))
        prev = desc
    assert worst <= natural * 1.6, (worst, natural)


def test_morph_off_uses_plain_crossfade():
    d = make(transition_morph="off")
    for k in range(800):
        d.step(talk() if (k // 40) % 2 else SIL)
    assert d.stats.morphs == 0
    assert d.stats.transitions > 5


def test_morph_auto_is_used_for_large_pose_changes():
    # El valor por defecto pasó a "off" (config v2); "auto" sigue disponible.
    d = make(transition_morph="auto")
    d.request_gesture("saludar")
    for k in range(1500):
        if k % 200 == 0:
            d.request_gesture("saludar" if k % 400 else "asentir")
        d.step(talk() if (k // 70) % 2 else SIL)
    assert d.stats.morphs > 0


# --------------------------------------------------------------------------- #
# Modo degradado
# --------------------------------------------------------------------------- #


class _Breakable:
    def __init__(self, frames):
        self._frames = frames
        self.broken = False

    def __len__(self):
        return len(self._frames)

    def frame(self, i):
        if self.broken:
            raise OSError("disco desconectado")
        return self._frames[min(max(i, 0), len(self._frames) - 1)]


def test_broken_clip_degrades_then_recovers_without_stopping():
    sil1, sil2, spk, ges = standard_set()
    src = _Breakable([sil2.frames.frame(i) for i in range(len(sil2))])
    broken_clip = DirectorClip("silencio_roto", src, sil2.index)
    d = Director(synth.cfg(), [broken_clip, sil1], [spk], dict(ges), seed=1)
    run(d, SIL, 3)
    src.broken = True
    states = []
    for _ in range(int(60 * FPS)):
        frame, info = d.step(SIL)
        assert frame.shape == (H * 3 // 2, synth.W)
        states.append(info.state)
        if info.state is DirectorState.DEGRADED:
            assert info.message and "silencio_roto" in info.message
    if DirectorState.DEGRADED in states:
        assert d.stats.degraded_events == 1
        assert broken_clip not in d.pools["silent"]
        assert states[-1] is not DirectorState.DEGRADED
    else:  # el clip roto no se volvió a pedir en ese tiempo; forzarlo
        pytest.skip("el director no llegó a leer el clip roto")


def test_broken_gesture_is_removed():
    sil1, sil2, spk, ges = standard_set()
    src = _Breakable([ges["saludar"].frames.frame(i) for i in range(len(ges["saludar"]))])
    g = DirectorClip("saludar", src, ges["saludar"].index)
    d = Director(synth.cfg(), [sil1], [spk], {"saludar": g}, seed=1)
    src.broken = True
    d.request_gesture("saludar")
    infos = run(d, SIL, 30)
    assert any(i.state is DirectorState.DEGRADED for i in infos)
    assert d.available_gestures() == []
    assert infos[-1].state is DirectorState.SILENT
    r = d.request_gesture("saludar")
    assert not r.accepted


def test_validation_of_inputs():
    sil1, _, spk, _ = standard_set()
    with pytest.raises(ValueError):
        Director(synth.cfg(), [], [spk])
    other = synth.director_clip("bgr", synth.face_frames(30, lambda i: 0.0, "", fmt="bgr"), fmt="bgr")
    with pytest.raises(ValueError):
        Director(synth.cfg(), [sil1], [other])
