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
CONFIG_VERSION = 2
"""2: se quita `output.prerecorded_label` (la imagen emitida no lleva marcas;
los indicadores van en la interfaz local) y `video.transition_morph` pasa a
'off' por defecto (el flujo óptico era caro en el hilo de render)."""

FIT_MODES = ("cover", "contain", "stretch")
LOOP_MODES = ("pingpong", "loop")
PLAYBACK_MODES = ("director", "classic")
MORPH_MODES = ("auto", "off")
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


def library_dir() -> Path:
    """Clips grabados por la aplicación y manifiesto de la biblioteca."""
    return app_home() / "library"


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

    echo_reference: str = "auto"
    """Referencia de eco para no tomar por voz propia el audio de los demás
    participantes que sale por los altavoces.

    'off'  sin referencia: la decisión es solo la del VAD.
    'auto' usa, si existe, una entrada que ya contiene la mezcla de salida del
           sistema («Mezcla estéreo», «Stereo Mix», «What U Hear», «[Loopback]»).
           Nunca elige un micrófono real. Si no hay ninguna, equivale a 'off'.
    otro   nombre (exacto o parcial) del dispositivo de referencia, por ejemplo
           «CABLE Output» si la reunión se enruta por un cable virtual.

    La referencia solo puede vetar tramas; nunca activa el habla por sí sola.
    Con auriculares no hay fuga acústica y la referencia no cambia nada."""


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

    release_ms: int = 600
    """Silencio continuo necesario para volver a SILENT (hangover).

    Con el modo `director` la boca ya se cierra sola en las pausas (sigue la
    envolvente de energía), así que este tiempo solo decide cuándo se pasa al
    grupo de clips de silencio. 600 ms evita alternar en cada respiración."""

    pause_tolerance_ms: int = 350
    """Pausas cortas dentro del habla que no reinician el contador de ataque."""

    min_speaking_ms: int = 400
    """Permanencia mínima en SPEAKING: evita parpadeos con sílabas sueltas."""

    min_silent_ms: int = 250
    """Permanencia mínima en SILENT antes de poder reactivar."""

    micropause_release_ms: int = 200
    """Silencio continuo dentro del habla para marcar una micro-pausa (boca
    cerrada, pose de escucha) sin salir de SPEAKING. Rango 150-250 ms."""


@dataclass
class VideoConfig:
    """Clips, formato de salida y transiciones."""

    speaking_clip: str = ""
    silent_clip: str = ""
    """Clips principales. Son siempre el primer elemento de cada grupo; el
    primer clip de silencio es además la referencia de alineación y color."""

    library_manifest: str = ""
    """Manifiesto de la biblioteca (clips adicionales y gestos). Vacío =
    <app_home>/library/manifest.json. Si no existe, solo se usan los dos clips
    principales: es el comportamiento de versiones anteriores."""

    playback: str = "director"
    """'director' (grupos de clips, boca guiada por la voz, gestos, entradas
    por similitud) | 'classic' (dos clips con fundido, comportamiento previo).
    'classic' queda como alternativa robusta si el índice no se puede usar."""

    normalize_clips: bool = True
    """Alinea encuadre y color de cada clip con la referencia al preparar."""

    transition_morph: str = "off"
    """'off' (por defecto): fundido simple entre fotogramas ancla. 'auto':
    flujo óptico DIS cuando difieren mucho; se calcula en el hilo de render y
    cuesta varios ms por transición, por eso no es el valor por defecto."""

    anchor_wait_ms: int = 180
    """Espera máxima a un fotograma ancla (boca cerrada, pose neutra) antes de
    cambiar de segmento o de grupo. Si no aparece, fundido corto inmediato.
    Se acota a 150-200 ms."""

    fallback_fade_ms: int = 200
    """Duración del fundido de reserva cuando no hay ancla a tiempo (o el clip
    tiene pocas anclas y se entra por su principio o su final). Se acota a
    150-200 ms."""

    seed: int | None = None
    """Semilla de las decisiones aleatorias del director (None = del reloj).
    Fijarla hace reproducible una sesión, útil para pruebas."""

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
    """Duración del fundido cruzado en modo 'classic'. En modo 'director' es
    la duración máxima de la transición adaptativa (con un mínimo de 400 ms),
    que dura menos cuanto más se parecen los fotogramas que une."""

    resume_window_ms: int = 1500
    """Solo modo 'classic'. Si el habla vuelve antes de esto, el clip de habla
    continúa donde iba; si no, se rebobina."""

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
    """La imagen emitida es siempre limpia (sin rótulos ni marcas). Las claves
    antiguas `prerecorded_label` e `identify_prerecorded` se ignoran al cargar."""


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
class GesturesConfig:
    """Gestos manuales: atajos, panel y asentimiento automático."""

    hotkeys_enabled: bool = True
    """Interruptor general de los atajos globales (también en la bandeja)."""

    cancel_hotkey: str = "Ctrl+Alt+F12"
    pause_hotkey: str = "Ctrl+Alt+F11"
    """Pausa manual del video: congela la última imagen (nunca negro)."""

    cooldown_ms: int = 800
    """Tras terminar un gesto, una nueva petición del MISMO gesto dentro de
    este tiempo se ignora: evita repeticiones por pulsación doble."""

    show_panel: bool = False
    """Panel flotante con un botón por gesto."""

    panel_x: int = -1
    panel_y: int = -1

    auto_nod: bool = False
    """Lanza el gesto `auto_nod_gesture` de vez en cuando mientras callas.
    Desactivado por defecto: asentir cuando nadie habla también se nota."""

    auto_nod_gesture: str = "asentir"
    auto_nod_min_s: float = 60.0
    auto_nod_max_s: float = 150.0

    auto_speaking_shots: bool = False
    """Usa solas las frases breves importadas (n.º 7 «Respuesta corta», n.º 8
    «Frase corta» al empezar a hablar; n.º 6 «Terminar una explicación» como
    cierre en intervenciones largas). Desactivado por defecto: solo se activa
    si la configuración pone `true` de forma explícita. Si sigues hablando al
    acabar la frase se vuelve al bucle de habla; si callas antes, se pasa al
    silencio con un fundido corto."""


@dataclass
class MetricsConfig:
    enabled: bool = False
    interval_s: float = 2.0
    csv_path: str = ""
    """Vacío = <app_home>/metrics/metrics-<timestamp>.csv"""


@dataclass
class SpeechControlConfig:
    """«Control de habla»: autorización efectiva del vídeo de habla.

    El VAD y la puerta de voz detectan voz física; esta sección decide si esa
    voz puede convertirse en vídeo de habla. Ver `camvoice/permission.py`.
    """

    mode: str = "teams_auto"
    """'teams_auto' (habla solo con el micrófono de Teams activo, leído por
    UI Automation) o 'manual' (interruptor Autorizar/Bloquear; no verifica
    Teams). El permiso manual no se guarda: arranca siempre en Bloquear."""

    manual_hotkey: str = "Ctrl+Alt+F10"
    """Atajo global que alterna Autorizar/Bloquear en modo manual. Vacío =
    sin atajo. Se valida como los de gestos y no puede repetir uno de ellos."""

    teams_poll_ms: int = 250
    """Periodo de lectura del botón de micrófono de Teams (100–2000 ms)."""

    unmuted_expiry_ms: int = 450
    """Caducidad de una lectura UNMUTED (200–2000 ms). Pasado ese tiempo sin
    una lectura nueva el estado vuelve a UNKNOWN y el habla se bloquea: con
    lecturas cada 250 ms, 450 ms tolera una lectura lenta pero nunca dos
    perdidas."""


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    effects: EffectsConfig = field(default_factory=EffectsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    gestures: GesturesConfig = field(default_factory=GesturesConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    speech: SpeechControlConfig = field(default_factory=SpeechControlConfig)

    # ------------------------------------------------------------------ #
    # Serialización
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AppConfig:
        data = data or {}
        kwargs: dict[str, Any] = {}
        version = data.get("version") if isinstance(data.get("version"), int) else 1
        for name, section_cls in _SECTIONS.items():
            raw = data.get(name)
            if isinstance(raw, dict):
                kwargs[name] = _build_section(section_cls, _migrate(name, raw, version))
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
        if v.playback not in PLAYBACK_MODES:
            errs.append(f"Modo de reproducción desconocido: {v.playback!r}.")
        if v.transition_morph not in MORPH_MODES:
            errs.append(f"Modo de transición desconocido: {v.transition_morph!r}.")
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
        if not 150 <= d.micropause_release_ms <= 250:
            errs.append("La micro-pausa debe estar entre 150 y 250 ms.")
        if not 150 <= v.anchor_wait_ms <= 200:
            errs.append("La espera al fotograma ancla debe estar entre 150 y 200 ms.")
        if not 150 <= v.fallback_fade_ms <= 200:
            errs.append("El fundido de reserva debe estar entre 150 y 200 ms.")
        if min(d.attack_ms, d.release_ms, d.pause_tolerance_ms) < 0:
            errs.append("Los tiempos del detector no pueden ser negativos.")
        if d.noise_floor_tau_s <= 0:
            errs.append("La constante de tiempo del piso de ruido debe ser positiva.")

        g = self.gestures
        if g.cooldown_ms < 0:
            errs.append("La espera entre gestos repetidos no puede ser negativa.")
        if not 5.0 <= g.auto_nod_min_s <= g.auto_nod_max_s:
            errs.append(
                "El intervalo del asentimiento automático debe ser de al menos 5 s "
                "y el mínimo no puede superar al máximo."
            )

        s = self.speech
        if s.mode not in ("teams_auto", "manual"):
            errs.append("El control de habla debe ser 'teams_auto' o 'manual'.")
        if not 100 <= s.teams_poll_ms <= 2000:
            errs.append("El periodo de lectura de Teams debe estar entre 100 y 2000 ms.")
        if not 200 <= s.unmuted_expiry_ms <= 2000:
            errs.append("La caducidad de la lectura de Teams debe estar entre 200 y 2000 ms.")
        if s.manual_hotkey.strip():
            from .gestures import HotkeyError, validate_hotkey

            try:
                validate_hotkey(s.manual_hotkey)
            except HotkeyError as exc:
                errs.append(f"Atajo de autorizar habla: {exc}")

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
    "gestures": GesturesConfig,
    "metrics": MetricsConfig,
    "speech": SpeechControlConfig,
}


def _migrate(section: str, raw: dict[str, Any], version: int) -> dict[str, Any]:
    """Adapta una sección guardada por una versión anterior.

    v1 -> v2: `transition_morph` era 'auto' por defecto y no se podía
    distinguir un valor elegido de uno heredado; pasa a 'off' (fundido entre
    anclas, barato). `prerecorded_label` desaparece (clave desconocida, se
    descarta en `_build_section`).
    """
    raw = dict(raw)
    if version < 2 and section == "video" and raw.get("transition_morph") == "auto":
        raw["transition_morph"] = "off"
    if section == "vad" and "micropause_release_ms" in raw:
        try:
            raw["micropause_release_ms"] = int(min(250, max(150, int(raw["micropause_release_ms"]))))
        except (TypeError, ValueError):
            raw.pop("micropause_release_ms")
    for key in ("anchor_wait_ms", "fallback_fade_ms"):
        if section == "video" and key in raw:
            try:
                raw[key] = int(min(200, max(150, int(raw[key]))))
            except (TypeError, ValueError):
                raw.pop(key)
    return raw


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
