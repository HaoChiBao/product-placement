"""Gemini product finder: prompt, 0-1000 boxes, and per-shot merge. HTTP is mocked."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from product_placement.brand import Brand, BrandRules
from product_placement.jev_moments import (
    DEFAULT_MODEL,
    FRAMES_PER_CALL,
    OPENROUTER_URL,
    VISIBILITY_MIN,
    call_validated,
    chunked,
    finder_prompt,
    gemini_box_to_bbox,
    keyframe_times,
    merge_hits,
    moment_model,
    parse_json_content,
    parse_objects,
    suggest_with_jev,
    track_window,
    Hit,
)
from product_placement.suggest import suggest
from product_placement.vision import BBox, detect_shots

_KEY = "super-secret-openrouter-key"
_TIME = re.compile(r"Frame (\d+) at ([0-9.]+)s")


def _jpeg() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _obj(**overrides) -> dict:
    raw = {
        "frame": 0,
        "label": "green wine bottle",
        "category": "bottle",
        "swap_with": "branded wine bottle",
        "visibility": 0.9,
        "box_2d": [200, 100, 700, 400],
    }
    raw.update(overrides)
    return raw


def _hit(**overrides) -> Hit:
    raw = dict(
        shot_i=0,
        time_s=1.0,
        label="green wine bottle",
        category="bottle",
        swap_with="branded wine bottle",
        visibility=0.9,
        box=BBox(100, 80, 40, 60),
        key=0,
    )
    raw.update(overrides)
    return Hit(**raw)


def test_parse_json_content_strips_fences_and_rejects_invalid():
    assert parse_json_content('```json\n{"objects": []}\n```') == {"objects": []}
    assert parse_json_content('```JSON\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_content("not json")
    with pytest.raises(ValueError):
        parse_json_content("```json\n{not json}\n```")


def test_keyframe_times_are_one_per_second_and_inset_from_cuts():
    times = keyframe_times(0.0, 10.0)
    assert len(times) == 10
    assert times[0] >= 0.15
    assert times[-1] <= 10.0 - 0.15
    assert times == sorted(times)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(0.5 <= gap <= 1.5 for gap in gaps)

    short = keyframe_times(2.0, 4.0)
    assert len(short) == 3
    assert short[0] >= 2.15
    assert short[-1] <= 3.85

    tiny = keyframe_times(0.0, 0.25)
    assert tiny == [0.125]


def test_batches_are_about_eight_frames():
    assert [len(batch) for batch in chunked(list(range(12)), FRAMES_PER_CALL)] == [8, 4]
    assert chunked(list(range(8)), FRAMES_PER_CALL) == [list(range(8))]


def test_gemini_box_uses_ymin_xmin_ymax_xmax_on_a_0_to_1000_scale():
    box = gemini_box_to_bbox([100, 500, 300, 900], 1000, 1000)
    assert box.as_dict() == {"x": 500, "y": 100, "w": 400, "h": 200}
    full = gemini_box_to_bbox([0, 0, 1000, 1000], 320, 180)
    assert full.as_dict() == {"x": 0, "y": 0, "w": 320, "h": 180}
    clamped = gemini_box_to_bbox([-20, -10, 1200, 1500], 200, 100)
    assert clamped.as_dict() == {"x": 0, "y": 0, "w": 200, "h": 100}
    with pytest.raises(ValueError):
        gemini_box_to_bbox([100, 100, 100, 400], 100, 100)
    tiny = gemini_box_to_bbox([200, 100, 500, 400], 10, 10)
    assert tiny.as_dict() == {"x": 1, "y": 2, "w": 3, "h": 3}
    collapsed = gemini_box_to_bbox([0.2, 0.1, 0.8, 0.4], 1000, 1000)
    assert collapsed.as_dict() == {"x": 0, "y": 0, "w": 1, "h": 1}


def test_prompt_asks_for_existing_objects_and_a_gemini_box():
    brand = Brand(
        id="b",
        name="Northstar",
        brief="Soda can. Prefer tables.",
        rules=BrandRules(prefer=["cups", "tables"], avoid=["beds"], placement="resting"),
    )
    text = finder_prompt([0.15, 1.0], brand)
    assert "EXISTING physical objects" in text
    assert "could swap" in text
    assert "never empty surfaces" in text
    assert "box_2d" in text
    assert "[ymin, xmin, ymax, xmax]" in text
    assert "0-1000" in text
    assert "visibility" in text
    assert "swap_with" in text
    assert '"objects"' in text
    assert "Frame 0 at 0.150s" in text
    assert "Frame 1 at 1.000s" in text
    assert "contact sheet" not in text.lower()
    assert "flat surface" not in text.lower()
    assert "Product category: Soda can" in text
    assert "Prefer: cups, tables" in text
    assert "Avoid: beds" in text
    assert "Brand: Northstar" in text
    bare = finder_prompt([1.0], None)
    assert "Product category" not in bare
    assert "Prefer:" not in bare


def _xyxy(box: list[int]) -> BBox:
    x1, y1, x2, y2 = box
    return BBox(x1, y1, x2 - x1, y2 - y1)


def test_parse_objects_skips_empty_surfaces_and_bad_boxes():
    payload = {
        "objects": [
            _obj(),
            _obj(label="empty tabletop", category="surface", visibility=0.99),
            _obj(label="person", category="person", visibility=0.99),
            _obj(label="mug", box_2d=[10, 10, 10, 40]),
            _obj(frame=3, label="can"),
            _obj(label="glass", visibility="high"),
        ]
    }
    found = parse_objects(payload, [(640, 360)])
    assert [item.label for item in found] == ["green wine bottle"]
    assert found[0].box.as_dict() == gemini_box_to_bbox(
        [200, 100, 700, 400], 640, 360
    ).as_dict()
    assert found[0].swap_with == "branded wine bottle"
    with pytest.raises(ValueError):
        parse_objects({"moments": []}, [(10, 10)])


def test_suits_tracks_use_category_overlap_and_shot_midpoints():
    """Timing and merge from the validated Suits clip (wine bottle spans 2.0–4.0s)."""
    shot2 = (2.002, 3.962)
    frames2 = [2.152, 2.982, 3.812]
    bottle_boxes = (
        (2.152, [1048, 435, 1137, 807], "green wine bottle"),
        (2.982, [1046, 435, 1142, 849], "wine bottle"),
        (3.812, [1046, 436, 1137, 823], "green wine bottle"),
    )
    glass_a = (
        (2.152, [818, 563, 952, 924]),
        (2.982, [820, 563, 954, 924]),
        (3.812, [818, 563, 954, 921]),
    )
    glass_b = (
        (2.152, [1073, 548, 1204, 885]),
        (2.982, [1071, 548, 1202, 889]),
        (3.812, [1071, 548, 1202, 883]),
    )
    plate = [557, 723, 929, 851]
    steak = [600, 750, 800, 820]
    hits: list[Hit] = []
    key = 0
    for t, box, label in bottle_boxes:
        hits.append(
            _hit(
                shot_i=1,
                time_s=t,
                label=label,
                category="bottle",
                visibility=0.85,
                box=_xyxy(box),
                key=key,
                swap_with="branded wine bottle",
            )
        )
        key += 1
    for t, box in glass_a:
        hits.append(
            _hit(
                shot_i=1,
                time_s=t,
                label="wine glass with red wine",
                category="wine_glass",
                visibility=0.8,
                box=_xyxy(box),
                key=key,
                swap_with="branded wine glass",
            )
        )
        key += 1
    for t, box in glass_b:
        hits.append(
            _hit(
                shot_i=1,
                time_s=t,
                label="red wine glass",
                category="wine_glass",
                visibility=0.75,
                box=_xyxy(box),
                key=key,
            )
        )
        key += 1
    hits.append(
        _hit(
            shot_i=1,
            time_s=2.152,
            label="plate with steak",
            category="plate",
            visibility=0.7,
            box=_xyxy(plate),
            key=key,
        )
    )
    key += 1
    hits.append(
        _hit(
            shot_i=1,
            time_s=2.152,
            label="steak",
            category="food",
            visibility=0.9,
            box=_xyxy(steak),
            key=key,
        )
    )
    key += 1
    hits.append(
        _hit(
            shot_i=1,
            time_s=2.982,
            label="wristwatch",
            category="other",
            visibility=0.45,
            box=_xyxy([770, 455, 839, 516]),
            key=key,
            swap_with="luxury watch",
        )
    )
    key += 1
    hits.append(
        _hit(
            shot_i=1,
            time_s=2.152,
            label="waiter bill folder",
            category="other",
            visibility=0.25,
            box=_xyxy([1450, 359, 1605, 604]),
            key=key,
        )
    )
    tracks = merge_hits(hits, [(0.0, 2.002), shot2], [[0.15, 1.001, 1.852], frames2])
    by_label = {track.label: track for track in tracks}
    assert "waiter bill folder" not in by_label
    assert "steak" not in by_label
    bottle = by_label["green wine bottle"]
    assert bottle.sightings == 3
    assert bottle.category == "bottle"
    assert bottle.start_s == pytest.approx(2.002)
    assert bottle.end_s == pytest.approx(3.962)
    assert bottle.visibility == pytest.approx(0.85)
    assert bottle.box.as_dict() == _xyxy(bottle_boxes[0][1]).as_dict()
    assert bottle.frame_key == 0
    assert by_label["wine glass with red wine"].start_s == pytest.approx(2.002)
    assert by_label["red wine glass"].start_s == pytest.approx(2.002)
    assert by_label["wine glass with red wine"].end_s == pytest.approx(3.962)
    assert by_label["red wine glass"].end_s == pytest.approx(3.962)
    watch = by_label["wristwatch"]
    assert watch.sightings == 1
    assert watch.visibility >= VISIBILITY_MIN
    assert watch.start_s == pytest.approx(2.567)
    assert watch.end_s == pytest.approx(3.397)
    plate_track = by_label["plate with steak"]
    assert plate_track.category == "plate"
    assert plate_track.sightings == 1


def test_inner_food_is_dropped_when_listed_before_its_plate():
    plate = _xyxy([557, 723, 929, 851])
    steak = _xyxy([600, 750, 800, 820])
    hits = [
        _hit(
            shot_i=0,
            time_s=1.0,
            label="steak",
            category="food",
            visibility=0.9,
            box=steak,
            key=0,
        ),
        _hit(
            shot_i=0,
            time_s=1.0,
            label="plate with steak",
            category="plate",
            visibility=0.7,
            box=plate,
            key=1,
        ),
    ]
    tracks = merge_hits(hits, [(0.0, 2.0)], [[0.15, 1.0, 1.85]])
    assert [track.label for track in tracks] == ["plate with steak"]


def test_track_window_uses_shot_edges_and_midpoints():
    frames = [2.152, 2.982, 3.812]
    assert track_window([2.152, 3.812], frames, 2.002, 3.962) == (2.002, 3.962)
    assert track_window([2.982], frames, 2.002, 3.962) == (2.567, 3.397)
    assert track_window([1.852], [0.15, 1.001, 1.852], 0.0, 2.002) == (1.426, 2.002)
    assert track_window([4.112], [4.112, 5.262, 6.411], 3.962, 10.01) == (
        3.962,
        round((4.112 + 5.262) / 2, 3),
    )


def test_moment_model_env(monkeypatch):
    monkeypatch.delenv("MOMENT_MODEL", raising=False)
    assert moment_model() == DEFAULT_MODEL
    assert DEFAULT_MODEL == "google/gemini-3.8-flash"
    monkeypatch.setenv("MOMENT_MODEL", "google/custom")
    assert moment_model() == "google/custom"
    monkeypatch.setenv("MOMENT_MODEL", "  ")
    assert moment_model() == DEFAULT_MODEL


def test_call_sends_frames_with_low_reasoning_and_retries_invalid_json():
    calls = {"n": 0}
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        seen.append(body)
        if calls["n"] == 1:
            return httpx.Response(500, text=f"denied {_KEY}")
        content = [{"type": "text", "text": json.dumps({"objects": [_obj(frame=1)]})}]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with _client(handler) as client:
        found = call_validated(
            client,
            key=_KEY,
            images=[_jpeg(), _jpeg()],
            prompt=finder_prompt([0.15, 1.0], None),
            schema_name="products",
            validate=lambda payload: parse_objects(payload, [(80, 40), (80, 40)]),
            model=DEFAULT_MODEL,
        )
    assert calls["n"] == 2
    assert len(found) == 1
    assert found[0].frame == 1
    body = seen[1]
    request_body = seen[0]
    assert request_body["model"] == DEFAULT_MODEL
    assert request_body["reasoning"] == {"effort": "low"}
    assert request_body["response_format"] == {"type": "json_object"}
    parts = request_body["messages"][0]["content"]
    images = [part for part in parts if part["type"] == "image_url"]
    assert len(images) == 2
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["messages"][0]["content"][0]["text"].startswith("You are finding EXISTING")


def test_invalid_json_retries_once_then_skips(capsys):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text=f"not json {_KEY}")

    with _client(handler) as client:
        result = call_validated(
            client,
            key=_KEY,
            images=[_jpeg()],
            prompt="x",
            schema_name="products",
            validate=lambda payload: parse_objects(payload, [(8, 8)]),
        )
    assert result is None
    assert calls["n"] == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _KEY not in captured.err
    assert "[redacted]" in captured.err


def test_suggest_dispatches_to_jev_when_key_set(monkeypatch):
    seen = {}

    def fake(video_path, n=5, **kwargs):
        seen["video"] = video_path
        seen["n"] = n
        seen["brand"] = kwargs.get("brand")
        seen["out_dir"] = kwargs.get("out_dir")
        return ["moments"]

    monkeypatch.setenv("OPENROUTER_API_KEY", "dispatch-key")
    monkeypatch.setattr("product_placement.jev_moments.suggest_with_jev", fake)
    brand = object()
    assert suggest("missing.mp4", n=4, out_dir="out", brand=brand) == ["moments"]
    assert seen == {"video": "missing.mp4", "n": 4, "brand": brand, "out_dir": "out"}


def test_suggest_with_jev_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        suggest_with_jev("nope.mp4", api_key="")


def _write_static_mp4(path: Path, *, seconds: float = 12.0, fps: float = 24.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_name(path.stem + "_mp4v.mp4")
    w, h = 320, 180
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened()
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = (40, 80, 90)
    cv2.rectangle(frame, (40, 30), (200, 140), (20, 180, 220), -1)
    for _ in range(int(seconds * fps)):
        writer.write(frame)
    writer.release()
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


def _write_cut_mp4(path: Path) -> Path:
    """10s clip, cuts at 2s and 4s."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_name(path.stem + "_mp4v.mp4")
    w, h, fps = 640, 360, 24.0
    rng = np.random.default_rng(1)
    noise = rng.integers(0, 255, (h, w), dtype=np.uint8)
    by, bx = int(h * 0.18), int(w * 0.18)
    shots = (
        (0.0, 2.0, 0, (20, 20, 210)),
        (2.0, 4.0, 240, (20, 190, 40)),
        (4.0, 10.0, 0, (210, 150, 20)),
    )
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened()
    for i in range(int(10 * fps)):
        t = i / fps
        for start, end, shift, color in shots:
            if start <= t < end:
                border = np.roll(noise, shift, axis=1)
                frame = np.full((h, w, 3), color, np.uint8)
                for c in range(3):
                    frame[:by, :, c] = border[:by]
                    frame[-by:, :, c] = border[-by:]
                    frame[:, :bx, c] = border[:, :bx]
                    frame[:, -bx:, c] = border[:, -bx:]
                writer.write(frame)
                break
    writer.release()
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


def _center_channel(frame: np.ndarray) -> int:
    h, w = frame.shape[:2]
    crop = frame[int(h * 0.3) : int(h * 0.7), int(w * 0.3) : int(w * 0.7)]
    return int(np.argmax(crop.mean(axis=(0, 1))))


def _frame_times(text: str) -> list[tuple[int, float]]:
    return [(int(i), float(t)) for i, t in _TIME.findall(text)]


def test_suggest_with_jev_mocked_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    monkeypatch.delenv("MOMENT_MODEL", raising=False)
    video = _write_cut_mp4(tmp_path / "cuts.mp4")
    cap = cv2.VideoCapture(str(video))
    try:
        shots = detect_shots(cap)
    finally:
        cap.release()
    assert len(shots) == 3
    sampled = [keyframe_times(shot.start_s, shot.end_s) for shot in shots]
    bottle_times = sampled[2][:2]
    phone_time = sampled[1][0]
    mug_time = sampled[2][-1]
    assert len(bottle_times) == 2
    assert mug_time not in bottle_times
    brand = Brand(
        id="b",
        name="Northstar",
        brief="Soda can. Prefer tables.",
        rules=BrandRules(prefer=["cups"], avoid=["beds"]),
    )
    seen: list[dict] = []
    bottle_box = [400, 280, 780, 430]
    phone_box = [120, 500, 360, 700]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = body["messages"][0]["content"][0]["text"]
        images = [part for part in body["messages"][0]["content"] if part["type"] == "image_url"]
        seen.append({"auth": request.headers["authorization"], "body": body, "images": len(images)})
        objects = []
        for index, t in _frame_times(text):
            if any(abs(t - sample) < 0.02 for sample in bottle_times):
                first = abs(t - bottle_times[0]) <= abs(t - bottle_times[1])
                objects.append(
                    _obj(
                        frame=index,
                        label="green wine bottle" if first else "wine bottle",
                        visibility=0.92 if first else 0.8,
                        box_2d=bottle_box,
                    )
                )
            elif abs(t - phone_time) < 0.02:
                objects.append(
                    _obj(
                        frame=index,
                        label="black phone",
                        category="phone",
                        swap_with="branded phone",
                        visibility=0.75,
                        box_2d=phone_box,
                    )
                )
            elif abs(t - mug_time) < 0.02:
                objects.append(
                    _obj(
                        frame=index,
                        label="coffee mug",
                        category="cup",
                        swap_with="branded mug",
                        visibility=0.1,
                        box_2d=[10, 10, 80, 80],
                    )
                )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"objects": objects})}}]}
        )

    with _client(handler) as client:
        moments = suggest_with_jev(
            video,
            n=5,
            out_dir=tmp_path / "out",
            brand=brand,
            client=client,
        )

    assert seen
    assert all(item["auth"] == f"Bearer {_KEY}" for item in seen)
    assert all(item["images"] <= FRAMES_PER_CALL for item in seen)
    total_frames = sum(len(times) for times in sampled)
    assert sum(item["images"] for item in seen) == total_frames
    if total_frames > FRAMES_PER_CALL:
        assert any(item["images"] == FRAMES_PER_CALL for item in seen)
    for item in seen:
        body = item["body"]
        assert body["model"] == "google/gemini-3.8-flash"
        assert body["reasoning"] == {"effort": "low"}
        assert body["response_format"]["type"] == "json_object"
        prompt = body["messages"][0]["content"][0]["text"]
        assert "EXISTING physical objects" in prompt
        assert "contact sheet" not in prompt.lower()
        assert "Product category: Soda can" in prompt
        assert "Avoid: beds" in prompt
        raw = body["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), cv2.IMREAD_COLOR)
        assert frame is not None
        assert frame.shape[1] == 640 and frame.shape[0] == 360

    by_label = {m.whats_there: m for m in moments}
    assert set(by_label) == {"green wine bottle", "black phone"}
    bottle = by_label["green wine bottle"]
    phone = by_label["black phone"]
    assert "coffee mug" not in by_label
    assert bottle.score == pytest.approx((0.92 + 0.8) / 2)
    assert "swap with branded wine bottle" in bottle.reason
    assert bottle.region.as_dict() == gemini_box_to_bbox(bottle_box, 640, 360).as_dict()
    assert phone.region.as_dict() == gemini_box_to_bbox(phone_box, 640, 360).as_dict()
    phone_window = track_window(
        [phone_time], sampled[1], shots[1].start_s, shots[1].end_s
    )
    bottle_window = track_window(
        bottle_times, sampled[2], shots[2].start_s, shots[2].end_s
    )
    assert (phone.start_s, phone.end_s) == pytest.approx(phone_window)
    assert (bottle.start_s, bottle.end_s) == pytest.approx(bottle_window)
    assert bottle.end_s > bottle.start_s

    bounds = [(shot.start_s, shot.end_s) for shot in shots]
    assert len(bounds) == 3
    for moment in moments:
        owners = [
            i
            for i, (start, end) in enumerate(bounds)
            if moment.start_s >= start - 1e-3 and moment.end_s <= end + 1e-3
        ]
        assert owners, (moment.start_s, moment.end_s)
        assert not any(moment.start_s < cut < moment.end_s for cut in (bounds[1][0], bounds[2][0]))
        frame = cv2.imread(str(moment.frame_path))
        assert frame is not None
        assert _center_channel(frame) == {0: 2, 1: 1, 2: 0}[owners[0]]
    saved = json.loads((tmp_path / "out" / "moments.json").read_text())
    assert saved["moments"][0]["whats_there"] == moments[0].whats_there
    assert saved["brand_id"] == "b"
    assert (tmp_path / "out" / "contact_sheet.jpg").is_file()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _KEY not in captured.err


class _Det:
    def __init__(self, *args, **kwargs):
        pass

    def detect(self, frame):
        return []


def _moment_sig(moments):
    return [
        (
            round(m.start_s, 3),
            round(m.end_s, 3),
            m.whats_there,
            m.region.as_dict(),
            round(m.score, 4),
        )
        for m in moments
    ]


@pytest.mark.parametrize("failure", ["empty", "error"])
def test_suggest_falls_back_to_cv_when_jev_fails(failure, tmp_path, monkeypatch, capsys):
    video = _write_static_mp4(tmp_path / "clip.mp4", seconds=3.0)
    monkeypatch.setattr("product_placement.suggest.FaceDetector", _Det)
    monkeypatch.setattr("product_placement.suggest.YoloX", _Det)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    classical = suggest(
        video,
        n=4,
        out_dir=tmp_path / "cv",
        yunet_path="yunet.onnx",
        yolox_path="yolox.onnx",
    )
    assert classical

    def empty(*args, **kwargs):
        return []

    def explode(*args, **kwargs):
        raise RuntimeError(f"router down {_KEY}")

    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    monkeypatch.setattr(
        "product_placement.jev_moments.suggest_with_jev",
        empty if failure == "empty" else explode,
    )
    got = suggest(
        video,
        n=4,
        out_dir=tmp_path / "fallback",
        yunet_path="yunet.onnx",
        yolox_path="yolox.onnx",
    )
    assert _moment_sig(got) == _moment_sig(classical)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _KEY not in captured.err
    assert "[suggest] Jev fallback:" in captured.err
    if failure == "empty":
        assert "no moments" in captured.err
    else:
        assert "router down" in captured.err
        assert "[redacted]" in captured.err
