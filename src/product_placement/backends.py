"""Pluggable edit backends: one function each, selected by name.

Shared shape: (clip, product, prompt, *, out_path, work_dir, ...) -> Path
"""

from __future__ import annotations

import mimetypes
import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from product_placement.audio import mux_original_audio
from product_placement.higgsfield import (
    DEFAULT_POLL_TIMEOUT_S,
    HiggsfieldClient,
    HiggsfieldError,
)


@dataclass
class EditResult:
    """Path plus optional identity for backends that report a paid sub-route."""

    path: Path
    backend: str = ""
    request_id: str = ""
    video_url: str = ""

REPLICATE_API = "https://api.replicate.com/v1"
REPLICATE_MODEL = "wan-video/wan-2.7-videoedit"
FAL_QUEUE = "https://queue.fal.run/fal-ai/wan-vace-apps/video-edit"
FAL_UPLOAD_INIT = (
    "https://rest.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3"
)


def _content_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    if path.suffix.lower() == ".png":
        return "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return guessed or "application/octet-stream"


def seedance_edit(
    clip: Path,
    product: Path,
    prompt: str,
    *,
    out_path: Path,
    work_dir: Path,
    resolution: str = "720p",
    backend: str = "auto",
    poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
    client: HiggsfieldClient | None = None,
) -> EditResult:
    """Existing Higgsfield Seedance 2.5 path (optional Genjutsu fallback)."""
    from product_placement.prepare import prepare_for_genjutsu

    if backend not in {"auto", "seedance", "genjutsu"}:
        raise ValueError("backend must be auto, seedance, or genjutsu")

    work_dir.mkdir(parents=True, exist_ok=True)
    owns = client is None
    hf = client or HiggsfieldClient()
    raw_out = work_dir / "seedance_raw.mp4"
    try:
        product_url = hf.upload_file(product)

        def _seedance(video_path: Path):
            video_url = hf.upload_file(video_path)
            return hf.run_seedance(
                prompt=prompt,
                video_url=video_url,
                image_url=product_url,
                resolution=resolution,
                poll_timeout_s=poll_timeout_s,
            )

        def _genjutsu(video_path: Path):
            prepared = prepare_for_genjutsu(video_path, work_dir / "genjutsu_input.mp4")
            video_url = hf.upload_file(prepared)
            return hf.run_genjutsu(
                prompt=prompt,
                video_url=video_url,
                image_url=product_url,
                resolution=resolution,
                poll_timeout_s=poll_timeout_s,
            )

        if backend == "seedance":
            result = _seedance(clip)
        elif backend == "genjutsu":
            result = _genjutsu(clip)
        else:
            try:
                result = _seedance(clip)
            except HiggsfieldError as exc:
                print(f"Seedance failed ({exc}); falling back to Genjutsu")
                result = _genjutsu(clip)

        hf.download(result.video_url, raw_out)
        path = mux_original_audio(raw_out, clip, out_path)
        return EditResult(
            path=path,
            backend=result.backend,
            request_id=result.request_id,
            video_url=result.video_url,
        )
    finally:
        if owns:
            hf.close()


def _replicate_token() -> str:
    token = (os.environ.get("REPLICATE_API_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Set REPLICATE_API_TOKEN")
    return token


def _replicate_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def replicate_upload(client: httpx.Client, path: Path, token: str) -> str:
    """Upload via Replicate Files API; return urls.get."""
    with path.open("rb") as fh:
        response = client.post(
            f"{REPLICATE_API}/files",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "content": (path.name, fh, _content_type(path)),
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Replicate upload failed ({response.status_code}): {response.text}"
        )
    data = response.json()
    url = (data.get("urls") or {}).get("get")
    if not url:
        raise RuntimeError(f"Replicate upload missing urls.get: {data}")
    return url


def build_wan27_input(
    *,
    video_url: str,
    prompt: str,
    reference_image_url: str,
    resolution: str = "720p",
) -> dict:
    """Schema from https://replicate.com/wan-video/wan-2.7-videoedit/api

    Fields: video, prompt, reference_image, resolution, aspect_ratio,
    duration, audio_setting, seed. No disable_safety_checker in the schema.
    """
    allowed = {"720p", "1080p"}
    if resolution not in allowed:
        raise ValueError(
            f"wan27 resolution must be one of {sorted(allowed)}, got {resolution!r}"
        )
    return {
        "video": video_url,
        "prompt": prompt,
        "reference_image": reference_image_url,
        "resolution": resolution,
        "audio_setting": "origin",
    }


def wan27_edit(
    clip: Path,
    product: Path,
    prompt: str,
    *,
    out_path: Path,
    work_dir: Path,
    resolution: str = "720p",
    poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    # Validate before any paid upload.
    build_wan27_input(
        video_url="https://example.invalid/v",
        prompt=prompt,
        reference_image_url="https://example.invalid/i",
        resolution=resolution,
    )
    token = _replicate_token()
    work_dir.mkdir(parents=True, exist_ok=True)
    owns = client is None
    http = client or httpx.Client(timeout=120.0)
    raw_out = work_dir / "wan27_raw.mp4"
    try:
        video_url = replicate_upload(http, clip, token)
        image_url = replicate_upload(http, product, token)
        body = {
            "input": build_wan27_input(
                video_url=video_url,
                prompt=prompt,
                reference_image_url=image_url,
                resolution=resolution,
            )
        }
        create = http.post(
            f"{REPLICATE_API}/models/{REPLICATE_MODEL}/predictions",
            headers=_replicate_headers(token),
            json=body,
        )
        if create.status_code >= 400:
            raise RuntimeError(
                f"Replicate create failed ({create.status_code}): {create.text}"
            )
        prediction = create.json()
        get_url = (prediction.get("urls") or {}).get("get") or (
            f"{REPLICATE_API}/predictions/{prediction['id']}"
        )

        deadline = time.monotonic() + poll_timeout_s
        delay = 2.0
        while time.monotonic() < deadline:
            status_resp = http.get(
                get_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            if status_resp.status_code >= 400:
                raise RuntimeError(
                    f"Replicate poll failed ({status_resp.status_code}): {status_resp.text}"
                )
            prediction = status_resp.json()
            status = prediction.get("status")
            if status == "succeeded":
                output = prediction.get("output")
                if isinstance(output, list):
                    output_url = output[0] if output else None
                elif isinstance(output, dict):
                    output_url = output.get("url") or output.get("video")
                else:
                    output_url = output
                if not output_url:
                    raise RuntimeError(f"Replicate succeeded without output: {prediction}")
                break
            if status in {"failed", "canceled"}:
                raise RuntimeError(f"Replicate ended status={status!r}: {prediction}")
            sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 1.5, 10.0)
        else:
            raise RuntimeError(f"Replicate timed out: {prediction}")

        with http.stream(
            "GET",
            output_url,
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"Replicate download failed ({response.status_code})")
            with raw_out.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
        return mux_original_audio(raw_out, clip, out_path)
    finally:
        if owns:
            http.close()


def _fal_key() -> str:
    key = (os.environ.get("FAL_KEY") or "").strip()
    if not key:
        raise RuntimeError("Set FAL_KEY")
    return key


def fal_upload(client: httpx.Client, path: Path, key: str) -> str:
    """Upload via fal storage initiate + PUT; return file_url."""
    init = client.post(
        FAL_UPLOAD_INIT,
        headers={
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
        },
        json={"file_name": path.name, "content_type": _content_type(path)},
    )
    if init.status_code >= 400:
        raise RuntimeError(f"fal upload initiate failed ({init.status_code}): {init.text}")
    meta = init.json()
    upload_url = meta["upload_url"]
    file_url = meta["file_url"]
    put = client.put(
        upload_url,
        headers={"Content-Type": _content_type(path)},
        content=path.read_bytes(),
    )
    if put.status_code >= 400:
        raise RuntimeError(f"fal upload PUT failed ({put.status_code}): {put.text}")
    return file_url


def build_wan_vace_body(
    *,
    prompt: str,
    video_url: str,
    image_url: str,
    resolution: str = "720p",
) -> dict:
    """Schema from https://modelschemas.com (fal-ai/wan-vace-apps/video-edit).

    Source also listed on fal model pages as fal-ai/wan-vace-apps/video-edit.
    """
    allowed = {"auto", "240p", "360p", "480p", "580p", "720p"}
    if resolution not in allowed:
        raise ValueError(
            f"wan-vace resolution must be one of {sorted(allowed)}, got {resolution!r}"
        )
    return {
        "prompt": prompt,
        "video_url": video_url,
        "image_urls": [image_url],
        "resolution": resolution,
        "enable_safety_checker": False,
    }


def wan_vace_edit(
    clip: Path,
    product: Path,
    prompt: str,
    *,
    out_path: Path,
    work_dir: Path,
    resolution: str = "720p",
    poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    # Validate before any paid upload.
    build_wan_vace_body(
        prompt=prompt,
        video_url="https://example.invalid/v",
        image_url="https://example.invalid/i",
        resolution=resolution,
    )
    key = _fal_key()
    work_dir.mkdir(parents=True, exist_ok=True)
    owns = client is None
    http = client or httpx.Client(timeout=120.0)
    raw_out = work_dir / "wan_vace_raw.mp4"
    try:
        video_url = fal_upload(http, clip, key)
        image_url = fal_upload(http, product, key)
        body = build_wan_vace_body(
            prompt=prompt,
            video_url=video_url,
            image_url=image_url,
            resolution=resolution,
        )
        submit = http.post(
            FAL_QUEUE,
            headers={
                "Authorization": f"Key {key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if submit.status_code >= 400:
            raise RuntimeError(
                f"fal submit failed ({submit.status_code}): {submit.text}"
            )
        handle = submit.json()
        status_url = handle["status_url"]
        response_url = handle["response_url"]

        deadline = time.monotonic() + poll_timeout_s
        delay = 2.0
        while time.monotonic() < deadline:
            status_resp = http.get(
                status_url,
                headers={"Authorization": f"Key {key}"},
            )
            if status_resp.status_code >= 400:
                raise RuntimeError(
                    f"fal poll failed ({status_resp.status_code}): {status_resp.text}"
                )
            status_payload = status_resp.json()
            status = status_payload.get("status")
            if status == "COMPLETED":
                break
            if status in {"FAILED", "CANCELLED", "ERROR"}:
                raise RuntimeError(f"fal ended status={status!r}: {status_payload}")
            sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 1.5, 10.0)
        else:
            raise RuntimeError(f"fal timed out polling {status_url}")

        result_resp = http.get(
            response_url,
            headers={"Authorization": f"Key {key}"},
        )
        if result_resp.status_code >= 400:
            raise RuntimeError(
                f"fal result failed ({result_resp.status_code}): {result_resp.text}"
            )
        result = result_resp.json()
        video = result.get("video") or {}
        output_url = video.get("url") if isinstance(video, dict) else video
        if not output_url:
            raise RuntimeError(f"fal completed without video.url: {result}")

        with http.stream("GET", output_url) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"fal download failed ({response.status_code})")
            with raw_out.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
        return mux_original_audio(raw_out, clip, out_path)
    finally:
        if owns:
            http.close()


MODAL_APP_NAME = "product-placement-wan-vace"
MODAL_FN_NAME = "edit"


def _require_modal_tokens() -> None:
    missing = [
        name
        for name in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")
        if not (os.environ.get(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing Modal credentials: set " + " and ".join(missing)
        )


def probe_portrait(clip: Path) -> bool:
    """True when the first video stream is taller than wide."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(clip),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (probe.stdout or "").strip()
    if "x" not in text:
        return False
    try:
        width_s, height_s = text.split("x", 1)
        return int(height_s) > int(width_s)
    except ValueError:
        return False


def modal_vace_hw(resolution: str, *, portrait: bool) -> tuple[int, int]:
    """Return (height, width) for VACE-14B 480p/720p, matching source orientation."""
    if resolution == "480p":
        return (832, 480) if portrait else (480, 832)
    if resolution == "720p":
        return (1280, 720) if portrait else (720, 1280)
    raise ValueError(
        f"wan-vace-modal resolution must be '480p' or '720p', got {resolution!r}"
    )


def wan_vace_modal_edit(
    clip: Path,
    product: Path,
    prompt: str,
    *,
    out_path: Path,
    work_dir: Path,
    resolution: str = "720p",
    mask: Path | None = None,
    remote: Callable[..., bytes] | None = None,
) -> Path:
    """Call the deployed Modal WanVACE function; mux original audio back.

    `mask` is a mask video (white = replace, black = keep), same frame count as
    the clip. Without it VACE regenerates the whole frame.
    """
    # Validate resolution before probing/calling Modal.
    modal_vace_hw(resolution, portrait=False)
    _require_modal_tokens()
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_out = work_dir / "wan_vace_modal_raw.mp4"

    if remote is None:
        import modal

        fn = modal.Function.from_name(MODAL_APP_NAME, MODAL_FN_NAME)
        remote = fn.remote

    height, width = modal_vace_hw(resolution, portrait=probe_portrait(clip))

    mp4 = remote(
        clip.read_bytes(),
        product.read_bytes(),
        prompt,
        height=height,
        width=width,
        mask_video_bytes=mask.read_bytes() if mask else None,
    )
    if not isinstance(mp4, (bytes, bytearray)):
        raise RuntimeError(f"Modal edit returned unexpected type: {type(mp4)!r}")
    raw_out.write_bytes(bytes(mp4))
    return mux_original_audio(raw_out, clip, out_path)


MODELS: dict[str, Callable[..., Path | EditResult]] = {
    "seedance": seedance_edit,
    "wan27": wan27_edit,
    "wan-vace": wan_vace_edit,
    "wan-vace-modal": wan_vace_modal_edit,
}
