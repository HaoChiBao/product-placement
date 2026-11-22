"""Regression: SQL with literal % must not reach psycopg after ? → %s rewrite."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from product_placement.catalog import MOVIES_PREFIX, list_titles
from product_placement.db import get_connection, init_schema


def psycopg_unsafe_percent_tokens(sql: str) -> list[str]:
    """Mirror psycopg: only %% / %s / %b / %t are allowed after placeholder rewrite."""
    bad: list[str] = []
    i = 0
    while i < len(sql):
        if sql[i] != "%":
            i += 1
            continue
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if nxt == "%":
            i += 2
            continue
        if nxt in {"s", "b", "t"}:
            i += 2
            continue
        bad.append(repr(sql[i : i + 3]))
        i += 1
    return bad


def test_watchable_titles_sql_safe_after_qmark_rewrite():
    """The bug was NOT LIKE 'movies/%' — literal % becomes invalid for psycopg."""
    sql = (
        "SELECT DISTINCT t.* FROM titles t "
        "INNER JOIN episodes e ON e.title_id = t.id "
        "WHERE e.video_key IS NOT NULL AND TRIM(e.video_key) != '' "
        "AND e.video_key NOT LIKE ? "
        "AND LOWER(COALESCE(e.suggest_status, '')) NOT IN ('processing', 'failed') "
        "ORDER BY t.created_at"
    )
    params = (f"{MOVIES_PREFIX}%",)
    assert params == ("movies/%",)
    converted = sql.replace("?", "%s")
    assert "NOT LIKE %s" in converted
    assert psycopg_unsafe_percent_tokens(converted) == []
    # The broken form must be detected by the helper.
    broken = converted.replace("NOT LIKE %s", "NOT LIKE 'movies/%'")
    assert psycopg_unsafe_percent_tokens(broken)


def test_list_titles_watchable_runs_on_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    conn = get_connection()
    init_schema(conn)
    assert list_titles(conn, watchable_only=True) == []
    conn.close()


def test_catalog_sql_string_literals_have_no_raw_percent():
    """Static guard: SQL-ish string literals in catalog.py must not embed raw %."""
    src_path = (
        Path(__file__).resolve().parents[1] / "src" / "product_placement" / "catalog.py"
    )
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    sql_hint = re.compile(
        r"\b(SELECT|INSERT|UPDATE|DELETE|WITH|WHERE|LIKE|JOIN)\b",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        if not sql_hint.search(text):
            continue
        if "%" not in text:
            continue
        stripped = text.replace("%%", "")
        if "%" in stripped:
            offenders.append(text.strip()[:120])
    assert offenders == [], f"SQL literals with raw %: {offenders}"


def test_psycopg_unsafe_helper_catches_movies_like():
    bad = "AND e.video_key NOT LIKE 'movies/%'"
    assert psycopg_unsafe_percent_tokens(bad)
    good = "AND e.video_key NOT LIKE %s"
    assert psycopg_unsafe_percent_tokens(good) == []
