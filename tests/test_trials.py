"""Ensayos de transición (`camvoice.video.trials`) sobre clips sintéticos.

Son pruebas SIMULADAS: caras dibujadas, reloj simulado, sin cámara ni
micrófono. Comprueban la mecánica del informe (qué par de fotogramas eligió el
director, cuánto esperó, si hubo reserva), no la calidad visual real.
"""

from __future__ import annotations

import math

import pytest

from camvoice.video.director import Director
from camvoice.video.trials import SCENARIOS, TrialSummary, measure, run_trial

from . import synth


@pytest.fixture(scope="module")
def clips():
    sil1, sil2, spk, ges = synth.standard_set()
    return [sil1, sil2], [spk], ges


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_cada_escenario_produce_un_informe_coherente(clips, scenario):
    silent, speaking, ges = clips
    cfg = synth.cfg()
    rep = run_trial(cfg, silent, speaking, ges, scenario, seed=3)
    assert rep.ok, rep.message
    assert rep.scenario == scenario
    assert rep.origin and rep.destination
    # La espera hasta el inicio del fundido nunca supera el límite (≤ 200 ms).
    assert 0.0 <= rep.wait_ms <= 200.0 + 1000.0 / cfg.fps
    assert rep.fade_ms > 0
    assert rep.cost is not None and math.isfinite(rep.cost)
    assert rep.high_cost == (rep.cost > Director.COST_THRESHOLD)
    assert rep.frames, "keep_frames=True debe devolver fotogramas para la vista previa"
    texto = rep.summary()
    assert SCENARIOS[scenario] in texto and "ms" in texto
    if scenario.startswith("gesto"):
        assert rep.gesture in ges


def test_hablar_callar_va_de_habla_a_silencio(clips):
    silent, speaking, ges = clips
    rep = run_trial(synth.cfg(), silent, speaking, ges, "hablar_callar", seed=5)
    assert rep.destination in {c.name for c in silent}
    rep = run_trial(synth.cfg(), silent, speaking, ges, "callar_hablar", seed=5)
    assert rep.destination in {c.name for c in speaking}


def test_escenario_de_gesto_sin_gestos_no_falla_en_silencio(clips):
    silent, speaking, _ = clips
    rep = run_trial(synth.cfg(), silent, speaking, {}, "gesto_silencio")
    assert not rep.ok and "gestos" in rep.message


def test_escenario_desconocido(clips):
    silent, speaking, ges = clips
    with pytest.raises(ValueError):
        run_trial(synth.cfg(), silent, speaking, ges, "volar")


def test_misma_semilla_mismo_resultado(clips):
    silent, speaking, ges = clips
    a = run_trial(synth.cfg(), silent, speaking, ges, "hablar_callar", seed=9, keep_frames=False)
    b = run_trial(synth.cfg(), silent, speaking, ges, "hablar_callar", seed=9, keep_frames=False)
    assert (a.origin_frame, a.destination_frame, a.wait_ms, a.fade_ms, a.anchor) == (
        b.origin_frame, b.destination_frame, b.wait_ms, b.fade_ms, b.anchor
    )


def test_measure_agrega_por_escenario(clips):
    silent, speaking, ges = clips
    avance = []
    summary = measure(synth.cfg(), silent, speaking, ges, repeats=3,
                      progress=lambda d, t, m: avance.append((d, t)))
    assert isinstance(summary, TrialSummary)
    assert summary.runs == 3 * len(SCENARIOS)
    assert summary.failed == 0
    assert summary.anchors + summary.fallbacks + summary.no_fade == summary.runs
    assert set(summary.by_scenario) == set(SCENARIOS)
    assert avance[-1] == (summary.runs, summary.runs)
    assert max(summary.waits_ms) <= 200.0 + 1000.0 / synth.FPS
    lineas = summary.lines()
    assert lineas[0].startswith("Ensayos:")
    assert any("Coste de discontinuidad" in l for l in lineas)


def test_measure_se_puede_cancelar(clips):
    silent, speaking, ges = clips
    summary = measure(synth.cfg(), silent, speaking, ges, repeats=5, cancel=lambda: True)
    assert summary.runs == 0


def test_cli_transitions_existe():
    from camvoice.cli import build_parser

    args = build_parser().parse_args(["transitions", "--repeats", "2"])
    assert args.repeats == 2
    assert callable(args.func)
