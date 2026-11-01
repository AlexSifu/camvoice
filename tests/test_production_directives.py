"""Directivas de producción: fundido de reserva corto, gestos hablando sin
retraso, frases breves automáticas (#6, #7, #8) y rotación de clips.

Pruebas SIMULADAS: caras sintéticas (testclips) y reloj falso; comprueban la
lógica del director, no la calidad visual con las grabaciones reales. La pausa
(congela la última imagen, nunca negro, reanuda en un ancla) ya la cubren
`test_stability.py` y `test_director_integration.py`.
"""

from __future__ import annotations

import pytest

from camvoice.audio.gate import VoiceState
from camvoice.config import AppConfig, GesturesConfig
from camvoice.video.director import Director, DirectorState, VoiceInput

from . import synth
from .synth import FPS, FakeClock, face_frames, gesture_frames, speaking_mouth

SIL = VoiceInput(VoiceState.SILENT, 0.0)
TALK = VoiceInput(VoiceState.SPEAKING, 0.8)
FRAME_MS = 1000.0 / FPS


@pytest.fixture(scope="module")
def clips():
    sil1, sil2, spk, ges = synth.standard_set()
    spk2 = synth.director_clip("habla_2", face_frames(180, speaking_mouth, "", t0=3.1, shift=(1, 0)))
    spk3 = synth.director_clip("habla_3", face_frames(180, speaking_mouth, "", t0=5.7, shift=(0, 1)))
    shots = {
        "respuesta_corta": synth.director_clip("respuesta_corta", face_frames(45, speaking_mouth, "")),
        "frase_corta": synth.director_clip("frase_corta", face_frames(60, speaking_mouth, "", t0=1.0)),
        "terminar_explicacion": synth.director_clip(
            "terminar_explicacion", face_frames(60, speaking_mouth, "", t0=2.0)),
    }
    return {"silent": [sil1, sil2], "speaking": [spk, spk2, spk3], "gestures": ges, "shots": shots}


def make(clips, *, shots=False, seed=1, **cfg_kw) -> Director:
    gestures = dict(clips["gestures"])
    actions = ()
    if shots:
        gestures.update(clips["shots"])
        actions = tuple(clips["shots"])
    return Director(
        synth.cfg(**cfg_kw), clips["silent"], clips["speaking"], gestures,
        gestures_cfg=GesturesConfig(cooldown_ms=0), clock=FakeClock(), seed=seed,
        speaking_actions=actions,
    )


def run(d: Director, voice: VoiceInput, seconds: float, log: list | None = None) -> None:
    for _ in range(int(round(seconds * FPS))):
        d.clock.advance(1.0 / FPS)
        _, info = d.step(voice)
        if log is not None:
            log.append(info)


# --------------------------------------------------------------------------- #
# Fundido de reserva fijo y corto
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pedido", [67, 150, 200, 400, 999])
def test_fundido_de_reserva_acotado_a_150_200_ms(clips, pedido):
    d = make(clips, fallback_fade_ms=pedido)
    ms = d._fallback_steps * FRAME_MS
    assert 150.0 - FRAME_MS < ms <= 200.0


def test_transicion_sin_ancla_usa_exactamente_el_fundido_de_reserva(clips):
    d = make(clips)
    run(d, SIL, 2.0)
    d._enter_from_frozen("speaking", min_steps=1, anchor=False)
    assert d._tr is not None and not d._tr.anchor
    assert d._tr.steps == d._fallback_steps
    assert d._tr.steps * FRAME_MS <= 200.0


def test_config_fundido_de_reserva_migra_y_valida():
    cfg = AppConfig.from_dict({"video": {"fallback_fade_ms": 999}})
    assert cfg.video.fallback_fade_ms == 200
    cfg = AppConfig.from_dict({"video": {"fallback_fade_ms": 40}})
    assert cfg.video.fallback_fade_ms == 150
    cfg.video.fallback_fade_ms = 300
    assert any("fundido de reserva" in e for e in cfg.validate())


# --------------------------------------------------------------------------- #
# Gesto pedido mientras se habla (sin variante «_hablando»)
# --------------------------------------------------------------------------- #


def test_gesto_hablando_entra_en_menos_de_180_ms_y_vuelve_a_hablar(clips):
    assert 150.0 <= Director.SPEAKING_GESTURE_WAIT_MS <= 180.0
    d = make(clips)
    run(d, TALK, 3.0)
    assert d.state is DirectorState.SPEAKING
    res = d.request_gesture("saludar")
    assert res.accepted
    log: list = []
    run(d, TALK, 0.5, log)
    assert d.stats.gestures_rejected == 0
    assert any(i.gesture == "saludar" and i.state in (DirectorState.TRANSITION, DirectorState.GESTURE)
               for i in log)
    waits = list(d.stats.gesture_waits_ms)
    assert waits and max(waits) <= 180.0
    # Sigue hablando: al terminar el gesto vuelve al bucle de habla.
    run(d, TALK, 4.0)
    assert d.state is DirectorState.SPEAKING
    assert d.cur.kind == "speaking"
    assert d.stats.gestures_played == 1


# --------------------------------------------------------------------------- #
# Frases breves automáticas (#6, #7, #8)
# --------------------------------------------------------------------------- #


def test_frases_breves_se_usan_solas_en_intervenciones_cortas(clips):
    d = make(clips, shots=True, seed=4)
    vistos: set[str] = set()
    for _ in range(24):  # silencio largo (> espera entre frases) + intervención breve
        log: list = []
        run(d, SIL, Director.AUTO_SHOT_COOLDOWN_S + 2.0)
        run(d, TALK, 2.5, log)
        vistos |= {i.gesture for i in log if i.gesture}
    assert d.stats.auto_shots >= 3
    assert vistos and vistos <= {"respuesta_corta", "frase_corta"}, vistos
    # No cuentan como gestos pedidos ni dejan nada pendiente.
    assert d.stats.gestures_played == 0 and d.stats.gestures_rejected == 0
    assert d.stats.discontinuities == 0


def _force_onset_shot(d: Director) -> None:
    d.AUTO_SHOT_ONSET_P = 1.0
    run(d, SIL, 3.0)
    for _ in range(FPS):
        d.clock.advance(1.0 / FPS)
        d.step(TALK)
        if d._auto_shot and d.state is DirectorState.GESTURE:
            return
    raise AssertionError("no arrancó la frase automática")


def test_si_sigues_hablando_la_frase_enlaza_con_el_habla(clips):
    d = make(clips, shots=True)
    _force_onset_shot(d)
    run(d, TALK, 6.0)
    assert d.state is DirectorState.SPEAKING and d.cur.kind == "speaking"
    assert not d._auto_shot and d.stats.auto_shots_cut == 0


def test_si_callas_la_frase_se_corta_con_fundido_corto(clips):
    d = make(clips, shots=True)
    _force_onset_shot(d)
    run(d, SIL, 1.0)
    assert d.stats.auto_shots_cut == 1
    assert not d._auto_shot
    assert d.cur.kind == "silent"
    assert d.stats.discontinuities == 0


def test_un_gesto_pedido_sustituye_a_la_frase_al_instante(clips):
    d = make(clips, shots=True)
    _force_onset_shot(d)
    assert d.request_gesture("saludar").accepted
    d.clock.advance(1.0 / FPS)
    _, info = d.step(TALK)
    assert info.gesture == "saludar"
    assert d._tr is not None and d._tr.dst.clip.name == "saludar"
    assert d._tr.steps * FRAME_MS <= 200.0
    run(d, TALK, 4.0)
    assert d.stats.gestures_played == 1 and d.stats.gestures_rejected == 0


def test_cierre_de_frase_en_intervenciones_largas(clips):
    d = make(clips, shots=True)
    d.AUTO_SHOT_ONSET_P = 0.0
    d.AUTO_SHOT_CLOSING_P = 1.0
    log: list = []
    run(d, SIL, 2.0)
    run(d, TALK, 40.0, log)
    nombres = {i.gesture for i in log if i.gesture}
    assert nombres == {"terminar_explicacion"}
    assert d.stats.auto_shots >= 1
    assert d.state in (DirectorState.SPEAKING, DirectorState.TRANSITION, DirectorState.GESTURE)


def test_frases_automaticas_se_pueden_desactivar(clips):
    d = Director(
        synth.cfg(), clips["silent"], clips["speaking"], {**clips["gestures"], **clips["shots"]},
        gestures_cfg=GesturesConfig(cooldown_ms=0, auto_speaking_shots=False), clock=FakeClock(),
        seed=1, speaking_actions=tuple(clips["shots"]),
    )
    d.AUTO_SHOT_ONSET_P = d.AUTO_SHOT_CLOSING_P = 1.0
    for _ in range(4):
        run(d, SIL, 22.0)
        run(d, TALK, 12.0)
    assert d.stats.auto_shots == 0


# --------------------------------------------------------------------------- #
# Rotación: silencio #1 <-> #3, habla entre variaciones sin volver siempre al 0
# --------------------------------------------------------------------------- #


def test_silencio_largo_alterna_entre_clips(clips):
    d = make(clips, seed=2)
    log: list = []
    run(d, SIL, 240.0, log)
    usados = [i.clip for i in log if i.state is DirectorState.SILENT]
    assert {"silencio", "silencio_2"} <= set(usados)
    cambios = sum(1 for a, b in zip(usados, usados[1:]) if a != b)
    assert cambios >= 4
    assert d.stats.discontinuities == 0


def test_habla_larga_rota_variaciones_sin_reiniciar_siempre_en_cero(clips):
    d = make(clips, seed=5)
    entradas: list[tuple[str, int]] = []
    original = d._choose_entry

    def espia(*a, **kw):
        clip, pos = original(*a, **kw)
        entradas.append((clip.name, pos))
        return clip, pos

    d._choose_entry = espia
    run(d, SIL, 2.0)
    run(d, TALK, 180.0)
    habla = [e for e in entradas if e[0].startswith("habla")]
    assert len(habla) >= 10
    assert len({c for c, _ in habla}) == 3, "debe rotar entre las tres variaciones"
    en_cero = sum(1 for _, p in habla if p <= 1)
    assert en_cero <= len(habla) // 2, habla
    assert len({p for _, p in habla}) >= 4
    assert d.stats.discontinuities == 0
