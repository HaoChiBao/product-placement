"""Eval timeline/resolution alignment without model weights."""

from __future__ import annotations

import numpy as np

from product_placement.eval import align_edited_to_original


def test_align_resamples_fps_and_resolution():
    # Mimic verified live shapes: 1080x1920@25fps source vs 720x1280@24fps output.
    # Keep arrays tiny for the unit test.
    original = [np.full((48, 27, 3), i, dtype=np.uint8) for i in range(25)]
    edited = [np.full((32, 18, 3), 100 + i, dtype=np.uint8) for i in range(24)]

    def resize(frame, w, h):
        # Nearest-neighbor style tile/crop for the test.
        out = np.zeros((h, w, 3), dtype=np.uint8)
        hh = min(h, frame.shape[0])
        ww = min(w, frame.shape[1])
        out[:hh, :ww] = frame[:hh, :ww]
        out[:] = frame[0, 0]
        return out

    aligned = align_edited_to_original(original, edited, 25.0, 24.0, resize=resize)
    assert len(aligned) == 25
    assert aligned[0].shape == (48, 27, 3)
    # t=0 -> edited[0]; t~1.0s -> edited[~24]
    assert aligned[0][0, 0, 0] == 100
    assert aligned[24][0, 0, 0] == 100 + 23
