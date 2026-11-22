"""Save a finished suggest job onto a catalog episode."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from product_placement.catalog import (
    adopt_suggest_job,
    create_title,
    insert_moment,
    list_moments,
    migrate_catalog_object_keys,
)
from product_placement.catalog_keys import moment_frame_key
from product_placement.db import get_connection, init_schema, utc_now
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
    yield conn, data
    conn.close()
    reset_object_store()


def _jpeg(path: Path, value: int) -> None:
    ok, buf = cv2.imencode(".jpg", np.full((8, 8, 3), value, np.uint8))
    assert ok
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.tobytes())


def test_adopt_suggest_job_replaces_surface_spots(catalog_env):
    conn, data = catalog_env
    title = create_title(conn, name="Obsession", kind="movie", source="hosted")
    episode_id = "fd1fc7d38c4f"
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "duration_s, suggest_status, created_at) VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?)",
        (
            episode_id,
            title["id"],
            "Obsession",
            "catalog/obsession/s01e01/original.mp4",
            85.3,
            "ready",
            utc_now(),
        ),
    )
    conn.commit()
    insert_moment(
        conn,
        episode_id=episode_id,
        start_s=63.7,
        end_s=67.0,
        region={"x": 1, "y": 2, "w": 3, "h": 4},
        whats_there="foreground marble tabletop",
        score=0.82,
        reason="surface",
    )

    job_id = "24aa1f69-134"
    job = data / "jobs" / job_id
    frames = job / "frames"
    _jpeg(frames / "moment_01.jpg", 32)
    outside = data / "moment_01.jpg"
    _jpeg(outside, 200)
    (job / "moments.json").write_text(
        json.dumps(
            {
                "result": {
                    "moments": [
                        {
                            "start_s": 55.182,
                            "end_s": 57.201,
                            "frame": str(outside),
                            "frame_w": 1920,
                            "frame_h": 1080,
                            "region": {"x": 680, "y": 797, "w": 211, "h": 112},
                            "whats_there": "plate with bread",
                            "score": 0.725,
                            "reason": "plate with bread; plate; swap with plate",
                        }
                    ]
                }
            }
        )
    )

    saved = adopt_suggest_job(conn, episode_id=episode_id, job_id=job_id)
    assert saved["count"] == 1
    rows = list_moments(conn, episode_id=episode_id)
    assert [row["whats_there"] for row in rows] == ["plate with bread"]
    assert rows[0]["region"] == {"x": 680, "y": 797, "w": 211, "h": 112}
    assert rows[0]["frame_key"] == "catalog/obsession/s01e01/moments/moment_01.jpg"
    with get_object_store().open_stream(rows[0]["frame_key"]) as handle:
        stored = handle.read()
    assert stored == (frames / "moment_01.jpg").read_bytes()
    assert stored != outside.read_bytes()

    with pytest.raises(ValueError, match="invalid job id"):
        adopt_suggest_job(conn, episode_id=episode_id, job_id="../etc/passwd")


def test_failed_upload_keeps_existing_moments(catalog_env, monkeypatch):
    conn, data = catalog_env
    title = create_title(conn, name="Home Alone", kind="movie", source="hosted")
    episode_id = "f507d5d1ce0f"
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "duration_s, suggest_status, created_at) VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?)",
        (
            episode_id,
            title["id"],
            "Home Alone",
            "catalog/home-alone/s01e01/original.mp4",
            145.0,
            "ready",
            utc_now(),
        ),
    )
    conn.commit()
    insert_moment(
        conn,
        episode_id=episode_id,
        start_s=1.0,
        end_s=2.0,
        region={"x": 0, "y": 0, "w": 10, "h": 10},
        whats_there="wooden floor",
        score=0.8,
    )
    job_id = "6d0cd540-ae4"
    frames = data / "jobs" / job_id / "frames"
    _jpeg(frames / "moment_01.jpg", 32)
    (data / "jobs" / job_id / "moments.json").write_text(
        json.dumps(
            {
                "moments": [
                    {
                        "start_s": 63.0,
                        "end_s": 64.0,
                        "frame": "moment_01.jpg",
                        "region": {"x": 1, "y": 1, "w": 2, "h": 2},
                        "whats_there": "paint can",
                        "score": 0.9,
                    }
                ]
            }
        )
    )

    def _boom(*_args, **_kwargs):
        raise OSError("store down")

    monkeypatch.setattr(get_object_store(), "put_bytes", _boom)
    with pytest.raises(OSError, match="store down"):
        adopt_suggest_job(conn, episode_id=episode_id, job_id=job_id)
    rows = list_moments(conn, episode_id=episode_id)
    assert [row["whats_there"] for row in rows] == ["wooden floor"]


def test_replaced_stills_survive_key_migration(catalog_env):
    conn, data = catalog_env
    title = create_title(conn, name="The Office", kind="tv", source="hosted")
    episode_id = "68d257858333"
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "duration_s, suggest_status, created_at) VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?)",
        (
            episode_id,
            title["id"],
            "The Office",
            "catalog/the-office/s01e01/original.mp4",
            296.0,
            "ready",
            utc_now(),
        ),
    )
    conn.commit()
    store = get_object_store()
    old_key = moment_frame_key("the-office", 1, 1, 1)
    extra_key = moment_frame_key("the-office", 1, 1, 2)
    store.put_bytes(b"old-surface", old_key, content_type="image/jpeg")
    store.put_bytes(b"old-extra", extra_key, content_type="image/jpeg")
    insert_moment(
        conn,
        episode_id=episode_id,
        start_s=1.0,
        end_s=2.0,
        region={"x": 0, "y": 0, "w": 8, "h": 8},
        whats_there="desk",
        frame_key=old_key,
    )
    job_id = "e48bec48-bc4"
    frames = data / "jobs" / job_id / "frames"
    _jpeg(frames / "moment_01.jpg", 90)
    (data / "jobs" / job_id / "moments.json").write_text(
        json.dumps(
            {
                "moments": [
                    {
                        "start_s": 12.0,
                        "end_s": 13.0,
                        "frame": "moment_01.jpg",
                        "region": {"x": 4, "y": 4, "w": 8, "h": 8},
                        "whats_there": "blue and white tumbler",
                        "score": 0.8,
                    }
                ]
            }
        )
    )

    adopt_suggest_job(conn, episode_id=episode_id, job_id=job_id)
    rows = list_moments(conn, episode_id=episode_id)
    assert rows[0]["frame_key"] == old_key
    with store.open_stream(old_key) as handle:
        assert handle.read() == (frames / "moment_01.jpg").read_bytes()
    assert not store.exists(extra_key)

    migrated = migrate_catalog_object_keys(conn)
    assert f"moment:{rows[0]['id']}" not in migrated["moved"]
    rows = list_moments(conn, episode_id=episode_id)
    assert rows[0]["whats_there"] == "blue and white tumbler"
    assert rows[0]["frame_key"] == old_key
    with store.open_stream(old_key) as handle:
        assert handle.read() == (frames / "moment_01.jpg").read_bytes()


def test_migration_prefers_adopted_still_over_leftover_index(catalog_env):
    conn, _data = catalog_env
    title = create_title(conn, name="Obsession", kind="movie", source="hosted")
    episode_id = "fd1fc7d38c4f"
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "duration_s, suggest_status, created_at) VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?)",
        (
            episode_id,
            title["id"],
            "Obsession",
            "catalog/obsession/s01e01/original.mp4",
            85.0,
            "ready",
            utc_now(),
        ),
    )
    conn.commit()
    store = get_object_store()
    leftover = moment_frame_key("obsession", 1, 1, 1)
    adopted = "catalog/obsession/s01e01/moments/abc123.jpg"
    store.put_bytes(b"old-surface", leftover, content_type="image/jpeg")
    store.put_bytes(b"new-bread", adopted, content_type="image/jpeg")
    moment = insert_moment(
        conn,
        episode_id=episode_id,
        start_s=55.0,
        end_s=57.0,
        region={"x": 1, "y": 1, "w": 2, "h": 2},
        whats_there="plate with bread",
        frame_key=adopted,
    )

    migrate_catalog_object_keys(conn)
    rows = list_moments(conn, episode_id=episode_id)
    assert rows[0]["id"] == moment["id"]
    assert rows[0]["frame_key"] == leftover
    with store.open_stream(leftover) as handle:
        assert handle.read() == b"new-bread"
    assert not store.exists(adopted)
