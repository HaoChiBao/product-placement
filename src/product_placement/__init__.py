"""Generative product placement via pluggable video-edit backends."""

__version__ = "0.4.0"

from product_placement.pipeline import swap
from product_placement.suggest import Moment, suggest

__all__ = ["swap", "suggest", "Moment", "__version__"]
