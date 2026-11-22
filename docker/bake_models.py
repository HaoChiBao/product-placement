"""Download YuNet + YOLOX (+ optional CLIP) into MODEL_CACHE_DIR at image build."""

from pathlib import Path

from product_placement.models_store import ensure_clip, ensure_yunet, ensure_yolox

p = Path("/app/models")
ensure_yunet(p)
ensure_yolox(p)
try:
    ensure_clip(p)
    print("clip ok")
except Exception as e:
    print("clip skip", e)
print("models ok", list(p.iterdir()))
