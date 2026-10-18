"""Captura de micrófono sobre sounddevice/PortAudio.

El callback de PortAudio corre en un hilo de tiempo real: aquí solo hace la
mezcla a mono y una copia a una cola acotada. Todo lo demás (remuestreo, VAD)
ocurre en el hilo trabajador de `pipeline.py`, de modo que un pico de CPU nunca
produce un corte en la captura.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

from ..config import AudioConfig
from .resample import to_mono

__all__ = ["DeviceInfo", "list_input_devices", "resolve_device", "AudioCapture", "AudioError"]


class AudioError(RuntimeError):
    """Error de dispositivo de audio con mensaje apto para mostrar al usuario."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    index: int
    name: str
    hostapi: str
    channels: int
    default_samplerate: float
    is_default: bool = False

    @property
    def label(self) -> str:
        mark = " (predeterminado)" if self.is_default else ""
        return f"[{self.index}] {self.name} - {self.hostapi}{mark}"


def _sd():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - depende del entorno
        raise AudioError(
            "No se pudo cargar sounddevice/PortAudio. Reinstala las dependencias "
            f"de audio. Detalle: {exc}"
        ) from exc
    return sd


def list_input_devices(*, preferred_hostapis: tuple[str, ...] = ("Windows WASAPI",)) -> list[DeviceInfo]:
    """Dispositivos de entrada disponibles, los de la API preferida primero.

    En Windows el mismo micrófono aparece varias veces (MME, DirectSound,
    WASAPI, WDM-KS). WASAPI da la latencia más baja y la frecuencia nativa, así
    que se ofrece primero.
    """
    sd = _sd()
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        default_index = sd.default.device[0] if sd.default.device else None
    except Exception as exc:
        raise AudioError(f"No se pudo consultar los dispositivos de audio: {exc}") from exc

    out: list[DeviceInfo] = []
    for index, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        api = str(hostapis[dev["hostapi"]]["name"])
        out.append(
            DeviceInfo(
                index=index,
                name=str(dev["name"]),
                hostapi=api,
                channels=int(dev["max_input_channels"]),
                default_samplerate=float(dev.get("default_samplerate") or 0.0),
                is_default=(index == default_index),
            )
        )

    def sort_key(d: DeviceInfo) -> tuple[int, int, str]:
        rank = preferred_hostapis.index(d.hostapi) if d.hostapi in preferred_hostapis else len(preferred_hostapis)
        return (rank, 0 if d.is_default else 1, d.name.lower())

    return sorted(out, key=sort_key)


def resolve_device(index: int | None, name: str = "") -> int | None:
    """Revalida un índice guardado; si ya no corresponde, busca por nombre.

    Los índices de PortAudio cambian al conectar o desconectar hardware, así que
    el nombre es la referencia estable.
    """
    try:
        devices = list_input_devices()
    except AudioError:
        return index
    if not devices:
        return None

    by_index = {d.index: d for d in devices}
    if index is not None and index in by_index:
        if not name or by_index[index].name == name:
            return index
    if name:
        for dev in devices:
            if dev.name == name:
                return dev.index
        for dev in devices:  # coincidencia laxa: el sufijo de la API puede variar
            if name.lower() in dev.name.lower() or dev.name.lower() in name.lower():
                return dev.index
    return None


# --------------------------------------------------------------------------- #


class AudioCapture:
    """Flujo de entrada mono con cola acotada.

    `read_all()` devuelve todo lo acumulado desde la llamada anterior, en la
    frecuencia nativa del dispositivo (el remuestreo es del consumidor, que sabe
    a qué frecuencia lo necesita).
    """

    QUEUE_SECONDS = 2.0

    def __init__(self, cfg: AudioConfig) -> None:
        self.cfg = cfg
        self.samplerate: int = 0
        self.channels: int = 1
        self.device_index: int | None = None
        self.device_name: str = ""
        self.blocksize: int = 0

        self.overflows = 0
        self.dropped_blocks = 0
        self.callback_error: str = ""

        self._stream = None
        self._queue: deque[np.ndarray] = deque()
        self._maxlen = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #

    @property
    def active(self) -> bool:
        stream = self._stream
        return bool(stream is not None and stream.active)

    def start(self) -> None:
        if self._stream is not None:
            return
        sd = _sd()

        index = resolve_device(self.cfg.device, self.cfg.device_name)
        samplerate, channels = self._negotiate(sd, index)

        self.device_index = index
        self.samplerate = samplerate
        self.channels = channels
        self.blocksize = max(1, int(samplerate * self.cfg.block_ms / 1000))
        self._maxlen = max(4, int(self.QUEUE_SECONDS * 1000 / max(1, self.cfg.block_ms)))
        self.overflows = 0
        self.dropped_blocks = 0
        self.callback_error = ""
        with self._lock:
            self._queue.clear()

        try:
            info = sd.query_devices(index if index is not None else sd.default.device[0])
            self.device_name = str(info["name"])
        except Exception:
            self.device_name = self.cfg.device_name or "dispositivo predeterminado"

        try:
            self._stream = sd.InputStream(
                device=index,
                channels=channels,
                samplerate=samplerate,
                blocksize=self.blocksize,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioError(
                f"No se pudo abrir el micrófono «{self.device_name}»: {exc}"
            ) from exc

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        with self._lock:
            self._queue.clear()

    # ------------------------------------------------------------------ #

    def _negotiate(self, sd, index: int | None) -> tuple[int, int]:
        """Elige frecuencia y canales que el dispositivo acepte de verdad.

        Se prefiere abrir directamente a la frecuencia del VAD (Windows
        remuestrea en modo compartido y sale gratis). Si no, se usa la nativa y
        el remuestreo lo hace el consumidor.
        """
        try:
            info = sd.query_devices(index if index is not None else sd.default.device[0])
        except Exception as exc:
            raise AudioError(
                "No hay ningún micrófono disponible. Conecta uno y vuelve a intentarlo. "
                f"Detalle: {exc}"
            ) from exc

        max_ch = max(1, int(info.get("max_input_channels", 1)))
        native = int(info.get("default_samplerate") or 48000)
        rate_candidates = [self.cfg.target_samplerate, native, 48000, 44100, 16000]
        ch_candidates = [c for c in (1, 2, max_ch) if 1 <= c <= max_ch]

        errors: list[str] = []
        seen: set[tuple[int, int]] = set()
        for rate in rate_candidates:
            for ch in ch_candidates:
                if (rate, ch) in seen:
                    continue
                seen.add((rate, ch))
                try:
                    sd.check_input_settings(
                        device=index, channels=ch, samplerate=rate, dtype="float32"
                    )
                    return int(rate), int(ch)
                except Exception as exc:
                    errors.append(f"{rate} Hz / {ch} canal(es): {exc}")

        raise AudioError(
            "El micrófono no acepta ninguna combinación de frecuencia y canales "
            "conocida.\n" + "\n".join(errors[:4])
        )

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            if getattr(status, "input_overflow", False):
                self.overflows += 1
            # El resto de banderas se registra pero no interrumpe la captura.
        try:
            mono = to_mono(np.asarray(indata))
            with self._lock:
                if len(self._queue) >= self._maxlen:
                    self._queue.popleft()
                    self.dropped_blocks += 1
                self._queue.append(mono.copy())
        except Exception as exc:  # pragma: no cover - defensivo
            self.callback_error = str(exc)

    # ------------------------------------------------------------------ #

    def read_all(self) -> np.ndarray:
        """Vacía la cola y devuelve el audio mono acumulado (float32)."""
        with self._lock:
            if not self._queue:
                return np.zeros(0, dtype=np.float32)
            blocks = list(self._queue)
            self._queue.clear()
        return np.concatenate(blocks) if len(blocks) > 1 else blocks[0]

    def queued_blocks(self) -> int:
        with self._lock:
            return len(self._queue)
