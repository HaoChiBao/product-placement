# Brand pick-a-video flow

How a business goes from onboarding to a placed product in a catalog video.

## Steps

1. **`/` onboarding** — Business name, website, product, optional product PNG. Form only; the PNG sits in memory (`pending-asset`) until research. Defaults come from the mock research file. Nothing hits the DB yet.

2. **`/research`** — Progress UI and brand profile are **mocked** (TinyFish is not wired). At the end it **really** calls `POST /api/catalog/brands`, which writes the brand + product asset to the catalog DB and object store (`brands/<id>/…`). The browser keeps `{ brandId, profile, hasAsset }` in `localStorage` (`inset.session`).

3. **`/videos`** — **Real** catalog: titles/episodes/moments from Postgres (or local SQLite) + object store. “Use this video” goes to `/review/<titleId>/<episodeId>`.

4. **`/review/…`** — Loads real moments and placements. Approve/reject calls `POST /api/catalog/placements`, which upserts a placement and (when generation can run) spawns the existing `placement-swap` job → `frame_swap` (Qwen edit + Seedance i2v, or intentional mock when `MOCK_HIGGSFIELD` / explicit test mode). Ready clips land at `catalog/<slug>/sXXeYY/brands/<brand>/<placement>/clip.mp4`.

5. **`/campaigns`** — Reads **campaign rows** from the DB (`draft` / `in_progress` / `live`), derived from draft saves and placement status. Live placements also appear on `/watch` via the watch payload overlay.

## Real vs stubbed

| Step | Real | Stubbed / held |
|------|------|----------------|
| Onboard | Form UX | Research defaults; no API |
| Research | Brand + asset persist | TinyFish steps/profile |
| Videos | Catalog list + moments | — |
| Review approve | Placement row + swap job when keys allow | No fabricated clip if Higgsfield keys are missing |
| Campaigns | DB campaigns + placement status | — |
| Watch | Ready placement overlays | Only after a real (or intentional mock) render |

## Gaps that remain

- TinyFish research is still mocked; profile text is local mock data even though the brand row is saved.
- Paid generation needs `HF_KEY` or `HF_API_KEY_ID` + `HF_API_KEY_SECRET`. Without those (and without `MOCK_HIGGSFIELD`), approve leaves the placement **queued** with a clear “key missing” error — no fake clip.
- Session pointer stays in the browser (`inset.session`); the source of truth for brand/product/campaigns/placements is the DB + bucket.
