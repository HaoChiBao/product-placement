"""Brand scans: per-brand moments from the finder, served instead of the generic ones."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from product_placement.catalog import (
    brand_moments,
    claim_brand_scan,
    create_brand,
    create_episode,
    create_title,
    finish_brand_scan,
    get_brand_scan,
    insert_moment,
    list_moments,
    upsert_placement,
)
from product_placement.db import get_connection, init_schema
from product_placement.object_store import reset_object_store


@pytest.fixture
def catalog_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in ("AWS_S3_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(key, raising=False)
    reset_object_store()
    conn = get_connection()
    init_schema(conn)
    yield conn
    conn.close()
    reset_object_store()


def _episode(conn, tmp_path: Path) -> dict:
    title = create_title(conn, name="Scan Film", kind="movie")
    video = tmp_path / "scan.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True,
        capture_output=True,
    )
    return create_episode(conn, title_id=title["id"], name="E1", season=1, episode=1, video_path=video)


def _moment(conn, episode_id: str, what: str, brand_id: str | None = None) -> dict:
    return insert_moment(
        conn,
        episode_id=episode_id,
        start_s=1.0,
        end_s=2.0,
        region={"x": 1, "y": 2, "w": 30, "h": 40},
        whats_there=what,
        score=0.8,
        reason=what,
        brand_id=brand_id,
    )


def test_brand_moments_replace_generic_only_when_ready(catalog_env, tmp_path):
    conn = catalog_env
    ep = _episode(conn, tmp_path)
    soda = create_brand(conn, name="Acme Cola", brief="Soda can. Prefer tables.")
    other = create_brand(conn, name="Other Co", brief="Laptop.")

    _moment(conn, ep["id"], "plate")
    _moment(conn, ep["id"], "wine bottle", brand_id=soda["id"])

    # Scan not finished: the brand still sees the generic moments.
    assert [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"], brand_id=soda["id"])] == ["plate"]
    assert claim_brand_scan(conn, episode_id=ep["id"], brand_id=soda["id"]) is True
    assert [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"], brand_id=soda["id"])] == ["plate"]

    finish_brand_scan(conn, episode_id=ep["id"], brand_id=soda["id"], status="ready")
    picked = list_moments(conn, episode_id=ep["id"], brand_id=soda["id"])
    assert [m["whats_there"] for m in picked] == ["wine bottle"]
    assert picked[0]["brand_fit"] is not None

    # Another brand and the brand-agnostic listing never see Acme's moments.
    assert [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"], brand_id=other["id"])] == ["plate"]
    assert [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"])] == ["plate"]

    # A shared moment the brand already acted on stays listed after its scan lands.
    plate = list_moments(conn, episode_id=ep["id"])[0]
    upsert_placement(conn, brand_id=soda["id"], moment_id=plate["id"], status="rejected")
    kept = [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"], brand_id=soda["id"])]
    assert sorted(kept) == ["plate", "wine bottle"]
    conn.execute("DELETE FROM placements")
    conn.commit()

    payload = brand_moments(conn, episode_id=ep["id"], brand_id=soda["id"])
    assert payload["scan"]["status"] == "ready"
    assert [m["whats_there"] for m in payload["moments"]] == ["wine bottle"]


def test_ready_scan_with_no_moments_falls_back_to_generic(catalog_env, tmp_path):
    conn = catalog_env
    ep = _episode(conn, tmp_path)
    soda = create_brand(conn, name="Acme Cola", brief="Soda can.")
    _moment(conn, ep["id"], "plate")
    claim_brand_scan(conn, episode_id=ep["id"], brand_id=soda["id"])
    finish_brand_scan(conn, episode_id=ep["id"], brand_id=soda["id"], status="ready")
    assert [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"], brand_id=soda["id"])] == ["plate"]


def test_claim_brand_scan_is_exclusive_and_retryable(catalog_env, tmp_path):
    conn = catalog_env
    ep = _episode(conn, tmp_path)
    soda = create_brand(conn, name="Acme Cola", brief="Soda can.")
    args = {"episode_id": ep["id"], "brand_id": soda["id"]}

    assert get_brand_scan(conn, **args) is None
    assert claim_brand_scan(conn, **args) is True
    assert claim_brand_scan(conn, **args) is False  # running

    finish_brand_scan(conn, **args, status="failed", error="model down")
    assert get_brand_scan(conn, **args)["error"] == "model down"
    assert claim_brand_scan(conn, **args) is True  # failed scans can retry

    # A running scan whose worker stopped updating it can be reclaimed.
    conn.execute("UPDATE brand_scans SET updated_at = ? WHERE id = ?",
                 ("2000-01-01T00:00:00+00:00", get_brand_scan(conn, **args)["id"]))
    conn.commit()
    assert claim_brand_scan(conn, **args) is True

    finish_brand_scan(conn, **args, status="ready")
    assert claim_brand_scan(conn, **args) is False  # ready stays ready


def test_rescans_only_replace_their_own_moments(catalog_env, tmp_path, monkeypatch):
    from product_placement import suggest as suggest_mod
    from product_placement.catalog import _brand_from_row, get_brand, get_episode
    from product_placement.jobs import _suggest_and_store
    from product_placement.suggest import Moment
    from product_placement.vision import BBox

    conn = catalog_env
    ep = _episode(conn, tmp_path)
    soda = create_brand(conn, name="Acme Cola", brief="Soda can.")
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    seen_brands: list = []

    def fake_suggest(video_path, n, *, out_dir, brand=None, **_kw):
        seen_brands.append(getattr(brand, "name", None))
        label = "soda spot" if brand else "generic spot"
        return [Moment(0.0, 1.0, frame, BBox(1, 2, 3, 4), label, 0.9, label, 160, 90)]

    monkeypatch.setattr(suggest_mod, "suggest", fake_suggest)
    episode = get_episode(conn, id=ep["id"])
    brand = _brand_from_row(get_brand(conn, id=soda["id"]), conn)

    _suggest_and_store(conn, episode, n=3)
    _suggest_and_store(conn, episode, n=3, brand=brand)
    _suggest_and_store(conn, episode, n=3)  # generic re-scan keeps the brand's rows

    rows = conn.execute(
        "SELECT whats_there, brand_id, frame_key FROM moments WHERE episode_id = ? ORDER BY whats_there",
        (ep["id"],),
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("generic spot", None), ("soda spot", soda["id"])]
    assert "/brands/acme-cola/moments/" in rows[1][2]
    assert seen_brands == [None, "Acme Cola", None]
