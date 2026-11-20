"""Genjutsu prep upscales undersized clips."""

from pathlib import Path

import pytest

from product_placement.prepare import MIN_PIXELS, prepare_for_genjutsu, probe_video


def _make_tiny_mp4(path: Path) -> None:
    import subprocess

    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_prepare_upscales_below_min_pixels(tmp_path: Path):
    src = tmp_path / "tiny.mp4"
    _make_tiny_mp4(src)
    w, h, dur = probe_video(src)
    assert w * h < MIN_PIXELS
    assert dur >= 4.0
    out = prepare_for_genjutsu(src, tmp_path / "out.mp4")
    ow, oh, _ = probe_video(out)
    assert ow * oh >= MIN_PIXELS
