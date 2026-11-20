"""Warp a product PNG onto tracked mask regions."""

from __future__ import annotations

import cv2
import numpy as np


def mask_bbox(mask: np.ndarray, threshold: float = 0.3) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask >= threshold)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def mask_orientation_deg(mask: np.ndarray, threshold: float = 0.3) -> float:
    ys, xs = np.where(mask >= threshold)
    if len(xs) < 20:
        return 0.0
    pts = np.column_stack([xs.astype(np.float32), ys.astype(np.float32)])
    mean = pts.mean(axis=0)
    centered = pts - mean
    cov = centered.T @ centered / max(len(pts) - 1, 1)
    eigvals, eigvecs = np.linalg.eigh(cov)
    axis = eigvecs[:, int(np.argmax(eigvals))]
    return float(np.degrees(np.arctan2(axis[1], axis[0])))


def feather_mask(mask: np.ndarray, radius: int = 5) -> np.ndarray:
    soft = np.clip(mask.astype(np.float32), 0.0, 1.0)
    k = max(1, radius * 2 + 1)
    soft = cv2.GaussianBlur(soft, (k, k), sigmaX=radius / 2.0)
    return np.clip(soft, 0.0, 1.0)


def color_match_product(
    product_rgb: np.ndarray,
    product_alpha: np.ndarray,
    frame_rgb: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Shift product mean color toward the ring of pixels around the mask."""
    h, w = mask.shape
    binary = (mask >= 0.4).astype(np.uint8)
    if binary.sum() < 20:
        return product_rgb

    dilate = cv2.dilate(binary, np.ones((15, 15), np.uint8), iterations=1)
    ring = (dilate > 0) & (binary == 0)
    if ring.sum() < 20:
        return product_rgb

    surround = frame_rgb[ring].astype(np.float32)
    prod_px = product_rgb[product_alpha > 0.1]
    if len(prod_px) < 20:
        return product_rgb

    src_mean = prod_px.mean(axis=0)
    dst_mean = surround.mean(axis=0)
    # Keep matching mild so branding still reads.
    delta = 0.35 * (dst_mean - src_mean)
    matched = np.clip(product_rgb.astype(np.float32) + delta, 0, 255)
    return matched.astype(np.float32)


def warp_product_to_mask(
    product_rgba: np.ndarray,
    mask: np.ndarray,
    frame_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Return warped RGB and alpha maps sized to the frame."""
    h, w = frame_shape
    out_rgb = np.zeros((h, w, 3), dtype=np.float32)
    out_a = np.zeros((h, w), dtype=np.float32)

    bbox = mask_bbox(mask)
    if bbox is None:
        return out_rgb, out_a

    x1, y1, x2, y2 = bbox
    bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)

    prod = product_rgba.astype(np.float32)
    # Stretch the product into the mask box (milestone 1: prove coverage, not perspective).
    resized = cv2.resize(prod, (bw, bh), interpolation=cv2.INTER_AREA)
    angle = mask_orientation_deg(mask)
    # Keep the product mostly upright; only apply mild tilt from the mask.
    if abs(angle) < 15 or abs(abs(angle) - 90) < 15:
        angle = 0.0
    elif abs(angle) > 45:
        angle = angle - 90.0 if angle > 0 else angle + 90.0
    angle = float(np.clip(angle, -25.0, 25.0))

    center = (bw / 2.0, bh / 2.0)
    rot = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos, sin = abs(rot[0, 0]), abs(rot[0, 1])
    nw = max(int(bh * sin + bw * cos), 1)
    nh = max(int(bh * cos + bw * sin), 1)
    rot[0, 2] += (nw / 2.0) - center[0]
    rot[1, 2] += (nh / 2.0) - center[1]
    warped = cv2.warpAffine(
        resized,
        rot,
        (nw, nh),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )

    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    x0 = int(round(cx - warped.shape[1] / 2.0))
    y0 = int(round(cy - warped.shape[0] / 2.0))

    x_a = max(x0, 0)
    y_a = max(y0, 0)
    x_b = min(x0 + warped.shape[1], w)
    y_b = min(y0 + warped.shape[0], h)
    if x_a >= x_b or y_a >= y_b:
        return out_rgb, out_a

    sx = x_a - x0
    sy = y_a - y0
    patch = warped[sy : sy + (y_b - y_a), sx : sx + (x_b - x_a)]
    out_rgb[y_a:y_b, x_a:x_b] = patch[..., :3]
    out_a[y_a:y_b, x_a:x_b] = patch[..., 3] / 255.0
    return out_rgb, out_a


def composite_frame(
    frame_rgb: np.ndarray,
    mask: np.ndarray,
    product_rgba: np.ndarray,
    feather_radius: int = 5,
) -> np.ndarray:
    h, w = frame_rgb.shape[:2]
    soft = feather_mask(mask, radius=feather_radius)
    prod_rgb, prod_a = warp_product_to_mask(product_rgba, soft, (h, w))
    prod_rgb = color_match_product(prod_rgb, prod_a, frame_rgb.astype(np.float32), soft)

    alpha = np.clip(soft * prod_a, 0.0, 1.0)[..., None]
    base = frame_rgb.astype(np.float32)
    out = base * (1.0 - alpha) + prod_rgb * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def composite_clip(
    frames: list[np.ndarray],
    masks: list[np.ndarray],
    product_rgba: np.ndarray,
    feather_radius: int = 5,
) -> list[np.ndarray]:
    if len(frames) != len(masks):
        raise ValueError("frames and masks must have the same length")
    return [
        composite_frame(frame, mask, product_rgba, feather_radius=feather_radius)
        for frame, mask in zip(frames, masks)
    ]
