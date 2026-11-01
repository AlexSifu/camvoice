"""Importación de la carpeta de grabaciones numeradas (`1.mp4` … `66.mp4`).

Flujo (lo usan la pestaña «Biblioteca» y `camvoice library`):

1. `scan_folder(carpeta)`: lista **solo** archivos de video cuyo nombre base
   sea un número (`^\\d+$`: `7`, `07`, `007`). No recursivo por defecto.
   `foto.jpg`, `movimientos.mp4.mp4`, `V_aud_001.mp4` o `WIN_….mp4` se
   ignoran sin error. Los números omitidos a propósito (50, 52, 59, 63) no se
   buscan. Si un número aparece con varias extensiones o con ceros a la
   izquierda (`7.mp4` y `07.mov`) **solo esa entrada** queda ambigua.
2. `validate_scan(...)`: abre cada video, comprueba que decodifica (principio,
   mitad y final), lee resolución, cadencia, duración y tamaño, y detecta
   cadencia variable. Estado: «Listo» (todo lo que se decodifica, con avisos si los hay) o «Error». Cancelable;
   lo ya validado se guarda (`probe_cache.json`) para reanudar.
3. `import_library(...)`: escribe el manifiesto v2 con una entrada por escena
   (ver video/library.py). Por defecto **referencia** los originales (no
   ocupa espacio); con `copy=True` los copia a `<biblioteca>/media` de forma
   atómica y cancelable. Nunca modifica ni borra los originales. Idempotente:
   repetirla no duplica nada y conserva atajos, gestos habilitados, anclas
   manuales y el resto de la configuración. Antes de escribir guarda una
   copia del manifiesto anterior para `rollback_library()`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..scenes import (
    CATALOG_GESTURE_SCENES,
    EXPECTED_COUNT,
    SCENES,
    SKIPPED,
    Scene,
    scene_by_number,
)

__all__ = [
    "VIDEO_EXTENSIONS",
    "STATUS_READY",
    "STATUS_REVIEW",
    "STATUS_ERROR",
    "STATUS_LABELS",
    "ImportCancelled",
    "ScanEntry",
    "ScanResult",
    "ProbeInfo",
    "scan_folder",
    "probe_video",
    "validate_scan",
    "space_needed",
    "import_library",
    "rollback_library",
    "default_source_folder",
]

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv"})
_NUMERIC = re.compile(r"^\d+$")

STATUS_READY = "listo"
STATUS_REVIEW = "revisar"
STATUS_ERROR = "error"
STATUS_LABELS = {STATUS_READY: "Listo", STATUS_REVIEW: "Por revisar", STATUS_ERROR: "Error"}

MIN_LOOP_S = 3.0
"""Un clip de bucle (silencio/habla) más corto se repetiría a la vista."""
MIN_ACTION_S = 0.8
MAX_SECONDS = 300.0
VFR_LONG_REVIEW = 0.30
"""Fracción de intervalos > 1,4x la mediana a partir de la cual se pide revisar."""
FREEZE_MS = 250.0
PROBE_VERSION = 3
"""Cambia si cambian los criterios de validación: invalida `probe_cache.json`.
3: todo archivo que se decodifica queda «Listo»; los avisos no bloquean."""
"""Un hueco mayor entre fotogramas se ve como congelación."""
"""Coeficiente de variación del intervalo entre fotogramas a partir del cual
la cadencia se considera muy irregular (el móvil graba a 28-30 FPS
variables; eso solo se anota, no se marca para revisar)."""
ProgressFn = Callable[[int, int, str], None]


class ImportCancelled(RuntimeError):
    """El usuario canceló la validación o la copia."""


def default_source_folder() -> Path:
    """Carpeta habitual de la app Cámara de Windows."""
    return Path.home() / "Pictures" / "Camera Roll"


# --------------------------------------------------------------------------- #
# Escaneo
# --------------------------------------------------------------------------- #


@dataclass
class ScanEntry:
    number: int
    scene: Scene | None
    paths: list[Path]

    @property
    def ambiguous(self) -> bool:
        return len(self.paths) > 1

    @property
    def path(self) -> Path | None:
        return self.paths[0] if len(self.paths) == 1 else None


@dataclass
class ScanResult:
    folder: Path
    entries: dict[int, ScanEntry] = field(default_factory=dict)
    """Escenas esperadas encontradas (una o varias rutas)."""

    unexpected: dict[int, list[Path]] = field(default_factory=dict)
    """Números que no son escenas (p. ej. 67) o que se omitieron a propósito."""

    ignored: list[Path] = field(default_factory=list)
    """Archivos sin nombre numérico o que no son video."""

    @property
    def missing(self) -> list[int]:
        return [s.number for s in SCENES if s.number not in self.entries]

    @property
    def ambiguous(self) -> list[int]:
        return sorted(n for n, e in self.entries.items() if e.ambiguous)

    @property
    def matched(self) -> dict[int, Path]:
        return {n: e.path for n, e in sorted(self.entries.items()) if e.path is not None}

    def summary(self) -> str:
        found = len(self.matched)
        parts = [f"{found}/{EXPECTED_COUNT} grabaciones detectadas"]
        parts.append(
            f"{len(SKIPPED)} omitidas a propósito ({', '.join(str(n) for n in sorted(SKIPPED))})"
        )
        if self.missing:
            parts.append("faltan: " + ", ".join(str(n) for n in self.missing))
        if self.ambiguous:
            parts.append("ambiguas: " + ", ".join(str(n) for n in self.ambiguous))
        if self.unexpected:
            parts.append(
                "números sin escena: " + ", ".join(str(n) for n in sorted(self.unexpected))
            )
        return " · ".join(parts)


def scan_folder(folder: str | os.PathLike[str], *, recursive: bool = False) -> ScanResult:
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"No existe la carpeta: {root}")
    files = sorted(root.rglob("*") if recursive else root.iterdir())
    result = ScanResult(root)
    for p in files:
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTENSIONS or not _NUMERIC.match(p.stem):
            result.ignored.append(p)
            continue
        number = int(p.stem)
        scene = scene_by_number(number)
        if scene is None:
            result.unexpected.setdefault(number, []).append(p)
            continue
        entry = result.entries.setdefault(number, ScanEntry(number, scene, []))
        entry.paths.append(p)
    return result


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #


@dataclass
class ProbeInfo:
    path: str
    size: int
    mtime_ns: int
    status: str
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frame_count: int = 0
    duration_s: float = 0.0
    vfr: bool = False
    mean_luma: float = 0.0
    thumbnail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProbeInfo:
        names = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in names})


def _read_at(cap: cv2.VideoCapture, index: int) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, index))
    ok, frame = cap.read()
    return frame if ok and frame is not None else None


def probe_video(
    path: str | os.PathLike[str],
    scene: Scene | None = None,
    *,
    thumbs_dir: Path | None = None,
    vfr_samples: int = 45,
) -> ProbeInfo:
    """Valida un video sin modificarlo. Nunca lanza: los fallos van a `issues`."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        return ProbeInfo(str(p), 0, 0, STATUS_ERROR, [f"No se puede leer: {exc}"])
    info = ProbeInfo(str(p), st.st_size, st.st_mtime_ns, STATUS_READY)
    if st.st_size == 0:
        info.status = STATUS_ERROR
        info.issues.append("El archivo está vacío (0 bytes).")
        return info
    cap = cv2.VideoCapture(str(p))
    try:
        if not cap.isOpened():
            info.status = STATUS_ERROR
            info.issues.append("No se pudo abrir: archivo dañado o códec no soportado.")
            return info
        info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        info.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        info.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        # Cadencia: intervalos entre marcas de tiempo de los primeros fotogramas.
        stamps: list[float] = []
        first = None
        for _ in range(max(2, vfr_samples)):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if first is None:
                first = frame
            stamps.append(float(cap.get(cv2.CAP_PROP_POS_MSEC)))
        if first is None:
            info.status = STATUS_ERROR
            info.issues.append("Se abre pero no entrega fotogramas (archivo incompleto).")
            return info
        if info.width <= 0 or info.height <= 0:
            info.height, info.width = first.shape[:2]
        # El primer intervalo de una webcam suele ser largo (arranque): no cuenta.
        diffs = np.diff(np.asarray(stamps))[1:]
        diffs = diffs[diffs > 0]
        if diffs.size >= 5:
            median = float(np.median(diffs))
            cv = float(np.std(diffs) / max(1e-6, float(np.mean(diffs))))
            measured = 1000.0 / median
            long_ratio = float(np.mean(diffs > 1.4 * median))
            if cv > 0.05 or (info.fps and abs(measured - info.fps) / info.fps > 0.05):
                info.vfr = True
                info.notes.append(
                    f"Cadencia variable (~{measured:.1f} FPS medidos, {info.fps:.2f} declarados, "
                    f"{100 * long_ratio:.0f} % de fotogramas perdidos por la cámara); "
                    "se remuestrea a la cadencia de salida."
                )
            # Solo es un problema visible una congelación real o huecos constantes.
            if float(diffs.max()) > FREEZE_MS or long_ratio > VFR_LONG_REVIEW:
                info.issues.append(
                    f"Cadencia muy irregular (hueco máx. {float(diffs.max()):.0f} ms, "
                    f"{100 * long_ratio:.0f} % de huecos largos): revisa si hay tirones."
                )
        fps = info.fps if 1.0 <= info.fps <= 240.0 else 25.0
        info.duration_s = info.frame_count / fps if info.frame_count > 0 else 0.0

        middle_idx = info.frame_count // 2
        last_idx = max(0, info.frame_count - 3)
        middle = _read_at(cap, middle_idx) if info.frame_count > 2 else first
        last = _read_at(cap, last_idx) if info.frame_count > 2 else first
        if middle is None or last is None:
            info.status = STATUS_ERROR
            info.issues.append(
                "No se pudo decodificar la mitad o el final del video (posible archivo truncado)."
            )
            return info
        info.mean_luma = float(np.mean(cv2.cvtColor(middle, cv2.COLOR_BGR2GRAY)))
        if thumbs_dir is not None:
            info.thumbnail = _write_thumb(middle, thumbs_dir, p, scene)
    finally:
        cap.release()

    if info.duration_s > MAX_SECONDS:
        info.issues.append(f"Dura {info.duration_s:.0f} s: solo se usan los primeros {MAX_SECONDS:.0f} s.")
    if scene is not None:
        minimum = MIN_ACTION_S if scene.is_action else MIN_LOOP_S
        if info.duration_s < minimum:
            info.issues.append(
                f"Dura {info.duration_s:.1f} s; para «{scene.label}» se esperan al menos {minimum:.1f} s."
            )
    # Los avisos (duración, cadencia, resolución) se informan pero no bloquean:
    # todo archivo que se decodifica queda «Listo» y se usa de inmediato. Solo
    # un fallo real de lectura o decodificación es «Error».
    return info


def _write_thumb(frame: np.ndarray, thumbs_dir: Path, source: Path, scene: Scene | None) -> str:
    try:
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        h, w = frame.shape[:2]
        tw = 160
        th = max(1, int(round(h * tw / max(1, w))))
        small = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
        name = f"{scene.number:02d}" if scene else source.stem
        out = thumbs_dir / f"{name}.jpg"
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return ""
        tmp = out.with_suffix(".jpg.tmp")
        tmp.write_bytes(buf.tobytes())
        tmp.replace(out)
        return str(out)
    except OSError:
        return ""


def _probe_cache_path(library_dir: Path) -> Path:
    return library_dir / "probe_cache.json"


def _load_probe_cache(library_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads(_probe_cache_path(library_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_probe_cache(library_dir: Path, cache: dict[str, dict]) -> None:
    path = _probe_cache_path(library_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # solo acelera la reanudación


def _cache_key(p: Path) -> str:
    st = p.stat()
    return f"v{PROBE_VERSION}|{os.path.normcase(str(p.resolve()))}|{st.st_size}|{st.st_mtime_ns}"


def validate_scan(
    scan: ScanResult,
    library_dir: Path,
    *,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
    force: bool = False,
) -> dict[int, ProbeInfo]:
    """Valida cada escena encontrada. Reanuda desde lo ya validado."""
    cache = {} if force else _load_probe_cache(library_dir)
    thumbs = library_dir / "thumbs"
    out: dict[int, ProbeInfo] = {}
    todo = sorted(scan.entries.items())
    total = len(todo)
    for i, (number, entry) in enumerate(todo, start=1):
        if cancel is not None and cancel():
            _save_probe_cache(library_dir, cache)
            raise ImportCancelled(f"Validación cancelada tras {i - 1} de {total} escenas.")
        label = entry.scene.label if entry.scene else str(number)
        if progress:
            progress(i - 1, total, f"Validando n.º {number} ({label}) · {i}/{total}")
        if entry.ambiguous:
            names = ", ".join(p.name for p in entry.paths)
            out[number] = ProbeInfo(
                str(entry.paths[0]), 0, 0, STATUS_ERROR,
                [f"Número repetido con varios archivos ({names}): deja solo uno."],
            )
            continue
        path = entry.paths[0]
        try:
            key = _cache_key(path)
        except OSError as exc:
            out[number] = ProbeInfo(str(path), 0, 0, STATUS_ERROR, [f"No se puede leer: {exc}"])
            continue
        cached = cache.get(key)
        if cached is not None:
            info = ProbeInfo.from_dict(cached)
            if not info.thumbnail or Path(info.thumbnail).is_file():
                out[number] = info
                continue
        info = probe_video(path, entry.scene, thumbs_dir=thumbs)
        out[number] = info
        cache[key] = info.to_dict()
        if i % 8 == 0:
            _save_probe_cache(library_dir, cache)
    _save_probe_cache(library_dir, cache)
    _flag_outliers(out)
    if progress:
        progress(total, total, f"Validación terminada: {total} escenas")
    return out


def _flag_outliers(results: dict[int, ProbeInfo]) -> None:
    """Resolución distinta de la del silencio base (n.º 1): se avisa (no bloquea)."""
    ref = results.get(1)
    if ref is None or ref.status == STATUS_ERROR:
        return
    for n, info in results.items():
        if info.status == STATUS_ERROR or n == 1:
            continue
        if (info.width, info.height) != (ref.width, ref.height):
            msg = (
                f"Resolución {info.width}x{info.height} distinta del silencio base "
                f"({ref.width}x{ref.height}): el encuadre puede no coincidir."
            )
            if msg not in info.issues:
                info.issues.append(msg)


# --------------------------------------------------------------------------- #
# Importación
# --------------------------------------------------------------------------- #


def space_needed(scan: ScanResult) -> int:
    """Bytes que ocuparía copiar las escenas encontradas (modo copia)."""
    total = 0
    for p in scan.matched.values():
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _copy_atomic(src: Path, dst: Path, cancel: Callable[[], bool] | None) -> None:
    """Copia en trozos a `dst.part` y renombra; nunca deja una copia a medias."""
    if dst.exists():
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and int(d.st_mtime) >= int(s.st_mtime):
            return  # ya copiado (reintento o segunda importación)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    try:
        with open(src, "rb") as fi, open(part, "wb") as fo:
            while True:
                if cancel is not None and cancel():
                    raise ImportCancelled("Copia cancelada.")
                chunk = fi.read(4 * 1024 * 1024)
                if not chunk:
                    break
                fo.write(chunk)
        shutil.copystat(src, part)
        part.replace(dst)
    finally:
        part.unlink(missing_ok=True)


def import_library(
    cfg,  # noqa: ANN001 - AppConfig
    scan: ScanResult,
    probes: dict[int, ProbeInfo],
    *,
    copy: bool = False,
    apply_main_clips: bool = True,
    progress: ProgressFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[object, list[str]]:
    """Escribe el manifiesto v2. Devuelve (Library, avisos).

    `apply_main_clips`: usa el n.º 1 como clip de silencio principal y el n.º 2
    como habla principal en `cfg.video` (los anteriores quedan anotados en el
    manifiesto para deshacer). No guarda `cfg`: lo hace quien llama.
    """
    from .library import (
        Library,
        SceneEntry,
        backup_path,
        load_library,
        manifest_path,
        save_library,
    )

    loaded = load_library(cfg, check_files=False)
    lib: Library = loaded.library
    path = manifest_path(cfg)
    media = path.parent / "media"
    warnings: list[str] = []

    items = sorted(scan.matched.items())
    total = len(items)
    for i, (number, src) in enumerate(items, start=1):
        if cancel is not None and cancel():
            raise ImportCancelled(f"Importación cancelada tras {i - 1} de {total} escenas.")
        scene = scene_by_number(number)
        assert scene is not None
        info = probes.get(number)
        if progress:
            progress(i - 1, total, f"Importando n.º {number} ({scene.label}) · {i}/{total}")
        working = src
        if copy and info is not None and info.status != STATUS_ERROR:
            working = media / f"{number:02d}{src.suffix.lower()}"
            _copy_atomic(src, working, cancel)
        prev = lib.scenes.get(number)
        entry = SceneEntry.from_scene(scene)
        if prev is not None:  # conserva lo que el usuario haya ajustado
            entry.enabled = prev.enabled
            entry.anchors = list(prev.anchors)
            entry.gesture = prev.gesture or entry.gesture
        entry.source = str(src)
        entry.working = str(working)
        if info is not None:
            entry.status = info.status
            entry.issues = list(info.issues)
            entry.notes = list(info.notes)
            entry.duration_s = round(info.duration_s, 3)
            entry.fps = round(info.fps, 3)
            entry.width, entry.height = info.width, info.height
            entry.size = info.size
            entry.vfr = info.vfr
            entry.thumbnail = info.thumbnail
        lib.scenes[number] = entry

    for number in scan.ambiguous:
        scene = scene_by_number(number)
        warnings.append(
            f"n.º {number} ({scene.label if scene else '?'}): varios archivos con el mismo "
            "número; no se importa hasta que quede uno."
        )
    for number in scan.missing:
        if number not in scan.entries:
            scene = scene_by_number(number)
            warnings.append(f"n.º {number} ({scene.label if scene else '?'}): no se encontró.")
    for number, paths in sorted(scan.unexpected.items()):
        why = "omitido a propósito" if number in SKIPPED else "sin escena en la tabla"
        warnings.append(
            f"n.º {number} ({why}): se ignora {', '.join(p.name for p in paths)}."
        )

    # Gestos: los 8 del catálogo (con atajo por defecto) apuntan a su escena;
    # el resto se registra por su nombre, sin atajo global salvo que el usuario
    # se lo asigne.
    for name, number in CATALOG_GESTURE_SCENES.items():
        entry = lib.scenes.get(number)
        if entry is not None and not entry.gesture:
            entry.gesture = name
    for entry in lib.scenes.values():
        if entry.is_action and not entry.gesture:
            entry.gesture = entry.slug
    lib.sync_scene_gestures()

    lib.source_folder = str(scan.folder)
    lib.imported_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    if apply_main_clips:
        base_silent, base_speaking = lib.scenes.get(1), lib.scenes.get(2)
        if base_silent and base_speaking and STATUS_ERROR not in (base_silent.status, base_speaking.status):
            if not lib.previous_main:
                lib.previous_main = {
                    "silent_clip": cfg.video.silent_clip,
                    "speaking_clip": cfg.video.speaking_clip,
                }
            cfg.video.silent_clip = base_silent.working
            cfg.video.speaking_clip = base_speaking.working
        else:
            warnings.append(
                "No se cambian los clips principales: falta el n.º 1 o el n.º 2, o tiene errores."
            )
    if path.exists():
        backup = backup_path(path)
        shutil.copy2(path, backup)
    save_library(lib, path)
    if progress:
        progress(total, total, f"Importadas {total} escenas")
    return lib, warnings


def rollback_library(cfg) -> str:  # noqa: ANN001 - AppConfig
    """Restaura el manifiesto anterior a la última importación.

    Si no había manifiesto previo, retira el actual (renombrándolo, no
    borrándolo). Devuelve un texto para el usuario. Los clips principales de
    `cfg.video` vuelven a los anteriores si se conocen; guardar `cfg` es cosa
    de quien llama.
    """
    from .library import backup_path, load_library, manifest_path

    path = manifest_path(cfg)
    current = load_library(cfg, check_files=False).library
    prev_main = dict(current.previous_main)
    backup = backup_path(path)
    if backup.exists():
        backup.replace(path)
        msg = f"Restaurado el manifiesto anterior ({path})."
    elif path.exists():
        retired = path.with_name(f"manifest.retirado-{time.strftime('%Y%m%d-%H%M%S')}.json")
        path.replace(retired)
        msg = f"No había manifiesto anterior; el actual se guardó como {retired.name}."
    else:
        return "No hay ninguna importación que deshacer."
    if prev_main:
        cfg.video.silent_clip = prev_main.get("silent_clip", cfg.video.silent_clip)
        cfg.video.speaking_clip = prev_main.get("speaking_clip", cfg.video.speaking_clip)
        msg += " Clips principales restaurados."
    return msg
