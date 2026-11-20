"""Optional offline eval (not on the swap critical path).

Measures mask-region change vs outside-mask drift and frame-to-frame flicker.
Uses Grounding DINO for a target box on the prompt frame. Install with:
  pip install -e '.[eval]'
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalScores:
    mask_region_change: float
    outside_mask_drift: float
    flicker: float


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

    def read_frames(path: Path):
        cap = cv2.VideoCapture(str(path))
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        cap.release()
        if not frames:
            raise ValueError(f"no frames in {path}")
        return frames

    original = read_frames(Path(original_clip))
    edited = read_frames(Path(edited_clip))
    n = min(len(original), len(edited))
    original, edited = original[:n], edited[:n]
    # Seedance may emit a different resolution than the source; align to original.
    oh, ow = original[0].shape[:2]
    aligned = []
    for frame in edited:
        if frame.shape[0] != oh or frame.shape[1] != ow:
            frame = cv2.resize(frame, (ow, oh), interpolation=cv2.INTER_AREA)
        aligned.append(frame)
    edited = aligned
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
