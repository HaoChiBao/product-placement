"""Local video prep for Genjutsu constraints (duration / min pixels)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Genjutsu: width * height >= 409_600 per frame; duration >= 4s; trim >30s.
MIN_PIXELS = 409_600
MIN_DURATION_S = 4.0
MAX_DURATION_S = 30.0


def probe_video(path: str | Path) -> tuple[int, int, float]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:format=duration",
        "-of",
        "json",
        str(path),
    ]
    raw = subprocess.check_output(cmd, text=True)
    data = json.loads(raw)
    stream = data["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    duration = float(data["format"]["duration"])
    return width, height, duration


def prepare_for_genjutsu(
    clip_path: str | Path,
    out_path: str | Path,
) -> Path:
    """Upscale and/or trim so the clip meets Genjutsu input rules."""
    clip_path = Path(clip_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    width, height, duration = probe_video(clip_path)
    pixels = width * height
    filters: list[str] = []

    if pixels < MIN_PIXELS:
        scale = (MIN_PIXELS / pixels) ** 0.5
        # Round up to even dimensions for yuv420p.
        tw = int(width * scale + 1) // 2 * 2
        th = int(height * scale + 1) // 2 * 2
        while tw * th < MIN_PIXELS:
            tw += 2
        filters.append(f"scale={tw}:{th}")

    cmd = ["ffmpeg", "-y", "-i", str(clip_path)]
    if duration > MAX_DURATION_S:
        cmd += ["-t", str(MAX_DURATION_S)]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out_path)]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    _, _, out_dur = probe_video(out_path)
    if out_dur + 1e-3 < MIN_DURATION_S:
        raise ValueError(
            f"video is {out_dur:.2f}s; Genjutsu requires at least {MIN_DURATION_S}s"
        )
    return out_path
