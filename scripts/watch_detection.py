"""Watch the Jev moment finder work on one clip, live in the browser.

Runs `suggest_with_jev` unchanged and records every model call: each contact sheet
it screens (with the tiles the model flags) and each candidate frame it verifies
(with the box it draws). A small local page polls those events and draws them.

  python scripts/watch_detection.py --clip "clips/The Office - Fire Drill.mp4"
  python scripts/watch_detection.py --clip clips/x.mp4 --model google/gemini-3.8-flash

Output goes to runs/live/<clip>/<model>/; the page stays up until Ctrl+C.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from product_placement import jev_moments  # noqa: E402
from product_placement.brand import Brand, parse_brief  # noqa: E402
from product_placement.crop_stitch import probe_video  # noqa: E402

VIEWER = Path(__file__).resolve().parent / "live_viewer.html"


def load_dotenv(path: Path) -> None:
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


class Recorder:
    """Thread-safe event log written to events.json after every change."""

    def __init__(self, out: Path, meta: dict):
        self.out = out
        self.lock = threading.Lock()
        self.state = {**meta, "phase": "Starting", "started": time.time(), "batches": [], "moments": None}
        self.count = 0
        self.flush()

    def flush(self) -> None:
        tmp = self.out / "events.json.tmp"
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        os.replace(tmp, self.out / "events.json")

    def update(self, **fields) -> None:
        with self.lock:
            self.state.update(fields)
            self.flush()

    def start_batch(self, images: list[bytes], times: list[float]) -> dict:
        with self.lock:
            self.count += 1
            frames = []
            for i, image in enumerate(images):
                name = f"batch{self.count:02d}_f{i}.jpg"
                (self.out / name).write_bytes(image)
                frames.append({"image": name, "time": times[i] if i < len(times) else None})
            batch = {"index": self.count, "frames": frames, "status": "pending", "t0": time.time()}
            self.state["batches"].append(batch)
            self.state["phase"] = "Asking the model about keyframes"
            self.flush()
            return batch

    def finish(self, batch: dict, **fields) -> None:
        with self.lock:
            batch.update(fields, seconds=round(time.time() - batch.pop("t0", time.time()), 1))
            self.flush()


_FRAME_TIME = re.compile(r"Frame (\d+) at ([\d.]+)s")


def _image_size(image: bytes) -> tuple[int, int]:
    import cv2
    import numpy as np

    decoded = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_8)
    if decoded is None:
        return 1, 1
    h, w = decoded.shape[:2]
    return w * 8, h * 8  # reduced by 8 on decode; only the ratio matters for boxes


def instrument(rec: Recorder) -> None:
    """Wrap the finder's model calls so each batch of keyframes and its answer lands in the event log."""
    original_call = jev_moments.call_validated
    original_shots = jev_moments.detect_shots
    original_collect = jev_moments._collect_keyframes

    def detect_shots(cap):
        rec.update(phase="Finding shot cuts (local, can take a minute)")
        shots = original_shots(cap)
        rec.update(shots=len(shots))
        return shots

    def collect_keyframes(cap, shots, dest):
        rec.update(phase="Pulling keyframes from each shot")
        keyframes = original_collect(cap, shots, dest)
        rec.update(keyframes=len(keyframes))
        return keyframes

    def call_validated(client, *, images, prompt, validate, **kwargs):
        times = [float(t) for _i, t in sorted(_FRAME_TIME.findall(prompt), key=lambda m: int(m[0]))]
        batch = rec.start_batch(images, times)
        result = original_call(client, images=images, prompt=prompt, validate=validate, **kwargs)
        if result is None:
            rec.finish(batch, status="failed")
            return result
        objects = []
        sizes: dict[int, tuple[int, int]] = {}
        for obj in result:
            if obj.frame not in sizes:
                sizes[obj.frame] = _image_size(images[obj.frame])
            w, h = sizes[obj.frame]
            b = obj.box
            objects.append(
                {
                    "frame": obj.frame,
                    "label": obj.label,
                    "category": obj.category,
                    "swap_with": obj.swap_with,
                    "visibility": obj.visibility,
                    "box": {"x": b.x / w, "y": b.y / h, "w": b.w / w, "h": b.h / h},
                }
            )
        rec.finish(batch, status="done", objects=objects)
        rec.update(phase="Asking the model about keyframes")
        return result

    jev_moments.call_validated = call_validated
    jev_moments.detect_shots = detect_shots
    jev_moments._collect_keyframes = collect_keyframes


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the terminal readable
        pass


def serve(directory: Path) -> int:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", required=True)
    ap.add_argument("--model", default=None, help="OpenRouter model (default: MOMENT_MODEL from .env)")
    ap.add_argument("--brand", help="brand name, e.g. 'Acme Cola' (added to the finder prompt)")
    ap.add_argument("--brief", default="", help="product + preferences, e.g. 'Soda can. Prefer tables, counters. Avoid beds.'")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv(REPO / ".env")
    if args.model:
        os.environ["MOMENT_MODEL"] = args.model
    model = jev_moments.moment_model()
    clip = Path(args.clip).resolve()
    if not clip.is_file():
        print(f"clip not found: {clip}", file=sys.stderr)
        return 2

    brand = None
    if args.brand:
        brief = args.brief or f"{args.brand} product"
        brand = Brand(id=slug(args.brand), name=args.brand, brief=brief, rules=parse_brief(brief))

    out = REPO / "runs" / "live" / slug(clip.stem) / (slug(model) + (f"--{slug(args.brand)}" if args.brand else ""))
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.jpg"):
        old.unlink()
    (out / "index.html").write_text(VIEWER.read_text(encoding="utf-8"), encoding="utf-8")

    _w, _h, duration, _fps = probe_video(clip)
    rec = Recorder(
        out,
        {
            "clip": clip.name,
            "model": model,
            "duration": round(duration, 1),
            "brand": brand.name if brand else None,
            "rules": brand.rules.to_dict() if brand else None,
        },
    )
    instrument(rec)

    port = serve(out)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"Watching {clip.name} with {model}" + (f" for {brand.name}" if brand else ""), flush=True)
    if brand:
        print(f"  brand rules: {brand.rules.to_dict()}", flush=True)
    print(f"  {url}\n", flush=True)
    if not args.no_open:
        webbrowser.open(url)

    try:
        moments = jev_moments.suggest_with_jev(clip, n=args.n, out_dir=out, brand=brand)
        rec.update(phase="Done", moments=[m.to_dict() for m in moments], finished=time.time())
        print(f"Done: {len(moments)} moments", flush=True)
    except Exception as exc:
        rec.update(phase="Stopped", error=f"{exc}", finished=time.time())
        print(f"Stopped: {exc}", flush=True)

    print("Viewer stays up; press Ctrl+C to quit.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
