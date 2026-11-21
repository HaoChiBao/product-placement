"""Download and cache YuNet + YOLOX ONNX weights used by suggest."""

from __future__ import annotations

import urllib.request
from pathlib import Path

YUNET_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
YOLOX_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/object_detection_yolox/object_detection_yolox_2022nov.onnx"
)

YUNET_NAME = "face_detection_yunet_2023mar.onnx"
YOLOX_NAME = "object_detection_yolox_2022nov.onnx"


def default_cache_dir() -> Path:
    import os

    env = (os.environ.get("MODEL_CACHE_DIR") or "").strip()
    if env:
        return Path(env)
    # Docker / Railway image bakes weights here at build time.
    baked = Path("/app/models")
    if baked.is_dir():
        return baked
    return Path.home() / ".cache" / "product-placement" / "models"


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1000:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".partial")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


def ensure_yunet(cache_dir: Path | None = None) -> Path:
    root = cache_dir or default_cache_dir()
    local = Path("checkpoints") / YUNET_NAME
    if local.is_file() and local.stat().st_size > 1000:
        return local
    return _download(YUNET_URL, root / YUNET_NAME)


def ensure_yolox(cache_dir: Path | None = None) -> Path:
    root = cache_dir or default_cache_dir()
    local = Path("checkpoints") / YOLOX_NAME
    if local.is_file() and local.stat().st_size > 1000:
        return local
    return _download(YOLOX_URL, root / YOLOX_NAME)
