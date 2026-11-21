"""Crop a face-free region, pad to ~5s for Seedance, then feather-stitch back."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# Higgsfield bills ceil(seconds). 120 frames @ 24fps = 5.005s → billed as 6s.
# Hold just under 5s so a 720p Seedance edit stays ~$1.63.
TARGET_HOLD_S = 4.96
EDIT_W = 1280
EDIT_H = 720
MAX_INPUT_S = 5.0
FEATHER_SIGMA = 6.0
DILATE_PX = 25

# Seedance 720p: $0.3264 / s (from Higgsfield /estimate).
SEEDANCE_USD_PER_S = 0.3264


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_dict(cls, d: dict) -> Region:
        return cls(int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"]))

    def clamp(self, frame_w: int, frame_h: int) -> Region:
        x = max(0, min(self.x, frame_w - 1))
        y = max(0, min(self.y, frame_h - 1))
        w = max(1, min(self.w, frame_w - x))
        h = max(1, min(self.h, frame_h - y))
        return Region(x, y, w, h)


def estimate_seedance_cost_usd(duration_s: float) -> float:
    """Billable seconds are ceil(duration); price is per billed second."""
    import math

    billed = max(1, int(math.ceil(max(0.0, float(duration_s)) - 1e-9)))
    return round(billed * SEEDANCE_USD_PER_S, 4)


def probe_video(path: str | Path) -> tuple[int, int, float, float]:
    """Return width, height, duration_s, fps."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate:format=duration",
        "-of",
        "json",
        str(path),
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))
    stream = data["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    duration = float(data["format"]["duration"])
    rate = stream.get("r_frame_rate") or "24/1"
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / float(den) if float(den) else 24.0
    else:
        fps = float(rate) or 24.0
    return width, height, duration, fps


def extract_moment_clip(
    video: str | Path,
    start_s: float,
    end_s: float,
    out_path: str | Path,
) -> Path:
    """Cut [start_s, end_s) from source; re-encode H.264, keep audio if present."""
    video = Path(video)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, float(end_s) - float(start_s))
    if duration > MAX_INPUT_S + 1e-3:
        raise ValueError(
            f"moment window {duration:.3f}s exceeds server cap of {MAX_INPUT_S}s"
        )
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(video),
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_path


def build_crop_hold(
    moment_clip: str | Path,
    region: Region,
    out_path: str | Path,
    *,
    hold_s: float = TARGET_HOLD_S,
) -> Path:
    """Crop region, upscale to 1280×720, hold last frame to ~hold_s (no audio)."""
    moment_clip = Path(moment_clip)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(moment_clip))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {moment_clip}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    region = region.clamp(frame_w, frame_h)

    frames: list[np.ndarray] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        crop = frame[region.y : region.y + region.h, region.x : region.x + region.w]
        frames.append(cv2.resize(crop, (EDIT_W, EDIT_H), interpolation=cv2.INTER_CUBIC))
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames in {moment_clip}")

    target_n = max(len(frames), int(round(hold_s * fps)))
    # Stay strictly under 5.0s of wall time at this fps.
    max_n = max(1, int(math_floor_under(5.0, fps)))
    target_n = min(target_n, max_n)
    while len(frames) < target_n:
        frames.append(frames[-1].copy())

    tmp = out_path.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(
        str(tmp),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (EDIT_W, EDIT_H),
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot open VideoWriter for {tmp}")
    for f in frames:
        writer.write(f)
    writer.release()

    # Remux to H.264 for Higgsfield upload compatibility.
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(tmp),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.unlink(missing_ok=True)
    return out_path


def math_floor_under(seconds: float, fps: float) -> int:
    """Largest frame count with duration < seconds."""
    if fps <= 0:
        return 1
    n = int(seconds * fps)
    while n > 1 and (n / fps) >= seconds:
        n -= 1
    return max(1, n)


def _feather_mask(h: int, w: int, sigma: float = FEATHER_SIGMA) -> np.ndarray:
    mask = np.ones((h, w), dtype=np.float32)
    # Soft edges: zero border then blur.
    border = max(1, int(round(sigma * 2)))
    mask[:border, :] = 0
    mask[-border:, :] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0
    k = max(3, int(round(sigma * 6)) | 1)
    mask = cv2.GaussianBlur(mask, (k, k), sigma)
    return np.clip(mask, 0.0, 1.0)


def stitch_edit_into_moment(
    moment_clip: str | Path,
    edited_crop: str | Path,
    region: Region,
    out_path: str | Path,
    *,
    color_match: bool = True,
) -> Path:
    """Paste edited 1280×720 crop back into the moment clip with gaussian feather."""
    moment_clip = Path(moment_clip)
    edited_crop = Path(edited_crop)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    src = cv2.VideoCapture(str(moment_clip))
    edit = cv2.VideoCapture(str(edited_crop))
    if not src.isOpened() or not edit.isOpened():
        raise RuntimeError("cannot open stitch inputs")

    fps = float(src.get(cv2.CAP_PROP_FPS) or 24.0)
    frame_w = int(src.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(src.get(cv2.CAP_PROP_FRAME_HEIGHT))
    region = region.clamp(frame_w, frame_h)

    src_frames: list[np.ndarray] = []
    while True:
        ok, frame = src.read()
        if not ok:
            break
        src_frames.append(frame)
    src.release()

    edit_frames: list[np.ndarray] = []
    while True:
        ok, frame = edit.read()
        if not ok:
            break
        edit_frames.append(frame)
    edit.release()
    if not src_frames or not edit_frames:
        raise RuntimeError("empty stitch inputs")

    # Seedance often returns fewer frames; remap i → round(i * (E-1)/(S-1)).
    s_n = len(src_frames)
    e_n = len(edit_frames)
    def map_i(i: int) -> int:
        if s_n <= 1 or e_n <= 1:
            return 0
        return int(round(i * (e_n - 1) / (s_n - 1)))

    # Optional global color offset from first mapped pair.
    offset = np.zeros(3, dtype=np.float32)
    if color_match:
        a = src_frames[0][
            region.y : region.y + region.h, region.x : region.x + region.w
        ].astype(np.float32)
        b = cv2.resize(edit_frames[0], (region.w, region.h)).astype(np.float32)
        offset = (a.mean(axis=(0, 1)) - b.mean(axis=(0, 1))).astype(np.float32)

    mask = _feather_mask(region.h, region.w)
    mask3 = mask[:, :, None]

    tmp = out_path.with_suffix(".stitch.raw.mp4")
    writer = cv2.VideoWriter(
        str(tmp),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (frame_w, frame_h),
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot open VideoWriter for {tmp}")

    for i, base in enumerate(src_frames):
        edit_f = edit_frames[map_i(i)]
        patch = cv2.resize(edit_f, (region.w, region.h), interpolation=cv2.INTER_CUBIC)
        if color_match:
            patch = np.clip(patch.astype(np.float32) + offset, 0, 255).astype(np.uint8)
        roi = base[region.y : region.y + region.h, region.x : region.x + region.w]
        blended = (
            patch.astype(np.float32) * mask3 + roi.astype(np.float32) * (1.0 - mask3)
        ).astype(np.uint8)
        out = base.copy()
        out[region.y : region.y + region.h, region.x : region.x + region.w] = blended
        writer.write(out)
    writer.release()

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(tmp),
        "-i",
        str(moment_clip),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.unlink(missing_ok=True)
    return out_path


def mock_edit_crop(
    crop_hold: str | Path,
    product_image: str | Path,
    out_path: str | Path,
) -> Path:
    """Unpaid stand-in for Seedance: overlay the product PNG on the crop."""
    crop_hold = Path(crop_hold)
    product_image = Path(product_image)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    product = cv2.imread(str(product_image), cv2.IMREAD_UNCHANGED)
    if product is None:
        raise RuntimeError(f"cannot read product image {product_image}")

    cap = cv2.VideoCapture(str(crop_hold))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {crop_hold}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or EDIT_W
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or EDIT_H

    # Fit product into ~45% of the frame, centered.
    pw = int(w * 0.45)
    ph = int(h * 0.45)
    if product.shape[2] == 4:
        bgr = product[:, :, :3]
        alpha = product[:, :, 3].astype(np.float32) / 255.0
    else:
        bgr = product
        alpha = np.ones(bgr.shape[:2], dtype=np.float32)
    bgr = cv2.resize(bgr, (pw, ph), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, (pw, ph), interpolation=cv2.INTER_AREA)
    alpha3 = alpha[:, :, None]
    x0 = (w - pw) // 2
    y0 = (h - ph) // 2

    tmp = out_path.with_suffix(".mock.raw.mp4")
    writer = cv2.VideoWriter(
        str(tmp),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (w, h),
    )
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        roi = frame[y0 : y0 + ph, x0 : x0 + pw].astype(np.float32)
        blended = bgr.astype(np.float32) * alpha3 + roi * (1.0 - alpha3)
        frame[y0 : y0 + ph, x0 : x0 + pw] = blended.astype(np.uint8)
        # Slight teal wash so the mock result is visually distinct.
        frame = cv2.addWeighted(frame, 0.92, np.full_like(frame, (40, 90, 70)), 0.08, 0)
        writer.write(frame)
    cap.release()
    writer.release()

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(tmp),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.unlink(missing_ok=True)
    return out_path
