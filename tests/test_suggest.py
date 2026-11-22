"""Offline tests for suggest ranking / face clearance / n — no network or GPU."""

from __future__ import annotations

import numpy as np

from product_placement.suggest import MIN_WINDOW_S, WINDOW_S, _Cand, _candidate_windows, clamp_n, pick_spread
from product_placement.vision import (
    BBox,
    Detection,
    Shot,
    choose_placement,
    largest_face_free_region,
    region_avoids_faces,
    _plural_label,
)


def _cand(start: float, score: float, shot: int = 0) -> _Cand:
    return _Cand(
        start_s=start,
        end_s=start + 5.0,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        region=BBox(0, 0, 4, 4),
        whats_there="cup",
        score=score,
        reason="test",
        shot_i=shot,
    )


def test_clamp_n_respects_3_to_5():
    assert clamp_n(1) == 3
    assert clamp_n(3) == 3
    assert clamp_n(5) == 5
    assert clamp_n(9) == 5


def test_pick_spread_returns_n_and_spaces_them():
    cands = [
        _cand(0, 0.9, 0),
        _cand(2, 0.95, 0),  # adjacent to first; should lose on gap
        _cand(20, 0.8, 1),
        _cand(40, 0.7, 2),
        _cand(60, 0.6, 3),
        _cand(80, 0.5, 4),
    ]
    picked = pick_spread(cands, 5, min_gap_s=10.0)
    assert len(picked) == 5
    starts = [p.start_s for p in picked]
    assert starts == sorted(starts)
    for a, b in zip(starts, starts[1:]):
        assert b - a >= 10.0


def test_pick_spread_fills_same_shot_on_the_second_pass():
    cands = [
        _cand(0, 1.0, 0),
        _cand(30, 0.9, 0),
        _cand(60, 0.8, 0),
    ]
    picked = pick_spread(cands, 3, min_gap_s=10.0)
    assert [p.start_s for p in picked] == [0.0, 30.0, 60.0]


def test_pick_spread_prefers_different_shots():
    # High score pair in same shot vs lower scores in other shots.
    cands = [
        _cand(0, 1.0, 0),
        _cand(30, 0.99, 0),
        _cand(60, 0.5, 1),
        _cand(90, 0.4, 2),
    ]
    picked = pick_spread(cands, 3, min_gap_s=10.0)
    assert len(picked) == 3
    assert len({p.shot_i for p in picked}) == 3


def test_region_avoids_detected_faces():
    faces = [BBox(100, 50, 80, 100)]
    clear = BBox(100, 400, 200, 150)
    overlap = BBox(110, 60, 60, 60)
    assert region_avoids_faces(clear, faces)
    assert not region_avoids_faces(overlap, faces)


def test_largest_face_free_region_skips_faces():
    faces = [BBox(0, 200, 400, 300)]
    box = largest_face_free_region(800, 600, faces, min_w_frac=0.25, min_h_frac=0.2)
    assert box is not None
    assert region_avoids_faces(box, faces)


def test_choose_placement_prefers_wine_glass_away_from_face():
    faces = [BBox(10, 10, 100, 120)]
    dets = [
        Detection("wine glass", 0.9, BBox(400, 350, 40, 80)),
        Detection("person", 0.95, BBox(0, 0, 200, 400)),
    ]
    out = choose_placement(dets, faces, 800, 600)
    assert out is not None
    region, label, score = out
    assert "wine glass" in label
    assert score >= 0.9
    assert region_avoids_faces(region, faces)


def test_candidate_windows_keeps_short_shots():
    shots = [
        Shot(0.0, 2.0),
        Shot(2.0, 4.0),
        Shot(4.0, 10.0),
    ]
    wins = _candidate_windows(shots, 10.0, WINDOW_S, MIN_WINDOW_S)
    # Short table shots become full-shot windows; long shot gets a 5s window.
    assert (0, 0.0, 2.0) in wins
    assert (1, 2.0, 4.0) in wins
    assert any(si == 2 and end - start >= WINDOW_S - 1e-6 for si, start, end in wins)


def test_plural_wine_glasses():
    assert _plural_label("wine glass") == "wine glasses"
    assert _plural_label("cup") == "cups"
