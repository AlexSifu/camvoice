"""Remuestreo y mezcla a mono, sin dependencias más allá de NumPy.

El VAD de WebRTC solo acepta 8/16/32/48 kHz, pero los micrófonos de Windows
suelen abrirse a 44100 Hz. Este módulo convierte el flujo de entrada a la
frecuencia objetivo manteniendo estado entre bloques, para que no aparezcan
discontinuidades en las costuras (que el detector leería como transitorios).

Estrategia:
  - Filtro FIR paso bajo (sinc enventanado con Hann) antes de diezmar, para que
    el ruido de alta frecuencia no se refleje dentro de la banda de voz.
  - Interpolación lineal sobre la señal filtrada. Para ratios enteros cae
    exactamente sobre las muestras originales; para 44100 -> 16000 el error de
    interpolación queda muy por debajo de lo que afecta a una decisión de VAD.
"""

from __future__ import annotations

import numpy as np

__all__ = ["Resampler", "to_mono", "float_to_int16", "rms_dbfs"]

_EPS = 1e-12


def to_mono(block: np.ndarray) -> np.ndarray:
    """Promedia canales. Acepta (n,) o (n, canales)."""
    if block.ndim == 1:
        return block.astype(np.float32, copy=False)
    if block.shape[1] == 1:
        return block[:, 0].astype(np.float32, copy=False)
    return block.mean(axis=1, dtype=np.float32)


def float_to_int16(block: np.ndarray) -> np.ndarray:
    """float32 en [-1, 1] -> int16 con recorte duro (sin envolvimiento)."""
    scaled = np.clip(block, -1.0, 1.0) * 32767.0
    return scaled.astype(np.int16)


def rms_dbfs(frame: np.ndarray) -> float:
    """Nivel RMS en dBFS. Acepta int16 o float en [-1, 1]."""
    if frame.size == 0:
        return -120.0
    data = frame.astype(np.float32)
    if frame.dtype == np.int16:
        data = data / 32768.0
    rms = float(np.sqrt(np.mean(np.square(data)) + _EPS))
    return max(-120.0, 20.0 * float(np.log10(rms + _EPS)))


def _design_lowpass(cutoff_norm: float, taps: int) -> np.ndarray:
    """Sinc enventanado con Hann. `cutoff_norm` en ciclos/muestra (0 < c < 0.5)."""
    taps = max(3, taps | 1)  # longitud impar => fase lineal con retardo entero
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    h = 2.0 * cutoff_norm * np.sinc(2.0 * cutoff_norm * n)
    h *= np.hanning(taps)
    total = h.sum()
    if total != 0:
        h /= total  # ganancia unitaria en DC
    return h.astype(np.float32)


class Resampler:
    """Conversor de frecuencia de muestreo con estado, mono float32 -> float32."""

    def __init__(self, src_rate: int, dst_rate: int, *, quality: int = 8) -> None:
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("Las frecuencias de muestreo deben ser positivas.")
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.ratio = self.src_rate / self.dst_rate
        self.passthrough = self.src_rate == self.dst_rate

        self._fir: np.ndarray | None = None
        self._tail = np.zeros(0, dtype=np.float32)
        if not self.passthrough and self.src_rate > self.dst_rate:
            # 0.45 deja un margen de guarda antes del Nyquist de destino.
            cutoff = 0.45 * self.dst_rate / self.src_rate
            taps = int(max(15, quality * self.ratio * 2 + 1))
            self._fir = _design_lowpass(cutoff, taps)
            self._tail = np.zeros(self._fir.size - 1, dtype=np.float32)

        self._buf = np.zeros(0, dtype=np.float32)
        self._frac = 0.0
        # Retardo del grupo del FIR, útil para informar latencia real.
        self.latency_ms = (
            0.0 if self._fir is None
            else (self._fir.size - 1) / 2.0 / self.src_rate * 1000.0
        )

    def reset(self) -> None:
        if self._fir is not None:
            self._tail = np.zeros(self._fir.size - 1, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._frac = 0.0

    def _filter(self, x: np.ndarray) -> np.ndarray:
        if self._fir is None:
            return x
        padded = np.concatenate((self._tail, x))
        out = np.convolve(padded, self._fir, mode="valid").astype(np.float32)
        keep = self._fir.size - 1
        self._tail = padded[-keep:] if keep else padded[:0]
        return out

    def process(self, block: np.ndarray) -> np.ndarray:
        """Convierte un bloque mono float32. Puede devolver 0 o N muestras."""
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        if self.passthrough:
            return x.copy()
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)

        buf = np.concatenate((self._buf, self._filter(x)))
        if buf.size < 2:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)

        span = buf.size - 1 - self._frac
        if span < 0:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)
        n_out = int(span / self.ratio) + 1

        idx = self._frac + np.arange(n_out, dtype=np.float64) * self.ratio
        # El recorte a buf.size-2 evita desbordar al leer buf[i0 + 1] cuando la
        # última posición cae exactamente sobre la última muestra; en ese caso
        # el peso vale 1.0 y la interpolación devuelve esa muestra, sin error.
        i0 = np.minimum(idx.astype(np.int64), buf.size - 2)
        w = (idx - i0).astype(np.float32)
        out = buf[i0] * (1.0 - w) + buf[i0 + 1] * w

        consumed = int(i0[-1])
        self._buf = buf[consumed:]
        self._frac = float(w[-1]) + self.ratio
        return out.astype(np.float32, copy=False)
