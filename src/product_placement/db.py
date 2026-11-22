"""Catalog DB: Postgres via DATABASE_URL, else SQLite under DATA_DIR."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_dir() -> Path:
    raw = (os.environ.get("DATA_DIR") or "").strip()
    root = Path(raw) if raw else Path("runs") / "ui"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _database_url() -> str | None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    return url or None


def is_postgres() -> bool:
    return _database_url() is not None


class Connection:
    """Thin wrapper so callers can use ``?`` placeholders on both backends."""

    def __init__(self, raw: Any, *, postgres: bool) -> None:
        self._raw = raw
        self.postgres = postgres

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        params = list(params or [])
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self._raw.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> Any:
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self._raw.executemany(sql, seq_of_params)

    def executescript(self, script: str) -> Any:
        if self.postgres:
            # psycopg doesn't have executescript; split on semicolons carefully.
            for stmt in _split_sql(script):
                if stmt.strip():
                    self._raw.execute(stmt)
            return None
        return self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def cursor(self) -> Any:
        return Cursor(self._raw.cursor(), postgres=self.postgres)

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class Cursor:
    def __init__(self, raw: Any, *, postgres: bool) -> None:
        self._raw = raw
        self.postgres = postgres

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        params = list(params or [])
        if self.postgres:
            sql = sql.replace("?", "%s")
        self._raw.execute(sql, params)
        return self

    def fetchone(self) -> Any:
        return self._raw.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._raw.fetchall())

    @property
    def description(self) -> Any:
        return self._raw.description

    @property
    def lastrowid(self) -> Any:
        return getattr(self._raw, "lastrowid", None)


def _split_sql(script: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            chunk = "\n".join(buf)
            for piece in chunk.split(";"):
                piece = piece.strip()
                if piece:
                    parts.append(piece)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def get_connection() -> Connection:
    url = _database_url()
    if url:
        import psycopg

        raw = psycopg.connect(url)
        return Connection(raw, postgres=True)

    path = data_dir() / "catalog.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return Connection(raw, postgres=False)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    id TEXT PRIMARY KEY,
    kind TEXT,
    name TEXT,
    slug TEXT UNIQUE,
    synopsis TEXT,
    poster_key TEXT,
    attribution TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    title_id TEXT,
    season INT,
    episode_number INT,
    name TEXT,
    video_key TEXT,
    poster_key TEXT,
    duration_s REAL,
    suggest_status TEXT,
    created_at TEXT,
    import_attempts INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS moments (
    id TEXT PRIMARY KEY,
    episode_id TEXT,
    start_s REAL,
    end_s REAL,
    region_json TEXT,
    whats_there TEXT,
    score REAL,
    reason TEXT,
    frame_key TEXT,
    frame_w INT,
    frame_h INT
);

CREATE TABLE IF NOT EXISTS brands (
    id TEXT PRIMARY KEY,
    name TEXT,
    brief TEXT,
    rules_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS brand_assets (
    id TEXT PRIMARY KEY,
    brand_id TEXT,
    filename TEXT,
    object_key TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS placements (
    id TEXT PRIMARY KEY,
    brand_id TEXT,
    moment_id TEXT,
    episode_id TEXT,
    status TEXT,
    clip_key TEXT,
    error TEXT,
    test_mode INT,
    backend TEXT,
    created_at TEXT,
    updated_at TEXT
);
"""


def init_schema(conn: Connection) -> None:
    conn.executescript(_SCHEMA)
    _ensure_column(conn, "episodes", "import_attempts", "INT DEFAULT 0")
    conn.commit()


def _ensure_column(conn: Connection, table: str, column: str, decl: str) -> None:
    cur = conn.cursor()
    if conn.postgres:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            (table, column),
        )
        if cur.fetchone():
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _brand_exists(conn: Connection, brand_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM brands WHERE id = ?", (brand_id,))
    return cur.fetchone() is not None


def migrate_file_brands(conn: Connection) -> list[str]:
    """Import DATA_DIR/brands/*/brand.json into the DB when missing; copy assets to object store."""
    from product_placement.object_store import get_object_store

    brands_root = data_dir() / "brands"
    if not brands_root.is_dir():
        return []

    store = get_object_store()
    imported: list[str] = []
    for child in sorted(brands_root.iterdir()):
        meta = child / "brand.json"
        if not meta.is_file():
            continue
        raw = json.loads(meta.read_text())
        brand_id = str(raw.get("id") or child.name)
        if _brand_exists(conn, brand_id):
            continue

        name = str(raw.get("name") or brand_id)
        brief = str(raw.get("brief") or "")
        rules = raw.get("rules") or {}
        now = utc_now()
        conn.execute(
            "INSERT INTO brands (id, name, brief, rules_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (brand_id, name, brief, json.dumps(rules), now),
        )
        for filename in raw.get("assets") or []:
            local = child / str(filename)
            if not local.is_file():
                continue
            asset_id = uuid.uuid4().hex[:12]
            from product_placement.catalog_keys import brand_asset_key

            key = brand_asset_key(brand_id, str(filename))
            content_type = _guess_content_type(local)
            store.put_file(local, key, content_type=content_type)
            conn.execute(
                "INSERT INTO brand_assets (id, brand_id, filename, object_key, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (asset_id, brand_id, str(filename), key, now),
            )
        imported.append(brand_id)

    conn.commit()
    return imported


def _guess_content_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
    }.get(suffix)


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    # psycopg tuple — caller should use dict rows; fall back empty
    return {}


def fetchall_dicts(conn: Connection, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        return []
    if conn.postgres:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    return [dict(r) for r in rows]


def fetchone_dict(conn: Connection, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    rows = fetchall_dicts(conn, sql, params)
    return rows[0] if rows else None


def new_id() -> str:
    return uuid.uuid4().hex[:12]
