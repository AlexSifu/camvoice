"""Pruebas de resistencia (soak) para reuniones largas.

Dos modos, los dos sin micrófono, sin cámara y sin conexión:

- **Tiempo real** (`run_soak`): la sesión completa (caché, director, motor de
  render, cámara nula y monitor de CPU/memoria) recibe una voz sintética
  guionizada en lugar del micrófono y peticiones de gesto periódicas. Mide FPS,
  fotogramas tardíos, transiciones, latencia de gestos y la pendiente de
  memoria. Dura lo que se le pida (2 h = 7200 s); es la prueba de verdad.
- **Simulado** (`simulate`): solo el director, con un reloj falso que avanza
  1/fps por paso. Recorre horas de reunión en minutos para comprobar la lógica
  a largo plazo (estados, gestos que siempre vuelven, sin repetición, sin
  saltos bruscos, memoria de Python acotada). No dice nada de FPS reales.

La voz sintética NO es audio: es una secuencia determinista (con semilla) de
estados y aperturas de boca que imita una reunión de tres personas: silencios
largos mientras hablan otros, respuestas cortas («sí»), intervenciones
seguidas con pausas y algún monólogo.
"""

from __future__ import annotations

import gc
import math
import random
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, field

from .audio.gate import VoiceState
import numpy as np

from .video.director import Director, VoiceInput

__all__ = ["VoiceScript", "SoakResult", "SimResult", "run_soak", "simulate"]


class VoiceScript:
    """Voz sintética determinista: `at(t)` -> VoiceInput.

    Segmentos alternos de silencio y habla. Dentro de la habla, la boca sigue
    sílabas de ~5 Hz y hay pausas breves (entre frases) con la boca cerrada,
    igual que hace el VAD real con `release_ms`.
    """

    # (probabilidad, duración mínima, duración máxima) de cada tipo de turno.
    SILENCE = ((0.55, 4.0, 25.0), (0.35, 25.0, 120.0), (0.10, 0.6, 3.0))
    SPEECH = ((0.35, 0.3, 0.9), (0.45, 2.0, 20.0), (0.20, 20.0, 90.0))

    def __init__(self, seed: int = 7, duration_s: float = 7200.0) -> None:
        rng = random.Random(seed)
        self._segments: list[tuple[float, float, bool]] = []
        t, speaking = 0.0, False
        while t < duration_s + 1.0:
            table = self.SPEECH if speaking else self.SILENCE
            r, acc = rng.random(), 0.0
            for p, lo, hi in table:
                acc += p
                if r <= acc:
                    break
            length = rng.uniform(lo, hi)
            self._segments.append((t, t + length, speaking))
            t += length
            speaking = not speaking
        self._rng_seed = seed
        self._i = 0

    def _segment(self, t: float) -> tuple[float, float, bool]:
        segs = self._segments
        if t < segs[self._i][0]:
            self._i = 0
        while self._i < len(segs) - 1 and t >= segs[self._i][1]:
            self._i += 1
        return segs[self._i]

    def at(self, t: float) -> VoiceInput:
        start, _end, speaking = self._segment(t)
        if not speaking:
            return VoiceInput(VoiceState.SILENT, 0.0)
        u = t - start
        # Micro-pausa de 0,4 s cada ~3,7 s de frase: la puerta sigue en SPEAKING
        # y el detector de micro-pausas la marca (boca cerrada, pose de escucha).
        if u > 1.0 and (u % 3.7) < 0.4:
            return VoiceInput(VoiceState.SPEAKING, 0.0, pause=(u % 3.7) >= 0.2)
        syll = 0.5 + 0.5 * math.sin(2 * math.pi * 5.1 * u + self._rng_seed)
        return VoiceInput(VoiceState.SPEAKING, round(min(1.0, 0.15 + 0.85 * syll), 3))

    def speech_fraction(self, duration_s: float) -> float:
        total = 0.0
        for a, b, sp in self._segments:
            if sp and a < duration_s:
                total += min(b, duration_s) - a
        return total / max(1e-9, duration_s)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return -1.0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q / 100.0 * (len(v) - 1))))]


# --------------------------------------------------------------------------- #
# Tiempo real
# --------------------------------------------------------------------------- #


@dataclass
class SoakResult:
    duration_s: float = 0.0
    mode: str = ""
    frames_sent: int = 0
    fps_target: int = 0
    fps_mean: float = 0.0
    fps_min: float = 0.0
    late_frames: int = 0
    resyncs: int = 0
    """Veces que el ritmo se resincronizó saltándose fotogramas (perdidos)."""

    frame_ms_p95: float = 0.0
    transitions: int = 0
    jumps: int = 0
    morphs: int = 0
    anchor_transitions: int = 0
    fallback_transitions: int = 0
    segment_changes: int = 0
    interrupted_transitions: int = 0
    discontinuities: int = 0
    degraded_events: int = 0
    gestures_requested: int = 0
    gestures_accepted: int = 0
    gesture_latency_p50_ms: float = -1.0
    gesture_latency_p95_ms: float = -1.0
    gesture_latency_max_ms: float = -1.0
    voice_age_p95_ms: float = -1.0
    cpu_mean: float = 0.0
    cpu_p95: float = 0.0
    rss_start_mb: float = 0.0
    rss_end_mb: float = 0.0
    rss_slope_mb_per_min: float = 0.0
    memory_verdict: str = ""
    errors: list[str] = field(default_factory=list)
    csv_path: str = ""

    def render(self) -> str:
        lines = [
            f"Duración medida:        {self.duration_s:.0f} s   modo {self.mode}",
            f"Fotogramas emitidos:    {self.frames_sent}",
            f"FPS medio / mínimo:     {self.fps_mean:.2f} / {self.fps_min:.2f} (objetivo {self.fps_target})",
            f"Fotogramas tardíos:     {self.late_frames}   resincronizaciones {self.resyncs}   "
            f"p95 ms/fotograma {self.frame_ms_p95:.2f}",
            f"Transiciones:           {self.transitions} (en ancla {self.anchor_transitions}, "
            f"fundido corto {self.fallback_transitions}, interrumpidas {self.interrupted_transitions})",
            f"Continuidad:            discontinuidades {self.discontinuities}   saltos {self.jumps}   "
            f"cambios de segmento {self.segment_changes}   morph {self.morphs}",
            f"Gestos pedidos/hechos:  {self.gestures_requested} / {self.gestures_accepted}",
            "Latencia gesto (ms):    "
            + (
                f"p50 {self.gesture_latency_p50_ms:.0f}  p95 {self.gesture_latency_p95_ms:.0f}  "
                f"máx {self.gesture_latency_max_ms:.0f}"
                if self.gesture_latency_max_ms >= 0
                else "sin medidas"
            ),
            "Edad dato de voz p95:   "
            + (f"{self.voice_age_p95_ms:.1f} ms" if self.voice_age_p95_ms >= 0 else "n/d (voz sintética)"),
            f"CPU (% de un núcleo):   media {self.cpu_mean:.1f}  p95 {self.cpu_p95:.1f}",
            f"Memoria RSS:            {self.rss_start_mb:.0f} -> {self.rss_end_mb:.0f} MB "
            f"({self.rss_slope_mb_per_min:+.2f} MB/min, {self.memory_verdict})",
            f"Modo degradado:         {self.degraded_events} eventos",
        ]
        if self.csv_path:
            lines.append(f"CSV de métricas:        {self.csv_path}")
        for e in self.errors:
            lines.append(f"Aviso: {e}")
        return "\n".join(lines)


def run_soak(
    session,  # noqa: ANN001 - controller.Session ya preparada
    duration_s: float,
    *,
    gesture_every_s: float = 45.0,
    seed: int = 7,
    progress: Callable[[float, object], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SoakResult:
    """Ejecuta la sesión con voz sintética y NullSink durante `duration_s`."""
    from .output.sink import NullSink

    script = VoiceScript(seed, duration_s)
    rng = random.Random(seed + 1)
    t0 = time.perf_counter()

    def voice() -> VoiceInput:
        return script.at(time.perf_counter() - t0)

    session.cfg.metrics.enabled = True
    session.cfg.metrics.interval_s = min(session.cfg.metrics.interval_s, 1.0)
    session.start(sink=NullSink(), voice_source=voice)
    t0 = time.perf_counter()
    result = SoakResult(fps_target=session.cfg.video.fps)
    next_gesture = gesture_every_s
    voice_ages: list[float] = []
    try:
        while True:
            now = time.perf_counter() - t0
            if now >= duration_s or (should_stop is not None and should_stop()):
                break
            if gesture_every_s > 0 and now >= next_gesture:
                available = session.engine.available_gestures() if session.engine else []
                if available:
                    result.gestures_requested += 1
                    if session.request_gesture(rng.choice(available)).accepted:
                        result.gestures_accepted += 1
                next_gesture += gesture_every_s
            status = session.status()
            if status.render.voice_age_ms >= 0:
                voice_ages.append(status.render.voice_age_ms)
            if status.render.error:
                if status.render.error not in result.errors:
                    result.errors.append(status.render.error)
            if progress is not None:
                progress(now, status)
            time.sleep(0.5)
    finally:
        status = session.status()
        latencies = []
        engine = session.engine
        if engine is not None and engine.director is not None:
            latencies = list(engine.director.stats.gesture_latencies_ms)
        summary = session.metrics.summary() if session.metrics else None
        session.stop()

    r = status.render
    result.duration_s = time.perf_counter() - t0
    result.mode = r.mode
    result.frames_sent = r.frames_sent
    result.transitions = r.transitions
    result.jumps = r.jumps
    result.morphs = r.morphs
    result.resyncs = r.resyncs
    result.anchor_transitions = r.anchor_transitions
    result.fallback_transitions = r.fallback_transitions
    result.segment_changes = r.segment_changes
    result.interrupted_transitions = r.interrupted_transitions
    result.discontinuities = r.discontinuities
    result.degraded_events = r.degraded_events
    result.gesture_latency_p50_ms = _pct(latencies, 50)
    result.gesture_latency_p95_ms = _pct(latencies, 95)
    result.gesture_latency_max_ms = max(latencies) if latencies else -1.0
    result.voice_age_p95_ms = _pct(voice_ages, 95)
    if summary is not None:
        result.fps_mean = summary.fps_mean
        result.fps_min = summary.fps_min
        result.late_frames = summary.late_frames
        result.frame_ms_p95 = summary.frame_ms_p95
        result.cpu_mean = summary.cpu_mean
        result.cpu_p95 = summary.cpu_p95
        result.rss_start_mb = summary.rss_start_mb
        result.rss_end_mb = summary.rss_end_mb
        result.rss_slope_mb_per_min = summary.rss_slope_mb_per_min
        result.memory_verdict = summary.memory_verdict
        result.csv_path = summary.csv_path
    return result


# --------------------------------------------------------------------------- #
# Simulado (reloj falso)
# --------------------------------------------------------------------------- #


@dataclass
class SimResult:
    simulated_s: float = 0.0
    wall_s: float = 0.0
    steps: int = 0
    state_steps: dict[str, int] = field(default_factory=dict)
    transitions: int = 0
    jumps: int = 0
    morphs: int = 0
    anchor_transitions: int = 0
    fallback_transitions: int = 0
    segment_changes: int = 0
    interrupted_transitions: int = 0
    discontinuities: int = 0
    """Fotogramas no consecutivos fuera de una transición (debe ser 0)."""

    pauses: int = 0
    paused_frames: int = 0
    pause_frame_changes: int = 0
    """Fotogramas en pausa distintos de la imagen congelada (debe ser 0)."""

    black_frames: int = 0
    """Fotogramas casi negros emitidos (debe ser 0)."""

    gesture_wait_max_ms: float = -1.0
    """Espera máxima petición -> inicio de un gesto pedido hablando."""

    gestures_requested: int = 0
    gestures_played: int = 0
    gestures_rejected: int = 0
    gestures_stuck: int = 0
    """Gestos que no volvieron a voz/silencio en un plazo razonable (debe ser 0)."""

    auto_shots: int = 0
    """Frases breves (#6, #7, #8) lanzadas solas por el director."""

    auto_shots_cut: int = 0
    max_step_distance: float = 0.0
    p99_step_distance: float = 0.0
    longest_frozen_s: float = 0.0
    py_mem_start_kb: float = 0.0
    py_mem_end_kb: float = 0.0
    py_mem_peak_kb: float = 0.0
    speech_fraction: float = 0.0

    def render(self) -> str:
        states = ", ".join(f"{k} {v / max(1, self.steps):.1%}" for k, v in sorted(self.state_steps.items()))
        return "\n".join(
            [
                f"Reunión simulada:       {self.simulated_s / 3600:.2f} h en {self.wall_s:.0f} s reales "
                f"({self.steps} fotogramas)",
                f"Voz en el guion:        {self.speech_fraction:.0%} del tiempo",
                f"Tiempo por estado:      {states}",
                f"Transiciones:           {self.transitions} (en ancla {self.anchor_transitions}, "
                f"fundido corto {self.fallback_transitions}, interrumpidas {self.interrupted_transitions})",
                f"Continuidad:            discontinuidades {self.discontinuities}   saltos {self.jumps}   "
                f"cambios de segmento {self.segment_changes}   morph {self.morphs}",
                f"Gestos pedidos/hechos:  {self.gestures_requested} / {self.gestures_played} "
                f"(rechazados {self.gestures_rejected}, atascados {self.gestures_stuck})",
                "Espera gesto hablando:  "
                + (f"máx {self.gesture_wait_max_ms:.0f} ms" if self.gesture_wait_max_ms >= 0 else "sin medidas"),
                f"Frases automáticas:     {self.auto_shots} (cortadas al callar {self.auto_shots_cut})",
                f"Pausas manuales:        {self.pauses} ({self.paused_frames} fotogramas; "
                f"cambios en pausa {self.pause_frame_changes}; negros {self.black_frames})",
                f"Cambio por fotograma:   máx {self.max_step_distance:.3f}  p99 {self.p99_step_distance:.3f}",
                f"Imagen congelada máx:  {self.longest_frozen_s:.1f} s",
                f"Memoria Python:         {self.py_mem_start_kb:.0f} -> {self.py_mem_end_kb:.0f} KB "
                f"(pico {self.py_mem_peak_kb:.0f} KB)",
            ]
        )


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def simulate(
    make_director: Callable[[Callable[[], float]], Director],
    simulated_s: float,
    *,
    fps: int,
    gesture_every_s: float = 45.0,
    seed: int = 7,
    measure_continuity: bool = True,
    pause_every_s: float = 0.0,
    pause_len_s: float = 5.0,
) -> SimResult:
    """Recorre `simulated_s` de reunión con un reloj falso.

    `make_director(clock)` construye el director con ese reloj, para que la
    espera entre gestos, la latencia y el asentimiento automático usen el
    tiempo simulado. Con `pause_every_s > 0` se activa la pausa manual
    durante `pause_len_s` cada `pause_every_s` segundos.
    """
    from .video.index import descriptor, distances, luma

    clock = _FakeClock()
    director = make_director(clock)
    script = VoiceScript(seed, simulated_s)
    rng = random.Random(seed + 1)
    res = SimResult(simulated_s=simulated_s, speech_fraction=script.speech_fraction(simulated_s))
    dt = 1.0 / fps
    steps = int(simulated_s * fps)
    next_gesture = gesture_every_s
    gesture_since: float | None = None
    # Preasignado ANTES de tracemalloc: así la medida de memoria es la del
    # director y no la de esta lista de resultados.
    step_d = np.zeros(steps // 3 + 2, dtype=np.float32)
    n_d = 0
    prev_desc = None
    fmt = director.cfg.pixel_format
    height = director.cfg.height
    same_key, same_run, longest = None, 0, 0
    next_pause = pause_every_s if pause_every_s > 0 else float("inf")
    pause_until = -1.0
    frozen: np.ndarray | None = None
    y_rows = slice(0, height) if fmt == "nv12" else slice(None)

    gc.collect()
    tracemalloc.start()
    res.py_mem_start_kb = tracemalloc.get_traced_memory()[0] / 1024
    wall0 = time.perf_counter()
    try:
        for i in range(steps):
            clock.t = i * dt
            if gesture_every_s > 0 and clock.t >= next_gesture:
                avail = director.available_gestures()
                if avail:
                    res.gestures_requested += 1
                    director.request_gesture(rng.choice(avail))
                next_gesture += gesture_every_s
            if clock.t >= next_pause:
                director.pause_video()
                pause_until = clock.t + pause_len_s
                next_pause += pause_every_s
            elif pause_until >= 0 and clock.t >= pause_until:
                director.resume_video()
                pause_until = -1.0
            frame, info = director.step(script.at(clock.t))
            key = info.state.value
            res.state_steps[key] = res.state_steps.get(key, 0) + 1
            if info.paused:
                res.paused_frames += 1
                if frozen is None:
                    frozen = frame.copy()
                elif not np.array_equal(frame, frozen):
                    res.pause_frame_changes += 1
            else:
                frozen = None
            if i % 5 == 0 and float(frame[y_rows].mean()) < 8.0:
                res.black_frames += 1

            # Un gesto (incluida su transición) no debe durar indefinidamente.
            if info.gesture:
                gesture_since = clock.t if gesture_since is None else gesture_since
                if clock.t - gesture_since > 30.0:
                    res.gestures_stuck += 1
                    gesture_since = clock.t
            else:
                gesture_since = None

            seg = (info.clip, info.frame_index) if not (info.blended or info.paused) else None
            if seg is not None and same_key is not None and seg == same_key:
                same_run += 1
                longest = max(longest, same_run)
            else:
                same_run = 0
            same_key = seg

            if measure_continuity and i % 3 == 0:
                d = descriptor(luma(frame, fmt, height))
                if prev_desc is not None:
                    step_d[n_d] = float(distances(prev_desc[None, :], d)[0])
                    n_d += 1
                prev_desc = d
            if i % (fps * 600) == 0:
                cur, peak = tracemalloc.get_traced_memory()
                res.py_mem_peak_kb = max(res.py_mem_peak_kb, peak / 1024)
    finally:
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    res.wall_s = time.perf_counter() - wall0
    res.py_mem_end_kb = cur / 1024
    res.py_mem_peak_kb = max(res.py_mem_peak_kb, peak / 1024)
    res.steps = steps
    st = director.stats
    res.transitions = director.transitions
    res.jumps = st.jumps
    res.morphs = st.morphs
    res.anchor_transitions = st.anchor_transitions
    res.fallback_transitions = st.fallback_transitions
    res.segment_changes = st.segment_changes
    res.interrupted_transitions = st.interrupted_transitions
    res.discontinuities = st.discontinuities
    res.pauses = st.pauses
    waits = list(st.gesture_waits_ms)
    res.gesture_wait_max_ms = max(waits) if waits else -1.0
    res.gestures_played = st.gestures_played
    res.gestures_rejected = st.gestures_rejected
    res.auto_shots = st.auto_shots
    res.auto_shots_cut = st.auto_shots_cut
    if n_d:
        res.max_step_distance = float(step_d[:n_d].max())
        res.p99_step_distance = float(np.percentile(step_d[:n_d], 99))
    res.longest_frozen_s = longest / fps
    return res
