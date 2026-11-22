"""Async job runner for suggest + crop-stitch Seedance swap (disk status JSON)."""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_root() -> Path:
    raw = (os.environ.get("DATA_DIR") or "").strip()
    if raw:
        root = Path(raw)
    else:
        root = Path("runs") / "ui"
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str) -> Path:
    path = data_root() / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def status_path(job_id: str) -> Path:
    return job_dir(job_id) / "status.json"


def write_status(job_id: str, **fields: Any) -> dict[str, Any]:
    path = status_path(job_id)
    current: dict[str, Any] = {}
    if path.is_file():
        current = json.loads(path.read_text())
    current.update(fields)
    current["id"] = job_id
    current["updated_at"] = utc_now()
    path.write_text(json.dumps(current, indent=2) + "\n")
    return current


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def mock_higgsfield_enabled() -> bool:
    return (os.environ.get("MOCK_HIGGSFIELD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def run_suggest_job(
    job_id: str,
    *,
    video_path: str | Path,
    n: int = 5,
    brand_id: str | None = None,
) -> dict[str, Any]:
    from product_placement.crop_stitch import probe_video
    from product_placement.suggest import clamp_n, suggest

    brand = None
    if brand_id:
        from product_placement.brand import load_brand

        brand = load_brand(brand_id)

    out = job_dir(job_id)
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    write_status(
        job_id,
        type="suggest",
        status="running",
        progress="Scanning shots · faces · props"
        + (f" · brand {brand.name}" if brand else ""),
        created_at=utc_now(),
        brand_id=brand_id,
    )
    t0 = time.perf_counter()
    try:
        moments = suggest(
            video_path=video_path,
            n=clamp_n(n),
            out_dir=out,
            brand=brand,
            use_clip=True,
        )
        elapsed = time.perf_counter() - t0
        # Persist a lightweight index the web layer can serve.
        moment_dicts = []
        for i, m in enumerate(moments):
            name = f"moment_{i + 1:02d}.jpg"
            local = frames / name
            # suggest already wrote frames; ensure path is under job dir.
            if m.frame_path.is_file() and m.frame_path.resolve() != local.resolve():
                local.write_bytes(m.frame_path.read_bytes())
            d = m.to_dict()
            d["frame"] = str(local)
            moment_dicts.append(d)

        moments_path = out / "moments.json"
        prior = {}
        if moments_path.is_file():
            prior = json.loads(moments_path.read_text())
        result = {
            "runId": job_id,
            "video": str(video_path),
            "n": len(moment_dicts),
            "window_s": 5.0,
            "duration_s": probe_video(video_path)[2],
            "elapsed_s": round(elapsed, 3),
            "moments": moment_dicts,
            "contact_sheet": str(out / "contact_sheet.jpg"),
            "brand_id": brand_id,
            "vetoed": prior.get("vetoed") or [],
        }
        (out / "moments.json").write_text(json.dumps(result, indent=2) + "\n")
        (out / "source.txt").write_text(str(video_path) + "\n")
        return write_status(
            job_id,
            status="completed",
            progress="Done",
            elapsed_s=round(elapsed, 3),
            result=result,
        )
    except Exception as exc:
        write_status(
            job_id,
            status="failed",
            progress="Failed",
            error=str(exc),
            traceback=traceback.format_exc()[-4000:],
        )
        raise


def run_swap_job(
    job_id: str,
    *,
    video_path: str | Path,
    product_path: str | Path,
    start_s: float,
    end_s: float,
    region: dict,
    target: str,
    confirm: bool,
    resolution: str = "720p",
    brand_id: str | None = None,
) -> dict[str, Any]:
    from product_placement.crop_stitch import (
        MAX_INPUT_S,
        Region,
        TARGET_HOLD_S,
        build_crop_hold,
        estimate_seedance_cost_usd,
        extract_moment_clip,
        mock_edit_crop,
        probe_video,
        stitch_edit_into_moment,
    )
    from product_placement.higgsfield import default_swap_prompt

    brand = None
    prompt = None
    if brand_id:
        from product_placement.brand import brand_swap_prompt, load_brand, primary_asset_path

        brand = load_brand(brand_id)
        asset = primary_asset_path(brand)
        if asset and asset.is_file():
            product_path = asset
        prompt = brand_swap_prompt(brand, target)

    if not confirm:
        raise ValueError("confirm=true is required before a paid (or mock) swap")
    if resolution != "720p":
        raise ValueError("hosted swap is capped at 720p")

    duration = float(end_s) - float(start_s)
    if duration <= 0:
        raise ValueError("end_s must be greater than start_s")
    if duration > MAX_INPUT_S + 1e-3:
        raise ValueError(f"input window {duration:.3f}s exceeds max {MAX_INPUT_S}s")

    hold_s = min(TARGET_HOLD_S, max(duration, 2.0))
    estimated = estimate_seedance_cost_usd(hold_s)
    out = job_dir(job_id)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    write_status(
        job_id,
        type="swap",
        status="running",
        progress="Extracting moment + building crop",
        created_at=utc_now(),
        estimated_cost_usd=estimated,
        mock=mock_higgsfield_enabled(),
        target=target,
        resolution=resolution,
    )

    t0 = time.perf_counter()
    try:
        reg = Region.from_dict(region)
        moment_clip = extract_moment_clip(
            video_path, start_s, end_s, work / "moment.mp4"
        )
        crop_hold = build_crop_hold(
            moment_clip, reg, work / "crop_hold.mp4", hold_s=hold_s
        )
        _, _, crop_dur, _ = probe_video(crop_hold)
        if crop_dur > MAX_INPUT_S + 1e-3:
            raise ValueError(f"crop hold {crop_dur:.3f}s exceeds max {MAX_INPUT_S}s")

        edited = work / "seedance_edit.mp4"
        backend = "mock" if mock_higgsfield_enabled() else "seedance"
        if mock_higgsfield_enabled():
            write_status(job_id, progress="Mock Seedance edit (MOCK_HIGGSFIELD=1)")
            mock_edit_crop(crop_hold, product_path, edited)
            request_id = "mock"
            video_url = ""
        else:
            write_status(job_id, progress="Uploading + running Seedance 2.5")
            from product_placement.backends import seedance_edit

            result = seedance_edit(
                crop_hold,
                Path(product_path),
                prompt or default_swap_prompt(target),
                out_path=edited,
                work_dir=work / "hf",
                resolution="720p",
                backend="seedance",
            )
            request_id = result.request_id
            video_url = result.video_url
            backend = result.backend or "seedance"

        write_status(job_id, progress="Feather-stitching edit into moment")
        stitched = stitch_edit_into_moment(
            moment_clip, edited, reg, out / "result.mp4"
        )
        # Also keep original moment for side-by-side UI.
        original = out / "original.mp4"
        if not original.exists():
            original.write_bytes(moment_clip.read_bytes())

        elapsed = time.perf_counter() - t0
        payload = {
            "original_url": f"/api/jobs/{job_id}/file/original.mp4",
            "result_url": f"/api/jobs/{job_id}/file/result.mp4",
            "download_url": f"/api/jobs/{job_id}/file/result.mp4?download=1",
            "original_path": str(original),
            "result_path": str(stitched),
            "backend": backend,
            "request_id": request_id,
            "video_url": video_url,
            "estimated_cost_usd": estimated,
            "billed_duration_s": round(crop_dur, 3),
            "elapsed_s": round(elapsed, 3),
            "mock": mock_higgsfield_enabled(),
            "target": target,
            "start_s": start_s,
            "end_s": end_s,
            "region": region,
        }
        (out / "swap.json").write_text(json.dumps(payload, indent=2) + "\n")
        return write_status(
            job_id,
            status="completed",
            progress="Done",
            elapsed_s=round(elapsed, 3),
            result=payload,
        )
    except Exception as exc:
        write_status(
            job_id,
            status="failed",
            progress="Failed",
            error=str(exc),
            traceback=traceback.format_exc()[-4000:],
        )
        raise


def run_episode_suggest_job(episode_id: str, *, n: int = 5) -> dict[str, Any]:
    """Download episode video from object store, run suggest(), persist moments + frames."""
    import tempfile

    from product_placement.catalog import get_episode, insert_moment
    from product_placement.db import get_connection, init_schema, utc_now
    from product_placement.object_store import get_object_store
    from product_placement.suggest import clamp_n, suggest

    conn = get_connection()
    init_schema(conn)
    try:
        ep = get_episode(conn, id=episode_id)
        if not ep:
            raise ValueError(f"episode not found: {episode_id}")
        video_key = ep.get("video_key")
        if not video_key:
            raise ValueError(f"episode {episode_id} has no video_key")

        conn.execute(
            "UPDATE episodes SET suggest_status = ? WHERE id = ?",
            ("running", episode_id),
        )
        conn.commit()

        store = get_object_store()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video_path = tmp_path / "episode.mp4"
            store.get_file(video_key, video_path)
            out_dir = tmp_path / "suggest"
            out_dir.mkdir(parents=True, exist_ok=True)

            moments = suggest(
                video_path=video_path,
                n=clamp_n(n),
                out_dir=out_dir,
                brand=None,
                use_clip=True,
            )

            # Replace prior moments for this episode.
            conn.execute("DELETE FROM moments WHERE episode_id = ?", (episode_id,))
            conn.commit()

            stored: list[dict[str, Any]] = []
            for i, m in enumerate(moments):
                frame_key = f"moments/{episode_id}/moment_{i + 1:02d}.jpg"
                if m.frame_path.is_file():
                    store.put_file(m.frame_path, frame_key, content_type="image/jpeg")
                row = insert_moment(
                    conn,
                    episode_id=episode_id,
                    start_s=m.start_s,
                    end_s=m.end_s,
                    region=m.region.as_dict(),
                    whats_there=m.whats_there,
                    score=m.score,
                    reason=m.reason,
                    frame_key=frame_key if m.frame_path.is_file() else None,
                    frame_w=m.frame_w,
                    frame_h=m.frame_h,
                )
                stored.append(row)

        conn.execute(
            "UPDATE episodes SET suggest_status = ? WHERE id = ?",
            ("ready", episode_id),
        )
        conn.commit()
        return {
            "episode_id": episode_id,
            "suggest_status": "ready",
            "moments": stored,
            "updated_at": utc_now(),
        }
    except Exception as exc:
        try:
            conn.execute(
                "UPDATE episodes SET suggest_status = ? WHERE id = ?",
                ("failed", episode_id),
            )
            conn.commit()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def run_placement_swap_job(
    placement_id: str,
    *,
    confirm: bool,
    test_mode: bool = True,
) -> dict[str, Any]:
    """Render an approved placement: queued → rendering → ready/failed; store clip."""
    import os
    import tempfile

    from product_placement.catalog import get_brand, get_episode, get_placement, update_placement
    from product_placement.db import fetchone_dict, get_connection, init_schema
    from product_placement.object_store import get_object_store

    # Paid path requires explicit confirm; test_mode defaults True (mock).
    if not test_mode and not confirm:
        raise ValueError("paid placement swap requires confirm=True (and test_mode=False)")
    if not confirm:
        raise ValueError("confirm=true is required before a placement swap")

    conn = get_connection()
    init_schema(conn)
    try:
        placement = get_placement(conn, id=placement_id)
        if not placement:
            raise ValueError(f"placement not found: {placement_id}")

        moment = fetchone_dict(
            conn, "SELECT * FROM moments WHERE id = ?", (placement["moment_id"],)
        )
        if not moment:
            raise ValueError(f"moment not found: {placement['moment_id']}")

        ep = get_episode(conn, id=placement["episode_id"] or moment["episode_id"])
        if not ep or not ep.get("video_key"):
            raise ValueError("episode video missing")

        brand_row = get_brand(conn, id=placement["brand_id"])
        if not brand_row:
            raise ValueError(f"brand not found: {placement['brand_id']}")

        update_placement(
            conn,
            placement_id,
            status="rendering",
            test_mode=1 if test_mode else 0,
            error=None,
        )

        store = get_object_store()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            video_path = tmp_path / "episode.mp4"
            store.get_file(ep["video_key"], video_path)

            # Resolve product asset: prefer DB object_key, else file brand dir.
            product_path = tmp_path / "product.png"
            assets = brand_row.get("assets") or []
            if assets and assets[0].get("object_key"):
                store.get_file(assets[0]["object_key"], product_path)
            else:
                from product_placement.brand import load_brand, primary_asset_path

                brand = load_brand(brand_row["id"])
                asset = primary_asset_path(brand)
                if not asset or not asset.is_file():
                    raise ValueError(f"brand {brand_row['id']} has no product asset")
                product_path.write_bytes(asset.read_bytes())

            region = json.loads(moment["region_json"] or "{}")
            target = moment.get("whats_there") or "object"

            # Force mock when test_mode; paid path leaves MOCK_HIGGSFIELD to env.
            prev_mock = os.environ.get("MOCK_HIGGSFIELD")
            if test_mode:
                os.environ["MOCK_HIGGSFIELD"] = "1"
            try:
                job_id = new_job_id()
                result = run_swap_job(
                    job_id,
                    video_path=video_path,
                    product_path=product_path,
                    start_s=float(moment["start_s"]),
                    end_s=float(moment["end_s"]),
                    region=region,
                    target=target,
                    confirm=True,
                    brand_id=brand_row["id"],
                )
            finally:
                if test_mode:
                    if prev_mock is None:
                        os.environ.pop("MOCK_HIGGSFIELD", None)
                    else:
                        os.environ["MOCK_HIGGSFIELD"] = prev_mock

            result_path = Path((result.get("result") or {}).get("result_path") or "")
            if not result_path.is_file():
                # Fallback: look under job dir
                result_path = job_dir(job_id) / "result.mp4"
            if not result_path.is_file():
                raise RuntimeError("swap completed but result clip missing")

            clip_key = f"placements/{placement_id}/clip.mp4"
            store.put_file(result_path, clip_key, content_type="video/mp4")
            backend = (result.get("result") or {}).get("backend") or (
                "mock" if test_mode else "seedance"
            )
            updated = update_placement(
                conn,
                placement_id,
                status="ready",
                clip_key=clip_key,
                backend=backend,
                test_mode=1 if test_mode else 0,
                error=None,
            )
            return updated
    except Exception as exc:
        try:
            update_placement(
                conn,
                placement_id,
                status="failed",
                error=str(exc)[:2000],
            )
        except Exception:
            pass
        raise
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="product_placement.jobs")
    sub = p.add_subparsers(dest="command", required=True)

    sug = sub.add_parser("suggest")
    sug.add_argument("--job-id", required=True)
    sug.add_argument("--video", required=True)
    sug.add_argument("-n", type=int, default=5)
    sug.add_argument("--brand-id", default=None)

    sw = sub.add_parser("swap")
    sw.add_argument("--job-id", required=True)
    sw.add_argument("--video", required=True)
    sw.add_argument("--product", required=True)
    sw.add_argument("--start", type=float, required=True)
    sw.add_argument("--end", type=float, required=True)
    sw.add_argument("--region-json", required=True, help='{"x":..,"y":..,"w":..,"h":..}')
    sw.add_argument("--target", required=True)
    sw.add_argument("--confirm", action="store_true")
    sw.add_argument("--resolution", default="720p")
    sw.add_argument("--brand-id", default=None)

    es = sub.add_parser("episode-suggest")
    es.add_argument("--episode-id", required=True)
    es.add_argument("-n", type=int, default=5)

    ps = sub.add_parser("placement-swap")
    ps.add_argument("--placement-id", required=True)
    ps.add_argument("--confirm", action="store_true")
    ps.add_argument(
        "--test-mode",
        dest="test_mode",
        action="store_true",
        default=True,
        help="Force mock backend (default)",
    )
    ps.add_argument(
        "--no-test-mode",
        dest="test_mode",
        action="store_false",
        help="Allow paid Seedance path (requires --confirm)",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    job_id = getattr(args, "job_id", None)
    try:
        if args.command == "suggest":
            run_suggest_job(
                args.job_id,
                video_path=args.video,
                n=args.n,
                brand_id=args.brand_id,
            )
            return
        if args.command == "swap":
            region = json.loads(args.region_json)
            run_swap_job(
                args.job_id,
                video_path=args.video,
                product_path=args.product,
                start_s=args.start,
                end_s=args.end,
                region=region,
                target=args.target,
                confirm=bool(args.confirm),
                resolution=args.resolution,
                brand_id=args.brand_id,
            )
            return
        if args.command == "episode-suggest":
            result = run_episode_suggest_job(args.episode_id, n=args.n)
            print(json.dumps(result, indent=2, default=str))
            return
        if args.command == "placement-swap":
            result = run_placement_swap_job(
                args.placement_id,
                confirm=bool(args.confirm),
                test_mode=bool(args.test_mode),
            )
            print(json.dumps(result, indent=2, default=str))
            return
        raise SystemExit(f"unknown command {args.command}")
    except Exception as exc:
        if job_id:
            try:
                write_status(
                    job_id,
                    status="failed",
                    progress="Failed",
                    error=str(exc),
                    traceback=traceback.format_exc()[-4000:],
                )
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
