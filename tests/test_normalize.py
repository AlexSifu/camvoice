"""Normalización de encuadre y color entre clips (video/normalize.py)."""

from __future__ import annotations

import cv2
import numpy as np

from camvoice.config import VideoConfig
from camvoice.testclips import make_test_library, render_face
from camvoice.video.normalize import IDENTITY, compute_normalization, estimate_alignment, sample_frames

W, H = 320, 240


def shifted(img, dx, dy, gain=1.0):
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    out = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    return np.clip(out.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def test_alignment_recovers_small_shift():
    ref = render_face(W, H, 0.0, mouth_open=0.0, label="")
    src = shifted(ref, 9, -6)
    matrix, note = estimate_alignment(src, ref)
    assert matrix is not None, note
    fixed = cv2.warpAffine(src, matrix, (W, H), borderMode=cv2.BORDER_REPLICATE)
    inner = (slice(30, H - 30), slice(30, W - 30))
    before = np.abs(src[inner].astype(int) - ref[inner]).mean()
    after = np.abs(fixed[inner].astype(int) - ref[inner]).mean()
    assert after < before * 0.4


def test_large_shift_is_rejected_not_forced():
    ref = render_face(W, H, 0.0, mouth_open=0.0, label="")
    src = shifted(ref, 60, 0)  # ~19 % del ancho: otra escena, no se deforma
    matrix, note = estimate_alignment(src, ref)
    assert matrix is None
    assert "demasiado grande" in note or "no convergió" in note


def test_aligned_input_needs_no_matrix():
    ref = render_face(W, H, 0.0, mouth_open=0.0, label="")
    matrix, note = estimate_alignment(ref.copy(), ref)
    assert matrix is None and "ya alineado" in note


def test_library_clip_is_corrected_and_reference_is_identity(tmp_path):
    lib = make_test_library(tmp_path, width=W, height=H, fps=10, seconds=2.0, gestures=())
    cfg = VideoConfig(width=W, height=H, fps=10)
    ref = sample_frames(lib["silent"], cfg)
    assert len(ref) >= 3

    assert compute_normalization(lib["silent"], ref, cfg, is_reference=True) is IDENTITY
    assert IDENTITY.fingerprint == ""

    norm = compute_normalization(lib["silent2"], ref, cfg)
    assert norm.matrix is not None, norm.notes
    assert norm.luts is not None, norm.notes
    assert norm.fingerprint.startswith("norm1:")

    src = sample_frames(lib["silent2"], cfg)[0]
    fixed = norm.apply(src)
    ref_mean = np.mean([f.mean() for f in ref])
    assert abs(fixed.mean() - ref_mean) < abs(src.mean() - ref_mean)


def test_fingerprint_is_deterministic(tmp_path):
    lib = make_test_library(tmp_path, width=W, height=H, fps=10, seconds=2.0, gestures=())
    cfg = VideoConfig(width=W, height=H, fps=10)
    ref = sample_frames(lib["silent"], cfg)
    a = compute_normalization(lib["silent2"], ref, cfg).fingerprint
    b = compute_normalization(lib["silent2"], ref, cfg).fingerprint
    assert a == b


def test_unreadable_clip_gives_identity_with_note(tmp_path):
    bad = tmp_path / "roto.mp4"
    bad.write_bytes(b"no es un video")
    cfg = VideoConfig(width=W, height=H, fps=10)
    ref = [render_face(W, H, 0.0, mouth_open=0.0, label="")]
    norm = compute_normalization(bad, ref, cfg)
    assert norm.identity and norm.notes
