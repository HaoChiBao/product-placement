"""swap(clip, target, product_image) -> new_clip via Higgsfield."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from product_placement.higgsfield import (
    HiggsfieldClient,
    HiggsfieldError,
    JobResult,
    default_swap_prompt,
)
from product_placement.prepare import prepare_for_genjutsu


@dataclass
class SwapResult:
    out_path: Path
    backend: str
    request_id: str
    video_url: str
    prompt: str


def swap(
    clip: str | Path,
    target: str,
    product_image: str | Path,
    out: str | Path,
    *,
    backend: str = "auto",
    resolution: str = "720p",
    prompt: str | None = None,
    work_dir: str | Path = "runs/swap",
    poll_timeout_s: float = 900.0,
    client: HiggsfieldClient | None = None,
) -> SwapResult:
    """Upload inputs, run Seedance 2.5 (or Genjutsu fallback), download result."""
    if backend not in {"auto", "seedance", "genjutsu"}:
        raise ValueError("backend must be auto, seedance, or genjutsu")

    clip = Path(clip)
    product_image = Path(product_image)
    out = Path(out)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    text = prompt or default_swap_prompt(target)

    owns = client is None
    hf = client or HiggsfieldClient()
    try:
        product_url = hf.upload_file(product_image)

        def _seedance(video_path: Path) -> JobResult:
            video_url = hf.upload_file(video_path)
            return hf.run_seedance(
                prompt=text,
                video_url=video_url,
                image_url=product_url,
                resolution=resolution,
                poll_timeout_s=poll_timeout_s,
            )

        def _genjutsu(video_path: Path) -> JobResult:
            prepared = prepare_for_genjutsu(video_path, work_dir / "genjutsu_input.mp4")
            video_url = hf.upload_file(prepared)
            return hf.run_genjutsu(
                prompt=text,
                video_url=video_url,
                image_url=product_url,
                resolution=resolution,
                poll_timeout_s=poll_timeout_s,
            )

        if backend == "seedance":
            result = _seedance(clip)
        elif backend == "genjutsu":
            result = _genjutsu(clip)
        else:
            try:
                result = _seedance(clip)
            except HiggsfieldError as exc:
                print(f"Seedance failed ({exc}); falling back to Genjutsu")
                result = _genjutsu(clip)

        hf.download(result.video_url, out)
        return SwapResult(
            out_path=out,
            backend=result.backend,
            request_id=result.request_id,
            video_url=result.video_url,
            prompt=text,
        )
    finally:
        if owns:
            hf.close()
