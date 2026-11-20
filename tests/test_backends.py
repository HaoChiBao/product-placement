"""Mocked routing + schema tests for pluggable edit backends."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from product_placement.backends import (
    MODELS,
    build_wan27_input,
    build_wan_vace_body,
    modal_vace_hw,
    wan27_edit,
    wan_vace_edit,
    wan_vace_modal_edit,
)
from product_placement.pipeline import swap


def test_models_registry_has_four():
    assert set(MODELS) == {"seedance", "wan27", "wan-vace", "wan-vace-modal"}


def test_wan27_input_schema():
    body = build_wan27_input(
        video_url="https://r/v.mp4",
        prompt="p",
        reference_image_url="https://r/i.png",
        resolution="720p",
    )
    assert body == {
        "video": "https://r/v.mp4",
        "prompt": "p",
        "reference_image": "https://r/i.png",
        "resolution": "720p",
        "audio_setting": "origin",
    }
    assert "disable_safety_checker" not in body
    with pytest.raises(ValueError, match="wan27 resolution"):
        build_wan27_input(
            video_url="u",
            prompt="p",
            reference_image_url="i",
            resolution="480p",
        )


def test_wan_vace_body_schema():
    body = build_wan_vace_body(
        prompt="p",
        video_url="https://f/v.mp4",
        image_url="https://f/i.png",
        resolution="720p",
    )
    assert body["enable_safety_checker"] is False
    assert body["image_urls"] == ["https://f/i.png"]
    assert body["video_url"] == "https://f/v.mp4"
    with pytest.raises(ValueError, match="wan-vace resolution"):
        build_wan_vace_body(
            prompt="p",
            video_url="u",
            image_url="i",
            resolution="1080p",
        )


def test_modal_vace_hw_respects_orientation():
    assert modal_vace_hw("720p", portrait=False) == (720, 1280)
    assert modal_vace_hw("720p", portrait=True) == (1280, 720)
    assert modal_vace_hw("480p", portrait=True) == (832, 480)
    with pytest.raises(ValueError, match="480p|720p"):
        modal_vace_hw("1080p", portrait=False)


def test_modal_orient_hw_helper():
    # Import by path: modal_app is not part of the installed package.
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "modal_app" / "wan_vace.py"
    spec = importlib.util.spec_from_file_location("wan_vace_modal_mod", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert mod._orient_hw(720, 1280, portrait=True) == (1280, 720)
    assert mod._orient_hw(1280, 720, portrait=False) == (720, 1280)
    assert mod._orient_hw(480, 832, portrait=True) == (832, 480)


def test_pipeline_routes_to_named_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    called = {}

    def fake_edit(clip, product, prompt, *, out_path, work_dir, resolution="720p", **_):
        called["model_args"] = (Path(clip).name, Path(product).name, prompt, resolution)
        Path(out_path).write_bytes(b"routed")
        return Path(out_path)

    monkeypatch.setitem(MODELS, "wan27", fake_edit)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    product = tmp_path / "p.png"
    product.write_bytes(b"i")
    out = tmp_path / "o.mp4"
    result = swap(
        clip=clip,
        target="mug",
        product_image=product,
        out=out,
        model="wan27",
        work_dir=tmp_path / "w",
    )
    assert result.model == "wan27"
    assert out.read_bytes() == b"routed"
    assert called["model_args"][0] == "c.mp4"


def test_wan27_edit_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "tok")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"video")
    product = tmp_path / "p.png"
    product.write_bytes(b"img")
    out = tmp_path / "o.mp4"
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files"):
            name = "v.mp4" if "video" in (request.headers.get("content-type") or "") else "i.png"
            # multipart — just return a get url
            return httpx.Response(
                201,
                json={"urls": {"get": f"https://replicate.delivery/{name}"}},
            )
        if request.url.path.endswith("/predictions"):
            return httpx.Response(
                201,
                json={
                    "id": "p1",
                    "status": "starting",
                    "urls": {"get": "https://api.replicate.com/v1/predictions/p1"},
                },
            )
        if request.url.path.endswith("/predictions/p1"):
            state["n"] += 1
            if state["n"] < 2:
                return httpx.Response(200, json={"status": "processing"})
            return httpx.Response(
                200,
                json={"status": "succeeded", "output": "https://cdn.example/out.mp4"},
            )
        if str(request.url) == "https://cdn.example/out.mp4":
            return httpx.Response(200, content=b"EDITED")
        return httpx.Response(404, text=str(request.url))

    def fake_mux(edited, original, out_path):
        Path(out_path).write_bytes(Path(edited).read_bytes())
        return Path(out_path)

    monkeypatch.setattr("product_placement.backends.mux_original_audio", fake_mux)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = wan27_edit(
        clip,
        product,
        "prompt",
        out_path=out,
        work_dir=tmp_path / "w",
        client=client,
        sleep=lambda _: None,
    )
    assert path.read_bytes() == b"EDITED"


def test_wan_vace_edit_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAL_KEY", "fal-key")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"video")
    product = tmp_path / "p.png"
    product.write_bytes(b"img")
    out = tmp_path / "o.mp4"
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "upload/initiate" in str(request.url):
            body = json.loads(request.content.decode())
            name = body["file_name"]
            return httpx.Response(
                200,
                json={
                    "upload_url": f"https://upload.fal/{name}",
                    "file_url": f"https://v3.fal.media/{name}",
                },
            )
        if request.url.host == "upload.fal":
            return httpx.Response(200)
        if "wan-vace-apps/video-edit" in str(request.url) and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "status_url": "https://queue.fal.run/status/s1",
                    "response_url": "https://queue.fal.run/result/s1",
                },
            )
        if str(request.url) == "https://queue.fal.run/status/s1":
            state["n"] += 1
            status = "IN_PROGRESS" if state["n"] < 2 else "COMPLETED"
            return httpx.Response(200, json={"status": status})
        if str(request.url) == "https://queue.fal.run/result/s1":
            return httpx.Response(
                200, json={"video": {"url": "https://cdn.fal/out.mp4"}}
            )
        if str(request.url) == "https://cdn.fal/out.mp4":
            return httpx.Response(200, content=b"VACE")
        return httpx.Response(404, text=str(request.url))

    def fake_mux(edited, original, out_path):
        Path(out_path).write_bytes(Path(edited).read_bytes())
        return Path(out_path)

    monkeypatch.setattr("product_placement.backends.mux_original_audio", fake_mux)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = wan_vace_edit(
        clip,
        product,
        "prompt",
        out_path=out,
        work_dir=tmp_path / "w",
        client=client,
        sleep=lambda _: None,
    )
    assert path.read_bytes() == b"VACE"


def test_wan_vace_modal_requires_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    product = tmp_path / "p.png"
    product.write_bytes(b"i")
    with pytest.raises(RuntimeError, match="MODAL_TOKEN_ID"):
        wan_vace_modal_edit(
            clip,
            product,
            "p",
            out_path=tmp_path / "o.mp4",
            work_dir=tmp_path / "w",
            remote=lambda *a, **k: b"x",
        )


def test_wan_vace_modal_routes_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "mid")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "msec")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"VIDEO")
    product = tmp_path / "p.png"
    product.write_bytes(b"IMAGE")
    out = tmp_path / "o.mp4"
    seen = {}

    def fake_remote(video_bytes, image_bytes, prompt, *, height, width):
        seen["video"] = video_bytes
        seen["image"] = image_bytes
        seen["prompt"] = prompt
        seen["size"] = (height, width)
        return b"MODAL-MP4"

    def fake_mux(edited, original, out_path):
        Path(out_path).write_bytes(Path(edited).read_bytes())
        return Path(out_path)

    monkeypatch.setattr("product_placement.backends.mux_original_audio", fake_mux)
    monkeypatch.setattr("product_placement.backends.probe_portrait", lambda _: True)
    path = wan_vace_modal_edit(
        clip,
        product,
        "swap mug",
        out_path=out,
        work_dir=tmp_path / "w",
        remote=fake_remote,
    )
    assert path.read_bytes() == b"MODAL-MP4"
    assert seen["video"] == b"VIDEO"
    assert seen["image"] == b"IMAGE"
    assert seen["prompt"] == "swap mug"
    assert seen["size"] == (1280, 720)


def test_pipeline_routes_wan_vace_modal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "mid")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "msec")

    def fake_edit(clip, product, prompt, *, out_path, work_dir, resolution="720p", **_):
        Path(out_path).write_bytes(b"modal-routed")
        return Path(out_path)

    monkeypatch.setitem(MODELS, "wan-vace-modal", fake_edit)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    product = tmp_path / "p.png"
    product.write_bytes(b"i")
    out = tmp_path / "o.mp4"
    result = swap(
        clip=clip,
        target="mug",
        product_image=product,
        out=out,
        model="wan-vace-modal",
        work_dir=tmp_path / "w",
    )
    assert result.model == "wan-vace-modal"
    assert out.read_bytes() == b"modal-routed"
