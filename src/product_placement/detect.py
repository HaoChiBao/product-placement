"""Locate the target object with Grounding DINO or a click point."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from PIL import Image


@dataclass
class Detection:
    box_xyxy: np.ndarray  # float32 [x1, y1, x2, y2]
    score: float
    label: str
    point_xy: np.ndarray | None = None  # float32 [x, y] when from a click


def detect_from_text(
    frame_rgb: np.ndarray,
    prompt: str,
    device: torch.device,
    model_id: str = "IDEA-Research/grounding-dino-tiny",
    box_threshold: float = 0.25,
    text_threshold: float = 0.20,
) -> Detection:
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id).to(device)
    model.eval()

    image = Image.fromarray(frame_rgb)
    # Grounding DINO expects lowercase text ending with a period.
    text = prompt.strip().lower()
    if not text.endswith("."):
        text = text + "."

    inputs = processor(images=image, text=text, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.inference_mode():
        outputs = model(**inputs)

    results = processor.post_process_grounded_object_detection(
        outputs,
        inputs["input_ids"],
        box_threshold=box_threshold,
        text_threshold=text_threshold,
        target_sizes=[image.size[::-1]],
    )[0]

    boxes = results["boxes"].detach().cpu().numpy()
    scores = results["scores"].detach().cpu().numpy()
    labels = results.get("labels") or results.get("text_labels") or []

    if len(boxes) == 0:
        raise RuntimeError(f'Grounding DINO found no match for "{prompt}"')

    best = int(np.argmax(scores))
    label = labels[best] if best < len(labels) else prompt
    return Detection(
        box_xyxy=boxes[best].astype(np.float32),
        score=float(scores[best]),
        label=str(label),
    )


def detect_from_point(x: float, y: float) -> Detection:
    return Detection(
        box_xyxy=np.array([x, y, x, y], dtype=np.float32),
        score=1.0,
        label="point",
        point_xy=np.array([x, y], dtype=np.float32),
    )
