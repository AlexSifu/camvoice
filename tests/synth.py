"""Clips sintéticos en memoria para probar índice y director sin archivos.

Usa la misma cara esquemática que `camvoice make-clips` (testclips.py), a
resolución reducida para que las pruebas sean rápidas.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from camvoice.config import VideoConfig
from camvoice.testclips import render_face
from camvoice.video.cache import bgr_to_nv12
from camvoice.video.director import DirectorClip
from camvoice.video.index import build_index

W, H, FPS = 160, 120, 15


def cfg(**kw) -> VideoConfig:
    base = dict(width=W, height=H, fps=FPS, pixel_format="nv12", transition_ms=400, seed=1234)
    base.update(kw)
    return VideoConfig(**base)


def speaking_mouth(i: int) -> float:
    t = i / FPS
    return max(0.0, min(1.0, 0.5 + 0.35 * math.sin(2 * math.pi * 3.1 * t) + 0.25 * math.sin(2 * math.pi * 1.3 * t + 1)))


def face_frames(n: int, mouth_fn, label: str, *, shift=(0, 0), gain: float = 1.0, t0: float = 0.0,
                fmt: str = "nv12", head_dy_fn=None) -> list[np.ndarray]:
    out = []
    for i in range(n):
        bgr = render_face(W, H, t0 + i / FPS, mouth_open=float(mouth_fn(i)), label=label)
        dx, dy = shift
        if head_dy_fn is not None:
            dy = dy + head_dy_fn(i)
        if dx or dy:
            m = np.float32([[1, 0, dx], [0, 1, dy]])
            bgr = cv2.warpAffine(bgr, m, (W, H), borderMode=cv2.BORDER_REPLICATE)
        if gain != 1.0:
            bgr = np.clip(bgr.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        out.append(bgr_to_nv12(bgr) if fmt == "nv12" else bgr)
    return out


def director_clip(name: str, frames: list[np.ndarray], fmt: str = "nv12") -> DirectorClip:
    idx = build_index(frames, pixel_format=fmt, height=H, fps=FPS)
    return DirectorClip(name, frames, idx)


def gesture_frames(n: int, label: str = "") -> list[np.ndarray]:
    """La cabeza sube y vuelve: empieza y termina en la pose neutra."""
    return face_frames(n, lambda i: 0.0, label, head_dy_fn=lambda i: -int(round(10 * math.sin(math.pi * i / (n - 1)))))


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


_cache: dict = {}


def standard_set():
    """(silencios, hablas, gestos) cacheado entre pruebas: indexar cuesta."""
    if "set" not in _cache:
        sil1 = director_clip("silencio", face_frames(150, lambda i: 0.0, ""))
        sil2 = director_clip("silencio_2", face_frames(150, lambda i: 0.0, "", t0=7.3, shift=(2, 1)))
        spk = director_clip("habla", face_frames(180, speaking_mouth, ""))
        ges = {"saludar": director_clip("saludar", gesture_frames(30)),
               "asentir": director_clip("asentir", gesture_frames(24))}
        _cache["set"] = (sil1, sil2, spk, ges)
    return _cache["set"]
