# product-placement

Find moments in a full video where a product can sit, then `swap(clip, target, product_image) -> new_clip` with a pluggable generative backend. Design notes: [docs/DESIGN.md](docs/DESIGN.md).

Based on the generative-swap work (`haochibao/generative-swap-760a` / PR #2).

## Web UI

Minimal Next.js page (Cult UI / shadcn components) that uploads or points at a video, runs `suggest()`, and lets you scrub each 5 s pick.

```bash
# from repo root, with the Python package + venv already installed
cd web && npm install && npm run dev
```

Open http://localhost:3000. API routes shell out to `python -m product_placement suggest` and ffmpeg for clips. No auth or DB.

## Suggest (pre-swap)

`suggest(video_path, n=5) -> list[Moment]` ranks 3–5 spread-out ~5 s windows that are stable, long enough to bill/edit, and have a face-free placement region (table / prop / similar) ready for the crop-and-stitch swap path.

Method (all local, no API key):

1. Shot cuts from HSV histogram correlation
2. Prefer ~5 s windows inside long shots; keep whole shots down to ~2 s when that’s all there is (swap can pad/hold)
3. Drop shaky windows (camera translation from an affine fit — actor motion is ignored)
4. YuNet faces + YOLOX-S (COCO) props/surfaces
5. Prefer replaceable props (wine glass, bottle, cup, …), else a face-free patch on a surface
6. Greedy score + time/shot spread; write a contact sheet

```bash
python -m product_placement suggest \
  --video path/to/film.mp4 \
  -n 5 \
  --out-dir runs/suggest
```

Writes `moments.json`, `contact_sheet.jpg`, and `frames/moment_XX.jpg`. Each moment has `start_s` / `end_s`, `region` `{x,y,w,h}` in source pixels, `whats_there`, `score`, and `reason`.

ONNX weights download on first run into `checkpoints/` (if present) or `~/.cache/product-placement/models/` (OpenCV Zoo YuNet + YOLOX-S). No paid key.

## Swap

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

`ffmpeg` is required (Genjutsu prep + audio mux). Suggest needs OpenCV + NumPy (in `requirements.txt`).

## Sample assets

- `assets/samples/hand_held_cup.mp4` — Pexels [#6204700](https://www.pexels.com/video/person-holding-a-cup-of-hot-coffee-6204700/) (Pexels License)
- `assets/samples/soda_can.png` — self-made PNG with alpha

For a freely licensed full-film smoke test, download Blender’s *Tears of Steel* (CC-BY) at runtime, e.g. `https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov` — do not commit it.

## Optional eval

`pip install -e '.[eval]'`, then `product_placement.eval.evaluate_swap(...)`.

## Tests

```bash
pytest -q
```

Suggest unit tests are pure logic (spread, face clearance, `n`); swap tests mock HTTP / Modal. No network or GPU required for `pytest`.

## Package

```
src/product_placement/
  suggest.py      # suggest(video) -> moments + contact sheet
  vision.py       # shots, YuNet faces, YOLOX props, motion
  models_store.py # ONNX download/cache
  backends.py     # seedance / wan27 / wan-vace / wan-vace-modal
  audio.py
  higgsfield.py
  prepare.py
  pipeline.py
  cli.py
  eval.py
modal_app/
  wan_vace.py
```
