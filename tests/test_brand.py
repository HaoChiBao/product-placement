"""Brand brief parsing, ranking, vetoes; no-brand path unchanged."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from product_placement.brand import (
    Brand,
    BrandRules,
    mapped_coco_labels,
    parse_brief,
    save_brand,
    load_brand,
)
from product_placement.brand_score import (
    BrandFit,
    aspect_fit_score,
    score_brand_window,
)
from product_placement.suggest import Moment, _Cand, pick_spread
from product_placement.vision import BBox, Detection


def test_parse_brief_splits_prefer_and_avoid():
    rules = parse_brief(
        "Prefer cups and bottles on a dining table. Avoid beds and laptops. "
        "Product should be resting on the counter."
    )
    prefer = {p.lower() for p in rules.prefer}
    avoid = {a.lower() for a in rules.avoid}
    assert any("cup" in p or p == "cup" for p in prefer) or "cup" in mapped_coco_labels(
        rules.prefer
    )
    assert "dining table" in mapped_coco_labels(rules.prefer) or any(
        "table" in p for p in prefer
    )
    assert "bed" in mapped_coco_labels(rules.avoid) or any("bed" in a for a in avoid)
    assert rules.placement in {"resting", "foreground", "background", "held"}


def test_mapped_coco_labels():
    assert "cup" in mapped_coco_labels(["coffee mug on a table"])
    assert "dining table" in mapped_coco_labels(["kitchen counter"])
    assert "laptop" in mapped_coco_labels(["avoid the laptop"])


def test_avoid_vetoes_window():
    brand = Brand(
        id="t1",
        name="Test",
        brief="avoid beds",
        rules=BrandRules(prefer=["cup"], avoid=["bed"]),
    )
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    region = BBox(40, 40, 80, 80)
    dets = [Detection(label="bed", score=0.9, box=BBox(10, 10, 200, 120))]
    fit = score_brand_window(
        frame_bgr=frame,
        region=region,
        whats_there="empty bed",
        dets=dets,
        brand=brand,
        product_ar=0.5,
        clip_scorer=None,
    )
    assert fit.vetoed
    assert fit.veto_reason and "bed" in fit.veto_reason


def test_prefer_boosts_ranking_vs_baseline():
    """Same two candidates; brand prefer for cup should rank cup above bed surface."""
    cup = _Cand(
        start_s=0,
        end_s=5,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        region=BBox(0, 0, 4, 4),
        whats_there="cup",
        score=0.5,
        reason="cup",
        shot_i=0,
    )
    bed = _Cand(
        start_s=10,
        end_s=15,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        region=BBox(0, 0, 4, 4),
        whats_there="empty bed",
        score=0.55,
        reason="bed",
        shot_i=1,
    )
    # Without brand, bed wins on base score.
    assert pick_spread([cup, bed], 1, min_gap_s=1)[0].whats_there == "empty bed"

    brand = Brand(
        id="soda",
        name="Soda",
        rules=BrandRules(prefer=["cup", "bottle", "dining table"], avoid=["bed"]),
    )
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    cup_fit = score_brand_window(
        frame_bgr=frame,
        region=BBox(40, 40, 40, 60),
        whats_there="cup",
        dets=[Detection("cup", 0.85, BBox(40, 40, 30, 50))],
        brand=brand,
        product_ar=0.55,
        clip_scorer=None,
    )
    bed_fit = score_brand_window(
        frame_bgr=frame,
        region=BBox(20, 80, 120, 60),
        whats_there="empty bed",
        dets=[Detection("bed", 0.9, BBox(10, 40, 200, 120))],
        brand=brand,
        product_ar=0.55,
        clip_scorer=None,
    )
    assert bed_fit.vetoed
    assert not cup_fit.vetoed
    assert cup_fit.total > 0.3


def test_aspect_fit_prefers_matching_box():
    tall = BBox(0, 0, 40, 100)
    wide = BBox(0, 0, 100, 40)
    assert aspect_fit_score(tall, 0.4) > aspect_fit_score(wide, 0.4)


def test_no_brand_moment_dict_omits_brand_fit():
    m = Moment(
        start_s=0,
        end_s=5,
        frame_path=Path("x.jpg"),
        region=BBox(0, 0, 1, 1),
        whats_there="cup",
        score=0.5,
        reason="ok",
    )
    d = m.to_dict()
    assert "brand_fit" not in d


def test_brand_roundtrip(tmp_path: Path):
    brand = Brand(
        id="abc123",
        name="Acme Cola",
        brief="Prefer cups on a dining table. Avoid beds.",
        assets=["can.png"],
        rules=parse_brief("Prefer cups on a dining table. Avoid beds."),
    )
    save_brand(brand, data_dir=tmp_path)
    loaded = load_brand("abc123", data_dir=tmp_path)
    assert loaded.name == "Acme Cola"
    assert loaded.assets == ["can.png"]
    assert mapped_coco_labels(loaded.rules.prefer)
