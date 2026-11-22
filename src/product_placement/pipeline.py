"""End-to-end swap: detect -> track -> (optional generate) -> composite."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

from product_placement.composite import composite_clip
from product_placement.detect import Detection, detect_from_point, detect_from_text
from product_placement.device import resolve_device
from product_placement.generate import maybe_generate
from product_placement.io import load_rgba, read_video, write_video
from product_placement.track import DEFAULT_CHECKPOINT, DEFAULT_CONFIG, track_masks


@dataclass
class SwapResult:
    frames: list[np.ndarray]
    masks: list[np.ndarray]
    fps: float
    detection: Detection
    device: str


def swap(
    clip_path: str | Path,
    product_path: str | Path,
    out_path: str | Path | None = None,
    target: str | None = None,
    point: tuple[float, float] | None = None,
    frame_index: int = 0,
    device: str | None = None,
    work_dir: str | Path = "runs/latest",
    sam_checkpoint: str | Path = DEFAULT_CHECKPOINT,
    sam_config: str = DEFAULT_CONFIG,
    enable_generation: bool = False,
    feather_radius: int = 5,
) -> SwapResult:
    if (target is None) == (point is None):
        raise ValueError("Provide exactly one of target text or point (x, y)")

    torch_device = resolve_device(device)
    frames, fps = read_video(clip_path)
    product = load_rgba(product_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if target is not None:
        detection = detect_from_text(frames[frame_index], target, torch_device)
    else:
        assert point is not None
        detection = detect_from_point(point[0], point[1])

    masks = track_masks(
        frames=frames,
        detection=detection,
        frame_index=frame_index,
        device=torch_device,
        work_dir=work_dir,
        checkpoint=sam_checkpoint,
        config=sam_config,
    )

    generated = maybe_generate(
        frames=frames,
        masks=masks,
        product_rgba=product,
        enabled=enable_generation,
    )

    edited = composite_clip(
        generated.frames,
        generated.masks,
        product,
        feather_radius=feather_radius,
    )

    if out_path is not None:
        write_video(out_path, edited, fps)

    return SwapResult(
        frames=edited,
        masks=masks,
        fps=fps,
        detection=detection,
        device=str(torch_device),
    )
