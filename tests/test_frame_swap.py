"""Frame-based swap: ffmpeg extract timestamp + i2v/qwen payload shapes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from product_placement.crop_stitch import estimate_frame_swap_cost_usd
from product_placement.frame_swap import (
    clamp_i2v_duration,
    extract_frame_at,
    run_frame_swap,
)
from product_placement.higgsfield import (
    QWEN_IMAGE_EDIT,
    SEEDANCE_I2V,
    HiggsfieldClient,
    build_qwen_edit_body,
    build_seedance_i2v_body,
)


def _make_test_video(path: Path, *, duration_s: float = 3.0, fps: int = 24) -> Path:
    """Solid-color clip with a distinct mid-frame flash for timestamp checks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 0–1s red, 1–2s green flash, 2–3s blue — extract at t=1.5 → green.
    filter_complex = (
        "color=c=red:s=320x240:d=1[r];"
        "color=c=green:s=320x240:d=1[g];"
        "color=c=blue:s=320x240:d=1[b];"
        "[r][g][b]concat=n=3:v=1:a=0"
    )
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-filter_complex",
            filter_complex,
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-t",
            f"{duration_s:.3f}",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


def _make_product(path: Path) -> Path:
    import cv2
    import numpy as np

    img = np.zeros((64, 64, 4), dtype=np.uint8)
    img[:, :] = (0, 0, 255, 255)  # solid red BGRA
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)
    return path


def test_extract_frame_at_exact_timestamp(tmp_path: Path):
    video = _make_test_video(tmp_path / "clip.mp4")
    out = tmp_path / "frame.jpg"
    extract_frame_at(video, 1.5, out)
    assert out.is_file() and out.stat().st_size > 100

    import cv2
    import numpy as np

    frame = cv2.imread(str(out))
    assert frame is not None
    # Green flash around t=1.5 → G channel dominates.
    mean = frame.mean(axis=(0, 1))
    b, g, r = mean
    assert g > r + 20 and g > b + 20


def test_seedance_i2v_body_shape():
    body = build_seedance_i2v_body(
        prompt="keep camera",
        image_url="https://cdn.example.com/edited.jpg",
        duration=5,
        resolution="720p",
    )
    assert body == {
        "prompt": "keep camera",
        "image_url": "https://cdn.example.com/edited.jpg",
        "duration": 5,
        "resolution": "720p",
        "bitrate_mode": "high",
        "generate_audio": True,
    }
    assert "video_url" not in body
    assert "image_urls" not in body


def test_seedance_i2v_body_rejects_bad_duration():
    with pytest.raises(ValueError, match="duration"):
        build_seedance_i2v_body(
            prompt="p",
            image_url="https://cdn.example.com/x.jpg",
            duration=3,
        )


def test_qwen_edit_body_shape():
    body = build_qwen_edit_body(
        prompt="place product",
        image_urls=[
            "https://cdn.example.com/frame.jpg",
            "https://cdn.example.com/product.png",
        ],
        resolution="1k",
        aspect_ratio="16:9",
    )
    assert body == {
        "prompt": "place product",
        "image_urls": [
            "https://cdn.example.com/frame.jpg",
            "https://cdn.example.com/product.png",
        ],
        "resolution": "1k",
        "aspect_ratio": "16:9",
        "prompt_extend": False,
        "enable_thinking": False,
    }


def test_clamp_i2v_duration():
    assert clamp_i2v_duration(2.0) == 4
    assert clamp_i2v_duration(5.0) == 5
    assert clamp_i2v_duration(5.1) == 6
    assert clamp_i2v_duration(40.0) == 30


def test_estimate_five_second_frame_swap_cost():
    # Qwen 1k ($0.04) + Seedance i2v 720p 5s ($0.2647 * 5)
    assert estimate_frame_swap_cost_usd(5.0) == pytest.approx(1.3635, abs=1e-4)


def test_mock_frame_swap_extracts_and_writes_clip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MOCK_HIGGSFIELD", "1")
    video = _make_test_video(tmp_path / "src.mp4")
    product = _make_product(tmp_path / "product.png")
    out = tmp_path / "out.mp4"
    work = tmp_path / "work"
    meta = run_frame_swap(
        video_path=video,
        product_path=product,
        start_s=1.5,
        end_s=3.5,
        region={"x": 40, "y": 40, "w": 80, "h": 80},
        target="mug",
        out_path=out,
        work_dir=work,
        confirm=True,
        test_mode=True,
    )
    assert meta["mock"] is True
    assert meta["backend"] == "mock"
    assert Path(meta["frame_path"]).is_file()
    assert Path(meta["edited_frame_path"]).is_file()
    assert out.is_file() and out.stat().st_size > 500
    assert meta["billed_duration_s"] == 4.0  # clamped from 2s window
    assert meta["start_s"] == 1.5


def test_frame_swap_requires_confirm(tmp_path: Path):
    with pytest.raises(ValueError, match="confirm"):
        run_frame_swap(
            video_path=tmp_path / "missing.mp4",
            product_path=tmp_path / "p.png",
            start_s=0,
            end_s=5,
            region={"x": 0, "y": 0, "w": 10, "h": 10},
            target="x",
            out_path=tmp_path / "o.mp4",
            work_dir=tmp_path / "w",
            confirm=False,
            test_mode=True,
        )


def _transport(handler):
    return httpx.MockTransport(handler)


def test_paid_frame_swap_posts_qwen_then_seedance_i2v(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MOCK_HIGGSFIELD", raising=False)
    video = _make_test_video(tmp_path / "src.mp4")
    product = _make_product(tmp_path / "product.png")
    out = tmp_path / "out.mp4"
    work = tmp_path / "work"
    posts: list[tuple[str, dict]] = []
    upload_n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/generate-upload-url"):
            upload_n["n"] += 1
            n = upload_n["n"]
            ctype = json.loads(request.content.decode()).get("content_type", "bin")
            return httpx.Response(
                200,
                json={
                    "public_url": f"https://cdn.example.com/up-{n}",
                    "upload_url": f"https://storage.example.com/put-{n}",
                    "content_type": ctype,
                    "upload_headers": {"Content-Type": ctype},
                },
            )
        if str(request.url).startswith("https://storage.example.com/put"):
            return httpx.Response(200)
        if str(request.url) == QWEN_IMAGE_EDIT:
            body = json.loads(request.content.decode())
            posts.append(("qwen", body))
            # Uploads 1+2: source frame then product.
            assert body["image_urls"] == [
                "https://cdn.example.com/up-1",
                "https://cdn.example.com/up-2",
            ]
            assert body["resolution"] == "1k"
            assert body["prompt_extend"] is False
            return httpx.Response(
                200,
                json={
                    "status": "queued",
                    "request_id": "edit-1",
                    "status_url": "https://api.higgsfield.ai/requests/edit-1/status",
                },
            )
        if request.url.path.endswith("/requests/edit-1/status"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "request_id": "edit-1",
                    "images": [{"url": "https://cdn.example.com/edited.jpg"}],
                },
            )
        if str(request.url) == "https://cdn.example.com/edited.jpg":
            return httpx.Response(200, content=b"\xff\xd8\xff" + b"\x00" * 64)
        if str(request.url) == SEEDANCE_I2V:
            body = json.loads(request.content.decode())
            posts.append(("i2v", body))
            # Upload 3: edited frame re-uploaded for i2v.
            assert body["image_url"] == "https://cdn.example.com/up-3"
            assert body["duration"] == 4
            assert body["resolution"] == "720p"
            assert body["bitrate_mode"] == "high"
            assert "video_url" not in body
            return httpx.Response(
                200,
                json={
                    "status": "queued",
                    "request_id": "i2v-1",
                    "status_url": "https://api.higgsfield.ai/requests/i2v-1/status",
                },
            )
        if request.url.path.endswith("/requests/i2v-1/status"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "request_id": "i2v-1",
                    "video": {"url": "https://cdn.example.com/clip.mp4"},
                },
            )
        if str(request.url) == "https://cdn.example.com/clip.mp4":
            return httpx.Response(200, content=b"FAKEMP4DATA")
        return httpx.Response(404, text=str(request.url))

    client = HiggsfieldClient(
        client=httpx.Client(transport=_transport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=lambda _: None,
    )
    meta = run_frame_swap(
        video_path=video,
        product_path=product,
        start_s=1.0,
        end_s=3.0,
        region={"x": 10, "y": 10, "w": 40, "h": 40},
        target="bottle",
        out_path=out,
        work_dir=work,
        confirm=True,
        test_mode=False,
        client=client,
    )
    client.close()

    assert meta["mock"] is False
    assert meta["backend"] == "seedance-i2v"
    assert meta["request_id"] == "i2v-1"
    assert meta["edit_request_id"] == "edit-1"
    assert out.read_bytes() == b"FAKEMP4DATA"
    assert [name for name, _ in posts] == ["qwen", "i2v"]
    assert posts[1][1]["prompt"]  # non-empty motion prompt
