"""E2E: import stays off the web request path; /health stays fast during transcode."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from product_placement.catalog import (
    import_movies_with_lock,
    list_titles,
    prepare_browser_mp4,
)
from product_placement.db import get_connection, init_schema
from product_placement.object_store import get_object_store, reset_object_store


def _have_encoder(name: str) -> bool:
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"], text=True, stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return name in out


def _write_vp9_opus_mkv(path: Path, *, duration: float = 2.0) -> None:
    """VP9 + Opus + soft subtitles — forces a real transcode path."""
    srt = path.with_suffix(".srt")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=960x540:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=f=440:d={duration}",
        "-i",
        str(srt),
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "500k",
        "-c:a",
        "libopus",
        "-c:s",
        "srt",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map",
        "2:0",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _write_h264_aac_mp4(path: Path, *, duration: float = 1.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=green:s=320x180:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=880:d={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def import_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FFMPEG_THREADS", "2")
    monkeypatch.setenv("FFMPEG_TIMEOUT_S", "120")
    monkeypatch.setenv("IMPORT_MAX_ATTEMPTS", "3")
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


@pytest.mark.skipif(not _have_encoder("libvpx-vp9"), reason="libvpx-vp9 unavailable")
def test_prepare_browser_mp4_vp9_opus_subs(import_env, tmp_path):
    conn, data = import_env
    mkv = tmp_path / "complex.mkv"
    _write_vp9_opus_mkv(mkv, duration=2.0)
    out = tmp_path / "out.mp4"
    action = prepare_browser_mp4(mkv, out)
    assert action == "transcode"
    assert out.is_file() and out.stat().st_size > 0
    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(out),
        ],
        text=True,
    )
    data_json = json.loads(probe)
    types = {s.get("codec_type") for s in data_json.get("streams") or []}
    assert "video" in types
    codecs = {
        s.get("codec_name")
        for s in data_json.get("streams") or []
        if s.get("codec_type") == "video"
    }
    assert "h264" in codecs
    # Subtitles must not be copied into the browser mp4.
    assert "subtitle" not in types


def test_prepare_browser_mp4_h264_aac_copy_or_remux(import_env, tmp_path):
    mp4 = tmp_path / "safe.mp4"
    _write_h264_aac_mp4(mp4, duration=1.0)
    out = tmp_path / "out.mp4"
    action = prepare_browser_mp4(mp4, out)
    assert action in {"copy", "remux"}
    assert out.is_file()


def test_import_ready_only_after_success(import_env, tmp_path):
    conn, data = import_env
    store = get_object_store()
    mp4 = tmp_path / "ready_case.mp4"
    _write_h264_aac_mp4(mp4, duration=1.0)
    store.put_file(mp4, "movies/ready_case.mp4", content_type="video/mp4")

    assert list_titles(conn, watchable_only=True) == []
    result = import_movies_with_lock(blocking=True, conn=conn)
    assert result["imported"] == 1
    titles = list_titles(conn, watchable_only=True)
    assert any(t["slug"] == "ready-case" for t in titles)


def test_health_stays_fast_while_import_runs(import_env, tmp_path):
    """Simulate the Next route: enqueue import without blocking the HTTP thread."""
    conn, data = import_env
    store = get_object_store()

    # Longer clip so prepare_browser_mp4 takes measurable wall time.
    mkv = tmp_path / "slow_import.mkv"
    if _have_encoder("libvpx-vp9"):
        _write_vp9_opus_mkv(mkv, duration=6.0)
        store.put_file(mkv, "movies/slow_import.mkv", content_type="video/x-matroska")
    else:
        mp4 = tmp_path / "slow_import.mp4"
        _write_h264_aac_mp4(mp4, duration=1.0)
        # Force transcode path via mpeg4 avi.
        avi = tmp_path / "slow_import.avi"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=orange:s=1280x720:d=8",
                "-c:v",
                "mpeg4",
                str(avi),
            ],
            check=True,
            capture_output=True,
        )
        store.put_file(avi, "movies/slow_import.avi", content_type="video/x-msvideo")

    state = {"import_done": False, "import_error": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            if self.path.startswith("/api/health"):
                body = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/api/catalog/titles"):
                c = get_connection()
                try:
                    init_schema(c)
                    titles = list_titles(c, watchable_only=True)
                finally:
                    c.close()
                body = json.dumps({"titles": titles}, default=str).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path.startswith("/api/catalog/import-movies"):
                # Non-blocking: same contract as the Next 202 route.
                def _run():
                    try:
                        env = os.environ.copy()
                        env["DATA_DIR"] = str(data)
                        env["PYTHONPATH"] = str(
                            Path(__file__).resolve().parents[1] / "src"
                        )
                        subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "product_placement.catalog",
                                "import-movies",
                                "--non-blocking",
                            ],
                            check=True,
                            env=env,
                            capture_output=True,
                        )
                        state["import_done"] = True
                    except Exception as exc:  # noqa: BLE001
                        state["import_error"] = str(exc)
                        state["import_done"] = True

                threading.Thread(target=_run, daemon=True).start()
                body = json.dumps({"accepted": True, "status": "queued"}).encode()
                self.send_response(202)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    try:
        # Kick import (must return immediately).
        t0 = time.monotonic()
        req = urllib.request.Request(
            base + "/api/catalog/import-movies", method="POST", data=b""
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 202
        assert time.monotonic() - t0 < 1.5

        # While import/transcode runs, health must stay snappy.
        saw_slow = False
        ready = False
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            h0 = time.monotonic()
            with urllib.request.urlopen(base + "/api/health", timeout=2) as resp:
                assert resp.status == 200
                payload = json.loads(resp.read().decode())
                assert payload.get("ok") is True
            dt = time.monotonic() - h0
            if dt > 1.0:
                saw_slow = True
                break
            with urllib.request.urlopen(
                base + "/api/catalog/titles?watchable=1", timeout=5
            ) as resp:
                titles = json.loads(resp.read().decode()).get("titles") or []
            if any(
                (t.get("slug") or "").startswith("slow-import") for t in titles
            ):
                ready = True
                break
            if state["import_done"] and state["import_error"]:
                pytest.fail(state["import_error"])
            if state["import_done"] and not titles:
                # Import finished but title missing — fail clearly.
                break
            time.sleep(0.25)

        assert not saw_slow, "health endpoint blocked during import"
        assert ready, "title should appear on /api/catalog/titles only once ready"
        # Wait briefly for the worker thread to mark completion.
        for _ in range(50):
            if state["import_done"]:
                break
            time.sleep(0.1)
        if state["import_error"]:
            pytest.fail(state["import_error"])
    finally:
        server.shutdown()
