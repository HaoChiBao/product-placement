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
| `OPENROUTER_API_KEY` | for the default live swap | Wan 3.0 object swap. When set, suggest also uses the Gemini finder; unset keeps classical CV |
| `SWAP_METHOD` | no | `wan` (default) or `higgsfield` (frame edit + Seedance fallback) |
| `HF_KEY` | for `SWAP_METHOD=higgsfield` | Higgsfield key as `ID:SECRET` |
| `HF_API_KEY_ID` + `HF_API_KEY_SECRET` | alt to `HF_KEY` | Same auth; both required if `HF_KEY` unset |
| `MOCK_HIGGSFIELD` | no | `1`/`true` → unpaid mock edit (product overlay) for UI tests. Does not call Wan |
| `DATA_DIR` | no | Job/upload/output root (default `/data`) |
| `MODEL_CACHE_DIR` | no | ONNX cache (default `/app/models` in the image) |
| `REPO_ROOT` | no | Repo root for sample paths (default `/app`) |
| `PYTHON_BIN` | no | Python executable (default `python3`; **not** the repo `.venv`) |
| `DATABASE_URL` | no | Postgres URL (else SQLite at `$DATA_DIR/catalog.sqlite`) |
| `AWS_S3_BUCKET_NAME` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | for Wan swaps | S3 presigned GET URLs for the clip and brand asset. Other features fall back to local files |
| `AWS_ENDPOINT_URL_S3` | no | Custom S3 endpoint (Railway bucket) |
| `AWS_DEFAULT_REGION` | no | S3 region (default `us-east-1`) |
| `MOMENT_MODEL` | no | OpenRouter model for moment finding (default `google/gemini-3.8-flash`) |
| `TINYFISH_API_KEY` | no | TinyFish key for `/research`: web search, page fetch and one paid agent run (~$0.016/step) per brand; without it the research page falls back to the mock |

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
| `/` | Your product: business name, website, product, optional own product image |
| `/research` | Brand research with TinyFish (`POST /api/research`, mock without `TINYFISH_API_KEY` or with `?mock=1`). The job also finds up to 3 product images (agent URL, Shopify JSON, page image links), keyed to transparent PNGs |
| `/product-image` | Confirm the product image (or upload your own, or skip), then `POST /api/catalog/brands` |
| `/videos` | Catalog episodes with their detected placement spots |
| `/review/<titleId>/<episodeId>` | Time bar with every spot (rescanned for the brand on first visit); approve queues `placement-swap` (real when Higgsfield keys are set; otherwise queued with key-missing — no fake clip) |
| `/campaigns` | Draft / in-progress / live campaigns from the DB |

Flow notes: [docs/brand-flow.md](docs/brand-flow.md).

Generating a placement needs a product image on the brand. Mock research data lives in `web/src/mock/research.example.ts`; copy it to `research.local.ts` (gitignored) for your own demo data.

```bash
# local (Python package on PYTHONPATH / editable install + ffmpeg)
cd web && npm install && npm run dev
# web unit tests (Node 22.18+)
cd web && npm test
```

Open http://localhost:3000. Seed the Blender open-movie catalog with `POST /api/catalog/seed`, or:

```bash
DATA_DIR=runs/ui python -m product_placement.catalog seed-blender
```

API routes spawn `python3 -m product_placement.jobs …` (uses `PYTHON_BIN` if set; falls back to repo `.venv` only in non-production).

## Suggest (pre-swap)

`suggest(video_path, n=5) -> list[Moment]` returns 3–5 spread-out windows with a placement box (`x, y, w, h`), a still frame, and a short reason.

When `OPENROUTER_API_KEY` is set, suggest detects shots, takes about one full-resolution keyframe per second inside each shot (at least three, each at least 0.15s from a cut), and sends those frames to Gemini on OpenRouter in batches of about eight with low reasoning effort. The model returns existing objects an editor could swap for a branded product — bottles, glasses, cans, devices, and similar — and never an empty surface. Each object has a label, a category, a suggested swap-in product, a visibility, and a box in Gemini's 0–1000 `[ymin, xmin, ymax, xmax]` format. Boxes become pixels. Detections in one shot merge when they share a category and their boxes overlap, including when one box contains the other. If the object is seen on the first or last keyframe of the shot, the moment extends to that shot edge; otherwise it runs to the midpoint between keyframes. A single sighting below visibility 0.3 is dropped. The object label is the moment's `whats_there`, which the swap prompt uses. Progress goes to stderr. `MOMENT_MODEL` overrides the model (default `google/gemini-3.8-flash`).

A brand passed to the CLI (`--brand`) or `run_suggest_job` adds that brand's product category (the first sentence of the brief) and its prefer/avoid rules to the prompt.

Catalog episodes are scanned twice. Upload runs `episode-suggest` once with no brand, so every brand sees spots right away. The first time a brand opens an episode on `/review`, the page calls `POST /api/catalog/brand-scan`, which spawns `brand-scan` (`run_brand_scan_job`): the same finder with that brand in the prompt. Its moments are stored with `moments.brand_id` and replace the shared ones for that brand once `brand_scans.status` is `ready`; shared moments the brand already approved or rejected stay listed. Each episode and brand pair is scanned once (about one finder run of OpenRouter usage); a failed scan can be retried from the review page, and a run stuck in `running` for 15 minutes is restarted on the next visit. Without `OPENROUTER_API_KEY` the brand scan uses the classical finder with the brand's prefer/avoid rules.

Without the key, the classical path runs: shot cuts, YuNet faces, YOLOX props/surfaces, and optical-flow camera-motion rejection. ONNX weights download on first run into `checkpoints/`, `MODEL_CACHE_DIR`, or `~/.cache/product-placement/models/`.

```bash
python -m product_placement suggest \
  --video path/to/film.mp4 \
  -n 5 \
  --out-dir runs/suggest
```

### Testing the finder on your own clips

Put videos in `clips/` (gitignored) and set `OPENROUTER_API_KEY` in the repo-root `.env`. Both scripts read that file and write under `runs/` (gitignored).

Watch the finder work on one clip, live in the browser: shot cuts, each batch of keyframes sent to the model with the boxes it returns (`label → swap with · visibility`), then the moments it keeps. `--brand`/`--brief` add a brand to the prompt the same way `run_suggest_job` does.

```bash
python scripts/watch_detection.py --clip "clips/my clip.mp4"
python scripts/watch_detection.py --clip "clips/my clip.mp4" --model anthropic/claude-sonnet-5.5 \
  --brand "Acme Cola" --brief "Soda can. Prefer tables, counters, bars, cups and bottles. Avoid beds."
```

Compare models on every clip in `clips/`: time, API calls, failed calls, tokens, and the moments each model keeps, plus `runs/model-tests/index.html` with each model's contact sheet side by side.

```bash
python scripts/compare_moment_models.py
python scripts/compare_moment_models.py --models google/gemini-3.8-flash openai/gpt-6.1-terra -n 5
```

To run the whole app on those clips: start the web app with the root `.env` loaded (Next only reads `web/.env*` on its own), upload a clip as a title and episode (`POST /api/catalog/titles`, then `POST /api/catalog/titles/<id>/episodes` with the `video` file), onboard a brand at `/`, and open the clip from `/videos`. With `MOCK_HIGGSFIELD=1`, approving a spot runs the free local mock swap.

```bash
cd web && node --env-file=../.env node_modules/next/dist/bin/next dev
```

## Swap

The hosted UI and catalog placements default to a full-clip edit on OpenRouter **Alibaba Wan 3.0** (`POST /api/v1/videos`, model `alibaba/wan-3.0`):

1. Clamp the moment window to the model max duration from `GET /api/v1/videos/models` (centered on the moment).
2. Upload that clip and the brand asset to S3 and pass presigned https GET URLs (`input_references`).
3. Poll `GET /api/v1/videos/{id}` in the background job, download `unsigned_urls`, mux the original audio, store the clip like any other placement.
4. Record `usage.cost`.

`SWAP_METHOD=higgsfield` keeps the previous frame path (Qwen Image 3 Edit, then Seedance 2.5 image-to-video). `MOCK_HIGGSFIELD=1` / `testMode=true` still runs that mock (real frame extract, local paste, no paid call) and does not call Wan. `confirm=true` is required for both mock and paid paths. Wan needs S3; data URLs are rejected by OpenRouter.

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

Copy [`.env.example`](.env.example) to `.env` and fill in what you need; Next reads these from the process environment (Railway and Docker inject them).

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
  suggest.py       # suggest(video) -> moments (Gemini or classical CV)
  jev_moments.py   # per-shot Gemini object boxes via OpenRouter
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
