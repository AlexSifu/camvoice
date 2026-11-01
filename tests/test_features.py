"""Envolvente de boca a partir de la energía de la voz (audio/features.py)."""

from __future__ import annotations

import math

import pytest

from camvoice.audio.features import MouthEnvelope, MouthEnvelopeConfig

FLOOR = -60.0


def run(env: MouthEnvelope, frames: list[tuple[float, bool]]) -> list[float]:
    return [env.update(db, FLOOR, voiced) for db, voiced in frames]


def test_silence_keeps_mouth_closed():
    env = MouthEnvelope(20)
    assert max(run(env, [(-60.0, False)] * 100)) == 0.0


def test_loud_voice_opens_and_quiet_voice_opens_less():
    loud = run(MouthEnvelope(20), [(-20.0, True)] * 30)[-1]
    quiet = run(MouthEnvelope(20), [(-45.0, True)] * 30)[-1]
    assert loud > 0.9
    assert 0.0 < quiet < loud


def test_noise_without_voice_flag_does_not_open():
    # Ruido fuerte que el VAD clasifica como no-voz: la boca no se mueve.
    env = MouthEnvelope(20)
    assert max(run(env, [(-25.0, False)] * 50)) == 0.0


def test_below_onset_stays_closed():
    cfg = MouthEnvelopeConfig(onset_db=6.0)
    env = MouthEnvelope(20, cfg)
    assert max(run(env, [(FLOOR + 5.0, True)] * 50)) == 0.0


def test_rise_is_faster_than_fall():
    env = MouthEnvelope(20)
    up = run(env, [(-20.0, True)] * 3)
    # Tras 60 ms de voz fuerte ya está bastante abierta (subida ~25 ms).
    assert up[-1] > 0.8
    down = run(env, [(-60.0, False)] * 3)
    # Tras 60 ms de silencio sigue abierta en parte (caída ~90 ms): no parpadea.
    assert down[-1] > 0.3
    tail = run(env, [(-60.0, False)] * 40)
    assert tail[-1] == 0.0


def test_short_si_produces_brief_opening():
    """Un «sí» de 200 ms abre y cierra la boca en menos de medio segundo."""
    env = MouthEnvelope(20)
    values = run(env, [(-60.0, False)] * 5 + [(-25.0, True)] * 10 + [(-60.0, False)] * 25)
    assert max(values) > 0.6
    assert values[-1] < 0.05


def test_syllables_modulate_the_mouth():
    env = MouthEnvelope(20)
    frames = []
    for i in range(100):
        level = -25.0 + 12.0 * math.sin(2 * math.pi * 5.0 * i * 0.02)  # 5 sílabas/s
        frames.append((level, True))
    values = run(env, frames)[20:]
    assert max(values) - min(values) > 0.3


def test_non_finite_levels_are_safe_and_reset_works():
    env = MouthEnvelope(20)
    assert env.update(float("-inf"), FLOOR, True) == 0.0
    assert env.update(-20.0, float("nan"), True) == 0.0
    run(env, [(-20.0, True)] * 10)
    env.reset()
    assert env.value == 0.0


def test_invalid_frame_ms():
    with pytest.raises(ValueError):
        MouthEnvelope(0)
