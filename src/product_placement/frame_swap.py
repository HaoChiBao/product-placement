"""Frame-based placement swap: extract → edit frame → Seedance image-to-video.

Paid path (Higgsfield):
1. Extract a full-res frame at the moment start with ffmpeg.
2. Edit that frame via Qwen Image 3 Edit (Higgsfield image-edit endpoint),
   placing the brand product into the moment bbox (product image as reference).
3. Animate the edited frame with Seedance 2.5 image-to-video at the moment
   duration and source aspect/resolution.

Mock path (MOCK_HIGGSFIELD / test_mode): still extracts the real frame, pastes
the product into the bbox locally, then holds that frame as a short H.264 clip.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from product_placement.crop_stitch import (
    MAX_INPUT_S,
    QWEN_EDIT_1K_USD,
    SEEDANCE_I2V_USD_PER_S,
    estimate_frame_swap_cost_usd,
)
from product_placement.higgsfield import (
    HiggsfieldClient,
    build_qwen_edit_body,
    build_seedance_i2v_body,
    default_i2v_prompt,
)


def extract_frame_at(
    video: str | Path,
    timestamp_s: float,
    out_path: str | Path,
) -> Path:
    """Extract one full-resolution frame at an exact timestamp (ffmpeg)."""
    video = Path(video)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, float(timestamp_s))
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{t:.6f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out_path.is_file() or out_path.stat().st_size < 100:
        raise RuntimeError(f"frame extract failed at t={t}: {out_path}")
    return out_path


def probe_aspect_ratio(video: str | Path) -> str:
    """Map source WxH to a Seedance-friendly resolution tier label helper."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(video),
    ]
    text = subprocess.check_output(cmd, text=True).strip()
    w_s, h_s = text.split("x", 1)
    w, h = int(w_s), int(h_s)
    return f"{w}:{h}"


def resolution_for_source(video: str | Path) -> str:
    """Pick Seedance resolution tier from source height (cap at 720p for cost)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=height",
        "-of",
        "csv=p=0",
        str(video),
    ]
    h = int(subprocess.check_output(cmd, text=True).strip() or "720")
    if h >= 1000:
        return "1080p"
    if h >= 600:
        return "720p"
    return "480p"


def paste_product_into_bbox(
    frame_path: str | Path,
    product_path: str | Path,
    region: dict[str, Any],
    out_path: str | Path,
) -> Path:
    """Local (mock) frame edit: alpha-composite product into the moment bbox."""
    frame_path = Path(frame_path)
    product_path = Path(product_path)
    out_path = Path(out_path)
    frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"cannot read frame {frame_path}")
    product = cv2.imread(str(product_path), cv2.IMREAD_UNCHANGED)
    if product is None:
        raise RuntimeError(f"cannot read product {product_path}")

    fh, fw = frame.shape[:2]
    x = int(max(0, min(region.get("x", 0), fw - 1)))
    y = int(max(0, min(region.get("y", 0), fh - 1)))
    w = int(max(1, min(region.get("w", 1), fw - x)))
    h = int(max(1, min(region.get("h", 1), fh - y)))

    if product.shape[2] == 4:
        bgr = product[:, :, :3]
        alpha = product[:, :, 3].astype(np.float32) / 255.0
    else:
        bgr = product
        alpha = np.ones(bgr.shape[:2], dtype=np.float32)

    bgr = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_AREA)[:, :, None]
    roi = frame[y : y + h, x : x + w].astype(np.float32)
    blended = bgr.astype(np.float32) * alpha + roi * (1.0 - alpha)
    frame[y : y + h, x : x + w] = blended.astype(np.uint8)
    # Slight wash so mock edits are visually distinct.
    frame = cv2.addWeighted(frame, 0.94, np.full_like(frame, (30, 70, 50)), 0.06, 0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), frame):
        raise RuntimeError(f"failed to write edited frame {out_path}")
    return out_path


def mock_i2v_from_frame(
    frame_path: str | Path,
    out_path: str | Path,
    *,
    duration_s: float,
    fps: float = 24.0,
) -> Path:
    """Hold the edited frame for duration_s as an H.264 clip (mock Seedance i2v)."""
    frame_path = Path(frame_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_s = max(1.0, float(duration_s))
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(frame_path),
        "-t",
        f"{duration_s:.3f}",
        "-r",
        str(fps),
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
    return out_path


def clamp_i2v_duration(duration_s: float) -> int:
    """Seedance i2v accepts integer seconds in [4, 30]."""
    return max(4, min(30, int(math.ceil(max(0.1, float(duration_s)) - 1e-9))))


def frame_edit_prompt(target: str, region: dict[str, Any]) -> str:
    return (
        f"In the first image, replace or place the product from the second image "
        f"into the region around ({region.get('x')},{region.get('y')}) "
        f"size {region.get('w')}x{region.get('h')} where the {target} is. "
        "Match lighting, perspective and shadows. Keep the rest of the frame identical."
    )


def run_frame_swap(
    *,
    video_path: str | Path,
    product_path: str | Path,
    start_s: float,
    end_s: float,
    region: dict[str, Any],
    target: str,
    out_path: str | Path,
    work_dir: str | Path,
    confirm: bool,
    test_mode: bool = True,
    client: HiggsfieldClient | None = None,
) -> dict[str, Any]:
    """Execute the frame-based swap pipeline. Returns metadata + result path."""
    if not confirm:
        raise ValueError("confirm=true is required before a placement swap")
    if not test_mode and not mock_enabled() and not confirm:
        raise ValueError("paid placement swap requires confirm=True")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration_s = max(0.1, float(end_s) - float(start_s))
    if duration_s > MAX_INPUT_S + 1e-3:
        raise ValueError(
            f"input window {duration_s:.3f}s exceeds max {MAX_INPUT_S}s"
        )
    i2v_duration = clamp_i2v_duration(duration_s)
    estimated = estimate_frame_swap_cost_usd(float(i2v_duration))

    frame_path = extract_frame_at(video_path, start_s, work_dir / "source_frame.jpg")
    aspect = probe_aspect_ratio(video_path)
    resolution = resolution_for_source(video_path)
    # Hosted cost gate: keep 720p default for paid runs.
    if not test_mode and not mock_enabled() and resolution not in {"720p"}:
        resolution = "720p"

    use_mock = bool(test_mode) or mock_enabled()
    edited_frame = work_dir / "edited_frame.jpg"
    request_id = "mock"
    backend = "mock"
    video_url = ""
    edit_request_id = "mock"

    if use_mock:
        paste_product_into_bbox(frame_path, product_path, region, edited_frame)
        mock_i2v_from_frame(edited_frame, out_path, duration_s=float(i2v_duration))
    else:
        owns = client is None
        hf = client or HiggsfieldClient()
        try:
            frame_url = hf.upload_file(frame_path)
            product_url = hf.upload_file(product_path)
            edit_body = build_qwen_edit_body(
                prompt=frame_edit_prompt(target, region),
                image_urls=[frame_url, product_url],
                resolution="1k",
                aspect_ratio=_aspect_for_qwen(aspect),
            )
            edit_result = hf.run_qwen_edit(
                prompt=edit_body["prompt"],
                image_urls=edit_body["image_urls"],
                resolution=edit_body["resolution"],
                aspect_ratio=edit_body["aspect_ratio"],
            )
            edit_request_id = edit_result.request_id
            hf.download(edit_result.image_url, edited_frame)

            edited_url = hf.upload_file(edited_frame)
            i2v_body = build_seedance_i2v_body(
                prompt=default_i2v_prompt(target),
                image_url=edited_url,
                duration=i2v_duration,
                resolution=resolution,
            )
            i2v = hf.run_seedance_i2v(
                prompt=i2v_body["prompt"],
                image_url=i2v_body["image_url"],
                duration=i2v_body["duration"],
                resolution=i2v_body["resolution"],
            )
            request_id = i2v.request_id
            video_url = i2v.video_url
            backend = i2v.backend or "seedance-i2v"
            hf.download(video_url, out_path)
        finally:
            if owns:
                hf.close()

    return {
        "result_path": str(out_path),
        "frame_path": str(frame_path),
        "edited_frame_path": str(edited_frame),
        "backend": backend,
        "request_id": request_id,
        "edit_request_id": edit_request_id,
        "video_url": video_url,
        "estimated_cost_usd": 0.0 if use_mock else estimated,
        "billed_duration_s": float(i2v_duration),
        "duration_s": duration_s,
        "aspect": aspect,
        "resolution": resolution,
        "mock": use_mock,
        "start_s": float(start_s),
        "end_s": float(end_s),
        "region": region,
        "seedance_usd_per_s": SEEDANCE_I2V_USD_PER_S,
        "qwen_edit_usd": QWEN_EDIT_1K_USD,
    }


def mock_enabled() -> bool:
    return (os.environ.get("MOCK_HIGGSFIELD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _aspect_for_qwen(aspect: str) -> str:
    """Map WxH to the nearest Qwen-allowed aspect_ratio enum."""
    try:
        w_s, h_s = aspect.split(":", 1)
        w, h = float(w_s), float(h_s)
        ratio = w / max(h, 1.0)
    except ValueError:
        return "16:9"
    choices = {
        "1:1": 1.0,
        "2:3": 2 / 3,
        "3:2": 3 / 2,
        "3:4": 3 / 4,
        "4:3": 4 / 3,
        "7:9": 7 / 9,
        "9:7": 9 / 7,
        "9:16": 9 / 16,
        "16:9": 16 / 9,
        "21:9": 21 / 9,
    }
    return min(choices.items(), key=lambda kv: abs(kv[1] - ratio))[0]
