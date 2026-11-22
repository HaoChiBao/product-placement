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
)
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


def _jpeg(path: Path) -> None:
    ok, buf = cv2.imencode(".jpg", np.full((8, 8, 3), 32, np.uint8))
    assert ok
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
    frames.mkdir(parents=True)
    _jpeg(frames / "moment_01.jpg")
    (job / "moments.json").write_text(
        json.dumps(
            {
                "result": {
                    "moments": [
                        {
                            "start_s": 55.182,
                            "end_s": 57.201,
                            "frame": "/data/jobs/24aa1f69-134/frames/moment_01.jpg",
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
    assert get_object_store().exists(rows[0]["frame_key"])

    with pytest.raises(ValueError, match="invalid job id"):
        adopt_suggest_job(conn, episode_id=episode_id, job_id="../etc/passwd")
