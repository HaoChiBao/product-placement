"""Catalog DB, local object store, brand migrate, placement confirm gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from product_placement.catalog import (
    _extract_poster_frame,
    _mean_luma,
    backfill_missing_posters,
    catalog_video_key,
    create_brand,
    create_episode,
    create_title,
    episode_is_watchable,
    get_title,
    get_watch_payload,
    humanize_movie_filename,
    import_movies_from_store,
    import_movies_with_lock,
    insert_moment,
    list_episodes,
    list_moments,
    list_titles,
    migrate_catalog_object_keys,
    movies_import_lock,
    register_preloaded_episode,
    seed_blender_open_movies,
    slugify,
    upsert_placement,
)
from product_placement.catalog_keys import (
    legacy_video_key,
    original_video_key,
    placement_clip_key,
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
    assert ep["video_key"] == "catalog/demo-film/s01e01/original.mp4"
    assert ep["poster_key"] == "catalog/demo-film/s01e01/poster.jpg"
    assert get_object_store().exists(ep["video_key"])
    refreshed = get_title(conn, id=title["id"])
    assert refreshed["poster_key"] == "catalog/demo-film/poster.jpg"

    preload_key = catalog_video_key("demo-film", 1, 2)
    get_object_store().put_bytes(b"not-a-real-mp4", preload_key, content_type="video/mp4")
    attached = register_preloaded_episode(
        conn,
        name="Demo Film",
        kind="movie",
        slug="demo-film",
        season=1,
        episode=2,
        episode_name="Credits",
        video_key=preload_key,
    )
    assert attached["episode"]["video_key"] == preload_key
    assert attached["episode"]["poster_key"] is None
    again = register_preloaded_episode(
        conn,
        name="Demo Film",
        kind="movie",
        slug="demo-film",
        season=1,
        episode=2,
        video_key=preload_key,
    )
    assert again["episode"]["video_key"] == preload_key
    assert again["video_key"] == preload_key

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
    assert placement.get("spawn") is True

    # Re-approve while queued must not request another swap spawn.
    again = upsert_placement(
        conn,
        brand_id=brand["id"],
        moment_id=moment["id"],
        status="approved",
    )
    assert again["status"] == "queued"
    assert again.get("spawn") is False
    assert again["id"] == placement["id"]

    rejected = upsert_placement(
        conn,
        brand_id=brand["id"],
        moment_id=moment["id"],
        status="rejected",
    )
    assert rejected["status"] == "rejected"
    assert rejected["id"] == placement["id"]

    # Re-approve after reject should spawn again.
    requeued = upsert_placement(
        conn,
        brand_id=brand["id"],
        moment_id=moment["id"],
        status="approved",
    )
    assert requeued["status"] == "queued"
    assert requeued.get("spawn") is True

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
    assert url == "/api/media/demo/hello.txt"


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


def test_claim_episode_suggest_is_exclusive(catalog_env, tmp_path):
    from product_placement.jobs import _claim_episode_suggest, reset_stale_suggest_running

    conn, data = catalog_env
    title = create_title(conn, name="Claim Film", kind="movie")
    video = tmp_path / "c.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:d=1",
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
        name="E1",
        season=1,
        episode=1,
        video_path=video,
    )
    assert _claim_episode_suggest(conn, ep["id"]) is True
    assert _claim_episode_suggest(conn, ep["id"]) is False
    # Crash recovery flips running → queued, then claim works again.
    assert reset_stale_suggest_running(conn) == 1
    assert _claim_episode_suggest(conn, ep["id"]) is True


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
    bunny = next(t for t in titles if t["slug"] == "big-buck-bunny")
    assert bunny["poster_key"] == "catalog/big-buck-bunny/poster.jpg"
    steel = next(t for t in titles if t["slug"] == "tears-of-steel")
    assert steel["poster_key"] == "catalog/tears-of-steel/poster.jpg"
    assert all(
        (t.get("attribution") or "").startswith("CC-BY") for t in titles if t["slug"]
    )

    # Second call without force creates nothing new (but does not hard-bail early).
    again = seed_blender_open_movies(conn, force=False, download_fn=fake_download)
    assert again["episodes_created"] == 0
    assert again["skipped"] is True


def _write_color_mp4(path: Path, *, color: str, duration: float = 2.0, size: str = "320x180"):
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:d={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_humanize_movie_filename_and_slug():
    assert humanize_movie_filename("the_matrix.mp4") == "The Matrix"
    assert slugify(humanize_movie_filename("the_matrix.mp4")) == "the-matrix"
    assert humanize_movie_filename("foo-bar_baz.mov") == "Foo Bar Baz"


def test_import_movies_from_local_object_store(catalog_env, tmp_path):
    conn, data = catalog_env
    store = get_object_store()

    video = tmp_path / "the_matrix.mp4"
    _write_color_mp4(video, color="blue", duration=3.0)
    store.put_file(video, "movies/the_matrix.mp4", content_type="video/mp4")
    store.put_bytes(b"not-video", "movies/readme.txt", content_type="text/plain")

    result = import_movies_from_store(conn)
    assert result["imported"] == 1
    assert result["ignored_non_video"] >= 1
    item = result["items"][0]
    assert item["name"] == "The Matrix"
    assert item["slug"] == "the-matrix"
    ep = item["episode"]
    assert ep["video_key"] == "catalog/the-matrix/s01e01/original.mp4"
    assert ep["season"] == 1
    assert ep["episode_number"] == 1
    assert ep["suggest_status"] == "queued"
    assert ep["poster_key"]
    assert store.exists(ep["poster_key"])
    assert store.exists(ep["video_key"])
    # James's upload stays put.
    assert store.exists("movies/the_matrix.mp4")
    title = get_title(conn, slug="the-matrix")
    assert title is not None
    assert title["kind"] == "movie"
    assert title["poster_key"]
    assert store.exists(title["poster_key"])

    again = import_movies_from_store(conn)
    assert again["imported"] == 0
    assert again["skipped_existing"] >= 1
    assert again["skipped"] is True


def test_import_movies_remuxes_mkv_and_transcodes_mpeg4(catalog_env, tmp_path):
    import subprocess

    conn, data = catalog_env
    store = get_object_store()

    # H.264 inside matroska → remux to original.mp4
    mkv = tmp_path / "blade_runner.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x180:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(mkv),
        ],
        check=True,
        capture_output=True,
    )
    store.put_file(mkv, "movies/blade_runner.mkv", content_type="video/x-matroska")

    # Non-H.264 (mpeg4) → transcode
    avi = tmp_path / "old_codec.avi"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:d=2",
            "-c:v",
            "mpeg4",
            str(avi),
        ],
        check=True,
        capture_output=True,
    )
    store.put_file(avi, "movies/old_codec.avi", content_type="video/x-msvideo")

    result = import_movies_from_store(conn)
    assert result["imported"] == 2
    by_slug = {i["slug"]: i for i in result["items"]}
    assert by_slug["blade-runner"]["video_key"] == original_video_key("blade-runner", 1, 1)
    assert by_slug["old-codec"]["video_key"] == original_video_key("old-codec", 1, 1)
    assert store.exists(by_slug["blade-runner"]["video_key"])
    assert store.exists(by_slug["old-codec"]["video_key"])
    assert store.exists("movies/blade_runner.mkv")
    assert store.exists("movies/old_codec.avi")

    again = import_movies_from_store(conn)
    assert again["imported"] == 0
    assert again["skipped_existing"] >= 2


def test_migrate_catalog_keys_video_and_placement(catalog_env, tmp_path):
    conn, data = catalog_env
    store = get_object_store()
    title = create_title(conn, name="Legacy Film", kind="movie", slug="legacy-film")
    video = tmp_path / "clip.mp4"
    _write_color_mp4(video, color="blue", duration=1.0)
    legacy = legacy_video_key("legacy-film", 1, 1)
    store.put_file(video, legacy, content_type="video/mp4")
    ep = create_episode(
        conn,
        title_id=title["id"],
        name="Legacy Film",
        season=1,
        episode=1,
        video_key=legacy,
        suggest_status="queued",
    )
    assert ep["video_key"] == legacy

    brand = create_brand(conn, name="Acme Cola", brief="x")
    moment = insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=0.0,
        end_s=1.0,
        region={"x": 1, "y": 1, "w": 10, "h": 10},
    )
    from product_placement.catalog import upsert_placement

    placement = upsert_placement(
        conn, brand_id=brand["id"], moment_id=moment["id"], status="approved"
    )
    old_clip = f"placements/{placement['id']}/clip.mp4"
    store.put_bytes(b"fake-clip", old_clip, content_type="video/mp4")
    conn.execute(
        "UPDATE placements SET clip_key = ?, status = ? WHERE id = ?",
        (old_clip, "ready", placement["id"]),
    )
    conn.commit()

    out = migrate_catalog_object_keys(conn)
    assert out["moved_count"] >= 1
    refreshed = conn.cursor()
    refreshed.execute("SELECT video_key FROM episodes WHERE id = ?", (ep["id"],))
    assert refreshed.fetchone()[0] == original_video_key("legacy-film", 1, 1)
    assert store.exists(original_video_key("legacy-film", 1, 1))
    assert not store.exists(legacy)

    refreshed.execute("SELECT clip_key FROM placements WHERE id = ?", (placement["id"],))
    new_clip = refreshed.fetchone()[0]
    assert new_clip == placement_clip_key(
        "legacy-film", 1, 1, "acme-cola", placement["id"]
    )
    assert store.exists(new_clip)
    assert not store.exists(old_clip)

    again = migrate_catalog_object_keys(conn)
    assert again["moved_count"] == 0


def test_watchable_titles_hide_orphans_and_processing(catalog_env, tmp_path):
    conn, data = catalog_env
    orphan = create_title(conn, name="Orphan", kind="movie", slug="orphan")
    assert orphan
    assert list_titles(conn, watchable_only=True) == []

    title = create_title(conn, name="Ready Film", kind="movie", slug="ready-film")
    video = tmp_path / "r.mp4"
    _write_color_mp4(video, color="blue", duration=1.0)
    create_episode(
        conn,
        title_id=title["id"],
        name="Ready Film",
        season=1,
        episode=1,
        video_path=video,
        suggest_status="queued",
    )
    watchable = list_titles(conn, watchable_only=True)
    assert [t["slug"] for t in watchable] == ["ready-film"]

    processing = create_title(conn, name="Processing", kind="movie", slug="processing")
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "poster_key, duration_s, suggest_status, created_at) "
        "VALUES (?, ?, 1, 1, ?, ?, NULL, 0, 'processing', ?)",
        ("epproc01", processing["id"], "Processing", "movies/x.mp4", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    watchable = list_titles(conn, watchable_only=True)
    assert [t["slug"] for t in watchable] == ["ready-film"]

    failed = create_title(conn, name="Failed Film", kind="movie", slug="failed-film")
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "poster_key, duration_s, suggest_status, created_at, import_attempts) "
        "VALUES (?, ?, 1, 1, ?, ?, NULL, 0, 'failed', ?, 2)",
        ("epfail01", failed["id"], "Failed Film", "movies/bad.mkv", "2020-01-01T00:00:00Z"),
    )
    conn.commit()
    watchable = list_titles(conn, watchable_only=True)
    assert [t["slug"] for t in watchable] == ["ready-film"]

    with pytest.raises(FileNotFoundError, match="episode not found"):
        get_watch_payload(conn, title_slug="orphan", season=1, episode=1)
    with pytest.raises(FileNotFoundError, match="episode not found"):
        get_watch_payload(conn, title_slug="processing", season=1, episode=1)
    with pytest.raises(FileNotFoundError, match="episode not found"):
        get_watch_payload(conn, title_slug="failed-film", season=1, episode=1)


def test_seed_repairs_orphan_title(catalog_env, tmp_path):
    conn, data = catalog_env
    create_title(
        conn,
        name="Big Buck Bunny",
        kind="movie",
        slug="big-buck-bunny",
        attribution="CC-BY Blender Foundation",
    )
    assert list_titles(conn, watchable_only=True) == []

    fake_src = tmp_path / "source.mp4"
    _write_color_mp4(fake_src, color="green", duration=3.0)

    def fake_download(url: str, dest: Path, *, timeout: float = 180.0) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fake_src.read_bytes())
        return dest

    result = seed_blender_open_movies(
        conn, force=False, download_fn=fake_download, cache_dir=tmp_path / "cache"
    )
    assert result["episodes_created"] >= 1
    bunny = get_title(conn, slug="big-buck-bunny")
    assert bunny
    from product_placement.catalog import list_episodes

    eps = list_episodes(conn, title_id=bunny["id"])
    assert eps
    assert eps[0]["video_key"] == original_video_key("big-buck-bunny", 1, 1)
    assert get_object_store().exists(eps[0]["video_key"])
    assert any(t["slug"] == "big-buck-bunny" for t in list_titles(conn, watchable_only=True))


def test_extract_poster_skips_near_black_frames(tmp_path):
    """Prefer a bright mid-film frame over a dark opening."""
    import subprocess

    dark = tmp_path / "dark.mp4"
    bright = tmp_path / "bright.mp4"
    video = tmp_path / "dark_then_bright.mp4"
    # Dark opening (~1s) then bright (~3s) so 25%/50% land in the bright half.
    _write_color_mp4(dark, color="black", duration=1.0)
    _write_color_mp4(bright, color="white", duration=3.0)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(dark),
            "-i",
            str(bright),
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    poster = tmp_path / "poster.jpg"
    _extract_poster_frame(video, poster, duration_s=4.0)
    assert poster.is_file()
    assert _mean_luma(poster) >= 18.0


def test_backfill_missing_posters(catalog_env, tmp_path):
    conn, data = catalog_env
    store = get_object_store()
    title = create_title(conn, name="No Poster Film", kind="movie", slug="no-poster-film")
    video = tmp_path / "clip.mp4"
    _write_color_mp4(video, color="orange", duration=2.0)
    key = "movies/no_poster_film.mp4"
    store.put_file(video, key, content_type="video/mp4")
    # Register without going through create_episode poster path by inserting a row
    # that already exists via preload, then clearing poster keys.
    attached = register_preloaded_episode(
        conn,
        name="No Poster Film",
        kind="movie",
        slug="no-poster-film",
        season=1,
        episode=1,
        video_key=key,
    )
    ep = attached["episode"]
    # Simulate missing posters (legacy / failed extract).
    conn.execute("UPDATE episodes SET poster_key = NULL WHERE id = ?", (ep["id"],))
    conn.execute("UPDATE titles SET poster_key = NULL WHERE id = ?", (title["id"],))
    conn.commit()
    if ep.get("poster_key") and store.exists(ep["poster_key"]):
        store.delete(ep["poster_key"])

    out = backfill_missing_posters(conn)
    assert ep["id"] in out["updated"]
    refreshed_ep = conn.cursor()
    refreshed_ep.execute("SELECT poster_key FROM episodes WHERE id = ?", (ep["id"],))
    pkey = refreshed_ep.fetchone()[0]
    assert pkey
    assert store.exists(pkey)
    refreshed_title = get_title(conn, id=title["id"])
    assert refreshed_title["poster_key"]
    assert store.exists(refreshed_title["poster_key"])

    again = backfill_missing_posters(conn)
    assert again["updated_count"] == 0


def test_episode_is_watchable_and_list_filters(catalog_env, tmp_path):
    conn, data = catalog_env
    title = create_title(conn, name="Filter Film", kind="movie", slug="filter-film")
    video = tmp_path / "ok.mp4"
    _write_color_mp4(video, color="blue", duration=1.0)
    ready = create_episode(
        conn,
        title_id=title["id"],
        name="Ready",
        season=1,
        episode=1,
        video_path=video,
        suggest_status="queued",
    )
    assert episode_is_watchable(ready)
    assert list_episodes(conn, title_id=title["id"], watchable_only=True)

    conn.execute(
        "UPDATE episodes SET suggest_status = ?, video_key = ? WHERE id = ?",
        ("processing", "movies/filter_film.mp4", ready["id"]),
    )
    conn.commit()
    ep = list_episodes(conn, title_id=title["id"])[0]
    assert not episode_is_watchable(ep)
    assert list_episodes(conn, title_id=title["id"], watchable_only=True) == []


def test_import_movies_retries_failed_until_max(catalog_env, tmp_path, monkeypatch):
    conn, data = catalog_env
    store = get_object_store()
    video = tmp_path / "retry_me.mp4"
    _write_color_mp4(video, color="red", duration=1.0)
    store.put_file(video, "movies/retry_me.mp4", content_type="video/mp4")

    calls = {"n": 0}

    def boom(src, dest):
        calls["n"] += 1
        raise RuntimeError("transcode boom")

    monkeypatch.setattr(
        "product_placement.catalog.prepare_browser_mp4", boom
    )
    monkeypatch.setenv("IMPORT_MAX_ATTEMPTS", "2")

    first = import_movies_from_store(conn)
    assert first["imported"] == 0
    assert first["failed"]
    ep = list_episodes(conn, title_id=get_title(conn, slug="retry-me")["id"])[0]
    assert ep["suggest_status"] == "failed"
    assert int(ep["import_attempts"] or 0) == 1
    assert not list_titles(conn, watchable_only=True)

    second = import_movies_from_store(conn)
    assert second["imported"] == 0
    assert second["failed"]
    ep = list_episodes(conn, title_id=get_title(conn, slug="retry-me")["id"])[0]
    assert int(ep["import_attempts"] or 0) == 2

    third = import_movies_from_store(conn)
    assert third["imported"] == 0
    assert third.get("skipped_max_attempts", 0) >= 1
    assert calls["n"] == 2


def test_import_movies_reuses_complete_etag_download(catalog_env, tmp_path, monkeypatch):
    conn, data = catalog_env
    store = get_object_store()
    video = tmp_path / "etag_film.mp4"
    _write_color_mp4(video, color="green", duration=1.0)
    store.put_file(video, "movies/etag_film.mp4", content_type="video/mp4")

    downloads = {"n": 0}
    real_stream = __import__(
        "product_placement.catalog", fromlist=["_stream_store_to_file"]
    )._stream_store_to_file

    def counted(store_obj, key, dest):
        downloads["n"] += 1
        return real_stream(store_obj, key, dest)

    monkeypatch.setattr(
        "product_placement.catalog._stream_store_to_file", counted
    )

    boom_once = {"n": 0}
    real_prepare = __import__(
        "product_placement.catalog", fromlist=["prepare_browser_mp4"]
    ).prepare_browser_mp4

    def flaky(src, dest):
        boom_once["n"] += 1
        if boom_once["n"] == 1:
            # Leave the source download in place, fail the prepare.
            raise RuntimeError("first prepare failed")
        return real_prepare(src, dest)

    monkeypatch.setattr("product_placement.catalog.prepare_browser_mp4", flaky)

    first = import_movies_from_store(conn)
    assert first["imported"] == 0
    assert downloads["n"] == 1

    second = import_movies_from_store(conn)
    assert second["imported"] == 1
    assert downloads["n"] == 1  # resumed same ETag download
    assert list_titles(conn, watchable_only=True)


def test_import_movies_lock_excludes_concurrent(catalog_env):
    conn, data = catalog_env
    with movies_import_lock(blocking=True) as held:
        assert held
        skipped = import_movies_with_lock(blocking=False, conn=conn)
        assert skipped["skipped"] is True
        assert skipped.get("reason") == "import already running"
