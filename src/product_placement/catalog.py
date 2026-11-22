"""Catalog CRUD + JSON CLI for the streaming product-placement POC."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
from product_placement.catalog_keys import (
    catalog_episode_prefix,
    catalog_poster_key,
    catalog_title_poster_key,
    catalog_video_key,
    original_video_key,
    slugify,
)
from product_placement.object_store import ObjectStore, get_object_store

log = logging.getLogger(__name__)

ATTRIBUTION_BLENDER = "CC-BY Blender Foundation"


def _configure_catalog_logging() -> None:
    """Send catalog logs to stderr so CLI JSON on stdout stays parseable."""
    logging.basicConfig(
        level=logging.INFO,
        format="[catalog] %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    log.setLevel(logging.INFO)


def seed_log(msg: str, *args: Any) -> None:
    text = msg % args if args else msg
    print(f"[catalog-seed] {text}", file=sys.stderr, flush=True)
    log.info("%s", text)


def humanize_movie_filename(filename: str) -> str:
    """`the_matrix.mp4` → 'The Matrix'."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\.\-]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title() if cleaned else "Untitled"


MOVIES_PREFIX = "movies/"
MOVIE_VIDEO_EXTS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".wmv",
    ".flv",
    ".ts",
    ".mpg",
    ".mpeg",
}
# Mean luma below this is treated as near-black (0–255 scale).
MIN_POSTER_MEAN_LUMA = 18.0
BROWSER_SAFE_VIDEO = {"h264", "avc1", "avc", "h.264"}
BROWSER_SAFE_AUDIO = {"aac", "mp3", "alac"}
MP4_LIKE_FORMATS = {"mp4", "m4v"}
IMPORT_LOCK_NAME = "import-movies.lock"
IMPORT_WAKE_NAME = "import-movies.wake"
DEFAULT_IMPORT_POLL_S = 60.0
DEFAULT_FFMPEG_TIMEOUT_S = 7200.0
DEFAULT_FFMPEG_THREADS = 4
DEFAULT_IMPORT_MAX_ATTEMPTS = 3


def import_tmp_root() -> Path:
    raw = (os.environ.get("IMPORT_TMP_DIR") or "").strip()
    root = Path(raw) if raw else data_dir() / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ffmpeg_timeout_s() -> float:
    try:
        return float(os.environ.get("FFMPEG_TIMEOUT_S") or DEFAULT_FFMPEG_TIMEOUT_S)
    except ValueError:
        return DEFAULT_FFMPEG_TIMEOUT_S


def _ffmpeg_threads() -> int:
    try:
        return max(1, int(os.environ.get("FFMPEG_THREADS") or DEFAULT_FFMPEG_THREADS))
    except ValueError:
        return DEFAULT_FFMPEG_THREADS


def _import_max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("IMPORT_MAX_ATTEMPTS") or DEFAULT_IMPORT_MAX_ATTEMPTS))
    except ValueError:
        return DEFAULT_IMPORT_MAX_ATTEMPTS


def _import_poll_s() -> float:
    try:
        return max(1.0, float(os.environ.get("IMPORT_POLL_S") or DEFAULT_IMPORT_POLL_S))
    except ValueError:
        return DEFAULT_IMPORT_POLL_S


def episode_is_watchable(ep: dict[str, Any] | None) -> bool:
    """Playable catalog original — not an in-flight/failed import pointing at movies/."""
    if not ep:
        return False
    vkey = str(ep.get("video_key") or "").strip()
    status = str(ep.get("suggest_status") or "").strip().lower()
    if not vkey or vkey.startswith(MOVIES_PREFIX):
        return False
    if status in {"processing", "failed"}:
        return False
    return True


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

TITLE_SOURCES = ("test", "hosted")
VIDEO_SOURCE_CHOICES = ("test", "hosted", "all")


def resolve_video_source(source: str | None = None) -> str:
    raw = (source if source is not None else os.environ.get("VIDEO_SOURCE") or "hosted")
    raw = str(raw).strip().lower()
    return raw if raw in VIDEO_SOURCE_CHOICES else "hosted"


def list_titles(
    conn: Connection,
    *,
    watchable_only: bool = False,
    source: str | None = None,
) -> list[dict[str, Any]]:
    src = resolve_video_source(source)
    params: list[Any] = []
    where: list[str] = []
    if src in TITLE_SOURCES:
        where.append("t.source = ?")
        params.append(src)
    if watchable_only:
        # Ready-to-watch only: catalog original present, never processing/failed or movies/.
        # Bind the LIKE pattern — a literal 'movies/%' breaks psycopg ( % is not %% / %s ).
        where.extend(
            [
                "e.video_key IS NOT NULL AND TRIM(e.video_key) != ''",
                "e.video_key NOT LIKE ?",
                "LOWER(COALESCE(e.suggest_status, '')) NOT IN ('processing', 'failed')",
            ]
        )
        params.append(f"{MOVIES_PREFIX}%")
        sql = (
            "SELECT DISTINCT t.* FROM titles t "
            "INNER JOIN episodes e ON e.title_id = t.id"
        )
    else:
        sql = "SELECT t.* FROM titles t"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.created_at"
    return fetchall_dicts(conn, sql, tuple(params) if params else None)


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
    source: str = "test",
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
    source_val = source if source in TITLE_SOURCES else "test"
    conn.execute(
        "INSERT INTO titles (id, kind, name, slug, synopsis, poster_key, attribution, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, kind, name, slug_val, synopsis, poster_key, attribution, source_val, now),
    )
    conn.commit()
    return get_title(conn, id=tid)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------


def list_episodes(
    conn: Connection, *, title_id: str, watchable_only: bool = False
) -> list[dict[str, Any]]:
    rows = fetchall_dicts(
        conn,
        "SELECT * FROM episodes WHERE title_id = ? ORDER BY season, episode_number",
        (title_id,),
    )
    if not watchable_only:
        return rows
    return [r for r in rows if episode_is_watchable(r)]


def get_episode(conn: Connection, *, id: str) -> dict[str, Any] | None:
    return fetchone_dict(conn, "SELECT * FROM episodes WHERE id = ?", (id,))


def _mean_luma(image_path: Path) -> float:
    """Average luma (0–255). Returns 0 when the image cannot be read."""
    try:
        import cv2

        img = cv2.imread(str(image_path))
        if img is None:
            return 0.0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return float(gray.mean())
    except Exception:
        return 0.0


def _try_extract_frame_at(video_path: Path, dest: Path, at_s: float) -> bool:
    """Write a single frame at at_s into dest. True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(max(0.0, float(at_s))),
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
            return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return False
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        frame_i = int(max(0.0, float(at_s)) * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_i))
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            cv2.imwrite(str(dest), frame)
            return dest.is_file() and dest.stat().st_size > 0
    except Exception:
        pass
    return False


def _extract_poster_frame(
    video_path: Path,
    dest: Path,
    *,
    at_s: float = 1.0,
    duration_s: float | None = None,
    min_luma: float = MIN_POSTER_MEAN_LUMA,
) -> Path:
    """Grab a non-near-black frame. Tries ~10%, 25%, 50% of duration."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dur = float(duration_s) if duration_s is not None else _probe_duration(video_path)
    if dur and dur > 0:
        times = [dur * frac for frac in (0.10, 0.25, 0.50)]
    else:
        times = [float(at_s), 2.0, 5.0]

    best_path: Path | None = None
    best_luma = -1.0
    best_ok_path: Path | None = None
    best_ok_luma = -1.0

    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            candidate = Path(tmp) / f"frame_{i}.jpg"
            if not _try_extract_frame_at(video_path, candidate, t):
                continue
            luma = _mean_luma(candidate)
            if luma > best_luma:
                best_luma = luma
                best_path = candidate
            if luma >= min_luma and luma > best_ok_luma:
                best_ok_luma = luma
                best_ok_path = candidate

        chosen = best_ok_path or best_path
        if chosen is not None:
            dest.write_bytes(chosen.read_bytes())
            return dest

    raise RuntimeError(f"cannot extract poster frame from {video_path}")


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
        vkey = vkey or original_video_key(slug, season, episode)
        store.put_file(vp, vkey, content_type="video/mp4")
        dur = dur if dur is not None else _probe_duration(vp)
        with tempfile.TemporaryDirectory() as tmp:
            poster = Path(tmp) / "poster.jpg"
            try:
                _extract_poster_frame(vp, poster, at_s=1.0, duration_s=dur)
                pkey = pkey or catalog_poster_key(slug, season, episode)
                store.put_file(poster, pkey, content_type="image/jpeg")
                title_poster = catalog_title_poster_key(slug)
                if not title.get("poster_key") or not store.exists(
                    str(title["poster_key"])
                ):
                    store.put_file(poster, title_poster, content_type="image/jpeg")
                    conn.execute(
                        "UPDATE titles SET poster_key = ? WHERE id = ?",
                        (title_poster, title_id),
                    )
            except Exception as exc:
                log.warning("poster extract failed for %s: %s", vp, exc)
                pkey = pkey if (pkey and store.exists(pkey)) else None
    elif vkey:
        if not store.exists(vkey):
            raise FileNotFoundError(
                f"object not in store: {vkey} "
                f"(expected {original_video_key(slug, season, episode)})"
            )
        pkey = pkey or catalog_poster_key(slug, season, episode)
        if not store.exists(pkey):
            # Download once and build a poster (same path as seed / movies import).
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    suffix = Path(str(vkey)).suffix or ".mp4"
                    local = Path(tmp) / f"video{suffix}"
                    store.get_file(vkey, local)
                    dur = dur if dur is not None else _probe_duration(local)
                    poster = Path(tmp) / "poster.jpg"
                    _extract_poster_frame(local, poster, duration_s=dur)
                    store.put_file(poster, pkey, content_type="image/jpeg")
                    title_poster = catalog_title_poster_key(slug)
                    if not title.get("poster_key") or not store.exists(
                        str(title["poster_key"])
                    ):
                        store.put_file(poster, title_poster, content_type="image/jpeg")
                        conn.execute(
                            "UPDATE titles SET poster_key = ? WHERE id = ?",
                            (title_poster, title_id),
                        )
            except Exception as exc:
                log.warning("poster extract failed for %s: %s", vkey, exc)
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
    key = video_key or original_video_key(slug_val, season, episode)
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
        ".m4v": "video/x-m4v",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }.get(path.suffix.lower(), "application/octet-stream")


def _poster_object_is_usable(store: Any, poster_key: str | None) -> bool:
    if not poster_key or not store.exists(poster_key):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "poster.jpg"
            store.get_file(poster_key, local)
            return _mean_luma(local) >= MIN_POSTER_MEAN_LUMA
    except Exception:
        return False


def ensure_episode_poster(
    conn: Connection,
    episode_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Build / refresh episode + title posters from the episode video."""
    ep = get_episode(conn, id=episode_id)
    if not ep:
        raise ValueError(f"episode not found: {episode_id}")
    title = get_title(conn, id=str(ep["title_id"]))
    if not title:
        raise ValueError(f"title not found for episode {episode_id}")
    video_key = ep.get("video_key")
    if not video_key:
        return {"skipped": True, "reason": "no video_key", "episode_id": episode_id}

    store = get_object_store()
    slug = str(title.get("slug") or "title")
    season = int(ep["season"])
    episode_n = int(ep["episode_number"])
    ep_poster = ep.get("poster_key")
    title_poster = title.get("poster_key")
    needs_ep = force or not _poster_object_is_usable(store, ep_poster)
    needs_title = force or not _poster_object_is_usable(store, title_poster)
    if not needs_ep and not needs_title:
        return {"skipped": True, "reason": "posters ok", "episode_id": episode_id}

    with tempfile.TemporaryDirectory() as tmp:
        suffix = Path(str(video_key)).suffix or ".mp4"
        local = Path(tmp) / f"video{suffix}"
        store.get_file(str(video_key), local)
        dur = float(ep.get("duration_s") or 0) or _probe_duration(local)
        if dur and not ep.get("duration_s"):
            conn.execute(
                "UPDATE episodes SET duration_s = ? WHERE id = ?",
                (float(dur), episode_id),
            )
        poster = Path(tmp) / "poster.jpg"
        _extract_poster_frame(local, poster, duration_s=dur)
        pkey = catalog_poster_key(slug, season, episode_n)
        store.put_file(poster, pkey, content_type="image/jpeg")
        conn.execute(
            "UPDATE episodes SET poster_key = ? WHERE id = ?",
            (pkey, episode_id),
        )
        tkey = catalog_title_poster_key(slug)
        if needs_title or not title_poster:
            store.put_file(poster, tkey, content_type="image/jpeg")
            conn.execute(
                "UPDATE titles SET poster_key = ? WHERE id = ?",
                (tkey, title["id"]),
            )
        conn.commit()
    return {
        "skipped": False,
        "episode_id": episode_id,
        "poster_key": pkey,
        "title_poster_key": tkey if (needs_title or not title_poster) else title_poster,
    }


def backfill_missing_posters(conn: Connection) -> dict[str, Any]:
    """Idempotently fill missing or near-black posters for all episodes."""
    store = get_object_store()
    eps = fetchall_dicts(
        conn,
        "SELECT * FROM episodes WHERE video_key IS NOT NULL AND video_key != '' "
        "ORDER BY created_at",
    )
    updated: list[str] = []
    skipped = 0
    failed: list[dict[str, str]] = []
    for ep in eps:
        title = get_title(conn, id=str(ep["title_id"]))
        needs = (
            not _poster_object_is_usable(store, ep.get("poster_key"))
            or not title
            or not _poster_object_is_usable(store, title.get("poster_key"))
        )
        if not needs:
            skipped += 1
            continue
        try:
            out = ensure_episode_poster(conn, str(ep["id"]))
            if out.get("skipped"):
                skipped += 1
            else:
                updated.append(str(ep["id"]))
        except Exception as exc:
            log.warning("poster backfill failed for %s: %s", ep.get("id"), exc)
            failed.append({"episode_id": str(ep["id"]), "error": str(exc)})
    return {
        "updated": updated,
        "updated_count": len(updated),
        "skipped": skipped,
        "failed": failed,
    }


def _ffprobe_json(path: Path) -> dict[str, Any]:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        text=True,
    )
    return json.loads(out)


def _normalize_codec(name: str | None) -> str:
    return (name or "").strip().lower().replace(" ", "")


def probe_browser_safety(path: Path) -> dict[str, Any]:
    """Classify a local media file for browser playback."""
    data = _ffprobe_json(path)
    fmt = str((data.get("format") or {}).get("format_name") or "").lower()
    video_codec = None
    audio_codec = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and video_codec is None:
            video_codec = _normalize_codec(stream.get("codec_name"))
        elif stream.get("codec_type") == "audio" and audio_codec is None:
            audio_codec = _normalize_codec(stream.get("codec_name"))
    fmt_tokens = {t.strip() for t in fmt.split(",") if t.strip()}
    in_mp4_like = bool(fmt_tokens & MP4_LIKE_FORMATS) or path.suffix.lower() in {
        ".mp4",
        ".m4v",
    }
    video_ok = video_codec in BROWSER_SAFE_VIDEO
    audio_ok = audio_codec is None or audio_codec in BROWSER_SAFE_AUDIO
    return {
        "format": fmt,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "in_mp4_like": in_mp4_like,
        "browser_safe_codecs": bool(video_ok and audio_ok),
        "duration_s": float((data.get("format") or {}).get("duration") or 0) or 0.0,
    }


def mp4_has_faststart(path: Path) -> bool:
    """True when an moov atom appears before mdat (web-friendly)."""
    try:
        with open(path, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    return False
                size = int.from_bytes(header[:4], "big")
                typ = header[4:8]
                if size == 1:
                    large = f.read(8)
                    if len(large) < 8:
                        return False
                    size = int.from_bytes(large, "big")
                    header_len = 16
                elif size == 0:
                    return typ == b"moov"
                else:
                    header_len = 8
                if typ == b"moov":
                    return True
                if typ == b"mdat":
                    return False
                skip = max(0, size - header_len)
                f.seek(skip, 1)
    except OSError:
        return False


def _run_ffmpeg(cmd: list[str]) -> None:
    timeout = _ffmpeg_timeout_s()
    threads = _ffmpeg_threads()
    env = os.environ.copy()
    # Cap libx264 thread pool even when -threads is ignored by some builds.
    env.setdefault("OMP_NUM_THREADS", str(threads))

    def _nice() -> None:
        try:
            os.nice(10)
        except OSError:
            pass

    kwargs: dict[str, Any] = {
        "check": True,
        "capture_output": True,
        "timeout": timeout,
        "env": env,
    }
    if hasattr(os, "nice"):
        kwargs["preexec_fn"] = _nice
    try:
        subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg timed out after {timeout:.0f}s") from exc


def prepare_browser_mp4(src: Path, dest: Path) -> str:
    """Ensure dest is H.264/AAC (or silent) MP4 with +faststart.

    Maps only the first video/audio streams, drops subs/attachments, caps at
    1080p, stereo AAC, and bounds CPU via -threads / nice / timeout.

    Returns action: 'copy' | 'remux' | 'transcode'.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    info = probe_browser_safety(src)
    threads = str(_ffmpeg_threads())
    map_av = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
    if info["browser_safe_codecs"] and info["in_mp4_like"] and mp4_has_faststart(src):
        # Still remux through maps so cover-art / extra audio never ship.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            *map_av,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-threads",
            threads,
            str(dest),
        ]
        try:
            _run_ffmpeg(cmd)
            if dest.is_file() and dest.stat().st_size > 0:
                return "copy"
        except (RuntimeError, subprocess.CalledProcessError):
            dest.unlink(missing_ok=True)
    if info["browser_safe_codecs"]:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            *map_av,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-threads",
            threads,
            str(dest),
        ]
        try:
            _run_ffmpeg(cmd)
            if dest.is_file() and dest.stat().st_size > 0:
                return "remux"
        except (RuntimeError, subprocess.CalledProcessError):
            dest.unlink(missing_ok=True)
    # Transcode to browser-safe H.264 + stereo AAC, capped at 1080p.
    dest.unlink(missing_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        *map_av,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        r"scale=-2:min(1080\,ih)",
        "-crf",
        "20",
        "-preset",
        "veryfast",
        "-threads",
        threads,
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    try:
        _run_ffmpeg(cmd)
    except (RuntimeError, subprocess.CalledProcessError):
        dest.unlink(missing_ok=True)
        cmd_no_audio = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            r"scale=-2:min(1080\,ih)",
            "-crf",
            "20",
            "-preset",
            "veryfast",
            "-threads",
            threads,
            "-an",
            "-movflags",
            "+faststart",
            str(dest),
        ]
        _run_ffmpeg(cmd_no_audio)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg failed to produce playable mp4 from {src}")
    return "transcode"


def _stream_store_to_file(store: ObjectStore, key: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with store.open_stream(key) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)
    return dest


def _copy_object(store: ObjectStore, src_key: str, dest_key: str) -> bool:
    """Copy one object to another key via a temp file. Returns True if copied."""
    if src_key == dest_key:
        return False
    if not store.exists(src_key):
        return False
    if store.exists(dest_key):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "obj"
        _stream_store_to_file(store, src_key, local)
        store.put_file(local, dest_key)
    return True


def migrate_catalog_object_keys(conn: Connection | None = None) -> dict[str, Any]:
    """Idempotently move video.mp4 / placements/ / moments/ to the new layout."""
    from product_placement.catalog_keys import (
        brand_slug,
        legacy_moment_frame_key,
        legacy_placement_clip_key,
        legacy_placement_edited_key,
        legacy_video_key,
        moment_frame_key,
        placement_clip_key,
        placement_edited_key,
        placement_frame_key,
    )

    own = conn is None
    if own:
        conn = get_connection()
        init_schema(conn)
    assert conn is not None
    store = get_object_store()
    moved: list[str] = []
    skipped = 0

    episodes = fetchall_dicts(conn, "SELECT * FROM episodes ORDER BY created_at")
    for ep in episodes:
        title = get_title(conn, id=str(ep["title_id"]))
        if not title:
            continue
        slug = str(title.get("slug") or "title")
        season = int(ep["season"])
        episode_n = int(ep["episode_number"])
        new_video = original_video_key(slug, season, episode_n)
        cur_video = ep.get("video_key") or ""

        # video.mp4 → original.mp4 (also accept missing DB key if legacy object exists)
        legacy = legacy_video_key(slug, season, episode_n)
        if cur_video.endswith("/video.mp4") or (
            (not cur_video or cur_video == legacy) and store.exists(legacy)
        ):
            src = cur_video if store.exists(str(cur_video)) else legacy
            if src != new_video:
                if _copy_object(store, str(src), new_video) or store.exists(new_video):
                    if store.exists(str(src)) and src != new_video:
                        try:
                            store.delete(str(src))
                        except Exception:
                            pass
                    conn.execute(
                        "UPDATE episodes SET video_key = ? WHERE id = ?",
                        (new_video, ep["id"]),
                    )
                    moved.append(f"video:{ep['id']}")
                else:
                    skipped += 1
            elif cur_video != new_video and store.exists(new_video):
                conn.execute(
                    "UPDATE episodes SET video_key = ? WHERE id = ?",
                    (new_video, ep["id"]),
                )
                moved.append(f"video-db:{ep['id']}")
            else:
                skipped += 1
        elif cur_video.endswith("/original.mp4"):
            skipped += 1

        # Moment frames: moments/<episode_id>/ → catalog/.../moments/
        moments = fetchall_dicts(
            conn,
            "SELECT * FROM moments WHERE episode_id = ? ORDER BY start_s",
            (ep["id"],),
        )
        for i, mom in enumerate(moments, start=1):
            old_key = mom.get("frame_key")
            new_key = moment_frame_key(slug, season, episode_n, i)
            candidates = []
            if old_key:
                candidates.append(str(old_key))
            candidates.append(legacy_moment_frame_key(str(ep["id"]), i))
            src = next((c for c in candidates if store.exists(c)), None)
            if not src:
                continue
            if src == new_key:
                skipped += 1
                continue
            if _copy_object(store, src, new_key) or store.exists(new_key):
                if src != new_key and store.exists(src):
                    try:
                        store.delete(src)
                    except Exception:
                        pass
                conn.execute(
                    "UPDATE moments SET frame_key = ? WHERE id = ?",
                    (new_key, mom["id"]),
                )
                moved.append(f"moment:{mom['id']}")

    placements = fetchall_dicts(conn, "SELECT * FROM placements ORDER BY created_at")
    for pl in placements:
        ep = get_episode(conn, id=str(pl.get("episode_id") or ""))
        if not ep:
            continue
        title = get_title(conn, id=str(ep["title_id"]))
        brand = get_brand(conn, id=str(pl["brand_id"]))
        if not title or not brand:
            continue
        slug = str(title.get("slug") or "title")
        bslug = brand_slug(str(brand.get("name") or ""), fallback=str(brand["id"]))
        season = int(ep["season"])
        episode_n = int(ep["episode_number"])
        pid = str(pl["id"])
        new_clip = placement_clip_key(slug, season, episode_n, bslug, pid)
        old_clip = pl.get("clip_key") or legacy_placement_clip_key(pid)
        if store.exists(str(old_clip)) and str(old_clip) != new_clip:
            if _copy_object(store, str(old_clip), new_clip) or store.exists(new_clip):
                try:
                    store.delete(str(old_clip))
                except Exception:
                    pass
                conn.execute(
                    "UPDATE placements SET clip_key = ? WHERE id = ?",
                    (new_clip, pid),
                )
                moved.append(f"clip:{pid}")
        elif pl.get("clip_key") != new_clip and store.exists(new_clip):
            conn.execute(
                "UPDATE placements SET clip_key = ? WHERE id = ?",
                (new_clip, pid),
            )
            moved.append(f"clip-db:{pid}")
        else:
            skipped += 1

        # edited_frame.jpg → edited.jpg (DB doesn't store this key; move object only)
        old_edited = legacy_placement_edited_key(pid)
        new_edited = placement_edited_key(slug, season, episode_n, bslug, pid)
        if store.exists(old_edited) and old_edited != new_edited:
            if _copy_object(store, old_edited, new_edited) or store.exists(new_edited):
                try:
                    store.delete(old_edited)
                except Exception:
                    pass
                moved.append(f"edited:{pid}")

        # Optional legacy source frame if present under placements/
        for legacy_frame in (
            f"placements/{pid}/frame.jpg",
            f"placements/{pid}/source_frame.jpg",
        ):
            new_frame = placement_frame_key(slug, season, episode_n, bslug, pid)
            if store.exists(legacy_frame) and legacy_frame != new_frame:
                if _copy_object(store, legacy_frame, new_frame) or store.exists(new_frame):
                    try:
                        store.delete(legacy_frame)
                    except Exception:
                        pass
                    moved.append(f"frame:{pid}")

    conn.commit()
    if own:
        conn.close()
    seed_log("migrate_catalog_object_keys moved=%s skipped=%s", len(moved), skipped)
    return {"moved": moved, "moved_count": len(moved), "skipped": skipped}


def _safe_etag_token(etag: str) -> str:
    return re.sub(r"[^\w.-]", "_", etag)[:80] or "unknown"


def _import_work_dir(key: str, etag: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    path = import_tmp_root() / "import" / digest / _safe_etag_token(etag)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_import_work_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def _download_movie_source(
    store: ObjectStore, key: str, dest: Path, *, size: int
) -> Path:
    """Download ``key`` to ``dest``, reusing a complete prior download for the same ETag."""
    if dest.is_file() and size > 0 and dest.stat().st_size == size:
        seed_log("resume download key=%s bytes=%s", key, size)
        return dest
    if dest.exists():
        dest.unlink(missing_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    partial.unlink(missing_ok=True)
    seed_log("download start key=%s dest=%s", key, dest)
    _stream_store_to_file(store, key, partial)
    partial.replace(dest)
    seed_log("download done key=%s bytes=%s", key, dest.stat().st_size)
    return dest


def _episode_import_attempts(ep: dict[str, Any] | None) -> int:
    if not ep:
        return 0
    try:
        return int(ep.get("import_attempts") or 0)
    except (TypeError, ValueError):
        return 0


def _should_skip_imported(
    prior: dict[str, Any] | None, *, key: str, dest_key: str, store: ObjectStore
) -> bool:
    """True when this movies/ object already has a playable catalog original."""
    if not prior:
        return False
    status = str(prior.get("suggest_status") or "").lower()
    vkey = str(prior.get("video_key") or "")
    if status == "processing":
        return False
    if status == "failed":
        # Retry failed imports unless attempts are exhausted (handled by caller).
        if vkey == dest_key or store.exists(dest_key):
            return True
        return False
    if vkey == dest_key or (vkey and not vkey.startswith(MOVIES_PREFIX) and store.exists(vkey)):
        return True
    if store.exists(dest_key):
        return True
    return False


def import_movies_from_store(
    conn: Connection | None = None,
    *,
    prefix: str = MOVIES_PREFIX,
) -> dict[str, Any]:
    """Import movies/ uploads into catalog/<slug>/s01e01/original.mp4."""
    own = conn is None
    if own:
        conn = get_connection()
        init_schema(conn)
    assert conn is not None

    store = get_object_store()
    keys = store.list_keys(prefix)
    created: list[dict[str, Any]] = []
    skipped_existing = 0
    skipped_max_attempts = 0
    ignored = 0
    failed: list[dict[str, str]] = []
    max_attempts = _import_max_attempts()

    seed_log("import-movies scan prefix=%s keys=%s", prefix, len(keys))

    for key in keys:
        rel = key[len(prefix) :] if key.startswith(prefix) else key
        if not rel or rel.endswith("/"):
            continue
        basename = Path(rel).name
        ext = Path(basename).suffix.lower()
        if ext not in MOVIE_VIDEO_EXTS:
            ignored += 1
            continue

        display_name = humanize_movie_filename(basename)
        slug = slugify(display_name)
        dest_key = original_video_key(slug, 1, 1)

        title = get_title(conn, slug=slug)
        prior = None
        if title:
            prior = fetchone_dict(
                conn,
                "SELECT * FROM episodes WHERE title_id = ? AND season = 1 AND episode_number = 1",
                (title["id"],),
            )

        if _should_skip_imported(prior, key=key, dest_key=dest_key, store=store):
            skipped_existing += 1
            continue

        attempts = _episode_import_attempts(prior)
        status = str((prior or {}).get("suggest_status") or "").lower()
        if status == "failed" and attempts >= max_attempts:
            skipped_max_attempts += 1
            seed_log(
                "skip movie import %s: max attempts %s",
                key,
                attempts,
            )
            continue

        seed_log("import movie key=%s slug=%s attempt=%s", key, slug, attempts + 1)
        work_dir: Path | None = None
        try:
            meta = store.head(key)
            etag = str(meta.get("etag") or "unknown")
            size = int(meta.get("size") or 0)
            work_dir = _import_work_dir(key, etag)

            if not title:
                title = create_title(
                    conn,
                    name=display_name,
                    kind="movie",
                    slug=slug,
                    synopsis=f"Imported from {key}",
                    source="hosted",
                )
            if prior and status in {"processing", "failed"}:
                ep = prior
                conn.execute(
                    "UPDATE episodes SET video_key = ?, suggest_status = ?, "
                    "import_attempts = ? WHERE id = ?",
                    (key, "processing", attempts + 1, ep["id"]),
                )
                conn.commit()
                ep = get_episode(conn, id=str(ep["id"])) or ep
            else:
                ep = create_episode(
                    conn,
                    title_id=title["id"],
                    name=display_name,
                    season=1,
                    episode=1,
                    video_key=None,
                    suggest_status="processing",
                )
                conn.execute(
                    "UPDATE episodes SET video_key = ?, suggest_status = ?, "
                    "import_attempts = ? WHERE id = ?",
                    (key, "processing", attempts + 1, ep["id"]),
                )
                conn.commit()
                ep = get_episode(conn, id=str(ep["id"])) or ep

            src_local = work_dir / f"source{ext}"
            _download_movie_source(store, key, src_local, size=size)
            playable = work_dir / "original.mp4"
            if playable.exists():
                playable.unlink(missing_ok=True)
            action = prepare_browser_mp4(src_local, playable)
            seed_log("movie %s prepare action=%s", slug, action)
            store.put_file(playable, dest_key, content_type="video/mp4")
            dur = _probe_duration(playable)
            conn.execute(
                "UPDATE episodes SET video_key = ?, duration_s = ?, suggest_status = ? "
                "WHERE id = ?",
                (dest_key, float(dur or 0.0), "queued", ep["id"]),
            )
            conn.commit()
            try:
                ensure_episode_poster(conn, str(ep["id"]))
            except Exception as exc:
                log.warning("poster failed for %s: %s", key, exc)
            ep = get_episode(conn, id=str(ep["id"])) or ep
            _cleanup_import_work_dir(work_dir)
            work_dir = None

            created.append(
                {
                    "title": get_title(conn, id=title["id"]),
                    "episode": ep,
                    "source_key": key,
                    "video_key": dest_key,
                    "name": display_name,
                    "slug": slug,
                }
            )
            seed_log("import movie done key=%s slug=%s", key, slug)
        except Exception as exc:
            seed_log("skip movie import %s: %s", key, exc)
            log.warning("skip movie import %s: %s", key, exc)
            failed.append({"key": key, "error": str(exc)})
            if title:
                bad = fetchone_dict(
                    conn,
                    "SELECT id, import_attempts FROM episodes WHERE title_id = ? AND season = 1 "
                    "AND episode_number = 1",
                    (title["id"],),
                )
                if bad:
                    conn.execute(
                        "UPDATE episodes SET suggest_status = ? WHERE id = ?",
                        ("failed", bad["id"]),
                    )
                    conn.commit()
            # Keep complete temp download for the same ETag; drop broken outputs.
            if work_dir is not None:
                playable = work_dir / "original.mp4"
                playable.unlink(missing_ok=True)

    from product_placement.jobs import reset_stale_suggest_running

    reclaimed = reset_stale_suggest_running(conn)
    need_suggest = fetchall_dicts(
        conn,
        "SELECT * FROM episodes WHERE suggest_status IN ('queued', 'failed') "
        "OR suggest_status IS NULL OR suggest_status = '' "
        "ORDER BY created_at",
    )
    # Suggest-queue should not re-drive import failures (movies/ keys).
    need_suggest = [
        ep
        for ep in need_suggest
        if not str(ep.get("video_key") or "").startswith(MOVIES_PREFIX)
        and str(ep.get("suggest_status") or "").lower() != "processing"
    ]
    titles_out = list_titles(conn, watchable_only=True)
    if own:
        conn.close()
    return {
        "skipped": len(created) == 0,
        "imported": len(created),
        "episodes": [c["episode"] for c in created if c.get("episode")],
        "items": created,
        "suggest_queue": need_suggest,
        "reclaimed_running": reclaimed,
        "titles": titles_out,
        "skipped_existing": skipped_existing,
        "skipped_max_attempts": skipped_max_attempts,
        "ignored_non_video": ignored,
        "failed": failed,
        "prefix": prefix,
    }


# ---------------------------------------------------------------------------
# Import lock + background worker
# ---------------------------------------------------------------------------


def _import_lock_path() -> Path:
    return data_dir() / IMPORT_LOCK_NAME


def _import_wake_path() -> Path:
    return data_dir() / IMPORT_WAKE_NAME


def request_movies_import() -> dict[str, str]:
    """Wake the background import worker (or a one-shot import process)."""
    path = _import_wake_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(utc_now() + "\n", encoding="utf-8")
    seed_log("import wake requested path=%s", path)
    return {"wake": str(path)}


def _clear_import_wake() -> None:
    path = _import_wake_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def movies_import_lock(*, blocking: bool = True) -> Iterator[bool]:
    """Exclusive lock so boot, poller, and manual trigger never import concurrently."""
    import fcntl

    path = _import_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    acquired = False
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fh.fileno(), flags)
            acquired = True
        except BlockingIOError:
            acquired = False
        if acquired:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()} {utc_now()}\n")
            fh.flush()
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def import_movies_with_lock(
    *, blocking: bool = True, conn: Connection | None = None
) -> dict[str, Any]:
    with movies_import_lock(blocking=blocking) as acquired:
        if not acquired:
            seed_log("import-movies skipped: lock held")
            return {
                "skipped": True,
                "reason": "import already running",
                "imported": 0,
                "episodes": [],
                "suggest_queue": [],
                "failed": [],
            }
        _clear_import_wake()
        return import_movies_from_store(conn)


def spawn_episode_suggest_detached(episode_id: str) -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "product_placement.jobs",
            "episode-suggest",
            "--episode-id",
            str(episode_id),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH") or str(Path(__file__).resolve().parents[1])},
    )


def _drain_suggest_queue(result: dict[str, Any]) -> None:
    queue = result.get("suggest_queue") or result.get("episodes") or []
    for ep in queue:
        if not isinstance(ep, dict):
            continue
        eid = ep.get("id")
        if not eid:
            continue
        st = str(ep.get("suggest_status") or "queued").lower()
        if st in {"queued", "failed", ""}:
            seed_log("spawn episode-suggest %s", eid)
            try:
                spawn_episode_suggest_detached(str(eid))
            except Exception as exc:
                log.warning("spawn suggest failed for %s: %s", eid, exc)


def _wait_poll_or_wake(seconds: float) -> None:
    deadline = time.monotonic() + max(0.1, seconds)
    wake = _import_wake_path()
    while time.monotonic() < deadline:
        if wake.exists():
            _clear_import_wake()
            seed_log("import worker woken early")
            return
        time.sleep(0.5)


def run_movies_import_worker(*, poll_s: float | None = None) -> None:
    """Long-lived poller: import new movies/ objects one pass at a time."""
    _configure_catalog_logging()
    interval = poll_s if poll_s is not None else _import_poll_s()
    seed_log("import worker start poll_s=%s tmp=%s", interval, import_tmp_root())
    while True:
        try:
            result = import_movies_with_lock(blocking=True)
            imported = int(result.get("imported") or 0)
            seed_log(
                "import worker pass imported=%s skipped_existing=%s failed=%s",
                imported,
                result.get("skipped_existing"),
                len(result.get("failed") or []),
            )
            if imported:
                _drain_suggest_queue(result)
        except Exception as exc:
            seed_log("import worker error: %s", exc)
            log.exception("import worker error")
        _wait_poll_or_wake(interval)


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
        from product_placement.catalog_keys import brand_asset_key

        key = brand_asset_key(bid, p.name)
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
    from product_placement.jobs import fail_stale_placements

    fail_stale_placements(conn)
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
            held = bool(str(existing.get("error") or "").strip())
            # Already in-flight or done — do not re-queue (avoids double Seedance bills).
            # Exception: queued/failed with an error (e.g. key missing) may retry.
            if cur in {"queued", "rendering", "ready"} and not (cur == "queued" and held):
                row = get_placement(conn, id=existing["id"])
                assert row is not None
                row["spawn"] = False
                return row
            new_status = "queued"
            spawn = True
        else:
            new_status = status
        conn.execute(
            "UPDATE placements SET status = ?, updated_at = ?, episode_id = ?, error = ? WHERE id = ?",
            (new_status, now, ep_id, None if status == "approved" else existing.get("error"), existing["id"]),
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
# Campaigns (brand × episode)
# ---------------------------------------------------------------------------

CAMPAIGN_STATUSES = frozenset({"draft", "in_progress", "live"})


def _derive_campaign_status(conn: Connection, *, brand_id: str, episode_id: str) -> str:
    rows = fetchall_dicts(
        conn,
        "SELECT status FROM placements WHERE brand_id = ? AND episode_id = ?",
        (brand_id, episode_id),
    )
    statuses = {str(r.get("status") or "") for r in rows}
    if "ready" in statuses:
        return "live"
    if statuses & {"queued", "rendering", "approved", "failed"}:
        return "in_progress"
    if "rejected" in statuses:
        return "in_progress"
    return "draft"


def list_campaigns(conn: Connection, *, brand_id: str) -> list[dict[str, Any]]:
    return fetchall_dicts(
        conn,
        "SELECT * FROM campaigns WHERE brand_id = ? ORDER BY updated_at DESC",
        (brand_id,),
    )


def get_campaign(
    conn: Connection, *, brand_id: str, episode_id: str
) -> dict[str, Any] | None:
    return fetchone_dict(
        conn,
        "SELECT * FROM campaigns WHERE brand_id = ? AND episode_id = ?",
        (brand_id, episode_id),
    )


def upsert_campaign(
    conn: Connection,
    *,
    brand_id: str,
    episode_id: str,
    title_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    ep = get_episode(conn, id=episode_id)
    if not ep:
        raise ValueError(f"episode not found: {episode_id}")
    tid = title_id or str(ep.get("title_id") or "")
    if not tid:
        raise ValueError("title_id required")

    prefer = (status or "").strip().lower() or None
    if prefer and prefer not in CAMPAIGN_STATUSES:
        raise ValueError(f"invalid campaign status: {status}")

    from_placements = _derive_campaign_status(
        conn, brand_id=brand_id, episode_id=episode_id
    )
    # Placement progress wins over an explicit draft.
    if from_placements in {"live", "in_progress"}:
        final = from_placements
    elif prefer:
        final = prefer
    else:
        final = "draft"

    now = utc_now()
    existing = get_campaign(conn, brand_id=brand_id, episode_id=episode_id)
    if existing:
        conn.execute(
            "UPDATE campaigns SET status = ?, title_id = ?, updated_at = ? WHERE id = ?",
            (final, tid, now, existing["id"]),
        )
        conn.commit()
        return get_campaign(conn, brand_id=brand_id, episode_id=episode_id)  # type: ignore[return-value]

    cid = new_id()
    conn.execute(
        "INSERT INTO campaigns (id, brand_id, title_id, episode_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, brand_id, tid, episode_id, final, now, now),
    )
    conn.commit()
    return get_campaign(conn, brand_id=brand_id, episode_id=episode_id)  # type: ignore[return-value]


def refresh_campaign_for_placement(conn: Connection, placement_id: str) -> dict[str, Any] | None:
    pl = get_placement(conn, id=placement_id)
    if not pl:
        return None
    return upsert_campaign(
        conn,
        brand_id=str(pl["brand_id"]),
        episode_id=str(pl["episode_id"]),
    )


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
        raise FileNotFoundError(f"title not found: {title_slug}")
    ep = fetchone_dict(
        conn,
        "SELECT * FROM episodes WHERE title_id = ? AND season = ? AND episode_number = ?",
        (title["id"], int(season), int(episode)),
    )
    if not ep or not episode_is_watchable(ep):
        raise FileNotFoundError(f"episode not found: s{season}e{episode}")

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
        start_s = r.get("clip_start_s")
        end_s = r.get("clip_end_s")
        if start_s is None:
            start_s = r["start_s"]
        if end_s is None:
            end_s = r["end_s"]
        placements.append(
            {
                "id": r["id"],
                "brand_id": r["brand_id"],
                "moment_id": r["moment_id"],
                "status": r["status"],
                "start_s": float(start_s),
                "end_s": float(end_s),
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
        # Direct .mp4 was removed from blender.org; the peach tree now ships a zip.
        "url": "https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4.zip",
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


def _extract_zip_media(archive: Path, dest_dir: Path) -> Path:
    """Extract the first playable video from a zip next to the archive."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        names = [
            n
            for n in zf.namelist()
            if not n.endswith("/") and Path(n).suffix.lower() in MOVIE_VIDEO_EXTS
        ]
        if not names:
            raise RuntimeError(f"no video file inside zip: {archive}")
        # Prefer smaller / named sample when multiple entries exist.
        names.sort(key=lambda n: (0 if "320" in n or "480" in n else 1, len(n)))
        member = names[0]
        target = dest_dir / Path(member).name
        with zf.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)
        seed_log("extracted %s -> %s (%s bytes)", member, target.name, target.stat().st_size)
        return target


def _download(url: str, dest: Path, *, timeout: float = 600.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    seed_log("downloading %s -> %s", url, dest.name)
    dest.unlink(missing_ok=True)
    # Prefer curl for progress / resume friendliness when available.
    try:
        subprocess.run(
            [
                "curl",
                "-L",
                "--fail",
                "--max-time",
                str(int(timeout)),
                "-o",
                str(dest),
                url,
            ],
            check=True,
            capture_output=True,
        )
        if not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError(f"curl left empty file for {url}")
    except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError) as exc:
        seed_log("curl failed (%s), falling back to urllib", exc)
        dest.unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except urllib.error.HTTPError as http_exc:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"download failed HTTP {http_exc.code} for {url}") from http_exc
        if not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError(f"download produced empty file for {url}")

    # Zip wrappers (BBB peach sample): extract the media and return that path.
    if dest.suffix.lower() == ".zip" or zipfile.is_zipfile(dest):
        media = _extract_zip_media(dest, dest.parent)
        return media
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
    """Seed Blender open-movie titles/episodes. Idempotent; repairs orphan titles."""
    _configure_catalog_logging()
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
    seed_log("seed-blender start force=%s", force)

    for src in BLENDER_SOURCES:
        title = get_title(conn, slug=src["slug"])
        missing_specs = []
        for ep_spec in src["episodes"]:
            prior = None
            if title:
                prior = fetchone_dict(
                    conn,
                    "SELECT id FROM episodes WHERE title_id = ? AND season = ? "
                    "AND episode_number = ?",
                    (title["id"], ep_spec["season"], ep_spec["episode"]),
                )
            if not prior:
                missing_specs.append(ep_spec)
        if not missing_specs and not force:
            skipped_existing += len(src["episodes"])
            seed_log("skip %s: episodes already present", src["slug"])
            continue

        url = src["url"]
        url_name = Path(urlparse_path(url)).name or f"{src['slug']}.mp4"
        download_dest = cache / url_name
        # Cached unzipped media from a prior zip download.
        media_hint = cache / f"{src['slug']}.mp4"
        raw_path: Path | None = None
        if not force:
            for candidate in (media_hint, download_dest):
                if candidate.is_file() and candidate.stat().st_size > 0:
                    if candidate.suffix.lower() == ".zip" or zipfile.is_zipfile(candidate):
                        try:
                            raw_path = _extract_zip_media(candidate, cache)
                            break
                        except Exception:
                            continue
                    raw_path = candidate
                    break

        if raw_path is None or force or raw_path.stat().st_size == 0:
            try:
                raw_path = download_fn(url, download_dest)
            except Exception as exc:
                seed_log("skip %s: download failed: %s", src["slug"], exc)
                # Do not create a title when media is unavailable (avoids orphans).
                continue

        # Only create the title after media is in hand.
        if not title:
            title = create_title(
                conn,
                name=src["name"],
                kind=src["kind"],
                synopsis=src["synopsis"],
                attribution=ATTRIBUTION_BLENDER,
                slug=src["slug"],
                source="test",
            )
            seed_log("created title %s", src["slug"])

        for ep_spec in src["episodes"]:
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
                seed_log("trim failed for %s, using full file: %s", src["slug"], exc)
                clip = raw_path

            try:
                ep = create_episode(
                    conn,
                    title_id=title["id"],
                    name=ep_spec["name"],
                    season=ep_spec["season"],
                    episode=ep_spec["episode"],
                    video_path=clip,
                    suggest_status="queued",
                )
            except Exception as exc:
                seed_log("create_episode failed for %s s%se%s: %s", src["slug"], ep_spec["season"], ep_spec["episode"], exc)
                continue
            created.append(ep)
            seed_log(
                "created episode %s s%02de%02d key=%s",
                src["slug"],
                ep_spec["season"],
                ep_spec["episode"],
                ep.get("video_key"),
            )
            title = get_title(conn, id=title["id"]) or title

    # Fill missing / near-black posters (e.g. BBB missing title poster, ToS dark).
    poster_backfill = backfill_missing_posters(conn)
    seed_log("poster backfill updated=%s", poster_backfill.get("updated_count"))

    titles_out = list_titles(conn, source="all")
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
    seed_log(
        "seed-blender done created=%s skipped_existing=%s",
        len(created),
        skipped_existing,
    )
    return {
        "skipped": len(created) == 0,
        "reason": "all episodes already present" if not created else None,
        "episodes_created": len(created),
        "episodes": created or need_suggest,
        "suggest_queue": need_suggest,
        "reclaimed_running": reclaimed,
        "titles": titles_out,
        "skipped_existing": skipped_existing,
        "poster_backfill": poster_backfill,
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
    lt = add("list-titles")
    lt.add_argument(
        "--watchable",
        action="store_true",
        help="Only titles with at least one ready (playable) episode",
    )
    lt.add_argument(
        "--source",
        choices=VIDEO_SOURCE_CHOICES,
        default=None,
        help="Filter by title source (default: VIDEO_SOURCE env or hosted)",
    )

    gt = add("get-title")
    gt.add_argument("--id", default=None)
    gt.add_argument("--slug", default=None)

    ct = add("create-title")
    ct.add_argument("--name", required=True)
    ct.add_argument("--kind", default="movie")
    ct.add_argument("--synopsis", default="")
    ct.add_argument("--attribution", default="")
    ct.add_argument(
        "--source",
        choices=TITLE_SOURCES,
        default="test",
        help="Title source: test (seed/demo) or hosted (movies/ import)",
    )

    le = add("list-episodes")
    le.add_argument("--title-id", required=True)
    le.add_argument(
        "--watchable",
        action="store_true",
        help="Only playable episodes (hide processing/failed/movies/)",
    )

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
        help="Existing object key. Defaults to catalog/<slug>/sXXeYY/original.mp4",
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

    uppl = add("update-placement")
    uppl.add_argument("--id", required=True)
    uppl.add_argument("--status", default=None)
    uppl.add_argument("--error", default=None)
    uppl.add_argument("--clear-error", action="store_true")
    uppl.add_argument("--test-mode", type=int, default=None)

    lc = add("list-campaigns")
    lc.add_argument("--brand-id", required=True)

    uc = add("upsert-campaign")
    uc.add_argument("--brand-id", required=True)
    uc.add_argument("--episode-id", required=True)
    uc.add_argument("--title-id", default=None)
    uc.add_argument(
        "--status",
        default=None,
        choices=("draft", "in_progress", "live"),
    )

    wp = add("get-watch-payload")
    wp.add_argument("--title-slug", required=True)
    wp.add_argument("--season", type=int, required=True)
    wp.add_argument("--episode", type=int, required=True)
    wp.add_argument("--brand-id", default=None)

    add("seed-blender")
    im = add("import-movies")
    im.add_argument(
        "--once",
        action="store_true",
        help="Run a single locked import pass (default)",
    )
    im.add_argument(
        "--non-blocking",
        action="store_true",
        help="Return immediately if another import holds the lock",
    )
    iw = add("import-movies-worker")
    iw.add_argument(
        "--poll-s",
        type=float,
        default=None,
        help="Seconds between polls (default IMPORT_POLL_S or 60)",
    )
    iw.add_argument(
        "--request",
        action="store_true",
        help="Only touch the wake file and exit",
    )
    add("backfill-posters")
    add("migrate-catalog-keys")

    return p


def main(argv: list[str] | None = None) -> None:
    _configure_catalog_logging()
    args = build_parser().parse_args(argv)
    conn = get_connection()
    try:
        if args.command == "init-db":
            init_schema(conn)
            imported = migrate_file_brands(conn)
            _print_json({"ok": True, "imported_brands": imported})
            return

        if args.command == "list-titles":
            _print_json(
                list_titles(
                    conn,
                    watchable_only=bool(args.watchable),
                    source=args.source,
                )
            )
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
                    source=args.source,
                )
            )
            return

        if args.command == "list-episodes":
            _print_json(
                list_episodes(
                    conn,
                    title_id=args.title_id,
                    watchable_only=bool(args.watchable),
                )
            )
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
            row = upsert_placement(
                conn,
                brand_id=args.brand_id,
                moment_id=args.moment_id,
                status=args.status,
            )
            try:
                refresh_campaign_for_placement(conn, row["id"])
            except Exception as exc:
                log.warning("campaign refresh failed: %s", exc)
            _print_json(row)
            return

        if args.command == "update-placement":
            fields: dict[str, Any] = {}
            if args.status:
                fields["status"] = args.status
            if args.clear_error:
                fields["error"] = None
            elif args.error is not None:
                fields["error"] = args.error
            if args.test_mode is not None:
                fields["test_mode"] = int(args.test_mode)
            row = update_placement(conn, args.id, **fields)
            try:
                refresh_campaign_for_placement(conn, args.id)
            except Exception as exc:
                log.warning("campaign refresh failed: %s", exc)
            _print_json(row)
            return

        if args.command == "list-campaigns":
            _print_json(list_campaigns(conn, brand_id=args.brand_id))
            return

        if args.command == "upsert-campaign":
            _print_json(
                upsert_campaign(
                    conn,
                    brand_id=args.brand_id,
                    episode_id=args.episode_id,
                    title_id=args.title_id,
                    status=args.status,
                )
            )
            return

        if args.command == "get-watch-payload":
            try:
                _print_json(
                    get_watch_payload(
                        conn,
                        title_slug=args.title_slug,
                        season=args.season,
                        episode=args.episode,
                        brand_id=args.brand_id,
                    )
                )
            except FileNotFoundError as exc:
                raise SystemExit(json.dumps({"error": str(exc)})) from exc
            return

        if args.command == "seed-blender":
            _print_json(seed_blender_open_movies(conn))
            return

        if args.command == "import-movies":
            _print_json(
                import_movies_with_lock(
                    blocking=not bool(args.non_blocking),
                    conn=conn,
                )
            )
            return

        if args.command == "import-movies-worker":
            if args.request:
                _print_json(request_movies_import())
                return
            conn.close()
            run_movies_import_worker(poll_s=args.poll_s)
            return

        if args.command == "backfill-posters":
            _print_json(backfill_missing_posters(conn))
            return

        if args.command == "migrate-catalog-keys":
            _print_json(migrate_catalog_object_keys(conn))
            return

        raise SystemExit(f"unknown command {args.command}")
    finally:
        if args.command != "import-movies-worker" or getattr(args, "request", False):
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
