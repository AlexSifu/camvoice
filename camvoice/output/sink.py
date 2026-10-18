"""Salidas de video: cámara virtual y sumidero nulo (para pruebas y medición).

La implementación real es OBS Virtual Camera a través de `pyvirtualcam`. Importa
el detalle de que **no hace falta tener OBS Studio abierto**: el instalador de
OBS registra un filtro DirectShow (`obs-virtualcam-module64.dll`) y
`pyvirtualcam` escribe directamente en su memoria compartida. Eso evita montar
escenas, automatizar la interfaz de OBS o escribir un controlador propio.

Consecuencia que conviene conocer: es un filtro DirectShow. Las aplicaciones de
videollamada habituales (Teams, Zoom, Meet y cualquier cosa basada en Chromium)
lo ven. Las aplicaciones que solo usan Media Foundation —como la app Cámara de
Windows— no.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..config import OutputConfig

__all__ = [
    "SinkError",
    "FrameSink",
    "NullSink",
    "VirtualCamSink",
    "build_sink",
    "diagnose_virtual_camera",
]


class SinkError(RuntimeError):
    """Fallo de la salida de video, con mensaje apto para el usuario."""


class FrameSink(Protocol):
    name: str
    device: str

    def send(self, frame: np.ndarray) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- #


class NullSink:
    """Descarta los fotogramas. Permite medir el coste del motor por separado."""

    name = "ninguna"

    def __init__(self) -> None:
        self.device = "(sin salida)"
        self.frames = 0

    def send(self, frame: np.ndarray) -> None:
        self.frames += 1

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #


_INSTALL_HINT = (
    "No hay ninguna cámara virtual registrada en el sistema.\n\n"
    "En Windows una cámara es una DLL que la aplicación receptora carga en su "
    "propio proceso, así que hace falta registrar un filtro una sola vez. Dos "
    "formas:\n"
    "  1. camvoice-cli install-camera     (filtro propio, solo para tu usuario,\n"
    "                                      sin administrador ni OBS)\n"
    "  2. Instalar OBS Studio             (registra el suyo; no hace falta abrirlo)\n\n"
    "«camvoice-cli cameras» muestra lo que hay registrado ahora mismo."
)


def _detail(texts: dict[str, str]) -> str:
    if not texts:
        return ""
    return "Detalle por backend:\n" + "\n".join(
        f"  {backend}: {message}" for backend, message in texts.items()
    )


class VirtualCamSink:
    """Cámara virtual con reapertura automática ante fallos transitorios."""

    name = "cámara virtual"

    def __init__(self, cfg: OutputConfig, width: int, height: int, fps: int, pixel_format: str) -> None:
        self.cfg = cfg
        self.width = width
        self.height = height
        self.fps = fps
        self.pixel_format = pixel_format
        self.device = ""
        self.native_format = ""
        self.backend_used = ""
        self.frames = 0
        self.reopens = 0
        self._cam = None
        self._open()

    # ------------------------------------------------------------------ #

    def _pixel_format(self):
        from pyvirtualcam import PixelFormat

        mapping = {"nv12": PixelFormat.NV12, "bgr": PixelFormat.BGR}
        if self.pixel_format not in mapping:
            raise SinkError(f"Formato de píxel no soportado por la salida: {self.pixel_format!r}")
        return mapping[self.pixel_format]

    def _candidate_backends(self) -> list[str]:
        """Backends a probar, en orden.

        Con `backend = "auto"` se consulta qué filtros hay registrados en el
        sistema y se prueban en orden de preferencia, colocando delante los que
        no dependen de OBS. Así la aplicación funciona con su propio filtro si
        lo tiene y cae al de OBS si es lo único presente, sin que el usuario
        tenga que elegir.
        """
        declared = (self.cfg.backend or "auto").strip().lower()
        if declared and declared != "auto":
            return [declared]

        from .filters import detect_backends

        detected = detect_backends(prefer_independent=True)
        # Si no se detecta nada se intentan los dos de todos modos: la
        # enumeración del registro puede fallar por permisos y es mejor dejar
        # que pyvirtualcam dé su propio error que negarse de antemano.
        return detected or ["unitycapture", "obs"]

    def _open(self) -> None:
        try:
            import pyvirtualcam
        except Exception as exc:
            raise SinkError(
                "No se pudo cargar pyvirtualcam. Reinstala las dependencias. "
                f"Detalle: {exc}"
            ) from exc

        errors: list[tuple[str, Exception]] = []
        for backend in self._candidate_backends():
            kwargs = dict(
                width=self.width,
                height=self.height,
                fps=self.fps,
                fmt=self._pixel_format(),
                backend=backend,
                print_fps=False,
            )
            if self.cfg.device:
                kwargs["device"] = self.cfg.device
            try:
                self._cam = pyvirtualcam.Camera(**kwargs)
            except Exception as exc:
                errors.append((backend, exc))
                continue
            self.backend_used = backend
            self.device = getattr(self._cam, "device", "") or "cámara virtual"
            native = getattr(self._cam, "native_fmt", None)
            self.native_format = getattr(native, "name", str(native or ""))
            return

        raise SinkError(self._explain_all(errors))

    def _explain_all(self, errors: list[tuple[str, Exception]]) -> str:
        """Mensaje único a partir de los fallos de todos los backends probados.

        Se prioriza la causa más accionable: «en uso» por otra aplicación es un
        problema que el usuario puede resolver en diez segundos, mientras que
        «no instalada» requiere registrar un filtro.
        """
        texts = {backend: str(exc) for backend, exc in errors}
        joined = " ".join(texts.values()).lower()

        if any(k in joined for k in ("in use", "busy", "access", "denied")):
            return (
                "La cámara virtual ya está en uso. Solo puede alimentarla una "
                "aplicación a la vez: cierra OBS Studio (o detén allí «Iniciar "
                "cámara virtual») y vuelve a intentarlo.\n\n"
                + _detail(texts)
            )
        return f"{_INSTALL_HINT}\n\n{_detail(texts)}"

    # ------------------------------------------------------------------ #

    def send(self, frame: np.ndarray) -> None:
        if self._cam is None:
            self._reopen()
        try:
            self._cam.send(frame)
            self.frames += 1
        except Exception as exc:
            self.close()
            raise SinkError(f"Se interrumpió el envío a la cámara virtual: {exc}") from exc

    def _reopen(self) -> None:
        last: Exception | None = None
        for attempt in range(max(1, self.cfg.reopen_attempts)):
            try:
                self._open()
                self.reopens += 1
                return
            except SinkError as exc:
                last = exc
                time.sleep(self.cfg.reopen_backoff_s * (attempt + 1))
        raise SinkError(
            f"No se pudo reabrir la cámara virtual tras {self.cfg.reopen_attempts} "
            f"intentos. {last}"
        )

    def close(self) -> None:
        cam, self._cam = self._cam, None
        if cam is None:
            return
        try:
            cam.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #


def build_sink(
    cfg: OutputConfig, width: int, height: int, fps: int, pixel_format: str
) -> FrameSink:
    if not cfg.enabled:
        return NullSink()
    return VirtualCamSink(cfg, width, height, fps, pixel_format)


@dataclass(frozen=True, slots=True)
class CameraDiagnosis:
    available: bool
    detail: str


def diagnose_virtual_camera(
    cfg: OutputConfig | None = None, *, width: int = 640, height: int = 480, fps: int = 15
) -> CameraDiagnosis:
    """Abre y cierra la cámara virtual para comprobar que funciona.

    Se usa desde la UI y desde `camvoice doctor`, porque el único modo fiable de
    saber si la salida está disponible es intentarlo.
    """
    cfg = cfg or OutputConfig()
    try:
        sink = VirtualCamSink(cfg, width, height, fps, "nv12")
    except SinkError as exc:
        return CameraDiagnosis(False, str(exc))
    try:
        detail = (
            f"Disponible: «{sink.device}» "
            f"({width}x{height} a {fps} FPS, formato nativo "
            f"{sink.native_format or 'desconocido'}, backend {sink.backend_used})"
        )
        return CameraDiagnosis(True, detail)
    finally:
        sink.close()
