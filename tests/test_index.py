"""Índice de similitud por clip (video/index.py)."""

from __future__ import annotations

import numpy as np
import pytest

from camvoice.video.index import (
    ClipIndex,
    IndexCancelled,
    _top_k_neighbors,
    build_index,
    descriptor,
    distances,
    luma,
)

from .synth import FPS, H, face_frames, speaking_mouth


@pytest.fixture(scope="module")
def speaking():
    frames = face_frames(150, speaking_mouth, "")
    return frames, build_index(frames, pixel_format="nv12", height=H, fps=FPS)


def test_descriptors_are_unit_vectors(speaking):
    _, idx = speaking
    norms = np.linalg.norm(idx.descriptors.astype(np.float32), axis=1)
    assert np.allclose(norms, 1.0, atol=1e-2)


def test_mouth_signal_tracks_true_opening(speaking):
    _, idx = speaking
    truth = np.array([speaking_mouth(i) for i in range(150)])
    corr = np.corrcoef(truth, idx.mouth)[0, 1]
    assert corr > 0.9, corr
    assert idx.mouth_range > 0.2


def test_static_clip_has_no_mouth_range():
    frames = face_frames(60, lambda i: 0.0, "")
    idx = build_index(frames, pixel_format="nv12", height=H, fps=FPS)
    assert idx.mouth_range < 0.05


def test_neighbors_exclude_temporal_band(speaking):
    _, idx = speaking
    min_gap = max(2, FPS // 2)
    for i in range(idx.frame_count):
        for j in idx.neighbors[i]:
            if j >= 0:
                assert abs(int(j) - i) > min_gap


def test_neighbors_sorted_by_distance(speaking):
    _, idx = speaking
    d = idx.neighbor_dist
    finite = np.isfinite(d)
    for row, mask in zip(d, finite):
        vals = row[mask]
        assert np.all(np.diff(vals) >= -1e-6)


def test_blocked_search_equals_full_matrix():
    rng = np.random.default_rng(0)
    desc = rng.standard_normal((300, 64)).astype(np.float32)
    desc /= np.linalg.norm(desc, axis=1, keepdims=True)
    small_nbr, small_d = _top_k_neighbors(desc, 5, 3, memory_mb=0.01, cancel=None, progress=None)
    big_nbr, big_d = _top_k_neighbors(desc, 5, 3, memory_mb=512, cancel=None, progress=None)
    assert np.array_equal(small_nbr, big_nbr)
    assert np.allclose(small_d, big_d)

    full = np.sqrt(np.maximum(0, 2 - 2 * desc @ desc.T))
    for i in (0, 150, 299):
        row = full[i].copy()
        row[max(0, i - 3): i + 4] = np.inf
        assert set(np.argsort(row)[:5]) == set(small_nbr[i])


def test_cancel_raises():
    frames = face_frames(40, speaking_mouth, "")
    with pytest.raises(IndexCancelled):
        build_index(frames, pixel_format="nv12", height=H, fps=FPS, cancel=lambda: True)


def test_progress_is_reported():
    frames = face_frames(30, speaking_mouth, "")
    seen = []
    build_index(frames, pixel_format="nv12", height=H, fps=FPS,
                progress=lambda d, t, m: seen.append((d, t, m)))
    assert seen and seen[-1][0] == seen[-1][1]


def test_nearest_respects_range(speaking):
    frames, idx = speaking
    q = descriptor(luma(frames[100], "nv12", H))
    pos, d = idx.nearest(q)
    # El clip sintético puede repetir exactamente un fotograma: basta con que
    # el hallado sea equivalente (distancia ~0) al buscado.
    assert d < 1e-2
    assert np.allclose(idx.descriptors[pos].astype(np.float32), idx.descriptors[100].astype(np.float32), atol=2e-2)
    pos, _ = idx.nearest(q, lo=0, hi=50)
    assert 0 <= pos < 50


def test_save_load_roundtrip_and_mismatch(tmp_path, speaking):
    _, idx = speaking
    path = tmp_path / "x.idx.npz"
    idx.save(path, frame_count=idx.frame_count)
    again = ClipIndex.load(path, frame_count=idx.frame_count)
    assert again is not None
    assert np.array_equal(again.neighbors, idx.neighbors)
    assert np.allclose(again.mouth, idx.mouth)
    assert abs(again.d_ok - idx.d_ok) < 1e-6
    # Otro número de fotogramas (clip cambiado): se descarta y se reconstruye.
    assert ClipIndex.load(path, frame_count=idx.frame_count + 1) is None
    path.write_bytes(b"basura")
    assert ClipIndex.load(path, frame_count=idx.frame_count) is None


def test_distances_shape(speaking):
    _, idx = speaking
    q = idx.descriptors[3]
    d = distances(idx.descriptors, q)
    assert d.shape == (idx.frame_count,)
    assert d[3] < 1e-2
