"""Pruebas de los detectores por trama y del remuestreador."""

from __future__ import annotations

import numpy as np
import pytest

from camvoice.audio.resample import Resampler, float_to_int16, rms_dbfs, to_mono
from camvoice.audio.vad import (
    EnergyDetector,
    WebrtcDetector,
    build_detector,
    webrtc_available,
)
from camvoice.config import VadConfig

RATE = 16000
FRAME_MS = 20
FRAME = RATE * FRAME_MS // 1000


def frames_of(signal: np.ndarray) -> list[np.ndarray]:
    n = signal.size // FRAME
    return [float_to_int16(signal[i * FRAME : (i + 1) * FRAME]) for i in range(n)]


def run(detector, signal: np.ndarray) -> list[bool]:  # noqa: ANN001
    return [detector.process(f).voiced for f in frames_of(signal)]


# --------------------------------------------------------------------------- #
# Nivel
# --------------------------------------------------------------------------- #


def test_rms_dbfs_de_silencio_es_el_minimo():
    assert rms_dbfs(np.zeros(320, dtype=np.int16)) <= -100.0


def test_rms_dbfs_de_escala_completa_es_cero():
    full = np.full(320, 32767, dtype=np.int16)
    assert rms_dbfs(full) == pytest.approx(0.0, abs=0.01)


def test_rms_dbfs_acepta_int16_y_float_por_igual():
    floats = 0.25 * np.sin(np.linspace(0, 20, 320)).astype(np.float32)
    assert rms_dbfs(floats) == pytest.approx(rms_dbfs(float_to_int16(floats)), abs=0.1)


def test_float_to_int16_recorta_sin_envolver():
    out = float_to_int16(np.array([-3.0, -1.0, 0.0, 1.0, 3.0], dtype=np.float32))
    assert out.tolist() == [-32767, -32767, 0, 32767, 32767]


def test_to_mono_promedia_los_canales():
    stereo = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=np.float32)
    assert to_mono(stereo).tolist() == pytest.approx([0.5, 0.5, 0.5])


# --------------------------------------------------------------------------- #
# Remuestreador
# --------------------------------------------------------------------------- #


def test_el_paso_directo_no_altera_la_senal():
    r = Resampler(16000, 16000)
    x = np.sin(np.linspace(0, 50, 1000)).astype(np.float32)
    assert np.allclose(r.process(x), x)


@pytest.mark.parametrize("src", [48000, 44100, 32000, 22050])
def test_el_numero_de_muestras_corresponde_al_ratio(src):
    r = Resampler(src, 16000)
    total = 0
    for _ in range(20):  # 20 bloques de 10 ms
        total += r.process(np.zeros(src // 100, dtype=np.float32)).size
    expected = 16000 * 20 / 100
    # Se tolera el retardo de arranque del FIR, que solo afecta al principio.
    assert abs(total - expected) <= 0.03 * expected


def test_mantiene_la_continuidad_entre_bloques():
    """Una rampa troceada debe salir como una rampa, sin escalones.

    Si el estado del filtro o la posición fraccionaria no se conservaran,
    aparecerían discontinuidades que el detector leería como transitorios.
    """
    r = Resampler(48000, 16000)
    ramp = np.linspace(0.0, 1.0, 48000, dtype=np.float32)
    out = np.concatenate([r.process(ramp[i : i + 480]) for i in range(0, ramp.size, 480)])
    assert out.size > 15000
    body = out[200:-200]  # se descartan los extremos: transitorio del FIR
    diffs = np.diff(body)
    assert diffs.min() > 0, "la rampa dejó de ser monótona"
    assert diffs.std() < diffs.mean() * 0.2, "hay escalones en las costuras"


def test_preserva_la_amplitud_de_un_tono_en_banda():
    r = Resampler(48000, 16000)
    t = np.arange(48000, dtype=np.float32) / 48000
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    out = np.concatenate([r.process(x[i : i + 480]) for i in range(0, x.size, 480)])
    assert rms_dbfs(out[500:]) == pytest.approx(rms_dbfs(x), abs=0.5)


def test_atenua_lo_que_esta_por_encima_del_nyquist_destino():
    """Un tono de 15 kHz a 48 kHz no debe reflejarse en la banda de voz."""
    r = Resampler(48000, 16000)
    t = np.arange(48000, dtype=np.float32) / 48000
    x = (0.5 * np.sin(2 * np.pi * 15000 * t)).astype(np.float32)
    out = np.concatenate([r.process(x[i : i + 480]) for i in range(0, x.size, 480)])
    assert rms_dbfs(out[1000:]) < rms_dbfs(x) - 25.0


def test_frecuencias_invalidas_se_rechazan():
    with pytest.raises(ValueError):
        Resampler(0, 16000)


# --------------------------------------------------------------------------- #
# Detector de energía
# --------------------------------------------------------------------------- #


def test_el_silencio_nunca_se_marca_como_voz(vad_cfg, signals):
    det = EnergyDetector(vad_cfg, FRAME_MS)
    assert not any(run(det, signals["silence"](RATE * 2)))


def test_un_tono_fuerte_se_marca_como_voz(vad_cfg, signals):
    det = EnergyDetector(vad_cfg, FRAME_MS)
    # Primero ruido bajo para fijar el piso, después el tono.
    signal = np.concatenate([signals["noise"](RATE, 0.003), signals["tone"](RATE)])
    decisions = run(det, signal)
    second_half = decisions[len(decisions) // 2 :]
    assert sum(second_half) > 0.9 * len(second_half)


def test_el_ruido_estacionario_se_absorbe_en_el_piso(vad_cfg, signals):
    """El piso adaptativo sube hasta el ruido y deja de dar positivos."""
    det = EnergyDetector(vad_cfg, FRAME_MS)
    decisions = run(det, signals["noise"](RATE * 6, 0.02, seed=3))
    tail = decisions[-50:]
    assert sum(tail) == 0, "el ruido constante sigue marcándose como voz"
    assert det.noise_floor_db > -60.0


def test_el_piso_de_ruido_baja_rapido_al_quedarse_en_silencio(vad_cfg, signals):
    det = EnergyDetector(vad_cfg, FRAME_MS)
    run(det, signals["noise"](RATE * 4, 0.05, seed=1))
    loud_floor = det.noise_floor_db
    run(det, signals["noise"](RATE * 2, 0.002, seed=2))
    assert det.noise_floor_db < loud_floor - 10.0


def test_la_puerta_absoluta_ignora_lo_inaudible(vad_cfg, signals):
    cfg = VadConfig(gate_db=-40.0, adaptive_noise_floor=True, snr_on_db=3.0)
    det = EnergyDetector(cfg, FRAME_MS)
    # Señal clara pero muy por debajo de la puerta: no debe pasar.
    quiet = signals["tone"](RATE * 2, amplitude=0.001)
    assert not any(run(det, quiet))


def test_la_calibracion_inicial_no_reporta_voz(vad_cfg, signals):
    det = EnergyDetector(vad_cfg, FRAME_MS)
    loud = signals["tone"](RATE, amplitude=0.5)
    decisions = [det.process(f) for f in frames_of(loud)]
    calibration_frames = EnergyDetector.CALIBRATION_MS // FRAME_MS
    assert all(d.calibrating for d in decisions[:calibration_frames])
    assert not any(d.voiced for d in decisions[:calibration_frames])


def test_el_umbral_absoluto_tambien_funciona(signals):
    cfg = VadConfig(adaptive_noise_floor=False, rms_threshold_db=-30.0, gate_db=-80.0)
    det = EnergyDetector(cfg, FRAME_MS)
    run(det, signals["silence"](RATE))  # pasa la calibración
    loud = run(det, signals["tone"](RATE, amplitude=0.5))
    quiet = run(det, signals["tone"](RATE, amplitude=0.005))
    assert sum(loud) > 0.9 * len(loud)
    assert sum(quiet) == 0


def test_los_umbrales_se_leen_en_vivo_del_objeto_de_configuracion(vad_cfg, signals):
    """La UI ajusta sensibilidad mutando VadConfig; el detector debe notarlo."""
    cfg = VadConfig(snr_on_db=6.0, snr_off_db=4.0, gate_db=-80.0)
    det = EnergyDetector(cfg, FRAME_MS)
    signal = signals["tone"](RATE, amplitude=0.02)
    run(det, signals["noise"](RATE, 0.004))
    before = run(det, signal)
    cfg.snr_on_db = 60.0  # exigencia imposible
    cfg.snr_off_db = 55.0
    after = run(det, signal)
    assert sum(before) > 0
    assert sum(after) == 0


# --------------------------------------------------------------------------- #
# Detector WebRTC
# --------------------------------------------------------------------------- #

requires_webrtc = pytest.mark.skipif(
    not webrtc_available(), reason="webrtcvad no está instalado"
)


@requires_webrtc
def test_webrtc_no_marca_voz_en_silencio(vad_cfg, signals):
    det = WebrtcDetector(vad_cfg, FRAME_MS, RATE)
    assert not any(run(det, signals["silence"](RATE * 2)))


@requires_webrtc
def test_webrtc_reconoce_una_senal_con_estructura_de_voz(vad_cfg, signals):
    det = WebrtcDetector(vad_cfg, FRAME_MS, RATE)
    run(det, signals["noise"](RATE // 2, 0.002))  # calibración
    decisions = run(det, signals["voiced"](RATE * 2))
    assert sum(decisions) > 0.5 * len(decisions)


@requires_webrtc
def test_webrtc_rechaza_tramas_de_tamano_incorrecto(vad_cfg):
    det = WebrtcDetector(vad_cfg, FRAME_MS, RATE)
    for _ in range(EnergyDetector.CALIBRATION_MS // FRAME_MS + 2):
        det.process(np.full(FRAME, 3000, dtype=np.int16))
    # Debe ser audible: una trama de ceros la descarta antes la puerta de
    # energía, sin llegar nunca a la comprobación de tamaño.
    with pytest.raises(ValueError):
        det.process(np.full(FRAME + 7, 3000, dtype=np.int16))


@requires_webrtc
@pytest.mark.parametrize("bad_frame_ms", [15, 25, 40])
def test_webrtc_solo_acepta_ventanas_validas(vad_cfg, bad_frame_ms):
    with pytest.raises(ValueError):
        WebrtcDetector(vad_cfg, bad_frame_ms, RATE)


@requires_webrtc
def test_webrtc_solo_acepta_frecuencias_validas(vad_cfg):
    with pytest.raises(ValueError):
        WebrtcDetector(vad_cfg, FRAME_MS, 22050)


# --------------------------------------------------------------------------- #
# Fábrica
# --------------------------------------------------------------------------- #


def test_la_fabrica_respeta_el_backend_rms():
    det = build_detector(VadConfig(backend="rms"), RATE)
    assert det.name == "rms"


def test_el_modo_automatico_cae_a_rms_si_la_combinacion_no_sirve():
    det = build_detector(VadConfig(backend="auto"), 22050)
    assert det.name == "rms"


def test_el_backend_webrtc_explicito_falla_en_vez_de_degradarse():
    with pytest.raises(Exception):
        build_detector(VadConfig(backend="webrtc"), 22050)


@requires_webrtc
def test_el_modo_automatico_prefiere_webrtc():
    assert build_detector(VadConfig(backend="auto"), RATE).name == "webrtc"


# --------------------------------------------------------------------------- #
# Entrada sin señal
# --------------------------------------------------------------------------- #


def test_el_piso_de_ruido_no_baja_del_limite_realista(vad_cfg, signals):
    """Sin este límite, un chasquido sobre silencio digital parece +70 dB de SNR.

    Es lo que se observó midiendo en un equipo real: la entrada callaba a
    -120 dBFS, el piso la seguía hasta ahí, y cualquier transitorio abría la
    puerta con una relación señal/ruido inventada.
    """
    det = EnergyDetector(vad_cfg, FRAME_MS)
    run(det, signals["silence"](RATE * 10))
    assert det.noise_floor_db == pytest.approx(EnergyDetector.MIN_NOISE_FLOOR_DB)


def test_un_chasquido_sobre_silencio_digital_no_abre_la_puerta(vad_cfg, signals):
    cfg = VadConfig(gate_db=-50.0, snr_on_db=9.0, snr_off_db=5.0)
    det = EnergyDetector(cfg, FRAME_MS)
    run(det, signals["silence"](RATE * 5))
    # Un transitorio de -55 dBFS: por debajo de la puerta absoluta.
    click = signals["tone"](FRAME * 3, amplitude=0.0018)
    assert not any(run(det, click))


def test_una_entrada_muda_se_detecta_y_se_avisa(vad_cfg, signals):
    """CA-12: un micrófono silenciado debe decirlo, no quedarse callado."""
    det = EnergyDetector(vad_cfg, FRAME_MS)
    decisions = [det.process(f) for f in frames_of(signals["silence"](RATE * 8))]
    early = EnergyDetector.SIGNAL_LOST_MS // FRAME_MS // 2
    assert not decisions[early].signal_lost, "avisó antes de tiempo"
    assert decisions[-1].signal_lost, "no avisó de la falta de señal"


def test_el_aviso_desaparece_al_volver_la_senal(vad_cfg, signals):
    det = EnergyDetector(vad_cfg, FRAME_MS)
    run(det, signals["silence"](RATE * 8))
    decisions = [det.process(f) for f in frames_of(signals["tone"](RATE, amplitude=0.2))]
    assert not decisions[-1].signal_lost


@requires_webrtc
def test_webrtc_tambien_propaga_el_aviso(vad_cfg, signals):
    det = WebrtcDetector(vad_cfg, FRAME_MS, RATE)
    decisions = [det.process(f) for f in frames_of(signals["silence"](RATE * 8))]
    assert decisions[-1].signal_lost
    assert not any(d.voiced for d in decisions)
