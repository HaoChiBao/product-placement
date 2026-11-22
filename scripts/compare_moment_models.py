"""Compare OpenRouter models on the moment finder (`suggest_with_jev`).

Runs `suggest_with_jev` once per (clip, model), unchanged except for MOMENT_MODEL,
and writes everything under runs/model-tests/:

  <clip>/<model>/moments.json, contact_sheet.jpg, frames/, log.txt
  summary.json, summary.md, index.html   (side-by-side contact sheets)

Usage (OPENROUTER_API_KEY from the environment or the repo .env):
  python scripts/compare_moment_models.py                      # all clips in clips/, default models
  python scripts/compare_moment_models.py --clips clips/a.mp4 --models google/gemini-3.8-flash
"""

from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from product_placement.jev_moments import suggest_with_jev  # noqa: E402

DEFAULT_MODELS = [
    "google/gemini-3.8-flash",  # finder default (MOMENT_MODEL)
    "typesafe/jev-router",
    "openai/gpt-6.1-terra",
    "anthropic/claude-sonnet-5.5",
    "anthropic/claude-haiku-5.5",
    "qwen/qwen3.8-flash",
]
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def load_dotenv(path: Path) -> None:
    """Fill os.environ from KEY=VALUE lines without overriding what's already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if key.strip() and value and key.strip() not in os.environ:
            os.environ[key.strip()] = value


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


class Usage:
    """Sums OpenRouter token usage from every response on a client."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt = 0
        self.completion = 0
        self.http_errors = 0

    def hook(self, response: httpx.Response) -> None:
        response.read()
        self.calls += 1
        if response.status_code >= 400:
            self.http_errors += 1
            return
        try:
            usage = response.json().get("usage") or {}
        except Exception:
            return
        self.prompt += int(usage.get("prompt_tokens") or 0)
        self.completion += int(usage.get("completion_tokens") or 0)


def run_one(clip: Path, model: str, out_dir: Path, n: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MOMENT_MODEL"] = model
    usage = Usage()
    log = io.StringIO()
    t0 = time.perf_counter()
    result: dict = {"clip": clip.name, "model": model, "out_dir": str(out_dir.relative_to(REPO))}
    with httpx.Client(timeout=60, event_hooks={"response": [usage.hook]}) as client:
        try:
            with contextlib.redirect_stderr(log):
                moments = suggest_with_jev(clip, n=n, out_dir=out_dir, client=client)
            result["status"] = "ok"
            result["moments"] = [m.to_dict() for m in moments]
        except Exception as exc:  # keep going across models
            result["status"] = "error"
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["moments"] = []
    result["seconds"] = round(time.perf_counter() - t0, 1)
    text = log.getvalue()
    result["failed_calls"] = len(re.findall(r"attempt \d failed", text))
    result["skipped_calls"] = len(re.findall(r" skipped$", text, re.MULTILINE))
    result["api_calls"] = usage.calls
    result["http_errors"] = usage.http_errors
    result["prompt_tokens"] = usage.prompt
    result["completion_tokens"] = usage.completion
    (out_dir / "log.txt").write_text(text, encoding="utf-8")
    return result


def write_reports(results: list[dict], root: Path) -> None:
    (root / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    lines = [
        "| Clip | Model | Status | Moments | Time (s) | API calls | Failed calls | Tokens in/out |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        status = r["status"] if r["status"] == "ok" else f"error: {r.get('error', '')[:60]}"
        lines.append(
            f"| {r['clip']} | `{r['model']}` | {status} | {len(r['moments'])} | {r['seconds']} | "
            f"{r['api_calls']} | {r['failed_calls']} | {r['prompt_tokens']}/{r['completion_tokens']} |"
        )
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    clips = sorted({r["clip"] for r in results})
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Moment model comparison</title>",
        "<style>body{font:14px system-ui;background:#141414;color:#eee;margin:24px}"
        "h2{margin-top:40px}.row{display:flex;gap:16px;overflow-x:auto;padding-bottom:8px}"
        ".card{flex:0 0 420px;background:#1f1f1f;border-radius:8px;padding:12px}"
        ".card img{width:100%;border-radius:4px}.err{color:#ff6b72}.m{color:#aaa;font-size:12px;margin:4px 0}"
        "code{color:#ffb4b8}</style>",
        "<h1>Moment model comparison</h1>",
    ]
    for clip in clips:
        parts.append(f"<h2>{html.escape(clip)}</h2><div class='row'>")
        for r in [x for x in results if x["clip"] == clip]:
            parts.append("<div class='card'>")
            parts.append(f"<b><code>{html.escape(r['model'])}</code></b>")
            parts.append(
                f"<div class='m'>{len(r['moments'])} moments · {r['seconds']}s · {r['api_calls']} calls · "
                f"{r['failed_calls']} failed · {r['prompt_tokens']}/{r['completion_tokens']} tokens</div>"
            )
            sheet = Path(r["out_dir"]) / "contact_sheet.jpg"
            if r["status"] == "ok" and (REPO / sheet).is_file():
                rel = os.path.relpath(REPO / sheet, root).replace(os.sep, "/")
                parts.append(f"<img src='{html.escape(rel)}' alt=''>")
            else:
                parts.append(f"<p class='err'>{html.escape(r.get('error', 'no contact sheet'))}</p>")
            for m in r["moments"]:
                parts.append(
                    f"<div class='m'>{m['start_s']:.1f}–{m['end_s']:.1f}s · <b>{html.escape(m['whats_there'])}</b> "
                    f"· {m['score']:.2f} · {html.escape(m['reason'])}</div>"
                )
            parts.append("</div>")
        parts.append("</div>")
    (root / "index.html").write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", nargs="*", help="video files (default: everything in clips/)")
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("-n", type=int, default=5, help="moments to keep per run (3-5)")
    ap.add_argument("--out", default="runs/model-tests")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252

    load_dotenv(REPO / ".env")
    if not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        print("OPENROUTER_API_KEY is not set (env or .env)", file=sys.stderr)
        return 2

    clips = [Path(c) for c in args.clips] if args.clips else sorted(
        p for p in (REPO / "clips").glob("*") if p.suffix.lower() in VIDEO_EXT
    )
    if not clips:
        print("no clips found; put videos in clips/ or pass --clips", file=sys.stderr)
        return 2

    root = REPO / args.out
    results: list[dict] = []
    for clip in clips:
        for model in args.models:
            out_dir = root / slug(clip.stem) / slug(model)
            print(f"→ {clip.name} · {model}", flush=True)
            r = run_one(clip.resolve(), model, out_dir, args.n)
            print(
                f"  {r['status']} · {len(r['moments'])} moments · {r['seconds']}s · "
                f"{r['api_calls']} calls · {r['failed_calls']} failed"
                + (f" · {r['error']}" if r["status"] != "ok" else ""),
                flush=True,
            )
            results.append(r)
            write_reports(results, root)  # keep partial results if interrupted
    print(f"\nwrote {root.relative_to(REPO)}/summary.md and index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
