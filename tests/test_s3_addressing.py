"""S3 addressing-style helpers (always run; no Postgres/MinIO required)."""

from __future__ import annotations

from product_placement.object_store import s3_addressing_style


def test_addressing_style_defaults(monkeypatch):
    monkeypatch.delenv("AWS_S3_ADDRESSING_STYLE", raising=False)
    assert s3_addressing_style("http://127.0.0.1:9000") == "path"
    assert s3_addressing_style("https://minio.example:9000") == "path"
    assert s3_addressing_style("https://s3.us-west-1.amazonaws.com") == "virtual"
    assert s3_addressing_style("https://bucket.railway.internal") == "virtual"
    monkeypatch.setenv("AWS_S3_ADDRESSING_STYLE", "path")
    assert s3_addressing_style("https://s3.us-west-1.amazonaws.com") == "path"
