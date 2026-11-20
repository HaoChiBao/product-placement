"""Propagate a target mask across frames with SAM 2."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from product_placement.detect import Detection
from product_placement.io import extract_jpeg_frames


DEFAULT_CHECKPOINT = "checkpoints/sam2.1_hiera_tiny.pt"
DEFAULT_CONFIG = "configs/sam2.1/sam2.1_hiera_t.yaml"


def _load_predictor(checkpoint: str | Path, config: str, device: torch.device):
    from sam2.build_sam import build_sam2_video_predictor

    return build_sam2_video_predictor(config, str(checkpoint), device=str(device))


def track_masks(
    frames: list[np.ndarray],
    detection: Detection,
    frame_index: int,
    device: torch.device,
    work_dir: str | Path,
    checkpoint: str | Path = DEFAULT_CHECKPOINT,
    config: str = DEFAULT_CONFIG,
) -> list[np.ndarray]:
    """Return one float32 HxW mask in [0, 1] per frame."""
    if frame_index < 0 or frame_index >= len(frames):
        raise IndexError(f"frame_index {frame_index} out of range for {len(frames)} frames")

    work_dir = Path(work_dir)
    jpeg_dir = extract_jpeg_frames(frames, work_dir / "jpeg_frames")
    predictor = _load_predictor(checkpoint, config, device)

    state = predictor.init_state(video_path=str(jpeg_dir))
    object_id = 1

    if detection.point_xy is not None:
        points = detection.point_xy.reshape(1, 2)
        labels = np.array([1], dtype=np.int32)
        predictor.add_new_points_or_box(
            inference_state=state,
            frame_idx=frame_index,
            obj_id=object_id,
            points=points,
            labels=labels,
        )
    else:
        predictor.add_new_points_or_box(
            inference_state=state,
            frame_idx=frame_index,
            obj_id=object_id,
            box=detection.box_xyxy,
        )

    masks: list[np.ndarray | None] = [None] * len(frames)
    for idx, _obj_ids, video_res_masks in predictor.propagate_in_video(state):
        mask = video_res_masks[0, 0].detach().float().cpu().numpy()
        masks[idx] = np.clip(mask, 0.0, 1.0).astype(np.float32)

    missing = [i for i, m in enumerate(masks) if m is None]
    if missing:
        raise RuntimeError(f"SAM 2 did not return masks for frames: {missing[:8]}")
    return masks  # type: ignore[return-value]
