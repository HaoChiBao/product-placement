"""Catalog CLI must keep JSON on stdout; logs go to stderr."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from product_placement.catalog import _configure_catalog_logging, seed_log
from product_placement.db import get_connection, init_schema
from product_placement.object_store import reset_object_store


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


def test_configure_catalog_logging_uses_stderr(catalog_env, capsys):
    _configure_catalog_logging()
    logging.getLogger("product_placement.catalog").info("hello-stderr")
    seed_log("seed-line %s", 1)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello-stderr" in captured.err
    assert "[catalog-seed] seed-line 1" in captured.err


def test_cli_list_titles_stdout_is_pure_json(catalog_env, monkeypatch):
    data = catalog_env[1]
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "DATA_DIR": str(data),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    env.pop("DATABASE_URL", None)
    for key in (
        "AWS_S3_BUCKET_NAME",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL_S3",
    ):
        env.pop(key, None)

    # Emit a log line during the same process startup path as production CLI.
    code = (
        "from product_placement.catalog import main, _configure_catalog_logging; "
        "import logging; "
        "_configure_catalog_logging(); "
        "logging.getLogger('product_placement.object_store').info('pre-json noise'); "
        "main(['list-titles'])"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "[catalog]" not in res.stdout
    assert "[catalog-seed]" not in res.stdout
    assert res.stdout.lstrip()[:1] in "{["
    json.loads(res.stdout)
    assert "pre-json noise" in res.stderr
