"""Biblioteca de clips: grupos de silencio y habla, y gestos con su atajo.

Manifiesto versionado (`<app_home>/library/manifest.json`, esquema 2)::

    {
      "version": 2,
      "silent":   ["D:/clips/silencio_2.mp4", ...],
      "speaking": ["D:/clips/habla_2.mp4", ...],
      "gestures": {
        "saludar": {"clip": "D:/clips/saludar.mp4", "hotkey": "Ctrl+Alt+F1", "enabled": true},
        ...
      },
      "source_folder": "C:/Users/.../Camera Roll",
      "scenes": {
        "17": {"number": 17, "slug": "saludar_mano", "label": "Saludar con la mano",
               "category": "GESTURE", "source": ".../17.mp4", "working": ".../17.mp4",
               "status": "listo", "duration_s": 6.1, "fps": 29.4, "width": 1920,
               "height": 1080, "gesture": "saludar", "anchors": [], "enabled": true, ...}
      }
    }

Esquema 2 (importación de las grabaciones numeradas, video/importer.py):
`scenes` guarda una entrada por escena de `scenes.SCENES`. Las escenas de
silencio y habla forman los grupos del director (además de `silent` y
`speaking`); los gestos y frases a petición se registran en `gestures` con el
nombre de `gesture` (los 8 del catálogo conservan su atajo por defecto; el
resto no registra atajo global salvo que el usuario se lo asigne). Un
manifiesto de esquema 1 se lee igual y se migra al guardar.

Compatibilidad: los dos clips de `config.video` (speaking_clip/silent_clip)
son siempre el primer elemento de su grupo. Sin manifiesto la biblioteca es
exactamente la de versiones anteriores: un clip por grupo y ningún gesto.

Un manifiesto ilegible o con entradas inválidas NUNCA impide arrancar: se
informa de cada problema (`LibraryLoad.issues`) y se usa lo que sea válido.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig, library_dir
from ..gestures import (
    CANCEL,
    DEFAULT_HOTKEYS,
    GESTURES,
    PAUSE,
    HotkeyError,
    gesture_label,
    is_assignable_gesture,
    validate_hotkey,
)
from ..scenes import (
    CATEGORIES,
    SPEAKING_ONE_SHOT,
    Scene,
    is_action_category,
    scene_by_number,
)

__all__ = [
    "MANIFEST_VERSION",
    "GestureEntry",
    "SceneEntry",
    "backup_path",
    "Library",
    "LibraryLoad",
    "manifest_path",
    "load_library",
    "save_library",
    "missing_gesture_message",
]

MANIFEST_VERSION = 2
MAX_POOL = 16
"""Tope de clips por grupo: cada clip es una caché en disco y un índice.
(La importación de las 62 grabaciones aporta 2 silencios y 11 hablas.)"""

LOOP_EXCLUDED_SCENES: dict[int, str] = {
    38: "hablar_una_mano",
    39: "hablar_ambas_manos",
    40: "hablar_consultando_notas",
}
"""Escenas de habla que nunca entran en los bucles automáticos (manos o mirada
abajo). Siguen en la biblioteca; solo no se eligen solas. Se identifican por
número de catálogo o por su slug."""


@dataclass
class GestureEntry:
    clip: str = ""
    hotkey: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"clip": self.clip, "hotkey": self.hotkey, "enabled": bool(self.enabled)}


@dataclass
class SceneEntry:
    """Una grabación importada (ver scenes.Scene para el significado)."""

    number: int
    slug: str
    label: str
    category: str
    description: str = ""
    source: str = ""
    """Archivo original (nunca se modifica)."""
    working: str = ""
    """Archivo que se usa: el original (modo referencia) o su copia."""
    status: str = "listo"
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    size: int = 0
    vfr: bool = False
    thumbnail: str = ""
    enabled: bool = True
    gesture: str = ""
    """Nombre con el que se pide (gestos y frases); vacío en los bucles."""
    anchors: list[int] = field(default_factory=list)
    """Fotogramas ancla marcados a mano (índices en la cadencia de salida)."""

    @classmethod
    def from_scene(cls, scene: Scene) -> SceneEntry:
        return cls(scene.number, scene.slug, scene.label, scene.category, scene.description)

    @property
    def is_action(self) -> bool:
        return is_action_category(self.category)

    @property
    def usable(self) -> bool:
        return self.enabled and self.status != "error" and bool(self.working)

    def to_dict(self) -> dict:
        return {
            "number": self.number, "slug": self.slug, "label": self.label,
            "category": self.category, "description": self.description,
            "source": self.source, "working": self.working, "status": self.status,
            "issues": list(self.issues), "notes": list(self.notes),
            "duration_s": self.duration_s, "fps": self.fps, "width": self.width,
            "height": self.height, "size": self.size, "vfr": bool(self.vfr),
            "thumbnail": self.thumbnail, "enabled": bool(self.enabled),
            "gesture": self.gesture, "anchors": [int(a) for a in self.anchors],
        }


@dataclass
class Library:
    version: int = MANIFEST_VERSION
    silent: list[str] = field(default_factory=list)
    speaking: list[str] = field(default_factory=list)
    gestures: dict[str, GestureEntry] = field(default_factory=dict)
    scenes: dict[int, SceneEntry] = field(default_factory=dict)
    source_folder: str = ""
    imported_at: str = ""
    previous_main: dict[str, str] = field(default_factory=dict)
    """Clips principales antes de importar (para deshacer)."""

    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        out = {
            "version": MANIFEST_VERSION,
            "silent": list(self.silent),
            "speaking": list(self.speaking),
            "gestures": {k: v.to_dict() for k, v in sorted(self.gestures.items())},
        }
        if self.scenes or self.source_folder:
            out["source_folder"] = self.source_folder
            out["imported_at"] = self.imported_at
            out["previous_main"] = dict(self.previous_main)
            out["scenes"] = {str(n): e.to_dict() for n, e in sorted(self.scenes.items())}
        return out

    def scene_paths(self, *categories: str) -> list[str]:
        return [
            e.working
            for _, e in sorted(self.scenes.items())
            if e.category in categories and e.usable
        ]

    def pools(self, cfg: AppConfig) -> tuple[list[str], list[str]]:
        """(silencio, habla): clip principal primero, sin duplicados ni vacíos.

        Las escenas importadas de silencio y habla se añaden por número; las
        frases a petición (`SPEAKING_ONE_SHOT`) nunca entran en el bucle."""
        silent_scenes = self.scene_paths("SILENT_BASE", "SILENT_VARIATION")
        speaking_scenes = self.scene_paths("SPEAKING_BASE", "SPEAKING_VARIATION")
        excluded = self._loop_excluded_paths()
        silent = _dedupe([cfg.video.silent_clip, *silent_scenes, *self.silent])
        speaking = _dedupe([cfg.video.speaking_clip, *speaking_scenes, *self.speaking])
        return (
            (_drop(silent, excluded) or silent[:1])[:MAX_POOL],
            (_drop(speaking, excluded) or speaking[:1])[:MAX_POOL],
        )

    def _loop_excluded_paths(self) -> set[str]:
        """Rutas (normalizadas) de las escenas de `LOOP_EXCLUDED_SCENES`."""
        out: set[str] = set()
        slugs = set(LOOP_EXCLUDED_SCENES.values())
        for e in self.scenes.values():
            if e.number in LOOP_EXCLUDED_SCENES or e.slug in slugs:
                for p in (e.working, e.source):
                    if p:
                        out.add(_norm(p))
        return out

    def speaking_actions(self) -> set[str]:
        """Nombres de las frases a petición: ya son habla, no esperan micro-pausa."""
        return {
            e.gesture for e in self.scenes.values()
            if e.category == SPEAKING_ONE_SHOT and e.gesture
        }

    def scene_for_gesture(self, name: str) -> SceneEntry | None:
        for e in self.scenes.values():
            if e.gesture == name:
                return e
        return None

    def sync_scene_gestures(self) -> None:
        """Registra en `gestures` las escenas a petición, sin pisar lo manual.

        Si un gesto del catálogo ya tenía un clip propio distinto (asignado a
        mano antes de importar), se respeta y la escena se registra con su
        propio nombre."""
        for _, e in sorted(self.scenes.items()):
            if not e.is_action:
                continue
            name = e.gesture or e.slug
            cur = self.gestures.get(name)
            mine = {_norm(e.working), _norm(e.source)}
            if cur is not None and cur.clip and Path(cur.clip).is_file() and _norm(cur.clip) not in mine:
                name = e.slug
                cur = self.gestures.get(name)
            e.gesture = name
            entry = cur or GestureEntry(hotkey=DEFAULT_HOTKEYS.get(name, ""))
            entry.clip = e.working
            entry.enabled = e.usable if cur is None else (cur.enabled and e.usable)
            self.gestures[name] = entry

    def active_gestures(self) -> dict[str, GestureEntry]:
        """Gestos habilitados con clip asignado y existente."""
        return {
            name: g
            for name, g in self.gestures.items()
            if g.enabled and g.clip and Path(g.clip).is_file()
        }

    def hotkeys(self) -> dict[str, str]:
        """Atajo efectivo de cada gesto activo (el por defecto si no tiene).

        Los gestos del catálogo usan su atajo por defecto; las escenas solo
        tienen atajo si el usuario se lo asignó (no se registran 62 atajos
        globales). Las variantes `_hablando` no tienen atajo propio."""
        out = {}
        for name in self.active_gestures():
            out_key = _effective_hotkey(name, self.gestures[name])
            if out_key:
                out[name] = out_key
        return out

    def missing_gestures(self) -> list[str]:
        active = self.active_gestures()
        return [g for g in GESTURES if g not in active]

    def set_gesture(self, name: str, clip: str, hotkey: str | None = None) -> None:
        if not is_assignable_gesture(name):
            raise ValueError(f"Gesto desconocido: {name!r}.")
        entry = self.gestures.get(name) or GestureEntry(hotkey=DEFAULT_HOTKEYS.get(name, ""))
        entry.clip = str(clip)
        if hotkey is not None:
            entry.hotkey = hotkey
        entry.enabled = True
        self.gestures[name] = entry

    def validate(self, *, check_files: bool = True) -> list[str]:
        """Problemas que el usuario debe conocer (no bloquean el arranque)."""
        issues: list[str] = []
        if check_files:
            for kind, paths in (("silencio", self.silent), ("habla", self.speaking)):
                for p in paths:
                    if not Path(p).is_file():
                        issues.append(f"Clip de {kind} no encontrado: {p}")
        seen: dict[str, str] = {}
        for name, g in self.gestures.items():
            if not g.enabled:
                continue
            if check_files and g.clip and not Path(g.clip).is_file():
                issues.append(f"Gesto «{gesture_label(name)}»: no se encuentra {g.clip}")
            text = _effective_hotkey(name, g)
            if not text:
                continue
            try:
                canon = validate_hotkey(text).text
            except HotkeyError as exc:
                issues.append(f"Gesto «{gesture_label(name)}»: {exc}")
                continue
            if canon in seen:
                issues.append(
                    f"El atajo {canon} está asignado a «{gesture_label(seen[canon])}» "
                    f"y a «{gesture_label(name)}»."
                )
            seen[canon] = name
        return issues


@dataclass
class LibraryLoad:
    library: Library
    issues: list[str]
    path: Path
    exists: bool


def _effective_hotkey(name: str, g: GestureEntry) -> str:
    from ..gestures import SPEAKING_SUFFIX

    if name.endswith(SPEAKING_SUFFIX):
        return ""
    if name in GESTURES:
        return g.hotkey or DEFAULT_HOTKEYS.get(name, "")
    return g.hotkey


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)) if p else ""


def backup_path(path: Path) -> Path:
    """Copia del manifiesto anterior a la última importación (para deshacer)."""
    return path.with_name(path.stem + ".anterior.json")


def _drop(paths: list[str], excluded: set[str]) -> list[str]:
    return [p for p in paths if _norm(p) not in excluded]


def _dedupe(paths) -> list[str]:  # noqa: ANN001
    out, seen = [], set()
    for p in paths:
        if not p:
            continue
        key = os.path.normcase(os.path.abspath(str(p)))
        if key in seen:
            continue
        seen.add(key)
        out.append(str(p))
    return out


def manifest_path(cfg: AppConfig) -> Path:
    return Path(cfg.video.library_manifest) if cfg.video.library_manifest else library_dir() / "manifest.json"


def _parse(data, issues: list[str]) -> Library:  # noqa: ANN001
    lib = Library()
    if not isinstance(data, dict):
        issues.append("El manifiesto de la biblioteca no es un objeto JSON; se ignora.")
        return lib
    version = data.get("version", 0)
    if not isinstance(version, int) or version < 0:
        issues.append("El manifiesto no tiene una versión válida; se intenta leer igualmente.")
        version = 0
    if version > MANIFEST_VERSION:
        issues.append(
            f"El manifiesto es de una versión más nueva ({version}) que esta aplicación "
            f"({MANIFEST_VERSION}); se usan solo los clips principales."
        )
        return lib
    # Versión 0 (borradores sin campo version) y 1: mismo formato sin escenas;
    # se migran al guardar.
    for key in ("silent", "speaking"):
        raw = data.get(key, [])
        if not isinstance(raw, list):
            issues.append(f"«{key}» debe ser una lista de rutas; se ignora.")
            continue
        vals = [str(p) for p in raw if isinstance(p, str) and p.strip()]
        if len(vals) != len(raw):
            issues.append(f"«{key}» contiene entradas que no son rutas; se ignoran.")
        setattr(lib, key, vals)
    _parse_scenes(data, lib, issues)
    gestures = data.get("gestures", {})
    if not isinstance(gestures, dict):
        issues.append("«gestures» debe ser un objeto; se ignora.")
        gestures = {}
    for name, entry in gestures.items():
        if name in (CANCEL, PAUSE):
            issues.append(f"«{name}» es un nombre reservado; se ignora.")
            continue
        if not is_assignable_gesture(name):
            issues.append(f"Gesto desconocido en el manifiesto: «{name}»; se ignora.")
            continue
        if isinstance(entry, str):
            entry = {"clip": entry}
        if not isinstance(entry, dict):
            issues.append(f"Entrada inválida para el gesto «{name}»; se ignora.")
            continue
        lib.gestures[name] = GestureEntry(
            clip=str(entry.get("clip") or ""),
            hotkey=str(entry.get("hotkey") or DEFAULT_HOTKEYS.get(name, "")),
            enabled=bool(entry.get("enabled", True)),
        )
    lib.version = MANIFEST_VERSION
    return lib


def _parse_scenes(data: dict, lib: Library, issues: list[str]) -> None:
    for key in ("source_folder", "imported_at"):
        if isinstance(data.get(key), str):
            setattr(lib, key, data[key])
    prev = data.get("previous_main")
    if isinstance(prev, dict):
        lib.previous_main = {k: str(v) for k, v in prev.items() if isinstance(v, str)}
    raw = data.get("scenes", {})
    if not isinstance(raw, dict):
        issues.append("«scenes» debe ser un objeto; se ignora.")
        return
    for key, item in raw.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            issues.append(f"Escena con número inválido: «{key}»; se ignora.")
            continue
        scene = scene_by_number(number)
        if scene is None or not isinstance(item, dict):
            issues.append(f"Escena n.º {key} desconocida o inválida; se ignora.")
            continue
        e = SceneEntry.from_scene(scene)
        category = item.get("category", scene.category)
        e.category = category if category in CATEGORIES else scene.category
        for name in ("source", "working", "status", "thumbnail", "gesture", "description"):
            if isinstance(item.get(name), str):
                setattr(e, name, item[name])
        for name in ("duration_s", "fps"):
            if isinstance(item.get(name), (int, float)):
                setattr(e, name, float(item[name]))
        for name in ("width", "height", "size"):
            if isinstance(item.get(name), int):
                setattr(e, name, int(item[name]))
        for name in ("issues", "notes"):
            if isinstance(item.get(name), list):
                setattr(e, name, [str(x) for x in item[name]])
        e.vfr = bool(item.get("vfr", False))
        e.enabled = bool(item.get("enabled", True))
        anchors = item.get("anchors", [])
        if isinstance(anchors, list):
            e.anchors = sorted({int(a) for a in anchors if isinstance(a, int) and a >= 0})
        # «revisar» (manifiestos anteriores) ya no existe: lo decodificable
        # está listo; solo «error» queda fuera.
        if e.status != "error":
            e.status = "listo"
        lib.scenes[number] = e


def load_library(cfg: AppConfig, *, check_files: bool = True) -> LibraryLoad:
    path = manifest_path(cfg)
    issues: list[str] = []
    if not path.exists():
        return LibraryLoad(Library(), issues, path, False)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append(
            f"No se pudo leer el manifiesto {path}: {exc}. Se usan solo los clips principales."
        )
        return LibraryLoad(Library(), issues, path, True)
    lib = _parse(data, issues)
    issues.extend(lib.validate(check_files=check_files))
    return LibraryLoad(lib, issues, path, True)


def save_library(lib: Library, path: Path) -> None:
    """Escritura atómica (tmp + replace) para no dejar un manifiesto a medias."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(lib.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def missing_gesture_message(name: str) -> str:
    return (
        f"No hay clip para el gesto «{gesture_label(name)}». Grábalo en la pestaña "
        "Grabar (plan «Gestos») o asígnalo en la pestaña Gestos."
    )
