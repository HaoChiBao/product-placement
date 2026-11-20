"""Mux original clip audio onto an edited video."""

from __future__ import annotations

import subprocess
from pathlib import Path


def mux_original_audio(
    edited_video: str | Path,
    original_clip: str | Path,
    out_path: str | Path,
) -> Path:
    """Replace/attach audio from original_clip onto edited_video.

    If the original has no audio stream, copy the edited video as-is.
    """
    edited_video = Path(edited_video)
    original_clip = Path(original_clip)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(original_clip),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    has_audio = bool(probe.stdout.strip())

    if not has_audio:
        if edited_video.resolve() == out_path.resolve():
            return out_path
        out_path.write_bytes(edited_video.read_bytes())
        return out_path

    tmp = out_path.with_suffix(".mux.tmp.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(edited_video),
        "-i",
        str(original_clip),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(tmp),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.replace(out_path)
    return out_path
