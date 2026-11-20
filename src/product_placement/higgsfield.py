"""Higgsfield REST client: auth, upload, submit, poll, download."""

from __future__ import annotations

import mimetypes
import os
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

API_BASE = "https://api.higgsfield.ai"
SEEDANCE_EDIT = f"{API_BASE}/bytedance/seedance-2.5/video-edit"
GENJUTSU_SWAP = f"{API_BASE}/higgsfield/genjutsu/object-swap/v1.0"
UPLOAD_URL = f"{API_BASE}/files/generate-upload-url"

TERMINAL = frozenset({"completed", "failed", "nsfw", "canceled"})


class HiggsfieldError(RuntimeError):
    """API or polling failure."""


@dataclass(frozen=True)
class JobResult:
    request_id: str
    video_url: str
    backend: str
    raw: dict[str, Any]


def auth_header(
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build Authorization from HF_KEY or HF_API_KEY_ID + HF_API_KEY_SECRET."""
    source = env if env is not None else os.environ
    combined = (source.get("HF_KEY") or "").strip()
    if combined:
        return {"Authorization": f"Key {combined}"}
    key_id = (source.get("HF_API_KEY_ID") or "").strip()
    secret = (source.get("HF_API_KEY_SECRET") or "").strip()
    if key_id and secret:
        return {"Authorization": f"Key {key_id}:{secret}"}
    raise HiggsfieldError(
        "Set HF_KEY (id:secret) or both HF_API_KEY_ID and HF_API_KEY_SECRET"
    )


def build_seedance_body(
    *,
    prompt: str,
    video_url: str,
    image_url: str,
    resolution: str = "720p",
) -> dict[str, Any]:
    # Docs: do not send duration; output length comes from the source video.
    return {
        "prompt": prompt,
        "video_url": video_url,
        "image_urls": [image_url],
        "resolution": resolution,
    }


def build_genjutsu_body(
    *,
    prompt: str,
    video_url: str,
    image_url: str,
    resolution: str = "720p",
) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "video_url": video_url,
        "image_urls": [image_url],
        "resolution": resolution,
    }


def default_swap_prompt(target: str) -> str:
    return (
        f"Replace the {target} in @Video1 with the product in @Image1. "
        "Keep the hands, camera motion, lighting and everything else identical."
    )


class HiggsfieldClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        env: dict[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=60.0)
        self._env = env
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HiggsfieldClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self, *, idempotent: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **auth_header(self._env)}
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        return headers

    def upload_file(self, path: str | Path) -> str:
        """Upload via Higgsfield presigned URL; return public_url."""
        path = Path(path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".mp4":
            content_type = "video/mp4"
        elif path.suffix.lower() == ".png":
            content_type = "image/png"
        elif path.suffix.lower() in {".jpg", ".jpeg"}:
            content_type = "image/jpeg"

        create = self._client.post(
            UPLOAD_URL,
            headers=self._headers(),
            json={"content_type": content_type},
        )
        if create.status_code >= 400:
            raise HiggsfieldError(
                f"upload-url failed ({create.status_code}): {create.text}"
            )
        meta = create.json()
        upload_url = meta["upload_url"]
        public_url = meta["public_url"]
        upload_headers = dict(meta.get("upload_headers") or {})
        if "Content-Type" not in upload_headers:
            upload_headers["Content-Type"] = content_type

        data = path.read_bytes()
        put = self._client.put(upload_url, headers=upload_headers, content=data)
        if put.status_code >= 400:
            raise HiggsfieldError(
                f"presigned upload failed ({put.status_code}): {put.text}"
            )
        return public_url

    def submit(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(
            url,
            headers=self._headers(idempotent=True),
            json=body,
        )
        if response.status_code >= 400:
            raise HiggsfieldError(
                f"submit failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        if "status_url" not in payload:
            raise HiggsfieldError(f"submit response missing status_url: {payload}")
        return payload

    def poll(
        self,
        status_url: str,
        *,
        timeout_s: float = 900.0,
        initial_delay_s: float = 2.0,
    ) -> dict[str, Any]:
        """Poll until a terminal status. Docs: start at 2s, back off to 10s."""
        deadline = time.monotonic() + timeout_s
        delay = initial_delay_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = self._client.get(
                status_url,
                headers=auth_header(self._env),
            )
            if response.status_code in {401, 404}:
                raise HiggsfieldError(
                    f"status poll stopped ({response.status_code}): {response.text}"
                )
            if response.status_code >= 500:
                self._sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 1.5, 10.0)
                continue
            if response.status_code >= 400:
                raise HiggsfieldError(
                    f"status poll failed ({response.status_code}): {response.text}"
                )
            last = response.json()
            status = last.get("status")
            if status in TERMINAL:
                return last
            self._sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 1.5, 10.0)
        raise HiggsfieldError(f"timed out waiting for {status_url}; last={last}")

    def download(self, url: str, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise HiggsfieldError(
                    f"download failed ({response.status_code}): {response.text}"
                )
            with out_path.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
        return out_path

    def run_seedance(
        self,
        *,
        prompt: str,
        video_url: str,
        image_url: str,
        resolution: str = "720p",
        poll_timeout_s: float = 900.0,
    ) -> JobResult:
        body = build_seedance_body(
            prompt=prompt,
            video_url=video_url,
            image_url=image_url,
            resolution=resolution,
        )
        handle = self.submit(SEEDANCE_EDIT, body)
        done = self.poll(handle["status_url"], timeout_s=poll_timeout_s)
        return self._as_result(done, backend="seedance")

    def run_genjutsu(
        self,
        *,
        prompt: str,
        video_url: str,
        image_url: str,
        resolution: str = "720p",
        poll_timeout_s: float = 900.0,
    ) -> JobResult:
        body = build_genjutsu_body(
            prompt=prompt,
            video_url=video_url,
            image_url=image_url,
            resolution=resolution,
        )
        handle = self.submit(GENJUTSU_SWAP, body)
        done = self.poll(handle["status_url"], timeout_s=poll_timeout_s)
        return self._as_result(done, backend="genjutsu")

    @staticmethod
    def _as_result(done: dict[str, Any], *, backend: str) -> JobResult:
        status = done.get("status")
        if status != "completed":
            raise HiggsfieldError(f"{backend} ended with status={status!r}: {done}")
        video = done.get("video") or {}
        url = video.get("url")
        if not url:
            raise HiggsfieldError(f"{backend} completed without video.url: {done}")
        return JobResult(
            request_id=str(done.get("request_id") or ""),
            video_url=url,
            backend=backend,
            raw=done,
        )
