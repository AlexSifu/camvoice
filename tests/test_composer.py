"""Pruebas del reproductor y del compositor (CA-06 y CA-08)."""

from __future__ import annotations

import numpy as np
import pytest

from camvoice.audio.gate import VoiceState
from camvoice.config import VideoConfig
from camvoice.video.clip import ClipPlayer
from camvoice.video.composer import FrameComposer, RenderState

# --------------------------------------------------------------------------- #
# ClipPlayer
# --------------------------------------------------------------------------- #


def test_el_bucle_normal_vuelve_al_principio():
    p = ClipPlayer(5, loop_mode="loop")
    indices = [p.index] + [p.advance().index for _ in range(6)]
    assert indices == [0, 1, 2, 3, 4, 0, 1]
    assert p.loops == 1


def test_el_modo_ida_y_vuelta_rebota_en_los_extremos():
    """El rebote elimina el salto del último al primer fotograma."""
    p = ClipPlayer(5, loop_mode="pingpong")
    indices = [p.index] + [p.advance().index for _ in range(9)]
    assert indices == [0, 1, 2, 3, 4, 3, 2, 1, 0, 1]


def test_el_rebote_nunca_salta_mas_de_un_fotograma():
    p = ClipPlayer(8, loop_mode="pingpong")
    previous = p.index
    for _ in range(100):
        current = p.advance().index
        assert abs(current - previous) <= 1, "hay un salto visible en el bucle"
        previous = current


def test_la_velocidad_escala_el_avance():
    p = ClipPlayer(100, loop_mode="loop", speed=2.0)
    p.advance()
    assert p.index == 2
    p.speed = 0.5
    p.advance()
    assert p.position == pytest.approx(2.5)


def test_un_clip_de_un_solo_fotograma_no_se_mueve():
    p = ClipPlayer(1)
    for _ in range(10):
        assert p.advance().index == 0


def test_una_velocidad_absurda_se_ancla_en_vez_de_divergir():
    p = ClipPlayer(4, loop_mode="pingpong", speed=1000.0)
    for _ in range(5):
        state = p.advance()
        assert 0 <= state.index <= 3
        assert np.isfinite(state.position)


def test_el_rebobinado_vuelve_al_primer_fotograma():
    p = ClipPlayer(10, loop_mode="pingpong")
    for _ in range(15):
        p.advance()
    p.rewind()
    assert p.index == 0
    assert p.direction == 1


def test_un_clip_vacio_se_rechaza():
    with pytest.raises(ValueError):
        ClipPlayer(0)


# --------------------------------------------------------------------------- #
# FrameComposer
# --------------------------------------------------------------------------- #


@pytest.fixture
def composer(fake_clip_factory):
    cfg = VideoConfig(
        width=8, height=8, fps=10, transition_ms=300,
        resume_window_ms=500, loop_mode="loop", pixel_format="bgr",
    )
    speaking = fake_clip_factory([200] * 20)
    silent = fake_clip_factory([50] * 20)
    return FrameComposer(cfg, speaking, silent), cfg


def value_of(frame: np.ndarray) -> float:
    return float(frame.mean())


def test_arranca_mostrando_el_clip_de_silencio(composer):
    comp, _ = composer
    frame, info = comp.step(VoiceState.SILENT)
    assert info.state is RenderState.SILENT
    assert value_of(frame) == 50


def test_la_transicion_dura_lo_configurado(composer):
    """300 ms a 10 FPS son 3 fotogramas de fundido."""
    comp, cfg = composer
    comp.step(VoiceState.SILENT)
    assert comp.transition_steps == 3

    states = [comp.step(VoiceState.SPEAKING)[1].state for _ in range(5)]
    assert states[:2] == [RenderState.TRANSITION, RenderState.TRANSITION]
    assert states[2] is RenderState.SPEAKING


def test_el_fundido_es_monotono_y_sin_saltos(composer):
    """CA-06: ni pantallas negras ni saltos bruscos durante el cambio."""
    comp, _ = composer
    comp.step(VoiceState.SILENT)
    values = [value_of(comp.step(VoiceState.SPEAKING)[0]) for _ in range(4)]
    assert values == sorted(values), "el fundido no avanza de forma monótona"
    assert values[0] > 50, "el primer fotograma del fundido no mezcla nada"
    assert values[-1] == 200
    assert all(50 <= v <= 200 for v in values), "se salió del rango de los clips"


def test_nunca_produce_un_fotograma_negro(composer):
    """Un negro en una videollamada se lee como una cámara caída."""
    comp, _ = composer
    sequence = (
        [VoiceState.SILENT] * 5
        + [VoiceState.SPEAKING] * 5
        + [VoiceState.SILENT] * 2
        + [VoiceState.SPEAKING] * 8
        + [VoiceState.SILENT] * 10
    )
    for state in sequence:
        frame, _ = comp.step(state)
        assert value_of(frame) >= 50, "apareció un fotograma más oscuro que ambos clips"


def test_sin_transicion_el_cambio_es_inmediato(fake_clip_factory):
    cfg = VideoConfig(
        width=8, height=8, fps=10, transition_ms=0, pixel_format="bgr", loop_mode="loop"
    )
    comp = FrameComposer(cfg, fake_clip_factory([200] * 5), fake_clip_factory([50] * 5))
    comp.step(VoiceState.SILENT)
    frame, info = comp.step(VoiceState.SPEAKING)
    assert info.state is RenderState.SPEAKING
    assert value_of(frame) == 200


def test_invertir_una_transicion_en_curso_no_salta(composer):
    """Si la voz vuelve a mitad del fundido, se deshace desde donde iba."""
    comp, _ = composer
    comp.step(VoiceState.SILENT)
    comp.step(VoiceState.SPEAKING)  # primer paso del fundido
    mid = value_of(comp.step(VoiceState.SPEAKING)[0])

    reverted = [value_of(comp.step(VoiceState.SILENT)[0]) for _ in range(4)]
    assert reverted[0] < mid, "al invertir no empezó a deshacerse"
    assert reverted == sorted(reverted, reverse=True)
    assert reverted[-1] == 50


def test_el_clip_de_habla_se_rebobina_tras_el_silencio_prolongado(fake_clip_factory):
    """Evita que el habla reanude con la boca a medio abrir."""
    cfg = VideoConfig(
        width=8, height=8, fps=10, transition_ms=0,
        resume_window_ms=500, pixel_format="bgr", loop_mode="loop",
    )
    comp = FrameComposer(
        cfg, fake_clip_factory(list(range(100, 120))), fake_clip_factory([50] * 20)
    )
    for _ in range(6):
        comp.step(VoiceState.SPEAKING)
    assert comp.speaking_player.index > 0

    rewound = False
    for _ in range(10):  # 1 s de silencio: más que la ventana de 500 ms
        rewound |= comp.step(VoiceState.SILENT)[1].speaking_rewound
    assert rewound
    assert comp.speaking_player.index == 0


def test_el_habla_continua_si_vuelve_dentro_de_la_ventana(fake_clip_factory):
    cfg = VideoConfig(
        width=8, height=8, fps=10, transition_ms=0,
        resume_window_ms=1000, pixel_format="bgr", loop_mode="loop",
    )
    comp = FrameComposer(
        cfg, fake_clip_factory(list(range(100, 150))), fake_clip_factory([50] * 50)
    )
    for _ in range(6):
        comp.step(VoiceState.SPEAKING)
    before = comp.speaking_player.index
    for _ in range(3):  # 300 ms: dentro de la ventana de 1 s
        comp.step(VoiceState.SILENT)
    comp.step(VoiceState.SPEAKING)
    assert comp.speaking_player.index == before + 1


def test_el_clip_de_silencio_sigue_avanzando_mientras_se_habla(fake_clip_factory):
    """Así parpadeos y respiración no se congelan al volver al silencio."""
    cfg = VideoConfig(
        width=8, height=8, fps=10, transition_ms=0, pixel_format="bgr", loop_mode="loop"
    )
    comp = FrameComposer(
        cfg, fake_clip_factory([200] * 50), fake_clip_factory(list(range(50)))
    )
    for _ in range(8):
        comp.step(VoiceState.SPEAKING)
    assert comp.silent_player.index == 8


def test_las_velocidades_se_releen_en_vivo(fake_clip_factory):
    cfg = VideoConfig(
        width=8, height=8, fps=10, transition_ms=0, pixel_format="bgr", loop_mode="loop"
    )
    comp = FrameComposer(cfg, fake_clip_factory([200] * 60), fake_clip_factory([50] * 60))
    comp.step(VoiceState.SPEAKING)
    comp.step(VoiceState.SPEAKING)
    cfg.speaking_speed = 3.0
    before = comp.speaking_player.position
    comp.step(VoiceState.SPEAKING)
    assert comp.speaking_player.position == pytest.approx(before + 3.0)


def test_una_sesion_larga_no_acumula_estados_raros(composer):
    """CA-08: 30 minutos simulados sin degradarse ni salirse de rango."""
    comp, cfg = composer
    rng = np.random.default_rng(11)
    target = VoiceState.SILENT
    frames = 30 * 60 * cfg.fps
    for i in range(frames):
        if rng.random() < 0.004:  # cambia de estado cada ~4 s de media
            target = (
                VoiceState.SPEAKING if target is VoiceState.SILENT else VoiceState.SILENT
            )
        frame, info = comp.step(target)
        assert 50 <= value_of(frame) <= 200
        assert info.state in RenderState
    assert comp.transitions > 10


def test_clips_vacios_se_rechazan(fake_clip_factory):
    cfg = VideoConfig(width=8, height=8, fps=10, pixel_format="bgr")
    with pytest.raises(ValueError):
        FrameComposer(cfg, fake_clip_factory([]), fake_clip_factory([50]))
