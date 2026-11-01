"""Referencia de reproducción y clasificación «eco remoto probable».

Problema que resuelve: en una videollamada el audio de los demás sale por los
altavoces, entra por el micrófono y el VAD lo reconoce —correctamente— como
voz. Ni WebRTC, ni la puerta absoluta, ni la SNR dicen *quién* habla. La única
forma honesta de distinguir la voz local de la reproducción remota es comparar
el micrófono con **lo que el equipo está reproduciendo**.

Qué hace este módulo:

  * `find_reference_device`  localiza un dispositivo de entrada que ya contenga
    la mezcla de salida del sistema (un «[Loopback]» de PortAudio si la
    compilación lo expone; si no, «Mezcla estéreo»/«Stereo Mix»/«What U Hear»
    del controlador de sonido). Es una **referencia de eco**, nunca el
    micrófono de decisión.
  * `EchoReference`  abre esa segunda entrada con la misma captura acotada del
    micrófono y entrega tramas a la frecuencia del VAD.
  * `EchoEstimator`  compara, trama a trama, la envolvente de energía del
    micrófono con la de la referencia buscando el retardo (±600 ms) con mayor
    correlación, estima la ganancia de acoplamiento altavoz→micrófono y
    clasifica cada trama en ECHO / DOUBLE / LOCAL / IDLE.

Qué NO es: no es cancelación de eco (no resta la referencia a la señal) ni un
detector de identidad del hablante. Es un clasificador conservador: solo veta
una trama cuando hay evidencia sólida de reproducción (referencia activa y
envolventes coherentes) y **no** hay evidencia de voz local residual (el
micrófono no supera el eco previsto en `LOCAL_MARGIN_DB`). Sin referencia, o
con la referencia en silencio, no cambia nada respecto al comportamiento
anterior.

Todo el trabajo por trama es un producto de matrices de 61×40 flotantes; no
hay FFT, modelos ni lecturas bloqueantes en el callback de captura.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from ..config import AudioConfig
from .capture import AudioCapture, AudioError, DeviceInfo, list_input_devices
from .resample import Resampler, rms_dbfs

__all__ = [
    "EchoState",
    "EchoDecision",
    "EchoEstimator",
    "EchoReference",
    "find_reference_device",
    "looks_like_system_mix",
]


# --------------------------------------------------------------------------- #
# Dispositivos
# --------------------------------------------------------------------------- #

_MIX_PATTERNS = (
    "stereo mix",
    "mezcla estéreo",
    "mezcla estereo",
    "mezcla est",  # el nombre llega truncado por MME («Mezcla est�reo (Realt»)
    "what u hear",
    "what you hear",
    "wave out mix",
    "loopback",
)
"""Nombres con los que los controladores exponen la mezcla de salida como
entrada. Son la referencia natural de eco y, a la vez, un micrófono erróneo."""

_VIRTUAL_PATTERNS = (
    "cable output",
    "vb-audio",
    "voicemeeter",
    "virtual cable",
)
"""Entradas virtuales que transportan audio de aplicaciones, no de la sala."""

_HOSTAPI_RANK = {
    "Windows WASAPI": 0,
    "MME": 1,
    "Windows DirectSound": 2,
    "Windows WDM-KS": 3,
}


def looks_like_system_mix(name: str) -> bool:
    """True si el nombre corresponde a una mezcla del sistema o entrada virtual.

    Usarlo como micrófono de decisión hace que todo lo que suena en el equipo
    cuente como voz propia. No se cambia el dispositivo elegido: se avisa.
    """
    low = (name or "").lower()
    return any(p in low for p in _MIX_PATTERNS) or any(p in low for p in _VIRTUAL_PATTERNS)


def _is_mix_name(name: str) -> bool:
    low = (name or "").lower()
    return any(p in low for p in _MIX_PATTERNS)


def find_reference_device(
    setting: str,
    *,
    exclude_index: int | None = None,
    devices: list[DeviceInfo] | None = None,
) -> DeviceInfo | None:
    """Elige el dispositivo de referencia de eco según `AudioConfig.echo_reference`.

    * `"off"` / vacío: sin referencia.
    * `"auto"`: solo dispositivos cuyo nombre identifica una mezcla de salida
      («[Loopback]», «Stereo Mix», «Mezcla estéreo», «What U Hear»...). Nunca
      se elige un micrófono real: una referencia que fuese otro micrófono
      vetaría la voz del propio usuario.
    * cualquier otro texto: nombre (exacto o parcial) del dispositivo, bajo la
      responsabilidad de quien lo configura (p. ej. «CABLE Output» si la
      reunión se enruta por un cable virtual).

    El dispositivo del micrófono (`exclude_index`) nunca es candidato.
    """
    mode = (setting or "").strip()
    if not mode or mode.lower() == "off":
        return None
    if devices is None:
        try:
            devices = list_input_devices()
        except AudioError:
            return None
    pool = [d for d in devices if d.index != exclude_index]
    if not pool:
        return None

    def rank(d: DeviceInfo) -> tuple[int, int, str]:
        return (_HOSTAPI_RANK.get(d.hostapi, 9), 0 if "loopback" in d.name.lower() else 1, d.name.lower())

    if mode.lower() == "auto":
        candidates = sorted((d for d in pool if _is_mix_name(d.name)), key=rank)
        return candidates[0] if candidates else None

    wanted = mode.lower()
    exact = sorted((d for d in pool if d.name.lower() == wanted), key=rank)
    if exact:
        return exact[0]
    partial = sorted(
        (d for d in pool if wanted in d.name.lower() or d.name.lower() in wanted), key=rank
    )
    return partial[0] if partial else None


# --------------------------------------------------------------------------- #
# Captura de la referencia
# --------------------------------------------------------------------------- #


class EchoReference:
    """Segunda entrada (mezcla de salida) remuestreada a la frecuencia del VAD.

    Reutiliza `AudioCapture`: mismo callback mínimo, misma cola acotada. Si el
    dispositivo falla al abrir o desaparece, el dueño lo descarta y sigue sin
    referencia; nunca es motivo para detener la captura del micrófono.
    """

    def __init__(self, device: DeviceInfo, cfg: AudioConfig, target_samplerate: int) -> None:
        self.device = device
        self.name = device.name
        self._capture = AudioCapture(
            replace(
                cfg,
                device=device.index,
                device_name=device.name,
                target_samplerate=target_samplerate,
            )
        )
        self._target = int(target_samplerate)
        self._resampler: Resampler | None = None
        self._pending = np.zeros(0, dtype=np.float32)

    def start(self) -> None:
        self._capture.start()
        self._resampler = Resampler(self._capture.samplerate, self._target)
        self._pending = np.zeros(0, dtype=np.float32)

    def stop(self) -> None:
        self._capture.stop()
        self._resampler = None
        self._pending = np.zeros(0, dtype=np.float32)

    @property
    def active(self) -> bool:
        return self._capture.active

    @property
    def error(self) -> str:
        return self._capture.callback_error

    @property
    def samplerate(self) -> int:
        return self._capture.samplerate

    @property
    def dropped_blocks(self) -> int:
        return self._capture.dropped_blocks

    def read_levels(self, frame_len: int) -> list[float]:
        """Vacía la cola y devuelve el nivel (dBFS) de cada trama completa nueva."""
        if self._resampler is None:
            return []
        block = self._capture.read_all()
        if block.size:
            resampled = self._resampler.process(block)
            if resampled.size:
                self._pending = (
                    np.concatenate((self._pending, resampled)) if self._pending.size else resampled
                )
        n = self._pending.size // max(1, frame_len)
        if n == 0:
            return []
        frames = self._pending[: n * frame_len].reshape(n, frame_len)
        self._pending = self._pending[n * frame_len :]
        # Mismo estimador de nivel que el detector de energía del micrófono.
        return [rms_dbfs(frames[i]) for i in range(n)]


# --------------------------------------------------------------------------- #
# Estimador
# --------------------------------------------------------------------------- #


class EchoState(Enum):
    OFF = "off"
    """Sin referencia disponible: la decisión es la del VAD, como antes."""

    IDLE = "idle"
    """Referencia presente pero en silencio reciente: no hay eco que vetar."""

    ECHO = "echo"
    """Reproducción activa y micrófono coherente con ella sin voz local
    residual apreciable: la trama se veta."""

    DOUBLE = "double"
    """Reproducción activa pero el micrófono supera el eco previsto: hay voz
    local además de la remota; la trama pasa."""

    LOCAL = "local"
    """Reproducción activa pero el micrófono no sigue su envolvente: lo que
    suena en el micrófono no viene de los altavoces; la trama pasa."""


@dataclass(frozen=True, slots=True)
class EchoDecision:
    state: EchoState
    veto: bool
    lag_ms: int = 0
    coherence: float = 0.0
    residual_db: float = math.nan
    """Nivel del micrófono menos el eco previsto (ref + ganancia). NaN
    mientras la ganancia de acoplamiento no está calibrada."""

    ref_db: float = -120.0


class EchoEstimator:
    """Clasificador de eco sobre envolventes de energía, puro y determinista.

    Las tramas del micrófono y de la referencia se indexan en el mismo eje
    (índice de trama del hilo de audio). La referencia llega por otro
    dispositivo con su propio retardo, así que la alineación se busca en
    ±`MAX_LAG` tramas por correlación de envolventes (coeficiente de Pearson
    sobre `WINDOW` tramas, sin media). Un desfase constante entre los dos
    flujos y la latencia de salida se absorben en esa búsqueda.
    """

    WINDOW = 40
    """Tramas de envolvente que se correlacionan (0,8 s a 20 ms)."""

    MAX_LAG = 30
    """Retardo máximo buscado, en tramas, en ambos sentidos (±600 ms a 20 ms).
    Cubre la latencia de salida de WASAPI compartido, altavoces HDMI y el
    desfase de arranque entre los dos flujos."""

    REF_ACTIVE_DB = 6.0
    """La referencia cuenta como activa `REF_ACTIVE_DB` por encima de su piso."""

    REF_GATE_DB = -60.0
    """Por debajo de este nivel absoluto la referencia nunca está activa."""

    COH_ON = 0.60
    COH_OFF = 0.45
    """Coherencia (correlación de envolventes) para entrar en ECHO y para
    mantenerse. Dos personas distintas hablando a la vez producen envolventes
    con correlación baja; la misma señal por dos caminos, alta."""

    COH_CAL = 0.75
    """Coherencia mínima para que una trama alimente la ganancia de acoplamiento."""

    CAL_FRAMES = 25
    """Tramas coherentes antes de confiar en la ganancia (0,5 s)."""

    LOCAL_MARGIN_DB = 8.0
    """Si el micrófono supera el eco previsto en esto o más, hay voz local
    (doble habla): no se veta. Una voz a medio metro queda normalmente 8-15 dB
    por encima del altavoz de un portátil captado por el mismo micrófono; una
    voz tenue o lejana puede quedar por debajo y se perderá mientras el otro
    hable. Es el compromiso documentado."""

    HANGOVER_MS = 240
    """Tras una trama ECHO el veto se prolonga este tiempo para cubrir la cola
    de reverberación cuando el participante remoto calla o hace una pausa."""

    FLOOR_TAU_UP_S = 3.0
    FLOOR_TAU_DOWN_S = 0.4
    MIN_FLOOR_DB = -75.0

    _SIZE = 256  # búfer circular; debe superar WINDOW + 2*MAX_LAG

    def __init__(self, frame_ms: int) -> None:
        if frame_ms <= 0:
            raise ValueError("frame_ms debe ser positivo.")
        self.frame_ms = int(frame_ms)
        dt = frame_ms / 1000.0
        self._a_up = 1.0 - math.exp(-dt / self.FLOOR_TAU_UP_S)
        self._a_down = 1.0 - math.exp(-dt / self.FLOOR_TAU_DOWN_S)
        self._lags = np.arange(-self.MAX_LAG, self.MAX_LAG + 1, dtype=np.int64)
        self._win = np.arange(-self.WINDOW + 1, 1, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._mic = np.full(self._SIZE, -120.0, dtype=np.float32)
        self._ref = np.full(self._SIZE, -120.0, dtype=np.float32)
        self._ref_seen = np.zeros(self._SIZE, dtype=bool)
        self._ref_max_index = -1
        self._mic_count = 0
        self.ref_floor_db = -70.0
        self._floor_init = False
        self._last_ref_active_index = -10**9
        self.lag: int | None = None
        self.coherence = 0.0
        self.gain_db: float | None = None
        self._cal_frames = 0
        self._echo = False
        self._hang_ms = self.HANGOVER_MS
        self.vetoes = 0
        self.state = EchoState.IDLE

    # ------------------------------------------------------------------ #

    def push_ref(self, index: int, ref_db: float) -> None:
        """Registra el nivel de la trama `index` de la referencia."""
        if not math.isfinite(ref_db):
            ref_db = -120.0
        if not self._floor_init:
            self.ref_floor_db = ref_db
            self._floor_init = True
        elif ref_db < self.ref_floor_db:
            self.ref_floor_db += (ref_db - self.ref_floor_db) * self._a_down
        else:
            self.ref_floor_db += (ref_db - self.ref_floor_db) * self._a_up
        self.ref_floor_db = max(self.MIN_FLOOR_DB, self.ref_floor_db)

        slot = index % self._SIZE
        self._ref[slot] = ref_db
        self._ref_seen[slot] = True
        if index > self._ref_max_index:
            self._ref_max_index = index
        if self._is_active(ref_db):
            self._last_ref_active_index = max(self._last_ref_active_index, index)

    def _is_active(self, ref_db: float) -> bool:
        return ref_db > self.REF_GATE_DB and ref_db > self.ref_floor_db + self.REF_ACTIVE_DB

    def classify(self, index: int, mic_db: float) -> EchoDecision:
        """Clasifica la trama `index` del micrófono (nivel en dBFS)."""
        if not math.isfinite(mic_db):
            mic_db = -120.0
        self._mic[index % self._SIZE] = mic_db
        self._mic_count += 1

        # Sin actividad reciente en la referencia no hay nada que vetar.
        recent = index - self._last_ref_active_index <= self.WINDOW
        if not recent or self._ref_max_index < 0:
            self._echo = False
            self._hang_ms = self.HANGOVER_MS
            self.state = EchoState.IDLE
            return EchoDecision(EchoState.IDLE, False, self._lag_ms(), 0.0, math.nan, -120.0)

        corr, valid = self._correlate(index)
        if valid.any():
            best = int(np.argmax(np.where(valid, corr, -2.0)))
            if self.lag is None or not valid[self.lag + self.MAX_LAG]:
                self.lag = int(self._lags[best])
            elif corr[best] > corr[self.lag + self.MAX_LAG] + 0.10:
                # Retardo nuevo claramente mejor (cambió el dispositivo de
                # salida, se recuperó un hueco): se adopta sin histéresis.
                self.lag = int(self._lags[best])
            self.coherence = float(max(0.0, corr[self.lag + self.MAX_LAG]))
        else:
            self.coherence = 0.0

        ref_db = self._ref_at(index + (self.lag or 0))
        if ref_db is None and self._ref_max_index >= 0:
            # La trama alineada aún no ha llegado (la referencia va por detrás
            # del micrófono en este instante): se usa la más reciente, que es
            # la mejor estimación disponible sin esperar.
            ref_db = self._ref_at(self._ref_max_index)
        active = ref_db is not None and self._is_active(ref_db)
        ref_val = ref_db if ref_db is not None else -120.0

        coh_threshold = self.COH_OFF if self._echo else self.COH_ON
        coherent = self.coherence >= coh_threshold

        residual = math.nan
        trusted = self.gain_db is not None and self._cal_frames >= self.CAL_FRAMES
        if active and trusted:
            residual = mic_db - (ref_val + float(self.gain_db))

        # Calibración de la ganancia de acoplamiento: solo con eco claro, y
        # bajando deprisa / subiendo despacio para que el doble habla no la
        # infle (el micrófono es entonces más fuerte que el eco).
        if active and self.coherence >= self.COH_CAL and mic_db > -70.0:
            sample = mic_db - ref_val
            if self.gain_db is None:
                self.gain_db = sample
            else:
                alpha = 0.25 if sample < self.gain_db else 0.03
                self.gain_db += (sample - self.gain_db) * alpha
            self._cal_frames += 1

        if active and coherent and (not trusted or residual < self.LOCAL_MARGIN_DB):
            state, veto = EchoState.ECHO, True
            self._hang_ms = 0
        elif active and trusted and residual >= self.LOCAL_MARGIN_DB:
            state, veto = EchoState.DOUBLE, False
            self._hang_ms = self.HANGOVER_MS
        elif self._hang_ms < self.HANGOVER_MS:
            # Cola del eco (reverberación, pausa breve del remoto).
            self._hang_ms += self.frame_ms
            state, veto = EchoState.ECHO, True
        else:
            state, veto = EchoState.LOCAL, False

        self._echo = state is EchoState.ECHO
        self.state = state
        if veto:
            self.vetoes += 1
        return EchoDecision(state, veto, self._lag_ms(), self.coherence, residual, ref_val)

    # ------------------------------------------------------------------ #

    def _lag_ms(self) -> int:
        return 0 if self.lag is None else int(self.lag * self.frame_ms)

    def _ref_at(self, index: int) -> float | None:
        if index < 0 or index > self._ref_max_index or index <= self._ref_max_index - self._SIZE:
            return None
        slot = index % self._SIZE
        return float(self._ref[slot]) if self._ref_seen[slot] else None

    def _correlate(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        """Correlación de envolventes para cada retardo candidato.

        Devuelve (corr[2*MAX_LAG+1], valid[2*MAX_LAG+1]); `valid` es False
        donde la ventana de la referencia aún no ha llegado o ya se perdió.
        """
        mic_idx = index + self._win  # (W,)
        if mic_idx[0] < 0 or self._mic_count < self.WINDOW:
            return np.zeros(self._lags.size), np.zeros(self._lags.size, dtype=bool)
        mic = self._mic[mic_idx % self._SIZE].astype(np.float64)
        mic -= mic.mean()
        mic_norm = float(np.sqrt(np.dot(mic, mic)))

        ref_idx = mic_idx[None, :] + self._lags[:, None]  # (L, W)
        in_range = (ref_idx >= 0) & (ref_idx <= self._ref_max_index) & (
            ref_idx > self._ref_max_index - self._SIZE
        )
        slots = ref_idx % self._SIZE
        valid = in_range.all(axis=1) & self._ref_seen[slots].all(axis=1)
        if mic_norm < 1e-3 or not valid.any():
            return np.zeros(self._lags.size), valid & False

        ref = self._ref[slots].astype(np.float64)
        ref -= ref.mean(axis=1, keepdims=True)
        ref_norm = np.sqrt(np.einsum("ij,ij->i", ref, ref))
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = (ref @ mic) / (ref_norm * mic_norm)
        corr = np.where(np.isfinite(corr) & (ref_norm > 1e-3), corr, 0.0)
        return corr, valid
