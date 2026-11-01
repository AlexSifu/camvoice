"""Normalización de encuadre y color entre clips (offline, al preparar).

Si dos clips se grabaron con la cabeza unos centímetros desplazada o con otra
luz, cualquier transición entre ellos «salta» aunque sea un fundido. Aquí se
estima, una vez por clip, una corrección pequeña respecto al clip de
referencia (el primer clip de silencio):

- **Encuadre**: alineación ECC euclídea (traslación + rotación) sobre luma
  reducida. Solo se acepta si es pequeña (≤ 8 % de desplazamiento, ≤ 4° de
  giro): una diferencia mayor indica otra escena, y «arreglarla» deformaría
  la imagen; en ese caso se informa y se deja el encuadre como está.
- **Color**: transferencia de media y desviación por canal en Lab (Reinhard)
  con la ganancia limitada a [0,7, 1,4], aplicada como tabla de consulta.

Todo es CPU clásica (OpenCV), determinista y se calcula con unas pocas
muestras del clip. El resultado se aplica dentro de `prepare_clip` y su huella
forma parte de la clave de caché, así que cambiar la referencia regenera las
cachés afectadas. El clip de referencia no se transforma (clave heredada).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import VideoConfig
from .cache import fit_frame

__all__ = ["Normalization", "sample_frames", "estimate_alignment", "compute_normalization"]

MAX_SHIFT_FRAC = 0.08
MAX_ROTATION_DEG = 4.0
GAIN_RANGE = (0.7, 1.4)
_ALIGN_WIDTH = 160


@dataclass(frozen=True)
class Normalization:
    matrix: np.ndarray | None
    """Matriz afín 2x3 a resolución de salida, o None si no se corrige encuadre."""

    luts: np.ndarray | None
    """Tablas 3x256 (L, a, b) o None si no se corrige color."""

    notes: tuple[str, ...] = ()

    @property
    def identity(self) -> bool:
        return self.matrix is None and self.luts is None

    @property
    def fingerprint(self) -> str:
        """Huella para la clave de caché. Vacía si no hay corrección."""
        if self.identity:
            return ""
        h = hashlib.sha1(b"norm1")
        if self.matrix is not None:
            h.update(np.round(self.matrix, 3).astype(np.float32).tobytes())
        if self.luts is not None:
            h.update(self.luts.tobytes())
        return "norm1:" + h.hexdigest()[:12]

    def apply(self, bgr: np.ndarray) -> np.ndarray:
        out = bgr
        if self.matrix is not None:
            h, w = out.shape[:2]
            out = cv2.warpAffine(
                out, self.matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )
        if self.luts is not None:
            lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
            for c in range(3):
                lab[..., c] = cv2.LUT(lab[..., c], self.luts[c])
            out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        return out


IDENTITY = Normalization(None, None)


def sample_frames(path: str | Path, cfg: VideoConfig, count: int = 5) -> list[np.ndarray]:
    """Fotogramas BGR repartidos por el clip, ya encajados como en la caché."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        targets = (
            sorted({int(total * (i + 0.5) / count) for i in range(count)}) if total > 0 else []
        )
        frames: list[np.ndarray] = []
        if targets:
            for t in targets:
                cap.set(cv2.CAP_PROP_POS_FRAMES, t)
                ok, frame = cap.read()
                if ok and frame is not None:
                    frames.append(frame)
        if not frames:  # contenedores sin índice: leer secuencialmente
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            for _ in range(count):
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                frames.append(frame)
    finally:
        cap.release()
    out = []
    for f in frames:
        fitted = fit_frame(f, cfg.width, cfg.height, cfg.fit, tuple(cfg.letterbox_color))
        if cfg.mirror:
            fitted = cv2.flip(fitted, 1)
        out.append(fitted)
    return out


def _representative(frames: list[np.ndarray]) -> np.ndarray:
    if len(frames) == 1:
        return frames[0]
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def _small_gray(bgr: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = bgr.shape[:2]
    scale = _ALIGN_WIDTH / w
    small = cv2.resize(bgr, (_ALIGN_WIDTH, max(8, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    return cv2.GaussianBlur(gray, (5, 5), 0), scale


def estimate_alignment(src: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray | None, str]:
    """Matriz 2x3 que lleva `src` sobre `ref`, o (None, motivo)."""
    g_src, scale = _small_gray(src)
    g_ref, _ = _small_gray(ref)
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5)
    try:
        _, warp = cv2.findTransformECC(g_ref, g_src, warp, cv2.MOTION_EUCLIDEAN, criteria, None, 5)
    except cv2.error:
        return None, "la alineación no convergió"
    h, w = src.shape[:2]
    tx, ty = float(warp[0, 2]) / scale, float(warp[1, 2]) / scale
    angle = math.degrees(math.atan2(float(warp[1, 0]), float(warp[0, 0])))
    if abs(tx) > MAX_SHIFT_FRAC * w or abs(ty) > MAX_SHIFT_FRAC * h or abs(angle) > MAX_ROTATION_DEG:
        return None, (
            f"diferencia de encuadre demasiado grande para corregirla "
            f"({tx:+.0f}px, {ty:+.0f}px, {angle:+.1f}°)"
        )
    if abs(tx) < 0.5 and abs(ty) < 0.5 and abs(angle) < 0.05:
        return None, "encuadre ya alineado"
    full = warp.astype(np.float64).copy()
    full[0, 2], full[1, 2] = tx, ty
    # ECC devuelve la transformación ref->src; para llevar src a ref se invierte.
    inv = cv2.invertAffineTransform(full)
    return inv.astype(np.float32), f"encuadre corregido ({-tx:+.0f}px, {-ty:+.0f}px, {-angle:+.1f}°)"


def _lab_stats(frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stack = []
    for f in frames:
        h, w = f.shape[:2]
        center = f[h // 8 : h - h // 8, w // 8 : w - w // 8]
        stack.append(cv2.cvtColor(center, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32))
    data = np.concatenate(stack)
    return data.mean(axis=0), data.std(axis=0) + 1e-3


def _color_luts(src_frames, ref_frames) -> tuple[np.ndarray | None, str]:  # noqa: ANN001
    mu_s, sd_s = _lab_stats(src_frames)
    mu_r, sd_r = _lab_stats(ref_frames)
    gain = np.clip(sd_r / sd_s, *GAIN_RANGE)
    if np.all(np.abs(mu_s - mu_r) < 1.5) and np.all(np.abs(gain - 1) < 0.03):
        return None, "color ya igualado"
    x = np.arange(256, dtype=np.float32)
    luts = np.stack(
        [np.clip((x - mu_s[c]) * gain[c] + mu_r[c], 0, 255).round().astype(np.uint8) for c in range(3)]
    )
    return luts, f"color igualado (ΔL {mu_r[0] - mu_s[0]:+.0f})"


def compute_normalization(
    path: str | Path,
    reference_frames: list[np.ndarray],
    cfg: VideoConfig,
    *,
    is_reference: bool = False,
) -> Normalization:
    """Corrección de `path` respecto a los fotogramas de referencia dados."""
    if is_reference or not reference_frames:
        return IDENTITY
    frames = sample_frames(path, cfg)
    if not frames:
        return Normalization(None, None, ("no se pudieron leer muestras; sin normalizar",))
    matrix, note_a = estimate_alignment(_representative(frames), _representative(reference_frames))
    if matrix is not None:
        frames = [
            cv2.warpAffine(f, matrix, (f.shape[1], f.shape[0]), borderMode=cv2.BORDER_REPLICATE)
            for f in frames
        ]
    luts, note_c = _color_luts(frames, reference_frames)
    return Normalization(matrix, luts, (note_a, note_c))
