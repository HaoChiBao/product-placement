"""Request building and OpenRouter video job polling. HTTP is mocked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from product_placement.openrouter_wan import (
    build_swap_prompt,
    build_wan_video_request,
    choose_duration,
    clamp_window,
    content_download_url,
    estimate_wan_cost_usd,
    nearest_aspect,
    poll_video_job,
    resolve_swap_method,
    run_wan_swap,
)


def test_swap_prompt_keeps_placement():
    text = build_swap_prompt("metal mug", "Soda Can")
    assert "Replace the metal mug with the Soda Can in place." in text
    assert "same position, size and hand placement as the original object" in text
    assert "faces, motion, cuts, and framing unchanged" in text


def test_request_body_matches_proven_shape():
    body = build_wan_video_request(
        prompt="replace it",
        video_url="https://s3.example/clip.mp4?sig=1",
        image_url="https://s3.example/can.png?sig=2",
        duration=10,
        seed=7,
    )
    assert body == {
        "model": "alibaba/wan-3.0",
        "prompt": "replace it",
        "input_references": [
            {
                "type": "video_url",
                "video_url": {"url": "https://s3.example/clip.mp4?sig=1"},
            },
            {
                "type": "image_url",
                "image_url": {"url": "https://s3.example/can.png?sig=2"},
            },
        ],
        "duration": 10,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "generate_audio": False,
        "seed": 7,
    }


def test_request_rejects_non_https():
    with pytest.raises(ValueError, match="https"):
        build_wan_video_request(
            prompt="p",
            video_url="data:video/mp4;base64,AAAA",
            duration=4,
            seed=1,
        )
    with pytest.raises(ValueError, match="https"):
        build_wan_video_request(
            prompt="p",
            video_url="https://s3.example/clip.mp4",
            image_url="http://s3.example/can.png",
            duration=4,
            seed=1,
        )


def test_clamp_window_centers_on_moment():
    assert clamp_window(0, 4, 30) == (0, 4)
    start, end = clamp_window(10, 50, 10)
    assert start == pytest.approx(25)
    assert end == pytest.approx(35)
    assert end - start == pytest.approx(10)


def test_choose_duration_prefers_longer_tie():
    assert choose_duration(5, [4, 6, 8]) == 6
    assert choose_duration(10.2, [2, 10, 15]) == 10
    assert choose_duration(3, None) == 3


def test_nearest_aspect():
    assert nearest_aspect(1920, 1080, ["16:9", "9:16", "1:1"]) == "16:9"
    assert nearest_aspect(1080, 1920, ["16:9", "9:16"]) == "9:16"
    assert nearest_aspect(0, 0) == "16:9"


def test_estimate_matches_measured_ten_second_price():
    assert estimate_wan_cost_usd(10) == pytest.approx(1.2)
    assert estimate_wan_cost_usd(5) == pytest.approx(0.6)


def test_relative_unsigned_url_is_absolute():
    url = content_download_url(
        {"unsigned_urls": ["/api/v1/videos/vid-1/content?index=0"]},
        "vid-1",
    )
    assert url == "https://openrouter.ai/api/v1/videos/vid-1/content?index=0"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_poll_pending_then_completed_records_cost():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        assert request.method == "GET"
        assert request.url.path == "/api/v1/videos/vid-1"
        if len(seen) == 1:
            return httpx.Response(200, json={"id": "vid-1", "status": "pending"})
        return httpx.Response(
            200,
            json={
                "id": "vid-1",
                "status": "completed",
                "unsigned_urls": ["/api/v1/videos/vid-1/content?index=0"],
                "usage": {"cost": 1.2, "is_byok": False},
            },
        )

    payload = poll_video_job(
        _client(handler),
        "vid-1",
        key="test-key",
        sleep=lambda _s: None,
        clock=lambda: 0.0,
        timeout_s=100,
    )
    assert payload["status"] == "completed"
    assert payload["usage"]["cost"] == 1.2
    assert seen == ["Bearer test-key", "Bearer test-key"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired"])
def test_poll_stops_on_terminal_failure(status: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "vid-1", "status": status, "error": "provider stopped"},
        )

    with pytest.raises(RuntimeError, match=status):
        poll_video_job(
            _client(handler),
            "vid-1",
            key="test-key",
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )


def test_poll_timeout():
    ticks = {"t": 0.0}

    def clock() -> float:
        return ticks["t"]

    def sleep(seconds: float) -> None:
        ticks["t"] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "vid-1", "status": "in_progress"})

    with pytest.raises(TimeoutError, match="timed out"):
        poll_video_job(
            _client(handler),
            "vid-1",
            key="test-key",
            timeout_s=20,
            interval_s=15,
            sleep=sleep,
            clock=clock,
        )


def test_submit_error_redacts_key():
    secret = "sk-or-v1-test-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad key {secret}")

    from product_placement.openrouter_wan import submit_video

    with pytest.raises(RuntimeError, match="400") as exc:
        submit_video(
            _client(handler),
            {"model": "alibaba/wan-3.0"},
            key=secret,
        )
    assert secret not in str(exc.value)
    assert "[redacted]" in str(exc.value)


class _Store:
    def __init__(self, *, https: bool = True) -> None:
        self.objects: dict[str, bytes] = {}
        self.https = https
        self.deleted: list[str] = []

    def put_file(self, local_path, key, content_type=None):
        self.objects[key] = Path(local_path).read_bytes()
        return key

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def public_or_presigned_url(self, key, expires=3600):
        if not self.https:
            return "/api/media/" + key
        return f"https://s3.example/{key}?exp={expires}"


def _tiny_mp4(path: Path, *, duration_s: float = 2) -> Path:
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:s=320x240:d={duration_s}",
            "-r",
            "8",
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


def test_run_wan_swap_posts_polls_and_stores_cost(tmp_path: Path):
    source = _tiny_mp4(tmp_path / "in.mp4", duration_s=40)
    product = tmp_path / "can.png"
    product.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    mp4 = source.read_bytes()
    store = _Store()
    polls = {"n": 0}
    posted: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer test-key"
        path = request.url.path
        if path.endswith("/videos/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "alibaba/wan-3.0",
                            "supported_durations": [2, 5, 10],
                            "supported_aspect_ratios": ["16:9", "4:3", "9:16"],
                            "supported_resolutions": ["720p"],
                        }
                    ]
                },
            )
        if request.method == "POST" and path.endswith("/videos"):
            posted["body"] = json.loads(request.content.decode())
            return httpx.Response(202, json={"id": "vid-9", "status": "pending"})
        if path.endswith("/videos/vid-9"):
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json={"id": "vid-9", "status": "in_progress"})
            return httpx.Response(
                200,
                json={
                    "id": "vid-9",
                    "status": "completed",
                    "unsigned_urls": ["/api/v1/videos/vid-9/content?index=0"],
                    "usage": {"cost": 1.2},
                },
            )
        if path.endswith("/videos/vid-9/content"):
            return httpx.Response(200, content=mp4)
        return httpx.Response(404, text=str(request.url))

    out = tmp_path / "out.mp4"
    meta = run_wan_swap(
        video_path=source,
        product_path=product,
        start_s=0,
        end_s=40,
        target="metal mug",
        product_name="Soda Can",
        out_path=out,
        work_dir=tmp_path / "work",
        seed=11,
        store=store,
        client=_client(handler),
        env={"OPENROUTER_API_KEY": "test-key"},
        sleep=lambda _s: None,
        clock=lambda: 0.0,
        poll_timeout_s=100,
    )
    body = posted["body"]
    assert body["model"] == "alibaba/wan-3.0"
    assert body["duration"] == 10
    assert body["resolution"] == "720p"
    assert body["generate_audio"] is False
    assert body["seed"] == 11
    assert body["aspect_ratio"] == "4:3"
    assert body["input_references"][0]["type"] == "video_url"
    assert body["input_references"][0]["video_url"]["url"].startswith("https://")
    assert body["input_references"][1]["type"] == "image_url"
    assert "same position, size and hand placement" in body["prompt"]
    assert "Soda Can" in body["prompt"]
    assert meta["backend"] == "wan-3.0"
    assert meta["request_id"] == "vid-9"
    assert meta["cost_usd"] == 1.2
    assert meta["start_s"] == pytest.approx(15)
    assert meta["end_s"] == pytest.approx(25)
    assert out.is_file() and out.stat().st_size > 100
    assert store.objects == {}
    assert len(store.deleted) == 2


def test_run_wan_swap_requires_https_presign(tmp_path: Path):
    source = _tiny_mp4(tmp_path / "in.mp4")
    product = tmp_path / "can.png"
    product.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method + " " + request.url.path)
        if request.url.path.endswith("/videos/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "alibaba/wan-3.0", "supported_durations": [10]}]},
            )
        return httpx.Response(500, text="should not submit")

    with pytest.raises(RuntimeError, match="presigned https"):
        run_wan_swap(
            video_path=source,
            product_path=product,
            start_s=0,
            end_s=2,
            target="mug",
            out_path=tmp_path / "out.mp4",
            work_dir=tmp_path / "work",
            store=_Store(https=False),
            client=_client(handler),
            env={"OPENROUTER_API_KEY": "test-key"},
            sleep=lambda _s: None,
        )
    assert not any(call.startswith("POST") for call in calls)


def test_default_method_is_wan(monkeypatch):
    monkeypatch.delenv("SWAP_METHOD", raising=False)
    assert resolve_swap_method(None) == "wan"
    assert resolve_swap_method("higgsfield") == "higgsfield"
    assert resolve_swap_method("seedance") == "higgsfield"
    monkeypatch.setenv("SWAP_METHOD", "frame")
    assert resolve_swap_method(None) == "higgsfield"


def test_swap_job_defaults_to_wan_and_records_cost(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MOCK_HIGGSFIELD", raising=False)
    monkeypatch.delenv("SWAP_METHOD", raising=False)
    monkeypatch.setattr("product_placement.jobs.data_root", lambda: tmp_path)

    def fake_wan(**kwargs):
        Path(kwargs["out_path"]).write_bytes(b"result-mp4")
        return {
            "backend": "wan-3.0",
            "request_id": "vid-1",
            "cost_usd": 1.2,
            "billed_duration_s": 10,
            "video_url": "",
            "frame_path": "",
            "edited_frame_path": "",
            "start_s": 0,
            "end_s": 10,
        }

    monkeypatch.setattr("product_placement.openrouter_wan.run_wan_swap", fake_wan)
    from product_placement.jobs import run_swap_job

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    product = tmp_path / "p.png"
    product.write_bytes(b"p")
    status = run_swap_job(
        "job1",
        video_path=video,
        product_path=product,
        start_s=0,
        end_s=10,
        region={"x": 1, "y": 2, "w": 3, "h": 4},
        target="mug",
        confirm=True,
    )
    assert status["status"] == "completed"
    assert status["result"]["backend"] == "wan-3.0"
    assert status["result"]["cost_usd"] == 1.2
    saved = json.loads((tmp_path / "jobs" / "job1" / "swap.json").read_text())
    assert saved["cost_usd"] == 1.2
    assert saved["method"] == "wan"


def test_mock_higgsfield_still_uses_frame_swap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MOCK_HIGGSFIELD", "1")
    monkeypatch.delenv("SWAP_METHOD", raising=False)
    monkeypatch.setattr("product_placement.jobs.data_root", lambda: tmp_path)
    called = {"wan": 0, "frame": 0}

    def fake_extract(video, start, end, out_path, max_s=None):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"clip")
        return Path(out_path)

    def fake_frame(**kwargs):
        called["frame"] += 1
        Path(kwargs["out_path"]).write_bytes(b"frame-mp4")
        return {
            "backend": "mock",
            "request_id": "mock",
            "billed_duration_s": 4,
            "frame_path": "",
            "edited_frame_path": "",
        }

    def fake_wan(**kwargs):
        called["wan"] += 1
        return {}

    monkeypatch.setattr("product_placement.crop_stitch.extract_moment_clip", fake_extract)
    monkeypatch.setattr("product_placement.frame_swap.run_frame_swap", fake_frame)
    monkeypatch.setattr("product_placement.openrouter_wan.run_wan_swap", fake_wan)
    from product_placement.jobs import run_swap_job

    run_swap_job(
        "job2",
        video_path=tmp_path / "v.mp4",
        product_path=tmp_path / "p.png",
        start_s=0,
        end_s=4,
        region={"x": 0, "y": 0, "w": 1, "h": 1},
        target="cup",
        confirm=True,
        method="wan",
    )
    assert called == {"wan": 0, "frame": 1}
