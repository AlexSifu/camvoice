"""Fixtures compartidas. Todo lo que escriba en disco va a un tmp_path."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from camvoice.config import AppConfig, VadConfig, VideoConfig


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Aísla la configuración y la caché de la instalación real del usuario."""
    home = tmp_path_factory.mktemp("camvoice-home")
    monkeypatch.setenv("CAMVOICE_HOME", str(home))
    return home


@pytest.fixture
def vad_cfg() -> VadConfig:
    return VadConfig(
        frame_ms=20,
        attack_ms=100,
        release_ms=400,
        pause_tolerance_ms=200,
        min_speaking_ms=300,
        min_silent_ms=200,
    )


@pytest.fixture
def video_cfg(tmp_path) -> VideoConfig:
    return VideoConfig(
        width=64,
        height=48,
        fps=10,
        transition_ms=300,
        resume_window_ms=500,
        pixel_format="nv12",
    )


# --------------------------------------------------------------------------- #
# Generadores de señales
# --------------------------------------------------------------------------- #


def tone(samples: int, rate: int = 16000, freq: float = 180.0, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(samples, dtype=np.float32) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def voiced_like(samples: int, rate: int = 16000, amplitude: float = 0.25) -> np.ndarray:
    """Señal con estructura armónica: lo que un VAD basado en GMM reconoce.

    Una F0 de 120 Hz con armónicos decrecientes y una modulación de sonoridad,
    que es lo que distingue la voz del ruido plano.
    """
    t = np.arange(samples, dtype=np.float32) / rate
    signal = np.zeros(samples, dtype=np.float32)
    for k in range(1, 12):
        signal += np.sin(2 * np.pi * 120.0 * k * t + k) / k
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4.0 * t)
    return (amplitude * signal * envelope).astype(np.float32)


def white_noise(samples: int, amplitude: float = 0.01, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(samples)).astype(np.float32)


def silence(samples: int) -> np.ndarray:
    return np.zeros(samples, dtype=np.float32)


@pytest.fixture
def signals():
    return {
        "tone": tone,
        "voiced": voiced_like,
        "noise": white_noise,
        "silence": silence,
    }


# --------------------------------------------------------------------------- #
# Clips sintéticos
# --------------------------------------------------------------------------- #


@pytest.fixture
def tiny_clips(tmp_path) -> tuple[Path, Path]:
    """Dos videos cortos y pequeños, escritos con OpenCV."""
    from camvoice.testclips import make_test_clips

    return make_test_clips(tmp_path / "clips", width=64, height=48, fps=10, seconds=1.2)


class FakeClip:
    """Fuente de fotogramas en memoria: cada fotograma es un valor constante.

    Permite comprobar transiciones con aritmética exacta: si el fotograma i
    vale i, el resultado de una mezcla dice exactamente qué se mezcló.
    """

    def __init__(self, values: list[int], shape: tuple[int, ...] = (8, 8)) -> None:
        self._frames = [np.full(shape, v, dtype=np.uint8) for v in values]

    def __len__(self) -> int:
        return len(self._frames)

    def frame(self, index: int) -> np.ndarray:
        return self._frames[min(max(index, 0), len(self._frames) - 1)]


@pytest.fixture
def fake_clip_factory():
    return FakeClip
