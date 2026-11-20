"""CLI for generative product placement."""

from __future__ import annotations

import argparse
import time

from product_placement.backends import MODELS
from product_placement.pipeline import swap


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product_placement",
        description="Swap a target object for a product image via a video-edit model.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    swap_p = sub.add_parser("swap", help="Generative video edit / object swap")
    swap_p.add_argument("--clip", required=True, help="Input video path")
    swap_p.add_argument("--target", required=True, help='Object text, e.g. "metal mug"')
    swap_p.add_argument("--product", required=True, help="Product image path (PNG/JPEG)")
    swap_p.add_argument("--out", required=True, help="Output video path")
    swap_p.add_argument(
        "--model",
        choices=sorted(MODELS),
        default="seedance",
        help="Edit backend (default: seedance)",
    )
    swap_p.add_argument(
        "--backend",
        choices=("auto", "seedance", "genjutsu"),
        default="auto",
        help="Only for --model seedance: seedance, genjutsu, or auto fallback",
    )
    swap_p.add_argument(
        "--resolution",
        choices=("480p", "720p", "1080p"),
        default="720p",
    )
    swap_p.add_argument("--prompt", default=None, help="Override the default swap prompt")
    swap_p.add_argument("--work-dir", default="runs/swap")
    swap_p.add_argument("--poll-timeout", type=float, default=1200.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "swap":
        parser.error(f"Unknown command: {args.command}")

    t0 = time.perf_counter()
    result = swap(
        clip=args.clip,
        target=args.target,
        product_image=args.product,
        out=args.out,
        model=args.model,
        backend=args.backend,
        resolution=args.resolution,
        prompt=args.prompt,
        work_dir=args.work_dir,
        poll_timeout_s=args.poll_timeout,
    )
    elapsed = time.perf_counter() - t0
    label = result.backend if result.model == "seedance" else result.model
    print(f"Wrote {result.out_path} via {label} in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
