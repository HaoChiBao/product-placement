"""Mocked HTTP tests for the Higgsfield client (no live API)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from product_placement.higgsfield import (
    DEFAULT_POLL_TIMEOUT_S,
    GENJUTSU_SWAP,
    SEEDANCE_EDIT,
    UPLOAD_URL,
    HiggsfieldClient,
    HiggsfieldError,
    auth_configured,
    auth_header,
    build_genjutsu_body,
    build_seedance_body,
    default_swap_prompt,
)


def test_auth_header_from_split_env():
    headers = auth_header({"HF_API_KEY_ID": "id", "HF_API_KEY_SECRET": "sec"})
    assert headers == {"Authorization": "Key id:sec"}


def test_auth_header_from_hf_key():
    headers = auth_header({"HF_KEY": "abc:xyz"})
    assert headers == {"Authorization": "Key abc:xyz"}


def test_auth_header_missing():
    with pytest.raises(HiggsfieldError):
        auth_header({})


def test_auth_configured():
    assert auth_configured({"HF_KEY": "a:b"})
    assert auth_configured({"HF_API_KEY_ID": "id", "HF_API_KEY_SECRET": "sec"})
    assert not auth_configured({})
    assert not auth_configured({"HF_API_KEY_ID": "id"})


def test_seedance_body_matches_verified_live_shape():
    body = build_seedance_body(
        prompt="p",
        video_url="https://example.com/v.mp4",
        image_url="https://example.com/i.jpg",
        resolution="720p",
    )
    assert body == {
        "prompt": "p",
        "video_url": "https://example.com/v.mp4",
        "image_urls": ["https://example.com/i.jpg"],
        "resolution": "720p",
        "bitrate_mode": "high",
        "generate_audio": True,
    }
    assert "duration" not in body


def test_default_poll_timeout_is_at_least_20_minutes():
    assert DEFAULT_POLL_TIMEOUT_S >= 1200.0


def test_genjutsu_body_shape():
    body = build_genjutsu_body(
        prompt="p",
        video_url="https://example.com/v.mp4",
        image_url="https://example.com/i.png",
    )
    assert body["image_urls"] == ["https://example.com/i.png"]
    assert body["video_url"].startswith("https://")


def test_default_prompt_mentions_target():
    prompt = default_swap_prompt("metal mug")
    assert "metal mug" in prompt
    assert "@Video1" in prompt and "@Image1" in prompt


def _transport(handler):
    return httpx.MockTransport(handler)


def test_upload_put_has_no_authorization(tmp_path: Path):
    product = tmp_path / "soda.jpg"
    product.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/generate-upload-url"):
            body = json.loads(request.content.decode())
            assert body == {"content_type": "image/jpeg"}
            assert "Authorization" in request.headers
            return httpx.Response(
                200,
                json={
                    "public_url": "https://cdn.example.com/soda.jpg",
                    "upload_url": "https://storage.example.com/put",
                    "content_type": "image/jpeg",
                    "upload_headers": {
                        "Content-Type": "image/jpeg",
                        "x-amz-tagging": "retention=temporary",
                    },
                },
            )
        if str(request.url) == "https://storage.example.com/put":
            seen["put_headers"] = dict(request.headers)
            assert request.method == "PUT"
            assert "authorization" not in {k.lower() for k in request.headers.keys()}
            return httpx.Response(200)
        return httpx.Response(404, text=str(request.url))

    with HiggsfieldClient(
        client=httpx.Client(transport=_transport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=lambda _: None,
    ) as hf:
        assert hf.upload_file(product) == "https://cdn.example.com/soda.jpg"
    assert "put_headers" in seen


def test_upload_submit_poll_download(tmp_path: Path):
    calls: list[str] = []
    product = tmp_path / "soda.png"
    product.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    out = tmp_path / "out.mp4"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/files/generate-upload-url"):
            return httpx.Response(
                200,
                json={
                    "public_url": "https://cdn.example.com/soda.png",
                    "upload_url": "https://storage.example.com/put",
                    "content_type": "image/png",
                    "upload_headers": {
                        "Content-Type": "image/png",
                        "x-amz-tagging": "retention=temporary",
                    },
                },
            )
        if str(request.url) == "https://storage.example.com/put":
            assert "authorization" not in {k.lower() for k in request.headers.keys()}
            return httpx.Response(200)
        if str(request.url) == SEEDANCE_EDIT:
            assert request.headers["Authorization"] == "Key id:secret"
            assert "application/json" in request.headers["Content-Type"]
            body = json.loads(request.content.decode())
            assert body["bitrate_mode"] == "high"
            assert body["generate_audio"] is True
            assert "duration" not in body
            assert body["image_urls"] == ["https://cdn.example.com/soda.png"]
            return httpx.Response(
                200,
                json={
                    "status": "queued",
                    "request_id": "req-1",
                    "status_url": "https://api.higgsfield.ai/requests/req-1/status",
                    "cancel_url": "https://api.higgsfield.ai/requests/req-1/cancel",
                    "expires_after": 604800,
                },
            )
        if request.url.path.endswith("/requests/req-1/status"):
            if calls.count("GET /requests/req-1/status") == 1:
                return httpx.Response(
                    200,
                    json={
                        "status": "in_progress",
                        "request_id": "req-1",
                        "status_url": "https://api.higgsfield.ai/requests/req-1/status",
                        "cancel_url": "https://api.higgsfield.ai/requests/req-1/cancel",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "request_id": "req-1",
                    "status_url": "https://api.higgsfield.ai/requests/req-1/status",
                    "cancel_url": "https://api.higgsfield.ai/requests/req-1/cancel",
                    "expires_at": "2026-11-28T00:00:00Z",
                    "video": {"url": "https://cdn.example.com/out.mp4"},
                },
            )
        if str(request.url) == "https://cdn.example.com/out.mp4":
            return httpx.Response(200, content=b"FAKEMP4")
        return httpx.Response(404, text=f"unexpected {request.url}")

    sleeps: list[float] = []
    with HiggsfieldClient(
        client=httpx.Client(transport=_transport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=sleeps.append,
    ) as hf:
        public = hf.upload_file(product)
        assert public == "https://cdn.example.com/soda.png"
        result = hf.run_seedance(
            prompt="swap",
            video_url="https://cdn.example.com/clip.mp4",
            image_url=public,
            poll_timeout_s=30,
            resolution="720p",
        )
        assert result.backend == "seedance"
        assert result.video_url.endswith("out.mp4")
        assert "cost" not in result.raw
        hf.download(result.video_url, out)

    assert out.read_bytes() == b"FAKEMP4"
    assert sleeps


def test_poll_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200,
                json={"status": "failed", "request_id": "x", "error": "boom"},
            )
        return httpx.Response(404)

    with HiggsfieldClient(
        client=httpx.Client(transport=_transport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=lambda _: None,
    ) as hf:
        done = hf.poll("https://api.higgsfield.ai/requests/x/status")
        with pytest.raises(HiggsfieldError):
            hf._as_result(done, backend="seedance")


def test_download_error_raises_higgsfield_error(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"content-type": "text/plain"})

    with HiggsfieldClient(
        client=httpx.Client(transport=_transport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=lambda _: None,
    ) as hf:
        with pytest.raises(HiggsfieldError, match=r"download failed \(403\)"):
            hf.download("https://cdn.example.com/missing.mp4", tmp_path / "x.mp4")


def test_auto_fallback_to_genjutsu_client_level():
    state = {"seedance": 0, "genjutsu": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SEEDANCE_EDIT:
            state["seedance"] += 1
            return httpx.Response(503, text="unavailable")
        if str(request.url) == GENJUTSU_SWAP:
            state["genjutsu"] += 1
            return httpx.Response(
                200,
                json={
                    "status": "queued",
                    "request_id": "g1",
                    "status_url": "https://api.higgsfield.ai/requests/g1/status",
                    "cancel_url": "https://api.higgsfield.ai/requests/g1/cancel",
                },
            )
        if request.url.path.endswith("/requests/g1/status"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "request_id": "g1",
                    "video": {"url": "https://cdn.example.com/out.mp4"},
                },
            )
        return httpx.Response(404, text=str(request.url))

    with HiggsfieldClient(
        client=httpx.Client(transport=_transport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=lambda _: None,
    ) as hf:
        with pytest.raises(HiggsfieldError):
            hf.run_seedance(
                prompt="p",
                video_url="https://cdn.example.com/clip.mp4",
                image_url="https://cdn.example.com/soda.png",
            )
        result = hf.run_genjutsu(
            prompt="p",
            video_url="https://cdn.example.com/clip.mp4",
            image_url="https://cdn.example.com/soda.png",
        )
        assert result.backend == "genjutsu"
        assert state["genjutsu"] == 1
