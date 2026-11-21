"""Generative product placement via pluggable video-edit backends."""

from __future__ import annotations

from typing import Any

__version__ = "0.4.0"

# Keep OpenCV-backed suggest() lazy so `import product_placement` / swap-only
# installs work without the optional `suggest` extra (numpy + opencv).
from product_placement.pipeline import swap

__all__ = ["swap", "suggest", "Moment", "__version__"]


def __getattr__(name: str) -> Any:
    if name in {"suggest", "Moment"}:
        from product_placement.suggest import Moment, suggest

        return suggest if name == "suggest" else Moment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
