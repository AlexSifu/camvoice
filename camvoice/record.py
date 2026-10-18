"""Grabación guiada de los dos clips, con temporizador y señales en pantalla.

Existe para que grabar no dependa de otra aplicación y, sobre todo, para que las
condiciones que importan queden impuestas por el propio programa en vez de
confiarse a que el usuario recuerde un documento:

* el **medio segundo inicial con la boca cerrada**, que es el fotograma al que
  se rebobina el clip de habla tras un silencio largo;
* la **duración** dentro del rango útil (8-20 s);
* el **cierre de boca al final** del clip de habla.

El guion es datos, no código: `RecordingPlan` describe las señales con sus
tiempos, así que la interfaz solo tiene que preguntar «¿qué toca ahora?». Eso
permite probar el guion completo sin cámara.

El audio **no se graba**: la aplicación nunca lo usa. Decirlo evita que alguien
busque por qué su clip "no tiene sonido".
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

__all__ = [
    "Cue",
    "RecordingPlan",
    "CameraInfo",
    "list_cameras",
    "RecorderStatus",
    "ClipRecorder",
    "RecordError",
    "SPEAKING_PLAN",
    "SILENT_PLAN",
    "READING_TEXT",
    "READING_TEXT_LONG",
    "split_phrases",
    "MIN_SEGMENT_S",
]


class RecordError(RuntimeError):
    """Fallo de grabación con mensaje apto para el usuario."""


# --------------------------------------------------------------------------- #
# Guion
# --------------------------------------------------------------------------- #

READING_TEXT = (
    "Esta mañana el tiempo parece bastante bueno, aunque por la tarde puede "
    "cambiar. Me faltan algunos papeles para el informe, pero podemos verlo "
    "con calma y confirmar la fecha final."
)
"""Texto de lectura para el clip de habla.

30 palabras, unos 12 s a ritmo normal. Elegido por dos razones y ninguna tiene
que ver con el significado:

1. **Mezcla de formas de boca parecida a la del español hablado.** No hay
   sincronización labial, así que lo único optimizable es que la distribución de
   posiciones de labios se parezca a la del habla real. Grabar diciendo muchas
   «o» y «u» deja la boca redondeada casi todo el tiempo y choca contra un habla
   llena de «a» y «e». Aquí hay 7 cierres bilabiales (p/b/m) y 4 labiodentales
   (f) repartidos, en proporciones cercanas al 8 % y 1 % del español.

2. **No dice nada que importe si alguien lo lee en los labios.** Es relleno de
   oficina deliberadamente anodino.
"""

READING_TEXT_LONG = (
    READING_TEXT
    + " Si te parece, lo dejamos preparado y mañana lo revisamos juntos sin prisa."
)
"""Versión para clips de 20 s."""


MIN_SEGMENT_S = 1.6
"""Tiempo mínimo que una frase permanece en pantalla.

Por debajo de esto no da tiempo a leerla mientras se mira al objetivo. Si el
reparto proporcional dejara frases más cortas, se agrupan de dos en dos.
"""


def split_phrases(texto: str) -> list[str]:
    """Parte un texto por su puntuación, conservando los signos.

    Se corta donde el hablante haría una pausa natural —coma, punto, punto y
    coma— porque son exactamente los puntos donde se puede cambiar lo que hay
    en pantalla sin interrumpir la lectura.
    """
    if not texto.strip():
        return []
    crudas = re.split(r"(?<=[,;.:])\s+", texto.strip())
    frases = [f.strip() for f in crudas if f.strip()]

    # Un fragmento de tres palabras no merece su propio turno en pantalla.
    # Se pega al vecino, pero **nunca cruzando un punto**: unir el final de una
    # oración con el principio de la siguiente produce frases que se leen mal
    # («...la fecha final. Si te parece,»).
    unidas: list[str] = []
    pendiente = ""
    for frase in frases:
        frase = f"{pendiente} {frase}".strip() if pendiente else frase
        pendiente = ""
        if len(frase.split()) >= 4:
            unidas.append(frase)
        elif unidas and not unidas[-1].rstrip().endswith("."):
            unidas[-1] = f"{unidas[-1]} {frase}"
        else:
            # Corto y tras un punto: espera a juntarse con lo que venga.
            pendiente = frase
    if pendiente:
        if unidas:
            unidas[-1] = f"{unidas[-1]} {pendiente}"
        else:
            unidas.append(pendiente)
    return unidas


@dataclass(frozen=True, slots=True)
class Cue:
    """Una señal del guion, con su ventana de tiempo."""

    start_s: float
    end_s: float
    title: str
    detail: str = ""
    critical: bool = False
    """Si es un momento en el que fallar arruina el clip, para destacarlo."""

    def contains(self, t: float) -> bool:
        return self.start_s <= t < self.end_s


@dataclass(frozen=True, slots=True)
class RecordingPlan:
    """Guion completo de un clip."""

    name: str
    kind: str
    """'speaking' | 'silent'."""

    duration_s: float
    lead_in_s: float
    """Tiempo inicial con la boca cerrada y quieto."""

    tail_s: float
    """Tiempo final para cerrar la boca."""

    reading_text: str = ""
    checklist: tuple[str, ...] = ()

    def cues(self) -> list[Cue]:
        """Señales derivadas de los tiempos. Siempre cubren [0, duration_s)."""
        fin_lead = self.lead_in_s
        inicio_cola = max(fin_lead, self.duration_s - self.tail_s)

        apertura = Cue(
            0.0,
            fin_lead,
            "BOCA CERRADA, mira al objetivo",
            "Este es el fotograma al que se vuelve tras cada silencio.",
            critical=True,
        )

        if self.kind == "speaking":
            centro = self._reading_cues(fin_lead, inicio_cola)
            cola = Cue(
                inicio_cola,
                self.duration_s,
                "Termina la frase y CIERRA LA BOCA",
                "No hace falta acabar el texto. Lo que importa es cerrar.",
                critical=True,
            )
        else:
            centro = [
                Cue(
                    fin_lead,
                    inicio_cola,
                    "Escucha. Parpadea con naturalidad",
                    "Boca cerrada, mandíbula relajada. Quieto pero vivo: "
                    "ni inmóvil ni inquieto.",
                )
            ]
            cola = Cue(
                inicio_cola,
                self.duration_s,
                "Sigue igual hasta el final",
                "Sin bostezos, sin estirarte, sin tocarte la cara.",
            )

        return [apertura, *centro, cola]

    def _reading_cues(self, inicio: float, fin: float) -> list[Cue]:
        """Divide el texto en frases y reparte la ventana de lectura entre ellas.

        Mostrar el párrafo entero de golpe no sirve: hay que buscar por dónde se
        iba mientras se habla a cámara. Aquí sale **una frase cada vez**, con el
        tiempo proporcional a su longitud, de modo que el ritmo de la pantalla
        marque el ritmo de lectura.

        El reparto es por número de caracteres, que para un texto en un solo
        idioma y sin cifras es un proxy suficientemente bueno de la duración
        hablada. No hace falta más: lo que importa es que ninguna frase se quede
        corta de tiempo, no clavar la sílaba.
        """
        ventana = max(0.0, fin - inicio)
        frases = split_phrases(self.reading_text)
        if not frases or ventana <= 0:
            return [
                Cue(inicio, fin, "Lee en voz alta, a ritmo normal", self.reading_text)
            ]

        # Con poco tiempo, menos frases pero legibles: se agrupan hasta que cada
        # una tenga al menos MIN_SEGMENT_S. Mejor leer tres frases con calma que
        # ver seis pasar sin poder seguirlas.
        while len(frases) > 1 and ventana / len(frases) < MIN_SEGMENT_S:
            frases = [
                " ".join(frases[i : i + 2]) for i in range(0, len(frases), 2)
            ]

        total = sum(len(f) for f in frases) or 1
        cues: list[Cue] = []
        t = inicio
        for indice, frase in enumerate(frases, start=1):
            ultimo = indice == len(frases)
            t_fin = fin if ultimo else t + ventana * len(frase) / total
            cues.append(
                Cue(
                    t,
                    t_fin,
                    f"Lee en voz alta  ·  {indice} de {len(frases)}",
                    frase,
                )
            )
            t = t_fin
        return cues

    def reading_phrases(self) -> list[str]:
        """Frases del texto de lectura, en el orden en que se mostrarán."""
        return [
            c.detail
            for c in self.cues()
            if c.title.startswith("Lee en voz alta")
        ]

    def next_cue_at(self, t: float) -> Cue | None:
        """Señal siguiente a la activa, para poder anticiparla en pantalla."""
        cues = self.cues()
        for actual, siguiente in zip(cues, cues[1:]):
            if actual.contains(t):
                return siguiente
        return None

    def cue_at(self, t: float) -> Cue:
        """Señal activa en el instante `t`."""
        for cue in self.cues():
            if cue.contains(t):
                return cue
        return self.cues()[-1]

    def with_duration(self, duration_s: float) -> RecordingPlan:
        """Mismo guion con otra duración, reajustando el texto de lectura."""
        duration_s = max(4.0, min(60.0, float(duration_s)))
        texto = self.reading_text
        if self.kind == "speaking":
            texto = READING_TEXT_LONG if duration_s >= 18.0 else READING_TEXT
        return replace(self, duration_s=duration_s, reading_text=texto)


SPEAKING_PLAN = RecordingPlan(
    name="Clip HABLANDO",
    kind="speaking",
    duration_s=15.0,
    lead_in_s=0.6,
    tail_s=1.5,
    reading_text=READING_TEXT,
    checklist=(
        "Boca cerrada al empezar",
        "Ritmo normal, sin exagerar",
        "Manos fuera del plano",
        "Sin contar números",
        "Mirada al objetivo",
    ),
)

SILENT_PLAN = RecordingPlan(
    name="Clip EN SILENCIO",
    kind="silent",
    duration_s=15.0,
    lead_in_s=0.6,
    tail_s=1.0,
    checklist=(
        "Boca cerrada todo el clip",
        "Parpadeo natural",
        "Nada que se vea raro reproducido al revés",
        "Sin tocarte la cara ni el pelo",
        "Misma posición que en el otro clip",
    ),
)


# --------------------------------------------------------------------------- #
# Cámaras físicas
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CameraInfo:
    index: int
    name: str
    width: int
    height: int
    fps: float

    @property
    def label(self) -> str:
        return f"[{self.index}] {self.name} — {self.width}x{self.height} @ {self.fps:.0f} FPS"


VIRTUAL_CAMERA_HINTS = ("camvoice", "unity video capture", "obs virtual")
"""Nombres que NO se ofrecen como fuente de grabación.

Grabar desde nuestra propia cámara virtual crearía un bucle: la aplicación
emitiría lo que está grabando de sí misma. Hay que excluirlas explícitamente
porque Windows no distingue una webcam de un filtro virtual, y de hecho esa
indistinguibilidad es lo que hace que el resto del proyecto funcione.
"""


def _is_virtual(name: str) -> bool:
    low = name.lower()
    return any(pista in low for pista in VIRTUAL_CAMERA_HINTS)


STATIC_PROBE_DELAY_S = 0.25
"""Separación entre los dos fotogramas de la prueba de imagen estática."""


def _looks_static(cap) -> bool:  # noqa: ANN001
    """True si dos fotogramas separados en el tiempo son idénticos bit a bit.

    Es la forma fiable de reconocer una cámara virtual sin nadie alimentándola:
    emite un fotograma de relleno congelado. **Un sensor real nunca entrega dos
    fotogramas idénticos**, porque siempre hay ruido de lectura; incluso con la
    tapa puesta, el último bit cambia.

    Hizo falta porque el camino obvio —cruzar el índice de OpenCV con el nombre
    del registro de Windows— no funciona: la clave `Instance` del registro solo
    contiene los filtros **software**, no las webcams físicas, así que el
    emparejamiento por posición mezclaba unas con otras. Medido en este equipo:
    una webcam real salía etiquetada como «Cámara 3» y una virtual podría
    haberse ofrecido como fuente, creando un bucle.
    """
    ok_a, a = cap.read()
    if not ok_a or a is None:
        return True
    time.sleep(STATIC_PROBE_DELAY_S)
    ok_b, b = cap.read()
    if not ok_b or b is None:
        return True
    return np.array_equal(a, b)


def list_cameras(max_index: int = 6) -> list[CameraInfo]:
    """Cámaras físicas disponibles para grabar.

    DirectShow no ofrece una lista por índice, así que hay que abrir cada uno y
    ver si responde. Se usa CAP_DSHOW porque Media Foundation tarda varios
    segundos por dispositivo en Windows.

    Se descartan las fuentes que entregan imagen estática: son cámaras
    virtuales sin productor, incluida la de esta propia aplicación. Grabar desde
    ella crearía un bucle.
    """
    nombres = physical_camera_names()
    encontradas: list[CameraInfo] = []

    for index in range(max_index):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if not cap.isOpened():
                continue
            if _looks_static(cap):
                continue
            ancho = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
            alto = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            # Los nombres del sistema se ofrecen como pista, no como garantía:
            # su orden no tiene por qué coincidir con el de los índices.
            nombre = (
                nombres[len(encontradas)]
                if len(encontradas) < len(nombres)
                else f"Cámara {index}"
            )
            if _is_virtual(nombre):
                continue
            encontradas.append(
                CameraInfo(index, nombre, ancho, alto, fps if 1 <= fps <= 240 else 30.0)
            )
        finally:
            cap.release()
    return encontradas


def physical_camera_names() -> list[str]:
    """Nombres de las cámaras físicas según el administrador de dispositivos.

    Solo para etiquetar: el orden no tiene por qué coincidir con los índices de
    DirectShow, así que la lista se usa como pista y la exclusión de cámaras
    virtuales se decide con `_looks_static`, que no depende de nombres.
    """
    import subprocess

    consulta = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' } | "
        "Where-Object { $_.Status -eq 'OK' } | "
        "Select-Object -ExpandProperty Name"
    )
    try:
        salida = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", consulta],
            capture_output=True, text=True, timeout=12,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if salida.returncode != 0:
        return []
    return [
        linea.strip()
        for linea in salida.stdout.splitlines()
        if linea.strip() and not _is_virtual(linea)
    ]


# --------------------------------------------------------------------------- #
# Grabador
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RecorderStatus:
    phase: str = "idle"
    """'idle' | 'preview' | 'countdown' | 'recording' | 'done' | 'error'."""

    elapsed_s: float = 0.0
    remaining_s: float = 0.0
    countdown_s: float = 0.0
    frames: int = 0
    cue_title: str = ""
    cue_detail: str = ""
    cue_critical: bool = False
    output_path: str = ""
    error: str = ""
    camera_label: str = ""
    measured_fps: float = 0.0

    @property
    def active(self) -> bool:
        return self.phase in ("preview", "countdown", "recording")


class ClipRecorder:
    """Graba un clip desde una cámara física siguiendo un guion.

    Corre en su propio hilo: abre la cámara, muestra vista previa, cuenta atrás,
    graba exactamente `plan.duration_s` y para solo. La interfaz lee
    `status()` y `latest_frame()`, igual que hace con el motor de render.
    """

    COUNTDOWN_S = 3.0
    PREVIEW_SETTLE_S = 0.4
    """Fotogramas iniciales que se descartan: muchas webcams entregan los
    primeros muy oscuros mientras ajustan exposición y balance de blancos."""

    def __init__(
        self,
        camera: CameraInfo,
        plan: RecordingPlan,
        output_path: Path,
        *,
        countdown_s: float | None = None,
    ) -> None:
        self.camera = camera
        self.plan = plan
        self.output_path = Path(output_path)
        self.countdown_s = self.COUNTDOWN_S if countdown_s is None else max(0.0, countdown_s)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._status_lock = threading.Lock()
        self._status = RecorderStatus(camera_label=camera.label)
        self._frame_lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._measured_input_fps = 0.0

    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="camvoice-recorder", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)

    def status(self) -> RecorderStatus:
        with self._status_lock:
            return self._status

    def latest_frame(self) -> np.ndarray | None:
        with self._frame_lock:
            return None if self._frame is None else self._frame.copy()

    # ------------------------------------------------------------------ #

    def _publish(self, **cambios) -> None:
        with self._status_lock:
            self._status = replace(self._status, **cambios)

    def _set_frame(self, frame: np.ndarray) -> None:
        with self._frame_lock:
            self._frame = frame

    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        cap = cv2.VideoCapture(self.camera.index, cv2.CAP_DSHOW)
        writer = None
        try:
            if not cap.isOpened():
                raise RecordError(
                    f"No se pudo abrir «{self.camera.name}». Puede estar en uso por "
                    "otra aplicación: cierra Teams, Zoom o el navegador y reintenta."
                )
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera.height)

            self._settle(cap)
            if self._stop.is_set():
                return
            if not self._countdown(cap):
                return

            writer = self._open_writer()
            self._record(cap, writer)
        except RecordError as exc:
            self._publish(phase="error", error=str(exc))
        except Exception as exc:  # pragma: no cover - defensivo
            self._publish(phase="error", error=f"Fallo inesperado al grabar: {exc}")
        finally:
            if writer is not None:
                writer.release()
            cap.release()

    MIN_MEASURE_S = 1.0
    """Tiempo mínimo de observación antes de fiarse de la cadencia medida."""

    def _settle(self, cap) -> None:  # noqa: ANN001
        """Descarta los primeros fotogramas mientras la cámara se estabiliza."""
        self._publish(phase="preview", cue_title="Colócate en el encuadre")
        fin = time.perf_counter() + self.PREVIEW_SETTLE_S
        while time.perf_counter() < fin and not self._stop.is_set():
            ok, frame = cap.read()
            if ok and frame is not None:
                self._set_frame(frame)

    def _countdown(self, cap) -> bool:  # noqa: ANN001
        """Cuenta atrás mostrando vista previa. False si se cancela.

        Aprovecha para **medir la cadencia real** de la cámara, que es lo que
        luego se escribe en el archivo.
        """
        inicio = time.perf_counter()
        primera = self.plan.cues()[0]
        leidos = 0
        while True:
            if self._stop.is_set():
                return False
            transcurrido = time.perf_counter() - inicio
            restante = self.countdown_s - transcurrido
            if restante <= 0 and transcurrido >= self.MIN_MEASURE_S:
                break
            ok, frame = cap.read()
            if ok and frame is not None:
                self._set_frame(frame)
                leidos += 1
            self._publish(
                phase="countdown",
                countdown_s=max(0.0, restante),
                cue_title=primera.title,
                cue_detail=primera.detail,
                cue_critical=True,
            )

        transcurrido = time.perf_counter() - inicio
        if leidos >= 5 and transcurrido > 0:
            self._measured_input_fps = leidos / transcurrido
            self._publish(measured_fps=self._measured_input_fps)
        return True

    def _writer_fps(self) -> float:
        """Cadencia que se escribe en el archivo.

        **Tiene que ser la medida, no la que declara la cámara.** Medido en este
        equipo: una webcam que declara 30 FPS entregaba 10 con poca luz —baja la
        cadencia para exponer más tiempo—. Escribiendo 30 en la cabecera, un
        clip de 5 s quedaba de 1,7 s y se reproducía al triple de velocidad, con
        la boca moviéndose de forma absurda.

        El paso de preparación remuestrea usando los FPS **declarados** en el
        archivo, así que una cabecera mentirosa se arrastra hasta la salida.
        """
        medida = getattr(self, "_measured_input_fps", 0.0)
        if 5.0 <= medida <= 120.0:
            return float(medida)
        return float(self.camera.fps)

    def _open_writer(self):  # noqa: ANN201
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(
            str(self.output_path),
            fourcc,
            self._writer_fps(),
            (self.camera.width, self.camera.height),
        )
        if not writer.isOpened():
            raise RecordError(
                f"No se pudo crear «{self.output_path.name}». Comprueba que la "
                "carpeta existe y que hay espacio en disco."
            )
        return writer

    def _record(self, cap, writer) -> None:  # noqa: ANN001
        """Graba durante `plan.duration_s` de reloj y para.

        El corte se decide por **tiempo transcurrido**, no por número de
        fotogramas: una webcam que declara 30 FPS puede entregar 24 con poca
        luz, y contar fotogramas daría un clip más largo de lo pedido y con el
        cierre de boca fuera de plano.
        """
        duracion = self.plan.duration_s
        inicio = time.perf_counter()
        fotogramas = 0

        while not self._stop.is_set():
            transcurrido = time.perf_counter() - inicio
            if transcurrido >= duracion:
                break

            ok, frame = cap.read()
            if not ok or frame is None:
                raise RecordError(
                    "La cámara dejó de entregar imagen a mitad de la grabación."
                )
            writer.write(frame)
            fotogramas += 1
            self._set_frame(frame)

            cue = self.plan.cue_at(transcurrido)
            self._publish(
                phase="recording",
                elapsed_s=transcurrido,
                remaining_s=max(0.0, duracion - transcurrido),
                frames=fotogramas,
                cue_title=cue.title,
                cue_detail=cue.detail,
                cue_critical=cue.critical,
            )

        if self._stop.is_set():
            self._publish(phase="idle", cue_title="Cancelado")
            return

        real = time.perf_counter() - inicio
        self._publish(
            phase="done",
            elapsed_s=real,
            remaining_s=0.0,
            frames=fotogramas,
            output_path=str(self.output_path),
            measured_fps=fotogramas / real if real > 0 else 0.0,
            cue_title="Grabación terminada",
            cue_detail=f"{fotogramas} fotogramas en {real:.1f} s",
            cue_critical=False,
        )


# --------------------------------------------------------------------------- #
# Superposición de guías
# --------------------------------------------------------------------------- #


def draw_framing_guides(bgr: np.ndarray) -> np.ndarray:
    """Dibuja las guías de encuadre sobre una copia del fotograma.

    Tres referencias, que son las que importan para este uso: el eje central
    para centrar la cara, la línea de ojos a un tercio de la altura, y el margen
    de cabeza. Sin ellas la gente se encuadra demasiado abajo y sale medio
    cuerpo con mucho techo.
    """
    salida = bgr.copy()
    h, w = salida.shape[:2]
    color = (90, 200, 120)
    tenue = (70, 90, 80)

    # Eje vertical central.
    cv2.line(salida, (w // 2, 0), (w // 2, h), tenue, 1)

    # Línea de ojos a un tercio desde arriba.
    y_ojos = h // 3
    cv2.line(salida, (0, y_ojos), (w, y_ojos), color, 1)
    cv2.putText(
        salida, "ojos", (8, y_ojos - 6), cv2.FONT_HERSHEY_SIMPLEX,
        0.45, color, 1, cv2.LINE_AA,
    )

    # Marco de cabeza y hombros: un 60 % del ancho, centrado.
    mx = int(w * 0.20)
    cv2.rectangle(salida, (mx, int(h * 0.08)), (w - mx, h), tenue, 1)
    return salida
