"""Object storage: S3 when AWS_* are set, else local DATA_DIR/objects/."""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def data_dir() -> Path:
    raw = (os.environ.get("DATA_DIR") or "").strip()
    root = Path(raw) if raw else Path("runs") / "ui"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _s3_config() -> dict[str, str] | None:
    bucket = (os.environ.get("AWS_S3_BUCKET_NAME") or "").strip()
    key_id = (os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    if not (bucket and key_id and secret):
        return None
    # Prefer AWS_ENDPOINT_URL_S3 (boto3 convention); accept AWS_S3_ENDPOINT_URL too.
    endpoint = (
        os.environ.get("AWS_ENDPOINT_URL_S3")
        or os.environ.get("AWS_S3_ENDPOINT_URL")
        or ""
    ).strip()
    return {
        "bucket": bucket,
        "access_key_id": key_id,
        "secret_access_key": secret,
        "endpoint_url": endpoint,
        "region": (os.environ.get("AWS_DEFAULT_REGION") or "us-east-1").strip()
        or "us-east-1",
    }


class ObjectStore:
    def put_file(
        self, local_path: str | Path, key: str, content_type: str | None = None
    ) -> str:
        raise NotImplementedError

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> str:
        raise NotImplementedError

    def get_file(self, key: str, dest: str | Path) -> Path:
        raise NotImplementedError

    def open_stream(self, key: str) -> BinaryIO:
        raise NotImplementedError

    def read_range(self, key: str, start: int, end: int) -> bytes:
        """Inclusive byte range [start, end], HTTP Range semantics."""
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def head(self, key: str) -> dict[str, Any]:
        """Return ``{"size": int, "etag": str}`` for an object."""
        raise NotImplementedError

    def list_keys(self, prefix: str = "") -> list[str]:
        """List object keys under prefix (inclusive)."""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def public_or_presigned_url(self, key: str, expires: int = 3600) -> str:
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (data_dir() / "objects")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Prevent path traversal
        cleaned = key.lstrip("/").replace("..", "_")
        return self.root / cleaned

    def put_file(
        self, local_path: str | Path, key: str, content_type: str | None = None
    ) -> str:
        import shutil

        src = Path(local_path)
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
        log.debug("local put_file key=%s bytes=%s", key, dest.stat().st_size)
        return key

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> str:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        log.debug("local put_bytes key=%s bytes=%s", key, len(data))
        return key

    def get_file(self, key: str, dest: str | Path) -> Path:
        import shutil

        src = self._path(key)
        if not src.is_file():
            raise FileNotFoundError(f"object not found: {key}")
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fsrc, open(out, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
        return out

    def open_stream(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"object not found: {key}")
        return open(path, "rb")

    def read_range(self, key: str, start: int, end: int) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"object not found: {key}")
        with open(path, "rb") as f:
            f.seek(max(0, int(start)))
            length = max(0, int(end) - int(start) + 1)
            return f.read(length)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def head(self, key: str) -> dict[str, Any]:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"object not found: {key}")
        st = path.stat()
        etag = f"local-{st.st_mtime_ns}-{st.st_size}"
        return {"size": int(st.st_size), "etag": etag}

    def list_keys(self, prefix: str = "") -> list[str]:
        cleaned = (prefix or "").lstrip("/").replace("..", "_")
        keys: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if cleaned and not rel.startswith(cleaned):
                continue
            keys.append(rel)
        return sorted(keys)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def public_or_presigned_url(self, key: str, expires: int = 3600) -> str:
        # Served by Next.js /api/media/[...key] with Range support.
        from urllib.parse import quote

        return "/api/media/" + quote(key.lstrip("/"), safe="/")


def s3_addressing_style(endpoint_url: str | None = None) -> str:
    """Railway buckets use virtual-host; MinIO/local usually need path style."""
    explicit = (os.environ.get("AWS_S3_ADDRESSING_STYLE") or "").strip().lower()
    if explicit in {"virtual", "path"}:
        return explicit
    ep = (
        endpoint_url
        or os.environ.get("AWS_ENDPOINT_URL_S3")
        or os.environ.get("AWS_S3_ENDPOINT_URL")
        or ""
    ).lower()
    if any(tok in ep for tok in ("localhost", "127.0.0.1", "0.0.0.0", "minio", ":9000")):
        return "path"
    return "virtual"


class S3ObjectStore(ObjectStore):
    def __init__(self, cfg: dict[str, str]) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = cfg["bucket"]
        self.region = cfg["region"]
        endpoint = cfg.get("endpoint_url") or None
        style = s3_addressing_style(endpoint)
        client_kwargs: dict = {
            "service_name": "s3",
            "aws_access_key_id": cfg["access_key_id"],
            "aws_secret_access_key": cfg["secret_access_key"],
            "region_name": self.region,
            "config": Config(s3={"addressing_style": style}),
        }
        if endpoint:
            client_kwargs["endpoint_url"] = endpoint
        self.client = boto3.client(**client_kwargs)
        self.endpoint_url = endpoint
        self.addressing_style = style
        log.info(
            "S3 object store bucket=%s region=%s endpoint=%s style=%s",
            self.bucket,
            self.region,
            "set" if endpoint else "default",
            style,
        )

    def put_file(
        self, local_path: str | Path, key: str, content_type: str | None = None
    ) -> str:
        path = Path(local_path)
        extra: dict = {}
        ct = content_type or mimetypes.guess_type(str(path))[0]
        if ct:
            extra["ContentType"] = ct
        if extra:
            self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra)
        else:
            self.client.upload_file(str(path), self.bucket, key)
        log.debug("s3 put_file key=%s", key)
        return key

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> str:
        extra: dict = {"Body": data, "Bucket": self.bucket, "Key": key}
        if content_type:
            extra["ContentType"] = content_type
        self.client.put_object(**extra)
        log.debug("s3 put_bytes key=%s bytes=%s", key, len(data))
        return key

    def get_file(self, key: str, dest: str | Path) -> Path:
        out = Path(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(out))
        return out

    def open_stream(self, key: str) -> BinaryIO:
        resp = self.client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"]

    def read_range(self, key: str, start: int, end: int) -> bytes:
        rng = f"bytes={int(start)}-{int(end)}"
        resp = self.client.get_object(Bucket=self.bucket, Key=key, Range=rng)
        body = resp["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        try:
            self.head(key)
            return True
        except FileNotFoundError:
            return False

    def head(self, key: str) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        try:
            resp = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(f"object not found: {key}") from exc
            raise
        etag = str(resp.get("ETag") or "").strip().strip('"')
        size = int(resp.get("ContentLength") or 0)
        if not etag:
            etag = f"s3-{size}"
        return {"size": size, "etag": etag}

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix or ""):
            for obj in page.get("Contents") or []:
                key = obj.get("Key")
                if key:
                    keys.append(str(key))
        return keys

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def public_or_presigned_url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(expires),
        )


_store: ObjectStore | None = None


def get_object_store(*, refresh: bool = False) -> ObjectStore:
    global _store
    if _store is not None and not refresh:
        return _store
    cfg = _s3_config()
    if cfg:
        _store = S3ObjectStore(cfg)
    else:
        _store = LocalObjectStore()
    return _store


def reset_object_store() -> None:
    """Test helper: drop cached store so env changes take effect."""
    global _store
    _store = None
