"""Caché de fotogramas: se decodifica una vez, se reproduce miles de veces.

Decisión central del proyecto. Decodificar H.264 en cada vuelta del bucle es
justo lo que el plan pide evitar, y además es irregular: los fotogramas clave
cuestan mucho más que los intermedios, lo que produce picos de CPU y jitter.

Aquí cada clip se convierte UNA vez a un archivo de fotogramas en crudo, ya en
la resolución, los FPS, el encuadre, el espejo y el formato de píxel de salida.
En ejecución, obtener un fotograma es indexar un `np.memmap`: una copia de
memoria, coste constante y predecible, sin decodificador en el bucle.

Con formato NV12 (el nativo de OBS Virtual Camera) la cadena completa queda sin
conversiones de color: el fotograma sale del memmap y entra en la cámara tal
cual, y el fundido cruzado es una interpolación lineal sobre los bytes, que en
YUV da exactamente el mismo resultado que en RGB porque la conversión entre
ambos es afín.

Un fotograma 640x480 NV12 ocupa 460.800 bytes: 6,9 MB por segundo de clip a
15 FPS. El sistema operativo mantiene en RAM solo las páginas en uso.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import VideoConfig, cache_dir, frame_bytes, frame_shape

__all__ = ["ClipCacheError", "ClipMeta", "ClipCache", "prepare_clip", "prune_cache", "probe_clip"]

CACHE_FORMAT_VERSION = 1
MAX_CLIP_SECONDS = 300.0
ASSUMED_FPS = 25.0
"""FPS que se asume cuando el contenedor no declara una cadencia creíble."""


class ClipCacheError(RuntimeError):
    """Error al preparar o abrir un clip, con mensaje apto para el usuario."""


# --------------------------------------------------------------------------- #
# Metadatos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ClipMeta:
    version: int
    source: str
    source_size: int
    source_mtime_ns: int
    source_fps: float
    source_width: int
    source_height: int
    width: int
    height: int
    fps: int
    fit: str
    mirror: bool
    pixel_format: str
    letterbox_color: tuple[int, int, int]
    frame_count: int
    frame_bytes: int
    truncated: bool
    created_at: float

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    @property
    def total_bytes(self) -> int:
        return self.frame_count * self.frame_bytes

    @property
    def shape(self) -> tuple[int, ...]:
        return frame_shape(self.width, self.height, self.pixel_format)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["letterbox_color"] = list(self.letterbox_color)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ClipMeta:
        data = dict(data)
        data["letterbox_color"] = tuple(int(x) for x in data.get("letterbox_color", (0, 0, 0)))
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True, slots=True)
class ClipProbe:
    """Lo que se puede saber de un clip sin prepararlo."""

    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float
    fourcc: str

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


def probe_clip(path: str | os.PathLike[str]) -> ClipProbe:
    """Lee las propiedades de un video. Levanta ClipCacheError si no se abre."""
    p = Path(path)
    if not p.exists():
        raise ClipCacheError(f"El archivo no existe: {p}")
    cap = cv2.VideoCapture(str(p))
    try:
        if not cap.isOpened():
            raise ClipCacheError(
                f"No se pudo abrir «{p.name}». Comprueba que es un video con un "
                "códec que OpenCV pueda leer (MP4/H.264 y AVI/MJPEG son seguros)."
            )
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        raw = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        fourcc = "".join(chr((raw >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")

        ok, frame = cap.read()
        if not ok or frame is None:
            raise ClipCacheError(
                f"«{p.name}» se abre pero no entrega fotogramas. El archivo puede "
                "estar incompleto o usar un códec no soportado."
            )
        if width <= 0 or height <= 0:
            height, width = frame.shape[:2]
    finally:
        cap.release()

    effective_fps = fps if 1.0 <= fps <= 240.0 else ASSUMED_FPS
    duration = count / effective_fps if count > 0 else 0.0
    return ClipProbe(width, height, fps, max(0, count), duration, fourcc)


# --------------------------------------------------------------------------- #
# Clave de caché
# --------------------------------------------------------------------------- #


def cache_key(path: str | os.PathLike[str], cfg: VideoConfig) -> str:
    """Identidad de una caché: fuente + todos los parámetros que la afectan.

    Incluye tamaño y mtime del archivo original, así que reemplazar el video por
    otro con el mismo nombre invalida la caché automáticamente.
    """
    p = Path(path).resolve()
    try:
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime_ns
    except OSError:
        size, mtime = -1, -1
    parts = (
        str(CACHE_FORMAT_VERSION),
        str(p).lower(),
        str(size),
        str(mtime),
        f"{cfg.width}x{cfg.height}@{cfg.fps}",
        cfg.fit,
        "mirror" if cfg.mirror else "direct",
        cfg.pixel_format,
        ",".join(str(int(c)) for c in cfg.letterbox_color),
    )
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{p.stem[:28]}-{digest}"


# --------------------------------------------------------------------------- #
# Geometría y formato
# --------------------------------------------------------------------------- #


def fit_frame(
    frame: np.ndarray, width: int, height: int, mode: str, pad_color: tuple[int, int, int]
) -> np.ndarray:
    """Lleva un fotograma BGR a (width, height) según el modo de ajuste.

    cover   recorta centrado para llenar el marco: no deforma y no deja barras,
            a costa de perder los bordes del encuadre original.
    contain cabe entero y rellena con barras: no deforma y no recorta.
    stretch deforma. Solo útil si el clip ya tiene una relación rara.
    """
    src_h, src_w = frame.shape[:2]
    if src_w == width and src_h == height:
        return frame
    if mode == "stretch":
        return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

    scale = (
        max(width / src_w, height / src_h) if mode == "cover"
        else min(width / src_w, height / src_h)
    )
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (new_w, new_h), interpolation=interp)

    if mode == "cover":
        x0 = max(0, (new_w - width) // 2)
        y0 = max(0, (new_h - height) // 2)
        return resized[y0 : y0 + height, x0 : x0 + width]

    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:, :] = pad_color
    x0 = (width - new_w) // 2
    y0 = (height - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def bgr_to_nv12(bgr: np.ndarray) -> np.ndarray:
    """BGR (h, w, 3) -> NV12 (h*3/2, w).

    OpenCV solo ofrece I420 (planos Y, U, V). NV12 quiere U y V entrelazados en
    un único plano, así que se reordena a mano. Se hace una vez por fotograma al
    preparar la caché, nunca en el bucle de reproducción.
    """
    h, w = bgr.shape[:2]
    if h % 2 or w % 2:
        raise ClipCacheError("NV12 exige alto y ancho pares.")
    i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
    nv12 = np.empty((h * 3 // 2, w), dtype=np.uint8)
    nv12[:h] = i420[:h]
    # En el diseño I420 de OpenCV los planos U y V ocupan h/4 filas cada uno,
    # justo detrás del plano Y.
    rows = h // 4
    u = i420[h : h + rows].reshape(-1)
    v = i420[h + rows : h + 2 * rows].reshape(-1)
    uv = nv12[h:].reshape(-1)
    uv[0::2] = u
    uv[1::2] = v
    return nv12


def nv12_to_bgr(nv12: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)


def to_display_bgr(frame: np.ndarray, pixel_format: str) -> np.ndarray:
    """Convierte un fotograma interno a BGR para la vista previa."""
    if pixel_format == "nv12":
        return nv12_to_bgr(frame)
    return frame


def encode_frame(bgr: np.ndarray, pixel_format: str) -> np.ndarray:
    if pixel_format == "nv12":
        return bgr_to_nv12(bgr)
    if pixel_format == "bgr":
        return np.ascontiguousarray(bgr)
    raise ClipCacheError(f"Formato de píxel no soportado: {pixel_format!r}")


# --------------------------------------------------------------------------- #
# Preparación
# --------------------------------------------------------------------------- #

ProgressFn = Callable[[int, int, str], None]
"""(hechos, total_estimado, mensaje). `total` puede ser 0 si no se conoce."""


def prepare_clip(
    path: str | os.PathLike[str],
    cfg: VideoConfig,
    *,
    force: bool = False,
    progress: ProgressFn | None = None,
    directory: Path | None = None,
) -> "ClipCache":
    """Prepara (o reutiliza) la caché de un clip y devuelve el acceso abierto."""
    source = Path(path)
    directory = directory or cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    key = cache_key(source, cfg)
    raw_path = directory / f"{key}.raw"
    meta_path = directory / f"{key}.json"

    if not force and raw_path.exists() and meta_path.exists():
        try:
            meta = ClipMeta.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
            if raw_path.stat().st_size == meta.total_bytes and meta.frame_count > 0:
                if progress:
                    progress(meta.frame_count, meta.frame_count, "Caché reutilizada")
                return ClipCache(raw_path, meta)
        except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
            pass  # caché ilegible: se regenera

    meta = _transcode(source, cfg, raw_path, meta_path, progress)
    return ClipCache(raw_path, meta)


def _transcode(
    source: Path, cfg: VideoConfig, raw_path: Path, meta_path: Path, progress: ProgressFn | None
) -> ClipMeta:
    probe = probe_clip(source)
    bytes_per_frame = frame_bytes(cfg.width, cfg.height, cfg.pixel_format)
    budget_frames = max(1, int(cfg.max_cache_mb) * 1024 * 1024 // bytes_per_frame)
    hard_cap = int(MAX_CLIP_SECONDS * cfg.fps)
    max_frames = min(budget_frames, hard_cap)

    src_fps = probe.fps if 1.0 <= probe.fps <= 240.0 else ASSUMED_FPS
    estimated = (
        min(max_frames, int(round(probe.frame_count * cfg.fps / src_fps)))
        if probe.frame_count > 0
        else 0
    )

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise ClipCacheError(f"No se pudo abrir «{source.name}» para procesarlo.")

    tmp_raw = raw_path.with_suffix(".raw.tmp")
    written = 0
    truncated = False
    src_index = -1
    current: np.ndarray | None = None

    try:
        with open(tmp_raw, "wb", buffering=1024 * 1024) as fh:
            for out_index in range(max_frames):
                needed = int(round(out_index * src_fps / cfg.fps))
                while src_index < needed:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        current = None
                        break
                    src_index += 1
                    current = frame
                if current is None:
                    break

                fitted = fit_frame(
                    current, cfg.width, cfg.height, cfg.fit, tuple(cfg.letterbox_color)
                )
                if cfg.mirror:
                    fitted = cv2.flip(fitted, 1)
                fh.write(encode_frame(fitted, cfg.pixel_format).tobytes())
                written += 1
                if progress and (written % 15 == 0 or written == 1):
                    progress(written, estimated, f"Preparando {source.name}")
            else:
                truncated = True
    except OSError as exc:
        tmp_raw.unlink(missing_ok=True)
        raise ClipCacheError(
            f"No se pudo escribir la caché de «{source.name}»: {exc}. "
            "Comprueba el espacio libre en disco."
        ) from exc
    finally:
        cap.release()

    if written == 0:
        tmp_raw.unlink(missing_ok=True)
        raise ClipCacheError(
            f"«{source.name}» no entregó ningún fotograma utilizable."
        )

    meta = ClipMeta(
        version=CACHE_FORMAT_VERSION,
        source=str(source.resolve()),
        source_size=source.stat().st_size,
        source_mtime_ns=source.stat().st_mtime_ns,
        source_fps=probe.fps,
        source_width=probe.width,
        source_height=probe.height,
        width=cfg.width,
        height=cfg.height,
        fps=cfg.fps,
        fit=cfg.fit,
        mirror=bool(cfg.mirror),
        pixel_format=cfg.pixel_format,
        letterbox_color=tuple(int(c) for c in cfg.letterbox_color),
        frame_count=written,
        frame_bytes=bytes_per_frame,
        truncated=truncated,
        created_at=time.time(),
    )

    tmp_raw.replace(raw_path)
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
    tmp_meta.replace(meta_path)
    if progress:
        progress(written, written, f"{source.name}: {written} fotogramas")
    return meta


# --------------------------------------------------------------------------- #
# Acceso
# --------------------------------------------------------------------------- #


class ClipCache:
    """Acceso de solo lectura a los fotogramas preparados de un clip."""

    def __init__(self, raw_path: Path, meta: ClipMeta) -> None:
        self.raw_path = raw_path
        self.meta = meta
        self._mm: np.memmap | None = None
        self._ram: np.ndarray | None = None
        self.open()

    # ------------------------------------------------------------------ #

    def open(self) -> None:
        if self._mm is not None or self._ram is not None:
            return
        try:
            self._mm = np.memmap(
                self.raw_path,
                dtype=np.uint8,
                mode="r",
                shape=(self.meta.frame_count, *self.meta.shape),
            )
        except (OSError, ValueError) as exc:
            raise ClipCacheError(
                f"No se pudo abrir la caché {self.raw_path.name}: {exc}"
            ) from exc

    def close(self) -> None:
        self._mm = None
        self._ram = None

    def __enter__(self) -> ClipCache:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __len__(self) -> int:
        return self.meta.frame_count

    # ------------------------------------------------------------------ #

    def load_to_ram(self) -> None:
        """Copia la caché completa a RAM. Elimina cualquier acceso a disco en
        ejecución, a cambio de `meta.total_bytes` de memoria residente."""
        if self._ram is not None:
            return
        self.open()
        assert self._mm is not None
        self._ram = np.array(self._mm)
        self._mm = None

    def warm_up(self) -> None:
        """Recorre la caché para que el sistema la tenga en la caché de páginas.

        Sin esto, la primera vuelta del bucle lee del disco y puede provocar un
        tirón visible en los primeros segundos.
        """
        store = self._ram if self._ram is not None else self._mm
        if store is None:
            return
        step = max(1, 4096 // max(1, self.meta.shape[-1]))
        for i in range(0, self.meta.frame_count, max(1, self.meta.frame_count // 64 or 1)):
            _ = int(store[i].reshape(-1)[::step].sum())

    def frame(self, index: int) -> np.ndarray:
        """Fotograma `index` como vista de solo lectura, sin copia."""
        store = self._ram if self._ram is not None else self._mm
        if store is None:
            raise ClipCacheError("La caché del clip está cerrada.")
        n = self.meta.frame_count
        if n == 0:
            raise ClipCacheError("La caché del clip está vacía.")
        return store[min(max(index, 0), n - 1)]

    @property
    def shape(self) -> tuple[int, ...]:
        return self.meta.shape

    def describe(self) -> str:
        m = self.meta
        return (
            f"{Path(m.source).name}: {m.frame_count} fotogramas "
            f"({m.duration_s:.1f} s a {m.fps} FPS), {m.width}x{m.height} "
            f"{m.pixel_format.upper()}, {m.total_bytes / 1e6:.0f} MB"
            + (" [recortado por el tope de caché]" if m.truncated else "")
        )


# --------------------------------------------------------------------------- #


def prune_cache(keep: set[str] | None = None, *, directory: Path | None = None) -> int:
    """Borra entradas de caché no referenciadas. Devuelve los bytes liberados."""
    directory = directory or cache_dir()
    if not directory.exists():
        return 0
    keep = keep or set()
    freed = 0
    for raw in directory.glob("*.raw"):
        if raw.stem in keep:
            continue
        meta = raw.with_suffix(".json")
        try:
            freed += raw.stat().st_size
            raw.unlink()
            meta.unlink(missing_ok=True)
        except OSError:
            continue
    return freed
