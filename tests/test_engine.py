"""Pruebas del motor de render, el reloj y la configuración.

El motor se prueba con un sumidero falso y clips en memoria, así que no hace
falta ni micrófono ni cámara virtual: lo que se verifica es la cadencia, la
continuidad y el comportamiento ante fallos de la salida.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from camvoice.audio.gate import VoiceState
from camvoice.config import AppConfig, VadConfig, VideoConfig
from camvoice.metrics import MetricsSummary, _percentile, _slope_per_minute, describe_host
from camvoice.output.sink import NullSink, SinkError
from camvoice.video.engine import Pacer, RenderEngine


class RecordingSink:
    """Sumidero que guarda lo que recibe y puede fallar a voluntad."""

    name = "prueba"

    def __init__(self, fail_after: int | None = None) -> None:
        self.device = "sumidero de prueba"
        self.frames = 0
        self.received: list[float] = []
        self.closed = False
        self._fail_after = fail_after

    def send(self, frame: np.ndarray) -> None:
        if self._fail_after is not None and self.frames >= self._fail_after:
            raise SinkError("fallo simulado de la cámara virtual")
        self.frames += 1
        self.received.append(float(np.asarray(frame).mean()))

    def close(self) -> None:
        self.closed = True


def build_engine(fake_clip_factory, sink, *, fps: int = 30, state=VoiceState.SILENT):  # noqa: ANN001
    cfg = AppConfig(
        video=VideoConfig(
            width=8, height=8, fps=fps, transition_ms=100,
            pixel_format="bgr", loop_mode="loop", preview_fps=5, preview_max_width=0,
        )
    )
    holder = {"state": state}
    engine = RenderEngine(
        cfg,
        fake_clip_factory([200] * 20),
        fake_clip_factory([50] * 20),
        lambda: holder["state"],
        sink=sink,
    )
    return engine, holder, cfg


# --------------------------------------------------------------------------- #
# Pacer
# --------------------------------------------------------------------------- #


def test_el_reloj_mantiene_la_cadencia_media():
    pacer = Pacer(50.0)
    started = time.perf_counter()
    for _ in range(25):
        pacer.wait()
    elapsed = time.perf_counter() - started
    assert elapsed == pytest.approx(0.5, abs=0.12)


def test_el_reloj_no_acumula_deriva():
    """Se calcula por acumulación de objetivos, no durmiendo un delta cada vez."""
    pacer = Pacer(100.0)
    started = time.perf_counter()
    for _ in range(50):
        pacer.wait()
        time.sleep(0.001)  # trabajo simulado en cada vuelta
    elapsed = time.perf_counter() - started
    assert elapsed == pytest.approx(0.5, abs=0.15)


def test_un_retraso_grande_se_cuenta_y_reancla():
    pacer = Pacer(100.0)
    pacer.wait()
    time.sleep(0.08)  # 8 periodos de retraso
    pacer.wait()
    assert pacer.late_frames == 1
    assert pacer.resyncs == 1


# --------------------------------------------------------------------------- #
# Motor
# --------------------------------------------------------------------------- #


def test_el_motor_emite_a_la_cadencia_pedida(fake_clip_factory):
    sink = RecordingSink()
    engine, _, _ = build_engine(fake_clip_factory, sink, fps=30)
    engine.start()
    time.sleep(1.0)
    engine.stop()

    assert 22 <= sink.frames <= 38, f"emitió {sink.frames} fotogramas en 1 s a 30 FPS"
    status = engine.status()
    assert status.fps_effective == pytest.approx(30, abs=6)
    assert not status.running


def test_el_motor_sigue_el_estado_de_voz(fake_clip_factory):
    sink = RecordingSink()
    engine, holder, _ = build_engine(fake_clip_factory, sink, fps=50)
    engine.start()
    time.sleep(0.3)
    holder["state"] = VoiceState.SPEAKING
    time.sleep(0.5)
    engine.stop()

    assert sink.received[2] == pytest.approx(50, abs=1), "no arrancó en el clip neutral"
    assert sink.received[-1] == pytest.approx(200, abs=1), "no acabó en el clip de habla"
    assert any(50 < v < 200 for v in sink.received), "no hubo fundido intermedio"


def test_ningun_fotograma_queda_fuera_del_rango_de_los_clips(fake_clip_factory):
    """CA-06: sin negros ni fotogramas espurios durante los cambios."""
    sink = RecordingSink()
    engine, holder, _ = build_engine(fake_clip_factory, sink, fps=60)
    engine.start()
    for _ in range(6):
        holder["state"] = (
            VoiceState.SPEAKING if holder["state"] is VoiceState.SILENT
            else VoiceState.SILENT
        )
        time.sleep(0.12)
    engine.stop()
    assert sink.received
    assert all(50 <= v <= 200 for v in sink.received)


def test_un_fallo_de_la_salida_no_detiene_el_motor(fake_clip_factory):
    """CA-12: la cámara virtual cae, el motor sobrevive y lo informa."""
    sink = RecordingSink(fail_after=5)
    engine, _, _ = build_engine(fake_clip_factory, sink, fps=50)
    engine.start()
    time.sleep(0.6)
    running_during = engine.running
    status = engine.status()
    engine.stop()

    assert running_during, "el motor se detuvo al fallar la salida"
    assert "fallo simulado" in status.error
    assert sink.frames == 5


def test_la_vista_previa_queda_disponible(fake_clip_factory):
    engine, _, _ = build_engine(fake_clip_factory, RecordingSink(), fps=30)
    engine.start()
    time.sleep(0.4)
    preview = engine.latest_preview()
    engine.stop()
    assert preview is not None
    assert preview.shape[:2] == (8, 8)


def test_el_sumidero_nulo_tambien_cuenta_fotogramas(fake_clip_factory):
    sink = NullSink()
    engine, _, _ = build_engine(fake_clip_factory, sink, fps=60)
    engine.start()
    time.sleep(0.4)
    engine.stop()
    assert sink.frames > 10


def test_arrancar_dos_veces_no_crea_un_segundo_hilo(fake_clip_factory):
    engine, _, _ = build_engine(fake_clip_factory, RecordingSink(), fps=30)
    engine.start()
    thread = engine._thread
    engine.start()
    assert engine._thread is thread
    engine.stop()


def test_detener_sin_arrancar_no_falla(fake_clip_factory):
    engine, _, _ = build_engine(fake_clip_factory, RecordingSink())
    engine.stop()
    assert not engine.running


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #


def test_la_configuracion_va_y_vuelve_de_json(tmp_path):
    cfg = AppConfig()
    cfg.video.speaking_clip = "C:/videos/hablando.mp4"
    cfg.vad.attack_ms = 222
    cfg.video.letterbox_color = (1, 2, 3)
    path = cfg.save(tmp_path / "config.json")

    loaded = AppConfig.load(path)
    assert loaded.video.speaking_clip == cfg.video.speaking_clip
    assert loaded.vad.attack_ms == 222
    assert loaded.video.letterbox_color == (1, 2, 3)


def test_las_claves_desconocidas_se_ignoran(tmp_path):
    """Permite leer configuraciones de otras versiones sin romper."""
    path = tmp_path / "config.json"
    path.write_text(
        '{"version": 99, "vad": {"attack_ms": 150, "inventado": true}, "ruido": 1}',
        encoding="utf-8",
    )
    cfg = AppConfig.load(path)
    assert cfg.vad.attack_ms == 150
    assert cfg.video.fps == 15  # el resto, por defecto


def test_una_configuracion_corrupta_arranca_con_valores_por_defecto(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ roto", encoding="utf-8")
    cfg = AppConfig.load(path)
    assert cfg.video.fps == 15
    assert path.with_suffix(".json.bad").exists(), "no conservó el archivo malo"


def test_la_validacion_detecta_los_errores_relevantes():
    cfg = AppConfig()
    cfg.video.width = 641  # impar: NV12 no lo admite
    cfg.video.fps = 0
    cfg.video.fit = "inventado"
    cfg.vad.frame_ms = 17
    cfg.vad.snr_on_db = 1.0
    cfg.vad.snr_off_db = 9.0
    errors = "\n".join(cfg.validate())
    assert "pares" in errors
    assert "FPS" in errors
    assert "inventado" in errors
    assert "ventana del VAD" in errors
    assert "apagado" in errors


def test_la_validacion_puede_omitir_los_clips():
    cfg = AppConfig()
    assert any("clip" in e for e in cfg.validate())
    assert not any("clip" in e for e in cfg.validate(require_clips=False))


def test_los_avisos_no_bloquean():
    cfg = AppConfig()
    cfg.video.fps = 60
    cfg.vad.attack_ms = 900
    warnings = "\n".join(cfg.warnings())
    assert "CPU" in warnings
    assert "latencia" in warnings


# --------------------------------------------------------------------------- #
# Métricas
# --------------------------------------------------------------------------- #


def test_los_percentiles_interpolan():
    assert _percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert _percentile([10], 95) == 10
    assert _percentile([], 95) == 0.0


def test_la_pendiente_de_memoria_detecta_una_fuga():
    from camvoice.metrics import Sample

    def sample(t: float, rss: float) -> Sample:
        return Sample(
            t=t, cpu_percent=0, cpu_percent_norm=0, rss_mb=rss, fps_effective=15,
            fps_capacity=100, avg_frame_ms=1, worst_frame_ms=2, late_frames=0,
            resyncs=0, frames_sent=0, transitions=0, voice_state="SILENT",
            render_state="SILENT", rms_db=-50, noise_floor_db=-60,
            audio_overflows=0, audio_dropped=0, threads=3,
        )

    stable = [sample(i * 60.0, 100.0 + (i % 2) * 0.3) for i in range(20)]
    leaking = [sample(i * 60.0, 100.0 + i * 5.0) for i in range(20)]
    assert abs(_slope_per_minute(stable)) < 1.0
    assert _slope_per_minute(leaking) == pytest.approx(5.0, abs=0.01)


def test_el_resumen_juzga_la_estabilidad_de_memoria():
    largo = MetricsSummary.MIN_SLOPE_DURATION_S + 60
    assert MetricsSummary(duration_s=largo, rss_slope_mb_per_min=0.2).memory_stable
    assert not MetricsSummary(duration_s=largo, rss_slope_mb_per_min=4.0).memory_stable


def test_una_ventana_corta_no_juzga_la_memoria():
    """Medido en este equipo: la misma configuracion dio +0,36, +0,69, +1,17 y
    +1,90 MB/min en ejecuciones de 45-90 s. El metodo no resuelve el umbral de
    1 MB/min en ventanas cortas, asi que no debe dar un veredicto."""
    corto = MetricsSummary(duration_s=45.0, rss_slope_mb_per_min=1.9)
    assert corto.memory_verdict == "no concluyente"
    assert corto.memory_stable, "no hay evidencia de fuga: no debe fallar"
    informe = corto.render().lower()
    assert "no concluyente" in informe
    assert "min" in informe, "debe decir cuánto hace falta medir"


def test_una_ventana_larga_si_juzga():
    largo = MetricsSummary(duration_s=1800.0, rss_slope_mb_per_min=3.0)
    assert largo.memory_verdict == "crecimiento"
    assert not largo.memory_stable


def test_el_resumen_se_puede_imprimir():
    text = MetricsSummary(
        duration_s=120, samples=60, cpu_mean=8.0, cpu_norm_mean=1.0,
        logical_cpus=8, host=describe_host(),
    ).render()
    assert "CPU (% de un núcleo)" in text
    assert "CPU (% de la máquina)" in text
    assert "Equipo de referencia" in text


def test_las_dos_escalas_de_cpu_no_se_confunden():
    """La escala por núcleo y la de máquina difieren por el número de núcleos."""
    from camvoice.metrics import PerfMonitor
    from camvoice.config import MetricsConfig
    import os

    monitor = PerfMonitor(MetricsConfig(interval_s=0.05), lambda: None)
    monitor.start()
    time.sleep(0.3)
    summary = monitor.stop()
    assert summary.logical_cpus == max(1, os.cpu_count() or 1)
    assert summary.samples >= 2
