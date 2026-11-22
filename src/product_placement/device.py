"""Resolve the torch device used for inference."""

from __future__ import annotations

import torch


def resolve_device(requested: str | None = None) -> torch.device:
    if requested:
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")
