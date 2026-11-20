# product-placement

`swap(clip, target, product_image) -> new_clip` using a generative video-edit API.

Primary path: **ByteDance Seedance 2.5** video-edit on Higgsfield. Fallback: **Genjutsu object-swap**. Overlay/compositing is gone; the model does the replacement.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

`ffmpeg` is required for Genjutsu prep (upscale / trim).

### Credentials

Export one of:

```bash
export HF_KEY='YOUR_KEY_ID:YOUR_KEY_SECRET'
# or
export HF_API_KEY_ID='YOUR_KEY_ID'
export HF_API_KEY_SECRET='YOUR_KEY_SECRET'
```

Keys are read from the environment only. Create them in the [Higgsfield Console](https://console.higgsfield.ai). The account needs API credit for a live run.

## Live run

Once the key and credits are available:

```bash
python -m product_placement swap \
  --clip assets/samples/hand_held_cup.mp4 \
  --target "metal mug" \
  --product assets/samples/soda_can.png \
  --out out.mp4
```

Options:

- `--backend auto|seedance|genjutsu` (default `auto`: Seedance, then Genjutsu on failure)
- `--resolution 480p|720p|1080p` (default `720p`)
- `--prompt "..."` to override the default swap prompt
- `--poll-timeout` seconds (default 1200; live Seedance jobs can take ~10+ minutes)

### What the call does

1. Upload the clip and product image through Higgsfield `POST /files/generate-upload-url`, then PUT to the presigned URL.
2. `POST https://api.higgsfield.ai/bytedance/seedance-2.5/video-edit` with `{prompt, video_url, image_urls, resolution}` (no `duration`).
3. Poll `status_url` until `completed` / `failed` / `nsfw` / `canceled`, then download `video.url`.
4. On Seedance failure with `--backend auto`, prepare the clip for Genjutsu (upscale to ≥409,600 px/frame, trim to ≤30 s, require ≥4 s) and call `POST .../higgsfield/genjutsu/object-swap/v1.0`.

Default prompt:

> Replace the {target} in @Video1 with the product in @Image1. Keep the hands, camera motion, lighting and everything else identical.

## Sample assets

- `assets/samples/hand_held_cup.mp4` — Pexels [#6204700](https://www.pexels.com/video/person-holding-a-cup-of-hot-coffee-6204700/) (Pexels License)
- `assets/samples/soda_can.png` — self-made PNG with alpha

## Optional eval

Not on the critical path. Install `pip install -e '.[eval]'`, then call `product_placement.eval.evaluate_swap(...)` to score mask-region change vs outside-mask drift and flicker (Grounding DINO box on the prompt frame).

## Tests

```bash
pytest -q
```

Uses mocked HTTP only; CI does not call Higgsfield.

## Package

```
src/product_placement/
  higgsfield.py   # auth, upload, submit, poll, download
  prepare.py      # Genjutsu duration / min-pixel prep via ffmpeg
  pipeline.py     # swap()
  cli.py
  eval.py         # optional offline metrics
```
