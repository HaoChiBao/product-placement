"""Catalog CRUD + JSON CLI for the streaming product-placement POC."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from product_placement.db import (
    Connection,
    data_dir,
    fetchall_dicts,
    fetchone_dict,
    get_connection,
    init_schema,
    migrate_file_brands,
    new_id,
    utc_now,
)
from product_placement.object_store import get_object_store

log = logging.getLogger(__name__)

ATTRIBUTION_BLENDER = "CC-BY Blender Foundation"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "title"


def catalog_episode_prefix(slug: str, season: int, episode: int) -> str:
    """Stable object prefix so a preload can land before the row exists."""
    return f"catalog/{slug}/s{int(season):02d}e{int(episode):02d}"


def catalog_video_key(slug: str, season: int, episode: int, ext: str = ".mp4") -> str:
    suffix = ext if ext.startswith(".") else f".{ext}"
    return f"{catalog_episode_prefix(slug, season, episode)}/video{suffix}"


def catalog_poster_key(slug: str, season: int, episode: int) -> str:
    return f"{catalog_episode_prefix(slug, season, episode)}/poster.jpg"


def catalog_title_poster_key(slug: str) -> str:
    return f"catalog/{slug}/poster.jpg"


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def list_titles(conn: Connection) -> list[dict[str, Any]]:
    return fetchall_dicts(conn, "SELECT * FROM titles ORDER BY created_at")


def get_title(
    conn: Connection, *, id: str | None = None, slug: str | None = None
) -> dict[str, Any] | None:
    if id:
        return fetchone_dict(conn, "SELECT * FROM titles WHERE id = ?", (id,))
    if slug:
        return fetchone_dict(conn, "SELECT * FROM titles WHERE slug = ?", (slug,))
    raise ValueError("id or slug required")


def create_title(
    conn: Connection,
    *,
    name: str,
    kind: str = "movie",
    synopsis: str = "",
    attribution: str = "",
    poster_key: str | None = None,
    slug: str | None = None,
    title_id: str | None = None,
) -> dict[str, Any]:
    tid = title_id or new_id()
    slug_val = slug or slugify(name)
    # Ensure unique slug
    base = slug_val
    n = 2
    while fetchone_dict(conn, "SELECT id FROM titles WHERE slug = ?", (slug_val,)):
        slug_val = f"{base}-{n}"
        n += 1
    now = utc_now()
    conn.execute(
        "INSERT INTO titles (id, kind, name, slug, synopsis, poster_key, attribution, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, kind, name, slug_val, synopsis, poster_key, attribution, now),
    )
    conn.commit()
    return get_title(conn, id=tid)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------


def list_episodes(conn: Connection, *, title_id: str) -> list[dict[str, Any]]:
    return fetchall_dicts(
        conn,
        "SELECT * FROM episodes WHERE title_id = ? ORDER BY season, episode_number",
        (title_id,),
    )


def get_episode(conn: Connection, *, id: str) -> dict[str, Any] | None:
    return fetchone_dict(conn, "SELECT * FROM episodes WHERE id = ?", (id,))


def _extract_poster_frame(video_path: Path, dest: Path, *, at_s: float = 1.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Prefer ffmpeg; fall back to OpenCV.
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(at_s),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    for try_s in (at_s, 0.0, 0.1):
        frame_i = int(try_s * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_i))
        ok, frame = cap.read()
        if ok and frame is not None:
            cap.release()
            cv2.imwrite(str(dest), frame)
            return dest
    cap.release()
    # Last resort: copy a 1x1 jpeg so episode creation still succeeds.
    import numpy as np

    cv2.imwrite(str(dest), np.zeros((64, 64, 3), dtype=np.uint8))
    return dest


def _probe_duration(video_path: Path) -> float:
    try:
        from product_placement.crop_stitch import probe_video

        return float(probe_video(video_path)[2])
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            text=True,
        )
        return float(out.strip())
    except Exception:
        return 0.0


def create_episode(
    conn: Connection,
    *,
    title_id: str,
    name: str,
    season: int = 1,
    episode: int = 1,
    video_path: str | Path | None = None,
    video_key: str | None = None,
    poster_key: str | None = None,
    duration_s: float | None = None,
    suggest_status: str = "queued",
    episode_id: str | None = None,
) -> dict[str, Any]:
    title = get_title(conn, id=title_id)
    if not title:
        raise ValueError(f"title not found: {title_id}")

    store = get_object_store()
    eid = episode_id or new_id()
    slug = str(title.get("slug") or "title")
    vkey = video_key
    pkey = poster_key
    dur = duration_s

    if video_path is not None:
        vp = Path(video_path)
        if not vp.is_file():
            raise FileNotFoundError(f"video not found: {vp}")
        vkey = vkey or catalog_video_key(slug, season, episode, vp.suffix or ".mp4")
        store.put_file(vp, vkey, content_type=_video_content_type(vp))
        dur = dur if dur is not None else _probe_duration(vp)
        with tempfile.TemporaryDirectory() as tmp:
            poster = Path(tmp) / "poster.jpg"
            _extract_poster_frame(vp, poster, at_s=1.0)
            pkey = pkey or catalog_poster_key(slug, season, episode)
            store.put_file(poster, pkey, content_type="image/jpeg")
            title_poster = catalog_title_poster_key(slug)
            if not title.get("poster_key"):
                store.put_file(poster, title_poster, content_type="image/jpeg")
                conn.execute(
                    "UPDATE titles SET poster_key = ? WHERE id = ?",
                    (title_poster, title_id),
                )
    elif vkey:
        if not store.exists(vkey):
            raise FileNotFoundError(
                f"object not in store: {vkey} "
                f"(expected {catalog_video_key(slug, season, episode)})"
            )
        pkey = pkey or catalog_poster_key(slug, season, episode)
        if not store.exists(pkey):
            pkey = None

    now = utc_now()
    conn.execute(
        "INSERT INTO episodes (id, title_id, season, episode_number, name, video_key, "
        "poster_key, duration_s, suggest_status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            eid,
            title_id,
            int(season),
            int(episode),
            name,
            vkey,
            pkey,
            float(dur or 0.0),
            suggest_status,
            now,
        ),
    )
    conn.commit()
    return get_episode(conn, id=eid)  # type: ignore[return-value]


def register_preloaded_episode(
    conn: Connection,
    *,
    name: str,
    kind: str = "movie",
    slug: str | None = None,
    synopsis: str = "",
    attribution: str = "",
    season: int = 1,
    episode: int = 1,
    episode_name: str | None = None,
    video_key: str | None = None,
) -> dict[str, Any]:
    """Point a catalog row at an object already in the store. Does not upload."""
    slug_val = slug or slugify(name)
    title = get_title(conn, slug=slug_val)
    if not title:
        title = create_title(
            conn,
            name=name,
            kind=kind if kind in {"movie", "series"} else "movie",
            synopsis=synopsis,
            attribution=attribution,
            slug=slug_val,
        )
    key = video_key or catalog_video_key(slug_val, season, episode)
    prior = fetchone_dict(
        conn,
        "SELECT id FROM episodes WHERE title_id = ? AND season = ? AND episode_number = ?",
        (title["id"], int(season), int(episode)),
    )
    if prior:
        existing = get_episode(conn, id=prior["id"])
        return {
            "title": get_title(conn, id=title["id"]),
            "episode": existing,
            "video_key": (existing or {}).get("video_key") or key,
        }
    ep = create_episode(
        conn,
        title_id=title["id"],
        name=episode_name or (name if kind == "movie" else f"Episode {episode}"),
        season=season,
        episode=episode,
        video_key=key,
        suggest_status="queued",
    )
    return {"title": get_title(conn, id=title["id"]), "episode": ep, "video_key": key}


def _video_content_type(path: Path) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }.get(path.suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------


def insert_moment(
    conn: Connection,
    *,
    episode_id: str,
    start_s: float,
    end_s: float,
    region: dict[str, Any] | str,
    whats_there: str = "",
    score: float = 0.0,
    reason: str = "",
    frame_key: str | None = None,
    frame_w: int = 0,
    frame_h: int = 0,
    moment_id: str | None = None,
) -> dict[str, Any]:
    mid = moment_id or new_id()
    region_json = region if isinstance(region, str) else json.dumps(region)
    conn.execute(
        "INSERT INTO moments (id, episode_id, start_s, end_s, region_json, whats_there, "
        "score, reason, frame_key, frame_w, frame_h) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            mid,
            episode_id,
            float(start_s),
            float(end_s),
            region_json,
            whats_there,
            float(score),
            reason,
            frame_key,
            int(frame_w or 0),
            int(frame_h or 0),
        ),
    )
    conn.commit()
    return fetchone_dict(conn, "SELECT * FROM moments WHERE id = ?", (mid,))  # type: ignore[return-value]


def list_moments(
    conn: Connection, *, episode_id: str, brand_id: str | None = None
) -> list[dict[str, Any]]:
    moments = fetchall_dicts(
        conn,
        "SELECT * FROM moments WHERE episode_id = ? ORDER BY start_s",
        (episode_id,),
    )
    for m in moments:
        if isinstance(m.get("region_json"), str):
            try:
                m["region"] = json.loads(m["region_json"])
            except json.JSONDecodeError:
                m["region"] = {}
        else:
            m["region"] = m.get("region_json")

    if brand_id:
        brand_row = get_brand(conn, id=brand_id)
        if brand_row:
            brand = _brand_from_row(brand_row, conn)
            for m in moments:
                m["brand_fit"] = _score_moment_for_brand(m, brand)

    return moments


def _brand_from_row(row: dict[str, Any], conn: Connection):
    from product_placement.brand import Brand, BrandRules

    rules_raw = row.get("rules_json") or "{}"
    rules = json.loads(rules_raw) if isinstance(rules_raw, str) else (rules_raw or {})
    assets = [
        a["filename"]
        for a in fetchall_dicts(
            conn,
            "SELECT filename FROM brand_assets WHERE brand_id = ? ORDER BY created_at",
            (row["id"],),
        )
    ]
    return Brand(
        id=row["id"],
        name=row.get("name") or row["id"],
        brief=row.get("brief") or "",
        assets=assets,
        rules=BrandRules.from_dict(rules),
    )


def _score_moment_for_brand(moment: dict[str, Any], brand) -> dict[str, Any]:
    """Fast field-only brand fit for list/poll paths (no YOLO / frame download).

    Full vision scoring runs once at suggest time; reloading ONNX on every
    brand-page poll was stalling and OOMing the host.
    """
    from product_placement.brand import product_aspect_ratio, primary_asset_path
    from product_placement.brand_score import BrandFit, aspect_fit_score
    from product_placement.vision import BBox

    region_raw = moment.get("region") or {}
    if isinstance(region_raw, str):
        try:
            region_raw = json.loads(region_raw)
        except json.JSONDecodeError:
            region_raw = {}
    region = BBox(
        int(region_raw.get("x", 0)),
        int(region_raw.get("y", 0)),
        int(region_raw.get("w", 1)),
        int(region_raw.get("h", 1)),
    )
    whats = str(moment.get("whats_there") or "")

    prefer = {p.lower() for p in brand.rules.prefer}
    avoid = {a.lower() for a in brand.rules.avoid}
    low = whats.lower()
    vetoed = any(a in low for a in avoid if a)
    class_boost = 0.0
    if any(p in low for p in prefer if p):
        class_boost = 0.55
    product_ar = None
    try:
        asset = primary_asset_path(brand)
        if asset and asset.is_file():
            product_ar = product_aspect_ratio(asset)
    except Exception:
        product_ar = None
    aspect = aspect_fit_score(region, product_ar)

    if vetoed:
        fit = BrandFit(
            vetoed=True,
            veto_reason="whats_there mentions avoid term",
            reason=f"vetoed: '{whats}' matches avoid",
            total=-1.0,
            class_boost=class_boost,
            aspect_fit=aspect,
        )
        return fit.to_dict()

    total = 0.55 * class_boost + 0.25 * aspect + 0.20 * 0.0
    fit = BrandFit(
        class_boost=class_boost,
        aspect_fit=aspect,
        reason=f"field score for '{whats}'" if whats else "field score",
        total=float(total),
    )
    return fit.to_dict()


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------


def list_brands(conn: Connection) -> list[dict[str, Any]]:
    rows = fetchall_dicts(conn, "SELECT * FROM brands ORDER BY created_at")
    for r in rows:
        r["rules"] = json.loads(r["rules_json"]) if r.get("rules_json") else {}
        r["assets"] = fetchall_dicts(
            conn,
            "SELECT id, filename, object_key, created_at FROM brand_assets WHERE brand_id = ?",
            (r["id"],),
        )
    return rows


def get_brand(conn: Connection, *, id: str) -> dict[str, Any] | None:
    row = fetchone_dict(conn, "SELECT * FROM brands WHERE id = ?", (id,))
    if not row:
        return None
    row["rules"] = json.loads(row["rules_json"]) if row.get("rules_json") else {}
    row["assets"] = fetchall_dicts(
        conn,
        "SELECT id, filename, object_key, created_at FROM brand_assets WHERE brand_id = ?",
        (id,),
    )
    return row


def create_brand(
    conn: Connection,
    *,
    name: str,
    brief: str = "",
    prefer: list[str] | None = None,
    avoid: list[str] | None = None,
    placement: str = "resting",
    asset_paths: list[str | Path] | None = None,
    brand_id: str | None = None,
) -> dict[str, Any]:
    from product_placement.brand import BrandRules, parse_brief

    bid = brand_id or new_id()
    rules = parse_brief(brief) if brief else BrandRules()
    if prefer is not None:
        rules.prefer = list(prefer)
    if avoid is not None:
        rules.avoid = list(avoid)
    if placement:
        rules.placement = placement

    now = utc_now()
    conn.execute(
        "INSERT INTO brands (id, name, brief, rules_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (bid, name, brief, json.dumps(rules.to_dict()), now),
    )

    store = get_object_store()
    for path in asset_paths or []:
        p = Path(path)
        if not p.is_file():
            continue
        asset_id = new_id()
        key = f"brands/{bid}/{p.name}"
        store.put_file(p, key)
        conn.execute(
            "INSERT INTO brand_assets (id, brand_id, filename, object_key, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (asset_id, bid, p.name, key, now),
        )
        # Also mirror into file-backed brands dir for primary_asset_path helpers.
        dest_dir = data_dir() / "brands" / bid
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest_dir / p.name)

    # Keep a brand.json mirror for file-based loaders.
    mirror = {
        "id": bid,
        "name": name,
        "brief": brief,
        "assets": [Path(p).name for p in (asset_paths or []) if Path(p).is_file()],
        "rules": rules.to_dict(),
    }
    bdir = data_dir() / "brands" / bid
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "brand.json").write_text(json.dumps(mirror, indent=2) + "\n")

    conn.commit()
    return get_brand(conn, id=bid)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Placements
# ---------------------------------------------------------------------------


def list_placements(
    conn: Connection, *, brand_id: str, episode_id: str | None = None
) -> list[dict[str, Any]]:
    if episode_id:
        return fetchall_dicts(
            conn,
            "SELECT * FROM placements WHERE brand_id = ? AND episode_id = ? "
            "ORDER BY created_at",
            (brand_id, episode_id),
        )
    return fetchall_dicts(
        conn,
        "SELECT * FROM placements WHERE brand_id = ? ORDER BY created_at",
        (brand_id,),
    )


def get_placement(conn: Connection, *, id: str) -> dict[str, Any] | None:
    return fetchone_dict(conn, "SELECT * FROM placements WHERE id = ?", (id,))


def upsert_placement(
    conn: Connection,
    *,
    brand_id: str,
    moment_id: str,
    status: str,
    episode_id: str | None = None,
    test_mode: int = 1,
    placement_id: str | None = None,
) -> dict[str, Any]:
    status = status.strip().lower()
    if status not in {"approved", "rejected", "queued", "rendering", "ready", "failed"}:
        raise ValueError(f"invalid status: {status}")

    moment = fetchone_dict(conn, "SELECT * FROM moments WHERE id = ?", (moment_id,))
    if not moment:
        raise ValueError(f"moment not found: {moment_id}")
    ep_id = episode_id or moment["episode_id"]

    existing = fetchone_dict(
        conn,
        "SELECT * FROM placements WHERE brand_id = ? AND moment_id = ?",
        (brand_id, moment_id),
    )
    now = utc_now()
    spawn = False
    if existing:
        cur = str(existing.get("status") or "")
        if status == "approved":
            # Already in-flight or done — do not re-queue (avoids double Seedance bills).
            if cur in {"queued", "rendering", "ready"}:
                row = get_placement(conn, id=existing["id"])
                assert row is not None
                row["spawn"] = False
                return row
            new_status = "queued"
            spawn = True
        else:
            new_status = status
        conn.execute(
            "UPDATE placements SET status = ?, updated_at = ?, episode_id = ? WHERE id = ?",
            (new_status, now, ep_id, existing["id"]),
        )
        conn.commit()
        row = get_placement(conn, id=existing["id"])
        assert row is not None
        row["spawn"] = spawn
        return row

    pid = placement_id or new_id()
    new_status = "queued" if status == "approved" else status
    spawn = new_status == "queued"
    conn.execute(
        "INSERT INTO placements (id, brand_id, moment_id, episode_id, status, clip_key, "
        "error, test_mode, backend, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, brand_id, moment_id, ep_id, new_status, None, None, int(test_mode), None, now, now),
    )
    conn.commit()
    row = get_placement(conn, id=pid)
    assert row is not None
    row["spawn"] = spawn
    return row


def update_placement(conn: Connection, placement_id: str, **fields: Any) -> dict[str, Any]:
    if not fields:
        return get_placement(conn, id=placement_id)  # type: ignore[return-value]
    fields = dict(fields)
    fields["updated_at"] = utc_now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [placement_id]
    conn.execute(f"UPDATE placements SET {cols} WHERE id = ?", vals)
    conn.commit()
    return get_placement(conn, id=placement_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Watch payload
# ---------------------------------------------------------------------------


def get_watch_payload(
    conn: Connection,
    *,
    title_slug: str,
    season: int,
    episode: int,
    brand_id: str | None = None,
) -> dict[str, Any]:
    title = get_title(conn, slug=title_slug)
    if not title:
        raise ValueError(f"title not found: {title_slug}")
    ep = fetchone_dict(
        conn,
        "SELECT * FROM episodes WHERE title_id = ? AND season = ? AND episode_number = ?",
        (title["id"], int(season), int(episode)),
    )
    if not ep:
        raise ValueError(f"episode not found: s{season}e{episode}")

    store = get_object_store()
    video_key = ep.get("video_key")
    video_url = store.public_or_presigned_url(video_key) if video_key else None
    poster_key = ep.get("poster_key") or title.get("poster_key")

    placements: list[dict[str, Any]] = []
    sql = (
        "SELECT p.*, m.start_s, m.end_s, m.region_json, m.whats_there "
        "FROM placements p JOIN moments m ON m.id = p.moment_id "
        "WHERE p.episode_id = ? AND p.status = 'ready'"
    )
    params: list[Any] = [ep["id"]]
    if brand_id:
        sql += " AND p.brand_id = ?"
        params.append(brand_id)
    sql += " ORDER BY m.start_s"
    rows = fetchall_dicts(conn, sql, params)
    for r in rows:
        clip_key = r.get("clip_key")
        placements.append(
            {
                "id": r["id"],
                "brand_id": r["brand_id"],
                "moment_id": r["moment_id"],
                "status": r["status"],
                "start_s": float(r["start_s"]),
                "end_s": float(r["end_s"]),
                "clip_key": clip_key,
                "clip_url": store.public_or_presigned_url(clip_key) if clip_key else None,
                "region": json.loads(r["region_json"]) if r.get("region_json") else None,
                "whats_there": r.get("whats_there"),
                "backend": r.get("backend"),
            }
        )

    return {
        "title": title,
        "episode": ep,
        "video_key": video_key,
        "video_url": video_url,
        "poster_key": poster_key,
        "poster_url": (
            store.public_or_presigned_url(poster_key) if poster_key else None
        ),
        "placements": placements,
        "brand_id": brand_id,
    }


# ---------------------------------------------------------------------------
# Seed Blender open movies
# ---------------------------------------------------------------------------

BLENDER_SOURCES = [
    {
        "slug": "tears-of-steel",
        "name": "Tears of Steel",
        "kind": "series",
        "synopsis": "Short sci-fi film by the Blender Foundation (open movie).",
        "url": "https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov",
        "episodes": [
            {"name": "Tears of Steel — Act 1", "season": 1, "episode": 1, "start": 0, "duration": 90},
            {"name": "Tears of Steel — Act 2", "season": 1, "episode": 2, "start": 120, "duration": 90},
        ],
    },
    {
        "slug": "big-buck-bunny",
        "name": "Big Buck Bunny",
        "kind": "movie",
        "synopsis": "Blender Foundation open movie (Peach) — short 320x180 sample.",
        "url": "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4",
        "episodes": [
            {"name": "Big Buck Bunny", "season": 1, "episode": 1, "start": 0, "duration": 120},
        ],
    },
    {
        "slug": "sintel",
        "name": "Sintel",
        "kind": "movie",
        "synopsis": "Blender Foundation open movie (Durian) — trailer.",
        "url": "https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4",
        "episodes": [
            {"name": "Sintel Trailer", "season": 1, "episode": 1, "start": 0, "duration": 90},
        ],
    },
]


def _download(url: str, dest: Path, *, timeout: float = 180.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s -> %s", url, dest.name)
    # Prefer curl for progress / resume friendliness when available.
    try:
        subprocess.run(
            ["curl", "-L", "--fail", "--max-time", str(int(timeout)), "-o", str(dest), url],
            check=True,
            capture_output=True,
        )
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        log.debug("curl failed (%s), falling back to urllib", exc)
    urllib.request.urlretrieve(url, str(dest))
    return dest


def _ffmpeg_trim(src: Path, dest: Path, *, start: float, duration: float) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_dur = _probe_duration(src)
    if src_dur > 0 and start >= src_dur:
        # Short fixtures / capped downloads: wrap offset into the available range.
        start = 0.0
    if src_dur > 0:
        duration = min(duration, max(0.5, src_dur - start))

    def _run(*, reencode: bool) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(src),
            "-t",
            str(duration),
        ]
        if reencode:
            cmd += [
                "-c:v",
                "libx264",
                "-an",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        else:
            cmd += ["-c", "copy"]
        cmd.append(str(dest))
        subprocess.run(cmd, check=True, capture_output=True)

    try:
        _run(reencode=False)
    except subprocess.CalledProcessError:
        dest.unlink(missing_ok=True)

    if not dest.is_file() or dest.stat().st_size == 0:
        # Re-encode if stream copy failed (some containers / keyframe gaps).
        _run(reencode=True)

    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg trim produced empty output for {src}")
    return dest


def seed_blender_open_movies(
    conn: Connection | None = None,
    *,
    force: bool = False,
    download_fn=_download,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Seed Blender open-movie titles/episodes when the catalog is empty."""
    own = conn is None
    if own:
        conn = get_connection()
        init_schema(conn)

    assert conn is not None
    # Idempotent fill: never bail just because some titles exist. A partial/
    # timed-out seed leaves empty titles; retries must create missing episodes.
    cache = cache_dir or (data_dir() / "seed_cache")
    cache.mkdir(parents=True, exist_ok=True)
    created: list[dict[str, Any]] = []
    skipped_existing = 0

    for src in BLENDER_SOURCES:
        title = get_title(conn, slug=src["slug"])
        if not title:
            title = create_title(
                conn,
                name=src["name"],
                kind=src["kind"],
                synopsis=src["synopsis"],
                attribution=ATTRIBUTION_BLENDER,
                slug=src["slug"],
            )

        url = src["url"]
        ext = Path(urlparse_path(url)).suffix or ".mp4"
        raw_path = cache / f"{src['slug']}{ext}"
        # Only download if any episode for this source is still missing.
        missing_eps = False
        for ep_spec in src["episodes"]:
            prior = fetchone_dict(
                conn,
                "SELECT id FROM episodes WHERE title_id = ? AND season = ? AND episode_number = ?",
                (title["id"], ep_spec["season"], ep_spec["episode"]),
            )
            if not prior:
                missing_eps = True
                break
        if not missing_eps and not force:
            skipped_existing += len(src["episodes"])
            continue

        if force or not raw_path.is_file() or raw_path.stat().st_size == 0:
            try:
                download_fn(url, raw_path)
            except Exception as exc:
                log.warning("skip %s: download failed: %s", src["slug"], exc)
                continue

        for ep_spec in src["episodes"]:
            # Skip if episode already exists for this title/season/ep.
            prior = fetchone_dict(
                conn,
                "SELECT id FROM episodes WHERE title_id = ? AND season = ? AND episode_number = ?",
                (title["id"], ep_spec["season"], ep_spec["episode"]),
            )
            if prior:
                skipped_existing += 1
                continue
            clip = cache / f"{src['slug']}_s{ep_spec['season']}e{ep_spec['episode']}.mp4"
            try:
                if ep_spec.get("start", 0) or ep_spec.get("duration"):
                    _ffmpeg_trim(
                        raw_path,
                        clip,
                        start=float(ep_spec.get("start") or 0),
                        duration=float(ep_spec.get("duration") or 90),
                    )
                else:
                    shutil.copy2(raw_path, clip)
            except Exception as exc:
                log.warning("trim failed for %s, using full file: %s", src["slug"], exc)
                clip = raw_path

            ep = create_episode(
                conn,
                title_id=title["id"],
                name=ep_spec["name"],
                season=ep_spec["season"],
                episode=ep_spec["episode"],
                video_path=clip,
                suggest_status="queued",
            )
            created.append(ep)
            # create_episode already writes catalog/<slug>/poster.jpg when the
            # title has none. Refresh so a later episode does not see a stale row.
            title = get_title(conn, id=title["id"]) or title

    titles_out = list_titles(conn)
    # Crash recovery + queue: reclaim abandoned 'running', then list claimable rows.
    from product_placement.jobs import reset_stale_suggest_running

    reclaimed = reset_stale_suggest_running(conn)
    need_suggest = fetchall_dicts(
        conn,
        "SELECT * FROM episodes WHERE suggest_status IN ('queued', 'failed') "
        "OR suggest_status IS NULL OR suggest_status = '' "
        "ORDER BY created_at",
    )
    if own:
        conn.close()
    return {
        # True only when nothing new was added.
        "skipped": len(created) == 0,
        "reason": "all episodes already present" if not created else None,
        "episodes_created": len(created),
        # Newly created first; callers also get the full suggest queue.
        "episodes": created or need_suggest,
        "suggest_queue": need_suggest,
        "reclaimed_running": reclaimed,
        "titles": titles_out,
        "skipped_existing": skipped_existing,
    }


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Print JSON to stdout (always on; accepted for Next.js callers)",
    )

    p = argparse.ArgumentParser(prog="product_placement.catalog")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[json_parent])

    add("init-db")
    add("list-titles")

    gt = add("get-title")
    gt.add_argument("--id", default=None)
    gt.add_argument("--slug", default=None)

    ct = add("create-title")
    ct.add_argument("--name", required=True)
    ct.add_argument("--kind", default="movie")
    ct.add_argument("--synopsis", default="")
    ct.add_argument("--attribution", default="")

    le = add("list-episodes")
    le.add_argument("--title-id", required=True)

    ge = add("get-episode")
    ge.add_argument("--id", required=True)

    ce = add("create-episode")
    ce.add_argument("--title-id", required=True)
    ce.add_argument("--name", required=True)
    ce.add_argument("--season", type=int, default=1)
    ce.add_argument("--episode", type=int, default=1)
    ce.add_argument("--video-path", default=None)
    ce.add_argument("--video-key", default=None)

    rp = add("register-preload")
    rp.add_argument("--name", required=True)
    rp.add_argument("--kind", default="movie", choices=("movie", "series"))
    rp.add_argument("--slug", default=None)
    rp.add_argument("--synopsis", default="")
    rp.add_argument("--attribution", default="")
    rp.add_argument("--season", type=int, default=1)
    rp.add_argument("--episode", type=int, default=1)
    rp.add_argument("--episode-name", default=None)
    rp.add_argument(
        "--video-key",
        default=None,
        help="Existing object key. Defaults to catalog/<slug>/sXXeYY/video.mp4",
    )

    lm = add("list-moments")
    lm.add_argument("--episode-id", required=True)
    lm.add_argument("--brand-id", default=None)

    add("list-brands")

    gb = add("get-brand")
    gb.add_argument("--id", required=True)

    cb = add("create-brand")
    cb.add_argument("--name", required=True)
    cb.add_argument("--brief", default="")
    cb.add_argument("--prefer", action="append", default=[])
    cb.add_argument("--avoid", action="append", default=[])
    cb.add_argument("--placement", default="resting")
    cb.add_argument("--asset", action="append", default=[], dest="assets")

    lp = add("list-placements")
    lp.add_argument("--brand-id", required=True)
    lp.add_argument("--episode-id", default=None)

    up = add("upsert-placement")
    up.add_argument("--brand-id", required=True)
    up.add_argument("--moment-id", required=True)
    up.add_argument("--status", required=True, choices=("approved", "rejected"))

    wp = add("get-watch-payload")
    wp.add_argument("--title-slug", required=True)
    wp.add_argument("--season", type=int, required=True)
    wp.add_argument("--episode", type=int, required=True)
    wp.add_argument("--brand-id", default=None)

    add("seed-blender")

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    conn = get_connection()
    try:
        if args.command == "init-db":
            init_schema(conn)
            imported = migrate_file_brands(conn)
            _print_json({"ok": True, "imported_brands": imported})
            return

        if args.command == "list-titles":
            _print_json(list_titles(conn))
            return

        if args.command == "get-title":
            row = get_title(conn, id=args.id, slug=args.slug)
            if not row:
                raise SystemExit(json.dumps({"error": "not found"}))
            _print_json(row)
            return

        if args.command == "create-title":
            _print_json(
                create_title(
                    conn,
                    name=args.name,
                    kind=args.kind,
                    synopsis=args.synopsis,
                    attribution=args.attribution,
                )
            )
            return

        if args.command == "list-episodes":
            _print_json(list_episodes(conn, title_id=args.title_id))
            return

        if args.command == "get-episode":
            row = get_episode(conn, id=args.id)
            if not row:
                raise SystemExit(json.dumps({"error": "not found"}))
            _print_json(row)
            return

        if args.command == "create-episode":
            if not args.video_path and not args.video_key:
                raise SystemExit("create-episode needs --video-path or --video-key")
            _print_json(
                create_episode(
                    conn,
                    title_id=args.title_id,
                    name=args.name,
                    season=args.season,
                    episode=args.episode,
                    video_path=args.video_path,
                    video_key=args.video_key,
                )
            )
            return

        if args.command == "register-preload":
            _print_json(
                register_preloaded_episode(
                    conn,
                    name=args.name,
                    kind=args.kind,
                    slug=args.slug,
                    synopsis=args.synopsis,
                    attribution=args.attribution,
                    season=args.season,
                    episode=args.episode,
                    episode_name=args.episode_name,
                    video_key=args.video_key,
                )
            )
            return

        if args.command == "list-moments":
            _print_json(
                list_moments(conn, episode_id=args.episode_id, brand_id=args.brand_id)
            )
            return

        if args.command == "list-brands":
            _print_json(list_brands(conn))
            return

        if args.command == "get-brand":
            row = get_brand(conn, id=args.id)
            if not row:
                raise SystemExit(json.dumps({"error": "not found"}))
            _print_json(row)
            return

        if args.command == "create-brand":
            _print_json(
                create_brand(
                    conn,
                    name=args.name,
                    brief=args.brief,
                    prefer=args.prefer or None,
                    avoid=args.avoid or None,
                    placement=args.placement,
                    asset_paths=args.assets or None,
                )
            )
            return

        if args.command == "list-placements":
            _print_json(
                list_placements(
                    conn, brand_id=args.brand_id, episode_id=args.episode_id
                )
            )
            return

        if args.command == "upsert-placement":
            _print_json(
                upsert_placement(
                    conn,
                    brand_id=args.brand_id,
                    moment_id=args.moment_id,
                    status=args.status,
                )
            )
            return

        if args.command == "get-watch-payload":
            _print_json(
                get_watch_payload(
                    conn,
                    title_slug=args.title_slug,
                    season=args.season,
                    episode=args.episode,
                    brand_id=args.brand_id,
                )
            )
            return

        if args.command == "seed-blender":
            _print_json(seed_blender_open_movies(conn))
            return

        raise SystemExit(f"unknown command {args.command}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
