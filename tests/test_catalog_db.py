"""Catalog DB, local object store, brand migrate, placement confirm gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from product_placement.catalog import (
    create_brand,
    create_episode,
    create_title,
    insert_moment,
    list_moments,
    list_titles,
    seed_blender_open_movies,
    upsert_placement,
)
from product_placement.db import (
    get_connection,
    init_schema,
    migrate_file_brands,
)
from product_placement.jobs import run_placement_swap_job
from product_placement.object_store import (
    LocalObjectStore,
    get_object_store,
    reset_object_store,
)


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
    yield conn, data
    conn.close()
    reset_object_store()


def test_init_schema_sqlite(catalog_env):
    conn, data = catalog_env
    db_path = data / "catalog.sqlite"
    assert db_path.is_file()
    # Tables exist
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = {r[0] for r in cur.fetchall()}
    assert {
        "titles",
        "episodes",
        "moments",
        "brands",
        "brand_assets",
        "placements",
    } <= names


def test_create_title_episode_moment_brand_placement(catalog_env, tmp_path):
    conn, data = catalog_env

    title = create_title(
        conn,
        name="Demo Film",
        kind="movie",
        synopsis="A short demo",
        attribution="CC-BY Test",
    )
    assert title["slug"] == "demo-film"
    assert list_titles(conn)

    # Tiny fake video via ffmpeg color source
    video = tmp_path / "clip.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    ep = create_episode(
        conn,
        title_id=title["id"],
        name="Episode 1",
        season=1,
        episode=1,
        video_path=video,
    )
    assert ep["suggest_status"] == "queued"
    assert ep["video_key"]
    assert get_object_store().exists(ep["video_key"])

    moment = insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=0.5,
        end_s=3.0,
        region={"x": 10, "y": 10, "w": 40, "h": 40},
        whats_there="cup",
        score=0.8,
        reason="test",
        frame_w=320,
        frame_h=180,
    )
    assert moment["id"]

    # Product image for brand
    product = tmp_path / "can.png"
    product.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    brand = create_brand(
        conn,
        name="Soda Co",
        brief="Prefer cups. Avoid beds.",
        prefer=["cup"],
        avoid=["bed"],
        asset_paths=[product],
    )
    assert brand["id"]
    assert brand["assets"]

    placement = upsert_placement(
        conn,
        brand_id=brand["id"],
        moment_id=moment["id"],
        status="approved",
    )
    assert placement["status"] == "queued"

    rejected = upsert_placement(
        conn,
        brand_id=brand["id"],
        moment_id=moment["id"],
        status="rejected",
    )
    assert rejected["status"] == "rejected"
    assert rejected["id"] == placement["id"]

    moments = list_moments(conn, episode_id=ep["id"], brand_id=brand["id"])
    assert len(moments) == 1
    assert "brand_fit" in moments[0]


def test_object_store_local_put_get_range(catalog_env):
    conn, data = catalog_env
    store = LocalObjectStore(data / "objects")
    key = "demo/hello.txt"
    store.put_bytes(b"abcdefghijklmnopqrstuvwxyz", key, content_type="text/plain")
    assert store.exists(key)
    dest = data / "out.txt"
    store.get_file(key, dest)
    assert dest.read_bytes() == b"abcdefghijklmnopqrstuvwxyz"
    # Inclusive range: start=0 end=3 → abcd
    assert store.read_range(key, 0, 3) == b"abcd"
    assert store.read_range(key, 23, 25) == b"xyz"
    url = store.public_or_presigned_url(key)
    assert url.startswith("file:")


def test_migrate_file_brands(catalog_env, tmp_path):
    conn, data = catalog_env
    brand_id = "fakecola01"
    bdir = data / "brands" / brand_id
    bdir.mkdir(parents=True)
    asset = bdir / "logo.png"
    asset.write_bytes(b"fake-png-bytes")
    (bdir / "brand.json").write_text(
        json.dumps(
            {
                "id": brand_id,
                "name": "Fake Cola",
                "brief": "Prefer bottles",
                "assets": ["logo.png"],
                "rules": {"prefer": ["bottle"], "avoid": [], "placement": "resting"},
            }
        )
        + "\n"
    )

    imported = migrate_file_brands(conn)
    assert brand_id in imported
    row = conn.cursor()
    row.execute("SELECT name FROM brands WHERE id = ?", (brand_id,))
    assert row.fetchone()[0] == "Fake Cola"
    row.execute("SELECT object_key FROM brand_assets WHERE brand_id = ?", (brand_id,))
    key = row.fetchone()[0]
    assert get_object_store().exists(key)

    # Second migrate is a no-op
    assert migrate_file_brands(conn) == []


def test_placement_paid_requires_confirm(catalog_env, monkeypatch):
    conn, data = catalog_env
    # Minimal placement row so the job can load it before the confirm gate...
    # run_placement_swap_job checks confirm before/with test_mode.
    with pytest.raises(ValueError, match="confirm"):
        run_placement_swap_job("missing", confirm=False, test_mode=True)

    with pytest.raises(ValueError, match="confirm"):
        run_placement_swap_job("missing", confirm=False, test_mode=False)

    with pytest.raises(ValueError, match="confirm"):
        # paid path without confirm
        run_placement_swap_job("missing", confirm=False, test_mode=False)


def test_seed_blender_mocked(catalog_env, tmp_path, monkeypatch):
    conn, data = catalog_env

    # Build a tiny local "download" that seed will trim.
    fake_src = tmp_path / "source.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x180:d=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(fake_src),
        ],
        check=True,
        capture_output=True,
    )

    def fake_download(url: str, dest: Path, *, timeout: float = 180.0) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fake_src.read_bytes())
        return dest

    result = seed_blender_open_movies(
        conn, force=True, download_fn=fake_download, cache_dir=tmp_path / "cache"
    )
    assert result["skipped"] is False
    assert result["episodes_created"] >= 1
    titles = list_titles(conn)
    assert any(t["slug"] == "big-buck-bunny" for t in titles)
    assert all(
        (t.get("attribution") or "").startswith("CC-BY") for t in titles if t["slug"]
    )

    # Second call without force skips
    again = seed_blender_open_movies(conn, force=False, download_fn=fake_download)
    assert again["skipped"] is True
