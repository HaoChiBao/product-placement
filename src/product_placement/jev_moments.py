"""Per-shot product finder on OpenRouter.

Samples about one full-resolution keyframe per second inside each shot, asks
Gemini for existing swappable objects, and merges those boxes into moments.
Used when OPENROUTER_API_KEY is set.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import httpx
import numpy as np

from product_placement.crop_stitch import probe_video
from product_placement.suggest import (
    MIN_WINDOW_S,
    Moment,
    _Cand,
    _read_frame_at,
    clamp_n,
    write_contact_sheet,
)
from product_placement.vision import BBox, Shot, detect_shots

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.8-flash"
KEYFRAME_EVERY_S = 1.0
KEYFRAME_MARGIN_S = 0.15
MIN_KEYFRAMES = 3
FRAMES_PER_CALL = 8
POOL = 8
MERGE_IOU = 0.3
VISIBILITY_MIN = 0.3
# A single keyframe is an instant. Swap rejects a zero-length moment.
MIN_SPAN_S = 0.1
CALL_TIMEOUT_S = 120.0
JPEG_QUALITY = 90

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)
_STOP = frozenset({"a", "an", "the", "of", "on", "in", "and", "with"})
_SURFACE = frozenset(
    {
        "table",
        "tabletop",
        "surface",
        "counter",
        "countertop",
        "desk",
        "shelf",
        "floor",
        "wall",
        "empty",
        "background",
        "seat",
        "sofa",
        "couch",
        "bench",
    }
)
_PEOPLE = frozenset({"person", "people", "face", "clothing", "clothes", "apparel"})


def moment_model() -> str:
    return (os.environ.get("MOMENT_MODEL") or "").strip() or DEFAULT_MODEL


def _err(msg: str) -> None:
    print(f"[jev] {msg}", file=sys.stderr, flush=True)


def _redact(text: str, key: str) -> str:
    flat = text.replace("\n", " ")
    if key:
        flat = flat.replace(key, "[redacted]")
    return flat[:300]


def keyframe_times(start_s: float, end_s: float) -> list[float]:
    """About one frame per second, at least three, each inset from the cuts."""
    start = float(start_s)
    end = float(end_s)
    span = end - start
    if span <= 0:
        return []
    if span <= 2 * KEYFRAME_MARGIN_S:
        return [round((start + end) / 2.0, 3)]
    n = max(MIN_KEYFRAMES, int(round(span / KEYFRAME_EVERY_S)))
    usable = span - 2 * KEYFRAME_MARGIN_S
    times = [
        round(start + KEYFRAME_MARGIN_S + usable * k / (n - 1), 3) for k in range(n)
    ]
    out: list[float] = []
    for t in times:
        if not out or t > out[-1] + 1e-9:
            out.append(t)
    return out


def chunked(items: list, size: int) -> list[list]:
    if size < 1:
        raise ValueError("size")
    return [items[i : i + size] for i in range(0, len(items), size)]


def brand_hint(brand: Any) -> str:
    if brand is None:
        return ""
    rules = brand.rules
    brief = (getattr(brand, "brief", "") or "").strip()
    category = brief.split(".")[0].strip() if brief else ""
    if not category:
        category = (getattr(brand, "name", "") or "").strip() or "unspecified"
    prefer = ", ".join(rules.prefer) or "none"
    avoid = ", ".join(rules.avoid) or "none"
    placement = (rules.placement or "resting").strip() or "resting"
    name = (getattr(brand, "name", "") or "").strip() or "brand"
    return (
        f" Brand: {name}. Product category: {category}. "
        f"Prefer: {prefer}. Avoid: {avoid}. Placement: {placement}."
    )


def finder_prompt(times: list[float], brand: Any) -> str:
    listing = ", ".join(f"Frame {i} at {t:.3f}s" for i, t in enumerate(times))
    n = len(times)
    last = max(0, n - 1)
    return (
        "You are finding EXISTING visible objects in these video frames that an editor could swap "
        "for a branded product: bottles, glasses, cups, mugs, cans, phones, laptops, and other devices, "
        "plus food or drink packages, boxes, bags, and watches. "
        "Only list objects that are actually visible in the frame. "
        "Never mark an empty surface, tabletop, counter, desk, floor, or wall, and never list people or clothing. "
        f"There are {n} frames in order ({listing}). "
        f"For each object return frame (the frame index, 0 to {last}), "
        "label (a short specific noun phrase such as 'green wine bottle'), "
        "category (one word: bottle, wine_glass, glass, cup, can, phone, laptop, plate, food, other), "
        "swap_with (the suggested swap-in product whose shape fits the object), "
        "visibility (how unoccluded and large it is, from 0 to 1), "
        "and box_2d as [ymin, xmin, ymax, xmax] with each edge from 0 to 1000 "
        "(0 is the top or left, 1000 is the bottom or right). "
        'Reply with JSON only: {"objects":[...]}. At most 10 objects per frame, most swappable first.'
        + brand_hint(brand)
    )


def parse_json_content(text: str) -> Any:
    raw = text.strip().lstrip("\ufeff")
    matched = _FENCE.match(raw)
    if matched:
        raw = matched.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON") from exc


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _label_tokens(label: str) -> frozenset[str]:
    return frozenset(tok for tok in re.findall(r"[a-z0-9]+", label.lower()) if tok not in _STOP)


def labels_similar(a: str, b: str) -> bool:
    ta = _label_tokens(a)
    tb = _label_tokens(b)
    if not ta or not tb:
        left = a.strip().lower()
        return bool(left) and left == b.strip().lower()
    if ta <= tb or tb <= ta:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.5


def _is_surface(label: str, category: str) -> bool:
    if category.strip().lower() in _PEOPLE:
        return True
    tokens = _label_tokens(label)
    return bool(tokens) and tokens <= _SURFACE


def gemini_box_to_bbox(box_2d: Any, width: int, height: int) -> BBox:
    """[ymin, xmin, ymax, xmax] in 0-1000 → pixel xywh, clamped to the frame."""
    if width < 1 or height < 1:
        raise ValueError("frame size")
    if not isinstance(box_2d, (list, tuple)) or len(box_2d) != 4:
        raise ValueError("box_2d")
    nums = [_finite(v) for v in box_2d]
    if any(n is None for n in nums):
        raise ValueError("box_2d")
    ymin, xmin, ymax, xmax = (min(1000.0, max(0.0, float(n))) for n in nums)
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("empty box")
    x = int(round(xmin / 1000.0 * width))
    y = int(round(ymin / 1000.0 * height))
    x2 = int(round(xmax / 1000.0 * width))
    y2 = int(round(ymax / 1000.0 * height))
    return BBox(x, y, max(1, x2 - x), max(1, y2 - y)).clamp(width, height)


def _frame_index(value: Any, n_frames: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        idx = value
    elif isinstance(value, float) and value.is_integer():
        idx = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        idx = int(value.strip())
    else:
        return None
    if idx < 0 or idx >= n_frames:
        return None
    return idx


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class RawObject:
    frame: int
    label: str
    category: str
    swap_with: str
    visibility: float
    box: BBox


def parse_objects(payload: Any, sizes: list[tuple[int, int]]) -> list[RawObject]:
    """Objects from one batch. A bad item is skipped; a bad payload raises."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("objects"), list):
        items = payload["objects"]
    else:
        raise ValueError("objects")
    n_frames = len(sizes)
    found: list[RawObject] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "frame" in item:
            frame = _frame_index(item.get("frame"), n_frames)
        elif n_frames == 1:
            frame = 0
        else:
            frame = None
        if frame is None:
            continue
        label = _text(item.get("label"))
        if not label:
            continue
        category = _text(item.get("category"))
        if _is_surface(label, category):
            continue
        visibility = _finite(item.get("visibility"))
        if visibility is None:
            continue
        visibility = min(1.0, max(0.0, visibility))
        width, height = sizes[frame]
        try:
            box = gemini_box_to_bbox(item.get("box_2d"), width, height)
        except ValueError:
            continue
        found.append(
            RawObject(
                frame=frame,
                label=label,
                category=category,
                swap_with=_text(item.get("swap_with")),
                visibility=visibility,
                box=box,
            )
        )
    return found


@dataclass(frozen=True)
class Hit:
    shot_i: int
    time_s: float
    label: str
    category: str
    swap_with: str
    visibility: float
    box: BBox
    key: int


@dataclass(frozen=True)
class Track:
    shot_i: int
    label: str
    category: str
    swap_with: str
    visibility: float
    box: BBox
    start_s: float
    end_s: float
    sightings: int
    frame_key: int


def clamp_track_window(
    times: list[float], shot_start: float, shot_end: float
) -> tuple[float, float]:
    """First and last sighting, clamped to the shot. A single sighting gets a short hold."""
    if not times:
        raise ValueError("times")
    shot_start = float(shot_start)
    shot_end = float(shot_end)
    if shot_end < shot_start:
        shot_start, shot_end = shot_end, shot_start
    start = min(max(min(times), shot_start), shot_end)
    end = min(max(max(times), shot_start), shot_end)
    if end < start:
        start, end = end, start
    if end - start < MIN_SPAN_S:
        end = min(shot_end, start + MIN_SPAN_S)
        if end - start < MIN_SPAN_S:
            start = max(shot_start, end - MIN_SPAN_S)
    if end <= start:
        return shot_start, shot_end
    return start, end


class _Group:
    def __init__(self, hit: Hit) -> None:
        self.shot_i = hit.shot_i
        self.hits: list[Hit] = [hit]


def merge_hits(hits: list[Hit], shots: list[tuple[float, float]]) -> list[Track]:
    """One track per object per shot. Same label family and overlapping boxes merge."""
    groups: list[_Group] = []
    ordered = sorted(hits, key=lambda h: (h.shot_i, h.time_s, -h.visibility))
    for hit in ordered:
        best: _Group | None = None
        best_iou = 0.0
        for group in groups:
            if group.shot_i != hit.shot_i:
                continue
            if not any(labels_similar(hit.label, prev.label) for prev in group.hits):
                continue
            iou = max(hit.box.iou(prev.box) for prev in group.hits)
            if iou > best_iou:
                best = group
                best_iou = iou
        if best is not None and best_iou >= MERGE_IOU:
            best.hits.append(hit)
        else:
            groups.append(_Group(hit))

    tracks: list[Track] = []
    for group in groups:
        if len(group.hits) == 1 and group.hits[0].visibility < VISIBILITY_MIN:
            continue
        if group.shot_i < 0 or group.shot_i >= len(shots):
            continue
        earliest = min(group.hits, key=lambda h: (h.time_s, -h.visibility))
        clearest = max(group.hits, key=lambda h: (h.visibility, -h.time_s))
        start_s, end_s = clamp_track_window(
            [h.time_s for h in group.hits], shots[group.shot_i][0], shots[group.shot_i][1]
        )
        tracks.append(
            Track(
                shot_i=group.shot_i,
                label=clearest.label,
                category=clearest.category,
                swap_with=clearest.swap_with or earliest.swap_with,
                visibility=sum(h.visibility for h in group.hits) / len(group.hits),
                box=earliest.box,
                start_s=start_s,
                end_s=end_s,
                sightings=len(group.hits),
                frame_key=earliest.key,
            )
        )
    return tracks


def _coerce_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                bits.append(part["text"])
        return "".join(bits)
    raise ValueError("unexpected message content")


def _content_text(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError("openrouter response")
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("missing message content") from exc
    text = _coerce_text(content)
    if not text.strip():
        raise ValueError("empty content")
    return text


def _post_content(
    client: httpx.Client,
    *,
    key: str,
    images: list[bytes],
    prompt: str,
    model: str,
) -> str:
    if not images:
        raise ValueError("no frames")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
                },
            }
        )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
        "reasoning": {"effort": "low"},
    }
    try:
        response = client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=CALL_TIMEOUT_S,
        )
    except Exception as exc:
        raise ValueError(f"openrouter request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        snippet = _redact(response.text[:400], key)
        raise ValueError(f"openrouter HTTP {response.status_code}: {snippet}")
    try:
        data = response.json()
    except Exception as exc:
        raise ValueError("openrouter response was not JSON") from exc
    return _content_text(data)


def call_validated(
    client: httpx.Client,
    *,
    key: str,
    images: list[bytes],
    prompt: str,
    schema_name: str,
    validate: Callable[[Any], Any],
    model: str | None = None,
) -> Any:
    """POST one batch of frames. Parse and validate. Retry once, then return None."""
    chosen = model or moment_model()
    for attempt in (1, 2):
        try:
            text = _post_content(
                client,
                key=key,
                images=images,
                prompt=prompt,
                model=chosen,
            )
            return validate(parse_json_content(text))
        except Exception as exc:
            _err(f"{schema_name} attempt {attempt} failed: {_redact(str(exc), key)}")
    _err(f"{schema_name} skipped")
    return None


def _parallel(fn: Callable, items: list) -> list:
    if len(items) <= 1:
        return [fn(item) for item in items]
    workers = min(POOL, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def _jpeg(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError("could not encode jpeg")
    return buf.tobytes()


@dataclass(frozen=True)
class Keyframe:
    shot_i: int
    time_s: float
    path: Path
    width: int
    height: int


def _collect_keyframes(cap: cv2.VideoCapture, shots: list[Shot], dest: Path) -> list[Keyframe]:
    found: list[Keyframe] = []
    for shot_i, shot in enumerate(shots):
        for t in keyframe_times(shot.start_s, shot.end_s):
            frame = _read_frame_at(cap, t, lo_s=shot.start_s, hi_s=shot.end_s)
            if frame is None or frame.size == 0:
                continue
            height, width = frame.shape[:2]
            if width < 2 or height < 2:
                continue
            path = dest / f"kf_{len(found):04d}.jpg"
            path.write_bytes(_jpeg(frame))
            found.append(Keyframe(shot_i, float(t), path, int(width), int(height)))
    return found


def _reason(label: str, category: str, swap_with: str) -> str:
    parts = [label]
    if category:
        parts.append(category)
    if swap_with:
        parts.append(f"swap with {swap_with}")
    return "; ".join(parts)


def _pick(cands: list[_Cand], n: int) -> list[_Cand]:
    ranked = sorted(cands, key=lambda c: c.score, reverse=True)
    picked: list[_Cand] = []
    for cand in ranked:
        if len(picked) >= n:
            break
        if any(cand.shot_i == prev.shot_i for prev in picked):
            continue
        picked.append(cand)
    for cand in ranked:
        if len(picked) >= n:
            break
        if any(cand is prev for prev in picked):
            continue
        picked.append(cand)
    picked.sort(key=lambda c: (c.start_s, -c.score))
    return picked


def _none_found() -> RuntimeError:
    return RuntimeError(
        "no placement moments found (no swappable object stayed visible inside a shot)"
    )


def suggest_with_jev(
    video_path: str | Path,
    n: int = 5,
    *,
    out_dir: str | Path = "runs/suggest",
    window_s: float = 5.0,
    brand: Any = None,
    diagnostics: dict[str, Any] | None = None,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> list[Moment]:
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n = clamp_n(n)
    key = (api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    width, height, duration, _fps = probe_video(video_path)
    if duration + 1e-6 < MIN_WINDOW_S:
        raise ValueError(f"video is {duration:.2f}s; need at least {MIN_WINDOW_S}s")
    if width < 2 or height < 2:
        raise RuntimeError(f"cannot read frame size for {video_path}")

    def _diag(candidates: int) -> None:
        if diagnostics is None:
            return
        diagnostics["vetoed"] = []
        diagnostics["candidates"] = candidates
        diagnostics["brand_id"] = getattr(brand, "id", None)
        diagnostics["finder"] = "gemini"

    _err(f"model {moment_model()} duration {duration:.1f}s")
    owns_client = client is None
    client = client or httpx.Client(timeout=CALL_TIMEOUT_S)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise FileNotFoundError(f"cannot open video: {video_path}")
        shots = detect_shots(cap)
        if not shots:
            shots = [Shot(0.0, duration)]
        shot_bounds = [(shot.start_s, shot.end_s) for shot in shots]

        with tempfile.TemporaryDirectory(prefix="product-frames-") as tmp:
            keyframes = _collect_keyframes(cap, shots, Path(tmp))
            if not keyframes:
                _diag(0)
                raise RuntimeError("no keyframes could be read")
            _err(f"{len(shots)} shots, {len(keyframes)} keyframes")
            batches = chunked(keyframes, FRAMES_PER_CALL)

            def _ask(batch: list[Keyframe]) -> list[Hit] | None:
                sizes = [(kf.width, kf.height) for kf in batch]
                parsed = call_validated(
                    client,
                    key=key,
                    images=[kf.path.read_bytes() for kf in batch],
                    prompt=finder_prompt([kf.time_s for kf in batch], brand),
                    schema_name="products",
                    validate=lambda payload, sizes=sizes: parse_objects(payload, sizes),
                )
                if parsed is None:
                    return None
                hits: list[Hit] = []
                for obj in parsed:
                    kf = batch[obj.frame]
                    hits.append(
                        Hit(
                            shot_i=kf.shot_i,
                            time_s=kf.time_s,
                            label=obj.label,
                            category=obj.category,
                            swap_with=obj.swap_with,
                            visibility=obj.visibility,
                            box=obj.box,
                            key=keyframes.index(kf),
                        )
                    )
                return hits

            answered = _parallel(_ask, batches)
            failed = sum(1 for result in answered if result is None)
            hits: list[Hit] = []
            for result in answered:
                if result:
                    hits.extend(result)
            _err(f"{len(batches)} batches, {failed} failed, {len(hits)} objects")
            if not hits and batches and failed == len(batches):
                _diag(0)
                raise RuntimeError(
                    "product identification failed; no frame batch returned a valid answer"
                )

            tracks = merge_hits(hits, shot_bounds)
            _diag(len(tracks))
            if not tracks:
                raise _none_found()

            cands: list[_Cand] = []
            for track in tracks:
                kf = keyframes[track.frame_key]
                frame = cv2.imread(str(kf.path))
                if frame is None:
                    continue
                cands.append(
                    _Cand(
                        start_s=track.start_s,
                        end_s=track.end_s,
                        frame=frame,
                        region=track.box,
                        whats_there=track.label,
                        score=track.visibility,
                        reason=_reason(track.label, track.category, track.swap_with),
                        shot_i=track.shot_i,
                    )
                )
            if not cands:
                raise _none_found()

            chosen = _pick(cands, n)
            moments: list[Moment] = []
            sheet_frames: list[np.ndarray] = []
            for i, cand in enumerate(chosen, start=1):
                frame_path = frames_dir / f"moment_{i:02d}.jpg"
                cv2.imwrite(str(frame_path), cand.frame)
                fh, fw = cand.frame.shape[:2]
                moments.append(
                    Moment(
                        start_s=cand.start_s,
                        end_s=cand.end_s,
                        frame_path=frame_path,
                        region=cand.region,
                        whats_there=cand.whats_there,
                        score=cand.score,
                        reason=cand.reason,
                        frame_w=int(fw),
                        frame_h=int(fh),
                    )
                )
                sheet_frames.append(cand.frame)

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
            "vetoed": [],
        }
        (out_dir / "moments.json").write_text(json.dumps(payload, indent=2) + "\n")
        _err(f"kept {len(moments)}")
        return moments
    finally:
        cap.release()
        if owns_client:
            client.close()
