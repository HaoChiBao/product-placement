# product-placement

Find moments in a full video where a product can sit, then `swap(clip, target, product_image) -> new_clip` with a pluggable generative backend. Design notes: [docs/DESIGN.md](docs/DESIGN.md).

Based on the generative-swap work (`haochibao/generative-swap-760a` / PR #2).

## Railway / Docker (hosted prototype)

One service: Next.js UI + Python package + `ffmpeg` + ONNX models (downloaded at **image build** into `/app/models`). CPU only.

```bash
docker build -t product-placement .
docker run --rm -p 3000:3000 \
  -e MOCK_HIGGSFIELD=1 \
  -e PORT=3000 \
  product-placement
```

Open http://localhost:$PORT (default **3000**). Railway injects `$PORT` automatically — the entrypoint honors it.

### Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `PORT` | yes (Railway sets it) | HTTP listen port |
| `HF_KEY` | for live swaps | Higgsfield key as `ID:SECRET` |
| `HF_API_KEY_ID` + `HF_API_KEY_SECRET` | alt to `HF_KEY` | Same auth; both required if `HF_KEY` unset |
| `MOCK_HIGGSFIELD` | no | `1`/`true` → unpaid mock edit (product overlay) for UI tests |
| `DATA_DIR` | no | Job/upload/output root (default `/data`) |
| `MODEL_CACHE_DIR` | no | ONNX cache (default `/app/models` in the image) |
| `REPO_ROOT` | no | Repo root for sample paths (default `/app`) |
| `PYTHON_BIN` | no | Python executable (default `python3`; **not** the repo `.venv`) |

Auth is resolved by `product_placement.higgsfield.auth_header`: prefer `HF_KEY`, else `HF_API_KEY_ID` + `HF_API_KEY_SECRET`.

### Railway notes

- Builder: Dockerfile (`railway.toml` / `railway.json`).
- Health check: `GET /api/health`.
- Memory: **≥2 GB** (4 GB recommended) — OpenCV + ffmpeg + Next under load.
- Volume (optional): attach a **Railway Volume** mounted at `/data` if you want uploads/results to survive restarts. The image creates `/data` itself; do not use a Dockerfile `VOLUME` instruction (Railway rejects it). Ephemeral disk is fine for a prototype.
- Do **not** commit ONNX weights; the image fetches them at build time.

### Cost gate

Seedance 2.5 at 720p is about **$0.3264/s** (~**$1.63** per 5 s). The UI shows the estimate and requires an explicit confirm click. The server rejects swap windows over **5 s** and resolutions other than **720p**. Long jobs are async: `POST /api/jobs/suggest|swap` then poll `GET /api/jobs/:id`.

## Web UI

Cult UI / Next.js page: upload → suggest moments → pick one → product image → confirm cost → crop-and-stitch Seedance swap → side-by-side play + download.

```bash
# local (Python package on PYTHONPATH / editable install + ffmpeg)
cd web && npm install && npm run dev
```

Open http://localhost:3000. API routes spawn `python3 -m product_placement.jobs …` (uses `PYTHON_BIN` if set; falls back to repo `.venv` only in non-production).

## Suggest (pre-swap)

`suggest(video_path, n=5) -> list[Moment]` ranks 3–5 spread-out windows that are stable, long enough to bill/edit, and have a face-free placement region ready for crop-and-stitch.

```bash
python -m product_placement suggest \
  --video path/to/film.mp4 \
  -n 5 \
  --out-dir runs/suggest
```

ONNX weights download on first run into `checkpoints/`, `MODEL_CACHE_DIR`, or `~/.cache/product-placement/models/` (OpenCV Zoo YuNet + YOLOX-S).

## Swap (crop-and-stitch)

Hosted path used by the UI:

1. Cut the moment window (≤5 s)
2. Crop the face-free bbox → upscale to **1280×720**
3. Hold the last frame to **≈4.96 s** (avoids Higgsfield `ceil` billing a 6th second)
4. Seedance 2.5 video-edit at 720p (or mock overlay when `MOCK_HIGGSFIELD=1`)
5. Feather-stitch the edit back into the moment; keep original audio when present

CLI full-clip swap (unchanged backends):

```bash
python -m product_placement swap \
  --model seedance|wan27|wan-vace|wan-vace-modal \
  --clip assets/samples/hand_held_cup.mp4 \
  --target "metal mug" \
  --product assets/samples/soda_can.png \
  --out out.mp4
```

## Models

| `--model` | Provider | Credentials |
|-----------|----------|-------------|
| `seedance` (default) | Higgsfield Seedance 2.5 (+ Genjutsu fallback via `--backend`) | `HF_KEY` or `HF_API_KEY_ID` + `HF_API_KEY_SECRET` |
| `wan27` | Replicate `wan-video/wan-2.7-videoedit` | `REPLICATE_API_TOKEN` |
| `wan-vace` | fal `fal-ai/wan-vace-apps/video-edit` | `FAL_KEY` |
| `wan-vace-modal` | Self-hosted Wan 2.1 VACE 14B on Modal | `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` |

### seedance

Higgsfield `POST .../bytedance/seedance-2.5/video-edit`. `--backend auto|seedance|genjutsu` (default `auto`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

`ffmpeg` is required. Suggest needs OpenCV + NumPy (in `requirements.txt`).

## Sample assets

- `assets/samples/hand_held_cup.mp4` — Pexels [#6204700](https://www.pexels.com/video/person-holding-a-cup-of-hot-coffee-6204700/) (Pexels License)
- `assets/samples/soda_can.png` — self-made PNG with alpha

For a freely licensed full-film smoke test, download Blender’s *Tears of Steel* (CC-BY) at runtime — do not commit it.

## Tests

```bash
pytest -q
```

## Package

```
src/product_placement/
  suggest.py       # suggest(video) -> moments
  vision.py
  models_store.py  # ONNX download/cache
  crop_stitch.py   # crop → hold ≈5s → feather stitch (+ mock edit)
  jobs.py          # async suggest/swap workers (status.json on disk)
  backends.py
  audio.py
  higgsfield.py
  prepare.py
  pipeline.py
  cli.py
  eval.py
Dockerfile
railway.toml
web/               # Next.js UI + job APIs
```
