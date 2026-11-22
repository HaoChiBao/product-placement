"""suggest(video) -> moments where a product could be placed (pre-swap stage)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from product_placement.models_store import ensure_yunet, ensure_yolox
from product_placement.vision import (
    BBox,
    FaceDetector,
    YoloX,
    camera_motion_px,
    choose_placement,
    detect_shots,
    region_avoids_faces,
)

WINDOW_S = 5.0
# Prefer ~5 s (edit/bill window), but keep short shots that still have a placeable prop.
MIN_WINDOW_S = 2.0
# Border-feature affine translation per sample step. Allows mild handheld / slow moves;
# whip-pans (often 200px+) still drop out.
MAX_CAM_MOTION = 100.0


@dataclass
class Moment:
    start_s: float
    end_s: float
    frame_path: Path
    region: BBox
    whats_there: str
    score: float
    reason: str
    frame_w: int = 0
    frame_h: int = 0
    brand_fit: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        d = {
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "frame": str(self.frame_path),
            "frame_w": self.frame_w,
            "frame_h": self.frame_h,
            "region": self.region.as_dict(),
            "whats_there": self.whats_there,
            "score": round(self.score, 4),
            "reason": self.reason,
        }
        if self.brand_fit is not None:
            d["brand_fit"] = self.brand_fit
        return d


@dataclass
class _Cand:
    start_s: float
    end_s: float
    frame: np.ndarray
    region: BBox
    whats_there: str
    score: float
    reason: str
    shot_i: int
    brand_fit: dict[str, Any] | None = None
    dets: list = field(default_factory=list)


def clamp_n(n: int) -> int:
    return max(3, min(5, int(n)))


def pick_spread(candidates: list[_Cand], n: int, *, min_gap_s: float) -> list[_Cand]:
    """Greedy top-score picks spaced in time; prefer different shots on the first pass."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    picked: list[_Cand] = []

    def _far(c: _Cand) -> bool:
        return all(abs(c.start_s - p.start_s) >= min_gap_s for p in picked)

    for c in ranked:
        if len(picked) >= n:
            break
        if not _far(c):
            continue
        if any(c.shot_i == p.shot_i for p in picked):
            continue
        picked.append(c)

    for c in ranked:
        if len(picked) >= n:
            break
        if c in picked or not _far(c):
            continue
        picked.append(c)

    picked.sort(key=lambda c: c.start_s)
    return picked[:n]


def _candidate_windows(
    shots,
    duration_s: float,
    window_s: float = WINDOW_S,
    min_window_s: float = MIN_WINDOW_S,
) -> list[tuple[int, float, float]]:
    """(shot_index, start_s, end_s). Prefer ~window_s; use full short shots down to min_window_s."""
    out: list[tuple[int, float, float]] = []
    for i, shot in enumerate(shots):
        if shot.duration_s + 1e-6 < min_window_s:
            continue
        if shot.duration_s + 1e-6 < window_s:
            # Short but usable shot — keep the whole thing (swap can pad/hold later).
            out.append((i, shot.start_s, shot.end_s))
            continue
        if shot.duration_s < window_s + 2.0:
            start = shot.start_s + max(0.0, (shot.duration_s - window_s) / 2.0)
            out.append((i, start, start + window_s))
            continue
        # Space windows every ~8s inside the shot, with margins.
        span = shot.duration_s - window_s
        step = min(8.0, max(window_s, span / max(1, int(span / 8.0))))
        t = shot.start_s
        while t + window_s <= shot.end_s + 1e-6:
            out.append((i, t, t + window_s))
            t += step
            if t > shot.end_s - window_s and abs((shot.end_s - window_s) - (t - step)) > 0.5:
                end_start = shot.end_s - window_s
                if end_start >= shot.start_s:
                    out.append((i, end_start, shot.end_s))
                break
    if not out and duration_s >= min_window_s:
        out.append((0, 0.0, min(window_s, duration_s)))
    return out


def _read_frame_at(video: cv2.VideoCapture, t_s: float) -> np.ndarray | None:
    fps = float(video.get(cv2.CAP_PROP_FPS) or 24.0)
    video.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t_s * fps))))
    ok, frame = video.read()
    return frame if ok else None


def _score_window(
    video: cv2.VideoCapture,
    start_s: float,
    end_s: float,
    faces: FaceDetector,
    props: YoloX,
    shot_i: int,
    *,
    brand=None,
    product_ar: float | None = None,
    clip_scorer=None,
) -> tuple[_Cand | None, str | None]:
    mid = 0.5 * (start_s + end_s)
    frame = _read_frame_at(video, mid)
    if frame is None:
        return None, None
    h, w = frame.shape[:2]
    cam = camera_motion_px(video, start_s, end_s)
    if cam > MAX_CAM_MOTION:
        return None, None

    face_boxes = faces.detect(frame)
    # Also peek near the ends so a face that swings in doesn't sneak past.
    for t in (start_s + 0.4, end_s - 0.4):
        f2 = _read_frame_at(video, t)
        if f2 is not None:
            face_boxes = face_boxes + faces.detect(f2)

    # Dedup faces roughly.
    uniq: list[BBox] = []
    for f in face_boxes:
        if all(f.iou(u) < 0.5 for u in uniq):
            uniq.append(f)
    face_boxes = uniq

    dets = props.detect(frame)
    preferred = set()
    min_area = 0.02
    if brand is not None:
        from product_placement.brand_score import preferred_prop_labels

        preferred = preferred_prop_labels(brand.rules)
        min_area = float(brand.rules.min_area or 0.02)

    placement = choose_placement(
        dets, face_boxes, w, h, preferred_labels=preferred or None
    )
    if placement is None:
        return None, None
    region, whats, det_score = placement
    if not region_avoids_faces(region, face_boxes):
        return None, None

    brand_fit_dict = None
    brand_total = 0.0
    if brand is not None:
        from product_placement.brand_score import reshape_region_to_product, score_brand_window

        # Prefer product-aspect sizing; keep the original face-free box if reshape
        # cannot grow (missing asset AR, or expanded box hits a face). Never drop a
        # valid prop solely for being under min_area — that is a soft preference.
        region = reshape_region_to_product(
            region, w, h, face_boxes, product_ar, min_area_frac=min_area
        )
        fit = score_brand_window(
            frame_bgr=frame,
            region=region,
            whats_there=whats,
            dets=dets,
            brand=brand,
            product_ar=product_ar,
            clip_scorer=clip_scorer,
        )
        if fit.vetoed:
            return None, fit.veto_reason or fit.reason
        brand_fit_dict = fit.to_dict()
        brand_total = fit.total

    stability = max(0.0, 1.0 - cam / MAX_CAM_MOTION)
    face_penalty = min(1.0, len(face_boxes) * 0.08)
    dur = end_s - start_s
    # Mild preference for full edit-length windows without discarding short props.
    length_factor = 1.0 if dur + 1e-6 >= WINDOW_S else 0.85 + 0.15 * (dur / WINDOW_S)
    base = (0.55 * det_score + 0.35 * stability + 0.10 * (1.0 - face_penalty)) * length_factor
    # Brand contribution re-ranks without erasing base quality.
    score = base if brand is None else (0.55 * base + 0.45 * brand_total)
    if dur + 1e-6 >= WINDOW_S:
        length_note = f"Stable {dur:.0f}s window"
    else:
        length_note = f"Short {dur:.1f}s shot (below {WINDOW_S:.0f}s edit window)"
    reason = (
        f"{length_note}; {whats} "
        f"(det {det_score:.2f}, cam {cam:.1f}px, faces {len(face_boxes)})"
    )
    if brand_fit_dict and brand_fit_dict.get("reason"):
        reason = f"{reason} · {brand_fit_dict['reason']}"
    return (
        _Cand(
            start_s=start_s,
            end_s=end_s,
            frame=frame,
            region=region,
            whats_there=whats,
            score=score,
            reason=reason,
            shot_i=shot_i,
            brand_fit=brand_fit_dict,
            dets=list(dets),
        ),
        None,
    )


def write_contact_sheet(
    moments: list[Moment],
    frames: list[np.ndarray],
    out_path: Path,
    *,
    thumb_w: int = 480,
) -> Path:
    if not moments:
        raise ValueError("no moments to sheet")
    thumbs = []
    for m, frame in zip(moments, frames):
        img = frame.copy()
        r = m.region
        cv2.rectangle(img, (r.x, r.y), (r.x + r.w, r.y + r.h), (0, 220, 80), 3)
        label = f"{m.start_s:.1f}-{m.end_s:.1f}s | {m.whats_there} | {m.score:.2f}"
        cv2.putText(
            img,
            label,
            (12, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (20, 20, 20),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            img,
            label,
            (12, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (240, 240, 240),
            2,
            cv2.LINE_AA,
        )
        scale = thumb_w / img.shape[1]
        th = int(img.shape[0] * scale)
        thumbs.append(cv2.resize(img, (thumb_w, th)))

    # Pad to same height, stack in a row (or wrap at 3).
    max_h = max(t.shape[0] for t in thumbs)
    cols = min(3, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    cell_h = max_h
    sheet = np.full((rows * cell_h, cols * thumb_w, 3), 30, dtype=np.uint8)
    for i, th in enumerate(thumbs):
        r, c = divmod(i, cols)
        y = r * cell_h
        pad = (cell_h - th.shape[0]) // 2
        sheet[y + pad : y + pad + th.shape[0], c * thumb_w : (c + 1) * thumb_w] = th
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), sheet)
    return out_path


def suggest(
    video_path: str | Path,
    n: int = 5,
    *,
    out_dir: str | Path = "runs/suggest",
    window_s: float = WINDOW_S,
    yunet_path: str | Path | None = None,
    yolox_path: str | Path | None = None,
    brand=None,
    product_image: str | Path | None = None,
    use_clip: bool = True,
    diagnostics: dict[str, Any] | None = None,
) -> list[Moment]:
    """Find 3–5 spread-out ~5s placement moments in a full-length video.

    When ``brand`` is set, prefer/avoid rules re-rank candidates and can veto windows.
    Without a brand, behavior matches the original scorer.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n = clamp_n(n)

    video = cv2.VideoCapture(str(video_path))
    if not video.isOpened():
        raise FileNotFoundError(f"cannot open video: {video_path}")

    fps = float(video.get(cv2.CAP_PROP_FPS) or 24.0)
    n_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = n_frames / fps if fps else 0.0
    if duration + 1e-6 < MIN_WINDOW_S:
        video.release()
        raise ValueError(f"video is {duration:.2f}s; need at least {MIN_WINDOW_S}s")

    shots = detect_shots(video)
    windows = _candidate_windows(shots, duration, window_s)

    face_model = Path(yunet_path) if yunet_path else ensure_yunet()
    prop_model = Path(yolox_path) if yolox_path else ensure_yolox()
    face_det = FaceDetector(str(face_model))
    prop_det = YoloX(str(prop_model), conf_threshold=0.32)

    from product_placement.brand import product_aspect_ratio

    product_ar = product_aspect_ratio(product_image) if product_image else None
    if brand is not None and product_ar is None and getattr(brand, "assets", None):
        from product_placement.brand import primary_asset_path

        asset = primary_asset_path(brand)
        product_ar = product_aspect_ratio(asset)

    clip_scorer = None
    if brand is not None and use_clip:
        try:
            from product_placement.clip_onnx import ClipOnnx

            clip_scorer = ClipOnnx()
            if not clip_scorer.available:
                clip_scorer = None
        except Exception:
            clip_scorer = None

    cands: list[_Cand] = []
    vetoed: list[dict[str, Any]] = []
    for shot_i, start_s, end_s in windows:
        cand, veto = _score_window(
            video,
            start_s,
            end_s,
            face_det,
            prop_det,
            shot_i,
            brand=brand,
            product_ar=product_ar,
            clip_scorer=clip_scorer,
        )
        if veto:
            vetoed.append({"start_s": start_s, "end_s": end_s, "reason": veto})
            continue
        if cand is not None:
            cands.append(cand)

    # Last resort on short clips with no COCO prop/surface: weak face-free patch.
    if not cands and duration <= 20.0 and brand is None:
        for shot_i, start_s, end_s in windows:
            mid = 0.5 * (start_s + end_s)
            frame = _read_frame_at(video, mid)
            if frame is None:
                continue
            cam = camera_motion_px(video, start_s, end_s)
            if cam > MAX_CAM_MOTION:
                continue
            h, w = frame.shape[:2]
            face_boxes = face_det.detect(frame)
            placement = choose_placement(
                [], face_boxes, w, h, allow_weak_fallback=True
            )
            if placement is None:
                continue
            region, whats, _ = placement
            cands.append(
                _Cand(
                    start_s=start_s,
                    end_s=end_s,
                    frame=frame,
                    region=region,
                    whats_there=whats,
                    score=0.15 * (1.0 - min(1.0, cam / MAX_CAM_MOTION)),
                    reason=(
                        f"Weak fallback: no COCO prop/surface; {whats} "
                        f"(cam {cam:.1f}px, faces {len(face_boxes)})"
                    ),
                    shot_i=shot_i,
                )
            )

    if diagnostics is not None:
        diagnostics["vetoed"] = vetoed
        diagnostics["candidates"] = len(cands)
        diagnostics["brand_id"] = getattr(brand, "id", None)

    if not cands:
        video.release()
        raise RuntimeError(
            "no placement moments found (need stable, face-clear windows with a surface/prop)"
            + (f"; vetoed {len(vetoed)} brand windows" if vetoed else "")
        )

    # Gap is on start times; keep it small enough that adjacent short shots can both survive.
    min_gap = max(MIN_WINDOW_S, duration / max(n * 2.5, 1))
    chosen = pick_spread(cands, n, min_gap_s=min_gap)
    if len(chosen) < min(n, len(cands)):
        chosen = pick_spread(cands, min(n, len(cands)), min_gap_s=MIN_WINDOW_S * 0.5)

    moments: list[Moment] = []
    sheet_frames: list[np.ndarray] = []
    for i, c in enumerate(chosen, start=1):
        frame_path = frames_dir / f"moment_{i:02d}.jpg"
        cv2.imwrite(str(frame_path), c.frame)
        fh, fw = c.frame.shape[:2]
        moments.append(
            Moment(
                start_s=c.start_s,
                end_s=c.end_s,
                frame_path=frame_path,
                region=c.region,
                whats_there=c.whats_there,
                score=c.score,
                reason=c.reason,
                frame_w=int(fw),
                frame_h=int(fh),
                brand_fit=c.brand_fit,
            )
        )
        sheet_frames.append(c.frame)

    sheet_path = out_dir / "contact_sheet.jpg"
    write_contact_sheet(moments, sheet_frames, sheet_path)

    payload = {
        "video": str(video_path),
        "n": len(moments),
        "window_s": window_s,
        "duration_s": round(duration, 3),
        "moments": [m.to_dict() for m in moments],
        "contact_sheet": str(sheet_path),
        "brand_id": getattr(brand, "id", None),
        "vetoed": vetoed,
    }
    (out_dir / "moments.json").write_text(json.dumps(payload, indent=2) + "\n")
    video.release()
    return moments


def moments_to_jsonable(moments: list[Moment]) -> list[dict]:
    return [m.to_dict() for m in moments]
