"""Apply brand rules to suggest candidates: YOLOX boosts, aspect fit, CLIP soft score."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from product_placement.brand import Brand, BrandRules, mapped_coco_labels, product_aspect_ratio
from product_placement.vision import BBox, Detection, REPLACEABLE, SURFACES, region_avoids_faces


@dataclass
class BrandFit:
    prefer_scores: dict[str, float] = field(default_factory=dict)
    avoid_scores: dict[str, float] = field(default_factory=dict)
    class_boost: float = 0.0
    aspect_fit: float = 0.0
    clip_prefer: float = 0.0
    clip_avoid: float = 0.0
    vetoed: bool = False
    veto_reason: str | None = None
    reason: str = ""
    total: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefer_scores": self.prefer_scores,
            "avoid_scores": self.avoid_scores,
            "class_boost": round(self.class_boost, 4),
            "aspect_fit": round(self.aspect_fit, 4),
            "clip_prefer": round(self.clip_prefer, 4),
            "clip_avoid": round(self.clip_avoid, 4),
            "vetoed": self.vetoed,
            "veto_reason": self.veto_reason,
            "reason": self.reason,
            "total": round(self.total, 4),
        }


def aspect_fit_score(region: BBox, product_ar: float | None) -> float:
    if not product_ar or product_ar <= 0 or region.h <= 0:
        return 0.5
    box_ar = region.w / max(1, region.h)
    # 1 when equal; decays as log-ratio grows
    return float(np.exp(-abs(np.log(box_ar / product_ar))))


def reshape_region_to_product(
    region: BBox,
    frame_w: int,
    frame_h: int,
    faces: list[BBox],
    product_ar: float | None,
    *,
    min_area_frac: float = 0.02,
) -> BBox:
    """Resize a face-free box toward the product aspect ratio."""
    if not product_ar or product_ar <= 0:
        return region
    area = max(region.area, int(min_area_frac * frame_w * frame_h))
    # w*h = area, w/h = ar → h = sqrt(area/ar), w = ar*h
    h = int(round(np.sqrt(area / product_ar)))
    w = int(round(product_ar * h))
    w = max(16, min(w, frame_w))
    h = max(16, min(h, frame_h))
    cx = region.x + region.w // 2
    cy = region.y + region.h // 2
    box = BBox(cx - w // 2, cy - h // 2, w, h).clamp(frame_w, frame_h)
    if region_avoids_faces(box, faces):
        return box
    return region


def _class_hits(dets: Sequence[Detection], labels: set[str]) -> list[Detection]:
    return [d for d in dets if d.label in labels]


def score_brand_window(
    *,
    frame_bgr: np.ndarray,
    region: BBox,
    whats_there: str,
    dets: Sequence[Detection],
    brand: Brand,
    product_ar: float | None = None,
    clip_scorer: Any | None = None,
) -> BrandFit:
    """Return brand_fit; vetoed windows should be dropped by the caller."""
    rules = brand.rules
    prefer_labels = mapped_coco_labels(rules.prefer)
    avoid_labels = mapped_coco_labels(rules.avoid)

    prefer_hits = _class_hits(dets, prefer_labels)
    avoid_hits = _class_hits(dets, avoid_labels)

    prefer_scores = {d.label: float(d.score) for d in prefer_hits}
    avoid_scores = {d.label: float(d.score) for d in avoid_hits}

    # Hard veto: an avoid-mapped COCO class is in frame (threshold matches YOLOX default).
    if avoid_hits:
        top = max(avoid_hits, key=lambda d: d.score)
        if top.score >= 0.32:
            reason = f"vetoed: detected {top.label} (matches avoid '{top.label}')"
            return BrandFit(
                prefer_scores=prefer_scores,
                avoid_scores=avoid_scores,
                vetoed=True,
                veto_reason=reason,
                reason=reason,
                total=-1.0,
            )

    class_boost = 0.0
    if prefer_hits:
        class_boost = min(1.0, 0.35 + 0.65 * max(d.score for d in prefer_hits))
    elif prefer_labels and whats_there:
        # Soft hit when placement label itself matches a prefer class / phrase.
        low = whats_there.lower()
        if any(lbl in low for lbl in prefer_labels):
            class_boost = 0.55
        elif any(p.lower() in low for p in rules.prefer):
            class_boost = 0.4

    # Placement style nudge from YOLOX surfaces vs props.
    if rules.placement in {"resting", "background"} and any(
        d.label in SURFACES for d in dets
    ):
        class_boost = min(1.0, class_boost + 0.15)
    if rules.placement in {"held", "foreground"} and any(
        d.label in REPLACEABLE for d in dets
    ):
        class_boost = min(1.0, class_boost + 0.1)

    aspect = aspect_fit_score(region, product_ar)

    clip_prefer = 0.0
    clip_avoid = 0.0
    if clip_scorer is not None and getattr(clip_scorer, "available", True):
        # Score the crop region (or full frame if crop tiny).
        h, w = frame_bgr.shape[:2]
        x0, y0 = max(0, region.x), max(0, region.y)
        x1, y1 = min(w, region.x + region.w), min(h, region.y + region.h)
        crop = frame_bgr[y0:y1, x0:x1]
        if crop.size < 100:
            crop = frame_bgr
        if rules.prefer:
            sims = clip_scorer.similarity(crop, rules.prefer[:4])
            clip_prefer = float(max(sims) if sims else 0.0)
            # CLIP cosine often ~0.2–0.35; normalize softly into 0–1
            clip_prefer = max(0.0, min(1.0, (clip_prefer - 0.15) / 0.25))
        if rules.avoid:
            sims = clip_scorer.similarity(crop, rules.avoid[:4])
            clip_avoid = float(max(sims) if sims else 0.0)
            clip_avoid = max(0.0, min(1.0, (clip_avoid - 0.15) / 0.25))
            if clip_avoid >= 0.85 and clip_avoid > clip_prefer + 0.2:
                phrase = rules.avoid[int(np.argmax(sims))] if sims else "avoid"
                reason = f"vetoed: scene matches avoid '{phrase}'"
                return BrandFit(
                    prefer_scores=prefer_scores,
                    avoid_scores=avoid_scores,
                    class_boost=class_boost,
                    aspect_fit=aspect,
                    clip_prefer=clip_prefer,
                    clip_avoid=clip_avoid,
                    vetoed=True,
                    veto_reason=reason,
                    reason=reason,
                    total=-1.0,
                )

    total = 0.55 * class_boost + 0.25 * aspect + 0.20 * max(0.0, clip_prefer - 0.5 * clip_avoid)

    # One-line plain reason.
    bits: list[str] = []
    if prefer_hits:
        best = max(prefer_hits, key=lambda d: d.score)
        bits.append(f"{best.label} matches prefer")
    elif rules.prefer:
        bits.append(f"prefer '{rules.prefer[0]}'")
    if avoid_labels and not avoid_hits:
        bits.append(f"no {next(iter(avoid_labels))} detected")
    if product_ar:
        bits.append(f"box fits product aspect ({aspect:.2f})")
    if clip_prefer > 0.4:
        bits.append(f"clip prefer {clip_prefer:.2f}")
    reason = "; ".join(bits) if bits else "brand rules applied"

    return BrandFit(
        prefer_scores=prefer_scores,
        avoid_scores=avoid_scores,
        class_boost=class_boost,
        aspect_fit=aspect,
        clip_prefer=clip_prefer,
        clip_avoid=clip_avoid,
        reason=reason,
        total=float(total),
    )


def preferred_prop_labels(rules: BrandRules) -> set[str]:
    labels = mapped_coco_labels(rules.prefer)
    return labels & (REPLACEABLE | SURFACES)
