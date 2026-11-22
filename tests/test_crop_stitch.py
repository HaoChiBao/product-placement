"""Offline tests for crop-hold cost caps and mock edit path."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from product_placement.crop_stitch import (
    MAX_INPUT_S,
    Region,
    build_crop_hold,
    estimate_seedance_cost_usd,
    extract_moment_clip,
    mock_edit_crop,
    stitch_edit_into_moment,
)


def test_estimate_seedance_cost_five_seconds():
    # ceil(4.96) == 5 → 5 * 0.3264
    assert estimate_seedance_cost_usd(4.96) == pytest.approx(1.632, abs=1e-3)
    assert estimate_seedance_cost_usd(5.0) == pytest.approx(1.632, abs=1e-3)
    assert estimate_seedance_cost_usd(5.01) == pytest.approx(1.9584, abs=1e-3)


def _write_tiny_mp4(path: Path, *, n_frames: int = 24, fps: float = 24.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 320, 180
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (w, h),
    )
    assert writer.isOpened()
    for i in range(n_frames):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (40, 60 + i, 80)
        cv2.rectangle(frame, (80, 40), (200, 140), (20, 180, 220), -1)
        writer.write(frame)
    writer.release()
    # Remux H.264 for ffmpeg consumers.
    out = path.with_name(path.stem + "_h264.mp4")
    import subprocess

    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out


def test_crop_hold_stays_under_five_seconds(tmp_path: Path):
    # Longer than 5s at 24fps so truncation (not just pad) is required.
    clip = _write_tiny_mp4(tmp_path / "src.mp4", n_frames=144)
    region = Region(80, 40, 120, 100)
    hold = build_crop_hold(clip, region, tmp_path / "hold.mp4", hold_s=4.96)
    assert hold.is_file()
    import json
    import subprocess

    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(hold),
        ],
        text=True,
    )
    duration = float(json.loads(raw)["format"]["duration"])
    assert duration < MAX_INPUT_S
    assert duration <= 4.96 + 1e-2


def test_mock_edit_and_stitch(tmp_path: Path):
    clip = _write_tiny_mp4(tmp_path / "src.mp4", n_frames=20)
    region = Region(80, 40, 120, 100)
    hold = build_crop_hold(clip, region, tmp_path / "hold.mp4", hold_s=2.0)

    product = tmp_path / "product.png"
    img = np.zeros((64, 64, 4), dtype=np.uint8)
    img[:, :, 1] = 200
    img[:, :, 3] = 255
    cv2.imwrite(str(product), img)

    edited = mock_edit_crop(hold, product, tmp_path / "edited.mp4")
    assert edited.is_file() and edited.stat().st_size > 500

    out = stitch_edit_into_moment(clip, edited, region, tmp_path / "stitched.mp4")
    assert out.is_file() and out.stat().st_size > 500


def test_extract_rejects_overlong_window(tmp_path: Path):
    clip = _write_tiny_mp4(tmp_path / "src.mp4", n_frames=48)
    with pytest.raises(ValueError, match="exceeds server cap"):
        extract_moment_clip(clip, 0.0, 6.0, tmp_path / "moment.mp4")
