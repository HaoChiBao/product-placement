"""Create a self-made product PNG and, if needed, a synthetic demo clip."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def make_soda_can_png(path: Path, width: int = 220, height: int = 420) -> None:
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    cx = width // 2
    rx = int(width * 0.38)
    top, bottom = int(height * 0.08), int(height * 0.92)

    for y in range(height):
        for x in range(width):
            dx = abs(x - cx)
            if dx > rx:
                continue
            # Cylinder shading.
            t = dx / max(rx, 1)
            shade = 1.0 - 0.45 * (t**2)
            if top <= y <= bottom:
                # Red body with white band.
                band = int(height * 0.42) <= y <= int(height * 0.58)
                if band:
                    color = (245, 245, 245)
                else:
                    color = (int(200 * shade), int(20 * shade), int(35 * shade))
                rgba[y, x, :3] = color
                rgba[y, x, 3] = 255
            elif int(height * 0.04) <= y < top and dx < int(rx * 0.75):
                # Silver lid.
                rgba[y, x, :3] = (int(190 * shade), int(190 * shade), int(195 * shade))
                rgba[y, x, 3] = 255

    # Script-like label mark (not a trademark).
    cv2.putText(
        rgba,
        "SODA",
        (int(width * 0.22), int(height * 0.52)),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        (220, 30, 40, 255),
        3,
        cv2.LINE_AA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))


def make_synthetic_cup_clip(path: Path, seconds: float = 5.0, fps: float = 12.0) -> None:
    """Locked-off table with a mostly still cup; tiny sway for tracking proof."""
    w, h = 640, 360
    n = int(seconds * fps)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (w, h),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        # Wood-ish table gradient.
        for y in range(h):
            frame[y, :] = (42 + y // 8, 28 + y // 10, 18 + y // 12)
        sway = int(2 * np.sin(i / fps * 1.2))
        cx, cy = 320 + sway, 210
        # Saucer
        cv2.ellipse(frame, (cx, cy + 55), (70, 18), 0, 0, 360, (210, 210, 210), -1)
        # Cup body
        cv2.ellipse(frame, (cx, cy + 20), (48, 55), 0, 0, 360, (235, 235, 240), -1)
        cv2.ellipse(frame, (cx, cy - 25), (48, 16), 0, 0, 360, (250, 250, 252), -1)
        # Coffee surface
        cv2.ellipse(frame, (cx, cy - 25), (40, 12), 0, 0, 360, (70, 45, 30), -1)
        # Handle
        cv2.ellipse(frame, (cx + 55, cy + 5), (18, 28), 0, -70, 70, (230, 230, 235), 7)
        writer.write(frame)
    writer.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", default="assets/samples/soda_can.png")
    parser.add_argument("--clip", default="")
    args = parser.parse_args()
    make_soda_can_png(Path(args.product))
    print(f"Wrote {args.product}")
    if args.clip:
        make_synthetic_cup_clip(Path(args.clip))
        print(f"Wrote {args.clip}")


if __name__ == "__main__":
    main()
