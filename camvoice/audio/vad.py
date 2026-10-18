"""Detectores de actividad de voz por trama.

Dos backends, misma interfaz:

  * `EnergyDetector`  - RMS con piso de ruido adaptativo e histéresis de trama.
    Sin dependencias nativas, funciona en cualquier máquina y es el fallback.
  * `WebrtcDetector`  - WebRTC VAD (GMM sobre sub-bandas), mucho mejor
    distinguiendo voz de ruido estacionario. Se le añade por delante la puerta
    de energía del detector anterior, porque WebRTC a veces marca voz en ruido
    muy bajo (micrófono silenciado o tapado).

Ninguno transcribe ni interpreta el audio: solo responden "¿hay voz en estos
20 ms?". La decisión temporal (entrar/salir de SPEAKING) vive en `gate.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..config import VadConfig
from .resample import rms_dbfs

__all__ = [
    "FrameDecision",
    "VoiceFrameDetector",
    "EnergyDetector",
    "WebrtcDetector",
    "build_detector",
    "webrtc_available",
]


@dataclass(frozen=True, slots=True)
class FrameDecision:
    """Resultado del análisis de una trama."""

    voiced: bool
    rms_db: float
    noise_floor_db: float
    backend: str
    calibrating: bool = False
    signal_lost: bool = False
    """La entrada lleva segundos por debajo de la puerta absoluta."""

    @property
    def snr_db(self) -> float:
        return self.rms_db - self.noise_floor_db


class VoiceFrameDetector(Protocol):
    name: str

    def process(self, frame: np.ndarray) -> FrameDecision: ...

    def reset(self) -> None: ...


# --------------------------------------------------------------------------- #
# Detector de energía
# --------------------------------------------------------------------------- #


class EnergyDetector:
    """RMS con piso de ruido adaptativo.

    El piso sube despacio (constante `noise_floor_tau_s`) y baja rápido, de modo
    que un ambiente que se vuelve silencioso se detecta en menos de un segundo,
    pero una frase larga no arrastra el piso hacia arriba.
    """

    name = "rms"

    # El piso baja con esta constante de tiempo, mucho más corta que la de subida.
    FAST_DECAY_TAU_S = 0.4
    CALIBRATION_MS = 300
    INITIAL_FLOOR_DB = -70.0

    MIN_NOISE_FLOOR_DB = -75.0
    """Suelo del seguidor de piso de ruido.

    Sin este límite el piso se hunde hasta el silencio digital (-120 dBFS) en
    cuanto la entrada calla de verdad —micrófono silenciado, o la supresión de
    ruido de Windows recortando a cero entre sonidos—. A partir de ahí la
    relación señal/ruido pierde todo significado: un chasquido de -50 dBFS
    aparece como +70 dB de SNR y abre la puerta. Ningún entorno acústico real
    con un micrófono funcionando está por debajo de -75 dBFS, así que un piso
    que lo pretenda está midiendo la ausencia de señal, no el ruido de la sala.
    """

    SIGNAL_LOST_MS = 5000
    """Tiempo por debajo de la puerta absoluta tras el cual se avisa al usuario.

    Un micrófono silenciado en Windows es indistinguible de uno averiado desde
    la aplicación: en ambos casos no pasa nada. Decirlo es mejor que quedarse
    en silencio para siempre sin explicación.
    """

    def __init__(self, cfg: VadConfig, frame_ms: int) -> None:
        self.cfg = cfg
        self.frame_ms = frame_ms
        self._alpha_up = _alpha(frame_ms / 1000.0, cfg.noise_floor_tau_s)
        self._alpha_down = _alpha(frame_ms / 1000.0, self.FAST_DECAY_TAU_S)
        self.reset()

    def reset(self) -> None:
        self.noise_floor_db = self.INITIAL_FLOOR_DB
        self._voiced = False
        self._elapsed_ms = 0
        self._initialised = False
        self._below_gate_ms = 0

    def process(self, frame: np.ndarray) -> FrameDecision:
        level = rms_dbfs(frame)
        self._elapsed_ms += self.frame_ms
        calibrating = self._elapsed_ms <= self.CALIBRATION_MS

        if not self._initialised:
            self.noise_floor_db = level
            self._initialised = True
        elif level < self.noise_floor_db:
            self.noise_floor_db += (level - self.noise_floor_db) * self._alpha_down
        elif not self._voiced:
            # El piso solo sube mientras creemos que no hay voz.
            self.noise_floor_db += (level - self.noise_floor_db) * self._alpha_up
        self.noise_floor_db = max(self.MIN_NOISE_FLOOR_DB, self.noise_floor_db)

        if level < self.cfg.gate_db:
            self._below_gate_ms += self.frame_ms
        else:
            self._below_gate_ms = 0
        signal_lost = self._below_gate_ms >= self.SIGNAL_LOST_MS

        if level < self.cfg.gate_db or calibrating:
            # Puerta absoluta: por debajo de esto no hay nada que analizar.
            self._voiced = False
            return FrameDecision(
                False, level, self.noise_floor_db, self.name, calibrating, signal_lost
            )

        if self.cfg.adaptive_noise_floor:
            snr = level - self.noise_floor_db
            threshold = self.cfg.snr_off_db if self._voiced else self.cfg.snr_on_db
            self._voiced = snr >= threshold
        else:
            # Umbral absoluto con una histéresis fija de 3 dB.
            on = self.cfg.rms_threshold_db
            off = on - 3.0
            self._voiced = level >= (off if self._voiced else on)

        return FrameDecision(
            self._voiced, level, self.noise_floor_db, self.name, False, False
        )


def _alpha(dt_s: float, tau_s: float) -> float:
    """Coeficiente de un filtro de primer orden para el paso `dt_s`."""
    if tau_s <= 0:
        return 1.0
    return float(1.0 - np.exp(-dt_s / tau_s))


# --------------------------------------------------------------------------- #
# Detector WebRTC
# --------------------------------------------------------------------------- #


def webrtc_available() -> bool:
    try:
        import webrtcvad  # noqa: F401
    except Exception:
        return False
    return True


class WebrtcDetector:
    """WebRTC VAD con la puerta de energía por delante.

    Requiere tramas de 10, 20 o 30 ms de int16 mono a 8/16/32/48 kHz.
    """

    name = "webrtc"

    def __init__(self, cfg: VadConfig, frame_ms: int, samplerate: int) -> None:
        import webrtcvad

        if frame_ms not in (10, 20, 30):
            raise ValueError("WebRTC VAD solo acepta tramas de 10, 20 o 30 ms.")
        if samplerate not in (8000, 16000, 32000, 48000):
            raise ValueError("WebRTC VAD solo acepta 8000, 16000, 32000 o 48000 Hz.")

        self.cfg = cfg
        self.frame_ms = frame_ms
        self.samplerate = samplerate
        self.expected_samples = samplerate * frame_ms // 1000
        self._vad = webrtcvad.Vad(int(cfg.aggressiveness))
        # Se reutiliza para el nivel, el piso de ruido y la puerta absoluta.
        self._energy = EnergyDetector(cfg, frame_ms)

    def reset(self) -> None:
        self._energy.reset()

    def process(self, frame: np.ndarray) -> FrameDecision:
        energy = self._energy.process(frame)
        if energy.calibrating or energy.rms_db < self.cfg.gate_db:
            return FrameDecision(
                False, energy.rms_db, energy.noise_floor_db, self.name,
                energy.calibrating, energy.signal_lost,
            )

        data = np.asarray(frame, dtype=np.int16)
        if data.size != self.expected_samples:
            raise ValueError(
                f"Se esperaban {self.expected_samples} muestras por trama "
                f"({self.frame_ms} ms a {self.samplerate} Hz), llegaron {data.size}."
            )
        voiced = bool(self._vad.is_speech(data.tobytes(), self.samplerate))
        return FrameDecision(
            voiced, energy.rms_db, energy.noise_floor_db, self.name, False, False
        )


# --------------------------------------------------------------------------- #
# Fábrica
# --------------------------------------------------------------------------- #


def build_detector(
    cfg: VadConfig, samplerate: int, *, frame_ms: int | None = None
) -> VoiceFrameDetector:
    """Crea el detector indicado en la configuración.

    Con backend 'auto' se intenta WebRTC y se cae a RMS si no está disponible o
    si la combinación de frecuencia y trama no le sirve.
    """
    frame_ms = frame_ms or cfg.frame_ms
    if cfg.backend == "rms":
        return EnergyDetector(cfg, frame_ms)
    try:
        return WebrtcDetector(cfg, frame_ms, samplerate)
    except Exception:
        if cfg.backend == "webrtc":
            raise
        return EnergyDetector(cfg, frame_ms)
