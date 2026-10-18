"""Pruebas de la histéresis temporal (CA-04 y CA-05).

La puerta es determinista, así que estas pruebas comprueban el comportamiento
exacto que los criterios de aceptación exigen, sin micrófono.
"""

from __future__ import annotations

import numpy as np
import pytest

from camvoice.audio.gate import SpeechGate, VoiceState
from camvoice.config import VadConfig


def feed(gate: SpeechGate, pattern: list[bool]) -> list[VoiceState]:
    return [gate.update(v).state for v in pattern]


def ms_to_frames(ms: int, frame_ms: int) -> int:
    return ms // frame_ms


# --------------------------------------------------------------------------- #


def test_arranca_en_silencio(vad_cfg):
    assert SpeechGate(vad_cfg).state is VoiceState.SILENT


def test_latencia_de_activacion_es_el_tiempo_de_ataque(vad_cfg):
    """CA-04: con voz continua, SPEAKING llega tras attack_ms."""
    gate = SpeechGate(vad_cfg)
    frame_ms = vad_cfg.frame_ms

    # min_silent_ms debe cumplirse antes de poder abrir: se consume con silencio.
    for _ in range(ms_to_frames(vad_cfg.min_silent_ms, frame_ms)):
        gate.update(False)

    opened_at = None
    for i in range(1, 50):
        if gate.update(True).state is VoiceState.SPEAKING:
            opened_at = i * frame_ms
            break
    assert opened_at is not None
    assert opened_at == vad_cfg.attack_ms


def test_la_latencia_declarada_coincide_con_la_medida(vad_cfg):
    gate = SpeechGate(vad_cfg)
    assert gate.expected_latency_ms == vad_cfg.attack_ms


def test_vuelve_a_silencio_tras_el_tiempo_de_permanencia(vad_cfg):
    """CA-04: al callar, SILENT llega tras release_ms de silencio continuo."""
    gate = SpeechGate(vad_cfg)
    frame_ms = vad_cfg.frame_ms
    while gate.state is not VoiceState.SPEAKING:
        gate.update(True)
    # Se agota min_speaking_ms hablando, para aislar el efecto de release_ms.
    for _ in range(ms_to_frames(vad_cfg.min_speaking_ms, frame_ms)):
        gate.update(True)

    closed_at = None
    for i in range(1, 100):
        if gate.update(False).state is VoiceState.SILENT:
            closed_at = i * frame_ms
            break
    assert closed_at == vad_cfg.release_ms


def test_una_pausa_corta_no_cierra_la_puerta(vad_cfg):
    """El hueco entre palabras no debe devolver el video al clip neutral."""
    gate = SpeechGate(vad_cfg)
    while gate.state is not VoiceState.SPEAKING:
        gate.update(True)

    gap = ms_to_frames(vad_cfg.release_ms - vad_cfg.frame_ms, vad_cfg.frame_ms)
    for _ in range(gap):
        assert gate.update(False).state is VoiceState.SPEAKING
    assert gate.update(True).state is VoiceState.SPEAKING


def test_la_tolerancia_a_pausas_acumula_el_ataque(vad_cfg):
    """Dos sílabas separadas por un hueco corto suman hacia la activación."""
    cfg = VadConfig(
        frame_ms=20, attack_ms=100, pause_tolerance_ms=200,
        release_ms=400, min_speaking_ms=0, min_silent_ms=0,
    )
    gate = SpeechGate(cfg)
    # 60 ms de voz, 40 ms de hueco (dentro de la tolerancia), 40 ms más de voz.
    for _ in range(3):
        gate.update(True)
    for _ in range(2):
        gate.update(False)
    assert gate.state is VoiceState.SILENT
    states = [gate.update(True).state for _ in range(2)]
    assert states[-1] is VoiceState.SPEAKING


def test_un_hueco_largo_descarta_el_ataque_acumulado(vad_cfg):
    cfg = VadConfig(
        frame_ms=20, attack_ms=200, pause_tolerance_ms=100,
        release_ms=400, min_speaking_ms=0, min_silent_ms=0,
    )
    gate = SpeechGate(cfg)
    for _ in range(5):  # 100 ms: la mitad del ataque
        gate.update(True)
    for _ in range(10):  # 200 ms de hueco, el doble de la tolerancia
        gate.update(False)
    # Tras el hueco hace falta el ataque completo otra vez.
    for i in range(9):
        assert gate.update(True).state is VoiceState.SILENT, f"abrió en la trama {i}"
    assert gate.update(True).state is VoiceState.SPEAKING


def test_el_ruido_de_fondo_no_abre_la_puerta(vad_cfg):
    """CA-05: tramas de voz aisladas y espaciadas no deben acumularse.

    Es el patrón que produce el ruido ambiente con un VAD agresivo: un positivo
    suelto cada pocas tramas. Sin la ventana de ataque, una tolerancia a pausas
    generosa acabaría abriendo la puerta sola.
    """
    cfg = VadConfig(
        frame_ms=20, attack_ms=120, pause_tolerance_ms=400,
        release_ms=400, min_speaking_ms=0, min_silent_ms=0,
    )
    gate = SpeechGate(cfg)
    # 30 s de un positivo cada 5 tramas (100 ms): 20 % de falsos positivos.
    pattern = [(i % 5 == 0) for i in range(1500)]
    states = feed(gate, pattern)
    assert all(s is VoiceState.SILENT for s in states)
    assert gate.transitions == 0


def test_el_ruido_aleatorio_moderado_produce_pocos_cambios(vad_cfg):
    """CA-05: estabilidad frente a un VAD que parpadea al azar."""
    rng = np.random.default_rng(7)
    gate = SpeechGate(vad_cfg)
    # 60 s de tramas con 30 % de probabilidad de dar positivo.
    pattern = list(rng.random(3000) < 0.30)
    feed(gate, pattern)
    changes_per_minute = gate.transitions
    assert changes_per_minute <= 4, f"demasiados cambios: {changes_per_minute}"


def test_permanencia_minima_evita_parpadeos(vad_cfg):
    """Una sílaba aislada mantiene SPEAKING al menos min_speaking_ms."""
    cfg = VadConfig(
        frame_ms=20, attack_ms=40, release_ms=40,
        min_speaking_ms=300, min_silent_ms=0, pause_tolerance_ms=0,
    )
    gate = SpeechGate(cfg)
    while gate.state is not VoiceState.SPEAKING:
        gate.update(True)
    held = 0
    while gate.update(False).state is VoiceState.SPEAKING:
        held += cfg.frame_ms
        assert held < 2000, "la puerta no cerró nunca"
    assert held >= cfg.min_speaking_ms - cfg.frame_ms


def test_el_reinicio_vuelve_al_estado_inicial(vad_cfg):
    gate = SpeechGate(vad_cfg)
    while gate.state is not VoiceState.SPEAKING:
        gate.update(True)
    gate.reset()
    assert gate.state is VoiceState.SILENT
    assert gate.transitions == 0


def test_changed_solo_marca_la_trama_del_cambio(vad_cfg):
    gate = SpeechGate(vad_cfg)
    marks = []
    for _ in range(40):
        marks.append(gate.update(True).changed)
    assert marks.count(True) == 1


@pytest.mark.parametrize("frame_ms", [10, 20, 30])
def test_la_latencia_es_consistente_en_cualquier_ventana(frame_ms):
    cfg = VadConfig(
        frame_ms=frame_ms, attack_ms=120, release_ms=400,
        min_speaking_ms=0, min_silent_ms=0, pause_tolerance_ms=0,
    )
    gate = SpeechGate(cfg)
    elapsed = 0
    while gate.update(True).state is not VoiceState.SPEAKING:
        elapsed += frame_ms
        assert elapsed < 1000
    elapsed += frame_ms
    # El redondeo a tramas completas nunca debe exceder una ventana de error.
    assert abs(elapsed - cfg.attack_ms) <= frame_ms


def test_frame_ms_invalido_se_rechaza(vad_cfg):
    with pytest.raises(ValueError):
        SpeechGate(vad_cfg, frame_ms=0)
