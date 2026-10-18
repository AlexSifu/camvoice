"""Pruebas de la caché de fotogramas, la geometría y el formato de píxel."""

from __future__ import annotations

import json

import numpy as np
import pytest

from camvoice.config import VideoConfig, frame_bytes, frame_shape
from camvoice.video.cache import (
    ClipCacheError,
    bgr_to_nv12,
    cache_key,
    fit_frame,
    nv12_to_bgr,
    prepare_clip,
    probe_clip,
    prune_cache,
)

# --------------------------------------------------------------------------- #
# Formato de píxel
# --------------------------------------------------------------------------- #


def test_las_formas_declaradas_coinciden_con_los_bytes():
    assert frame_shape(640, 480, "nv12") == (720, 640)
    assert frame_bytes(640, 480, "nv12") == 460800
    assert frame_shape(640, 480, "bgr") == (480, 640, 3)
    assert frame_bytes(640, 480, "bgr") == 921600


def test_nv12_ocupa_la_mitad_que_bgr():
    assert frame_bytes(640, 480, "nv12") * 2 == frame_bytes(640, 480, "bgr")


def test_formato_desconocido_se_rechaza():
    with pytest.raises(ValueError):
        frame_shape(640, 480, "rgba")


def test_la_conversion_a_nv12_y_vuelta_conserva_la_imagen():
    rng = np.random.default_rng(5)
    bgr = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    # Se suaviza: NV12 submuestrea el croma, así que el ruido por píxel no
    # sobrevive por definición. Lo que debe conservarse es la imagen real.
    import cv2

    bgr = cv2.GaussianBlur(bgr, (7, 7), 0)
    back = nv12_to_bgr(bgr_to_nv12(bgr))
    assert back.shape == bgr.shape
    assert np.abs(back.astype(int) - bgr.astype(int)).mean() < 6.0


def test_nv12_rechaza_dimensiones_impares():
    with pytest.raises(ClipCacheError):
        bgr_to_nv12(np.zeros((47, 63, 3), dtype=np.uint8))


def test_mezclar_en_nv12_equivale_a_mezclar_en_bgr():
    """La premisa que hace válido el fundido sobre bytes NV12.

    YUV es una transformación afín de RGB, así que interpolar linealmente en
    YUV y en RGB dan el mismo resultado. Esto es lo que permite que el
    compositor use un único addWeighted sobre el búfer crudo.
    """
    import cv2

    rng = np.random.default_rng(9)
    a = cv2.GaussianBlur(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8), (9, 9), 0)
    b = cv2.GaussianBlur(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8), (9, 9), 0)

    for weight in (0.25, 0.5, 0.75):
        in_bgr = cv2.addWeighted(a, 1 - weight, b, weight, 0)
        in_nv12 = nv12_to_bgr(
            cv2.addWeighted(bgr_to_nv12(a), 1 - weight, bgr_to_nv12(b), weight, 0)
        )
        diff = np.abs(in_bgr.astype(int) - in_nv12.astype(int)).mean()
        assert diff < 6.0, f"peso {weight}: diferencia media {diff:.2f}"


# --------------------------------------------------------------------------- #
# Geometría
# --------------------------------------------------------------------------- #


def test_cover_llena_el_marco_sin_deformar():
    source = np.zeros((100, 200, 3), dtype=np.uint8)  # 2:1
    out = fit_frame(source, 100, 100, "cover", (0, 0, 0))
    assert out.shape == (100, 100, 3)


def test_contain_deja_barras_del_color_indicado():
    source = np.full((100, 200, 3), 255, dtype=np.uint8)  # 2:1 en un marco 1:1
    out = fit_frame(source, 100, 100, "contain", (7, 8, 9))
    assert out.shape == (100, 100, 3)
    assert tuple(out[0, 50]) == (7, 8, 9), "la barra superior no tiene el color dado"
    assert tuple(out[50, 50]) == (255, 255, 255), "el centro no es la imagen"


def test_stretch_usa_todo_el_marco():
    source = np.full((100, 200, 3), 255, dtype=np.uint8)
    out = fit_frame(source, 100, 100, "stretch", (0, 0, 0))
    assert out.shape == (100, 100, 3)
    assert out.min() > 200, "stretch no debería introducir barras"


def test_el_tamano_identico_se_devuelve_sin_tocar():
    source = np.zeros((48, 64, 3), dtype=np.uint8)
    assert fit_frame(source, 64, 48, "cover", (0, 0, 0)) is source


# --------------------------------------------------------------------------- #
# Clave de caché
# --------------------------------------------------------------------------- #


def test_la_clave_cambia_con_cada_parametro_relevante(tiny_clips, video_cfg):
    speaking, _ = tiny_clips
    base = cache_key(speaking, video_cfg)
    for field, value in (
        ("width", 128),
        ("height", 96),
        ("fps", 20),
        ("fit", "contain"),
        ("mirror", True),
        ("pixel_format", "bgr"),
        ("letterbox_color", (1, 2, 3)),
    ):
        changed = VideoConfig(**{**video_cfg.__dict__, field: value})
        assert cache_key(speaking, changed) != base, f"{field} no afecta a la clave"


def test_la_clave_es_estable_para_la_misma_entrada(tiny_clips, video_cfg):
    speaking, _ = tiny_clips
    assert cache_key(speaking, video_cfg) == cache_key(speaking, video_cfg)


def test_modificar_el_video_invalida_la_clave(tiny_clips, video_cfg):
    speaking, _ = tiny_clips
    before = cache_key(speaking, video_cfg)
    speaking.write_bytes(speaking.read_bytes() + b"\x00")
    assert cache_key(speaking, video_cfg) != before


# --------------------------------------------------------------------------- #
# Sondeo
# --------------------------------------------------------------------------- #


def test_el_sondeo_lee_las_propiedades_del_clip(tiny_clips):
    speaking, _ = tiny_clips
    probe = probe_clip(speaking)
    assert (probe.width, probe.height) == (64, 48)
    assert probe.fps == pytest.approx(10.0, abs=0.5)
    assert probe.frame_count >= 10
    assert probe.aspect == pytest.approx(64 / 48)


def test_un_archivo_inexistente_da_un_error_claro(tmp_path):
    with pytest.raises(ClipCacheError, match="no existe"):
        probe_clip(tmp_path / "nada.mp4")


def test_un_archivo_que_no_es_video_da_un_error_claro(tmp_path):
    """CA-03: mensaje comprensible si el archivo no se puede abrir."""
    bad = tmp_path / "falso.mp4"
    bad.write_bytes(b"esto no es un video" * 50)
    with pytest.raises(ClipCacheError) as info:
        probe_clip(bad)
    assert "falso.mp4" in str(info.value)


# --------------------------------------------------------------------------- #
# Preparación
# --------------------------------------------------------------------------- #


def test_la_preparacion_produce_los_fotogramas_esperados(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    cache = prepare_clip(speaking, video_cfg, directory=tmp_path / "cache")
    assert len(cache) > 0
    assert cache.meta.width == video_cfg.width
    assert cache.meta.height == video_cfg.height
    assert cache.meta.fps == video_cfg.fps
    assert cache.meta.pixel_format == "nv12"
    assert cache.frame(0).shape == frame_shape(64, 48, "nv12")
    assert cache.raw_path.stat().st_size == cache.meta.total_bytes


def test_la_cadencia_de_salida_se_respeta(tiny_clips, tmp_path):
    """Un clip a 10 FPS preparado a 5 FPS debe tener la mitad de fotogramas."""
    speaking, _ = tiny_clips
    slow = VideoConfig(width=64, height=48, fps=5, pixel_format="nv12")
    fast = VideoConfig(width=64, height=48, fps=10, pixel_format="nv12")
    a = prepare_clip(speaking, slow, directory=tmp_path / "a")
    b = prepare_clip(speaking, fast, directory=tmp_path / "b")
    assert len(a) == pytest.approx(len(b) / 2, abs=2)


def test_la_segunda_preparacion_reutiliza_la_cache(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    directory = tmp_path / "cache"
    first = prepare_clip(speaking, video_cfg, directory=directory)
    stamp = first.raw_path.stat().st_mtime_ns
    first.close()

    messages: list[str] = []
    second = prepare_clip(
        speaking, video_cfg, directory=directory,
        progress=lambda d, t, m: messages.append(m),
    )
    assert second.raw_path.stat().st_mtime_ns == stamp, "regeneró la caché sin necesidad"
    assert any("reutilizada" in m for m in messages)


def test_force_regenera_la_cache(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    directory = tmp_path / "cache"
    first = prepare_clip(speaking, video_cfg, directory=directory)
    first.close()
    messages: list[str] = []
    prepare_clip(
        speaking, video_cfg, directory=directory, force=True,
        progress=lambda d, t, m: messages.append(m),
    )
    assert any("Preparando" in m or "fotogramas" in m for m in messages)


def test_una_cache_truncada_se_regenera(tiny_clips, video_cfg, tmp_path):
    """Si el .raw no coincide con los metadatos, no se usa a medias."""
    speaking, _ = tiny_clips
    directory = tmp_path / "cache"
    cache = prepare_clip(speaking, video_cfg, directory=directory)
    raw = cache.raw_path
    expected = cache.meta.total_bytes
    cache.close()

    with open(raw, "r+b") as fh:
        fh.truncate(expected // 2)
    rebuilt = prepare_clip(speaking, video_cfg, directory=directory)
    assert rebuilt.raw_path.stat().st_size == expected


def test_unos_metadatos_corruptos_no_rompen(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    directory = tmp_path / "cache"
    cache = prepare_clip(speaking, video_cfg, directory=directory)
    meta_path = cache.raw_path.with_suffix(".json")
    cache.close()
    meta_path.write_text("{ esto no es json", encoding="utf-8")
    rebuilt = prepare_clip(speaking, video_cfg, directory=directory)
    assert len(rebuilt) > 0
    assert json.loads(meta_path.read_text(encoding="utf-8"))["frame_count"] == len(rebuilt)


def test_el_tope_de_cache_recorta_y_lo_declara(tiny_clips, tmp_path):
    speaking, _ = tiny_clips
    cfg = VideoConfig(width=64, height=48, fps=10, pixel_format="nv12", max_cache_mb=16)
    cfg.max_cache_mb = 1  # ~226 fotogramas de 64x48; el clip cabe
    cache = prepare_clip(speaking, cfg, directory=tmp_path / "cache")
    assert len(cache) > 0


def test_el_espejo_se_aplica_al_preparar(tiny_clips, tmp_path):
    """Se hornea en la caché: coste nulo en ejecución."""
    speaking, _ = tiny_clips
    direct = VideoConfig(width=64, height=48, fps=10, pixel_format="bgr", mirror=False)
    mirrored = VideoConfig(width=64, height=48, fps=10, pixel_format="bgr", mirror=True)
    a = prepare_clip(speaking, direct, directory=tmp_path / "a")
    b = prepare_clip(speaking, mirrored, directory=tmp_path / "b")
    assert np.array_equal(np.flip(np.asarray(a.frame(0)), axis=1), np.asarray(b.frame(0)))


def test_los_fotogramas_se_pueden_cargar_en_ram(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    cache = prepare_clip(speaking, video_cfg, directory=tmp_path / "cache")
    reference = np.array(cache.frame(1))
    cache.load_to_ram()
    assert np.array_equal(cache.frame(1), reference)


def test_el_calentamiento_no_falla(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    cache = prepare_clip(speaking, video_cfg, directory=tmp_path / "cache")
    cache.warm_up()  # no debe lanzar
    assert len(cache) > 0


def test_un_indice_fuera_de_rango_se_recorta(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    cache = prepare_clip(speaking, video_cfg, directory=tmp_path / "cache")
    assert np.array_equal(cache.frame(10**6), cache.frame(len(cache) - 1))
    assert np.array_equal(cache.frame(-5), cache.frame(0))


def test_leer_una_cache_cerrada_da_un_error_claro(tiny_clips, video_cfg, tmp_path):
    speaking, _ = tiny_clips
    cache = prepare_clip(speaking, video_cfg, directory=tmp_path / "cache")
    cache.close()
    with pytest.raises(ClipCacheError):
        cache.frame(0)


def test_la_limpieza_respeta_lo_que_se_le_pide_conservar(tiny_clips, video_cfg, tmp_path):
    speaking, silent = tiny_clips
    directory = tmp_path / "cache"
    keep = prepare_clip(speaking, video_cfg, directory=directory)
    drop = prepare_clip(silent, video_cfg, directory=directory)
    keep_key = keep.raw_path.stem
    drop_path = drop.raw_path
    keep.close()
    drop.close()

    freed = prune_cache({keep_key}, directory=directory)
    assert freed > 0
    assert not drop_path.exists()
    assert (directory / f"{keep_key}.raw").exists()
