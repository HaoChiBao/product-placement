"""Async job runner for suggest and placement swaps (disk status JSON)."""

from __future__ import annotations

import argparse
import json
import os
import re
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
    scanning = (
        "Finding swappable objects"
        if (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        else "Scanning shots · faces · props"
    )
    write_status(
        job_id,
        type="suggest",
        status="running",
        progress=scanning + (f" · brand {brand.name}" if brand else ""),
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
    method: str | None = None,
) -> dict[str, Any]:
    """Object swap. Default is Wan 3.0; Higgsfield frame swap is the fallback."""
    from product_placement.openrouter_wan import (
        estimate_wan_cost_usd,
        resolve_swap_method,
    )

    product_name = "product"
    if brand_id:
        # Optional file-backed override; catalog placements resolve assets via object store.
        try:
            from product_placement.brand import load_brand, primary_asset_path

            brand = load_brand(brand_id)
            if brand.name:
                product_name = brand.name
            asset = primary_asset_path(brand)
            if asset and asset.is_file():
                product_path = asset
        except Exception:
            pass

    if not confirm:
        raise ValueError("confirm=true is required before a paid (or mock) swap")
    if resolution != "720p":
        raise ValueError("hosted swap is capped at 720p")

    duration = float(end_s) - float(start_s)
    if duration <= 0:
        raise ValueError("end_s must be greater than start_s")

    chosen = resolve_swap_method(method)
    use_frame = mock_higgsfield_enabled() or chosen == "higgsfield"
    if use_frame:
        from product_placement.crop_stitch import MAX_INPUT_S, estimate_frame_swap_cost_usd
        from product_placement.frame_swap import clamp_i2v_duration

        if duration > MAX_INPUT_S + 1e-3:
            raise ValueError(f"input window {duration:.3f}s exceeds max {MAX_INPUT_S}s")
        estimated = estimate_frame_swap_cost_usd(float(clamp_i2v_duration(duration)))
        progress = "Extracting frame + editing for Seedance i2v"
    else:
        estimated = estimate_wan_cost_usd(duration)
        progress = "Wan 3.0 full-clip swap"

    out = job_dir(job_id)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    write_status(
        job_id,
        type="swap",
        status="running",
        progress=progress,
        created_at=utc_now(),
        estimated_cost_usd=estimated,
        mock=mock_higgsfield_enabled(),
        target=target,
        resolution=resolution,
        method=chosen,
    )

    t0 = time.perf_counter()
    try:
        result_path = out / "result.mp4"
        original = out / "original.mp4"
        if use_frame:
            from product_placement.crop_stitch import extract_moment_clip
            from product_placement.frame_swap import run_frame_swap

            moment_clip = extract_moment_clip(
                video_path, start_s, end_s, work / "moment.mp4"
            )
            if not original.exists():
                original.write_bytes(moment_clip.read_bytes())
            write_status(
                job_id,
                progress=(
                    "Mock frame swap (MOCK_HIGGSFIELD=1)"
                    if mock_higgsfield_enabled()
                    else "Frame edit + Seedance 2.5 image-to-video"
                ),
            )
            meta = run_frame_swap(
                video_path=video_path,
                product_path=product_path,
                start_s=start_s,
                end_s=end_s,
                region=region,
                target=target,
                out_path=result_path,
                work_dir=work / "frame",
                confirm=True,
                test_mode=mock_higgsfield_enabled(),
            )
        else:
            from product_placement.openrouter_wan import run_wan_swap

            def _on_status(status: str, _payload: dict[str, Any]) -> None:
                write_status(job_id, progress=f"Wan 3.0 {status or 'running'}")

            meta = run_wan_swap(
                video_path=video_path,
                product_path=product_path,
                start_s=start_s,
                end_s=end_s,
                target=target,
                product_name=product_name,
                out_path=result_path,
                work_dir=work / "wan",
                resolution=resolution,
                on_status=_on_status,
            )
            source_clip = work / "wan" / "source.mp4"
            if source_clip.is_file() and not original.exists():
                original.write_bytes(source_clip.read_bytes())

        elapsed = time.perf_counter() - t0
        cost = meta.get("cost_usd")
        payload = {
            "original_url": f"/api/jobs/{job_id}/file/original.mp4",
            "result_url": f"/api/jobs/{job_id}/file/result.mp4",
            "download_url": f"/api/jobs/{job_id}/file/result.mp4?download=1",
            "original_path": str(original),
            "result_path": str(result_path),
            "backend": meta.get("backend") or (
                "mock" if mock_higgsfield_enabled() else "seedance-i2v"
            ),
            "request_id": meta.get("request_id") or "",
            "edit_request_id": meta.get("edit_request_id") or "",
            "video_url": meta.get("video_url") or "",
            "estimated_cost_usd": estimated,
            "cost_usd": cost,
            "billed_duration_s": meta.get("billed_duration_s") or duration,
            "elapsed_s": round(elapsed, 3),
            "mock": mock_higgsfield_enabled(),
            "method": chosen,
            "target": target,
            "start_s": meta.get("start_s", start_s),
            "end_s": meta.get("end_s", end_s),
            "region": region,
            "frame_path": meta.get("frame_path"),
            "edited_frame_path": meta.get("edited_frame_path"),
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


def _claim_episode_suggest(conn, episode_id: str) -> bool:
    """Atomically claim an episode for suggest. Returns False if already claimed/ready."""
    result = conn.execute(
        "UPDATE episodes SET suggest_status = ? WHERE id = ? AND ("
        "suggest_status IS NULL OR suggest_status IN ('', 'queued', 'failed')"
        ")",
        ("running", episode_id),
    )
    conn.commit()
    rowcount = getattr(result, "rowcount", None)
    if rowcount is None:
        # Some drivers need a cursor; fall back to re-read.
        from product_placement.catalog import get_episode

        ep = get_episode(conn, id=episode_id)
        return bool(ep and ep.get("suggest_status") == "running")
    return int(rowcount) > 0


def reset_stale_suggest_running(conn) -> int:
    """Crash recovery: flip abandoned 'running' rows back to queued (boot/seed)."""
    result = conn.execute(
        "UPDATE episodes SET suggest_status = ? WHERE suggest_status = ?",
        ("queued", "running"),
    )
    conn.commit()
    return int(getattr(result, "rowcount", 0) or 0)


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

        if not _claim_episode_suggest(conn, episode_id):
            # Another worker owns it, or it's already ready.
            fresh = get_episode(conn, id=episode_id) or ep
            return {
                "episode_id": episode_id,
                "suggest_status": fresh.get("suggest_status"),
                "skipped": True,
                "reason": "already claimed or ready",
                "updated_at": utc_now(),
            }

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

            # Replace prior moments for this episode (only after claim).
            conn.execute("DELETE FROM moments WHERE episode_id = ?", (episode_id,))
            conn.commit()

            from product_placement.catalog import get_title
            from product_placement.catalog_keys import moment_frame_key

            title = get_title(conn, id=str(ep["title_id"]))
            slug = str((title or {}).get("slug") or "title")
            season = int(ep.get("season") or 1)
            episode_n = int(ep.get("episode_number") or 1)

            stored: list[dict[str, Any]] = []
            for i, m in enumerate(moments):
                frame_key = moment_frame_key(slug, season, episode_n, i + 1)
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
    except Exception:
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
    method: str | None = None,
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

        # Idempotent: a duplicate spawn must not re-bill / overwrite a ready clip.
        cur_status = str(placement.get("status") or "")
        if cur_status == "ready" and placement.get("clip_key"):
            return placement
        if cur_status == "rendering":
            return placement

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
            product_name = str(brand_row.get("name") or "product")

            from product_placement.openrouter_wan import resolve_swap_method

            start_s = float(moment["start_s"])
            end_s = float(moment["end_s"])
            duration = end_s - start_s
            if duration <= 0:
                raise ValueError("moment end_s must be greater than start_s")

            chosen = resolve_swap_method(method)
            use_frame = test_mode or mock_higgsfield_enabled() or chosen == "higgsfield"
            if use_frame:
                from product_placement.crop_stitch import MAX_INPUT_S
                from product_placement.frame_swap import run_frame_swap

                if duration > MAX_INPUT_S + 1e-3:
                    raise ValueError(
                        f"input window {duration:.3f}s exceeds max {MAX_INPUT_S}s"
                    )

                work = tmp_path / "frame_swap"
                result_path = work / "clip.mp4"
                # Force mock when test_mode; paid path leaves MOCK_HIGGSFIELD to env.
                prev_mock = os.environ.get("MOCK_HIGGSFIELD")
                if test_mode:
                    os.environ["MOCK_HIGGSFIELD"] = "1"
                try:
                    meta = run_frame_swap(
                        video_path=video_path,
                        product_path=product_path,
                        start_s=start_s,
                        end_s=end_s,
                        region=region,
                        target=target,
                        out_path=result_path,
                        work_dir=work / "work",
                        confirm=True,
                        test_mode=test_mode,
                    )
                finally:
                    if test_mode:
                        if prev_mock is None:
                            os.environ.pop("MOCK_HIGGSFIELD", None)
                        else:
                            os.environ["MOCK_HIGGSFIELD"] = prev_mock
            else:
                from product_placement.openrouter_wan import run_wan_swap

                work = tmp_path / "wan"
                result_path = work / "clip.mp4"
                meta = run_wan_swap(
                    video_path=video_path,
                    product_path=product_path,
                    start_s=start_s,
                    end_s=end_s,
                    target=target,
                    product_name=product_name,
                    out_path=result_path,
                    work_dir=work / "work",
                    store=store,
                )

            if not result_path.is_file():
                raise RuntimeError("swap completed but result clip missing")

            from product_placement.catalog import get_title
            from product_placement.catalog_keys import (
                brand_slug,
                placement_clip_key,
                placement_edited_key,
                placement_frame_key,
            )

            title = get_title(conn, id=str(ep["title_id"]))
            slug = str((title or {}).get("slug") or "title")
            season = int(ep.get("season") or 1)
            episode_n = int(ep.get("episode_number") or 1)
            bslug = brand_slug(
                str(brand_row.get("name") or ""), fallback=str(brand_row["id"])
            )
            clip_key = placement_clip_key(slug, season, episode_n, bslug, placement_id)
            store.put_file(result_path, clip_key, content_type="video/mp4")
            # Persist source + edited stills beside the clip.
            source_frame = Path(meta.get("frame_path") or "")
            if source_frame.is_file():
                store.put_file(
                    source_frame,
                    placement_frame_key(slug, season, episode_n, bslug, placement_id),
                    content_type="image/jpeg",
                )
            edited = Path(meta.get("edited_frame_path") or "")
            if edited.is_file():
                store.put_file(
                    edited,
                    placement_edited_key(slug, season, episode_n, bslug, placement_id),
                    content_type="image/jpeg",
                )
            backend = meta.get("backend") or (
                "mock" if test_mode or mock_higgsfield_enabled() else "seedance-i2v"
            )
            fields: dict[str, Any] = {
                "status": "ready",
                "clip_key": clip_key,
                "backend": backend,
                "test_mode": 1 if test_mode else 0,
                "error": None,
            }
            if meta.get("cost_usd") is not None:
                fields["cost_usd"] = meta["cost_usd"]
            if meta.get("start_s") is not None and meta.get("end_s") is not None:
                fields["clip_start_s"] = float(meta["start_s"])
                fields["clip_end_s"] = float(meta["end_s"])
            updated = update_placement(conn, placement_id, **fields)
            try:
                from product_placement.catalog import refresh_campaign_for_placement

                refresh_campaign_for_placement(conn, placement_id)
            except Exception:
                pass
            return updated
    except Exception as exc:
        try:
            update_placement(
                conn,
                placement_id,
                status="failed",
                error=str(exc)[:2000],
            )
            try:
                from product_placement.catalog import refresh_campaign_for_placement

                refresh_campaign_for_placement(conn, placement_id)
            except Exception:
                pass
        except Exception:
            pass
        raise
    finally:
        conn.close()


def run_research_job(
    job_id: str,
    *,
    business: str,
    website: str = "",
    product: str,
    note: str = "",
    client: Any = None,
    tinyfish: Any = None,
    image_http: Any = None,
    clip: Any = None,
) -> dict[str, Any]:
    """TinyFish search → fetch → one Agent run → BrandProfile + product images. Never raises.

    Image sourcing is free and never fails the job. The web client saves the brand
    after the user picks an image; this worker only builds the profile.
    """
    import httpx

    from product_placement.research import (
        PROFILE_SCHEMA,
        ResearchError,
        TinyFish,
        build_goal,
        host_of,
        normalize_site,
        pick_official_site,
        read_cache,
        to_brand_profile,
        write_cache,
    )

    site = normalize_site(website)
    steps = [
        {"label": "Searching the web", "detail": f"“{business}” · “{product}”"},
        {"label": "Fetching your website", "detail": host_of(site) or "finding your site"},
        {"label": "Reading your pages", "detail": "TinyFish web agent"},
        {"label": "Building brand profile", "detail": "tone, audience, visual identity"},
    ]
    sources: list[dict[str, Any]] = []
    write_status(
        job_id,
        type="research",
        status="running",
        step=0,
        steps=steps,
        sources=sources,
        detail="Searching the web",
        profile=None,
        error=None,
        tinyfish_run_id=None,
        streaming_url=None,
        cached=False,
        images=[],
        created_at=utc_now(),
    )
    t0 = time.perf_counter()

    def ms_since(t: float) -> int:
        return int((time.perf_counter() - t) * 1000)

    def find_images(agent_url: str | None, product_url: str | None) -> list[dict[str, Any]]:
        from product_placement.product_image import source_product_images

        steps[2]["detail"] = "Finding product images"
        write_status(job_id, step=2, steps=steps, detail="Finding product images")
        images = source_product_images(
            job_dir(job_id),
            fetch=tf.fetch,
            site=site,
            product=product,
            agent_url=agent_url,
            product_url=product_url,
            http=image_http,
            clip=clip,
            business=business,
            search=tf.search,
            search_results=found.get("results") or [],
        )
        n = len(images)
        steps[2]["detail"] = f"{n} product image{'' if n == 1 else 's'} found" if n else "No usable product image"
        return images

    try:
        tf = tinyfish or TinyFish(client=client)

        # 1. Search (free). Picks the site when none was given.
        query = f"{business} {product}".strip()
        t = time.perf_counter()
        try:
            found = tf.search(query)
            sources.append({"kind": "search", "url": f"“{query}”", "ms": ms_since(t)})
        except (ResearchError, httpx.HTTPError):
            if site:
                found = {}
            else:
                raise
        if not site:
            site = pick_official_site(found.get("results") or [], business)
            if not site:
                raise ResearchError(f"Couldn't find a website for {business}. Add it on the first page.")
        host = host_of(site)
        steps[1]["detail"] = host
        write_status(job_id, step=1, steps=steps, sources=sources, detail=f"Fetching {host}")

        cached = read_cache(site, product)
        if cached is not None:
            steps[1]["detail"] = f"{host} · cached profile"
            images = find_images(cached.get("image_url"), cached.get("product_url"))
            steps[3]["detail"] = "Saved profile from the last 7 days"
            return write_status(
                job_id,
                status="completed",
                step=len(steps),
                steps=steps,
                sources=sources,
                detail="Using a cached profile",
                profile=cached["profile"],
                images=images,
                cached=True,
                elapsed_s=round(time.perf_counter() - t0, 3),
            )

        # 2. Fetch (free). Failures are ignored; the agent reads the site itself.
        t = time.perf_counter()
        try:
            fetched = tf.fetch([site, f"{site}/about"])
            ms = ms_since(t)
            pages = fetched.get("results") or []
            for page in pages:
                url = str(page.get("final_url") or page.get("url") or "")
                label = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
                if label and label not in {s["url"] for s in sources}:
                    sources.append({"kind": "fetch", "url": label, "ms": ms})
            steps[1]["detail"] = f"{host} · {len(pages)} page{'' if len(pages) == 1 else 's'}"
        except Exception:
            steps[1]["detail"] = f"{host} · fetch skipped"

        # 3. One Agent run (paid). Live PROGRESS lines go into the step detail.
        agent_steps = 0
        write_status(
            job_id, step=2, steps=steps, sources=sources, detail="Starting the web agent"
        )

        def on_event(event: dict[str, Any]) -> None:
            nonlocal agent_steps
            kind = event.get("type")
            if kind == "STARTED" and event.get("run_id"):
                write_status(job_id, tinyfish_run_id=str(event["run_id"]))
            elif kind == "STREAMING_URL" and event.get("streaming_url"):
                write_status(job_id, streaming_url=str(event["streaming_url"]))
            elif kind == "PROGRESS" and event.get("purpose"):
                agent_steps += 1
                steps[2]["detail"] = str(event["purpose"])
                write_status(
                    job_id, steps=steps, detail=str(event["purpose"]), agent_steps=agent_steps
                )

        result = tf.agent_sse(site, build_goal(business, product, note), PROFILE_SCHEMA, on_event)
        if result.get("status") != "ok":
            reason = str(result.get("reason") or "").strip()
            what = "was blocked" if result.get("status") == "blocked" else "couldn't find the page"
            raise ResearchError(
                f"The TinyFish agent {what} on {host}" + (f": {reason}" if reason else ".")
            )

        # 4. Product images (free): agent URL, page og/img, Shopify JSON.
        agent_url = str(result.get("image_url") or "").strip() or None
        product_url = str(result.get("productUrl") or "").strip() or None
        images = find_images(agent_url, product_url)

        # 5. Map to the web BrandProfile. The client saves the brand.
        write_status(job_id, step=3, steps=steps, detail="Building brand profile")
        profile = to_brand_profile(result, business, site, product)
        write_cache(site, product, profile, image_url=agent_url, product_url=product_url)
        return write_status(
            job_id,
            status="completed",
            step=len(steps),
            steps=steps,
            detail="Profile ready",
            profile=profile,
            images=images,
            agent_steps=agent_steps,
            elapsed_s=round(time.perf_counter() - t0, 3),
        )
    except Exception as exc:
        # Exit 0 with a readable error so the spawn exit handler doesn't overwrite it.
        if isinstance(exc, ResearchError):
            message = str(exc)
        elif isinstance(exc, httpx.TimeoutException):
            message = "TinyFish took too long to respond. Try again."
        elif isinstance(exc, httpx.HTTPError):
            message = "Couldn't reach TinyFish. Check the connection and try again."
        else:
            message = f"Research failed: {exc}"
        return write_status(
            job_id,
            status="failed",
            detail="Failed",
            error=message,
            elapsed_s=round(time.perf_counter() - t0, 3),
        )


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
    sw.add_argument(
        "--method",
        default=None,
        help="wan (default) or higgsfield frame/Seedance fallback",
    )

    es = sub.add_parser("episode-suggest")
    es.add_argument("--episode-id", required=True)
    es.add_argument("-n", type=int, default=5)

    br = sub.add_parser("brand-research")
    br.add_argument("--job-id", required=True)
    br.add_argument("--business", required=True)
    br.add_argument("--website", default="")
    br.add_argument("--product", required=True)
    br.add_argument("--note", default="")

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
        help="Allow the paid swap path (requires --confirm)",
    )
    ps.add_argument(
        "--method",
        default=None,
        help="wan (default) or higgsfield frame/Seedance fallback",
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
                method=args.method,
            )
            return
        if args.command == "episode-suggest":
            result = run_episode_suggest_job(args.episode_id, n=args.n)
            print(json.dumps(result, indent=2, default=str))
            return
        if args.command == "brand-research":
            run_research_job(
                args.job_id,
                business=args.business,
                website=args.website,
                product=args.product,
                note=args.note,
            )
            return
        if args.command == "placement-swap":
            result = run_placement_swap_job(
                args.placement_id,
                confirm=bool(args.confirm),
                test_mode=bool(args.test_mode),
                method=args.method,
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
