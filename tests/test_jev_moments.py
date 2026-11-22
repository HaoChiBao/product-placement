"""Jev moment finder: schema parsing, tile times, and box conversion. HTTP is mocked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from product_placement.brand import Brand, BrandRules
from product_placement.crop_stitch import probe_video
from product_placement.jev_moments import (
    OPENROUTER_URL,
    box_prompt,
    call_validated,
    choose_layout,
    compose_sheet,
    fractions_to_bbox,
    moment_model,
    moment_window,
    parse_json_content,
    placement_from_payload,
    plan_sheets,
    screen_prompt,
    sheet_hits_from_payload,
    sheet_schema,
    suggest_with_jev,
)
from product_placement.suggest import _read_frame_at, suggest

_KEY = "super-secret-openrouter-key"


def _jpeg() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()


def _box(**overrides) -> dict:
    raw = {
        "target": "mug",
        "present": True,
        "presence": 0.9,
        "left": 0.1,
        "top": 0.2,
        "right": 0.4,
        "bottom": 0.5,
        "confidence": 0.8,
        "reason": "clear mug on the table",
    }
    raw.update(overrides)
    return raw


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_json_content_strips_fences_and_rejects_invalid():
    assert parse_json_content('```json\n{"moments": []}\n```') == {"moments": []}
    assert parse_json_content('```JSON\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_content("not json")
    with pytest.raises(ValueError):
        parse_json_content("```json\n{not json}\n```")


def test_plan_sheets_maps_tiles_to_timestamps():
    sheets = plan_sheets(61, fps=1.0, per_sheet=60)
    assert len(sheets) == 2
    assert sheets[0].tiles[0] == ("T01", 0.0)
    assert sheets[0].tiles[59] == ("T60", 59.0)
    assert sheets[0].time_of("T02") == 1.0
    assert sheets[0].time_of("T99") is None
    assert sheets[1].tiles == (("T01", 60.0),)
    short = plan_sheets(3, fps=1.0, per_sheet=60)
    assert short[0].tiles == (("T01", 0.0), ("T02", 1.0), ("T03", 2.0))


def test_layout_and_sheet_stay_within_max_side():
    layout = choose_layout(16 / 9, 60, 1568)
    assert layout.cols * layout.tile_w <= 1568
    assert layout.rows * layout.tile_h <= 1568
    assert layout.capacity >= 60
    assert layout.tile_w >= 2 and layout.tile_h >= 2
    small = choose_layout(16 / 9, 4, 1568)
    frames = [
        np.full((small.tile_h, small.tile_w, 3), 255, np.uint8) for _ in range(4)
    ]
    sheet = compose_sheet(frames, ["T01", "T02", "T03", "T04"], small)
    assert sheet.shape[0] <= 1568
    assert sheet.shape[1] <= 1568
    assert int(sheet[0, 0, 0]) == 0


def test_fractions_to_bbox_uses_pixel_xywh():
    box = fractions_to_bbox(0.1, 0.2, 0.4, 0.5, 1920, 1080)
    assert box.as_dict() == {"x": 192, "y": 216, "w": 576, "h": 324}
    full = fractions_to_bbox(0.0, 0.0, 1.0, 1.0, 320, 180)
    assert full.as_dict() == {"x": 0, "y": 0, "w": 320, "h": 180}
    with pytest.raises(ValueError):
        fractions_to_bbox(0.8, 0.1, 0.2, 0.5, 100, 100)


def test_absent_and_low_presence_drop_the_box():
    assert placement_from_payload(_box(present=False, presence=0.99), 1920, 1080) is None
    assert placement_from_payload(_box(present=True, presence=0.49), 100, 80) is None
    kept = placement_from_payload(_box(present=True, presence=0.5, left=0.1, top=0.1, right=0.5, bottom=0.5), 100, 80)
    assert kept is not None
    assert kept.region.as_dict() == {"x": 10, "y": 8, "w": 40, "h": 32}
    with pytest.raises(ValueError):
        placement_from_payload(_box(present="yes"), 100, 100)
    with pytest.raises(ValueError):
        placement_from_payload(_box(left=0.9, right=0.2), 100, 100)


def test_fenced_sheet_response_maps_tile_time():
    plan = plan_sheets(3, fps=1.0)[0]
    seen: list[httpx.Request] = []
    body = "```json\n" + json.dumps(
        {"moments": [{"tile": "T02", "score": 0.8, "reason": "clear table"}]}
    ) + "\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})

    with _client(handler) as client:
        hits = call_validated(
            client,
            key=_KEY,
            image=_jpeg(),
            prompt=screen_prompt(plan, None),
            schema_name="placement_sheet",
            schema=sheet_schema([tid for tid, _ in plan.tiles]),
            validate=lambda payload: sheet_hits_from_payload(payload, plan),
            model="typesafe/jev-router",
        )
    assert len(hits) == 1
    assert hits[0].tile == "T02"
    assert hits[0].time_s == 1.0
    assert hits[0].reason == "clear table"
    request = seen[0]
    assert str(request.url) == OPENROUTER_URL
    payload = json.loads(request.content)
    assert payload["model"] == "typesafe/jev-router"
    assert payload["response_format"]["json_schema"]["strict"] is True
    enum = payload["response_format"]["json_schema"]["schema"]["properties"]["moments"]["items"][
        "properties"
    ]["tile"]["enum"]
    assert enum == ["T01", "T02", "T03"]
    url = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert request.headers["authorization"] == f"Bearer {_KEY}"


def test_message_content_list_and_retry_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            content = "```json\n{bad}\n```"
        else:
            content = [{"type": "text", "text": '{"moments": []}'}]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    plan = plan_sheets(1)[0]
    with _client(handler) as client:
        hits = call_validated(
            client,
            key=_KEY,
            image=_jpeg(),
            prompt="x",
            schema_name="placement_sheet",
            schema={"type": "object"},
            validate=lambda payload: sheet_hits_from_payload(payload, plan),
        )
    assert hits == []
    assert calls["n"] == 2


def test_invalid_json_retries_once_then_skips(capsys):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text=f"denied {_KEY}")

    with _client(handler) as client:
        result = call_validated(
            client,
            key=_KEY,
            image=_jpeg(),
            prompt="x",
            schema_name="placement_box",
            schema={"type": "object"},
            validate=lambda payload: placement_from_payload(payload, 10, 10),
        )
    assert result is None
    assert calls["n"] == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _KEY not in captured.err
    assert "[redacted]" in captured.err


def test_call_drops_absent_target_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_box(present=False, presence=0.99))}}]},
        )

    with _client(handler) as client:
        result = call_validated(
            client,
            key=_KEY,
            image=_jpeg(),
            prompt=box_prompt(None),
            schema_name="placement_box",
            schema={"type": "object"},
            validate=lambda payload: placement_from_payload(payload, 200, 100),
        )
    assert result is None
    assert calls["n"] == 1


def test_brand_hint_is_in_both_prompts():
    brand = Brand(
        id="b",
        name="Northstar",
        brief="Soda can. Prefer tables.",
        rules=BrandRules(prefer=["cups", "tables"], avoid=["beds"], placement="resting"),
    )
    plan = plan_sheets(2)[0]
    for text in (screen_prompt(plan, brand), box_prompt(brand)):
        assert "Product category: Soda can" in text
        assert "Prefer: cups, tables" in text
        assert "Avoid: beds" in text
        assert "Brand: Northstar" in text
    assert "Product category" not in screen_prompt(plan, None)
    assert "Prefer:" not in box_prompt(None)


def test_moment_model_env(monkeypatch):
    monkeypatch.setenv("MOMENT_MODEL", "typesafe/custom")
    assert moment_model() == "typesafe/custom"
    monkeypatch.setenv("MOMENT_MODEL", "  ")
    assert moment_model() == "typesafe/jev-router"


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


def test_suggest_with_jev_mocked_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    monkeypatch.setenv("MOMENT_MODEL", "typesafe/jev-router")
    video = _write_static_mp4(tmp_path / "clip.mp4")
    _, _, duration, _ = probe_video(video)
    assert duration > 5
    brand = Brand(
        id="b",
        name="Northstar",
        brief="Soda can. Prefer tables.",
        rules=BrandRules(prefer=["cups"], avoid=["beds"]),
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(
            {
                "auth": request.headers["authorization"],
                "body": body,
            }
        )
        text = body["messages"][0]["content"][0]["text"]
        if "Valid tile ids" in text:
            content = (
                "```json\n"
                + json.dumps(
                    {"moments": [{"tile": "T06", "score": 0.91, "reason": "clear table"}]}
                )
                + "\n```"
            )
        else:
            content = json.dumps(
                _box(
                    target="wooden table",
                    present=True,
                    presence=0.88,
                    left=0.25,
                    top=0.25,
                    right=0.75,
                    bottom=0.75,
                    confidence=0.77,
                    reason="flat surface, no face",
                )
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with _client(handler) as client:
        moments = suggest_with_jev(
            video,
            n=5,
            out_dir=tmp_path / "out",
            brand=brand,
            client=client,
        )

    assert len(seen) == 2
    assert all(item["auth"] == f"Bearer {_KEY}" for item in seen)
    screen_text = seen[0]["body"]["messages"][0]["content"][0]["text"]
    box_text = seen[1]["body"]["messages"][0]["content"][0]["text"]
    assert "T06" in screen_text
    assert "Product category: Soda can" in screen_text
    assert "Avoid: beds" in box_text
    assert seen[0]["body"]["model"] == "typesafe/jev-router"
    assert seen[1]["body"]["response_format"]["type"] == "json_schema"

    assert len(moments) == 1
    moment = moments[0]
    assert moment.whats_there == "wooden table"
    assert moment.score == pytest.approx(0.77)
    assert moment.reason == "flat surface, no face"
    assert moment.start_s == pytest.approx(moment_window(5.0, duration, 5.0)[0])
    assert moment.end_s == pytest.approx(moment_window(5.0, duration, 5.0)[1])
    assert moment.start_s != pytest.approx(moment_window(0.0, duration, 5.0)[0])
    assert moment.region.as_dict() == fractions_to_bbox(
        0.25, 0.25, 0.75, 0.75, moment.frame_w, moment.frame_h
    ).as_dict()
    assert moment.frame_path.is_file()
    saved = json.loads((tmp_path / "out" / "moments.json").read_text())
    assert saved["moments"][0]["region"] == moment.region.as_dict()
    assert saved["brand_id"] == "b"
    assert (tmp_path / "out" / "contact_sheet.jpg").is_file()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _KEY not in captured.err


def _write_cut_mp4(path: Path) -> Path:
    """10s clip, cuts at 2s and 4s. In-shot motion is flat; a window across a cut is not."""
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


def test_jev_windows_stay_inside_the_shot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    video = _write_cut_mp4(tmp_path / "cuts.mp4")
    shot_channel = {0: 2, 1: 1, 2: 0}
    bounds = ((0.0, 2.0), (2.0, 4.0), (4.0, 10.0))

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["messages"][0]["content"][0]["text"]
        if "Valid tile ids" in text:
            content = json.dumps(
                {
                    "moments": [
                        {"tile": "T02", "score": 0.91, "reason": "clear table"},
                        {"tile": "T04", "score": 0.8, "reason": "clear shelf"},
                        {"tile": "T06", "score": 0.7, "reason": "clear desk"},
                    ]
                }
            )
        else:
            content = json.dumps(
                _box(
                    target="tabletop",
                    present=True,
                    presence=0.9,
                    left=0.2,
                    top=0.2,
                    right=0.6,
                    bottom=0.6,
                    confidence=0.7,
                    reason="flat surface",
                )
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with _client(handler) as client:
        moments = suggest_with_jev(video, n=5, out_dir=tmp_path / "out", client=client)

    assert len(moments) == 3
    seen = set()
    for moment in moments:
        owners = [
            i
            for i, (start, end) in enumerate(bounds)
            if moment.start_s >= start - 1e-3 and moment.end_s <= end + 1e-3
        ]
        assert owners, (moment.start_s, moment.end_s)
        assert not any(moment.start_s < cut < moment.end_s for cut in (2.0, 4.0))
        assert moment.end_s - moment.start_s <= 5.0 + 1e-3
        frame = cv2.imread(str(moment.frame_path))
        assert frame is not None
        assert _center_channel(frame) == shot_channel[owners[0]]
        seen.add(owners[0])
    assert seen == {0, 1, 2}
    cap = cv2.VideoCapture(str(video))
    try:
        inside = _read_frame_at(cap, 1.999, lo_s=0.0, hi_s=2.0)
    finally:
        cap.release()
    assert inside is not None
    assert _center_channel(inside) == shot_channel[0]
    captured = capsys.readouterr()
    assert _KEY not in captured.out
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
