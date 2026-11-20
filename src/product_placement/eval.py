"""Optional offline eval (not on the swap critical path).

Measures mask-region change vs outside-mask drift and frame-to-frame flicker.
Uses Grounding DINO for a target box on the prompt frame. Install with:
  pip install -e '.[eval]'

Seedance output can differ in resolution and fps from the source (verified:
1080x1920@25fps -> 720x1280@24fps), so frames are resampled before scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalScores:
    mask_region_change: float
    outside_mask_drift: float
    flicker: float


def align_edited_to_original(
    original_frames: list,
    edited_frames: list,
    original_fps: float,
    edited_fps: float,
    resize,
):
    """Resample edited frames onto the original timeline and resolution.

    `resize(frame, width, height)` returns a frame matching the original size.
    """
    if not original_frames or not edited_frames:
        raise ValueError("both clips need at least one frame")
    oh, ow = original_frames[0].shape[:2]
    original_fps = max(float(original_fps), 1e-6)
    edited_fps = max(float(edited_fps), 1e-6)

    aligned = []
    for i in range(len(original_frames)):
        t = i / original_fps
        j = int(round(t * edited_fps))
        j = min(max(j, 0), len(edited_frames) - 1)
        frame = edited_frames[j]
        if frame.shape[0] != oh or frame.shape[1] != ow:
            frame = resize(frame, ow, oh)
        aligned.append(frame)
    return aligned


def evaluate_swap(
    original_clip: str | Path,
    edited_clip: str | Path,
    target: str,
    *,
    frame_index: int = 0,
) -> EvalScores:
    try:
        import cv2
        import numpy as np
        import torch
        from PIL import Image
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "Eval extras missing. Install with: pip install -e '.[eval]'"
        ) from exc

    def read_video(path: Path):
        cap = cv2.VideoCapture(str(path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 24.0
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        cap.release()
        if not frames:
            raise ValueError(f"no frames in {path}")
        return frames, fps

    original, original_fps = read_video(Path(original_clip))
    edited, edited_fps = read_video(Path(edited_clip))
    edited = align_edited_to_original(
        original,
        edited,
        original_fps,
        edited_fps,
        resize=lambda frame, w, h: cv2.resize(
            frame, (w, h), interpolation=cv2.INTER_AREA
        ),
    )
    n = len(original)
    idx = min(max(frame_index, 0), n - 1)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model_id = "IDEA-Research/grounding-dino-tiny"
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id).to(device)
    model.eval()

    image = Image.fromarray(original[idx])
    text = target.strip().lower()
    if not text.endswith("."):
        text += "."
    inputs = processor(images=image, text=text, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.inference_mode():
        outputs = model(**inputs)
    det = processor.post_process_grounded_object_detection(
        outputs,
        inputs["input_ids"],
        box_threshold=0.25,
        text_threshold=0.2,
        target_sizes=[image.size[::-1]],
    )[0]
    if len(det["boxes"]) == 0:
        raise RuntimeError(f'no detection for "{target}"')
    box = det["boxes"][int(det["scores"].argmax())].detach().cpu().numpy()
    x1, y1, x2, y2 = box.astype(int)
    h, w = original[idx].shape[:2]
    mask = np.zeros((h, w), dtype=bool)
    mask[max(y1, 0) : min(y2, h), max(x1, 0) : min(x2, w)] = True
    outside = ~mask

    def mean_abs(a, b, region):
        if region.sum() == 0:
            return 0.0
        diff = np.abs(a.astype(np.float32) - b.astype(np.float32))
        return float(diff[region].mean())

    inside, outside_vals, flicker = [], [], []
    for i in range(n):
        inside.append(mean_abs(original[i], edited[i], mask))
        outside_vals.append(mean_abs(original[i], edited[i], outside))
        if i > 0:
            flicker.append(mean_abs(edited[i - 1], edited[i], mask))

    return EvalScores(
        mask_region_change=float(np.mean(inside)),
        outside_mask_drift=float(np.mean(outside_vals)),
        flicker=float(np.mean(flicker)) if flicker else 0.0,
    )
