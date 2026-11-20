"""swap(clip, target, product_image) -> new_clip via a pluggable edit model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from product_placement.backends import MODELS, EditResult
from product_placement.higgsfield import default_swap_prompt


@dataclass
class SwapResult:
    out_path: Path
    model: str
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
    model: str = "seedance",
    backend: str = "auto",
    resolution: str = "720p",
    prompt: str | None = None,
    work_dir: str | Path = "runs/swap",
    poll_timeout_s: float = 1200.0,
    **kwargs,
) -> SwapResult:
    """Route to MODELS[model]; muxing of original audio is inside each backend."""
    if model not in MODELS:
        raise ValueError(f"Unknown model {model!r}; choose from {sorted(MODELS)}")

    clip = Path(clip)
    product_image = Path(product_image)
    out = Path(out)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    text = prompt or default_swap_prompt(target)

    edit = MODELS[model]
    call_kwargs = {
        "out_path": out,
        "work_dir": work_dir,
        "resolution": resolution,
        **kwargs,
    }
    if model == "seedance":
        call_kwargs["backend"] = backend
        call_kwargs["poll_timeout_s"] = poll_timeout_s
    elif model in {"wan27", "wan-vace"}:
        call_kwargs["poll_timeout_s"] = poll_timeout_s

    raw = edit(clip, product_image, text, **call_kwargs)
    if isinstance(raw, EditResult):
        return SwapResult(
            out_path=raw.path,
            model=model,
            backend=raw.backend or model,
            request_id=raw.request_id,
            video_url=raw.video_url,
            prompt=text,
        )
    return SwapResult(
        out_path=raw,
        model=model,
        backend=model,
        request_id="",
        video_url="",
        prompt=text,
    )
