# product-placement

`swap(clip, target, product_image) -> new_clip` using a pluggable generative video-edit backend. Design notes for collaborators: [docs/DESIGN.md](docs/DESIGN.md).

```bash
python -m product_placement swap \
  --model seedance|wan27|wan-vace|wan-vace-modal \
  --clip assets/samples/hand_held_cup.mp4 \
  --target "metal mug" \
  --product assets/samples/soda_can.png \
  --out out.mp4
```

Original clip audio is muxed back onto the edited video for every model.

## Models

| `--model` | Provider | Credentials |
|-----------|----------|-------------|
| `seedance` (default) | Higgsfield Seedance 2.5 (+ Genjutsu fallback via `--backend`) | `HF_KEY` or `HF_API_KEY_ID` + `HF_API_KEY_SECRET` |
| `wan27` | Replicate `wan-video/wan-2.7-videoedit` | `REPLICATE_API_TOKEN` |
| `wan-vace` | fal `fal-ai/wan-vace-apps/video-edit` | `FAL_KEY` |
| `wan-vace-modal` | Self-hosted Wan 2.1 VACE 14B on Modal | `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` |

### seedance

Higgsfield `POST .../bytedance/seedance-2.5/video-edit`. `--backend auto|seedance|genjutsu` (default `auto`).

### wan27

Replicate input: `video`, `prompt`, `reference_image`, `resolution`, `audio_setting=origin`. Schema source: https://replicate.com/wan-video/wan-2.7-videoedit/api — no `disable_safety_checker` field.

### wan-vace

fal queue body: `prompt`, `video_url`, `image_urls`, `resolution`, `enable_safety_checker: false`. Schema source: fal `wan-vace-apps/video-edit`.

### wan-vace-modal (self-host)

Runs open **Wan-AI/Wan2.1-VACE-14B** (Diffusers id `Wan-AI/Wan2.1-VACE-14B-diffusers`) on Modal.

**VACE task:** reference-guided masked inpainting — source frames + a mask video of the target object (white = replace; masked pixels are grayed per VACE convention) + product as `reference_images`. Without a mask the whole frame is regenerated and the object usually is not swapped. Use a generous, single-blob mask (dilated convex hull of the target); a tight object-shaped mask makes VACE redraw the original object.

Optional `distill=True` on the Modal function enables the LightX2V step/CFG-distill LoRA (`Kijai/WanVideo_comfy`, `Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors`): use ~6 steps and `guidance_scale=1.0` for fast drafts (~3x faster per round), then a full run (20 steps, guidance 5) for the final, which follows the reference more faithfully.

**Entry point:** Diffusers [`WanVACEPipeline`](https://huggingface.co/docs/diffusers/api/pipelines/wan) (same task surface as Wan2.1 [`generate.py --task vace-14B`](https://github.com/Wan-Video/Wan2.1/blob/main/generate.py)).

Deploy (needs Modal account + GPU billing):

```bash
pip install modal
modal token set   # or export MODAL_TOKEN_ID / MODAL_TOKEN_SECRET
modal deploy modal_app/wan_vace.py
```

- **GPU:** H100 (80GB). Weights cached in Modal Volume `wan-vace-14b-weights`.
- **Rough cost:** H100 ≈ $0.0011/s (~$3.95/hr). A short 720p clip (~3–8 min wall time once warm) is roughly **$0.20–$0.60**; cold start + weight load can push a first clip toward **~$1–3**. Not verified live in this repo.

Client looks up `modal.Function.from_name("product-placement-wan-vace", "edit")`. Missing `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` raises an error that names them.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

`ffmpeg` is required (Genjutsu prep + audio mux).

## Sample assets

- `assets/samples/hand_held_cup.mp4` — Pexels [#6204700](https://www.pexels.com/video/person-holding-a-cup-of-hot-coffee-6204700/) (Pexels License)
- `assets/samples/soda_can.png` — self-made PNG with alpha

## Optional eval

`pip install -e '.[eval]'`, then `product_placement.eval.evaluate_swap(...)`.

## Tests

```bash
pytest -q
```

Mocked HTTP / Modal client routing only; CI does not call live APIs or deploy Modal.

## Package

```
src/product_placement/
  backends.py     # seedance / wan27 / wan-vace / wan-vace-modal
  audio.py        # mux original audio
  higgsfield.py
  prepare.py
  pipeline.py
  cli.py
  eval.py
modal_app/
  wan_vace.py     # Modal deploy target
```
