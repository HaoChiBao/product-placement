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
| `DATABASE_URL` | no | Postgres URL (else SQLite at `$DATA_DIR/catalog.sqlite`) |
| `AWS_S3_BUCKET_NAME` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | no | S3 (or S3-compatible) object store; else local `$DATA_DIR/objects/` |
| `AWS_ENDPOINT_URL_S3` | no | Custom S3 endpoint (Railway bucket) |
| `AWS_DEFAULT_REGION` | no | S3 region (default `us-east-1`) |

Auth is resolved by `product_placement.higgsfield.auth_header`: prefer `HF_KEY`, else `HF_API_KEY_ID` + `HF_API_KEY_SECRET`.

### Railway notes

- Builder: Dockerfile (`railway.toml` / `railway.json`).
- Health check: `GET /api/health`.
- Memory: **≥2 GB** (4 GB recommended) — OpenCV + ffmpeg + Next under load.
- Volume (optional): attach a **Railway Volume** mounted at `/data` if you want uploads/results to survive restarts. The image creates `/data` itself; do not use a Dockerfile `VOLUME` instruction (Railway rejects it). Ephemeral disk is fine for a prototype.
- Do **not** commit ONNX weights; the image fetches them at build time.

### Cost gate

Frame-based placement (default): Qwen Image 3 Edit (~**$0.04**/1k image) + Seedance 2.5 image-to-video at 720p (**$0.2647/s** → ~**$1.32**/5 s) ≈ **$1.36** per 5 s clip. The UI shows the estimate and requires an explicit confirm click. The server rejects swap windows over **5 s** and resolutions other than **720p**. Long jobs are async: `POST /api/jobs/suggest|swap` then poll `GET /api/jobs/:id`.

## Web UI

Brand-side flow (the only UI; the provider and watch APIs are unchanged):

| Route | Role |
|-------|------|
| `/` | Your product: business name, website, product, optional product image |
| `/research` | Brand research (TinyFish steps are mocked for now), then `POST /api/catalog/brands` |
| `/videos` | Catalog episodes with their detected placement spots |
| `/review/<titleId>/<episodeId>` | Time bar with every spot; approve (queues a test-mode swap) or reject each one |
| `/campaigns` | Drafts (saved in the browser), in-progress and live placements |

Generating a placement needs a product image on the brand. Mock research data lives in `web/src/mock/research.example.ts`; copy it to `research.local.ts` (gitignored) for your own demo data.

```bash
# local (Python package on PYTHONPATH / editable install + ffmpeg)
cd web && npm install && npm run dev
```

Open http://localhost:3000. Seed the Blender open-movie catalog with `POST /api/catalog/seed`, or:

```bash
DATA_DIR=runs/ui python -m product_placement.catalog seed-blender
```

API routes spawn `python3 -m product_placement.jobs …` (uses `PYTHON_BIN` if set; falls back to repo `.venv` only in non-production).

## Suggest (pre-swap)

`suggest(video_path, n=5) -> list[Moment]` ranks 3–5 spread-out windows that are stable, long enough to bill/edit, and have a face-free placement region ready for crop-and-stitch.

```bash
python -m product_placement suggest \
  --video path/to/film.mp4 \
  -n 5 \
  --out-dir runs/suggest
```

ONNX weights download on first run into `checkpoints/`, `MODEL_CACHE_DIR`, or `~/.cache/product-placement/models/` (OpenCV Zoo YuNet + YOLOX-S).

## Swap (frame → edit → image-to-video)

Hosted path used by the UI and catalog placements:

1. Extract a full-res frame at the moment start (`ffmpeg`, exact timestamp)
2. Edit that frame so the brand product appears in the moment bbox — Higgsfield **Qwen Image 3 Edit** (`POST /alibaba/qwen-image-3/edit`) with the product asset as a second reference (mock: local OpenCV paste)
3. Animate with **Seedance 2.5 image-to-video** (`POST /bytedance/seedance-2.5/image-to-video`) at the moment duration (clamped 4–30 s) and source resolution tier (mock: hold the edited frame as H.264)
4. The returned clip is what the watch overlay plays

`MOCK_HIGGSFIELD=1` / `testMode=true` still runs real frame extraction; only the paid API calls are mocked. `confirm=true` is required for both mock and paid paths.

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
  catalog.py       # titles/episodes/moments/brands/placements + seed CLI
  db.py            # Postgres or SQLite
  object_store.py  # S3 or local objects/
  vision.py
  models_store.py  # ONNX download/cache
  crop_stitch.py   # crop → hold ≈5s → feather stitch (+ mock edit)
  jobs.py          # suggest/swap + episode-suggest/placement-swap
  backends.py
  …
Dockerfile
railway.toml
web/               # Next.js: brand flow UI + API routes
scripts/seed_catalog.py
```
