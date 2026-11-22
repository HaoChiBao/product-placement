"""Integration tests against real Postgres + MinIO when RUN_PG_MINIO=1."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from product_placement.catalog import (
    create_brand,
    create_episode,
    create_title,
    insert_moment,
    list_titles,
    upsert_placement,
)
from product_placement.db import get_connection, init_schema, is_postgres
from product_placement.object_store import (
    get_object_store,
    reset_object_store,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PG_MINIO", "").strip() not in {"1", "true", "yes"},
    reason="Set RUN_PG_MINIO=1 with DATABASE_URL + AWS_* pointing at local Postgres/MinIO",
)


@pytest.fixture()
def pg_minio_env(tmp_path, monkeypatch):
    assert (os.environ.get("DATABASE_URL") or "").strip(), "DATABASE_URL required"
    assert (os.environ.get("AWS_S3_BUCKET_NAME") or "").strip(), "bucket required"
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # Leave DATABASE_URL / AWS_* from the environment (docker compose / CI).
    reset_object_store()
    conn = get_connection()
    assert is_postgres()
    init_schema(conn)
    # Clean tables for isolation
    for table in (
        "placements",
        "moments",
        "brand_assets",
        "brands",
        "episodes",
        "titles",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    yield conn
    conn.close()
    reset_object_store()


def test_postgres_rows_and_minio_objects(pg_minio_env, tmp_path):
    conn = pg_minio_env
    store = get_object_store()
    assert store.__class__.__name__ == "S3ObjectStore"

    title = create_title(conn, name="PG Film", kind="movie", slug="pg-film")
    video = tmp_path / "clip.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:d=3",
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
        name="Ep1",
        season=1,
        episode=1,
        video_path=video,
    )
    assert ep["video_key"]
    assert store.exists(ep["video_key"])
    assert store.exists(ep["poster_key"])

    # Prove object bytes + Range
    data = store.read_range(ep["video_key"], 0, 15)
    assert len(data) == 16
    assert store.read_range(ep["video_key"], 0, 3) == data[:4]

    moment = insert_moment(
        conn,
        episode_id=ep["id"],
        start_s=0.5,
        end_s=3.0,
        region={"x": 10, "y": 10, "w": 40, "h": 40},
        whats_there="cup",
        score=0.5,
        reason="test",
        frame_key=None,
        frame_w=640,
        frame_h=360,
    )
    brand = create_brand(
        conn,
        name="MinIO Cola",
        brief="Prefer cups",
        asset_paths=[str(Path("assets/samples/soda_can.png").resolve())],
    )
    pl = upsert_placement(
        conn, brand_id=brand["id"], moment_id=moment["id"], status="approved"
    )
    assert pl["status"] == "queued"

    from product_placement.db import fetchall_dicts, fetchone_dict

    assert len(fetchall_dicts(conn, "SELECT id FROM titles")) >= 1
    assert len(fetchall_dicts(conn, "SELECT id FROM episodes")) >= 1
    got = fetchone_dict(conn, "SELECT status FROM placements WHERE id = ?", (pl["id"],))
    assert got["status"] == "queued"
    assert list_titles(conn)
