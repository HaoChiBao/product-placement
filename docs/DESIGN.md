# Design

## Goal

`suggest(video) -> moments`, then `swap(clip, target, product_image) -> new_clip`: find face-free placement windows in a full cut, then replace a named object/region with a product image while keeping motion and scene intact.

Longer term: per-viewer targeted placements inside shows (different brands for Viewer A vs Viewer B on the same shot).

## Pipeline

0. **Suggest** — shot cuts → stable ~5 s windows → YuNet faces + YOLOX props/surfaces → ranked, spread-out moments with a face-free bbox (feeds crop-and-stitch).
1. **Find target** — text / click / suggested region.
2. **Track** — SAM 2 (overlay path) or skip when the generative backend edits the whole crop.
3. **Crop** — padded face-free region around the placement.
4. **Generate** — replace inside the crop (Seedance / Wan / VACE).
5. **Composite** — feather the edit back onto the source; match color where possible.
6. **Evaluate** — side-by-side review plus a flicker / outside-mask drift metric.

## Milestones

**M1 — tracking + static composite**  
Detect → track → paste PNG. Shipped as the overlay path on PR #1; generative rebuild is PR #2.

**M2 — real generation (in progress)**  
- Self-hosted Wan 2.1 VACE 14B on Modal: masked inpainting swaps, but looks unnatural at 480p; raw unmasked runs don't actually swap the object.
- Wan 2.7 via Alibaba DashScope `wan2.7-videoedit`: pending account access.
- Seedance 2.5 via Higgsfield: face-free crop-and-stitch works better for moderation; `suggest` finds those moments.

**M3 — evaluation + viewer**  
Offline scores, then a Next.js viewer with a Viewer A / Viewer B brand toggle. A minimal Cult UI `suggest` page (`web/`) already runs local suggest and previews 5 s clips.

## How to run

See the root [README](../README.md).

## Status / open problems

Generation quality is the bottleneck. Lighting and shadow match still fail often; relighting (e.g. Light-A-Video) is a candidate for the composite step. VACE needs a generous region mask or it restyles the shot instead of swapping.
