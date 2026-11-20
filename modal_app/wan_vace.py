"""Self-host Wan 2.1 VACE 14B on Modal (diffusers WanVACEPipeline).

Entry point: Hugging Face Diffusers `WanVACEPipeline` with
`Wan-AI/Wan2.1-VACE-14B-diffusers` (same weights as `Wan-AI/Wan2.1-VACE-14B`).
Docs: https://huggingface.co/docs/diffusers/api/pipelines/wan
Official CLI twin: https://github.com/Wan-Video/Wan2.1/blob/main/generate.py
(`--task vace-14B`, `--src_video` / `--src_mask` / `--src_ref_images`).

Task choice (object swap): reference-guided masked V2V — keep the source
video as conditioning frames, use a full-frame white mask (generate), and
pass the product PNG as `reference_images`. Grounding/SAM masks from the
old overlay path are gone on this branch, so we do not build a region mask.

Deploy:
  modal deploy modal_app/wan_vace.py

Lookup from the client:
  modal.Function.from_name("product-placement-wan-vace", "edit")
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import modal

APP_NAME = "product-placement-wan-vace"
MODEL_ID = "Wan-AI/Wan2.1-VACE-14B-diffusers"
VOLUME_PATH = "/models"
WEIGHTS_DIR = f"{VOLUME_PATH}/Wan2.1-VACE-14B-diffusers"

# H100 80GB fits 14B bf16; A100-80GB is the fallback if H100 is scarce.
GPU = "H100"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0")
    .pip_install(
        "torch==2.5.1",
        "torchvision==0.20.1",
        "diffusers>=0.33.0",
        "transformers>=4.46.0",
        "accelerate>=1.1.0",
        "huggingface-hub>=0.26.0",
        "safetensors",
        "sentencepiece",
        "imageio",
        "imageio-ffmpeg",
        "opencv-python-headless==4.10.0.84",
        "Pillow",
        "numpy==1.26.4",
        "ftfy",
    )
)

volume = modal.Volume.from_name("wan-vace-14b-weights", create_if_missing=True)
app = modal.App(APP_NAME, image=image)


def _ensure_weights() -> str:
    """Download weights into the Modal volume once, then reuse."""
    target = Path(WEIGHTS_DIR)
    marker = target / "model_index.json"
    if marker.is_file():
        return str(target)
    target.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=MODEL_ID,
        local_dir=str(target),
        local_dir_use_symlinks=False,
    )
    volume.commit()
    return str(target)


def _load_pipeline(ckpt_dir: str):
    import torch
    from diffusers import AutoencoderKLWan, WanVACEPipeline
    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler

    vae = AutoencoderKLWan.from_pretrained(
        ckpt_dir, subfolder="vae", torch_dtype=torch.float32
    )
    pipe = WanVACEPipeline.from_pretrained(
        ckpt_dir, vae=vae, torch_dtype=torch.bfloat16
    )
    # 5.0 for 720P per Diffusers Wan docs.
    pipe.scheduler = UniPCMultistepScheduler.from_config(
        pipe.scheduler.config, flow_shift=5.0
    )
    pipe.to("cuda")
    return pipe


def _decode_video_frames(video_bytes: bytes, max_frames: int = 81):
    import cv2
    import numpy as np
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
        tmp.write(video_bytes)
        tmp.flush()
        cap = cv2.VideoCapture(tmp.name)
        if not cap.isOpened():
            raise RuntimeError("Could not open input video bytes")
        frames = []
        fps = cap.get(cv2.CAP_PROP_FPS) or 16.0
        while len(frames) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(rgb))
        cap.release()
    if not frames:
        raise RuntimeError("Input video has no frames")
    # Wan VACE expects frame_num = 4k + 1.
    n = len(frames)
    target = min(max_frames, n - ((n - 1) % 4))
    if target < 5:
        target = 5 if n >= 5 else n
        while target > 1 and (target - 1) % 4 != 0:
            target -= 1
    frames = frames[:target]
    return frames, float(fps)


def _resize_frames(frames, height: int, width: int):
    return [f.convert("RGB").resize((width, height)) for f in frames]


def _full_generate_mask(num_frames: int, height: int, width: int):
    """White = generate. No region mask available → edit the whole frame."""
    from PIL import Image

    white = Image.new("L", (width, height), 255)
    return [white] * num_frames


def _export_mp4(frames, fps: float) -> bytes:
    from diffusers.utils import export_to_video

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.mp4"
        export_to_video(frames, str(out), fps=max(fps, 1.0))
        return out.read_bytes()


@app.function(
    gpu=GPU,
    timeout=60 * 40,
    volumes={VOLUME_PATH: volume},
    memory=65536,
)
def edit(
    video_bytes: bytes,
    reference_image_bytes: bytes,
    prompt: str,
    *,
    height: int = 720,
    width: int = 1280,
    num_inference_steps: int = 30,
    guidance_scale: float = 5.0,
) -> bytes:
    """Run reference-guided VACE edit; return mp4 bytes. No safety checker."""
    from PIL import Image

    ckpt = _ensure_weights()
    # Cache the pipeline on the container between warm calls.
    if not hasattr(edit, "_pipe"):
        edit._pipe = _load_pipeline(ckpt)
    pipe = edit._pipe

    frames, fps = _decode_video_frames(video_bytes)
    frames = _resize_frames(frames, height, width)
    mask = _full_generate_mask(len(frames), height, width)
    ref = Image.open(io.BytesIO(reference_image_bytes)).convert("RGB")

    result = pipe(
        video=frames,
        mask=mask,
        reference_images=[ref],
        prompt=prompt,
        height=height,
        width=width,
        num_frames=len(frames),
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
    )
    out_frames = result.frames[0]
    return _export_mp4(out_frames, fps)


@app.local_entrypoint()
def main():
    print(
        f"Deployed app={APP_NAME!r} fn=edit gpu={GPU} model={MODEL_ID}. "
        "Call via modal.Function.from_name(...).remote(video, image, prompt)."
    )
