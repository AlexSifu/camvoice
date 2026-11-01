"""Índice offline de un clip: descriptores, vecinos, apertura de boca.

Es lo que permite al director (director.py):

- **entrar** en un clip por el fotograma más parecido al que se está emitiendo
  (en vez de por el fotograma 0), con una búsqueda vectorial en tiempo de
  ejecución sobre los descriptores (n x 768 flotantes: < 1 ms);
- **variar** la reproducción sin bucles visibles, saltando a un vecino
  parecido de otra parte del mismo clip («video textures»);
- **guiar la boca** con la voz, eligiendo entre vecinos el que tiene la
  apertura de boca más cercana a la que pide el audio.

Descriptor: luma reducida a 32x24, centrada y normalizada (distancia euclídea
entre vectores unitarios). Vecinos: los `k` más cercanos fuera de una banda
temporal, calculados por bloques de filas para no materializar la matriz
n x n completa; el tamaño de bloque sale de un presupuesto de memoria.
`d_ok` = percentil 90 de la distancia entre fotogramas consecutivos: un salto
por debajo de esa distancia es tan suave como el movimiento normal del clip.

Apertura de boca: caja de la cara con el clasificador Haar que incluye OpenCV
(mediana sobre unas cuantas muestras) y, dentro de su tercio inferior, la
proporción de píxeles oscuros (la cavidad bucal). Si no se encuentra la cara
se usa la zona en la que la guía de encuadre coloca la boca. Es una medida
relativa por clip, normalizada a 0..1 entre sus percentiles 5 y 95.

Versión 2 añade, por fotograma, el **brillo medio** (luma 0..255) y la
**posición y escala de la cabeza** (centro x, y y ancho de la cara relativos
al fotograma; Haar cada pocos fotogramas e interpolación entre medias). El
director los usa para elegir entradas compatibles (misma cabeza, misma luz) y
reducir la doble imagen de los fundidos. Si no se detecta la cara, `head`
queda a NaN y ese término no cuenta.

Versión 3 añade, por fotograma, la **miniatura de luma sin normalizar**
(`thumbs`, 24x32 uint8: la misma reducción que el descriptor, antes de centrar
y normalizar) y la **fiabilidad de la cabeza** (`head_gap`: distancia en
fotogramas a la detección Haar real más cercana; inf si no hubo ninguna). Con
la miniatura el director mide la diferencia real entre dos fotogramas por
zonas (cara y torso) en niveles de gris, en vez de una distancia global
relativa al clip; con `head_gap` descarta como «no fiable» la cabeza
interpolada lejos de una detección. Un índice v2 en disco se recalcula solo.

El cálculo es cancelable (`cancel()` devuelve True) e informa del progreso.
El resultado se guarda junto a la caché como `.idx.npz`; un índice de otra
versión se recalcula solo (reindexado incremental: solo los clips cuyo
índice falta o está desfasado).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

__all__ = [
    "INDEX_VERSION",
    "IndexCancelled",
    "ClipIndex",
    "luma",
    "descriptor",
    "thumbnail",
    "build_index",
    "load_or_build_index",
    "index_path_for",
]

INDEX_VERSION = 3
DESC_W, DESC_H = 32, 24
DEFAULT_K = 8
DEFAULT_MEMORY_MB = 64
HEAD_STEP = 3
"""Se busca la cara uno de cada `HEAD_STEP` fotogramas (Haar ~ 3-6 ms)."""


class IndexCancelled(RuntimeError):
    """El usuario canceló la indexación."""


def luma(frame: np.ndarray, pixel_format: str, height: int) -> np.ndarray:
    """Plano de luminancia (h, w) uint8 de un fotograma NV12 o BGR."""
    if pixel_format == "nv12":
        return frame[:height]
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def thumbnail(y: np.ndarray) -> np.ndarray:
    """Luma reducida a (DESC_H, DESC_W) uint8 sin normalizar (misma reducción
    que el descriptor)."""
    return cv2.resize(y, (DESC_W, DESC_H), interpolation=cv2.INTER_AREA)


def _descriptor_from_thumb(thumb: np.ndarray) -> np.ndarray:
    small = thumb.astype(np.float32).ravel()
    small -= small.mean()
    norm = float(np.linalg.norm(small))
    return small / norm if norm > 1e-6 else small


def descriptor(y: np.ndarray) -> np.ndarray:
    return _descriptor_from_thumb(thumbnail(y))


def distances(desc: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Distancia euclídea entre `query` (D,) y cada fila de `desc` (n, D)."""
    dots = desc @ query
    return np.sqrt(np.maximum(0.0, 2.0 - 2.0 * dots))


@dataclass
class ClipIndex:
    descriptors: np.ndarray
    """(n, D) float32, filas unitarias."""

    mouth: np.ndarray
    """(n,) float32 en 0..1."""

    neighbors: np.ndarray
    """(n, k) int32; -1 = sin vecino."""

    neighbor_dist: np.ndarray
    """(n, k) float32."""

    d_ok: float
    face_found: bool = False
    mouth_range: float = 0.0
    """Diferencia entre percentiles 95 y 5 de la medida bruta de boca. Casi
    cero = la boca no se mueve en el clip (típico de un clip de silencio)."""

    brightness: np.ndarray | None = None
    """(n,) float32: luma media 0..255 de cada fotograma."""

    head: np.ndarray | None = None
    """(n, 3) float32: centro x, centro y y ancho de la cara (0..1); NaN si
    no se detectó."""

    thumbs: np.ndarray | None = None
    """(n, DESC_H, DESC_W) uint8: luma reducida sin normalizar (v3). None si
    el índice se construyó sin miniaturas (compatibilidad)."""

    head_gap: np.ndarray | None = None
    """(n,) float32: fotogramas hasta la detección Haar real más cercana
    (0 = detectada en este fotograma; inf = sin detección en el clip)."""

    def __post_init__(self) -> None:
        n = self.frame_count
        if self.brightness is None or len(self.brightness) != n:
            self.brightness = np.zeros(n, dtype=np.float32)
        if self.head is None or len(self.head) != n:
            self.head = np.full((n, 3), np.nan, dtype=np.float32)
        if self.thumbs is not None and (
            self.thumbs.ndim != 3 or self.thumbs.shape[0] != n
        ):
            self.thumbs = None
        if self.head_gap is None or len(self.head_gap) != n:
            finite = np.isfinite(self.head[:, 0])
            self.head_gap = np.where(finite, 0.0, np.inf).astype(np.float32)

    @property
    def head_found(self) -> bool:
        return bool(np.isfinite(self.head[:, 0]).any())

    @property
    def frame_count(self) -> int:
        return int(self.descriptors.shape[0])

    def nearest(self, query: np.ndarray, *, lo: int = 0, hi: int | None = None) -> tuple[int, float]:
        """Fotograma más parecido a `query` dentro de [lo, hi)."""
        hi = self.frame_count if hi is None else min(hi, self.frame_count)
        lo = max(0, min(lo, hi - 1))
        d = distances(self.descriptors[lo:hi], query)
        j = int(np.argmin(d))
        return lo + j, float(d[j])

    def save(self, path: Path, *, frame_count: int) -> None:
        tmp = path.with_name(path.name + ".tmp.npz")
        np.savez_compressed(
            tmp,
            version=np.int32(INDEX_VERSION),
            frame_count=np.int32(frame_count),
            descriptors=self.descriptors.astype(np.float16),
            mouth=self.mouth,
            neighbors=self.neighbors,
            neighbor_dist=self.neighbor_dist,
            d_ok=np.float32(self.d_ok),
            face_found=np.bool_(self.face_found),
            mouth_range=np.float32(self.mouth_range),
            brightness=self.brightness.astype(np.float32),
            head=self.head.astype(np.float32),
            head_gap=self.head_gap.astype(np.float32),
            thumbs=(
                self.thumbs.astype(np.uint8)
                if self.thumbs is not None
                else np.zeros((0, DESC_H, DESC_W), dtype=np.uint8)
            ),
        )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, *, frame_count: int) -> ClipIndex | None:
        try:
            with np.load(path) as z:
                if int(z["version"]) != INDEX_VERSION or int(z["frame_count"]) != frame_count:
                    return None
                return cls(
                    descriptors=z["descriptors"].astype(np.float32),
                    mouth=z["mouth"].astype(np.float32),
                    neighbors=z["neighbors"].astype(np.int32),
                    neighbor_dist=z["neighbor_dist"].astype(np.float32),
                    d_ok=float(z["d_ok"]),
                    face_found=bool(z["face_found"]),
                    mouth_range=float(z["mouth_range"]),
                    brightness=z["brightness"].astype(np.float32),
                    head=z["head"].astype(np.float32),
                    head_gap=z["head_gap"].astype(np.float32),
                    thumbs=(z["thumbs"].astype(np.uint8) if z["thumbs"].shape[0] else None),
                )
        except (OSError, KeyError, ValueError, EOFError):
            return None


# --------------------------------------------------------------------------- #
# Boca
# --------------------------------------------------------------------------- #

_cascade = None


def _face_cascade():
    global _cascade
    if _cascade is None:
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _cascade = cv2.CascadeClassifier(path)
    return _cascade


def mouth_roi(lumas: list[np.ndarray]) -> tuple[tuple[int, int, int, int], bool]:
    """(x0, y0, x1, y1) de la zona de la boca y si se detectó la cara."""
    h, w = lumas[0].shape[:2]
    boxes = []
    cascade = _face_cascade()
    if not cascade.empty():
        for y in lumas:
            found = cascade.detectMultiScale(
                y, scaleFactor=1.15, minNeighbors=5, minSize=(w // 8, h // 8)
            )
            if len(found):
                boxes.append(max(found, key=lambda b: b[2] * b[3]))
    if len(boxes) >= max(1, len(lumas) // 3):
        fx, fy, fw, fh = np.median(np.array(boxes), axis=0)
        x0, x1 = fx + 0.25 * fw, fx + 0.75 * fw
        y0, y1 = fy + 0.66 * fh, fy + 0.96 * fh
        found = True
    else:  # zona de la boca según la guía de encuadre (cara centrada)
        x0, x1 = 0.38 * w, 0.62 * w
        y0, y1 = 0.55 * h, 0.78 * h
        found = False
    x0, x1 = int(max(0, x0)), int(min(w, max(x0 + 2, x1)))
    y0, y1 = int(max(0, y0)), int(min(h, max(y0 + 2, y1)))
    return (x0, y0, x1, y1), found


def _detect_head(y: np.ndarray) -> tuple[float, float, float] | None:
    """(cx, cy, ancho) de la cara mayor, relativos al fotograma; None si no hay."""
    cascade = _face_cascade()
    if cascade.empty():
        return None
    h, w = y.shape[:2]
    scale = 1.0
    if w > 320:  # a 320 px la cara sigue siendo grande y Haar es 4x más rápido
        scale = 320.0 / w
        y = cv2.resize(y, (320, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    hh, ww = y.shape[:2]
    found = cascade.detectMultiScale(y, scaleFactor=1.15, minNeighbors=5, minSize=(ww // 8, hh // 8))
    if not len(found):
        return None
    fx, fy, fw, fh = max(found, key=lambda b: b[2] * b[3])
    return (fx + fw / 2) / ww, (fy + fh / 2) / hh, fw / ww


def _interpolate_head(samples: dict[int, tuple[float, float, float]], n: int) -> np.ndarray:
    head = np.full((n, 3), np.nan, dtype=np.float32)
    if len(samples) < max(1, (n // HEAD_STEP) // 4):
        return head  # cara detectada en muy pocas muestras: no es fiable
    xs = np.array(sorted(samples), dtype=np.float32)
    vals = np.array([samples[int(i)] for i in xs], dtype=np.float32)
    grid = np.arange(n, dtype=np.float32)
    for c in range(3):
        head[:, c] = np.interp(grid, xs, vals[:, c])
    return head


def _head_gaps(samples: dict[int, tuple[float, float, float]], n: int) -> np.ndarray:
    """Distancia (fotogramas) de cada fotograma a la detección real más cercana."""
    if not samples:
        return np.full(n, np.inf, dtype=np.float32)
    xs = np.array(sorted(samples), dtype=np.float32)
    grid = np.arange(n, dtype=np.float32)
    pos = np.searchsorted(xs, grid)
    left = xs[np.clip(pos - 1, 0, len(xs) - 1)]
    right = xs[np.clip(pos, 0, len(xs) - 1)]
    return np.minimum(np.abs(grid - left), np.abs(grid - right)).astype(np.float32)


def _normalize_mouth(raw: np.ndarray) -> tuple[np.ndarray, float]:
    lo, hi = np.percentile(raw, [5, 95])
    spread = float(hi - lo)
    if spread < 0.01:
        return np.zeros_like(raw, dtype=np.float32), spread
    return np.clip((raw - lo) / spread, 0.0, 1.0).astype(np.float32), spread


# --------------------------------------------------------------------------- #
# Construcción
# --------------------------------------------------------------------------- #


def _top_k_neighbors(
    desc: np.ndarray,
    k: int,
    min_gap: int,
    memory_mb: float,
    cancel: Callable[[], bool] | None,
    progress: Callable[[int, int, str], None] | None,
) -> tuple[np.ndarray, np.ndarray]:
    n = desc.shape[0]
    k_eff = max(0, min(k, n - 2 * min_gap - 1))
    nbr = np.full((n, max(1, k)), -1, dtype=np.int32)
    nd = np.full((n, max(1, k)), np.inf, dtype=np.float32)
    if k_eff <= 0:
        return nbr, nd
    rows = int(max(16, min(n, memory_mb * 1024 * 1024 // (n * 4 * 3))))
    cols = np.arange(n)
    for start in range(0, n, rows):
        if cancel is not None and cancel():
            raise IndexCancelled("Indexación cancelada.")
        stop = min(n, start + rows)
        dots = desc[start:stop] @ desc.T  # (rows, n): el único bloque en memoria
        d = np.sqrt(np.maximum(0.0, 2.0 - 2.0 * dots))
        band = np.abs(cols[None, :] - np.arange(start, stop)[:, None]) <= min_gap
        d[band] = np.inf
        part = np.argpartition(d, k_eff - 1, axis=1)[:, :k_eff]
        pd = np.take_along_axis(d, part, axis=1)
        order = np.argsort(pd, axis=1)
        nbr[start:stop, :k_eff] = np.take_along_axis(part, order, axis=1)
        nd[start:stop, :k_eff] = np.take_along_axis(pd, order, axis=1)
        if progress:
            progress(stop, n, "Buscando fotogramas parecidos")
    return nbr, nd


def build_index(
    frames,  # noqa: ANN001 - ClipCache o secuencia de fotogramas
    *,
    pixel_format: str,
    height: int,
    fps: int,
    k: int = DEFAULT_K,
    memory_mb: float = DEFAULT_MEMORY_MB,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ClipIndex:
    n = len(frames)
    if n == 0:
        raise ValueError("No se puede indexar un clip vacío.")
    get = frames.frame if hasattr(frames, "frame") else frames.__getitem__

    sample_idx = np.linspace(0, n - 1, num=min(n, 12)).astype(int)
    roi, face_found = mouth_roi([np.ascontiguousarray(luma(get(int(i)), pixel_format, height)) for i in sample_idx])
    x0, y0, x1, y1 = roi
    ref = np.concatenate(
        [luma(get(int(i)), pixel_format, height)[y0:y1, x0:x1].ravel() for i in sample_idx]
    )
    dark_thr = float(np.percentile(ref, 50)) * 0.62

    desc = np.empty((n, DESC_W * DESC_H), dtype=np.float32)
    thumbs = np.empty((n, DESC_H, DESC_W), dtype=np.uint8)
    raw_mouth = np.empty(n, dtype=np.float32)
    bright = np.empty(n, dtype=np.float32)
    heads: dict[int, tuple[float, float, float]] = {}
    for i in range(n):
        if cancel is not None and i % 64 == 0 and cancel():
            raise IndexCancelled("Indexación cancelada.")
        y = luma(get(i), pixel_format, height)
        thumbs[i] = thumbnail(y)
        desc[i] = _descriptor_from_thumb(thumbs[i])
        raw_mouth[i] = float(np.mean(y[y0:y1, x0:x1] < dark_thr))
        bright[i] = float(cv2.mean(y)[0])
        if i % HEAD_STEP == 0 or i == n - 1:
            found = _detect_head(np.ascontiguousarray(y))
            if found is not None:
                heads[i] = found
        if progress and (i % 30 == 0 or i == n - 1):
            progress(i + 1, n, "Analizando fotogramas")

    if n >= 2:
        consecutive = np.sqrt(np.maximum(0.0, 2.0 - 2.0 * np.sum(desc[1:] * desc[:-1], axis=1)))
        d_ok = float(max(1e-3, np.percentile(consecutive, 90)))
    else:
        d_ok = 1e-3

    min_gap = max(2, fps // 2)
    nbr, nd = _top_k_neighbors(desc, k, min_gap, memory_mb, cancel, progress)
    mouth, spread = _normalize_mouth(raw_mouth)
    head = _interpolate_head(heads, n)
    gaps = _head_gaps(heads, n)
    return ClipIndex(desc, mouth, nbr, nd, d_ok, face_found, spread, bright, head,
                     thumbs=thumbs, head_gap=gaps)


def index_path_for(cache) -> Path:  # noqa: ANN001 - ClipCache
    raw = Path(cache.raw_path)
    return raw.with_name(raw.stem + ".idx.npz")


def load_or_build_index(
    cache,  # noqa: ANN001 - ClipCache
    *,
    force: bool = False,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    memory_mb: float = DEFAULT_MEMORY_MB,
) -> ClipIndex:
    meta = cache.meta
    path = index_path_for(cache)
    if not force and path.exists():
        idx = ClipIndex.load(path, frame_count=meta.frame_count)
        if idx is not None:
            return idx
    idx = build_index(
        cache,
        pixel_format=meta.pixel_format,
        height=meta.height,
        fps=meta.fps,
        cancel=cancel,
        progress=progress,
        memory_mb=memory_mb,
    )
    try:
        idx.save(path, frame_count=meta.frame_count)
    except OSError:
        pass  # sin escritura: el índice sirve igual en memoria para esta sesión
    return idx
