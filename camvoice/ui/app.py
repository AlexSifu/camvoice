"""Interfaz gráfica (PySide6).

Reglas de la ventana:

* La UI nunca genera fotogramas ni toca el audio. Un único `QTimer` lee el
  estado publicado por los hilos de audio y render y refresca lo que ha
  cambiado. Así ningún arrastre de un deslizador puede provocar un tirón en la
  salida.
* La preparación de clips (lo único que tarda) corre en un hilo aparte con
  barra de progreso: la ventana sigue respondiendo.
* Los controles se separan en dos clases. Los que se aplican **en caliente**
  (sensibilidad, tiempos, velocidades, transición, todos los efectos, cámara
  virtual) quedan activos mientras la sesión corre. Los que invalidan la caché
  de fotogramas (clips, resolución, FPS, encuadre) se bloquean con una
  explicación, en vez de fingir que surten efecto.

La configuración va en pestañas: el panel único obligaba a desplazarse para
llegar a lo de abajo, y con los efectos añadidos no cabría.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..audio.capture import AudioError, DeviceInfo, list_input_devices
from ..config import AppConfig, EffectsConfig
from ..controller import Session, SessionError
from ..video.cache import ClipCacheError, probe_clip
from .recorder_tab import RecorderTab
from .tray import TrayController, tray_available
from .widgets import (
    STYLESHEET,
    Card,
    PercentSlider,
    combo,
    labelled_row,
    spin_double,
    state_badge_style,
)

VIDEO_FILTER = "Videos (*.mp4 *.mov *.avi *.mkv *.webm *.m4v);;Todos los archivos (*.*)"

RESOLUTIONS = [
    ("320 × 240   · 4:3 · mínima", 320, 240),
    ("480 × 360   · 4:3", 480, 360),
    ("640 × 480   · 4:3 · recomendada", 640, 480),
    ("640 × 360   · 16:9", 640, 360),
    ("848 × 480   · 16:9", 848, 480),
    ("960 × 540   · 16:9", 960, 540),
    ("960 × 720   · 4:3", 960, 720),
    ("1280 × 720  · 16:9 · HD", 1280, 720),
    ("1920 × 1080 · 16:9 · Full HD", 1920, 1080),
]

FPS_OPTIONS = [10, 12, 15, 20, 24, 25, 30]

EFFECT_PRESETS: dict[str, dict[str, float | bool]] = {
    "Sin efectos": {},
    "Discreto": {"blur": 12.0, "grain": 8.0, "vignette": 15.0},
    "Cámara mala": {"blur": 22.0, "grain": 30.0, "saturation": -20.0, "contrast": -12.0},
    "Privacidad media": {"blur": 45.0},
    "Privacidad alta": {"pixelate": 60.0},
    "Irreconocible": {"pixelate": 85.0, "blur": 20.0},
}
"""Puntos de partida razonables.

Existen porque nueve deslizadores a cero no sugieren qué hacer con ellos. Cada
valor sigue siendo editable después de aplicar un preajuste.
"""

EFFECT_FIELDS = (
    "blur", "pixelate", "brightness", "contrast",
    "saturation", "zoom", "grain", "vignette",
)


# --------------------------------------------------------------------------- #
# Hilo de preparación
# --------------------------------------------------------------------------- #


class PrepareWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object, str)
    """(PreparedClips | None, mensaje de error)"""

    def __init__(self, session: Session, force: bool) -> None:
        super().__init__()
        self._session = session
        self._force = force

    def run(self) -> None:
        try:
            clips = self._session.prepare(
                progress=lambda d, t, m: self.progress.emit(d, t, m), force=self._force
            )
        except (SessionError, ClipCacheError) as exc:
            self.finished.emit(None, str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensivo
            self.finished.emit(None, f"Fallo inesperado al preparar los clips: {exc}")
            return
        self.finished.emit(clips, "")


# --------------------------------------------------------------------------- #
# Ventana
# --------------------------------------------------------------------------- #


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, config_file: Path | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.config_file = config_file
        self.session = Session(cfg)
        self._devices: list[DeviceInfo] = []
        self._thread: QThread | None = None
        self._worker: PrepareWorker | None = None
        self._loading = True
        self._last_message = ""
        self.tray: TrayController | None = None
        self._really_quit = False

        self.setWindowTitle("Cámara virtual por voz")
        self.resize(1180, 780)
        self.setMinimumSize(940, 640)
        self.setStyleSheet(STYLESHEET)

        self._build()
        self._load_devices()
        self._load_from_config()
        self._loading = False
        self._update_clip_info()
        self._set_running(False)
        self._setup_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(max(33, int(1000 / max(1, cfg.video.preview_fps))))

    # ------------------------------------------------------------------ #
    # Construcción
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)
        root.addWidget(self._build_left(), 5)
        root.addWidget(self._build_right(), 4)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Listo. Empieza eligiendo los dos videos.")

    # --- izquierda: estado y vista previa ----------------------------- #

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.state_badge = QLabel()
        self.state_badge.setAlignment(Qt.AlignCenter)
        self.state_badge.setMinimumHeight(48)
        self._apply_badge("-")
        layout.addWidget(self.state_badge)

        self.preview = QLabel("La vista previa aparecerá al iniciar.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(420, 300)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet(
            "background:#0c0e11; color:#6e7681;"
            "border:1px solid #2d343d; border-radius:8px;"
        )
        layout.addWidget(self.preview, 1)

        medidor = Card("nivel del micrófono")
        self.level_meter = QProgressBar()
        self.level_meter.setRange(-60, 0)
        self.level_meter.setValue(-60)
        self.level_meter.setFormat("%v dBFS")
        medidor.add(self.level_meter)
        self.detector_info = QLabel("Detector detenido.")
        self.detector_info.setObjectName("dim")
        self.detector_info.setWordWrap(True)
        medidor.add(self.detector_info)
        layout.addWidget(medidor)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        self.banner.setFrameShape(QFrame.StyledPanel)
        layout.addWidget(self.banner)

        botones = QHBoxLayout()
        botones.setSpacing(8)
        self.start_button = QPushButton("▶   Iniciar")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self._on_start)
        botones.addWidget(self.start_button, 2)

        self.stop_button = QPushButton("■   Detener")
        self.stop_button.setMinimumHeight(42)
        self.stop_button.clicked.connect(self._on_stop)
        botones.addWidget(self.stop_button, 1)
        layout.addLayout(botones)

        self.metrics_label = QLabel("—")
        self.metrics_label.setStyleSheet(
            "color:#8b949e; font-family:Consolas,monospace; font-size:11px;"
        )
        layout.addWidget(self.metrics_label)
        return panel

    # --- derecha: pestañas -------------------------------------------- #

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.tabs = QTabWidget()
        self.recorder_tab = RecorderTab(self._on_clip_recorded)
        self.tabs.addTab(self._scrollable(self._tab_clips()), "Videos")
        self.tabs.addTab(self._scrollable(self.recorder_tab), "Grabar")
        self.tabs.addTab(self._scrollable(self._tab_audio()), "Micrófono")
        self.tabs.addTab(self._scrollable(self._tab_detector()), "Detector")
        self.tabs.addTab(self._scrollable(self._tab_effects()), "Efectos")
        self.tabs.addTab(self._scrollable(self._tab_output()), "Salida")
        layout.addWidget(self.tabs, 1)

        guardar = QPushButton("Guardar configuración")
        guardar.clicked.connect(self._on_save)
        layout.addWidget(guardar)
        return panel

    @staticmethod
    def _scrollable(contenido: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # Sin desplazamiento horizontal: el contenido se ajusta al ancho del
        # panel. Si no, una etiqueta larga ensancha la página y los botones de
        # la derecha quedan recortados fuera de la vista.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(contenido)
        return scroll

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(4, 10, 10, 10)
        layout.setSpacing(10)
        return pagina, layout

    # --- pestaña: videos ---------------------------------------------- #

    def _tab_clips(self) -> QWidget:
        pagina, layout = self._page()

        tarjeta = Card("clips de referencia")
        self.speaking_path = QLabel("(sin seleccionar)")
        self.silent_path = QLabel("(sin seleccionar)")
        for etiqueta in (self.speaking_path, self.silent_path):
            etiqueta.setObjectName("dim")
            etiqueta.setWordWrap(True)

        self.speaking_button = QPushButton("Elegir clip HABLANDO…")
        self.speaking_button.clicked.connect(self._pick_speaking)
        tarjeta.add(self.speaking_button)
        tarjeta.add(self.speaking_path)

        self.silent_button = QPushButton("Elegir clip EN SILENCIO…")
        self.silent_button.clicked.connect(self._pick_silent)
        tarjeta.add(self.silent_button)
        tarjeta.add(self.silent_path)

        self.clip_info = QLabel()
        self.clip_info.setWordWrap(True)
        self.clip_info.setStyleSheet("color:#8b949e; font-size:11px;")
        tarjeta.add(self.clip_info)
        layout.addWidget(tarjeta)

        consejo = Card("cómo grabarlos")
        texto = QLabel(
            "Graba los dos <b>seguidos, sin mover la cámara ni cambiar la luz</b>, "
            "de 8 a 20 s cada uno.<br><br>"
            "Que el <b>primer fotograma de ambos</b> tenga la boca cerrada y la "
            "mirada al frente: es la pose desde la que se reanuda el habla tras un "
            "silencio largo.<br><br>"
            "Evita gestos amplios, manos en plano y girar la cabeza."
        )
        texto.setWordWrap(True)
        texto.setObjectName("dim")
        consejo.add(texto)
        layout.addWidget(consejo)

        reproduccion = Card("reproducción")
        self.loop_combo = combo(
            [("Ida y vuelta (sin costura)", "pingpong"), ("Bucle normal", "loop")],
            ayuda="Ida y vuelta reproduce hacia atrás al llegar al final, lo que "
            "elimina el corte visible del salto del último al primer fotograma.",
        )
        self.loop_combo.currentIndexChanged.connect(self._on_live_video)
        reproduccion.add(labelled_row("Modo de bucle", self.loop_combo))

        self.speed_speaking = spin_double(0.25, 2.0, 0.05, 1.0)
        self.speed_speaking.valueChanged.connect(self._on_live_video)
        reproduccion.add(
            labelled_row(
                "Velocidad habla", self.speed_speaking,
                ayuda="Útil si el movimiento de la boca se ve demasiado lento o "
                "demasiado frenético.",
            )
        )

        self.speed_silent = spin_double(0.25, 2.0, 0.05, 1.0)
        self.speed_silent.valueChanged.connect(self._on_live_video)
        reproduccion.add(labelled_row("Velocidad silencio", self.speed_silent))

        self.transition = PercentSlider(
            "Transición", maximo=800, defecto=160, sufijo=" ms",
            ayuda="Duración del fundido cruzado al cambiar de clip. 0 corta en "
            "seco; más de 400 ms se percibe como lentitud.",
        )
        self.transition.bind(lambda _v: self._on_live_video())
        reproduccion.add(self.transition)

        self.resume_window = PercentSlider(
            "Reanudar habla antes de", maximo=5000, defecto=1500, sufijo=" ms",
            ayuda="Si el habla vuelve antes de este tiempo el clip continúa donde "
            "iba; si no, se rebobina al primer fotograma para no reanudar con la "
            "boca a medio abrir.",
        )
        self.resume_window.bind(lambda _v: self._on_live_video())
        reproduccion.add(self.resume_window)

        layout.addWidget(reproduccion)
        layout.addStretch(1)
        return pagina

    # --- pestaña: micrófono ------------------------------------------- #

    def _tab_audio(self) -> QWidget:
        pagina, layout = self._page()

        tarjeta = Card("dispositivo de entrada")
        fila = QHBoxLayout()
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        fila.addWidget(self.mic_combo, 1)
        actualizar = QPushButton("Actualizar")
        actualizar.clicked.connect(self._load_devices)
        fila.addWidget(actualizar)
        tarjeta.add_layout(fila)

        nota = QLabel(
            "Se puede cambiar con la sesión en marcha. Los dispositivos WASAPI "
            "aparecen primero: dan la latencia más baja y la frecuencia nativa."
        )
        nota.setWordWrap(True)
        nota.setObjectName("dim")
        tarjeta.add(nota)
        layout.addWidget(tarjeta)

        aviso = Card("si el nivel salta a −120 dB")
        texto = QLabel(
            "Si el nivel alterna entre valores normales y exactamente −120 dBFS, "
            "algo en la cadena de captura está recortando a cero: la supresión de "
            "ruido de Windows, un «modo comunicaciones» o el software del propio "
            "micrófono. Eso degrada cualquier detector de voz.<br><br>"
            "Desactívalo en <i>Configuración → Sistema → Sonido → Entrada → tu "
            "micrófono → Mejoras de audio</i>."
        )
        texto.setWordWrap(True)
        texto.setObjectName("dim")
        aviso.add(texto)
        layout.addWidget(aviso)
        layout.addStretch(1)
        return pagina

    # --- pestaña: detector -------------------------------------------- #

    def _tab_detector(self) -> QWidget:
        pagina, layout = self._page()

        motor = Card("motor de detección")
        self.vad_backend = combo(
            [
                ("Automático (WebRTC si está disponible)", "auto"),
                ("WebRTC VAD", "webrtc"),
                ("Energía (RMS adaptativo)", "rms"),
            ],
            ayuda="WebRTC distingue voz de ruido estacionario mucho mejor que el "
            "nivel de energía. El modo energía es el respaldo.",
        )
        self.vad_backend.currentIndexChanged.connect(
            lambda: self._apply_vad(backend=self.vad_backend.currentData())
        )
        motor.add(labelled_row("Detector", self.vad_backend))

        self.aggressiveness = PercentSlider(
            "Exigencia", maximo=3, defecto=2, sufijo="",
            ayuda="0 a 3. Más alto = menos falsos positivos con ruido, pero puede "
            "perder voz baja.",
        )
        self.aggressiveness.bind(lambda v: self._apply_vad(aggressiveness=int(v)))
        motor.add(self.aggressiveness)
        layout.addWidget(motor)

        umbrales = Card("sensibilidad")
        self.snr_on = PercentSlider(
            "Umbral de voz", minimo=3, maximo=25, defecto=9, sufijo=" dB",
            ayuda="Cuántos dB por encima del ruido de fondo cuentan como voz.",
        )
        self.snr_on.bind(lambda v: self._apply_vad(snr_on_db=float(v)))
        umbrales.add(self.snr_on)

        self.gate_db = PercentSlider(
            "Puerta de silencio", minimo=-70, maximo=-25, defecto=-50, sufijo=" dB",
            ayuda="Por debajo de este nivel absoluto nunca hay voz. Si el detector "
            "se activa solo, súbelo; si tu voz no lo activa, bájalo.",
        )
        self.gate_db.bind(lambda v: self._apply_vad(gate_db=float(v)))
        umbrales.add(self.gate_db)
        layout.addWidget(umbrales)

        tiempos = Card("tiempos")
        self.attack = PercentSlider(
            "Activación", minimo=20, maximo=600, defecto=120, sufijo=" ms",
            ayuda="Voz continua necesaria para pasar a «hablando». Es lo que domina "
            "la latencia percibida.",
        )
        self.attack.bind(lambda v: self._apply_vad(attack_ms=int(v)))
        tiempos.add(self.attack)

        self.release = PercentSlider(
            "Permanencia en voz", minimo=100, maximo=2000, defecto=450, sufijo=" ms",
            ayuda="Silencio continuo necesario para volver a «en silencio». Subirlo "
            "evita cortes entre frases.",
        )
        self.release.bind(lambda v: self._apply_vad(release_ms=int(v)))
        tiempos.add(self.release)

        self.pause_tol = PercentSlider(
            "Tolerancia a pausas", maximo=1200, defecto=350, sufijo=" ms",
            ayuda="Huecos cortos al hablar que no reinician la cuenta de activación.",
        )
        self.pause_tol.bind(lambda v: self._apply_vad(pause_tolerance_ms=int(v)))
        tiempos.add(self.pause_tol)
        layout.addWidget(tiempos)

        ayuda = Card("ajustarlo con datos, no a ojo")
        texto = QLabel(
            "La sensibilidad depende del micrófono y de la sala: no hay un valor "
            "por defecto que sirva para todos. Para medir:<br><br>"
            "<code>camvoice-cli vad-test --duration 30</code><br><br>"
            "Muestra nivel, piso de ruido y cada cambio de estado con marca de "
            "tiempo. En silencio deberías ver <b>0 cambios</b>."
        )
        texto.setWordWrap(True)
        texto.setObjectName("dim")
        ayuda.add(texto)
        layout.addWidget(ayuda)
        layout.addStretch(1)
        return pagina

    # --- pestaña: efectos --------------------------------------------- #

    def _tab_effects(self) -> QWidget:
        pagina, layout = self._page()

        cabecera = Card("efectos")
        self.effects_enabled = QCheckBox("Aplicar efectos a la salida")
        self.effects_enabled.stateChanged.connect(self._on_effects_toggled)
        cabecera.add(self.effects_enabled)

        self.preset_combo = combo(
            [(nombre, nombre) for nombre in EFFECT_PRESETS],
            ayuda="Puntos de partida. Después puedes ajustar cada deslizador.",
        )
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        cabecera.add(labelled_row("Preajuste", self.preset_combo))

        self.effects_summary = QLabel()
        self.effects_summary.setWordWrap(True)
        self.effects_summary.setObjectName("dim")
        cabecera.add(self.effects_summary)
        layout.addWidget(cabecera)

        privacidad = Card("privacidad")
        self.blur = PercentSlider(
            "Desenfoque", defecto=0,
            ayuda="Desenfoque gaussiano. El porcentaje es independiente de la "
            "resolución: el 60 % se ve igual a 320×240 que a 720p.",
        )
        self.blur.bind(lambda v: self._apply_effect(blur=v))
        privacidad.add(self.blur)

        self.pixelate = PercentSlider(
            "Pixelado", defecto=0,
            ayuda="Mosaico de bloques. Al 100 % cada bloque mide 1/16 del ancho.",
        )
        self.pixelate.bind(lambda v: self._apply_effect(pixelate=v))
        privacidad.add(self.pixelate)

        nota = QLabel(
            "Desenfoque y pixelado altos a la vez se anulan: el desenfoque borra "
            "los bloques. Usa uno de los dos."
        )
        nota.setWordWrap(True)
        nota.setStyleSheet("color:#8b949e; font-size:11px;")
        privacidad.add(nota)
        layout.addWidget(privacidad)

        color = Card("imagen")
        self.brightness = PercentSlider(
            "Brillo", minimo=-100, maximo=100, defecto=0,
            ayuda="Útil para igualar el clip con la luz actual de la sala.",
        )
        self.brightness.bind(lambda v: self._apply_effect(brightness=v))
        color.add(self.brightness)

        self.contrast = PercentSlider("Contraste", minimo=-100, maximo=100, defecto=0)
        self.contrast.bind(lambda v: self._apply_effect(contrast=v))
        color.add(self.contrast)

        self.saturation = PercentSlider(
            "Saturación", minimo=-100, maximo=100, defecto=0,
            ayuda="−100 deja la imagen en blanco y negro.",
        )
        self.saturation.bind(lambda v: self._apply_effect(saturation=v))
        color.add(self.saturation)
        layout.addWidget(color)

        extras = Card("aspecto y encuadre")
        self.zoom = PercentSlider(
            "Zoom", minimo=1.0, maximo=3.0, defecto=1.0, sufijo="×", decimales=2,
            ayuda="Recorte centrado ampliado. Permite acercar la cara o quitar "
            "fondo de los bordes sin volver a grabar.",
        )
        self.zoom.bind(lambda v: self._apply_effect(zoom=v))
        extras.add(self.zoom)

        self.grain = PercentSlider(
            "Grano", defecto=0,
            ayuda="Ruido de luminancia. Un clip comprimido se ve demasiado limpio "
            "para ser una webcam; un poco de grano acerca el aspecto.",
        )
        self.grain.bind(lambda v: self._apply_effect(grain=v))
        extras.add(self.grain)

        self.vignette = PercentSlider(
            "Viñeta", defecto=0, ayuda="Oscurece las esquinas.",
        )
        self.vignette.bind(lambda v: self._apply_effect(vignette=v))
        extras.add(self.vignette)

        self.flip_live = QCheckBox("Efecto espejo (en vivo)")
        self.flip_live.setToolTip(
            "Se aplica al instante, sin volver a preparar la caché. El espejo de "
            "la pestaña Salida se hornea al preparar y cuesta cero, pero obliga a "
            "reiniciar la sesión."
        )
        self.flip_live.stateChanged.connect(
            lambda: self._apply_effect(flip_horizontal=self.flip_live.isChecked())
        )
        extras.add(self.flip_live)
        layout.addWidget(extras)

        reset = QPushButton("Restablecer todos los efectos")
        reset.clicked.connect(self._reset_effects)
        layout.addWidget(reset)
        layout.addStretch(1)
        return pagina

    # --- pestaña: salida ---------------------------------------------- #

    def _tab_output(self) -> QWidget:
        pagina, layout = self._page()

        formato = Card("formato de salida")
        self.resolution = combo([(etiqueta, (w, h)) for etiqueta, w, h in RESOLUTIONS])
        self.resolution.currentIndexChanged.connect(self._on_geometry_changed)
        formato.add(labelled_row("Resolución", self.resolution))

        self.fps_combo = combo([(f"{f} FPS", f) for f in FPS_OPTIONS])
        self.fps_combo.currentIndexChanged.connect(self._on_geometry_changed)
        formato.add(labelled_row("Cadencia", self.fps_combo))

        self.fit_combo = combo(
            [
                ("Recortar para llenar (sin deformar)", "cover"),
                ("Caber entero (con barras)", "contain"),
                ("Estirar (deforma)", "stretch"),
            ],
            ayuda="Qué hacer cuando la relación de aspecto del clip no coincide "
            "con la de salida. No se puede a la vez no deformar, no recortar y no "
            "dejar barras.",
        )
        self.fit_combo.currentIndexChanged.connect(self._on_geometry_changed)
        formato.add(labelled_row("Encuadre", self.fit_combo))

        self.mirror_check = QCheckBox("Espejo horneado en la caché")
        self.mirror_check.setToolTip(
            "Coste cero en ejecución, pero obliga a reiniciar. Para probar el "
            "espejo al momento usa el de la pestaña Efectos."
        )
        self.mirror_check.stateChanged.connect(self._on_geometry_changed)
        formato.add(self.mirror_check)

        self.cache_info = QLabel()
        self.cache_info.setWordWrap(True)
        self.cache_info.setStyleSheet("color:#8b949e; font-size:11px;")
        formato.add(self.cache_info)
        layout.addWidget(formato)

        camara = Card("cámara virtual")
        self.virtual_check = QCheckBox("Enviar a la cámara virtual")
        self.virtual_check.stateChanged.connect(self._on_virtual_toggled)
        camara.add(self.virtual_check)

        self.virtual_status = QLabel()
        self.virtual_status.setWordWrap(True)
        self.virtual_status.setObjectName("dim")
        camara.add(self.virtual_status)

        fila = QHBoxLayout()
        comprobar = QPushButton("Comprobar cámara")
        comprobar.clicked.connect(self._on_check_camera)
        fila.addWidget(comprobar)
        listar = QPushButton("Ver cámaras del sistema")
        listar.clicked.connect(self._on_list_cameras)
        fila.addWidget(listar)
        camara.add_layout(fila)
        layout.addWidget(camara)

        fondo = Card("segundo plano")
        self.tray_check = QCheckBox("Al cerrar la ventana, seguir en el área de notificación")
        self.tray_check.setToolTip(
            "La aplicación sigue funcionando con un icono junto al reloj, que "
            "cambia de color según el estado del detector."
        )
        self.tray_check.stateChanged.connect(self._on_tray_toggled)
        fondo.add(self.tray_check)

        aviso_bandeja = QLabel(
            "Quita la ventana de la barra de tareas y de Alt+Tab. <b>No oculta "
            "la aplicación del sistema</b>: sigue estando en el Administrador de "
            "tareas, en el propio icono del área de notificación y en la lista "
            "de cámaras del programa de videollamada, donde aparece «CamVoice "
            "Virtual Camera»."
        )
        aviso_bandeja.setWordWrap(True)
        aviso_bandeja.setObjectName("dim")
        aviso_bandeja.setStyleSheet("color:#8b949e; font-size:11px;")
        fondo.add(aviso_bandeja)
        layout.addWidget(fondo)

        layout.addStretch(1)
        return pagina

    # ------------------------------------------------------------------ #
    # Bandeja del sistema
    # ------------------------------------------------------------------ #

    def _setup_tray(self) -> None:
        if not tray_available():
            self.tray_check.setEnabled(False)
            self.tray_check.setToolTip(
                "Este escritorio no ofrece área de notificación."
            )
            return
        self.tray = TrayController(
            on_show=self._restore_from_tray,
            on_start=self._on_start,
            on_stop=self._on_stop,
            on_quit=self._quit_from_tray,
            parent=self,
        )
        self.tray.show()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._really_quit = True
        self.close()

    def _on_tray_toggled(self) -> None:
        if self._loading:
            return
        self.cfg.ui.minimize_to_tray = self.tray_check.isChecked()

    # ------------------------------------------------------------------ #
    # Grabación
    # ------------------------------------------------------------------ #

    def _on_clip_recorded(self, kind: str, ruta: str) -> None:
        """La pestaña Grabar acaba de producir un clip utilizable."""
        if kind == "speaking":
            self.cfg.video.speaking_clip = ruta
            self.speaking_path.setText(ruta)
        else:
            self.cfg.video.silent_clip = ruta
            self.silent_path.setText(ruta)
        self._update_clip_info()
        self._update_cache_info()
        self.tabs.setCurrentIndex(0)
        self.statusBar().showMessage(f"Clip asignado: {ruta}", 6000)

    # ------------------------------------------------------------------ #
    # Carga y guardado
    # ------------------------------------------------------------------ #

    def _load_devices(self) -> None:
        anterior = self.mic_combo.currentData() if self.mic_combo.count() else None
        previo, self._loading = self._loading, True
        self.mic_combo.clear()
        try:
            self._devices = list_input_devices()
        except AudioError as exc:
            self._devices = []
            self._show_message(str(exc), "err")
        self.mic_combo.addItem("Dispositivo predeterminado de Windows", None)
        for dev in self._devices:
            self.mic_combo.addItem(dev.label, dev.index)
        objetivo = anterior if anterior is not None else self.cfg.audio.device
        self.mic_combo.setCurrentIndex(max(0, self.mic_combo.findData(objetivo)))
        self._loading = previo

    def _load_from_config(self) -> None:
        v, d, o, e = self.cfg.video, self.cfg.vad, self.cfg.output, self.cfg.effects
        self.speaking_path.setText(v.speaking_clip or "(sin seleccionar)")
        self.silent_path.setText(v.silent_clip or "(sin seleccionar)")

        indice = self.resolution.findData((v.width, v.height))
        if indice < 0:
            self.resolution.addItem(
                f"{v.width} × {v.height} · personalizada", (v.width, v.height)
            )
            indice = self.resolution.count() - 1
        self.resolution.setCurrentIndex(indice)

        indice_fps = self.fps_combo.findData(v.fps)
        if indice_fps < 0:
            self.fps_combo.addItem(f"{v.fps} FPS", v.fps)
            indice_fps = self.fps_combo.count() - 1
        self.fps_combo.setCurrentIndex(indice_fps)

        self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(v.fit)))
        self.mirror_check.setChecked(v.mirror)
        self.loop_combo.setCurrentIndex(max(0, self.loop_combo.findData(v.loop_mode)))
        self.speed_speaking.setValue(v.speaking_speed)
        self.speed_silent.setValue(v.silent_speed)
        self.transition.set_silently(v.transition_ms)
        self.resume_window.set_silently(v.resume_window_ms)
        self.virtual_check.setChecked(o.enabled)

        self.vad_backend.setCurrentIndex(max(0, self.vad_backend.findData(d.backend)))
        self.aggressiveness.set_silently(d.aggressiveness)
        self.snr_on.set_silently(d.snr_on_db)
        self.gate_db.set_silently(d.gate_db)
        self.attack.set_silently(d.attack_ms)
        self.release.set_silently(d.release_ms)
        self.pause_tol.set_silently(d.pause_tolerance_ms)

        self.tray_check.setChecked(self.cfg.ui.minimize_to_tray)
        self.effects_enabled.setChecked(e.enabled)
        self._load_effects_into_widgets(e)
        self._update_cache_info()
        self._update_effects_summary()

    def _load_effects_into_widgets(self, e: EffectsConfig) -> None:
        for widget, valor in (
            (self.blur, e.blur), (self.pixelate, e.pixelate),
            (self.brightness, e.brightness), (self.contrast, e.contrast),
            (self.saturation, e.saturation), (self.zoom, e.zoom),
            (self.grain, e.grain), (self.vignette, e.vignette),
        ):
            widget.set_silently(valor)
        anterior = self.flip_live.blockSignals(True)
        self.flip_live.setChecked(e.flip_horizontal)
        self.flip_live.blockSignals(anterior)

    def _collect_into_config(self) -> None:
        v, o = self.cfg.video, self.cfg.output
        ancho, alto = self.resolution.currentData()
        v.width, v.height = int(ancho), int(alto)
        v.fps = int(self.fps_combo.currentData())
        v.fit = self.fit_combo.currentData()
        v.mirror = self.mirror_check.isChecked()
        v.loop_mode = self.loop_combo.currentData()
        v.speaking_speed = float(self.speed_speaking.value())
        v.silent_speed = float(self.speed_silent.value())
        v.transition_ms = int(self.transition.value())
        v.resume_window_ms = int(self.resume_window.value())
        o.enabled = self.virtual_check.isChecked()
        self.cfg.ui.minimize_to_tray = self.tray_check.isChecked()
        self.cfg.audio.device = self.mic_combo.currentData()
        self.cfg.audio.device_name = next(
            (d.name for d in self._devices if d.index == self.cfg.audio.device), ""
        )

    def _on_save(self) -> None:
        self._collect_into_config()
        errores = self.cfg.validate(require_clips=False)
        if errores:
            QMessageBox.warning(self, "Configuración incompleta", "\n".join(errores))
            return
        ruta = self.cfg.save(self.config_file)
        self.statusBar().showMessage(f"Guardado en {ruta}", 5000)

    # ------------------------------------------------------------------ #
    # Clips
    # ------------------------------------------------------------------ #

    def _pick_speaking(self) -> None:
        self._pick_clip("Selecciona el video en el que HABLAS", "speaking")

    def _pick_silent(self) -> None:
        self._pick_clip("Selecciona el video en el que estás EN SILENCIO", "silent")

    def _pick_clip(self, titulo: str, cual: str) -> None:
        inicio = self.cfg.video.speaking_clip or self.cfg.video.silent_clip or ""
        ruta, _ = QFileDialog.getOpenFileName(
            self, titulo, str(Path(inicio).parent) if inicio else "", VIDEO_FILTER
        )
        if not ruta:
            return
        try:
            probe_clip(ruta)
        except ClipCacheError as exc:
            QMessageBox.critical(self, "Video no utilizable", str(exc))
            return
        if cual == "speaking":
            self.cfg.video.speaking_clip = ruta
            self.speaking_path.setText(ruta)
        else:
            self.cfg.video.silent_clip = ruta
            self.silent_path.setText(ruta)
        self._update_clip_info()
        self._update_cache_info()

    def _update_clip_info(self) -> None:
        lineas: list[str] = []
        for etiqueta, ruta in (
            ("Hablando", self.cfg.video.speaking_clip),
            ("Silencio", self.cfg.video.silent_clip),
        ):
            if not ruta:
                continue
            try:
                probe = probe_clip(ruta)
            except ClipCacheError as exc:
                lineas.append(f"{etiqueta}: {exc}")
                continue
            lineas.append(
                f"{etiqueta}: {probe.width}×{probe.height}, {probe.fps:.1f} FPS, "
                f"{probe.duration_s:.1f} s, {probe.fourcc or '?'}"
            )
        self.clip_info.setText(
            "\n".join(lineas) or "Selecciona los dos videos para continuar."
        )

    def _update_cache_info(self) -> None:
        from ..config import frame_bytes

        v = self.cfg.video
        try:
            por_fotograma = frame_bytes(v.width, v.height, v.pixel_format)
        except ValueError:
            return
        self.cache_info.setText(
            f"Caché de fotogramas: {por_fotograma * v.fps / 1e6:.1f} MB por segundo "
            f"de clip ({por_fotograma / 1024:.0f} KB por fotograma, "
            f"{v.pixel_format.upper()}). Es el precio de no decodificar video en el "
            "bucle de salida."
        )

    # ------------------------------------------------------------------ #
    # Cambios en caliente
    # ------------------------------------------------------------------ #

    def _apply_vad(self, **cambios) -> None:
        if self._loading:
            return
        for clave, valor in cambios.items():
            setattr(self.cfg.vad, clave, valor)
        # El umbral de apagado sigue al de encendido para que no se invierta.
        cambios.setdefault("snr_off_db", max(1.0, self.cfg.vad.snr_on_db - 4.0))
        self.cfg.vad.snr_off_db = cambios["snr_off_db"]
        try:
            self.session.audio.apply_vad_changes(**cambios)
        except AttributeError:
            pass

    def _apply_effect(self, **cambios) -> None:
        """Surte efecto al instante: la cadena lee el mismo objeto de config."""
        if self._loading:
            return
        for clave, valor in cambios.items():
            setattr(self.cfg.effects, clave, valor)
        self._update_effects_summary()

    def _on_effects_toggled(self) -> None:
        if self._loading:
            return
        self.cfg.effects.enabled = self.effects_enabled.isChecked()
        self._update_effects_summary()

    def _on_preset(self) -> None:
        if self._loading:
            return
        nombre = self.preset_combo.currentData()
        ajustes = EFFECT_PRESETS.get(nombre, {})
        base = EffectsConfig()
        previo, self._loading = self._loading, True
        try:
            for campo in EFFECT_FIELDS:
                setattr(
                    self.cfg.effects, campo, ajustes.get(campo, getattr(base, campo))
                )
            self.cfg.effects.flip_horizontal = bool(
                ajustes.get("flip_horizontal", self.cfg.effects.flip_horizontal)
            )
            self._load_effects_into_widgets(self.cfg.effects)
        finally:
            self._loading = previo
        self.effects_enabled.setChecked(True)
        self._update_effects_summary()
        self.statusBar().showMessage(f"Preajuste aplicado: {nombre}", 3000)

    def _reset_effects(self) -> None:
        if self.preset_combo.currentIndex() == 0:
            self._on_preset()
        else:
            self.preset_combo.setCurrentIndex(0)

    def _update_effects_summary(self) -> None:
        from ..video.effects import EffectChain

        cadena = EffectChain(self.cfg.effects)
        if not cadena.active:
            self.effects_summary.setText(
                "Ninguno activo: la salida va sin tocar y sin coste añadido."
            )
            return
        self.effects_summary.setText(f"Activos: {cadena.describe()}")

    def _on_live_video(self, _valor: int = 0) -> None:
        if self._loading:
            return
        v = self.cfg.video
        v.transition_ms = int(self.transition.value())
        v.resume_window_ms = int(self.resume_window.value())
        v.loop_mode = self.loop_combo.currentData()
        v.speaking_speed = float(self.speed_speaking.value())
        v.silent_speed = float(self.speed_silent.value())

    def _on_mic_changed(self) -> None:
        if self._loading:
            return
        indice = self.mic_combo.currentData()
        nombre = next((d.name for d in self._devices if d.index == indice), "")
        self.cfg.audio.device = indice
        self.cfg.audio.device_name = nombre
        if self.session.audio.running:
            self.session.audio.set_device(indice, nombre)
            self.statusBar().showMessage("Cambiando de micrófono…", 3000)

    def _on_geometry_changed(self) -> None:
        if self._loading:
            return
        self._collect_into_config()
        self._update_cache_info()
        if self.session.running:
            self.statusBar().showMessage(
                "La resolución, los FPS y el encuadre se aplican al volver a iniciar.",
                6000,
            )

    def _on_virtual_toggled(self) -> None:
        if self._loading:
            return
        activo = self.virtual_check.isChecked()
        self.cfg.output.enabled = activo
        if self.session.engine is not None:
            self.session.engine.set_virtual_output(activo)

    def _on_check_camera(self) -> None:
        from ..output.sink import diagnose_virtual_camera

        self._collect_into_config()
        diag = diagnose_virtual_camera(
            self.cfg.output,
            width=self.cfg.video.width,
            height=self.cfg.video.height,
            fps=self.cfg.video.fps,
        )
        if diag.available:
            QMessageBox.information(self, "Cámara virtual", diag.detail)
        else:
            QMessageBox.warning(self, "Cámara virtual no disponible", diag.detail)

    def _on_list_cameras(self) -> None:
        from ..output.filters import describe_availability

        QMessageBox.information(self, "Cámaras del sistema", describe_availability())

    # ------------------------------------------------------------------ #
    # Arranque y parada
    # ------------------------------------------------------------------ #

    def _on_start(self) -> None:
        self._collect_into_config()
        errores = self.cfg.validate()
        if errores:
            QMessageBox.warning(
                self, "Falta configuración", "\n".join(f"• {e}" for e in errores)
            )
            return

        self.session.cfg = self.cfg
        self.start_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.statusBar().showMessage("Preparando los clips…")
        self._hide_message()

        self._thread = QThread(self)
        self._worker = PrepareWorker(self.session, force=False)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_prepare_progress)
        self._worker.finished.connect(self._on_prepared)
        self._thread.start()

    def _on_prepare_progress(self, hechos: int, total: int, mensaje: str) -> None:
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(hechos)
        self.progress.setFormat(f"{mensaje} — %p%")

    def _on_prepared(self, clips, error: str) -> None:  # noqa: ANN001
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
        self._worker = None
        self.progress.setVisible(False)
        self.start_button.setEnabled(True)

        if clips is None:
            self._show_message(error, "err")
            self.statusBar().showMessage("No se pudo preparar.", 5000)
            return

        detalle = f"{clips.speaking.describe()}\n{clips.silent.describe()}"
        detalle += f"\nCaché total: {clips.total_mb:.0f} MB"
        if clips.warnings:
            detalle += "\n\nAvisos:\n• " + "\n• ".join(clips.warnings)
            self._show_message(clips.warnings[0], "warn")
        self.clip_info.setText(detalle)

        try:
            self.session.start()
        except SessionError as exc:
            self._show_message(str(exc), "err")
            return
        self._set_running(True)
        self.statusBar().showMessage("En marcha.", 4000)

    def _on_stop(self) -> None:
        self.session.stop()
        self._set_running(False)
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Detenido.")
        self._apply_badge("-")
        self.statusBar().showMessage("Detenido.", 4000)

    def _set_running(self, corriendo: bool) -> None:
        self.start_button.setEnabled(not corriendo)
        self.stop_button.setEnabled(corriendo)
        # Lo que invalida la caché de fotogramas no se toca en caliente.
        for widget in (
            self.speaking_button, self.silent_button, self.resolution,
            self.fps_combo, self.fit_combo, self.mirror_check,
        ):
            widget.setEnabled(not corriendo)

    # ------------------------------------------------------------------ #
    # Refresco periódico
    # ------------------------------------------------------------------ #

    def _refresh(self) -> None:
        estado = self.session.status()
        voz, render = estado.voice, estado.render

        self._apply_badge(render.state.value if render.running else "-")
        self.level_meter.setValue(int(max(-60.0, min(0.0, voz.rms_db))))

        if voz.running:
            extra = ""
            if voz.resampled_from and voz.resampled_from != voz.samplerate:
                extra = f" (desde {voz.resampled_from} Hz)"
            self.detector_info.setText(
                f"{voz.backend} a {voz.samplerate} Hz{extra}  ·  "
                f"ruido {voz.noise_floor_db:.0f} dB  ·  SNR {voz.snr_db:+.0f} dB  ·  "
                f"latencia ~{voz.expected_latency_ms} ms"
                + ("  ·  calibrando…" if voz.calibrating else "")
            )
        else:
            self.detector_info.setText("Detector detenido.")

        if render.running:
            self.metrics_label.setText(
                f"FPS {render.fps_effective:5.2f}/{self.cfg.video.fps}   "
                f"ms/frame {render.avg_frame_ms:5.2f} (peor {render.worst_frame_ms:5.2f})   "
                f"frames {render.frames_sent}   tardíos {render.late_frames}   "
                f"cambios {render.transitions}"
            )
            self.virtual_status.setText(
                f"Activa: {render.sink_device}" if render.virtual_output
                else "Cámara virtual inactiva."
            )
        else:
            self.metrics_label.setText("—")

        if self.tray is not None:
            detalle = (
                f"{render.fps_effective:.1f} FPS · {render.sink_device}"
                if render.running and render.virtual_output
                else ""
            )
            self.tray.update_state(
                render.state.value, running=render.running, detail=detalle
            )

        mensaje = render.error or voz.hint or render.warning
        if mensaje:
            self._show_message(mensaje, "err" if (render.error or voz.error) else "warn")
        elif self._last_message:
            self._hide_message()

        self._refresh_preview()

    def _refresh_preview(self) -> None:
        frame = self.session.latest_preview()
        if frame is None:
            return
        alto, ancho = frame.shape[:2]
        imagen = QImage(
            np.ascontiguousarray(frame).data, ancho, alto, 3 * ancho, QImage.Format_BGR888
        )
        pixmap = QPixmap.fromImage(imagen.copy())
        self.preview.setPixmap(
            pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _apply_badge(self, clave: str) -> None:
        texto, estilo = state_badge_style(clave)
        self.state_badge.setText(texto)
        self.state_badge.setStyleSheet(estilo)

    def _show_message(self, texto: str, nivel: str = "warn") -> None:
        if texto == self._last_message:
            return
        self._last_message = texto
        colores = {"err": ("#3a1f22", "#ffb3b8"), "warn": ("#332a12", "#e3b341")}
        fondo, color = colores.get(nivel, colores["warn"])
        self.banner.setStyleSheet(
            f"background:{fondo}; color:{color}; padding:9px; border-radius:6px;"
        )
        self.banner.setText(texto)
        self.banner.setVisible(True)

    def _hide_message(self) -> None:
        self._last_message = ""
        self.banner.setVisible(False)

    # ------------------------------------------------------------------ #

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        # Con la bandeja activa, cerrar la ventana esconde pero no termina: la
        # sesión sigue alimentando la cámara virtual. Para salir de verdad hay
        # que usar «Salir» en el menú del icono.
        if (
            not self._really_quit
            and self.tray is not None
            and self.cfg.ui.minimize_to_tray
        ):
            event.ignore()
            self.hide()
            self.tray.notify(
                "Sigue funcionando en segundo plano",
                "La cámara virtual continúa activa. Usa el icono junto al reloj "
                "para volver o para salir.",
            )
            return

        self.timer.stop()
        self.recorder_tab.shutdown()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        if self.tray is not None:
            self.tray.hide()
        self.session.close()
        super().closeEvent(event)


# --------------------------------------------------------------------------- #


def run_gui(cfg: AppConfig, config_file: Path | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Cámara virtual por voz")
    app.setStyle("Fusion")
    window = MainWindow(cfg, config_file)
    window.show()
    return int(app.exec())
