"""Placement moments from TypeSafe Jev on OpenRouter.

Samples the video at 1 fps, screens labelled contact sheets, then asks for a
presence-gated box on the full frame. Used when OPENROUTER_API_KEY is set.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import subprocess
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
    MAX_CAM_MOTION,
    MIN_WINDOW_S,
    WINDOW_S,
    Moment,
    _Cand,
    _read_frame_at,
    clamp_n,
    pick_spread,
    write_contact_sheet,
)
from product_placement.vision import BBox, camera_motion_px, detect_shots

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "typesafe/jev-router"
MAX_SIDE = 1568
PER_SHEET = 60
SAMPLE_FPS = 1.0
POOL = 8
VERIFY_TOP = 12
PRESENCE_MIN = 0.5
CALL_TIMEOUT_S = 45.0

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)


def moment_model() -> str:
    return (os.environ.get("MOMENT_MODEL") or "").strip() or DEFAULT_MODEL


def _err(msg: str) -> None:
    print(f"[jev] {msg}", file=sys.stderr, flush=True)


def _redact(text: str, key: str) -> str:
    flat = text.replace("\n", " ")
    if key:
        flat = flat.replace(key, "[redacted]")
    return flat[:300]


@dataclass(frozen=True)
class Layout:
    cols: int
    rows: int
    tile_w: int
    tile_h: int

    @property
    def capacity(self) -> int:
        return self.cols * self.rows


def choose_layout(aspect: float, tiles: int, max_side: int = MAX_SIDE) -> Layout:
    """Largest tiles that fit `tiles` frames of this aspect inside max_side."""
    if aspect <= 0 or tiles < 1 or max_side < 2:
        raise ValueError("no contact-sheet layout")
    best: Layout | None = None
    best_key: tuple[int, int] | None = None
    for cols in range(1, tiles + 1):
        rows = math.ceil(tiles / cols)
        tile_w = math.floor(min(max_side / cols, max_side * aspect / rows))
        tile_h = math.floor(tile_w / aspect) if aspect else 0
        if tile_w < 1 or tile_h < 1:
            continue
        candidate = Layout(cols, rows, tile_w, tile_h)
        key = (candidate.tile_w * candidate.tile_h, -(candidate.capacity - tiles))
        if best is None or best_key is None or key > best_key:
            best = candidate
            best_key = key
    if best is None:
        raise ValueError(f"no contact-sheet layout for {tiles} tiles")
    return best


def _layout_for(aspect: float, n_frames: int) -> Layout:
    return choose_layout(aspect, min(PER_SHEET, max(1, n_frames)), MAX_SIDE)


def tile_label(slot: int, per_sheet: int) -> str:
    width = max(2, len(str(max(per_sheet, 1))))
    return f"T{slot + 1:0{width}d}"


@dataclass(frozen=True)
class SheetPlan:
    index: int
    tiles: tuple[tuple[str, float], ...]

    def time_of(self, tile_id: str) -> float | None:
        for tid, t in self.tiles:
            if tid == tile_id:
                return t
        return None


def plan_sheets(
    n_frames: int, *, fps: float = SAMPLE_FPS, per_sheet: int = PER_SHEET
) -> list[SheetPlan]:
    """Map sampled frame i to time i/fps, chunked into sheets labelled T01…"""
    if n_frames <= 0 or per_sheet <= 0 or fps <= 0:
        return []
    plans: list[SheetPlan] = []
    for index, start in enumerate(range(0, n_frames, per_sheet)):
        count = min(per_sheet, n_frames - start)
        tiles = tuple(
            (tile_label(slot, per_sheet), round((start + slot) / fps, 3))
            for slot in range(count)
        )
        plans.append(SheetPlan(index=index, tiles=tiles))
    return plans


def moment_window(
    t: float,
    duration: float,
    window_s: float,
    *,
    lo: float | None = None,
    hi: float | None = None,
) -> tuple[float, float]:
    start_bound = 0.0 if lo is None else float(lo)
    end_bound = float(duration) if hi is None else float(hi)
    span = end_bound - start_bound
    if span <= window_s:
        return start_bound, max(end_bound, start_bound)
    start = min(max(start_bound, t - window_s / 2.0), end_bound - window_s)
    return start, start + window_s


def _draw_label(tile: np.ndarray, label: str) -> None:
    h, w = tile.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.35, min(0.9, h / 160))
    thickness = 1
    (tw, th), base = cv2.getTextSize(label, font, scale, thickness)
    while scale > 0.3 and tw + 8 > w:
        scale = round(scale - 0.05, 2)
        (tw, th), base = cv2.getTextSize(label, font, scale, thickness)
    cv2.rectangle(tile, (0, 0), (min(w, tw + 8), min(h, th + base + 6)), (0, 0, 0), -1)
    cv2.putText(tile, label, (4, th + 2), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)


def _fit_tile(frame: np.ndarray, tile_w: int, tile_h: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w == tile_w and h == tile_h:
        return frame.copy()
    scale = min(tile_w / max(w, 1), tile_h / max(h, 1))
    nw = max(1, min(tile_w, int(round(w * scale))))
    nh = max(1, min(tile_h, int(round(h * scale))))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((tile_h, tile_w, 3), np.uint8)
    x = (tile_w - nw) // 2
    y = (tile_h - nh) // 2
    canvas[y : y + nh, x : x + nw] = resized
    return canvas


def compose_sheet(frames: list[np.ndarray], ids: list[str], layout: Layout) -> np.ndarray:
    if len(frames) != len(ids):
        raise ValueError("one id per frame")
    if len(frames) > layout.capacity:
        raise ValueError("too many frames for the layout")
    canvas = np.zeros((layout.rows * layout.tile_h, layout.cols * layout.tile_w, 3), np.uint8)
    for slot, (frame, tile_id) in enumerate(zip(frames, ids)):
        col = slot % layout.cols
        row = slot // layout.cols
        x = col * layout.tile_w
        y = row * layout.tile_h
        tile = _fit_tile(frame, layout.tile_w, layout.tile_h)
        _draw_label(tile, tile_id)
        canvas[y : y + layout.tile_h, x : x + layout.tile_w] = tile
    return canvas


def _jpeg(image: np.ndarray, *, max_side: int | None = None) -> bytes:
    if max_side is not None:
        h, w = image.shape[:2]
        longest = max(h, w)
        if longest > max_side and longest > 0:
            scale = max_side / longest
            image = cv2.resize(
                image,
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("could not encode jpeg")
    return buf.tobytes()


def sample_tiles(video: Path, dest: Path, layout: Layout) -> list[Path]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg was not found on PATH")
    dest.mkdir(parents=True, exist_ok=True)
    vf = (
        f"fps={SAMPLE_FPS}:round=up,"
        f"scale={layout.tile_w}:{layout.tile_h}:force_original_aspect_ratio=decrease"
    )
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-vf",
            vf,
            "-q:v",
            "4",
            str(dest / "f%06d.jpg"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()[:500]
        raise RuntimeError(f"ffmpeg could not sample frames: {detail}")
    return sorted(dest.glob("f*.jpg"))


def _sheet_jpegs(paths: list[Path], plans: list[SheetPlan], layout: Layout) -> list[bytes]:
    jpegs: list[bytes] = []
    cursor = 0
    for plan in plans:
        chunk = paths[cursor : cursor + len(plan.tiles)]
        if len(chunk) != len(plan.tiles):
            raise RuntimeError("sheet frame count does not match the tile map")
        if len(chunk) > layout.capacity:
            raise RuntimeError("sheet has more frames than the layout")
        frames: list[np.ndarray] = []
        for path in chunk:
            img = cv2.imread(str(path))
            if img is None:
                img = np.zeros((layout.tile_h, layout.tile_w, 3), np.uint8)
            frames.append(img)
        cursor += len(plan.tiles)
        ids = [tid for tid, _ in plan.tiles]
        jpegs.append(_jpeg(compose_sheet(frames, ids, layout)))
    return jpegs


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def sheet_schema(tile_ids: list[str]) -> dict[str, Any]:
    hit = _object_schema(
        {
            "tile": {"type": "string", "enum": list(tile_ids)},
            "score": {"type": "number"},
            "reason": {"type": "string"},
        },
        ["tile", "score", "reason"],
    )
    return _object_schema({"moments": {"type": "array", "items": hit}}, ["moments"])


BOX_SCHEMA = _object_schema(
    {
        "target": {"type": "string"},
        "present": {"type": "boolean"},
        "presence": {"type": "number"},
        "left": {"type": "number"},
        "top": {"type": "number"},
        "right": {"type": "number"},
        "bottom": {"type": "number"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    [
        "target",
        "present",
        "presence",
        "left",
        "top",
        "right",
        "bottom",
        "confidence",
        "reason",
    ],
)


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


def screen_prompt(plan: SheetPlan, brand: Any) -> str:
    ids = ", ".join(tid for tid, _ in plan.tiles)
    return (
        "The image is a contact sheet of video frames, one per second, left to right, top to bottom. "
        "Each tile has its id in the top-left corner. "
        f"Valid tile ids: {ids}. "
        "List the tiles that are good product-placement moments: a steady shot, a clear prop or flat "
        "surface a product can sit on, and no face blocking that spot. "
        "score is from 0 to 1. reason is one short sentence. Omit tiles that do not qualify."
        + brand_hint(brand)
    )


def box_prompt(brand: Any) -> str:
    return (
        "This is one full video frame. Name the placement target: a clear prop or flat surface "
        "where a product can sit, not blocked by a face. "
        "present is true only if that target is visible. "
        "presence is how sure you are that it is visible, from 0 to 1. "
        "Always include a box as fractions of the frame width and height: left, top, right, bottom, "
        "each from 0 to 1, origin at the top-left. right must be greater than left and bottom greater than top. "
        f"The box is discarded when present is false or presence is below {PRESENCE_MIN:.1f}. "
        "confidence is how sure the box edges are, from 0 to 1. reason is one short sentence."
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


def _unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name)
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(name)
    return number


@dataclass(frozen=True)
class TileHit:
    tile: str
    time_s: float
    score: float
    reason: str


def sheet_hits_from_payload(payload: Any, plan: SheetPlan) -> list[TileHit]:
    if not isinstance(payload, dict) or not isinstance(payload.get("moments"), list):
        raise ValueError("sheet moments")
    times = {tid: t for tid, t in plan.tiles}
    best: dict[str, TileHit] = {}
    for item in payload["moments"]:
        if not isinstance(item, dict):
            raise ValueError("tile")
        tile = item.get("tile")
        if not isinstance(tile, str) or tile not in times:
            raise ValueError("tile")
        score = _unit(item.get("score"), "score")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason")
        hit = TileHit(tile, times[tile], score, reason.strip())
        prev = best.get(tile)
        if prev is None or hit.score > prev.score:
            best[tile] = hit
    return list(best.values())


def fractions_to_bbox(
    left: float, top: float, right: float, bottom: float, width: int, height: int
) -> BBox:
    if width < 1 or height < 1:
        raise ValueError("frame size")
    if float(right) <= float(left) or float(bottom) <= float(top):
        raise ValueError("empty box")
    x = int(round(float(left) * width))
    y = int(round(float(top) * height))
    x2 = int(round(float(right) * width))
    y2 = int(round(float(bottom) * height))
    return BBox(x, y, max(1, x2 - x), max(1, y2 - y)).clamp(width, height)


@dataclass(frozen=True)
class Placement:
    target: str
    presence: float
    confidence: float
    reason: str
    region: BBox


def placement_from_payload(payload: Any, width: int, height: int) -> Placement | None:
    """Drop the box when the presence gate fails. The model always returns a rectangle."""
    if not isinstance(payload, dict):
        raise ValueError("placement")
    if "present" not in payload or "presence" not in payload:
        raise ValueError("presence")
    present = payload["present"]
    if not isinstance(present, bool):
        raise ValueError("present")
    presence = _unit(payload["presence"], "presence")
    if not present or presence < PRESENCE_MIN:
        return None
    target = payload.get("target")
    reason = payload.get("reason")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason")
    region = fractions_to_bbox(
        _unit(payload.get("left"), "left"),
        _unit(payload.get("top"), "top"),
        _unit(payload.get("right"), "right"),
        _unit(payload.get("bottom"), "bottom"),
        width,
        height,
    )
    confidence = _unit(payload.get("confidence"), "confidence")
    return Placement(target.strip(), presence, confidence, reason.strip(), region)


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
    image: bytes,
    prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    model: str,
) -> str:
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
                        },
                    },
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
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
    image: bytes,
    prompt: str,
    schema_name: str,
    schema: dict[str, Any],
    validate: Callable[[Any], Any],
    model: str | None = None,
) -> Any:
    """POST one image. Parse and validate. Retry once, then return None."""
    chosen = model or moment_model()
    for attempt in (1, 2):
        try:
            text = _post_content(
                client,
                key=key,
                image=image,
                prompt=prompt,
                schema_name=schema_name,
                schema=schema,
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


def _shot_index(shots, t: float) -> int:
    for i, shot in enumerate(shots):
        if shot.start_s <= t < shot.end_s:
            return i
    return max(0, len(shots) - 1)


@dataclass
class _Frame:
    start_s: float
    end_s: float
    reason: str
    shot_i: int
    frame: np.ndarray


def _none_found() -> RuntimeError:
    return RuntimeError(
        "no placement moments found (need stable, face-clear windows with a surface/prop)"
    )


def suggest_with_jev(
    video_path: str | Path,
    n: int = 5,
    *,
    out_dir: str | Path = "runs/suggest",
    window_s: float = WINDOW_S,
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
        diagnostics["finder"] = "jev"

    _err(f"model {moment_model()} duration {duration:.1f}s")
    aspect = width / height
    owns_client = client is None
    client = client or httpx.Client(timeout=CALL_TIMEOUT_S)
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise FileNotFoundError(f"cannot open video: {video_path}")

        with tempfile.TemporaryDirectory(prefix="jev-sheets-") as tmp:
            estimate = max(1, int(math.ceil(duration * SAMPLE_FPS)))
            paths = sample_tiles(video_path, Path(tmp), _layout_for(aspect, estimate))
            if not paths:
                raise RuntimeError("ffmpeg produced no frames")
            layout = _layout_for(aspect, len(paths))
            plans = plan_sheets(len(paths), fps=SAMPLE_FPS, per_sheet=PER_SHEET)
            jpegs = _sheet_jpegs(paths, plans, layout)

        def _screen(item: tuple[bytes, SheetPlan]) -> list[TileHit] | None:
            jpeg, plan = item
            ids = [tid for tid, _ in plan.tiles]
            return call_validated(
                client,
                key=key,
                image=jpeg,
                prompt=screen_prompt(plan, brand),
                schema_name="placement_sheet",
                schema=sheet_schema(ids),
                validate=lambda payload: sheet_hits_from_payload(payload, plan),
            )

        screened = _parallel(_screen, list(zip(jpegs, plans)))
        hits: list[TileHit] = []
        failed = 0
        for result in screened:
            if result is None:
                failed += 1
                continue
            hits.extend(result)
        _err(f"{len(plans)} sheets, {failed} failed, {len(hits)} tiles")
        if not hits:
            _diag(0)
            if plans and failed == len(plans):
                raise RuntimeError(
                    "placement screening failed; no contact sheet returned a valid answer"
                )
            raise _none_found()

        by_time: dict[float, TileHit] = {}
        for hit in hits:
            prev = by_time.get(hit.time_s)
            if prev is None or hit.score > prev.score:
                by_time[hit.time_s] = hit
        shots = detect_shots(cap)
        blank = np.zeros((2, 2, 3), np.uint8)
        screen_cands = [
            _Cand(
                start_s=hit.time_s,
                end_s=hit.time_s,
                frame=blank,
                region=BBox(0, 0, 1, 1),
                whats_there="",
                score=hit.score,
                reason=hit.reason,
                shot_i=_shot_index(shots, hit.time_s),
            )
            for hit in by_time.values()
        ]
        min_gap = max(MIN_WINDOW_S, duration / max(n * 2.5, 1))
        short = pick_spread(screen_cands, VERIFY_TOP, min_gap_s=min_gap)
        if len(short) < min(n, len(screen_cands)):
            short = pick_spread(screen_cands, VERIFY_TOP, min_gap_s=MIN_WINDOW_S * 0.5)

        prepared: list[_Frame] = []
        for cand in short:
            if shots:
                shot = shots[_shot_index(shots, cand.start_s)]
                lo, hi = shot.start_s, shot.end_s
            else:
                lo, hi = 0.0, duration
            start_s, end_s = moment_window(
                cand.start_s, duration, window_s, lo=lo, hi=hi
            )
            cam = camera_motion_px(cap, start_s, end_s)
            if cam > MAX_CAM_MOTION:
                _err(f"skip {cand.start_s:.1f}s camera motion {cam:.0f}px")
                continue
            frame = _read_frame_at(cap, cand.start_s, lo_s=lo, hi_s=hi)
            if frame is None:
                _err(f"skip {cand.start_s:.1f}s unreadable frame")
                continue
            prepared.append(
                _Frame(
                    start_s=start_s,
                    end_s=end_s,
                    reason=cand.reason,
                    shot_i=cand.shot_i,
                    frame=frame,
                )
            )
        _err(f"verifying {len(prepared)}")

        def _verify(item: _Frame) -> _Cand | None:
            h, w = item.frame.shape[:2]
            if w < 2 or h < 2:
                return None
            placed = call_validated(
                client,
                key=key,
                image=_jpeg(item.frame, max_side=MAX_SIDE),
                prompt=box_prompt(brand),
                schema_name="placement_box",
                schema=BOX_SCHEMA,
                validate=lambda payload: placement_from_payload(payload, w, h),
            )
            if not isinstance(placed, Placement):
                return None
            return _Cand(
                start_s=item.start_s,
                end_s=item.end_s,
                frame=item.frame,
                region=placed.region,
                whats_there=placed.target,
                score=placed.confidence,
                reason=placed.reason or item.reason,
                shot_i=item.shot_i,
            )

        verified = [c for c in _parallel(_verify, prepared) if c is not None]
        _diag(len(verified))
        if not verified:
            raise _none_found()

        chosen = pick_spread(verified, n, min_gap_s=min_gap)
        if len(chosen) < min(n, len(verified)):
            chosen = pick_spread(verified, min(n, len(verified)), min_gap_s=MIN_WINDOW_S * 0.5)

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
