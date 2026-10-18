"""Configuración de la aplicación: dataclasses + persistencia JSON.

Un único objeto `AppConfig` es la fuente de verdad: la UI lo edita y el motor lo
lee. Se guarda en %APPDATA%/camvoice/config.json (o CAMVOICE_HOME si está
definida) para no reconfigurar en cada arranque.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "camvoice"
CONFIG_VERSION = 1

FIT_MODES = ("cover", "contain", "stretch")
LOOP_MODES = ("pingpong", "loop")
VAD_BACKENDS = ("auto", "webrtc", "rms")
PIXEL_FORMATS = ("nv12", "bgr")
VALID_SAMPLERATES = (8000, 16000, 32000, 48000)


def app_home() -> Path:
    """Directorio de datos de la aplicación (config, caché de clips, métricas)."""
    override = os.environ.get("CAMVOICE_HOME")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME}"


def cache_dir() -> Path:
    return app_home() / "clipcache"


def metrics_dir() -> Path:
    return app_home() / "metrics"


def config_path() -> Path:
    return app_home() / "config.json"


# --------------------------------------------------------------------------- #
# Secciones
# --------------------------------------------------------------------------- #


@dataclass
class AudioConfig:
    """Captura de micrófono."""

    device: int | None = None
    """Índice de dispositivo de sounddevice. None = dispositivo por defecto."""

    device_name: str = ""
    """Nombre recordado: permite re-resolver el índice si el orden cambia."""

    target_samplerate: int = 16000
    """WebRTC VAD solo acepta 8k/16k/32k/48k. 16k es el punto dulce."""

    block_ms: int = 10
    """Bloque que se pide a PortAudio. 10 ms mantiene baja la latencia."""


@dataclass
class VadConfig:
    """Detección de actividad de voz y su histéresis temporal."""

    backend: str = "auto"
    """'auto' (webrtc con caída a rms) | 'webrtc' | 'rms'."""

    aggressiveness: int = 2
    """0..3 para WebRTC VAD. Mayor = más estricto (menos falsos positivos)."""

    frame_ms: int = 20
    """Ventana de análisis. WebRTC VAD acepta 10, 20 o 30 ms."""

    # --- detector de energía (fallback, y puerta de ruido del backend webrtc) ---
    adaptive_noise_floor: bool = True
    """Estima el piso de ruido y usa umbrales relativos (SNR) en vez de absolutos."""

    rms_threshold_db: float = -42.0
    """Umbral absoluto en dBFS cuando adaptive_noise_floor está desactivado."""

    snr_on_db: float = 9.0
    """dB sobre el piso de ruido para marcar la trama como voz."""

    snr_off_db: float = 5.0
    """Umbral inferior: histéresis a nivel de trama."""

    noise_floor_tau_s: float = 3.0
    """Constante de tiempo del seguidor del piso de ruido (sube lento, baja rápido)."""

    gate_db: float = -50.0
    """Puerta de ruido absoluta en dBFS: por debajo de esto nunca hay voz, pase
    lo que diga WebRTC.

    La voz conversacional a medio metro de un micrófono normal está entre -30 y
    -15 dBFS; -50 dBFS deja pasar incluso un susurro lejano y a la vez descarta
    chasquidos, teclado y el zumbido de un ventilador. Medido en este equipo,
    bajarlo a -60 dBFS producía unas 12 activaciones falsas por minuto sin que
    nadie hablara. Si tu voz no activa el detector, bájalo; si se activa solo,
    súbelo. `camvoice vad-test` muestra los niveles reales para decidirlo."""

    # --- histéresis temporal (SpeechGate) ---
    attack_ms: int = 120
    """Voz continua necesaria para pasar a SPEAKING. Domina la latencia percibida."""

    release_ms: int = 450
    """Silencio continuo necesario para volver a SILENT (hangover)."""

    pause_tolerance_ms: int = 350
    """Pausas cortas dentro del habla que no reinician el contador de ataque."""

    min_speaking_ms: int = 400
    """Permanencia mínima en SPEAKING: evita parpadeos con sílabas sueltas."""

    min_silent_ms: int = 250
    """Permanencia mínima en SILENT antes de poder reactivar."""


@dataclass
class VideoConfig:
    """Clips, formato de salida y transiciones."""

    speaking_clip: str = ""
    silent_clip: str = ""

    width: int = 640
    height: int = 480
    fps: int = 15

    fit: str = "cover"
    """'cover' (recorte centrado, sin deformar) | 'contain' (barras) | 'stretch'."""

    letterbox_color: tuple[int, int, int] = (0, 0, 0)
    """Color BGR de las barras en modo 'contain'."""

    mirror: bool = False
    """Espejo horizontal. Se aplica al preparar la caché (coste nulo en vivo)."""

    pixel_format: str = "nv12"
    """Formato interno. 'nv12' coincide con el nativo de OBS VirtualCam y evita
    una conversión por fotograma; 'bgr' es el camino genérico."""

    loop_mode: str = "pingpong"
    """'pingpong' (ida y vuelta: bucle sin costura) | 'loop' (reinicio al final)."""

    speaking_speed: float = 1.0
    silent_speed: float = 1.0

    transition_ms: int = 160
    """Duración del fundido cruzado entre clips."""

    resume_window_ms: int = 1500
    """Si el habla vuelve antes de esto, el clip de habla continúa donde iba; si
    no, se rebobina. Evita que la boca quede congelada en una pose abierta."""

    max_cache_mb: int = 1536
    """Tope por clip para la caché de fotogramas sin comprimir."""

    preview_fps: int = 10
    preview_max_width: int = 480


@dataclass
class EffectsConfig:
    """Efectos sobre el fotograma de salida.

    Todos los valores son porcentajes para que el resultado visual no dependa
    de la resolución: el 60 % de desenfoque se ve igual a 320x240 que a 720p.
    Se aplican en caliente, sin volver a preparar la caché de clips.
    """

    enabled: bool = True
    """Interruptor general. Desactivarlo salta la cadena completa, incluida la
    conversión de formato que necesita, y deja el coste en cero."""

    zoom: float = 1.0
    """Recorte centrado ampliado, 1.0 a 4.0. Reencuadra sin regrabar."""

    blur: float = 0.0
    """Desenfoque gaussiano, 0 a 100 %."""

    pixelate: float = 0.0
    """Pixelado en bloques, 0 a 100 %."""

    brightness: float = 0.0
    """-100 a 100. Útil para igualar el clip con la luz de la sala."""

    contrast: float = 0.0
    """-100 a 100, en escala logarítmica para que el deslizador sea simétrico."""

    saturation: float = 0.0
    """-100 (gris) a 100 (saturado)."""

    vignette: float = 0.0
    """Oscurecimiento de las esquinas, 0 a 100 %."""

    grain: float = 0.0
    """Ruido de luminancia, 0 a 100 %. Un clip comprimido se ve demasiado
    limpio para ser una webcam; un poco de grano acerca el aspecto."""

    flip_horizontal: bool = False
    """Espejo aplicado en vivo, sin tocar la caché (a diferencia de
    `VideoConfig.mirror`, que se hornea al preparar)."""


@dataclass
class OutputConfig:
    """Cámara virtual."""

    enabled: bool = True

    backend: str = "auto"
    """Backend de pyvirtualcam: 'auto', 'obs' o 'unitycapture'.

    Con 'auto' se consulta qué filtros DirectShow hay registrados en el sistema
    y se prefiere el que no depende de OBS, de modo que una instalación
    autónoma no compite con OBS por el dispositivo ni lo necesita."""

    device: str = ""
    """Dispositivo concreto; vacío = el que elija el backend."""

    reopen_attempts: int = 5
    reopen_backoff_s: float = 1.5


@dataclass
class UiConfig:
    """Comportamiento de la ventana."""

    minimize_to_tray: bool = True
    """Al cerrar la ventana, seguir en el area de notificacion en vez de salir.

    Quita la ventana de la barra de tareas y de Alt+Tab, pero NO oculta la
    aplicacion del sistema: sigue en el Administrador de tareas, en el propio
    icono junto al reloj, y en la lista de camaras del programa de videollamada.
    Es minimizacion a bandeja estandar, no evasion."""

    record_duration_s: float = 15.0
    """Duracion por defecto en el grabador guiado."""


@dataclass
class MetricsConfig:
    enabled: bool = False
    interval_s: float = 2.0
    csv_path: str = ""
    """Vacío = <app_home>/metrics/metrics-<timestamp>.csv"""


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    effects: EffectsConfig = field(default_factory=EffectsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

    # ------------------------------------------------------------------ #
    # Serialización
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AppConfig:
        data = data or {}
        kwargs: dict[str, Any] = {}
        if isinstance(data.get("version"), int):
            kwargs["version"] = data["version"]
        for name, section_cls in _SECTIONS.items():
            raw = data.get(name)
            if isinstance(raw, dict):
                kwargs[name] = _build_section(section_cls, raw)
        return cls(**kwargs)

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)  # atómico: nunca deja un JSON a medias
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        path = path or config_path()
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            # Config corrupta: arrancar con valores por defecto es preferible a
            # no arrancar. Se conserva el archivo malo para diagnóstico.
            try:
                path.replace(path.with_suffix(".json.bad"))
            except OSError:
                pass
            return cls()

    # ------------------------------------------------------------------ #
    # Validación
    # ------------------------------------------------------------------ #

    def validate(self, *, require_clips: bool = True) -> list[str]:
        """Devuelve problemas legibles para el usuario (lista vacía = OK)."""
        errs: list[str] = []
        v, a, d, o = self.video, self.audio, self.vad, self.output

        if v.width <= 0 or v.height <= 0:
            errs.append("La resolución de salida debe ser positiva.")
        elif v.width % 2 or v.height % 2:
            errs.append(
                "El ancho y el alto deben ser pares: lo exige el formato NV12 de "
                "la cámara virtual."
            )
        if not 1 <= v.fps <= 60:
            errs.append("Los FPS de salida deben estar entre 1 y 60.")
        if v.fit not in FIT_MODES:
            errs.append(f"Modo de ajuste desconocido: {v.fit!r}.")
        if v.loop_mode not in LOOP_MODES:
            errs.append(f"Modo de bucle desconocido: {v.loop_mode!r}.")
        if v.pixel_format not in PIXEL_FORMATS:
            errs.append(f"Formato de píxel desconocido: {v.pixel_format!r}.")
        for label, speed in (("habla", v.speaking_speed), ("silencio", v.silent_speed)):
            if not 0.1 <= speed <= 4.0:
                errs.append(f"La velocidad del clip de {label} debe estar entre 0.1 y 4.0.")
        if v.transition_ms < 0:
            errs.append("La duración de la transición no puede ser negativa.")
        if v.max_cache_mb < 16:
            errs.append("El tope de caché por clip debe ser de al menos 16 MB.")

        if require_clips:
            for label, raw in (("habla", v.speaking_clip), ("silencio", v.silent_clip)):
                if not raw:
                    errs.append(f"Falta seleccionar el clip de {label}.")
                elif not Path(raw).exists():
                    errs.append(f"No se encuentra el clip de {label}: {raw}")

        if a.target_samplerate not in VALID_SAMPLERATES:
            errs.append(
                "La frecuencia de muestreo del VAD debe ser 8000, 16000, 32000 o 48000 Hz."
            )
        if not 1 <= a.block_ms <= 100:
            errs.append("El bloque de audio debe estar entre 1 y 100 ms.")
        if d.frame_ms not in (10, 20, 30):
            errs.append("La ventana del VAD debe ser de 10, 20 o 30 ms.")
        if not 0 <= d.aggressiveness <= 3:
            errs.append("La agresividad del VAD debe estar entre 0 y 3.")
        if d.backend not in VAD_BACKENDS:
            errs.append(f"Backend de VAD desconocido: {d.backend!r}.")
        if d.snr_off_db > d.snr_on_db:
            errs.append("El umbral SNR de apagado debe ser menor o igual al de encendido.")
        if min(d.attack_ms, d.release_ms, d.pause_tolerance_ms) < 0:
            errs.append("Los tiempos del detector no pueden ser negativos.")
        if d.noise_floor_tau_s <= 0:
            errs.append("La constante de tiempo del piso de ruido debe ser positiva.")

        if o.reopen_attempts < 0:
            errs.append("Los reintentos de reapertura no pueden ser negativos.")

        if not 4.0 <= self.ui.record_duration_s <= 60.0:
            errs.append("La duracion de grabacion debe estar entre 4 y 60 s.")

        e = self.effects
        if not 1.0 <= e.zoom <= 4.0:
            errs.append("El zoom debe estar entre 1.0 y 4.0.")
        for etiqueta, valor in (
            ("desenfoque", e.blur),
            ("pixelado", e.pixelate),
            ("viñeta", e.vignette),
            ("grano", e.grain),
        ):
            if not 0.0 <= valor <= 100.0:
                errs.append(f"El {etiqueta} debe estar entre 0 y 100 %.")
        for etiqueta, valor in (
            ("brillo", e.brightness),
            ("contraste", e.contrast),
            ("saturación", e.saturation),
        ):
            if not -100.0 <= valor <= 100.0:
                errs.append(f"El {etiqueta} debe estar entre -100 y 100 %.")

        return errs

    def warnings(self) -> list[str]:
        """Avisos que no impiden arrancar pero conviene mostrar."""
        warns: list[str] = []
        v = self.video
        if v.fps > 30:
            warns.append(
                f"{v.fps} FPS multiplica el coste de CPU y el tamaño de la caché; "
                "15 FPS es suficiente para videollamadas."
            )
        if v.width * v.height > 1280 * 720:
            warns.append(
                "Por encima de 720p la caché de fotogramas crece mucho "
                f"({frame_bytes(v.width, v.height, v.pixel_format) / 1e6:.1f} MB por segundo "
                f"de clip a {v.fps} FPS)."
            )
        if v.transition_ms > 1000:
            warns.append("Una transición de más de 1 s se percibe como lentitud.")
        if self.vad.attack_ms > 400:
            warns.append(
                f"Un ataque de {self.vad.attack_ms} ms supera el objetivo de latencia "
                "de 200-400 ms."
            )
        e = self.effects
        if e.enabled and v.pixel_format == "nv12" and (e.blur or e.pixelate or e.grain):
            warns.append(
                "Con efectos activos y formato NV12 hay una ida y vuelta a BGR "
                "por fotograma. Son unos 2 ms a 640x480; si buscas el mínimo "
                "coste con efectos, usa el formato 'bgr'."
            )
        if e.enabled and e.blur > 70 and e.pixelate > 70:
            warns.append(
                "Desenfoque y pixelado altos a la vez se anulan entre sí: el "
                "desenfoque borra los bloques. Usa uno de los dos."
            )
        if v.pixel_format == "nv12" and self.output.backend == "unitycapture":
            warns.append(
                "UnityCapture trabaja en RGBA, así que con NV12 habrá una "
                "conversión por fotograma. Es asumible a 640x480, pero si usas "
                "ese filtro y buscas el mínimo coste, prueba 'bgr'."
            )
        return warns


# --------------------------------------------------------------------------- #
# Utilidades de formato
# --------------------------------------------------------------------------- #


def frame_shape(width: int, height: int, pixel_format: str) -> tuple[int, ...]:
    """Forma del array numpy que representa un fotograma en memoria."""
    if pixel_format == "nv12":
        # Plano Y (h filas) + plano UV entrelazado y submuestreado (h/2 filas).
        return (height * 3 // 2, width)
    if pixel_format == "bgr":
        return (height, width, 3)
    raise ValueError(f"Formato de píxel no soportado: {pixel_format!r}")


def frame_bytes(width: int, height: int, pixel_format: str) -> int:
    n = 1
    for dim in frame_shape(width, height, pixel_format):
        n *= dim
    return n


# --------------------------------------------------------------------------- #
# Reconstrucción tolerante de secciones
# --------------------------------------------------------------------------- #

_SECTIONS: dict[str, type] = {
    "audio": AudioConfig,
    "vad": VadConfig,
    "video": VideoConfig,
    "effects": EffectsConfig,
    "output": OutputConfig,
    "ui": UiConfig,
    "metrics": MetricsConfig,
}


def _build_section(section_cls: type, raw: dict[str, Any]) -> Any:
    """Instancia una sección ignorando claves desconocidas.

    Permite leer configuraciones de versiones anteriores o posteriores sin
    romper: lo que no se reconoce se descarta y queda el valor por defecto.
    """
    known = {f.name for f in fields(section_cls)}
    kwargs = {k: v for k, v in raw.items() if k in known}
    if "letterbox_color" in kwargs and isinstance(kwargs["letterbox_color"], (list, tuple)):
        kwargs["letterbox_color"] = tuple(int(x) for x in kwargs["letterbox_color"][:3])
    if "device" in kwargs and section_cls is AudioConfig and kwargs["device"] is not None:
        try:
            kwargs["device"] = int(kwargs["device"])
        except (TypeError, ValueError):
            kwargs["device"] = None
    return section_cls(**kwargs)
