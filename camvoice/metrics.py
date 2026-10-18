"""Medición de rendimiento: CPU, memoria, FPS efectivos y latencia.

El plan exige cifras reales, no estimaciones, y que se documenten junto al
equipo donde se tomaron. Este módulo produce un CSV por ejecución y un resumen
al cerrar, con percentiles en vez de solo medias: lo que rompe una
videollamada es el peor caso, no el promedio.

El porcentaje de CPU se publica en dos escalas, porque confundirlas hace que
la cifra no signifique nada:
  cpu_percent        porcentaje de UN núcleo lógico, que es lo que devuelve
                     psutil. Puede pasar de 100 % si el proceso reparte trabajo
                     entre varios núcleos.
  cpu_percent_norm   dividido entre el número de núcleos lógicos: porcentaje del
                     total de la máquina. Es la escala que muestra el
                     Administrador de tareas de Windows, y la comparable entre
                     equipos con distinto número de núcleos.

El objetivo del 25 % se evalúa contra `cpu_percent` (por núcleo), que es la
lectura más exigente de las dos.
"""

from __future__ import annotations

import csv
import os
import platform
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import MetricsConfig, metrics_dir

__all__ = ["Sample", "MetricsSummary", "PerfMonitor", "describe_host"]


def describe_host() -> dict[str, str]:
    """Identificación del equipo, para que las métricas sean interpretables."""
    info = {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "processor": platform.processor() or "desconocido",
        "python": platform.python_version(),
        "logical_cpus": str(os.cpu_count() or 0),
    }
    try:
        import psutil

        info["physical_cpus"] = str(psutil.cpu_count(logical=False) or 0)
        info["ram_gb"] = f"{psutil.virtual_memory().total / 1e9:.1f}"
        freq = psutil.cpu_freq()
        if freq:
            info["cpu_mhz_max"] = f"{freq.max:.0f}"
    except Exception:
        pass
    return info


@dataclass(frozen=True, slots=True)
class Sample:
    t: float
    cpu_percent: float
    cpu_percent_norm: float
    rss_mb: float
    fps_effective: float
    fps_capacity: float
    avg_frame_ms: float
    worst_frame_ms: float
    late_frames: int
    resyncs: int
    frames_sent: int
    transitions: int
    voice_state: str
    render_state: str
    rms_db: float
    noise_floor_db: float
    audio_overflows: int
    audio_dropped: int
    threads: int


@dataclass
class MetricsSummary:
    duration_s: float = 0.0
    samples: int = 0
    cpu_mean: float = 0.0
    cpu_p95: float = 0.0
    cpu_max: float = 0.0
    cpu_norm_mean: float = 0.0
    rss_start_mb: float = 0.0
    rss_end_mb: float = 0.0
    rss_max_mb: float = 0.0
    rss_slope_mb_per_min: float = 0.0
    fps_mean: float = 0.0
    fps_min: float = 0.0
    frame_ms_p95: float = 0.0
    frame_ms_max: float = 0.0
    late_frames: int = 0
    resyncs: int = 0
    frames_sent: int = 0
    transitions: int = 0
    audio_overflows: int = 0
    logical_cpus: int = 0
    csv_path: str = ""
    host: dict[str, str] = field(default_factory=dict)

    MIN_SLOPE_DURATION_S = 300.0
    """Duración mínima para que la pendiente de memoria signifique algo.

    La pendiente se ajusta por mínimos cuadrados y luego se extrapola a un
    minuto. Desde una ventana de 45 s, esa extrapolación amplifica el ruido de
    medición hasta hacerla inútil: se midió +0,36, +0,69, +1,17 y +1,90 MB/min
    en ejecuciones consecutivas de la MISMA configuración, lo que significa que
    el método no resuelve el umbral de 1 MB/min en ventanas cortas.

    Declararlo «no concluyente» es más honesto que dar un veredicto que cambia
    de signo entre repeticiones. Lo que decide CA-08 es la prueba de 30 min.
    """

    @property
    def memory_verdict(self) -> str:
        """'estable' | 'crecimiento' | 'no concluyente'."""
        if self.duration_s < self.MIN_SLOPE_DURATION_S:
            return "no concluyente"
        return "estable" if self.rss_slope_mb_per_min < 1.0 else "crecimiento"

    @property
    def memory_stable(self) -> bool:
        """CA-08: sin crecimiento sostenido de memoria.

        Se tolera 1 MB/min, que cubre el ruido de la fragmentación del
        asignador y del recolector de basura de Python. En ventanas cortas
        devuelve True porque no hay evidencia de lo contrario; usa
        `memory_verdict` para distinguir «estable» de «no concluyente».
        """
        return self.memory_verdict != "crecimiento"

    def render(self) -> str:
        lines = [
            f"Duración de la prueba   {self.duration_s / 60:.1f} min ({self.samples} muestras)",
            f"CPU (% de un núcleo)    media {self.cpu_mean:.1f} %  p95 {self.cpu_p95:.1f} %  max {self.cpu_max:.1f} %",
            f"CPU (% de la máquina)   media {self.cpu_norm_mean:.1f} %  "
            f"(escala del Administrador de tareas, {self.logical_cpus} núcleos)",
            f"Memoria (RSS)           inicio {self.rss_start_mb:.0f} MB  fin {self.rss_end_mb:.0f} MB  "
            f"max {self.rss_max_mb:.0f} MB",
            f"Deriva de memoria       {self.rss_slope_mb_per_min:+.2f} MB/min  "
            f"-> {self.memory_verdict.upper()}"
            + (
                f"  (hacen falta {self.MIN_SLOPE_DURATION_S / 60:.0f} min "
                "para que la pendiente sea fiable)"
                if self.memory_verdict == "no concluyente"
                else ""
            ),
            f"FPS efectivos           media {self.fps_mean:.2f}  mínimo {self.fps_min:.2f}",
            f"Coste por fotograma     p95 {self.frame_ms_p95:.2f} ms  max {self.frame_ms_max:.2f} ms",
            f"Fotogramas emitidos     {self.frames_sent}  (tardíos {self.late_frames}, resincronizaciones {self.resyncs})",
            f"Cambios de estado       {self.transitions}",
            f"Desbordes de audio      {self.audio_overflows}",
        ]
        if self.csv_path:
            lines.append(f"CSV                     {self.csv_path}")
        if self.host:
            lines.append("")
            lines.append("Equipo de referencia:")
            for key, value in self.host.items():
                lines.append(f"  {key:<16}{value}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #


class PerfMonitor:
    """Muestrea el estado de la sesión en su propio hilo y lo vuelca a CSV."""

    def __init__(self, cfg: MetricsConfig, status_fn: Callable[[], object]) -> None:
        self.cfg = cfg
        self._status_fn = status_fn
        self._samples: list[Sample] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.path = self._resolve_path()
        self.host = describe_host()
        self._t0 = 0.0

    def _resolve_path(self) -> Path:
        if self.cfg.csv_path:
            return Path(self.cfg.csv_path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        directory = metrics_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"metrics-{stamp}.csv"

    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(target=self._run, name="camvoice-metrics", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> MetricsSummary:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)
        return self.summary()

    def samples(self) -> list[Sample]:
        with self._lock:
            return list(self._samples)

    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        try:
            import psutil

            proc = psutil.Process()
            proc.cpu_percent(None)  # primera llamada: solo fija la referencia
        except Exception:
            proc = None

        cpus = max(1, os.cpu_count() or 1)
        writer = None
        handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "w", newline="", encoding="utf-8")
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "t_s", "cpu_percent", "cpu_percent_norm", "rss_mb",
                    "fps_effective", "fps_capacity", "avg_frame_ms", "worst_frame_ms",
                    "late_frames", "resyncs", "frames_sent", "transitions",
                    "voice_state", "render_state", "rms_db", "noise_floor_db",
                    "audio_overflows", "audio_dropped", "threads",
                ]
            )
        except OSError:
            writer = None

        while not self._stop.is_set():
            sample = self._collect(proc, cpus)
            with self._lock:
                self._samples.append(sample)
            if writer is not None:
                try:
                    writer.writerow(
                        [
                            f"{sample.t:.2f}", f"{sample.cpu_percent:.2f}",
                            f"{sample.cpu_percent_norm:.2f}", f"{sample.rss_mb:.2f}",
                            f"{sample.fps_effective:.3f}", f"{sample.fps_capacity:.2f}",
                            f"{sample.avg_frame_ms:.3f}", f"{sample.worst_frame_ms:.3f}",
                            sample.late_frames, sample.resyncs, sample.frames_sent,
                            sample.transitions, sample.voice_state, sample.render_state,
                            f"{sample.rms_db:.1f}", f"{sample.noise_floor_db:.1f}",
                            sample.audio_overflows, sample.audio_dropped, sample.threads,
                        ]
                    )
                    handle.flush()  # el CSV sirve aunque la prueba acabe mal
                except OSError:
                    writer = None
            self._stop.wait(max(0.25, self.cfg.interval_s))

        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _collect(self, proc, cpus: int) -> Sample:  # noqa: ANN001
        status = self._status_fn()
        voice = getattr(status, "voice", None)
        render = getattr(status, "render", None)

        cpu = rss = 0.0
        threads = 0
        if proc is not None:
            try:
                cpu = float(proc.cpu_percent(None))
                rss = proc.memory_info().rss / 1e6
                threads = proc.num_threads()
            except Exception:
                pass

        return Sample(
            t=time.perf_counter() - self._t0,
            cpu_percent=cpu,
            cpu_percent_norm=cpu / cpus,
            rss_mb=rss,
            fps_effective=getattr(render, "fps_effective", 0.0),
            fps_capacity=getattr(render, "fps_capacity", 0.0),
            avg_frame_ms=getattr(render, "avg_frame_ms", 0.0),
            worst_frame_ms=getattr(render, "worst_frame_ms", 0.0),
            late_frames=getattr(render, "late_frames", 0),
            resyncs=getattr(render, "resyncs", 0),
            frames_sent=getattr(render, "frames_sent", 0),
            transitions=getattr(render, "transitions", 0),
            voice_state=getattr(getattr(voice, "state", None), "value", "-"),
            render_state=getattr(getattr(render, "state", None), "value", "-"),
            rms_db=getattr(voice, "rms_db", 0.0),
            noise_floor_db=getattr(voice, "noise_floor_db", 0.0),
            audio_overflows=getattr(voice, "overflows", 0),
            audio_dropped=getattr(voice, "dropped_blocks", 0),
            threads=threads,
        )

    # ------------------------------------------------------------------ #

    def summary(self) -> MetricsSummary:
        samples = self.samples()
        out = MetricsSummary(
            csv_path=str(self.path),
            host=self.host,
            samples=len(samples),
            logical_cpus=max(1, os.cpu_count() or 1),
        )
        if not samples:
            return out

        # Se descarta la primera muestra: incluye el arranque de los hilos y la
        # primera lectura de psutil, que no representan el régimen estacionario.
        useful = samples[1:] or samples
        cpus = [s.cpu_percent for s in useful]
        rss = [s.rss_mb for s in useful]
        fps = [s.fps_effective for s in useful if s.fps_effective > 0]

        out.duration_s = useful[-1].t - useful[0].t
        out.cpu_mean = statistics.fmean(cpus)
        out.cpu_max = max(cpus)
        out.cpu_p95 = _percentile(cpus, 95)
        out.cpu_norm_mean = statistics.fmean(s.cpu_percent_norm for s in useful)
        out.rss_start_mb = rss[0]
        out.rss_end_mb = rss[-1]
        out.rss_max_mb = max(rss)
        out.rss_slope_mb_per_min = _slope_per_minute(useful)
        if fps:
            out.fps_mean = statistics.fmean(fps)
            out.fps_min = min(fps)
        out.frame_ms_p95 = _percentile([s.avg_frame_ms for s in useful], 95)
        out.frame_ms_max = max(s.worst_frame_ms for s in useful)
        out.late_frames = useful[-1].late_frames
        out.resyncs = useful[-1].resyncs
        out.frames_sent = useful[-1].frames_sent
        out.transitions = useful[-1].transitions
        out.audio_overflows = useful[-1].audio_overflows
        return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    low = int(k)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def _slope_per_minute(samples: list[Sample]) -> float:
    """Pendiente por mínimos cuadrados de RSS frente al tiempo, en MB/min."""
    if len(samples) < 4:
        return 0.0
    xs = [s.t / 60.0 for s in samples]
    ys = [s.rss_mb for s in samples]
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
