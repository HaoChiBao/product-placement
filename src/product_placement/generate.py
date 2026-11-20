"""Placeholder for a future generative inpainting / video rewrite step.

Milestone 1 leaves this as a no-op so tracking output can feed compositing
directly. Later milestones can replace `maybe_generate` without rewriting
detect/track/composite.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class GenerationResult:
    frames: list[np.ndarray]
    masks: list[np.ndarray]
    used_generation: bool = False


def maybe_generate(
    frames: list[np.ndarray],
    masks: list[np.ndarray],
    product_rgba: np.ndarray,
    enabled: bool = False,
) -> GenerationResult:
    """Slot for Seedance / video inpainting between tracking and compositing."""
    del product_rgba  # reserved for later generative conditioning
    if not enabled:
        return GenerationResult(frames=frames, masks=masks, used_generation=False)
    raise NotImplementedError(
        "Generative inpainting is not part of milestone 1. "
        "Pass enabled=False or implement a backend here."
    )
