# Design

## Goal

`swap(clip, target, product_image) -> new_clip`: replace a named object in a short video with a product image, keeping motion, hands, and scene otherwise intact.

Longer term: per-viewer targeted placements inside shows (different brands for Viewer A vs Viewer B on the same shot).

## Pipeline (6 steps)

1. **Find target** — Grounding DINO from a text prompt, or a click.
2. **Track** — SAM 2 mask through the clip.
3. **Crop** — padded, stabilized region around the tracked object.
4. **Generate** — replace inside the crop using crop + mask + product as reference.
5. **Composite** — feather the edit back onto the source; match color where possible.
6. **Evaluate** — side-by-side review plus a flicker / outside-mask drift metric.

## Milestones

**M1 — tracking + static composite**  
Detect → track → paste PNG. Shipped as the overlay path on PR #1; generative rebuild is PR #2.

**M2 — real generation (in progress)**  
- Self-hosted Wan 2.1 VACE 14B on Modal: masked inpainting swaps, but looks unnatural at 480p; raw unmasked runs don't actually swap the object.
- Wan 2.7 via Alibaba DashScope `wan2.7-videoedit`: pending account access.
- Seedance 2.5 via Higgsfield: testing a face-free crop-and-stitch approach.

**M3 — evaluation + viewer**  
Offline scores, then a Next.js viewer with a Viewer A / Viewer B brand toggle.

## How to run

See the root [README](../README.md).

## Status / open problems

Generation quality is the bottleneck. Lighting and shadow match still fail often; relighting (e.g. Light-A-Video) is a candidate for the composite step. VACE needs a generous region mask or it restyles the shot instead of swapping.
