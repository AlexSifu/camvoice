"""Generador de clips sintéticos de prueba.

Permite validar la cadena completa —preparación, bucle, transiciones, cámara
virtual, rendimiento— antes de grabar nada. Los dos clips comparten fondo,
iluminación y encuadre, igual que se pide a las grabaciones reales, así que
también sirven para ver si una transición está bien ajustada.

No pretenden parecer una persona: son una cara esquemática cuya boca se mueve.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

__all__ = ["make_test_clips", "render_face"]

_SKIN = (150, 185, 215)
_BG_TOP = (70, 55, 45)
_BG_BOTTOM = (120, 100, 85)
_MOUTH = (60, 60, 110)
_EYE = (40, 35, 30)


def _background(width: int, height: int) -> np.ndarray:
    """Degradado vertical fijo: hace visible cualquier salto entre clips."""
    ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    top = np.array(_BG_TOP, dtype=np.float32)
    bottom = np.array(_BG_BOTTOM, dtype=np.float32)
    canvas = top[None, None, :] * (1 - ramp[:, :, None]) + bottom[None, None, :] * ramp[:, :, None]
    return np.repeat(canvas, width, axis=1).astype(np.uint8)


def render_face(
    width: int,
    height: int,
    t: float,
    *,
    mouth_open: float,
    label: str,
) -> np.ndarray:
    """Un fotograma de la cara esquemática.

    `t` en segundos (para parpadeo y respiración) y `mouth_open` en 0..1.
    """
    frame = _background(width, height)
    cx = width // 2
    # Respiración: un desplazamiento vertical lento común a los dos clips, para
    # que la transición no tenga que absorber también un salto de postura.
    breathe = int(round(math.sin(t * 2.0 * math.pi / 4.0) * height * 0.006))
    cy = int(height * 0.52) + breathe
    head_w = int(width * 0.21)
    head_h = int(height * 0.30)

    cv2.ellipse(frame, (cx, cy), (head_w, head_h), 0, 0, 360, _SKIN, -1, cv2.LINE_AA)
    cv2.ellipse(
        frame,
        (cx, cy - int(head_h * 0.72)),
        (int(head_w * 1.02), int(head_h * 0.42)),
        0, 180, 360, (70, 60, 55), -1, cv2.LINE_AA,
    )

    # Parpadeo cada ~3,2 s, de 120 ms.
    phase = (t % 3.2) / 3.2
    blink = phase > 0.962
    eye_dx = int(head_w * 0.42)
    eye_y = cy - int(head_h * 0.18)
    for sign in (-1, 1):
        center = (cx + sign * eye_dx, eye_y)
        if blink:
            cv2.line(
                frame,
                (center[0] - int(head_w * 0.18), center[1]),
                (center[0] + int(head_w * 0.18), center[1]),
                _EYE, max(1, height // 200), cv2.LINE_AA,
            )
        else:
            cv2.ellipse(
                frame, center,
                (int(head_w * 0.18), int(head_h * 0.10)),
                0, 0, 360, (245, 245, 245), -1, cv2.LINE_AA,
            )
            cv2.circle(frame, center, max(2, int(head_h * 0.055)), _EYE, -1, cv2.LINE_AA)

    mouth_y = cy + int(head_h * 0.42)
    mouth_w = int(head_w * 0.46)
    mouth_h = max(2, int(head_h * (0.045 + 0.26 * mouth_open)))
    cv2.ellipse(
        frame, (cx, mouth_y), (mouth_w, mouth_h), 0, 0, 360, _MOUTH, -1, cv2.LINE_AA
    )
    if mouth_open > 0.35:  # dientes: refuerza la sensación de boca abierta
        cv2.ellipse(
            frame,
            (cx, mouth_y - int(mouth_h * 0.55)),
            (int(mouth_w * 0.78), max(1, int(mouth_h * 0.22))),
            0, 0, 360, (235, 235, 235), -1, cv2.LINE_AA,
        )

    cv2.putText(
        frame, label, (12, height - 14), cv2.FONT_HERSHEY_SIMPLEX,
        max(0.4, width / 1280), (255, 255, 255), max(1, width // 640), cv2.LINE_AA,
    )
    return frame


def _write(path: Path, width: int, height: int, fps: int, frames: int, mouth_fn, label: str) -> Path:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
    if not writer.isOpened():
        raise RuntimeError(
            f"OpenCV no pudo crear {path.name}. Prueba otra carpeta de salida."
        )
    try:
        for i in range(frames):
            t = i / fps
            writer.write(render_face(width, height, t, mouth_open=mouth_fn(t), label=label))
    finally:
        writer.release()
    return path


def make_test_clips(
    outdir: Path,
    *,
    width: int = 640,
    height: int = 480,
    fps: int = 30,
    seconds: float = 6.0,
) -> tuple[Path, Path]:
    """Escribe `habla-prueba.mp4` y `silencio-prueba.mp4`. Devuelve sus rutas."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    frames = max(2, int(round(seconds * fps)))

    def speaking(t: float) -> float:
        # Dos osciladores no armónicos: la boca no cae en un patrón obvio.
        a = math.sin(t * 2.0 * math.pi * 3.1)
        b = math.sin(t * 2.0 * math.pi * 1.7 + 1.2)
        return float(min(1.0, max(0.05, 0.5 + 0.38 * a + 0.16 * b)))

    speaking_path = _write(
        outdir / "habla-prueba.mp4", width, height, fps, frames, speaking, "HABLANDO"
    )
    silent_path = _write(
        outdir / "silencio-prueba.mp4", width, height, fps, frames, lambda t: 0.0, "SILENCIO"
    )
    return speaking_path, silent_path
