"""Características de audio para guiar la boca: envolvente de apertura 0..1.

La puerta de voz (`gate.py`) responde a una pregunta binaria —¿se está
hablando?— y decide qué grupo de clips se usa. Esto responde a otra distinta:
**cuánto debería estar abierta la boca ahora mismo**. Se deriva de la energía
de la trama relativa al piso de ruido, con una envolvente de subida rápida y
bajada media, y cae a cero en cuanto la trama deja de ser voz. Así la boca se
cierra en las pausas cortas aunque la puerta siga en SPEAKING.

Es energía, no fonemas: no distingue una «a» de una «m» de igual intensidad.
Lo que sí da es lo que más delata una boca desacoplada —que se mueva cuando no
suena nada y que no se abra en las sílabas fuertes—. Ver
docs/DISENO_CAMVOICE_TIEMPO_REAL.md §Limitaciones.

Pura y determinista: avanza en pasos de `frame_ms`, sin consultar el reloj.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["MouthEnvelopeConfig", "MouthEnvelope", "MicroPauseDetector"]


@dataclass(frozen=True, slots=True)
class MouthEnvelopeConfig:
    range_db: float = 18.0
    """dB por encima del umbral de voz que corresponden a apertura completa."""

    rise_ms: float = 25.0
    """Constante de tiempo de subida: una sílaba fuerte abre en 1-2 tramas."""

    fall_ms: float = 90.0
    """Constante de tiempo de bajada: cierra sin convertir el habla en un
    temblor, pero en ~3 tramas de video a 15 FPS ya está casi cerrada."""

    onset_db: float = 6.0
    """Margen sobre el piso de ruido a partir del cual empieza a abrir."""

    peak_decay_db_s: float = 3.0
    """Velocidad (dB/s) a la que el pico de referencia baja hacia la voz
    actual. La apertura completa corresponde al pico reciente y la boca cerrada
    a `range_db` por debajo: así las sílabas modulan la boca tanto si se habla
    fuerte como bajo, en vez de saturar en «abierta» todo el rato."""


def _alpha(dt_ms: float, tau_ms: float) -> float:
    if tau_ms <= 0:
        return 1.0
    return 1.0 - math.exp(-dt_ms / tau_ms)


class MouthEnvelope:
    """Seguidor de envolvente: (rms_db, piso, voz) por trama -> apertura 0..1."""

    def __init__(self, frame_ms: int, cfg: MouthEnvelopeConfig | None = None) -> None:
        if frame_ms <= 0:
            raise ValueError("frame_ms debe ser positivo.")
        self.cfg = cfg or MouthEnvelopeConfig()
        self.frame_ms = int(frame_ms)
        self._a_up = _alpha(frame_ms, self.cfg.rise_ms)
        self._a_down = _alpha(frame_ms, self.cfg.fall_ms)
        self._peak_decay = self.cfg.peak_decay_db_s * frame_ms / 1000.0
        self.value = 0.0
        self.peak_db: float | None = None

    def reset(self) -> None:
        self.value = 0.0
        self.peak_db = None

    def update(self, rms_db: float, noise_floor_db: float, voiced: bool) -> float:
        """Avanza una trama y devuelve la apertura objetivo actual."""
        if voiced and math.isfinite(rms_db) and math.isfinite(noise_floor_db):
            cfg = self.cfg
            onset = noise_floor_db + cfg.onset_db
            # Pico de referencia: sube al instante, baja despacio. Nunca por
            # debajo de onset + range, para no amplificar una voz muy baja.
            if self.peak_db is None or rms_db > self.peak_db:
                self.peak_db = rms_db
            else:
                self.peak_db = max(rms_db, self.peak_db - self._peak_decay)
            top = max(self.peak_db, onset + cfg.range_db)
            lo = max(onset, top - cfg.range_db)
            raw = min(1.0, max(0.0, (rms_db - lo) / max(1e-6, top - lo)))
        else:
            raw = 0.0
        alpha = self._a_up if raw > self.value else self._a_down
        self.value += (raw - self.value) * alpha
        if self.value < 1e-4:
            self.value = 0.0
        return self.value


class MicroPauseDetector:
    """Marca las micro-pausas dentro del habla: True tras `release_ms` de
    tramas sin voz seguidas; vuelve a False en la primera trama con voz.

    Es independiente de la puerta SILENT/SPEAKING (cuyo `release_ms` es largo
    para no alternar de grupo en cada respiración): sirve para cerrar la boca
    y elegir una pose de escucha en pausas de 150-250 ms sin abandonar el
    estado de habla. Pura y determinista, avanza en pasos de `frame_ms`.
    """

    MIN_MS = 150
    MAX_MS = 250

    def __init__(self, frame_ms: int, release_ms: int = 200) -> None:
        if frame_ms <= 0:
            raise ValueError("frame_ms debe ser positivo.")
        self.frame_ms = int(frame_ms)
        self.release_ms = int(min(self.MAX_MS, max(self.MIN_MS, int(release_ms))))
        self._quiet_ms = 0
        self.paused = False

    def reset(self) -> None:
        self._quiet_ms = 0
        self.paused = False

    def update(self, voiced: bool) -> bool:
        if voiced:
            self._quiet_ms = 0
            self.paused = False
        else:
            self._quiet_ms += self.frame_ms
            if self._quiet_ms >= self.release_ms:
                self.paused = True
        return self.paused
