"""Pipeline auto-fallback with mocked Higgsfield + stubbed prepare."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import product_placement.backends as backends
from product_placement.higgsfield import GENJUTSU_SWAP, SEEDANCE_EDIT, HiggsfieldClient
from product_placement.pipeline import swap


def test_auto_falls_back_when_seedance_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    product = tmp_path / "soda.png"
    product.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")
    out = tmp_path / "out.mp4"

    def fake_prepare(src, dst):
        path = Path(dst)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"prepared")
        return path

    monkeypatch.setattr(backends, "prepare_for_genjutsu", fake_prepare, raising=False)
    monkeypatch.setattr(
        "product_placement.prepare.prepare_for_genjutsu", fake_prepare
    )

    def fake_mux(edited, original, out_path):
        Path(out_path).write_bytes(Path(edited).read_bytes())
        return Path(out_path)

    monkeypatch.setattr(backends, "mux_original_audio", fake_mux)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/generate-upload-url"):
            body = json.loads(request.content.decode())
            ctype = body["content_type"]
            key = "video.mp4" if ctype.startswith("video/") else "image.png"
            return httpx.Response(
                200,
                json={
                    "public_url": f"https://cdn.example.com/{key}",
                    "upload_url": f"https://storage.example.com/{key}",
                    "content_type": ctype,
                    "upload_headers": {"Content-Type": ctype},
                },
            )
        if request.url.host == "storage.example.com":
            return httpx.Response(200)
        if str(request.url) == SEEDANCE_EDIT:
            return httpx.Response(500, text="seedance down")
        if str(request.url) == GENJUTSU_SWAP:
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
        if str(request.url) == "https://cdn.example.com/out.mp4":
            return httpx.Response(200, content=b"OK")
        return httpx.Response(404, text=str(request.url))

    client = HiggsfieldClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        env={"HF_KEY": "id:secret"},
        sleep=lambda _: None,
    )
    result = swap(
        clip=clip,
        target="metal mug",
        product_image=product,
        out=out,
        model="seedance",
        backend="auto",
        work_dir=tmp_path / "work",
        client=client,
    )
    assert result.model == "seedance"
    assert result.backend == "genjutsu"
    assert result.request_id == "g1"
    assert out.read_bytes() == b"OK"
