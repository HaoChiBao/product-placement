"""Composite-step unit tests that need no model weights."""

import numpy as np

from product_placement.composite import (
    composite_clip,
    composite_frame,
    feather_mask,
    mask_bbox,
    warp_product_to_mask,
)


def _make_product(w: int = 40, h: int = 80) -> np.ndarray:
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = 180
    rgba[:, :, 2] = 40
    rgba[:, :, 3] = 255
    rgba[:10, :, 3] = 0  # transparent top edge
    return rgba


def test_mask_bbox_and_feather():
    mask = np.zeros((64, 64), dtype=np.float32)
    mask[20:40, 10:30] = 1.0
    assert mask_bbox(mask) == (10, 20, 30, 40)
    soft = feather_mask(mask, radius=3)
    assert soft.dtype == np.float32
    assert soft.max() <= 1.0
    assert soft.min() >= 0.0
    assert soft[30, 20] > soft[0, 0]


def test_warp_and_composite_on_synthetic_arrays():
    frame = np.full((120, 160, 3), 40, dtype=np.uint8)
    mask = np.zeros((120, 160), dtype=np.float32)
    mask[30:90, 60:100] = 1.0
    product = _make_product()

    rgb, alpha = warp_product_to_mask(product, mask, frame.shape[:2])
    assert rgb.shape == (120, 160, 3)
    assert alpha.shape == (120, 160)
    assert alpha.max() > 0.5

    out = composite_frame(frame, mask, product, feather_radius=2)
    assert out.shape == frame.shape
    assert out.dtype == np.uint8
    # Product region should diverge from the flat background.
    assert abs(int(out[60, 80].mean()) - int(frame[60, 80].mean())) > 10


def test_composite_clip_length():
    frames = [np.zeros((32, 32, 3), dtype=np.uint8) for _ in range(3)]
    masks = [np.zeros((32, 32), dtype=np.float32) for _ in range(3)]
    masks[1][8:24, 8:24] = 1.0
    out = composite_clip(frames, masks, _make_product(16, 24), feather_radius=1)
    assert len(out) == 3
    assert out[0].shape == (32, 32, 3)
