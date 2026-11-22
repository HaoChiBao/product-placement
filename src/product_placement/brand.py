"""Brand onboarding: file-backed brand profiles and brief → structured rules."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from product_placement.vision import COCO_CLASSES

# Words/phrases that map onto YOLOX COCO labels for class boosts / vetoes.
_CLASS_ALIASES: dict[str, str] = {
    "wine glass": "wine glass",
    "wineglass": "wine glass",
    "glass": "wine glass",
    "cup": "cup",
    "mug": "cup",
    "coffee": "cup",
    "bottle": "bottle",
    "soda": "bottle",
    "can": "bottle",
    "bowl": "bowl",
    "vase": "vase",
    "laptop": "laptop",
    "computer": "laptop",
    "phone": "cell phone",
    "cell phone": "cell phone",
    "book": "book",
    "table": "dining table",
    "dining table": "dining table",
    "counter": "dining table",
    "kitchen counter": "dining table",
    "desk": "dining table",
    "couch": "couch",
    "sofa": "couch",
    "bed": "bed",
    "bench": "bench",
    "tv": "tv",
    "television": "tv",
    "sink": "sink",
    "refrigerator": "refrigerator",
    "fridge": "refrigerator",
    "suitcase": "suitcase",
    "chair": "chair",
    "handbag": "handbag",
    "clock": "clock",
    "plant": "potted plant",
    "potted plant": "potted plant",
}


@dataclass
class BrandRules:
    prefer: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    placement: str = "resting"  # resting | held | foreground | background
    min_area: float = 0.02  # fraction of frame

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefer": list(self.prefer),
            "avoid": list(self.avoid),
            "placement": self.placement,
            "min_area": self.min_area,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BrandRules:
        raw = raw or {}
        return cls(
            prefer=[str(x).strip() for x in (raw.get("prefer") or []) if str(x).strip()],
            avoid=[str(x).strip() for x in (raw.get("avoid") or []) if str(x).strip()],
            placement=str(raw.get("placement") or "resting"),
            min_area=float(raw.get("min_area") or 0.02),
        )


@dataclass
class Brand:
    id: str
    name: str
    brief: str = ""
    assets: list[str] = field(default_factory=list)  # filenames under brand dir
    rules: BrandRules = field(default_factory=BrandRules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "brief": self.brief,
            "assets": list(self.assets),
            "rules": self.rules.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Brand:
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            brief=str(raw.get("brief") or ""),
            assets=[str(x) for x in (raw.get("assets") or [])],
            rules=BrandRules.from_dict(raw.get("rules")),
        )


def brands_root(data_dir: str | Path | None = None) -> Path:
    import os

    if data_dir is not None:
        root = Path(data_dir)
    else:
        env = (os.environ.get("DATA_DIR") or "").strip()
        root = Path(env) if env else Path("runs") / "ui"
    path = root / "brands"
    path.mkdir(parents=True, exist_ok=True)
    return path


def brand_dir(brand_id: str, data_dir: str | Path | None = None) -> Path:
    return brands_root(data_dir) / brand_id


def new_brand_id() -> str:
    return uuid.uuid4().hex[:12]


_AVOID_RE = re.compile(
    r"\b(avoid|never|don't|do not|no|without|not on|keep off)\b", re.I
)
_PREFER_RE = re.compile(
    r"\b(prefer|want|like|place|put|on a|on the|in a|in the|held|resting)\b", re.I
)


def _split_phrases(brief: str) -> list[str]:
    """Split on sentence breaks and on commas that introduce a new prefer/avoid clause."""
    text = brief or ""
    # Prefer cups, avoid beds → two phrases
    text = re.sub(
        r",\s*(?=(?:avoid|never|don't|do not|no|prefer|want|like|place)\b)",
        ". ",
        text,
        flags=re.I,
    )
    parts = re.split(r"[.\n;|/]+", text)
    return [p.strip() for p in parts if p.strip()]


def parse_brief(brief: str) -> BrandRules:
    """Deterministic brief → rules. Structured rules stay editable after this seed."""
    prefer: list[str] = []
    avoid: list[str] = []
    placement = "resting"
    min_area = 0.02

    for phrase in _split_phrases(brief):
        low = phrase.lower()
        is_avoid = bool(_AVOID_RE.search(low))
        bucket = avoid if is_avoid else prefer
        # Strip leading directive words for cleaner chips.
        cleaned = re.sub(
            r"^(avoid|never|don't|do not|no|prefer|want|like|please)\s+",
            "",
            phrase,
            flags=re.I,
        ).strip(" ,.-")
        if not cleaned:
            continue
        if cleaned.lower() not in {x.lower() for x in bucket}:
            bucket.append(cleaned)

        # Placement / size only from prefer (or neutral) phrases — not avoid text.
        if is_avoid:
            continue
        if re.search(r"\bheld\b|\bin hand\b|\bhand[-\s]?held\b", low):
            placement = "held"
        elif re.search(r"\bresting\b|\bon (a |the )?(table|counter|desk|surface)\b", low):
            placement = "resting"
        if re.search(r"\bforeground\b|\bclose[-\s]?up\b", low):
            placement = "foreground"
        elif re.search(r"\bbackground\b|\bfar\b|\bdistant\b", low):
            placement = "background"
        if re.search(r"\blarge\b|\bprominent\b|\bbig\b", low):
            min_area = max(min_area, 0.04)
        if re.search(r"\bsmall\b|\bsubtle\b|\btiny\b", low):
            min_area = min(min_area, 0.015)

    # Surface mapped COCO class names as chips, scored per phrase (not whole brief)
    # so "table … Avoid beds" does not pull dining table into avoid.
    for phrase in _split_phrases(brief):
        low = phrase.lower()
        bucket = avoid if _AVOID_RE.search(low) else prefer
        for alias, label in sorted(_CLASS_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if alias not in low:
                continue
            if label.lower() in {x.lower() for x in bucket}:
                continue
            bucket.append(label)

    return BrandRules(prefer=prefer, avoid=avoid, placement=placement, min_area=min_area)


def mapped_coco_labels(phrases: list[str]) -> set[str]:
    """Map free-text prefer/avoid chips onto YOLOX COCO class names."""
    out: set[str] = set()
    for phrase in phrases:
        low = phrase.lower()
        if low in COCO_CLASSES:
            out.add(low)
            continue
        for alias, label in sorted(_CLASS_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if alias in low or low in alias:
                out.add(label)
                break
    return out


def save_brand(brand: Brand, data_dir: str | Path | None = None) -> Path:
    d = brand_dir(brand.id, data_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "brand.json"
    path.write_text(json.dumps(brand.to_dict(), indent=2) + "\n")
    return path


def load_brand(brand_id: str, data_dir: str | Path | None = None) -> Brand:
    path = brand_dir(brand_id, data_dir) / "brand.json"
    if not path.is_file():
        raise FileNotFoundError(f"brand not found: {brand_id}")
    return Brand.from_dict(json.loads(path.read_text()))


def list_brands(data_dir: str | Path | None = None) -> list[Brand]:
    root = brands_root(data_dir)
    out: list[Brand] = []
    for child in sorted(root.iterdir()):
        meta = child / "brand.json"
        if meta.is_file():
            out.append(Brand.from_dict(json.loads(meta.read_text())))
    return out


def delete_brand(brand_id: str, data_dir: str | Path | None = None) -> None:
    d = brand_dir(brand_id, data_dir)
    if d.is_dir():
        shutil.rmtree(d)


def resolve_brand(brand_ref: str, data_dir: str | Path | None = None) -> Brand:
    """Resolve a brand id or path to brand.json / brand directory."""
    ref = Path(brand_ref)
    if ref.is_file() and ref.name == "brand.json":
        return Brand.from_dict(json.loads(ref.read_text()))
    if ref.is_dir() and (ref / "brand.json").is_file():
        return Brand.from_dict(json.loads((ref / "brand.json").read_text()))
    return load_brand(brand_ref, data_dir)


def primary_asset_path(brand: Brand, data_dir: str | Path | None = None) -> Path | None:
    if not brand.assets:
        return None
    return brand_dir(brand.id, data_dir) / brand.assets[0]


def product_aspect_ratio(product_path: str | Path | None) -> float | None:
    if not product_path:
        return None
    path = Path(product_path)
    if not path.is_file():
        return None
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    h, w = img.shape[:2]
    if h <= 0:
        return None
    return float(w) / float(h)


def brand_swap_prompt(brand: Brand, target: str) -> str:
    prefer = ", ".join(brand.rules.prefer[:4]) or "natural product placement"
    avoid = ", ".join(brand.rules.avoid[:3])
    bits = [
        f"Replace the {target} in @Video1 with the product in @Image1 for brand {brand.name}.",
        f"Placement brief: {brand.brief.strip() or prefer}.",
        f"Prefer: {prefer}. Placement style: {brand.rules.placement}.",
    ]
    if avoid:
        bits.append(f"Avoid: {avoid}.")
    bits.append("Keep hands, camera motion, lighting and everything else identical.")
    return " ".join(bits)
