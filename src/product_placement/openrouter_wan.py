"""Full-clip object swap via OpenRouter Alibaba Wan 3.0.

OpenRouter only accepts https inputs (base64 data URLs 400). The worker
uploads the clamped moment clip and the brand asset, then passes S3
presigned GET URLs. Polling stays in the background job.
"""

from __future__ import annotations

import mimetypes
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

API_BASE = "https://openrouter.ai/api/v1"
VIDEOS_URL = f"{API_BASE}/videos"
MODELS_URL = f"{API_BASE}/videos/models"
MODEL_ID = "alibaba/wan-3.0"
WAN_720P_USD_PER_10S = 1.2
DEFAULT_POLL_TIMEOUT_S = 2700.0
DEFAULT_POLL_INTERVAL_S = 15.0
TERMINAL_FAILURE = frozenset({"failed", "cancelled", "expired"})
# One blip during an ~8 minute render must not abandon a job that is still running.
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_ASPECTS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "3:2", "2:3")
_FRAME_METHODS = frozenset({"higgsfield", "frame", "seedance", "segment"})


def resolve_swap_method(method: str | None = None) -> str:
    """`wan` (default) or `higgsfield` (frame edit + Seedance)."""
    if method is None:
        raw = os.environ.get("SWAP_METHOD") or "wan"
    else:
        raw = method
    raw = raw.strip().lower()
    if raw in {"", "wan", "wan-3.0", "wan3", "openrouter"}:
        return "wan"
    if raw in _FRAME_METHODS:
        return "higgsfield"
    raise ValueError(f"unknown swap method {method or raw!r}")


def estimate_wan_cost_usd(duration_s: float) -> float:
    """~$1.20 per 10s at 720p, before the job reports usage.cost."""
    seconds = max(1, int(round(max(0.0, float(duration_s)))))
    return round(seconds / 10.0 * WAN_720P_USD_PER_10S, 4)


def build_swap_prompt(source_object: str, product: str) -> str:
    source = (source_object or "object").strip() or "object"
    name = (product or "product").strip() or "product"
    return (
        f"Replace the {source} with the {name} in place. "
        "Keep exactly the same position, size and hand placement as the original object. "
        "Keep faces, motion, cuts, and framing unchanged."
    )


def clamp_window(
    start_s: float,
    end_s: float,
    max_duration_s: float,
) -> tuple[float, float]:
    """Shrink a moment/shot window to the model max, centered on its midpoint."""
    start = float(start_s)
    end = float(end_s)
    if end < start:
        start, end = end, start
    cap = float(max_duration_s)
    if cap <= 0:
        raise ValueError("max duration must be positive")
    if end - start <= cap + 1e-6:
        return start, end
    mid = (start + end) / 2.0
    half = cap / 2.0
    new_start = mid - half
    new_end = mid + half
    if new_start < start:
        new_start = start
        new_end = start + cap
    if new_end > end:
        new_end = end
        new_start = max(start, end - cap)
    return new_start, new_end


def choose_duration(seconds: float, supported: list[int] | None) -> int:
    """Shortest allowed duration that still covers the clip.

    A shorter Wan output is cut further by mux (`-shortest`), so the stored
    clip would end before the moment.
    """
    import math

    need = max(1, int(math.ceil(max(0.0, float(seconds)) - 1e-9)))
    if supported:
        options = sorted({int(d) for d in supported})
        if not options:
            raise ValueError("supported durations empty")
        covering = [d for d in options if d >= need]
        return covering[0] if covering else options[-1]
    return max(2, min(30, max(2, need)))


def nearest_aspect(
    width: int,
    height: int,
    supported: list[str] | None = None,
) -> str:
    if width <= 0 or height <= 0:
        return "16:9"
    ratio = width / float(height)
    labels = list(supported or _ASPECTS)

    def _value(label: str) -> float:
        a, b = label.split(":", 1)
        return float(a) / float(b)

    parsed: list[tuple[str, float]] = []
    for label in labels:
        try:
            parsed.append((label, _value(label)))
        except (ValueError, ZeroDivisionError):
            continue
    if not parsed:
        return "16:9"
    return min(parsed, key=lambda item: abs(item[1] - ratio))[0]


def _require_https(url: str, what: str) -> str:
    if not str(url).startswith("https://"):
        raise ValueError(f"{what} must be an https URL")
    return str(url)


def build_wan_video_request(
    *,
    prompt: str,
    video_url: str,
    duration: int,
    seed: int,
    image_url: str | None = None,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
    generate_audio: bool = False,
    model: str = MODEL_ID,
) -> dict[str, Any]:
    """JSON body for POST /api/v1/videos. Rejects non-https inputs."""
    video = _require_https(video_url, "video_url")
    refs: list[dict[str, Any]] = [
        {"type": "video_url", "video_url": {"url": video}},
    ]
    if image_url:
        image = _require_https(image_url, "image_url")
        refs.append({"type": "image_url", "image_url": {"url": image}})
    return {
        "model": model,
        "prompt": prompt,
        "input_references": refs,
        "duration": int(duration),
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": bool(generate_audio),
        "seed": int(seed),
    }


def find_wan_model(payload: Any, model_id: str = MODEL_ID) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("id") == model_id:
        return payload
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        data = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("id") == model_id:
            return item
    raise RuntimeError(f"video model {model_id} not listed")


def max_supported_duration(model: dict[str, Any]) -> int:
    raw = model.get("supported_durations") or []
    durations = []
    for item in raw:
        try:
            durations.append(int(item))
        except (TypeError, ValueError):
            continue
    if not durations:
        return 30
    return max(durations)


def supported_duration_list(model: dict[str, Any]) -> list[int] | None:
    raw = model.get("supported_durations")
    if not raw:
        return None
    out = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out or None


def usage_cost(payload: dict[str, Any]) -> float | None:
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    if cost is None:
        return None
    return float(cost)


def content_download_url(payload: dict[str, Any], job_id: str) -> str:
    urls = payload.get("unsigned_urls") or []
    if urls:
        raw = str(urls[0])
        if raw.startswith("https://") or raw.startswith("http://"):
            return raw
        if not raw.startswith("/"):
            raw = "/" + raw
        return urljoin("https://openrouter.ai/", raw)
    return f"{VIDEOS_URL}/{job_id}/content"


def _redact(text: str, key: str) -> str:
    flat = text.replace("\n", " ")
    if key:
        flat = flat.replace(key, "[redacted]")
    return flat[:300]


def _auth_headers(key: str, *, json_body: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def openrouter_api_key(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    key = (source.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return key


def fetch_wan_model(
    client: httpx.Client,
    key: str,
    *,
    model_id: str = MODEL_ID,
) -> dict[str, Any]:
    try:
        response = client.get(MODELS_URL, headers=_auth_headers(key))
    except Exception as exc:
        raise RuntimeError(
            f"openrouter video models request failed: {type(exc).__name__}"
        ) from None
    if response.status_code >= 400:
        raise RuntimeError(
            f"openrouter video models failed ({response.status_code}): "
            f"{_redact(response.text, key)}"
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("openrouter video models response was not JSON") from None
    return find_wan_model(payload, model_id)


def poll_video_job(
    client: httpx.Client,
    job_id: str,
    *,
    key: str,
    timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
    interval_s: float = DEFAULT_POLL_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_status: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """GET /videos/{id} until completed, or failed/cancelled/expired, or timeout.

    A dropped connection or a 408/429/5xx is retried until the deadline. The
    provider job keeps running, so one blip must not fail the placement.
    """
    deadline = clock() + float(timeout_s)
    url = f"{VIDEOS_URL}/{job_id}"
    headers = _auth_headers(key)
    while True:
        if clock() > deadline:
            raise TimeoutError(f"openrouter video job {job_id} timed out")
        try:
            response = client.get(url, headers=headers)
        except (httpx.HTTPError, OSError, TimeoutError):
            sleep(interval_s)
            continue
        if response.status_code in _RETRY_STATUS:
            sleep(interval_s)
            continue
        if response.status_code >= 400:
            raise RuntimeError(
                f"openrouter video poll failed ({response.status_code}): "
                f"{_redact(response.text, key)}"
            )
        try:
            payload = response.json()
        except Exception:
            raise RuntimeError("openrouter video poll response was not JSON") from None
        status = str(payload.get("status") or "")
        if on_status is not None:
            on_status(status, payload)
        if status == "completed":
            return payload
        if status in TERMINAL_FAILURE:
            detail = _redact(str(payload.get("error") or ""), key)
            raise RuntimeError(f"openrouter video job {status}: {detail}".rstrip())
        sleep(interval_s)


def download_video(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    key: str,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = _auth_headers(key)
    try:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code >= 400:
                raise RuntimeError(
                    f"openrouter video download failed ({response.status_code})"
                )
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"openrouter video download failed: {type(exc).__name__}"
        ) from None
    if not dest.is_file() or dest.stat().st_size < 100:
        raise RuntimeError("openrouter video download was empty")
    return dest


def _content_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    if path.suffix.lower() == ".png":
        return "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return guessed or "application/octet-stream"


def _https_presign(store: Any, key: str, *, what: str, expires: int = 7200) -> str:
    url = store.public_or_presigned_url(key, expires=expires)
    try:
        return _require_https(url, what)
    except ValueError as exc:
        raise RuntimeError(
            "Wan swap needs S3 presigned https URLs "
            "(set AWS_S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)"
        ) from exc


def submit_video(
    client: httpx.Client,
    body: dict[str, Any],
    *,
    key: str,
) -> str:
    try:
        response = client.post(VIDEOS_URL, headers=_auth_headers(key, json_body=True), json=body)
    except Exception as exc:
        raise RuntimeError(
            f"openrouter video submit failed: {type(exc).__name__}"
        ) from None
    if response.status_code >= 400:
        raise RuntimeError(
            f"openrouter video submit failed ({response.status_code}): "
            f"{_redact(response.text, key)}"
        )
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError("openrouter video submit response was not JSON") from None
    job_id = str(payload.get("id") or "").strip()
    if not job_id:
        raise RuntimeError("openrouter video submit missing id")
    return job_id


def run_wan_swap(
    *,
    video_path: str | Path,
    product_path: str | Path,
    start_s: float,
    end_s: float,
    target: str,
    out_path: str | Path,
    work_dir: str | Path,
    product_name: str = "product",
    resolution: str = "720p",
    seed: int | None = None,
    store: Any = None,
    client: httpx.Client | None = None,
    env: dict[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_status: Callable[[str, dict[str, Any]], None] | None = None,
    poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S,
    model_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract the moment, edit it with Wan 3.0, mux the original audio, return metadata."""
    from product_placement.audio import mux_original_audio
    from product_placement.crop_stitch import extract_moment_clip, probe_video
    from product_placement.frame_swap import extract_frame_at

    key = openrouter_api_key(env)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if float(end_s) <= float(start_s):
        raise ValueError("end_s must be greater than start_s")

    owns_store = store is None
    if owns_store:
        from product_placement.object_store import get_object_store

        store = get_object_store()

    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0))

    uploaded: list[str] = []
    chosen_seed = int(seed) if seed is not None else random.SystemRandom().randrange(0, 2**31)
    try:
        model = model_info if model_info is not None else fetch_wan_model(http, key)
        cap = max_supported_duration(model)
        window_start, window_end = clamp_window(start_s, end_s, float(cap))
        clip_path = extract_moment_clip(
            video_path,
            window_start,
            window_end,
            work_dir / "source.mp4",
            max_s=float(cap),
        )
        try:
            width, height, probed, _fps = probe_video(clip_path)
        except Exception:
            width, height, probed = 0, 0, max(0.1, window_end - window_start)
        duration = choose_duration(
            probed if probed > 0 else (window_end - window_start),
            supported_duration_list(model),
        )
        aspect = nearest_aspect(
            width,
            height,
            list(model.get("supported_aspect_ratios") or []) or None,
        )

        token = uuid.uuid4().hex
        clip_key = f"tmp/wan/{token}/clip.mp4"
        store.put_file(clip_path, clip_key, content_type="video/mp4")
        uploaded.append(clip_key)
        product = Path(product_path)
        product_key = f"tmp/wan/{token}/product{product.suffix.lower() or '.png'}"
        store.put_file(product, product_key, content_type=_content_type(product))
        uploaded.append(product_key)
        video_url = _https_presign(store, clip_key, what="video_url")
        image_url = _https_presign(store, product_key, what="image_url")

        prompt = build_swap_prompt(target, product_name)
        body = build_wan_video_request(
            prompt=prompt,
            video_url=video_url,
            image_url=image_url,
            duration=duration,
            seed=chosen_seed,
            resolution=resolution,
            aspect_ratio=aspect,
            generate_audio=False,
        )
        job_id = submit_video(http, body, key=key)
        completed = poll_video_job(
            http,
            job_id,
            key=key,
            timeout_s=poll_timeout_s,
            sleep=sleep,
            clock=clock,
            on_status=on_status,
        )
        download_url = content_download_url(completed, job_id)
        raw_out = work_dir / "wan_raw.mp4"
        download_video(http, download_url, raw_out, key=key)
        mux_original_audio(raw_out, clip_path, out_path)

        frame_path = ""
        try:
            frame = extract_frame_at(clip_path, 0.0, work_dir / "source_frame.jpg")
            frame_path = str(frame)
        except Exception:
            frame_path = ""

        cost = usage_cost(completed)
        return {
            "result_path": str(out_path),
            "frame_path": frame_path,
            "edited_frame_path": "",
            "backend": "wan-3.0",
            "request_id": job_id,
            "video_url": "",
            "cost_usd": cost,
            "estimated_cost_usd": estimate_wan_cost_usd(float(duration)),
            "billed_duration_s": float(duration),
            "duration_s": float(probed or (window_end - window_start)),
            "start_s": window_start,
            "end_s": window_end,
            "resolution": resolution,
            "aspect_ratio": aspect,
            "seed": chosen_seed,
            "prompt": prompt,
            "mock": False,
        }
    finally:
        for object_key in uploaded:
            try:
                store.delete(object_key)
            except Exception:
                pass
        if owns_client:
            http.close()
