"""Ensayos de transición sin cámara, sin micrófono y sin reloj real.

Reproduce, con el mismo `Director` que usa el render, los cambios que más se
notan en una reunión (hablar→callar, callar→hablar, gesto→silencio y
gesto→habla) con un reloj simulado. Sirve para:

- la pestaña Biblioteca (botones «Probar transición»): enseña el par
  origen/destino, la espera hasta el ancla, si hubo fundido de reserva y el
  coste de discontinuidad, más los fotogramas para verlo;
- la medición sobre los clips reales (`camvoice transitions`), que repite
  muchos cambios y resume la distribución de costes y esperas.

Todo es determinista para una semilla dada y no toca la caché (solo lee).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..audio.gate import VoiceState
from ..config import GesturesConfig, VideoConfig
from .cache import nv12_to_bgr
from .director import Director, DirectorClip, DirectorState, VoiceInput

SCENARIOS: dict[str, str] = {
    "hablar_callar": "Hablar → callar",
    "callar_hablar": "Callar → hablar",
    "gesto_silencio": "Gesto → silencio",
    "gesto_habla": "Gesto → habla",
}

_SIL = VoiceInput(VoiceState.SILENT, 0.0)
_TALK = VoiceInput(VoiceState.SPEAKING, 0.8)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@dataclass
class TrialReport:
    scenario: str
    origin: str = ""
    origin_frame: int = -1
    destination: str = ""
    destination_frame: int = -1
    wait_ms: float = 0.0
    """Desde el cambio de voz (o el fin del gesto) hasta que empieza el fundido."""
    fade_ms: float = 0.0
    anchor: bool = False
    fallback: bool = False
    cost: float | None = None
    high_cost: bool = False
    gesture: str = ""
    ok: bool = True
    message: str = ""
    frames: list[np.ndarray] = field(default_factory=list)
    """Fotogramas BGR reducidos (desde un poco antes del cambio)."""

    def summary(self) -> str:
        if not self.ok:
            return f"{SCENARIOS.get(self.scenario, self.scenario)}: {self.message}"
        cost = "—" if self.cost is None else f"{self.cost:.2f}"
        how = "fundido de reserva" if self.fallback else "ancla"
        risk = "  ⚠ coste alto" if self.high_cost else ""
        return (
            f"{SCENARIOS.get(self.scenario, self.scenario)}"
            + (f" ({self.gesture})" if self.gesture else "")
            + f": {self.origin} [{self.origin_frame}] → {self.destination} "
            f"[{self.destination_frame}] · espera {self.wait_ms:.0f} ms · "
            f"fundido {self.fade_ms:.0f} ms · {how} · coste {cost}{risk}"
        )


def _small_bgr(frame: np.ndarray, cfg: VideoConfig, width: int) -> np.ndarray:
    bgr = nv12_to_bgr(frame) if cfg.pixel_format == "nv12" else np.asarray(frame)
    h, w = bgr.shape[:2]
    if w <= width:
        return bgr.copy()
    return cv2.resize(bgr, (width, max(1, int(round(h * width / w)))), interpolation=cv2.INTER_AREA)


def run_trial(
    cfg: VideoConfig,
    silent: Sequence[DirectorClip],
    speaking: Sequence[DirectorClip],
    gestures: dict[str, DirectorClip] | None = None,
    scenario: str = "hablar_callar",
    *,
    gesture: str = "",
    gestures_cfg: GesturesConfig | None = None,
    speaking_actions: Sequence[str] = (),
    seed: int = 1,
    warmup_s: float | None = None,
    keep_frames: bool = True,
    frame_width: int = 320,
    cancel: Callable[[], bool] | None = None,
) -> TrialReport:
    """Ejecuta un escenario y devuelve qué transición eligió el director."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Escenario desconocido: {scenario!r}")
    rep = TrialReport(scenario)
    gestures = dict(gestures or {})
    if scenario.startswith("gesto"):
        if not gestures:
            rep.ok, rep.message = False, "No hay gestos preparados."
            return rep
        gesture = gesture if gesture in gestures else sorted(gestures)[0]
        rep.gesture = gesture
    fps = max(1, int(cfg.fps))
    dt = 1.0 / fps
    clock = _Clock()
    rng = random.Random(seed)
    director = Director(
        cfg, silent, speaking, gestures,
        gestures_cfg=gestures_cfg or GesturesConfig(cooldown_ms=0, auto_nod=False),
        clock=clock, seed=seed, speaking_actions=speaking_actions,
    )
    if warmup_s is None:
        warmup_s = 1.0 + rng.random() * 2.0  # cambia el punto de partida entre semillas

    before = _TALK if scenario in ("hablar_callar",) else _SIL
    after = _SIL if scenario in ("hablar_callar", "gesto_silencio") else _TALK
    frames: list[np.ndarray] = []
    infos = []
    keep_before = int(0.5 * fps)

    def step(voice: VoiceInput):
        clock.t += dt
        frame, info = director.step(voice)
        infos.append(info)
        if keep_frames:
            frames.append(_small_bgr(frame, cfg, frame_width))
            if len(frames) > 12 * fps:
                del frames[0]
        return info

    for _ in range(int(warmup_s * fps)):
        info = step(before)
    # Para callar→hablar el punto de partida debe ser silencio estable.
    for _ in range(4 * fps):
        if infos and infos[-1].state is not DirectorState.TRANSITION:
            break
        step(before)

    costs_before = len(director.stats.entry_costs)
    if scenario.startswith("gesto"):
        res = director.request_gesture(gesture)
        if not res.accepted:
            rep.ok, rep.message = False, res.message
            return rep
        # Se espera a que el gesto esté en pantalla; si va a habla, se habla
        # mientras dura (la voz llega a mitad del gesto, que es lo normal).
        started = False
        for _ in range(30 * fps):
            if cancel is not None and cancel():
                rep.ok, rep.message = False, "Cancelado."
                return rep
            info = step(_SIL if not started else after)
            if info.state is DirectorState.GESTURE:
                started = True
            elif started and info.state is not DirectorState.GESTURE:
                break
        else:
            rep.ok, rep.message = False, "El gesto no terminó en 30 s."
            return rep
        # `infos[-1]` es el primer fotograma tras el gesto: el cambio empieza ahí.
        switch = len(infos) - 1
    else:
        switch = len(infos)

    origin_info = infos[switch - 1] if switch >= 1 else infos[-1]
    rep.origin, rep.origin_frame = origin_info.clip, origin_info.frame_index
    start = end = None
    i = switch
    while True:
        if i >= len(infos):
            if cancel is not None and cancel():
                rep.ok, rep.message = False, "Cancelado."
                return rep
            if len(infos) - switch > 8 * fps:
                break
            step(after)
            continue
        info = infos[i]
        if start is None and info.state is DirectorState.TRANSITION:
            start = i
            rep.anchor = bool(info.anchor)
        elif start is not None and info.state is not DirectorState.TRANSITION:
            end = i
            break
        i += 1
    if start is None or end is None:
        # Sin fundido: el destino ya era el clip en curso (p. ej. gesto que
        # termina en silencio y el silencio sigue), cuenta como espera nula.
        last = infos[-1]
        rep.destination, rep.destination_frame = last.clip, last.frame_index
        rep.message = "Sin fundido: continúa el mismo clip."
        rep.fallback = False
    else:
        dest = infos[end]
        rep.destination, rep.destination_frame = dest.clip, dest.frame_index
        rep.wait_ms = (start - switch) * 1000.0 / fps
        rep.fade_ms = (end - start) * 1000.0 / fps
        rep.fallback = not rep.anchor
    new_costs = list(director.stats.entry_costs)[costs_before:]
    if new_costs:
        rep.cost = float(new_costs[-1])
        rep.high_cost = rep.cost > Director.COST_THRESHOLD
    for _ in range(int(0.5 * fps)):
        step(after)
    if keep_frames:
        first = max(0, len(frames) - (len(infos) - switch) - keep_before)
        rep.frames = frames[first:]
    return rep


@dataclass
class TrialSummary:
    runs: int = 0
    failed: int = 0
    anchors: int = 0
    fallbacks: int = 0
    no_fade: int = 0
    waits_ms: list[float] = field(default_factory=list)
    fades_ms: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    high_cost: int = 0
    by_scenario: dict[str, dict[str, float]] = field(default_factory=dict)

    @staticmethod
    def _pct(values: list[float], q: float) -> float:
        return float(np.percentile(values, q)) if values else float("nan")

    def lines(self) -> list[str]:
        p = self._pct
        out = [
            f"Ensayos: {self.runs}  (fallidos {self.failed}, sin fundido {self.no_fade})",
            f"Entradas por ancla: {self.anchors}   fundidos de reserva: {self.fallbacks}",
            f"Espera hasta el fundido: p50 {p(self.waits_ms, 50):.0f} ms · "
            f"p95 {p(self.waits_ms, 95):.0f} ms · máx {max(self.waits_ms, default=0):.0f} ms",
            f"Duración del fundido: p50 {p(self.fades_ms, 50):.0f} ms · "
            f"p95 {p(self.fades_ms, 95):.0f} ms",
            f"Coste de discontinuidad: p50 {p(self.costs, 50):.2f} · p95 {p(self.costs, 95):.2f} · "
            f"máx {max(self.costs, default=0):.2f} · por encima de {Director.COST_THRESHOLD:g}: "
            f"{self.high_cost}",
        ]
        for name, d in self.by_scenario.items():
            out.append(
                f"  {SCENARIOS[name]:<18} n={int(d['n'])}  coste p50 {d['cost_p50']:.2f} "
                f"p95 {d['cost_p95']:.2f}  espera p95 {d['wait_p95']:.0f} ms  reserva {int(d['fallback'])}"
            )
        return out


def measure(
    cfg: VideoConfig,
    silent: Sequence[DirectorClip],
    speaking: Sequence[DirectorClip],
    gestures: dict[str, DirectorClip] | None = None,
    *,
    repeats: int = 20,
    speaking_actions: Sequence[str] = (),
    progress: Callable[[int, int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> TrialSummary:
    """Repite cada escenario `repeats` veces con semillas y gestos distintos."""
    summary = TrialSummary()
    gestures = dict(gestures or {})
    names = sorted(g for g in gestures if g not in speaking_actions) or sorted(gestures)
    scenarios = [s for s in SCENARIOS if gestures or not s.startswith("gesto")]
    total = repeats * len(scenarios)
    done = 0
    for scenario in scenarios:
        costs: list[float] = []
        waits: list[float] = []
        fallback = 0
        for k in range(repeats):
            if cancel is not None and cancel():
                return summary
            gesture = names[k % len(names)] if names and scenario.startswith("gesto") else ""
            rep = run_trial(
                cfg, silent, speaking, gestures, scenario, gesture=gesture, seed=1000 + k,
                speaking_actions=speaking_actions, keep_frames=False, cancel=cancel,
            )
            done += 1
            if progress:
                progress(done, total, f"{SCENARIOS[scenario]} {k + 1}/{repeats}")
            summary.runs += 1
            if not rep.ok:
                summary.failed += 1
                continue
            if rep.message.startswith("Sin fundido"):
                summary.no_fade += 1
            else:
                summary.waits_ms.append(rep.wait_ms)
                summary.fades_ms.append(rep.fade_ms)
                waits.append(rep.wait_ms)
                if rep.anchor:
                    summary.anchors += 1
                else:
                    summary.fallbacks += 1
                    fallback += 1
            if rep.cost is not None:
                summary.costs.append(rep.cost)
                costs.append(rep.cost)
                summary.high_cost += int(rep.high_cost)
        summary.by_scenario[scenario] = {
            "n": float(repeats),
            "cost_p50": TrialSummary._pct(costs, 50),
            "cost_p95": TrialSummary._pct(costs, 95),
            "wait_p95": TrialSummary._pct(waits, 95),
            "fallback": float(fallback),
        }
    return summary
