"""Director: voz (estado, micro-pausa) y gestos -> fotograma de salida.

Sustituye al compositor clásico (composer.py, que se conserva como modo
`classic`) con una máquina de estados explícita y determinista::

    SILENT ──voz──> TRANSITION ──> SPEAKING
       ^  \\                          |  (micro-pausa / silencio)
       |   └─gesto─> TRANSITION ─> GESTURE ──fin/cancelar──> TRANSITION ─> (según la voz ACTUAL)
       └──────────── silencio ───────┘
    cualquier estado ──pausa manual──> VIDEO_PAUSED ──reanudar──> TRANSITION ─> (según la voz)
    cualquier estado ──fallo de lectura──> DEGRADED ──> TRANSITION ─> SILENT

Prioridades (de mayor a menor) y reglas de cancelación, evaluadas una vez por
fotograma en este orden:

1. **VIDEO_PAUSED** (pausa manual). Congela la última imagen válida; cancela
   el gesto en curso y los pendientes, descarta cambios programados. Las
   peticiones de gesto se rechazan mientras dura. Al reanudar se funde desde
   la imagen congelada hasta un fotograma ancla del estado que pida la voz.
2. **Cancelar gesto**. Interrumpe el gesto (o la transición hacia él) con un
   fundido corto inmediato hacia el grupo que pida la voz.
3. **GESTURE en curso**. No se interrumpe por la voz; las nuevas peticiones
   quedan en una cola de longitud 1 (la más reciente sustituye a la anterior).
4. **Gesto pendiente**. En silencio espera a un ancla (≤ `anchor_wait_ms`).
   Hablando usa la variante `<gesto>_hablando` si existe; si no, espera como
   mucho `SPEAKING_GESTURE_WAIT_MS` (170 ms) a una micro-pausa, un ancla o el
   silencio y después transiciona igual (nunca se rechaza ni se aplaza).
5. **Voz** (SILENT / SPEAKING; la micro-pausa cuenta como silencio). Un
   cambio de grupo pendiente se anula si la voz vuelve antes de ejecutarse.
6. **Rotación de segmento** (variedad, fin de clip). La menor prioridad.

Continuidad (por qué no hay saltos):

- **Reproducción estrictamente consecutiva.** En SILENT, SPEAKING y GESTURE
  cada fotograma es el siguiente del mismo clip (`pos + 1`). Nunca se salta
  dentro de un clip para perseguir la boca: eso producía tirones visibles.
- **Cambios planificados por pares (salida, entrada).** Al programar un cambio
  de grupo o de segmento se evalúan, de una vez, los fotogramas de salida que
  caben en el plazo (`anchor_wait_ms`, 150-200 ms) contra las entradas
  precalculadas del destino, y se elige el par de menor coste. Si hay un par
  *compatible*, el cambio se ejecuta en ese fotograma de salida con un único
  fotograma mezclado (o corte limpio). Si no lo hay, al vencer el plazo se
  hace un **corte seco** a la entrada menos defectuosa: nunca se mezclan dos
  caras incompatibles (eso era la doble imagen).
- **Compatibilidad en unidades interpretables (índice v3).** El coste de un par
  es el peor de sus términos normalizados: diferencia media de luma en la zona
  de la cara y en la del torso (niveles de gris, sobre miniaturas 32x24 sin
  normalizar), desplazamiento y cambio de escala de la cabeza (en anchos de
  cara), brillo medio y, al pasar de habla a silencio, apertura de boca del
  fotograma de salida. Un par es compatible si todos los términos están dentro
  de su umbral (coste ≤ `COST_THRESHOLD`); si la cabeza no es fiable en uno de
  los dos lados, el par se considera incompatible (no se «supone»). La elección
  es determinista: orden estable por coste, desempate por orden de clip y
  posición; el azar queda solo para la duración de los segmentos y la
  alternancia de clips.
- **Rotación aplazable.** Un cambio de segmento del mismo grupo sin par
  compatible se aplaza (se vuelve a intentar unos fotogramas después) salvo que
  el clip esté acabándose; nunca aplaza un cambio pedido por la voz ni un gesto.
- **Coste de discontinuidad.** Cada transición registra su coste
  (`DirectorStats.entry_costs`); por encima de `COST_THRESHOLD` cuenta como
  transición de riesgo visible (`high_cost_transitions`) y se ejecuta como
  corte, no como fundido.
- **Nunca se funde desde una imagen compuesta.** Si una transición se
  interrumpe (cancelar gesto a mitad de fundido, pausa), la siguiente entrada
  es un corte a fotogramas reales. Desde una imagen congelada (pausa manual)
  solo se funde si el fotograma congelado procede de un clip indexado y es
  compatible con la entrada.

Es puro respecto al tiempo: el reloj se inyecta (`clock`) y las esperas se
cuentan en fotogramas, así las pruebas simulan horas de reunión en segundos y
el resultado no depende de la carga de la máquina. No hay sincronía labial:
la boca no sigue fonemas (ver docs/DISENO_CAMVOICE_TIEMPO_REAL.md).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

from ..audio.gate import VoiceState
from ..config import GesturesConfig, VideoConfig
from ..gestures import CANCEL, SPEAKING_SUFFIX, gesture_label, speaking_variant
from .composer import ClipSource
from .index import DESC_H, DESC_W, HEAD_STEP, ClipIndex, descriptor, distances, luma, thumbnail

__all__ = [
    "DirectorState",
    "VoiceInput",
    "DirectorClip",
    "DirectorInfo",
    "DirectorStats",
    "GestureResult",
    "Director",
]

_log = logging.getLogger(__name__)


class DirectorState(Enum):
    SILENT = "SILENT"
    SPEAKING = "SPEAKING"
    TRANSITION = "TRANSITION"
    GESTURE = "GESTURE"
    VIDEO_PAUSED = "VIDEO_PAUSED"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class VoiceInput:
    state: VoiceState = VoiceState.SILENT
    mouth: float = 0.0
    """Apertura de boca estimada 0..1 (audio/features.py). Informativa: el
    director ya no salta dentro de los clips para perseguirla."""

    pause: bool = False
    """Micro-pausa dentro del habla (audio/features.MicroPauseDetector): se
    trata como silencio (boca cerrada, pose de escucha) sin que la puerta de
    voz haya salido de SPEAKING."""


@dataclass
class DirectorClip:
    name: str
    frames: object
    """ClipCache, lista de fotogramas o ClipSource."""

    index: ClipIndex
    manual_anchors: tuple[int, ...] = ()
    """Anclas marcadas a mano (sustituyen a las automáticas si hay alguna válida)."""

    def __post_init__(self) -> None:
        if not isinstance(self.frames, ClipSource):
            self.frames = ClipSource(self.frames)
        if len(self.frames) != self.index.frame_count:
            raise ValueError(
                f"El índice de «{self.name}» no corresponde al clip "
                f"({self.index.frame_count} != {len(self.frames)} fotogramas)."
            )

    def __len__(self) -> int:
        return len(self.frames)


@dataclass(frozen=True, slots=True)
class DirectorInfo:
    state: DirectorState
    target: VoiceState
    progress: float
    clip: str
    frame_index: int
    blended: bool
    morph: bool
    gesture: str
    pending_gesture: str
    jumped: bool
    """Siempre False: se conserva por compatibilidad (ya no hay saltos)."""

    message: str = ""
    paused: bool = False
    anchor: bool = False
    """True si la transición en curso es un par compatible (un fotograma
    mezclado); False si es un corte seco."""


@dataclass(frozen=True, slots=True)
class GestureResult:
    accepted: bool
    message: str


@dataclass
class _Playhead:
    clip: DirectorClip
    kind: str  # "silent" | "speaking" | "gesture"
    pos: int
    """Último fotograma mostrado. El siguiente es `pos + 1`."""

    frames_in_clip: int = 0


@dataclass
class _Morph:
    a: np.ndarray
    b: np.ndarray
    planes: list  # [(map_x, map_y, flow_x, flow_y, slicer, shape)]


@dataclass
class _Transition:
    dst: _Playhead
    then: DirectorState
    steps: int
    src: _Playhead | None = None
    src_frame: np.ndarray | None = None
    morph: _Morph | None = None
    step: int = 0
    gesture: str = ""
    anchor: bool = False


@dataclass
class _Change:
    """Cambio programado; se ejecuta en un ancla o al vencer `deadline`."""

    kind: str  # "silent" | "speaking" | "gesture"
    deadline: int
    """Fotograma (contador interno) en el que se ejecuta aunque no haya ancla."""

    reason: str  # "voice" | "rotate" | "gesture"
    gesture: str = ""
    t_req: float = 0.0
    need_pause: bool = False
    """Gesto pedido hablando sin variante: espera a micro-pausa o silencio."""

    tick_req: int = 0
    prefer_other: bool = False
    """Rotación: se prefiere otro clip del grupo (decidido una vez al programar)."""

    plan: "_Plan | None" = None
    """Par (salida, entrada) compatible encontrado dentro del plazo; None si
    no lo hay (al vencer el plazo se corta a la mejor entrada disponible)."""


@dataclass(frozen=True, slots=True)
class _Plan:
    tick: int
    """Contador interno en el que el fotograma siguiente es la salida elegida."""

    clip: DirectorClip
    pos: int
    cost: float


@dataclass
class _Option:
    """Entrada candidata en un clip destino."""

    key: tuple
    """Clave de orden determinista: (coste + penalización de repetir, suma de
    términos, orden del clip en el grupo, posición)."""

    clip: DirectorClip
    pos: int
    cost: float
    """Coste visual (sin penalización de repetir), en unidades de COST_THRESHOLD."""

    ok: bool
    """Par compatible: todos los términos dentro de su umbral y cabeza fiable."""


@dataclass
class _Anchors:
    mask: np.ndarray
    """(n,) bool: fotogramas ancla (boca cerrada, pose cercana a la neutra)."""

    entries: np.ndarray
    """(m,) int32: anclas en las que se puede entrar (con cola suficiente)."""

    desc: np.ndarray
    """(m, D) descriptores de `entries` (copia contigua)."""

    head: np.ndarray
    """(m, 3) cabeza (cx, cy, ancho) de `entries`; NaN si no se detectó."""

    bright: np.ndarray
    """(m,) brillo medio de `entries`."""

    manual: bool = False
    thumb: np.ndarray | None = None
    """(m, DESC_H*DESC_W) int16: miniaturas de luma de `entries` (índice v3)."""

    head_ok: np.ndarray | None = None
    """(m,) bool: cabeza fiable (detección Haar cercana) en cada entrada."""

    face_idx: np.ndarray | None = None
    """Índices planos de la miniatura que cubren la cara (según la cabeza
    mediana del clip)."""

    body_idx: np.ndarray | None = None
    """Índices planos de la miniatura bajo la cara (torso, hombros, manos)."""


@dataclass
class DirectorStats:
    transitions: int = 0
    morphs: int = 0
    jumps: int = 0
    """Compatibilidad: saltos no consecutivos fuera de transición. Siempre 0."""

    gestures_played: int = 0
    gestures_rejected: int = 0
    degraded_events: int = 0
    gesture_latencies_ms: deque = field(default_factory=lambda: deque(maxlen=100))
    segment_changes: int = 0
    anchor_transitions: int = 0
    fallback_transitions: int = 0
    interrupted_transitions: int = 0
    discontinuities: int = 0
    """Fotogramas no consecutivos fuera de una transición (debe ser 0)."""

    pauses: int = 0
    speaking_variants: int = 0
    gesture_waits_ms: deque = field(default_factory=lambda: deque(maxlen=100))
    """Espera petición -> inicio del gesto cuando se pidió hablando."""

    entry_costs: deque = field(default_factory=lambda: deque(maxlen=5000))
    """Coste de cada entrada elegida: COST_THRESHOLD x el peor término
    normalizado (cara, torso, cabeza, brillo, boca); ≤ COST_THRESHOLD = par
    compatible."""

    high_cost_transitions: int = 0
    """Transiciones con coste > Director.COST_THRESHOLD: par incompatible,
    ejecutadas como corte seco (sin mezclar dos caras)."""

    auto_shots: int = 0
    """Frases breves (#6, #7, #8) lanzadas solas por el director."""

    auto_shots_cut: int = 0
    """Frases breves cortadas con fundido porque callaste antes de su final."""


def _smoothstep(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


class Director:
    W_REUSE = 0.8
    MORPH_RATIO = 2.0
    D_OK_FLOOR = 0.03
    """Clips casi estáticos dan un d_ok minúsculo; este suelo evita que cualquier
    diferencia mínima se trate como un cambio de pose enorme."""
    MIN_TAIL_S = 1.0
    """No se entra en los últimos segundos de un clip: no habría a dónde ir."""
    SILENT_DWELL_S = (8.0, 22.0)
    SPEAKING_DWELL_S = (5.0, 12.0)
    HISTORY = 16
    SEGMENT_S = 2.0
    MAX_ANCHORS = 96
    SPEAKING_GESTURE_WAIT_MS = 170.0
    """Espera máxima a una micro-pausa o a un ancla para un gesto pedido
    hablando (150-180 ms). Pasado ese tiempo el gesto entra con un fundido
    corto; al terminar se vuelve al habla."""
    AUTO_SHOT_ONSET_P = 0.35
    """Probabilidad de abrir una intervención con una frase breve (#7, #8)."""
    AUTO_SHOT_CLOSING_P = 0.35
    """Probabilidad de usar el cierre (#6) en un cambio de segmento de una
    intervención larga."""
    AUTO_SHOT_COOLDOWN_S = 20.0
    LONG_SPEECH_S = 8.0
    AUTO_SHOT_FINISH_S = 0.25
    """Si callas durante una frase breve y le queda menos que esto, se termina
    (su final ya cierra la boca); si le queda más, fundido corto al silencio."""
    CLOSING_SHOTS = ("terminar_explicacion",)
    SILENT_SWITCH_P = 0.7
    """En silencios largos, probabilidad de cambiar de clip (#1 <-> #3) al rotar."""
    SPEAKING_SWITCH_P = 0.6
    COST_THRESHOLD = 4.0
    """Coste de un par (salida, entrada) = COST_THRESHOLD x el peor de sus
    términos normalizados. Por debajo o igual, el par es compatible (un
    fotograma mezclado); por encima, se corta sin mezclar. Se conserva el valor
    4 por compatibilidad con los informes (trials, soak)."""
    # Umbrales de compatibilidad (un término normalizado vale 1.0 en el umbral).
    # Calibrados sobre los clips reales del clipcache (miniaturas 32x24):
    # fotogramas consecutivos: cara 0,2-1,9 / torso 0,1-0,2 niveles; mejores
    # pares #3->#5: cara ~4,4-5,4 / torso ~1,7-2,3; #2->#1: cara 2-3 / torso 1,2;
    # #5->#1 (cabeza en movimiento, boca abierta): cara mediana ~10,7, torso
    # 3,5-4,7, desplazamiento de cabeza mediano 0,035 anchos de cara (p90 0,098).
    FACE_MAD_OK = 6.0
    """Diferencia media de luma (niveles 0..255) en la zona de la cara."""
    BODY_MAD_OK = 3.5
    """Diferencia media de luma bajo la cara (torso, hombros, manos)."""
    HEAD_POS_OK = 0.06
    """Desplazamiento del centro de la cara, en anchos de cara (~10 px a 640)."""
    HEAD_SCALE_OK = 0.06
    """Cambio del ancho de la cara, relativo al ancho de la cara."""
    LUMA_OK = 6.0
    """Diferencia de brillo medio del fotograma (niveles 0..255)."""
    MOUTH_OK = 0.45
    """Apertura de boca (relativa al clip, 0..1) del fotograma de salida al
    pasar de habla a silencio."""
    HEAD_UNRELIABLE = 1.5
    """Término de cabeza cuando no es fiable en alguno de los dos lados: el par
    no puede declararse compatible (1,5 > 1), pero sigue siendo ordenable."""
    HEAD_MAX_GAP = 2 * HEAD_STEP
    """La cabeza interpolada a más de estos fotogramas de una detección real
    no es fiable."""
    DESC_OK_RATIO = 2.0
    """Sin miniaturas (índice sin v3 en memoria): distancia de descriptores en
    unidades de d_ok a partir de la cual el par deja de ser compatible."""
    ROTATE_RETRY_FRAMES = 6
    """Rotación aplazada por falta de par compatible: fotogramas hasta volver a
    intentarlo (y ventana de salidas que se evalúa en cada intento)."""
    NEUTRAL_QUANTILE = 0.95
    NEUTRAL_MARGIN = 2.0
    """Pose neutra absoluta: un ancla de los bucles debe estar a una distancia
    del fotograma 0 de la referencia (#1) no mayor que NEUTRAL_MARGIN veces el
    cuantil NEUTRAL_QUANTILE de la propia variación de la referencia."""
    MIN_NEUTRAL_ENTRIES = 3
    """Si un clip deja menos anclas que esto con el criterio neutro, se conserva
    su criterio relativo (para no quedarse sin anclas)."""

    def __init__(
        self,
        cfg: VideoConfig,
        silent: Sequence[DirectorClip],
        speaking: Sequence[DirectorClip],
        gestures: dict[str, DirectorClip] | None = None,
        *,
        gestures_cfg: GesturesConfig | None = None,
        clock: Callable[[], float] = time.perf_counter,
        seed: int | None = None,
        speaking_actions: Iterable[str] = (),
    ) -> None:
        if not silent or not speaking:
            raise ValueError("Hacen falta al menos un clip de silencio y uno de habla.")
        shapes = {c.frames.frame(0).shape for c in [*silent, *speaking, *(gestures or {}).values()]}
        if len(shapes) != 1:
            raise ValueError(f"Todos los clips deben tener la misma geometría: {shapes}")
        self.cfg = cfg
        self.gcfg = gestures_cfg or GesturesConfig()
        self.pools: dict[str, list[DirectorClip]] = {"silent": list(silent), "speaking": list(speaking)}
        self.gestures: dict[str, DirectorClip] = dict(gestures or {})
        self.speaking_actions: frozenset[str] = frozenset(speaking_actions)
        """Frases a petición (SPEAKING_ONE_SHOT): son habla, no esperan micro-pausa."""
        self.clock = clock
        seed = cfg.seed if seed is None else seed
        self.rng = random.Random(seed)
        self.stats = DirectorStats()
        self.fps = max(1, int(cfg.fps))
        self._shape = next(iter(shapes))

        wait_ms = min(200.0, max(150.0, float(getattr(cfg, "anchor_wait_ms", 180))))
        self._wait_frames = max(1, self._steps_for_ms(wait_ms))
        self._max_steps = max(1, self._steps_for_ms(self._max_transition_ms()))
        fallback_ms = min(200.0, max(150.0, float(getattr(cfg, "fallback_fade_ms", 200))))
        self._fallback_steps = max(2, self._steps_for_ms(fallback_ms))
        self._fallback_steps = min(self._fallback_steps, max(2, self._max_steps))
        self._lookahead = self._wait_frames + self._max_steps + 1

        # Anclas precalculadas (una vez, fuera del bucle de render).
        self._ref = self.pools["silent"][0].index.descriptors[0].astype(np.float32)
        ref_clip = self.pools["silent"][0]
        d_ref = distances(
            np.asarray(ref_clip.index.descriptors[: len(ref_clip)], dtype=np.float32), self._ref
        )
        self._neutral_d = self.NEUTRAL_MARGIN * max(
            float(np.quantile(d_ref, self.NEUTRAL_QUANTILE)) if d_ref.size else 0.0, self.D_OK_FLOOR
        )
        self._last_entry_cost = 0.0
        self._last_entry_ok = False
        self._rotate_retry = 0
        self._frozen_ref: tuple[DirectorClip, int] | None = None
        """(clip, pos) del fotograma congelado en la pausa manual, si procede de
        un clip indexado (permite medir su compatibilidad al reanudar)."""
        self._anchors: dict[int, _Anchors] = {}
        for kind, pool in self.pools.items():
            for clip in pool:
                self._anchors[id(clip)] = self._build_anchors(clip, kind)
        for clip in self.gestures.values():
            if id(clip) not in self._anchors:
                self._anchors[id(clip)] = self._gesture_anchors(clip)

        self.state = DirectorState.SILENT
        self.target = VoiceState.SILENT
        first = self.pools["silent"][0]
        start = int(self._anchors[id(first)].entries[0]) if len(self._anchors[id(first)].entries) else 0
        self.cur = _Playhead(first, "silent", start - 1)
        self._tr: _Transition | None = None
        self._change: _Change | None = None
        self._history: deque[tuple[str, int]] = deque(maxlen=self.HISTORY)
        self._dwell = self._new_dwell("silent")
        self._tick = 0
        self._buffer: np.ndarray | None = None
        self._last_good = np.zeros(self._shape, dtype=np.uint8)
        self._have_good = False
        self._last_key: tuple[int, int] | None = None
        self._message = ""

        self._req_lock = threading.Lock()
        self._pending: tuple[str, float] | None = None
        self._cancel = False
        self._pause_flag = False
        self._active_gesture = ""
        self._gesture_t_req = 0.0
        self._latency_pending = False
        self.last_gesture_latency_ms: float | None = None
        self._last_gesture_end: dict[str, float] = {}
        self._next_auto_nod = self._schedule_auto_nod(self.clock())
        self._dis = None
        # Frases breves automáticas (SPEAKING_ONE_SHOT importadas).
        shots = sorted(n for n in self.speaking_actions if n in self.gestures)
        self._closing_shots = [n for n in shots if n in self.CLOSING_SHOTS]
        self._onset_shots = [n for n in shots if n not in self.CLOSING_SHOTS]
        self._auto_shot = False
        self._last_auto_shot = -1e9
        self._last_auto_name = ""
        self._speech_t0: float | None = None

    # ------------------------------------------------------------------ #
    # API pública (otros hilos)
    # ------------------------------------------------------------------ #

    def available_gestures(self) -> list[str]:
        """Gestos que se pueden pedir (sin las variantes `_hablando`)."""
        return sorted(n for n in self.gestures if not n.endswith(SPEAKING_SUFFIX))

    def request_gesture(self, name: str) -> GestureResult:
        """Pide un gesto. Seguro desde cualquier hilo; no bloquea el render."""
        if name == CANCEL:
            self.cancel_gesture()
            return GestureResult(True, "Gesto cancelado.")
        if self._pause_flag:
            self.stats.gestures_rejected += 1
            return GestureResult(False, "El video está en pausa; reanúdalo para hacer gestos.")
        if name not in self.gestures or name.endswith(SPEAKING_SUFFIX):
            from .library import missing_gesture_message

            self.stats.gestures_rejected += 1
            return GestureResult(False, missing_gesture_message(name))
        now = self.clock()
        last_end = self._last_gesture_end.get(name)
        if last_end is not None and (now - last_end) * 1000.0 < self.gcfg.cooldown_ms:
            self.stats.gestures_rejected += 1
            return GestureResult(False, f"«{gesture_label(name)}» acaba de terminar; espera un momento.")
        with self._req_lock:
            replaced = self._pending is not None
            self._pending = (name, now)
            self._cancel = False
        extra = " (sustituye al pendiente)" if replaced else ""
        return GestureResult(True, f"Gesto «{gesture_label(name)}» en cola{extra}.")

    def cancel_gesture(self) -> None:
        with self._req_lock:
            self._pending = None
            self._cancel = True

    def pause_video(self) -> None:
        """Pausa manual: congela la última imagen válida (nunca negro)."""
        with self._req_lock:
            self._pause_flag = True

    def resume_video(self) -> None:
        with self._req_lock:
            self._pause_flag = False

    def toggle_video_pause(self) -> bool:
        """Alterna la pausa manual. Devuelve True si queda en pausa."""
        with self._req_lock:
            self._pause_flag = not self._pause_flag
            return self._pause_flag

    @property
    def video_paused(self) -> bool:
        return self._pause_flag

    @property
    def transitions(self) -> int:
        return self.stats.transitions

    # ------------------------------------------------------------------ #
    # Paso principal (hilo de render)
    # ------------------------------------------------------------------ #

    def step(self, voice: VoiceInput | VoiceState) -> tuple[np.ndarray, DirectorInfo]:
        if isinstance(voice, VoiceState):
            voice = VoiceInput(voice, 1.0 if voice is VoiceState.SPEAKING else 0.0)
        self.target = voice.state
        self._tick += 1
        if self._desired_kind(voice) == "speaking":
            if self._speech_t0 is None:
                self._speech_t0 = self.clock()
        else:
            self._speech_t0 = None
        blended = morph = False
        progress = 0.0
        try:
            frame = self._handle_pause(voice)
            if frame is None:
                self._handle_requests(voice)
                self._auto_nod(voice)
                if self.state is DirectorState.DEGRADED:
                    self._recover()
                if self.state is not DirectorState.TRANSITION:
                    self._advance(voice)
                if self.state is DirectorState.TRANSITION:
                    frame, blended, morph, progress = self._step_transition(voice)
                else:
                    frame = self._frame(self.cur)
                    self._check_continuity()
                np.copyto(self._last_good, frame)
                self._have_good = True
                if self._latency_pending and self._showing_gesture():
                    self._latency_pending = False
                    ms = (self.clock() - self._gesture_t_req) * 1000.0
                    self.last_gesture_latency_ms = ms
                    self.stats.gesture_latencies_ms.append(ms)
        except _ClipFault as fault:
            frame = self._degrade(fault)
            blended = morph = False
            progress = 0.0
        except Exception as exc:  # noqa: BLE001 - un fallo de lógica no debe parar el render
            frame = self._logic_fault(exc)
            blended = morph = False
            progress = 0.0

        tr = self._tr
        info = DirectorInfo(
            state=self.state,
            target=self.target,
            progress=progress,
            clip=self.cur.clip.name,
            frame_index=self.cur.pos,
            blended=blended,
            morph=morph,
            gesture=self._active_gesture,
            pending_gesture=self._pending_name(),
            jumped=False,
            message=self._message,
            paused=self.state is DirectorState.VIDEO_PAUSED,
            anchor=bool(tr is not None and tr.anchor),
        )
        return frame, info

    def _pending_name(self) -> str:
        pending = self._pending
        if pending is not None:
            return pending[0]
        ch = self._change
        return ch.gesture if ch is not None and ch.kind == "gesture" else ""

    def _check_continuity(self) -> None:
        """Cuenta fotogramas no consecutivos fuera de transición (garantía)."""
        key = (id(self.cur.clip), self.cur.pos)
        last = self._last_key
        if last is not None and last[0] == key[0] and key[1] != last[1] + 1:
            self.stats.discontinuities += 1
        elif last is not None and last[0] != key[0]:
            self.stats.discontinuities += 1
        self._last_key = key

    # ------------------------------------------------------------------ #
    # Pausa manual
    # ------------------------------------------------------------------ #

    def _handle_pause(self, voice: VoiceInput) -> np.ndarray | None:
        """Devuelve la imagen congelada mientras dura la pausa; None si no."""
        with self._req_lock:
            want = self._pause_flag
            if want:
                self._pending = None
                self._cancel = False
        if want and self.state is not DirectorState.VIDEO_PAUSED:
            if not self._have_good:
                # Aún no se ha emitido nada: se congela el fotograma actual.
                ph = self._tr.dst if self._tr is not None else self.cur
                np.copyto(self._last_good, self._frame(_Playhead(ph.clip, ph.kind, max(0, ph.pos))))
                self._have_good = True
            # Solo un fotograma real (no una mezcla) sirve de referencia medible.
            self._frozen_ref = (
                (self.cur.clip, max(0, self.cur.pos))
                if self._tr is None and self.state is not DirectorState.DEGRADED
                else None
            )
            if self._active_gesture:
                self._release_gesture(cancelled=True)
            self._change = None
            self._tr = None
            self.state = DirectorState.VIDEO_PAUSED
            self.stats.pauses += 1
        if self.state is DirectorState.VIDEO_PAUSED:
            if want:
                return self._last_good
            # Reanudar: fundido suave desde la imagen congelada a un ancla solo
            # si el fotograma congelado es compatible con ella; si no, corte.
            kind = self._desired_kind(voice)
            ref, self._frozen_ref = self._frozen_ref, None
            self._enter_from_frozen(kind, min_steps=3, anchor=True, ref=ref)
        return None

    # ------------------------------------------------------------------ #
    # Peticiones de gesto
    # ------------------------------------------------------------------ #

    def _showing_gesture(self) -> bool:
        if self.state is DirectorState.GESTURE:
            return True
        return self._tr is not None and self._tr.dst.kind == "gesture" and self._tr.step > 0

    def _gesture_active(self) -> bool:
        return self.state is DirectorState.GESTURE or (self._tr is not None and self._tr.dst.kind == "gesture")

    def _handle_requests(self, voice: VoiceInput) -> None:
        with self._req_lock:
            cancel, self._cancel = self._cancel, False
            pending = None
            takes = self.state in (DirectorState.SILENT, DirectorState.SPEAKING) or (
                self._auto_shot and self.state is DirectorState.GESTURE
            )
            if self._pending is not None and takes:
                pending, self._pending = self._pending, None
        if cancel:
            if self._change is not None and self._change.kind == "gesture":
                self._change = None
            if self._gesture_active():
                self._end_gesture(voice, cancelled=True)
                return
        if pending is not None:
            name, t_req = pending
            if name not in self.gestures:
                return
            if self._auto_shot and self.state is DirectorState.GESTURE:
                # Una frase automática nunca retrasa un gesto pedido: se
                # sustituye ya, con fundido corto.
                self._release_gesture(cancelled=True)
                self._active_gesture = name
                self._gesture_t_req = t_req
                self._latency_pending = True
                self._start_gesture(name, voice, t_req=t_req, tick_req=self._tick)
                return
            speaking = self._desired_kind(voice) == "speaking"
            has_variant = speaking_variant(name) in self.gestures
            need_pause = speaking and not has_variant and name not in self.speaking_actions
            wait = self._steps_for_ms(self.SPEAKING_GESTURE_WAIT_MS) if need_pause else self._wait_frames
            self._change = _Change(
                "gesture",
                self._tick + max(1, wait) - 1,
                "gesture",
                gesture=name,
                t_req=t_req,
                need_pause=need_pause,
                tick_req=self._tick,
            )
            self._active_gesture = name
            self._gesture_t_req = t_req
            self._latency_pending = True
            self._plan(self._change, voice)

    def _release_gesture(self, *, cancelled: bool) -> None:
        name = self._active_gesture
        self._active_gesture = ""
        self._latency_pending = False
        if self._auto_shot:
            # Frase automática: no cuenta como gesto pedido ni activa la espera
            # entre repeticiones de los atajos.
            self._auto_shot = False
            return
        if name:
            self._last_gesture_end[name] = self.clock()
            if not cancelled:
                self.stats.gestures_played += 1

    def _end_gesture(self, voice: VoiceInput, *, cancelled: bool = False, early: bool = False) -> None:
        """Fin (o cancelación) del gesto: siguiente gesto en cola o vuelta a la voz.

        `early`: una frase automática se corta antes de su final (callaste):
        no es pose neutra, así que se sale con el fundido de reserva."""
        self._release_gesture(cancelled=cancelled)
        with self._req_lock:
            pending, self._pending = self._pending, None
        if pending is not None and pending[0] in self.gestures and not cancelled:
            name, t_req = pending
            self._active_gesture = name
            self._gesture_t_req = t_req
            self._latency_pending = True
            self._start_gesture(name, voice, t_req=t_req, tick_req=self._tick)
            return
        if pending is not None:
            with self._req_lock:
                if self._pending is None:
                    self._pending = pending
        kind = self._desired_kind(voice)
        if self.state is DirectorState.GESTURE and not cancelled:
            # Final natural: se mide la compatibilidad del último fotograma del
            # gesto con la mejor entrada; si no es compatible, corte (sin
            # mezclar la mano o la cabeza del gesto con el clip de destino).
            self._execute_change(_Change(kind, self._tick, "voice"), anchor=not early)
        else:
            # Cancelación: la imagen mostrada es un fotograma real del gesto
            # (medible) o una mezcla en curso (nunca se funde desde ella).
            ref = (
                (self.cur.clip, max(0, self.cur.pos))
                if self.state is DirectorState.GESTURE and self._tr is None
                else None
            )
            self._enter_from_frozen(kind, min_steps=self._fallback_steps, anchor=False, ref=ref)

    def _start_gesture(self, name: str, voice: VoiceInput, *, t_req: float, tick_req: int,
                       anchor: bool = False) -> None:
        speaking = self._desired_kind(voice) == "speaking"
        variant = speaking_variant(name)
        clip = self.gestures[variant] if speaking and variant in self.gestures else self.gestures[name]
        if clip is not self.gestures[name]:
            self.stats.speaking_variants += 1
        if speaking and not self._auto_shot:
            self.stats.gesture_waits_ms.append((self._tick - tick_req) * 1000.0 / self.fps)
        dst = _Playhead(clip, "gesture", -1)
        moving = self.state in (DirectorState.SILENT, DirectorState.SPEAKING, DirectorState.GESTURE)
        if moving and self._tr is None:
            src = _Playhead(self.cur.clip, self.cur.kind, self.cur.pos)
            q = min(src.pos + 1, len(src.clip) - 1)
            a_desc = self._desc_at(src.clip, q)
            # Mismo criterio de continuidad que un cambio de grupo: solo se
            # mezcla un fotograma si la pose de salida es compatible con el
            # principio del gesto; si no, corte. El gesto no se retrasa.
            opt = self._options_for_clip(clip, a_desc, self._src_query(src.clip, src.kind, q, "gesture"))
            self._record_cost(opt.cost if opt is not None else float("inf"))
            # La medida manda (`anchor` del plan coincide con ella por construcción).
            self._start_transition(dst, DirectorState.GESTURE, src=src, a_desc=a_desc,
                                   b_pos=0, anchor=self._last_entry_ok, gesture=name)
        else:
            # Imagen mostrada sin procedencia medible (mezcla, recuperación): corte.
            self._start_transition(dst, DirectorState.GESTURE, src_frame=self._last_good.copy(),
                                   a_desc=self._query(), b_pos=0, anchor=False, gesture=name)

    def _try_auto_shot(self, closing: bool, voice: VoiceInput, *, anchor: bool) -> bool:
        """Lanza una frase breve automática (#7/#8 al empezar a hablar, #6 como
        cierre en intervenciones largas). Devuelve True si la lanzó."""
        names = self._closing_shots if closing else self._onset_shots
        if not names or not getattr(self.gcfg, "auto_speaking_shots", False):
            return False
        if self._pending is not None or self._active_gesture:
            return False
        now = self.clock()
        if now - self._last_auto_shot < self.AUTO_SHOT_COOLDOWN_S:
            return False
        p = self.AUTO_SHOT_CLOSING_P if closing else self.AUTO_SHOT_ONSET_P
        if self.rng.random() >= p:
            return False
        options = [n for n in names if n != self._last_auto_name] or names
        name = self.rng.choice(options)
        self._auto_shot = True
        self._active_gesture = name
        self._latency_pending = False
        self._last_auto_shot = now
        self._last_auto_name = name
        self.stats.auto_shots += 1
        self._start_gesture(name, voice, t_req=now, tick_req=self._tick, anchor=anchor)
        return True

    def _schedule_auto_nod(self, now: float) -> float:
        lo, hi = self.gcfg.auto_nod_min_s, self.gcfg.auto_nod_max_s
        return now + self.rng.uniform(lo, max(lo, hi))

    def _auto_nod(self, voice: VoiceInput) -> None:
        if not self.gcfg.auto_nod or self.gcfg.auto_nod_gesture not in self.gestures:
            return
        now = self.clock()
        if self.state is not DirectorState.SILENT or voice.state is VoiceState.SPEAKING:
            # Solo cuando llevas un rato callado: se reprograma mientras hablas.
            self._next_auto_nod = max(self._next_auto_nod, now + self.gcfg.auto_nod_min_s / 2)
            return
        if now >= self._next_auto_nod and self._pending is None and self._change is None:
            self.request_gesture(self.gcfg.auto_nod_gesture)
            self._next_auto_nod = self._schedule_auto_nod(now)

    # ------------------------------------------------------------------ #
    # Avance en estados estables (siempre consecutivo)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _desired_kind(voice: VoiceInput) -> str:
        # El grupo sigue al estado estable de SpeechGate. La micro-pausa no
        # cambia de grupo: solo sirve de momento preferente para un gesto ya
        # pedido (ver `_advance`).
        return "speaking" if voice.state is VoiceState.SPEAKING else "silent"

    def _new_dwell(self, kind: str) -> int:
        lo, hi = self.SPEAKING_DWELL_S if kind == "speaking" else self.SILENT_DWELL_S
        return int(self.rng.uniform(lo, hi) * self.fps)

    def _advance(self, voice: VoiceInput) -> None:
        cur = self.cur
        n = len(cur.clip)
        nxt = cur.pos + 1

        if self.state is DirectorState.GESTURE:
            tail = max(1, self._max_steps // 2)
            if nxt >= n - tail:
                self._end_gesture(voice)
                return
            if (
                self._auto_shot
                and self._desired_kind(voice) == "silent"
                and n - nxt > self._steps_for_s(self.AUTO_SHOT_FINISH_S)
            ):
                # Callaste antes del final de la frase: fundido corto al silencio.
                self.stats.auto_shots_cut += 1
                self._end_gesture(voice, early=True)
                return
            cur.pos = nxt
            cur.frames_in_clip += 1
            return

        desired = self._desired_kind(voice)
        ch = self._change
        if ch is None or ch.kind != "gesture":
            if cur.kind != desired:
                if ch is None or ch.kind != desired:
                    # Cambio pedido por la voz: nunca se aplaza más allá del plazo.
                    self._change = _Change(desired, self._tick + self._wait_frames - 1, "voice")
                    self._plan(self._change, voice)
            elif ch is not None and ch.reason == "voice":
                self._change = None  # la voz volvió antes de ejecutar el cambio
            near_end = nxt >= n - 1 - self._lookahead
            if (
                self._change is None
                and (near_end or cur.frames_in_clip >= self._dwell)
                and self._tick >= self._rotate_retry
            ):
                rot = _Change(cur.kind, self._tick + self._wait_frames - 1, "rotate")
                rot.prefer_other = (
                    len(self.pools[cur.kind]) > 1 and self.rng.random() < (
                        self.SILENT_SWITCH_P if cur.kind == "silent" else self.SPEAKING_SWITCH_P
                    )
                )
                window = self._wait_frames if near_end else max(self._wait_frames, self.ROTATE_RETRY_FRAMES)
                self._plan(rot, voice, window=window)
                if rot.plan is None and not near_end:
                    # Sin par compatible: la rotación se aplaza (el clip sigue
                    # en marcha, sin corte) y se reintenta más adelante.
                    self._rotate_retry = self._tick + self.ROTATE_RETRY_FRAMES
                else:
                    if rot.plan is not None:
                        rot.deadline = max(rot.deadline, rot.plan.tick)
                    self._change = rot

        ch = self._change
        if ch is not None:
            # «anchor» = en este fotograma se ejecuta el par compatible planificado.
            anchor = ch.plan is not None and ch.plan.tick == self._tick
            if ch.kind == "gesture" and ch.need_pause:
                # Micro-pausa, par compatible o fin de la espera (≤ 180 ms).
                ready = desired != "speaking" or voice.pause or anchor or self._tick >= ch.deadline
            else:
                ready = anchor or self._tick >= ch.deadline
            if ready or nxt >= n - 1:
                self._change = None
                if ch.kind == "gesture":
                    if ch.gesture in self.gestures:
                        self._start_gesture(ch.gesture, voice, t_req=ch.t_req, tick_req=ch.tick_req,
                                            anchor=anchor)
                        return
                    self._release_gesture(cancelled=True)
                else:
                    long_speech = (
                        self._speech_t0 is not None
                        and self.clock() - self._speech_t0 >= self.LONG_SPEECH_S
                    )
                    if ch.kind == "speaking" and cur.kind == "silent" and ch.reason == "voice":
                        if self._try_auto_shot(False, voice, anchor=anchor):
                            return
                    elif ch.kind == "speaking" and ch.reason == "rotate" and long_speech:
                        if self._try_auto_shot(True, voice, anchor=anchor):
                            return
                    self._execute_change(ch, anchor=anchor)
                    return

        cur.pos = min(nxt, n - 1)
        cur.frames_in_clip += 1

    def _execute_change(self, ch: _Change, *, anchor: bool) -> None:
        """Ejecuta un cambio de segmento o de grupo desde el clip en marcha.

        Con `anchor` (par planificado para este fotograma) se usa la entrada
        del plan y se mezcla un solo fotograma. Sin él (plazo vencido, fin de
        clip o fin de gesto sin par compatible) se elige la entrada menos
        defectuosa desde el fotograma actual y se corta sin mezclar."""
        cur = self.cur
        src = _Playhead(cur.clip, cur.kind, cur.pos)
        q = min(cur.pos + 1, len(cur.clip) - 1)
        a_desc = self._desc_at(cur.clip, q)
        rotate = ch.reason == "rotate"
        plan = ch.plan
        if anchor and plan is not None and plan.tick == self._tick:
            clip, pos = plan.clip, plan.pos
            self._remember(clip.name, pos)
            self._record_cost(plan.cost)
        else:
            exclude = self._exclude_for(ch)
            clip, pos = self._choose_entry(
                ch.kind, a_desc, exclude=exclude, prefer_other=ch.prefer_other,
                **self._src_query(cur.clip, cur.kind, q, ch.kind),
            )
            # Solo se mezcla si el par medido es compatible (p. ej. final natural
            # de un gesto que termina en la pose neutra); si no, corte seco.
            anchor = self._last_entry_ok
        if rotate:
            self.stats.segment_changes += 1
        then = DirectorState.SPEAKING if ch.kind == "speaking" else DirectorState.SILENT
        self._dwell = self._new_dwell(ch.kind)
        self._start_transition(_Playhead(clip, ch.kind, pos - 1), then, src=src, a_desc=a_desc,
                               b_pos=pos, anchor=anchor)

    def _enter_from_frozen(
        self,
        kind: str,
        *,
        min_steps: int,
        anchor: bool,
        ref: tuple[DirectorClip, int] | None = None,
    ) -> None:
        """Entra en un grupo desde la imagen mostrada (congelada o mezcla).

        Solo se funde si la imagen es un fotograma real de un clip indexado
        (`ref`) y resulta compatible con la entrada elegida; una mezcla, una
        imagen de procedencia desconocida o un par incompatible producen un
        corte seco a fotogramas reales (nunca doble cara ni negro)."""
        query = self._query()
        if ref is not None and self._tr is None:
            rclip, rpos = ref
            rpos = max(0, min(len(rclip) - 1, int(rpos)))
            params = self._src_query(rclip, self._kind_of(rclip), rpos, kind)
        else:
            # Sin procedencia medible: cabeza no fiable -> nunca compatible -> corte.
            y = luma(self._last_good, self.cfg.pixel_format, self.cfg.height)
            params = dict(q_thumb=thumbnail(y), q_head=None, q_head_ok=False,
                          q_luma=float(cv2.mean(y)[0]), q_mouth=None, d_ok=None)
        clip, pos = self._choose_entry(kind, query, **params)
        anchor = self._last_entry_ok  # `anchor` pedido solo si el par lo permite
        then = DirectorState.SPEAKING if kind == "speaking" else DirectorState.SILENT
        self._dwell = self._new_dwell(kind)
        self._start_transition(_Playhead(clip, kind, pos - 1), then, src_frame=self._last_good.copy(),
                               a_desc=query, b_pos=pos, anchor=anchor, min_steps=min_steps)

    # ------------------------------------------------------------------ #
    # Anclas y elección de entrada
    # ------------------------------------------------------------------ #

    def _build_anchors(self, clip: DirectorClip, kind: str) -> _Anchors:
        idx = clip.index
        n = len(clip)
        hi = n - self._steps_for_s(self.MIN_TAIL_S) - self._lookahead
        hi = max(1, min(n - 1, hi))
        mouth = np.asarray(idx.mouth[:n], dtype=np.float32)
        d = distances(np.asarray(idx.descriptors[:n], dtype=np.float32), self._ref)
        if kind == "silent":
            mouth_ok = mouth <= max(0.35, float(np.quantile(mouth, 0.6)))
        else:
            mouth_ok = mouth <= max(0.2, float(np.quantile(mouth, 0.3)))
        pose_ok = d <= max(float(np.quantile(d[:hi], 0.6)), d.min() + 2.0 * self.D_OK_FLOOR)
        mask = mouth_ok & pose_ok
        # Además de «lo más neutro de este clip», exigir pose cercana a la
        # neutra absoluta (#1), si el clip deja suficientes anclas así.
        neutral = mask & (d <= self._neutral_d)
        if int(np.count_nonzero(neutral[:hi])) >= self.MIN_NEUTRAL_ENTRIES:
            mask = neutral
        manual = sorted({int(a) for a in clip.manual_anchors if 0 <= int(a) < hi})
        if manual:  # marcadas a mano: mandan sobre el detector
            mask = np.zeros(n, dtype=bool)
            mask[manual] = True
        entries = np.flatnonzero(mask[:hi])
        if entries.size == 0:
            score = d[:hi] / max(float(d[:hi].max()), 1e-6) + mouth[:hi]
            entries = np.sort(np.argsort(score)[: max(1, hi // 10)])
            mask = mask.copy()
            mask[entries] = True
        if entries.size > self.MAX_ANCHORS:
            pick = np.linspace(0, entries.size - 1, self.MAX_ANCHORS).round().astype(int)
            entries = entries[pick]
        return self._anchors_for(clip, mask, entries.astype(np.int32), manual=bool(manual))

    def _gesture_anchors(self, clip: DirectorClip) -> _Anchors:
        """Un gesto solo se entra por su fotograma 0; su principio y su final
        son la pose neutra (anclas)."""
        n = len(clip)
        mask = np.zeros(n, dtype=bool)
        mask[[0, -1]] = True
        return self._anchors_for(clip, mask, np.zeros(1, dtype=np.int32))

    def _anchors_for(self, clip: DirectorClip, mask: np.ndarray, entries: np.ndarray, *,
                     manual: bool = False) -> _Anchors:
        idx = clip.index
        desc = np.ascontiguousarray(np.asarray(idx.descriptors, dtype=np.float32)[entries])
        head = np.ascontiguousarray(idx.head[entries], dtype=np.float32)
        bright = np.ascontiguousarray(idx.brightness[entries], dtype=np.float32)
        gaps = idx.head_gap[entries] if idx.head_gap is not None else np.zeros(entries.size)
        head_ok = np.isfinite(head).all(axis=1) & (np.asarray(gaps) <= self.HEAD_MAX_GAP)
        thumb = None
        if idx.thumbs is not None:
            thumb = np.ascontiguousarray(idx.thumbs[entries].reshape(entries.size, -1).astype(np.int16))
        # Zonas de cara y torso en la miniatura según la cabeza mediana del
        # clip (las cabezas de los clips grabados varían < 0,5 px de miniatura);
        # sin cabeza fiable, la zona de la guía de encuadre.
        finite = idx.head[np.isfinite(idx.head[:, 0])]
        med = np.median(finite, axis=0) if finite.shape[0] else np.array([0.5, 0.4, 0.27], np.float32)
        face_idx, body_idx = self._region_idx(med)
        return _Anchors(mask=mask, entries=entries, desc=desc, head=head, bright=bright, manual=manual,
                        thumb=thumb, head_ok=head_ok, face_idx=face_idx, body_idx=body_idx)

    def _region_idx(self, head: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Índices planos (en la miniatura DESC_H x DESC_W) de la zona de la cara
        y de la zona inferior (torso) para una cabeza (cx, cy, ancho)."""
        cx, cy, w = (float(v) for v in head[:3])
        w = max(0.05, min(0.9, w))
        aspect = self.cfg.width / max(1, self.cfg.height)
        hx = 0.6 * w
        hy = 0.6 * w * aspect * 1.15
        x0 = max(0, int(np.floor((cx - hx) * DESC_W)))
        x1 = min(DESC_W, int(np.ceil((cx + hx) * DESC_W)))
        y0 = max(0, int(np.floor((cy - hy) * DESC_H)))
        y1 = min(DESC_H, int(np.ceil((cy + hy) * DESC_H)))
        x1, y1 = max(x1, x0 + 2), max(y1, y0 + 2)
        grid = np.arange(DESC_H * DESC_W).reshape(DESC_H, DESC_W)
        face = grid[y0:y1, x0:x1].ravel()
        body = grid[min(y1, DESC_H - 2):, :].ravel()
        return face.astype(np.intp), body.astype(np.intp)

    def _anchor_mask(self, clip: DirectorClip) -> np.ndarray:
        a = self._anchors.get(id(clip))
        if a is None:  # gestos: su principio y su final son la pose neutra
            m = np.zeros(len(clip), dtype=bool)
            m[[0, -1]] = True
            return m
        return a.mask

    def _kind_of(self, clip: DirectorClip) -> str:
        for kind, pool in self.pools.items():
            if clip in pool:
                return kind
        return "gesture"

    def _segment(self, pos: int) -> int:
        return pos // max(1, self._steps_for_s(self.SEGMENT_S))

    def _remember(self, clip: str, pos: int) -> None:
        self._history.append((clip, self._segment(pos)))

    def _reuse_penalty(self, clip: str, pos: int) -> float:
        seg = (clip, self._segment(pos))
        return self.W_REUSE * sum(1 for h in self._history if h == seg) / 2.0

    def _desc_at(self, clip: DirectorClip, pos: int) -> np.ndarray:
        pos = max(0, min(len(clip) - 1, pos))
        return np.asarray(clip.index.descriptors[pos], dtype=np.float32)

    def _query(self) -> np.ndarray:
        """Descriptor de la imagen mostrada (solo para imágenes sin índice:
        congelada, mezcla o recuperación). Un 32x24 de luma: < 0,1 ms."""
        return descriptor(luma(self._last_good, self.cfg.pixel_format, self.cfg.height))

    def _src_query(self, clip: DirectorClip, src_kind: str, q: int, dst_kind: str) -> dict:
        """Parámetros de consulta del fotograma `q` de un clip indexado."""
        idx = clip.index
        q = max(0, min(len(clip) - 1, q))
        gap = float(idx.head_gap[q]) if idx.head_gap is not None else 0.0
        head = np.asarray(idx.head[q], dtype=np.float32)
        return dict(
            q_thumb=idx.thumbs[q] if idx.thumbs is not None else None,
            q_head=head,
            q_head_ok=bool(np.isfinite(head).all() and gap <= self.HEAD_MAX_GAP),
            q_luma=float(idx.brightness[q]),
            q_mouth=float(idx.mouth[q]) if (src_kind == "speaking" and dst_kind == "silent") else None,
            d_ok=float(idx.d_ok),
        )

    def _exclude_for(self, ch: _Change) -> tuple[DirectorClip, int] | None:
        cur = self.cur
        if (ch.reason == "rotate" or cur.kind == ch.kind) and cur.kind != "gesture":
            return (cur.clip, cur.pos)
        return None

    def _record_cost(self, cost: float) -> None:
        """Registra el coste del par elegido y si es compatible."""
        self._last_entry_cost = float(cost)
        self._last_entry_ok = bool(np.isfinite(cost) and cost <= self.COST_THRESHOLD)
        if not np.isfinite(cost):
            return  # sin candidatas medibles: no se contabiliza (como antes)
        self.stats.entry_costs.append(round(float(cost), 3))
        if not self._last_entry_ok:
            self.stats.high_cost_transitions += 1

    def _choose_entry(
        self,
        kind: str,
        query: np.ndarray,
        *,
        exclude: tuple[DirectorClip, int] | None = None,
        prefer_other: bool = False,
        q_head: np.ndarray | None = None,
        q_luma: float | None = None,
        q_thumb: np.ndarray | None = None,
        q_head_ok: bool = True,
        q_mouth: float | None = None,
        d_ok: float | None = None,
    ) -> tuple[DirectorClip, int]:
        """Entrada del grupo `kind` de menor coste respecto al fotograma de
        consulta (cara, torso, cabeza, brillo, boca). Determinista: orden
        estable por coste y desempate por clip y posición. Registra el coste y
        si el par es compatible (`_last_entry_ok`)."""
        pool = self.pools[kind]
        options = self._entry_options(
            kind, query, exclude=exclude, prefer_other=prefer_other, q_head=q_head,
            q_luma=q_luma, q_thumb=q_thumb, q_head_ok=q_head_ok, q_mouth=q_mouth, d_ok=d_ok,
        )
        if not options:  # p. ej. un único clip muy corto: cualquier ancla vale
            clip = pool[0]
            a = self._anchors[id(clip)]
            self._record_cost(float("inf"))
            return clip, int(a.entries[0]) if a.entries.size else 0
        best = options[0]
        self._remember(best.clip.name, best.pos)
        self._record_cost(best.cost)
        return best.clip, best.pos

    def _entry_options(
        self,
        kind: str,
        query: np.ndarray,
        *,
        exclude: tuple[DirectorClip, int] | None = None,
        prefer_other: bool = False,
        q_head: np.ndarray | None = None,
        q_luma: float | None = None,
        q_thumb: np.ndarray | None = None,
        q_head_ok: bool = True,
        q_mouth: float | None = None,
        d_ok: float | None = None,
    ) -> list[_Option]:
        """Candidatas de entrada en el grupo `kind`, ordenadas (determinista)."""
        pool = self.pools.get(kind, [])
        seg_frames = self._steps_for_s(self.SEGMENT_S)
        options: list[_Option] = []
        for ci, clip in enumerate(pool):
            if prefer_other and exclude is not None and clip is exclude[0] and len(pool) > 1:
                continue
            a = self._anchors[id(clip)]
            if a.entries.size == 0:
                continue
            cost, total, ok = self._pair_costs(
                a, query, q_thumb=q_thumb, q_head=q_head, q_head_ok=q_head_ok,
                q_luma=q_luma, q_mouth=q_mouth, d_ok=d_ok,
            )
            for k in np.argsort(cost, kind="stable")[:8]:
                pos = int(a.entries[k])
                if exclude is not None and clip is exclude[0] and abs(pos - exclude[1]) < seg_frames:
                    continue
                c = float(cost[k])
                options.append(_Option(
                    key=(c + self._reuse_penalty(clip.name, pos), float(total[k]), ci, pos),
                    clip=clip, pos=pos, cost=c, ok=bool(ok[k]),
                ))
        options.sort(key=lambda o: o.key)
        return options

    def _options_for_clip(self, clip: DirectorClip, query: np.ndarray, params: dict) -> _Option | None:
        """Mejor entrada de un clip concreto (gestos: su fotograma 0) respecto
        al fotograma de consulta (`query`: descriptor; `params`: _src_query)."""
        a = self._anchors.get(id(clip))
        if a is None or a.entries.size == 0:
            return None
        cost, total, ok = self._pair_costs(
            a, query, q_thumb=params.get("q_thumb"), q_head=params.get("q_head"),
            q_head_ok=bool(params.get("q_head_ok", False)), q_luma=params.get("q_luma"),
            q_mouth=params.get("q_mouth"), d_ok=params.get("d_ok"),
        )
        k = int(np.argmin(cost))
        pos = int(a.entries[k])
        return _Option(key=(float(cost[k]), float(total[k]), 0, pos), clip=clip, pos=pos,
                       cost=float(cost[k]), ok=bool(ok[k]))

    def _pair_costs(
        self,
        a: _Anchors,
        query: np.ndarray,
        *,
        q_thumb: np.ndarray | None,
        q_head: np.ndarray | None,
        q_head_ok: bool,
        q_luma: float | None,
        q_mouth: float | None,
        d_ok: float | None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Coste de cada entrada de `a` respecto al fotograma de consulta.

        Devuelve (coste, suma de términos, compatible). Cada término vale 1,0
        en su umbral; coste = COST_THRESHOLD x el peor término, así «coste ≤
        COST_THRESHOLD» equivale a «todos los términos dentro de su umbral».
        Vectorizado sobre las entradas del clip (≤ MAX_ANCHORS): ~ decenas de
        microsegundos por clip."""
        m = a.entries.size
        terms: list[np.ndarray] = []
        if q_thumb is not None and a.thumb is not None and a.face_idx is not None:
            diff = np.abs(a.thumb - np.asarray(q_thumb, dtype=np.int16).ravel()[None, :])
            terms.append(diff[:, a.face_idx].mean(axis=1) / self.FACE_MAD_OK)
            terms.append(diff[:, a.body_idx].mean(axis=1) / self.BODY_MAD_OK)
        else:  # sin miniaturas: distancia global de descriptores en unidades de d_ok
            scale = max(float(d_ok) if d_ok else self.D_OK_FLOOR, self.D_OK_FLOOR)
            terms.append(distances(a.desc, np.asarray(query, dtype=np.float32)) / scale / self.DESC_OK_RATIO)
        head_term = np.full(m, self.HEAD_UNRELIABLE, dtype=np.float32)
        if q_head is not None and q_head_ok and a.head_ok is not None:
            qh = np.asarray(q_head, dtype=np.float32)
            if np.isfinite(qh).all() and qh[2] > 1e-3:
                fw = float(qh[2])
                aspect = self.cfg.height / max(1, self.cfg.width)
                dx = a.head[:, 0] - qh[0]
                dy = (a.head[:, 1] - qh[1]) * aspect
                dpos = np.hypot(dx, dy) / fw / self.HEAD_POS_OK
                dsc = np.abs(a.head[:, 2] - qh[2]) / fw / self.HEAD_SCALE_OK
                ht = np.maximum(dpos, dsc)
                head_term = np.where(a.head_ok & np.isfinite(ht), ht, self.HEAD_UNRELIABLE).astype(np.float32)
        terms.append(head_term)
        if q_luma is not None and q_luma > 0 and float(a.bright.max(initial=0.0)) > 0:
            terms.append(np.abs(a.bright - float(q_luma)) / self.LUMA_OK)
        if q_mouth is not None:
            terms.append(np.full(m, float(q_mouth) / self.MOUTH_OK, dtype=np.float32))
        t = np.stack([np.asarray(x, dtype=np.float32) for x in terms])
        worst = t.max(axis=0)
        cost = self.COST_THRESHOLD * worst
        ok = np.isfinite(cost) & (cost <= self.COST_THRESHOLD)
        return cost, t.sum(axis=0), ok

    def _plan(self, ch: _Change, voice: VoiceInput, *, window: int | None = None) -> None:
        """Busca, de una vez, el par (fotograma de salida dentro del plazo,
        entrada del destino) compatible de menor coste y lo deja en `ch.plan`.

        Trabajo acotado: `window` salidas x entradas precalculadas, una sola
        vez por cambio programado (no por fotograma). Sin par compatible,
        `ch.plan` queda a None y el cambio se resolverá al vencer el plazo con
        un corte a la mejor entrada."""
        ch.plan = None
        cur = self.cur
        n = len(cur.clip)
        if window is None:
            window = max(1, ch.deadline - self._tick + 1)
        best: tuple[tuple, int, _Option] | None = None
        if ch.kind == "gesture":
            name = ch.gesture
            speaking = self._desired_kind(voice) == "speaking"
            variant = speaking_variant(name)
            clip = self.gestures.get(variant) if speaking and variant in self.gestures else self.gestures.get(name)
            if clip is None:
                return
            for k in range(window):
                q = cur.pos + 1 + k
                if q >= n:
                    break
                opt = self._options_for_clip(
                    clip, self._desc_at(cur.clip, q), self._src_query(cur.clip, cur.kind, q, "gesture")
                )
                if opt is not None and opt.ok and (best is None or opt.key < best[0]):
                    best = (opt.key, k, opt)
        else:
            exclude = self._exclude_for(ch)
            for k in range(window):
                q = cur.pos + 1 + k
                if q >= n:
                    break
                params = self._src_query(cur.clip, cur.kind, q, ch.kind)
                options = self._entry_options(
                    ch.kind, self._desc_at(cur.clip, q), exclude=exclude,
                    prefer_other=ch.prefer_other, **params,
                )
                for opt in options:
                    if opt.ok:
                        if best is None or opt.key < best[0]:
                            best = (opt.key, k, opt)
                        break  # ya están ordenadas: la primera compatible es la mejor
        if best is not None:
            _, k, opt = best
            ch.plan = _Plan(tick=self._tick + k, clip=opt.clip, pos=opt.pos, cost=opt.cost)

    # ------------------------------------------------------------------ #
    # Transiciones
    # ------------------------------------------------------------------ #

    def _steps_for_ms(self, ms: float) -> int:
        return max(0, int(round(ms * self.fps / 1000.0)))

    def _steps_for_s(self, s: float) -> int:
        return max(1, int(round(s * self.fps)))

    def _max_transition_ms(self) -> float:
        return max(float(self.cfg.transition_ms), 400.0)

    def _start_transition(
        self,
        dst: _Playhead,
        then: DirectorState,
        *,
        a_desc: np.ndarray,
        b_pos: int,
        src: _Playhead | None = None,
        src_frame: np.ndarray | None = None,
        anchor: bool = False,
        gesture: str = "",
        min_steps: int = 1,
    ) -> None:
        if self._tr is not None:
            self.stats.interrupted_transitions += 1
        idx = dst.clip.index
        b_desc = self._desc_at(dst.clip, b_pos)
        ratio = float(distances(a_desc[None, :], b_desc)[0]) / max(idx.d_ok, self.D_OK_FLOOR)
        if anchor:
            # Par compatible: un único fotograma mezclado (steps=2) salvo que el
            # llamador pida más (reanudar la pausa, 3). La mezcla es la
            # excepción, condicionada a la compatibilidad geométrica medida.
            steps = min(max(2, min_steps), max(2, self._max_steps))
        else:
            # Par incompatible o sin procedencia medible: corte seco. Nunca se
            # mezclan dos caras distintas (eso era la doble imagen).
            steps = 1
        self.stats.transitions += 1
        if anchor:
            self.stats.anchor_transitions += 1
        else:
            self.stats.fallback_transitions += 1

        tr = _Transition(dst=dst, then=then, steps=steps, gesture=gesture, anchor=anchor)
        if self.cfg.transition_morph == "auto" and ratio > self.MORPH_RATIO and steps >= 3:
            try:
                a = src_frame if src_frame is not None else self._frame(
                    _Playhead(src.clip, src.kind, min(src.pos + 1, len(src.clip) - 1))
                )
                tr.morph = self._build_morph(np.array(a), np.array(self._frame(_Playhead(dst.clip, dst.kind, b_pos))))
                self.stats.morphs += 1
            except cv2.error:
                tr.morph = None
        if tr.morph is None:
            tr.src = src
            tr.src_frame = src_frame if src is None else None
        self._tr = tr
        self.cur = dst
        self.state = DirectorState.TRANSITION
        self._last_key = None

    def _step_transition(self, voice: VoiceInput) -> tuple[np.ndarray, bool, bool, float]:
        tr = self._tr
        assert tr is not None
        # Si la voz cambia de grupo a mitad de un fundido, NO se reorienta desde
        # la imagen mezclada (sería fundido sobre fundido): el fundido termina
        # (≤ max_steps) y después `_advance` programa el cambio al grupo que pida
        # la voz en ese momento, partiendo de fotogramas reales del clip. Si la
        # voz ya volvió, no queda nada pendiente.
        tr.step += 1
        raw = tr.step / tr.steps
        n_dst = len(tr.dst.clip)
        tr.dst.pos = min(tr.dst.pos + 1, n_dst - 1)
        if raw >= 1.0:
            self._tr = None
            self.state = tr.then
            self.cur = tr.dst
            self.cur.frames_in_clip = 0
            frame = self._frame(self.cur)
            self._last_key = (id(self.cur.clip), self.cur.pos)
            return frame, False, False, 1.0

        w = _smoothstep(raw)
        if tr.morph is not None:
            return self._morph_at(tr.morph, w), True, True, raw

        if tr.src is not None:
            tr.src.pos = min(tr.src.pos + 1, len(tr.src.clip) - 1)
            a = self._frame(tr.src)
        else:
            a = tr.src_frame
        b = self._frame(tr.dst)
        return self._blend(a, b, w), True, False, raw

    def _blend(self, a: np.ndarray, b: np.ndarray, w: float) -> np.ndarray:
        if self._buffer is None or self._buffer.shape != a.shape:
            self._buffer = np.empty_like(a)
        cv2.addWeighted(a, 1.0 - w, b, w, 0.0, dst=self._buffer)
        return self._buffer

    # -- interpolación con flujo óptico (opcional, transition_morph='auto') -- #

    def _planes(self, frame_shape: tuple[int, ...]) -> list[tuple[slice, tuple[int, int], float]]:
        """(filas, (alto, ancho) del plano, escala respecto a la luma)."""
        h, w = self.cfg.height, self.cfg.width
        if self.cfg.pixel_format == "nv12":
            return [(slice(0, h), (h, w), 1.0), (slice(h, h + h // 2), (h // 2, w // 2), 0.5)]
        return [(slice(0, h), (h, w), 1.0)]

    def _build_morph(self, a: np.ndarray, b: np.ndarray) -> _Morph:
        if self._dis is None:
            self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        ya = luma(a, self.cfg.pixel_format, self.cfg.height)
        yb = luma(b, self.cfg.pixel_format, self.cfg.height)
        h, w = ya.shape
        sw = 160
        sh = max(8, int(round(h * sw / w)))
        sa = cv2.resize(ya, (sw, sh), interpolation=cv2.INTER_AREA)
        sb = cv2.resize(yb, (sw, sh), interpolation=cv2.INTER_AREA)
        flow = self._dis.calc(sa, sb, None)  # a -> b, en píxeles reducidos
        planes = []
        for rows, (ph, pw), _scale in self._planes(a.shape):
            f = cv2.resize(flow, (pw, ph), interpolation=cv2.INTER_LINEAR)
            fx = f[..., 0] * (pw / sw)
            fy = f[..., 1] * (ph / sh)
            gx, gy = np.meshgrid(np.arange(pw, dtype=np.float32), np.arange(ph, dtype=np.float32))
            planes.append((gx, gy, fx.astype(np.float32), fy.astype(np.float32), rows, (ph, pw)))
        return _Morph(a, b, planes)

    def _morph_at(self, m: _Morph, t: float) -> np.ndarray:
        if self._buffer is None or self._buffer.shape != m.a.shape:
            self._buffer = np.empty_like(m.a)
        out = self._buffer
        for gx, gy, fx, fy, rows, (ph, pw) in m.planes:
            pa = m.a[rows]
            pb = m.b[rows]
            if pa.ndim == 2 and pa.shape[1] != pw:  # plano UV entrelazado de NV12
                pa = pa.reshape(ph, pw, 2)
                pb = pb.reshape(ph, pw, 2)
            wa = cv2.remap(pa, gx - t * fx, gy - t * fy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            wb = cv2.remap(
                pb, gx + (1 - t) * fx, gy + (1 - t) * fy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )
            mixed = cv2.addWeighted(wa, 1.0 - t, wb, t, 0.0)
            out[rows] = mixed.reshape(out[rows].shape)
        return out

    # ------------------------------------------------------------------ #
    # Lectura de fotogramas y modo degradado
    # ------------------------------------------------------------------ #

    def _frame(self, ph: _Playhead) -> np.ndarray:
        try:
            frame = ph.clip.frames.frame(max(0, ph.pos))
        except Exception as exc:  # caché borrada, disco extraíble, etc.
            raise _ClipFault(ph, exc) from exc
        if frame.shape != self._shape:
            raise _ClipFault(ph, ValueError(f"geometría inesperada {frame.shape}"))
        return frame

    def _degrade(self, fault: "_ClipFault") -> np.ndarray:
        clip = fault.playhead.clip
        if self.state is not DirectorState.DEGRADED:  # un evento por entrada, no por fotograma
            self.stats.degraded_events += 1
        self._message = f"No se pudo leer «{clip.name}» ({fault.exc}); se retira y se continúa."
        for pool in self.pools.values():
            if clip in pool and len(pool) > 1:
                pool.remove(clip)
        for name, g in list(self.gestures.items()):
            if g is clip:
                del self.gestures[name]
        if self._active_gesture:
            self._active_gesture = ""
            self._latency_pending = False
        self._tr = None
        self._change = None
        self._last_key = None
        self.state = DirectorState.DEGRADED
        return self._last_good

    def _logic_fault(self, exc: Exception) -> np.ndarray:
        """Error inesperado de lógica: se registra, se congela la última imagen
        buena y se vuelve al silencio por la ruta de recuperación (DEGRADED)."""
        _log.exception("Error en el director; se recupera desde la última imagen: %s", exc)
        if self.state is not DirectorState.DEGRADED:
            self.stats.degraded_events += 1
        if self._active_gesture:
            self._release_gesture(cancelled=True)
        self._tr = None
        self._change = None
        self._last_key = None
        self.state = DirectorState.DEGRADED
        return self._last_good

    def _recover(self) -> None:
        """Vuelve a un ancla de silencio desde la imagen congelada (corte: la
        imagen congelada no tiene procedencia medible)."""
        clip = self.pools["silent"][0]
        query = self._query()
        a = self._anchors[id(clip)]
        pos = int(a.entries[int(np.argmin(distances(a.desc, query)))]) if a.entries.size else 0
        self._frame(_Playhead(clip, "silent", pos))  # si también falla, seguimos en DEGRADED
        self._message = ""
        self._dwell = self._new_dwell("silent")
        self._start_transition(_Playhead(clip, "silent", pos - 1), DirectorState.SILENT,
                               src_frame=self._last_good.copy(), a_desc=query, b_pos=pos,
                               anchor=False, min_steps=self._fallback_steps)


class _ClipFault(Exception):
    def __init__(self, playhead: _Playhead, exc: Exception) -> None:
        super().__init__(str(exc))
        self.playhead = playhead
        self.exc = exc
