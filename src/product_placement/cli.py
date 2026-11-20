"""CLI entrypoints for product placement."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from product_placement.io import read_video
from product_placement.pipeline import swap
from product_placement.track import DEFAULT_CHECKPOINT, DEFAULT_CONFIG


def _parse_point(value: str) -> tuple[float, float]:
    parts = value.replace(" ", "").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("point must be x,y")
    return float(parts[0]), float(parts[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product_placement",
        description="Swap a tracked object in a clip for a product image (milestone 1).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    swap_p = sub.add_parser("swap", help="Detect/track a target and composite a product")
    swap_p.add_argument("--clip", required=True, help="Input video path")
    swap_p.add_argument("--product", required=True, help="Product PNG with alpha")
    swap_p.add_argument("--out", required=True, help="Output video path")
    swap_p.add_argument("--target", default=None, help='Text prompt, e.g. "coffee cup"')
    swap_p.add_argument(
        "--point",
        type=_parse_point,
        default=None,
        help="Click point x,y on --frame (alternative to --target)",
    )
    swap_p.add_argument("--frame", type=int, default=0, help="Prompt frame index")
    swap_p.add_argument("--device", default=None, help="cpu or cuda (default: auto)")
    swap_p.add_argument("--work-dir", default="runs/latest", help="Scratch directory")
    swap_p.add_argument("--sam-checkpoint", default=DEFAULT_CHECKPOINT)
    swap_p.add_argument("--sam-config", default=DEFAULT_CONFIG)
    swap_p.add_argument(
        "--side-by-side",
        default=None,
        help="Optional path for an original|edited still",
    )
    swap_p.add_argument(
        "--mask-overlay",
        default=None,
        help="Optional path for a mask overlay still",
    )
    return parser


def _save_proof_stills(
    clip_path: str,
    result_frames: list[np.ndarray],
    masks: list[np.ndarray],
    frame_index: int,
    side_by_side: str | None,
    mask_overlay: str | None,
) -> None:
    originals, _ = read_video(clip_path)
    idx = min(max(frame_index, 0), len(originals) - 1)
    orig = originals[idx]
    edited = result_frames[idx]
    mask = masks[idx]

    if side_by_side:
        pair = np.concatenate([orig, edited], axis=1)
        Path(side_by_side).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(
            side_by_side,
            cv2.cvtColor(pair, cv2.COLOR_RGB2BGR),
        )

    if mask_overlay:
        overlay = orig.astype(np.float32).copy()
        tint = np.zeros_like(overlay)
        tint[..., 1] = 255
        alpha = np.clip(mask, 0, 1)[..., None] * 0.45
        blended = overlay * (1 - alpha) + tint * alpha
        Path(mask_overlay).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(
            mask_overlay,
            cv2.cvtColor(np.clip(blended, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR),
        )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "swap":
        if (args.target is None) == (args.point is None):
            parser.error("Provide exactly one of --target or --point")

        t0 = time.perf_counter()
        result = swap(
            clip_path=args.clip,
            product_path=args.product,
            out_path=args.out,
            target=args.target,
            point=args.point,
            frame_index=args.frame,
            device=args.device,
            work_dir=args.work_dir,
            sam_checkpoint=args.sam_checkpoint,
            sam_config=args.sam_config,
        )
        elapsed = time.perf_counter() - t0
        print(
            f"Wrote {args.out} ({len(result.frames)} frames @ {result.fps:.2f} fps) "
            f"on {result.device} in {elapsed:.1f}s"
        )
        print(
            f"Detection: label={result.detection.label!r} "
            f"score={result.detection.score:.3f} "
            f"box={result.detection.box_xyxy.tolist()}"
        )
        if args.side_by_side or args.mask_overlay:
            _save_proof_stills(
                clip_path=args.clip,
                result_frames=result.frames,
                masks=result.masks,
                frame_index=args.frame,
                side_by_side=args.side_by_side,
                mask_overlay=args.mask_overlay,
            )
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
