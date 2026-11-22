"""Admin import of finished placements and generic finder moments."""

from __future__ import annotations

import json

import pytest

from product_placement.catalog import (
    create_brand,
    create_episode,
    create_title,
    get_watch_payload,
    import_finished_placements,
    insert_moment,
    list_moments,
    replace_generic_moments,
)
from product_placement.db import fetchall_dicts, get_connection, init_schema
from product_placement.object_store import get_object_store, reset_object_store


@pytest.fixture()
def catalog_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in (
        "AWS_S3_BUCKET_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL_S3",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_object_store()
    conn = get_connection()
    init_schema(conn)
    yield conn
    conn.close()
    reset_object_store()


def _episode(conn, *, slug="home-alone", name="Home Alone", duration_s=145.45, episode_id=None):
    title = create_title(conn, name=name, kind="movie", slug=slug, source="hosted")
    vkey = f"catalog/{slug}/s01e01/original.mp4"
    get_object_store().put_bytes(b"mp4", vkey, content_type="video/mp4")
    ep = create_episode(
        conn,
        title_id=title["id"],
        name=name,
        season=1,
        episode=1,
        video_key=vkey,
        duration_s=duration_s,
        suggest_status="ready",
        episode_id=episode_id,
    )
    return title, ep


def _placement(**overrides):
    item = {
        "episode_id": "ep",
        "start_s": 104.701,
        "end_s": 107.701,
        "brand": "AT&T",
        "object": "landline phone",
        "video_s3_key": "swaps/home-alone/ha-1.mp4",
        "cost_usd": 0.3576,
    }
    item.update(overrides)
    return item


def _moment(**overrides):
    item = {
        "start_s": 0.0,
        "end_s": 7.702,
        "region": {"x": 1077, "y": 1003, "w": 63, "h": 54},
        "whats_there": "Christmas ornament",
        "score": 0.85,
        "reason": "Gemini object finder; category packaging.",
        "frame_key": "catalog/home-alone/s01e01/moments/moment_01.jpg",
        "frame_w": 1920,
        "frame_h": 1080,
        "brand_id": None,
    }
    item.update(overrides)
    return item


def test_placement_import_is_idempotent_and_plays_on_watch(catalog_env):
    conn = catalog_env
    _title, ep = _episode(conn, episode_id="f507d5d1ce0f")
    first = import_finished_placements(
        conn,
        [
            _placement(episode_id=ep["id"]),
            _placement(
                episode_id=ep["id"],
                start_s=62.668,
                end_s=64.668,
                brand="Sherwin-Williams",
                object="paint can on banister",
                video_s3_key="swaps/home-alone/ha-4.mp4",
                cost_usd=0.30,
            ),
        ],
    )
    assert [row["action"] for row in first["placements"]] == ["created", "created"]
    assert first["placements"][0]["status"] == "ready"
    assert first["placements"][0]["backend"] == "wan-3.0"
    assert first["placements"][0]["clip_key"] == "swaps/home-alone/ha-1.mp4"
    assert first["placements"][0]["cost_usd"] == pytest.approx(0.3576)

    again = import_finished_placements(
        conn,
        [
            _placement(
                episode_id=ep["id"],
                end_s=108.0,
                video_s3_key="swaps/home-alone/ha-1-v2.mp4",
                cost_usd=0.4,
                object="phone",
            )
        ],
    )
    assert again["placements"][0]["action"] == "updated"
    assert again["placements"][0]["id"] == first["placements"][0]["id"]
    assert again["placements"][0]["clip_key"] == "swaps/home-alone/ha-1-v2.mp4"
    assert again["placements"][0]["cost_usd"] == pytest.approx(0.4)
    assert again["placements"][0]["clip_end_s"] == pytest.approx(108.0)

    rows = fetchall_dicts(conn, "SELECT id FROM placements WHERE episode_id = ?", (ep["id"],))
    assert len(rows) == 2
    brands = fetchall_dicts(conn, "SELECT name FROM brands ORDER BY name")
    assert [b["name"] for b in brands] == ["AT&T", "Sherwin-Williams"]

    payload = get_watch_payload(conn, title_slug="home-alone", season=1, episode=1)
    by_brand = {p["brand_name"]: p for p in payload["placements"]}
    assert by_brand["AT&T"]["start_s"] == pytest.approx(104.701)
    assert by_brand["AT&T"]["end_s"] == pytest.approx(108.0)
    assert by_brand["AT&T"]["clip_url"].endswith("swaps/home-alone/ha-1-v2.mp4")
    assert by_brand["Sherwin-Williams"]["start_s"] == pytest.approx(62.668)
    assert "phone" == fetchall_dicts(
        conn, "SELECT whats_there FROM moments WHERE id = ?", (again["placements"][0]["moment_id"],)
    )[0]["whats_there"]


def test_placement_import_reuses_brand_and_keeps_other_starts(catalog_env):
    conn = catalog_env
    _title, ep = _episode(conn)
    existing = create_brand(conn, name="Zippo", brief="")
    imported = import_finished_placements(
        conn,
        {
            "placements": [
                _placement(
                    episode_id=ep["id"],
                    start_s=17.085,
                    end_s=20.085,
                    brand="zippo",
                    object="lighter fluid can in drawer",
                    video_s3_key="swaps/office/to-1.mp4",
                    cost_usd=0.36,
                ),
                _placement(
                    episode_id=ep["id"],
                    start_s=1.46,
                    end_s=8.46,
                    brand="Zippo",
                    object="another can",
                    video_s3_key="swaps/office/to-1b.mp4",
                    cost_usd=0.2,
                ),
            ]
        },
    )
    assert imported["placements"][0]["brand_id"] == existing["id"]
    assert imported["placements"][1]["brand_id"] == existing["id"]
    assert imported["placements"][0]["id"] != imported["placements"][1]["id"]
    assert len(fetchall_dicts(conn, "SELECT id FROM brands")) == 1
    assert len(fetchall_dicts(conn, "SELECT id FROM placements")) == 2


def test_versioned_placements_share_a_window(catalog_env):
    conn = catalog_env
    _title, ep = _episode(conn)
    start = 107.87
    end = 113.87
    daisy = dict(
        episode_id=ep["id"],
        start_s=start,
        end_s=end,
        brand="Daisy",
        object="sour cream tub",
        slot_key="fridge-tub",
    )

    def daisy_item(**overrides):
        item = dict(daisy)
        item.update(overrides)
        return _placement(**item)

    first = import_finished_placements(
        conn,
        [
            daisy_item(video_s3_key="swaps/home-alone/ha-2.mp4", version_label="Daisy v1"),
            daisy_item(
                video_s3_key="swaps/home-alone/ha-2b.mp4",
                cost_usd=0.32,
                version_label="Daisy v2",
            ),
            _placement(
                episode_id=ep["id"],
                start_s=start,
                end_s=end,
                brand="Swiss Miss",
                object="cocoa tin",
                video_s3_key="swaps/home-alone/ha-3.mp4",
                cost_usd=0.33,
                version_label="Swiss Miss",
                slot_key="fridge-tub",
            ),
        ],
    )
    assert [row["action"] for row in first["placements"]] == ["created", "created", "created"]
    assert len({row["id"] for row in first["placements"]}) == 3

    updated = import_finished_placements(
        conn,
        [
            daisy_item(
                start_s=start + 0.0004,
                video_s3_key="swaps/home-alone/ha-2-new.mp4",
                cost_usd=0.4,
                version_label="  Daisy v1  ",
                slot_key="other-slot",
            )
        ],
    )
    assert updated["placements"][0]["action"] == "updated"
    assert updated["placements"][0]["id"] == first["placements"][0]["id"]
    assert updated["placements"][0]["version_label"] == "Daisy v1"
    assert updated["placements"][0]["slot_key"] == "other-slot"
    assert updated["placements"][0]["clip_key"] == "swaps/home-alone/ha-2-new.mp4"

    rows = fetchall_dicts(
        conn,
        "SELECT version_label, clip_key FROM placements WHERE episode_id = ? ORDER BY created_at, id",
        (ep["id"],),
    )
    assert [row["version_label"] for row in rows] == ["Daisy v1", "Daisy v2", "Swiss Miss"]
    assert rows[1]["clip_key"] == "swaps/home-alone/ha-2b.mp4"
    assert rows[2]["clip_key"] == "swaps/home-alone/ha-3.mp4"

    bare = import_finished_placements(
        conn,
        [
            daisy_item(
                video_s3_key="swaps/home-alone/ha-2-bare.mp4",
                cost_usd=0.2,
                version_label="  ",
                slot_key="",
            )
        ],
    )
    assert bare["placements"][0]["action"] == "created"
    assert bare["placements"][0]["version_label"] is None
    assert bare["placements"][0]["slot_key"] is None
    again = import_finished_placements(
        conn,
        [
            daisy_item(
                video_s3_key="swaps/home-alone/ha-2-bare-2.mp4",
                cost_usd=0.21,
                slot_key=" ",
            )
        ]
    )
    assert again["placements"][0]["action"] == "updated"
    assert again["placements"][0]["id"] == bare["placements"][0]["id"]

    apart = import_finished_placements(
        conn,
        [
            daisy_item(
                start_s=start + 0.002,
                video_s3_key="swaps/home-alone/ha-2-later.mp4",
                version_label="Daisy v1",
            )
        ],
    )
    assert apart["placements"][0]["action"] == "created"
    assert apart["placements"][0]["id"] != first["placements"][0]["id"]

    payload = get_watch_payload(conn, title_slug="home-alone", season=1, episode=1)
    exact = [p for p in payload["placements"] if abs(p["start_s"] - start) < 1e-6]
    shifted = [p for p in payload["placements"] if abs(p["start_s"] - (start + 0.0004)) < 1e-6]
    assert [p["version_label"] for p in exact] == ["Daisy v2", "Swiss Miss", None]
    assert [p["slot_key"] for p in exact] == ["fridge-tub", "fridge-tub", None]
    assert exact[2]["clip_url"].endswith("swaps/home-alone/ha-2-bare-2.mp4")
    assert shifted[0]["version_label"] == "Daisy v1"
    assert shifted[0]["slot_key"] == "other-slot"
    assert shifted[0]["brand_id"]
    assert shifted[0]["clip_url"].endswith("swaps/home-alone/ha-2-new.mp4")
    forced = get_watch_payload(
        conn,
        title_slug="home-alone",
        season=1,
        episode=1,
        brand_id=shifted[0]["brand_id"],
    )
    assert {p["brand_name"] for p in forced["placements"]} == {"Daisy"}


def test_legacy_placement_table_gains_version_columns(catalog_env):
    conn = catalog_env
    conn.execute("ALTER TABLE placements DROP COLUMN version_label")
    conn.execute("ALTER TABLE placements DROP COLUMN slot_key")
    conn.commit()
    init_schema(conn)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(placements)")
    cols = {row[1] for row in cur.fetchall()}
    assert "version_label" in cols
    assert "slot_key" in cols


def test_placement_import_rejects_unknown_episode(catalog_env):
    conn = catalog_env
    with pytest.raises(ValueError, match="episode not found"):
        import_finished_placements(conn, [_placement(episode_id="missing")])
    assert fetchall_dicts(conn, "SELECT id FROM brands") == []
    assert fetchall_dicts(conn, "SELECT id FROM placements") == []


def test_moments_replace_is_generic_only_and_returns_backup(catalog_env):
    conn = catalog_env
    _title, ep = _episode(conn, episode_id="f507d5d1ce0f")
    brand = create_brand(conn, name="AT&T")
    generic = insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=1.0,
        end_s=2.0,
        region={"x": 1, "y": 2, "w": 3, "h": 4},
        whats_there="old plate",
        score=0.2,
        reason="old",
        frame_key="moments/old.jpg",
    )
    branded = insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=9.0,
        end_s=10.0,
        region={"x": 4, "y": 5, "w": 6, "h": 7},
        whats_there="landline phone",
        score=1.0,
        reason="Imported finished swap",
        brand_id=brand["id"],
    )
    other_title, other_ep = _episode(conn, slug="obsession", name="Obsession", episode_id="fd1fc7d38c4f")
    del other_title
    other = insert_moment(
        conn,
        episode_id=other_ep["id"],
        start_s=3.0,
        end_s=4.0,
        region={"x": 1, "y": 1, "w": 8, "h": 8},
        whats_there="keep me",
    )

    loaded = replace_generic_moments(
        conn,
        {
            "episode_id": ep["id"],
            "moments": [
                _moment(),
                _moment(
                    start_s=8.781,
                    end_s=10.01,
                    whats_there="ornament",
                    score=0.4,
                    region={"x": 1077, "y": 1002, "w": 67, "h": 55},
                    frame_s3_key="catalog/home-alone/s01e01/moments/moment_02.jpg",
                    frame_key="",
                ),
            ],
        },
    )
    assert [row["id"] for row in loaded["backup"]] == [generic["id"]]
    assert loaded["deleted"] == 1
    assert loaded["inserted"] == 2
    assert loaded["moments"][0]["whats_there"] == "Christmas ornament"
    assert json.loads(loaded["moments"][0]["region_json"]) == {
        "x": 1077,
        "y": 1003,
        "w": 63,
        "h": 54,
    }
    assert loaded["moments"][0]["frame_key"] == "catalog/home-alone/s01e01/moments/moment_01.jpg"
    assert loaded["moments"][0]["brand_id"] is None
    assert loaded["moments"][1]["frame_key"] == "catalog/home-alone/s01e01/moments/moment_02.jpg"
    assert loaded["moments"][1]["score"] == pytest.approx(0.4)

    generic_now = list_moments(conn, episode_id=ep["id"])
    assert [m["whats_there"] for m in generic_now] == ["Christmas ornament", "ornament"]
    assert all(m["brand_id"] is None for m in generic_now)
    still = fetchall_dicts(conn, "SELECT * FROM moments WHERE id = ?", (branded["id"],))
    assert still[0]["whats_there"] == "landline phone"
    assert still[0]["brand_id"] == brand["id"]
    assert fetchall_dicts(conn, "SELECT whats_there FROM moments WHERE id = ?", (other["id"],))[0][
        "whats_there"
    ] == "keep me"

    second = replace_generic_moments(
        conn,
        {"episode_id": ep["id"], "moments": [_moment(whats_there="mug", start_s=3, end_s=4)]},
    )
    assert [row["whats_there"] for row in second["backup"]] == ["Christmas ornament", "ornament"]
    assert [m["whats_there"] for m in list_moments(conn, episode_id=ep["id"])] == ["mug"]
    assert fetchall_dicts(conn, "SELECT id FROM moments WHERE id = ?", (branded["id"],))


def test_moments_replace_rolls_back(catalog_env, monkeypatch):
    conn = catalog_env
    _title, ep = _episode(conn)
    insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=1,
        end_s=2,
        region={"x": 1, "y": 1, "w": 2, "h": 2},
        whats_there="plate",
    )
    real = insert_moment
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr("product_placement.catalog.insert_moment", boom)
    with pytest.raises(RuntimeError, match="boom"):
        replace_generic_moments(
            conn,
            {
                "episode_id": ep["id"],
                "moments": [_moment(), _moment(start_s=4, end_s=5, whats_there="mug")],
            },
        )
    left = fetchall_dicts(
        conn, "SELECT whats_there FROM moments WHERE episode_id = ?", (ep["id"],)
    )
    assert [row["whats_there"] for row in left] == ["plate"]


def test_bad_moment_does_not_delete(catalog_env):
    conn = catalog_env
    _title, ep = _episode(conn)
    insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=1,
        end_s=2,
        region={"x": 1, "y": 1, "w": 2, "h": 2},
        whats_there="plate",
    )
    with pytest.raises(ValueError, match="greater than"):
        replace_generic_moments(
            conn,
            {"episode_id": ep["id"], "moments": [_moment(start_s=5, end_s=5)]},
        )
    assert fetchall_dicts(conn, "SELECT whats_there FROM moments")[0]["whats_there"] == "plate"
