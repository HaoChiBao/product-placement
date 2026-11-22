"""Product image sourcing: keying, checks, Shopify, ranking, end to end (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import httpx
import numpy as np

from product_placement.product_image import (
    MAX_BYTES,
    Candidate,
    CleanImage,
    collect_candidates,
    decode_image,
    download_image,
    is_shopify,
    parse_shopify_json,
    rank,
    rank_image_links,
    shopify_handle,
    source_product_images,
    to_rgba,
)


def packshot(w: int = 800, h: int = 800, bg: int = 255) -> np.ndarray:
    """A dark 300×400 'can' centred on a plain background."""
    img = np.full((h, w, 3), bg, np.uint8)
    img[200:600, 250:550] = (40, 30, 200)
    return img


def png_bytes(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        return routes.get(str(req.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- keying + trimming


def test_white_background_is_keyed_to_alpha_and_trimmed():
    rgba, cutout = to_rgba(packshot())
    assert cutout is True
    assert rgba.shape[2] == 4
    h, w = rgba.shape[:2]
    # Trimmed to the product (allowing ~1 px of feathered edge).
    assert abs(w - 300) <= 2 and abs(h - 400) <= 2
    assert rgba[h // 2, w // 2, 3] == 255  # product stays opaque


def test_existing_alpha_is_trimmed_not_rekeyed():
    img = np.zeros((800, 800, 4), np.uint8)
    img[100:700, 300:500] = (10, 20, 30, 255)
    rgba, cutout = to_rgba(img)
    assert cutout is True
    assert rgba.shape[:2] == (600, 200)


def test_busy_background_stays_opaque():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (800, 800, 3), dtype=np.uint8)
    rgba, cutout = to_rgba(img)
    assert cutout is False
    assert rgba.shape[:2] == (800, 800)
    assert int(rgba[:, :, 3].min()) == 255


# ----------------------------------------------------------------- download checks


def test_decode_rejects_small_and_non_images():
    assert decode_image(png_bytes(packshot(400, 400))) is None
    assert decode_image(b"<html>not an image</html>") is None
    assert decode_image(png_bytes(packshot())) is not None


def test_download_rejects_wrong_type_oversize_and_errors():
    good = png_bytes(packshot())
    http = client(
        {
            "https://cdn.test/ok.png": httpx.Response(200, headers={"content-type": "image/png"}, content=good),
            "https://cdn.test/page": httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"),
            "https://cdn.test/big.png": httpx.Response(
                200, headers={"content-type": "image/png"}, content=b"\0" * (MAX_BYTES + 1)
            ),
            "https://cdn.test/svg": httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>"),
        }
    )
    assert download_image(http, "https://cdn.test/ok.png", "https://brand.test") == good
    assert download_image(http, "https://cdn.test/page", "https://brand.test") is None
    assert download_image(http, "https://cdn.test/big.png", "https://brand.test") is None
    assert download_image(http, "https://cdn.test/svg", "https://brand.test") is None
    assert download_image(http, "https://cdn.test/missing.png", "https://brand.test") is None


def test_download_sends_browser_headers():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    download_image(http, "https://cdn.test/a.jpg", "https://brand.test")
    assert seen["referer"] == "https://brand.test"
    assert "Mozilla" in seen["user-agent"]
    assert "x-api-key" not in seen


# ------------------------------------------------------------------ page + Shopify


def test_rank_image_links_prefers_the_named_product_and_drops_logos():
    links = [
        "https://www.pepsi.com/themes/pepsizero_fe/logo.png",
        "https://www.pepsi.com/prod/2026-10/product_image_pepsi_vanilla-cream_zero_0.png",
        {"url": "/prod/2026-01/product_image_pepsi_zero_0.png"},
        "https://www.pepsi.com/prod/2026-01/product_image_pepsi_wild_cherry.png",
        "https://www.pepsi.com/icons/cart.svg",
    ]
    cands = rank_image_links(links, "https://www.pepsi.com/", "Pepsi Zero Sugar 355 ml can")
    urls = [c.url for c in cands]
    assert urls[0] == "https://www.pepsi.com/prod/2026-01/product_image_pepsi_zero_0.png"
    assert not any("logo" in u or u.endswith(".svg") for u in urls)
    assert all(c.source == "img" for c in cands)


def test_shopify_json_and_handle():
    payload = json.dumps(
        {"product": {"images": [{"src": "//cdn.shopify.com/a.png"}, {"src": "https://cdn.shopify.com/b.jpg"}]}}
    )
    assert [c.url for c in parse_shopify_json(payload)] == [
        "https://cdn.shopify.com/a.png",
        "https://cdn.shopify.com/b.jpg",
    ]
    assert all(c.source == "shopify" for c in parse_shopify_json(payload))
    assert parse_shopify_json("not json") == []
    escaped = '<p>{"product":{"body\\_html":"x","images":[{"src":"https://cdn.shopify.com/c.png"}]}}</p>'
    assert [c.url for c in parse_shopify_json(escaped)] == ["https://cdn.shopify.com/c.png"]
    assert shopify_handle([], "https://shop.test/products/tree-runner?variant=1", "x") == "tree-runner"
    links = ["https://shop.test/products/gift-card", {"url": "/products/mens-tree-runners"}]
    assert shopify_handle(links, "", "Men's Tree Runners") == "mens-tree-runners"
    assert is_shopify(["https://shop.test/cdn/shop/files/a.png"])
    assert is_shopify(["https://cdn.shopify.com/s/files/a.png"])
    assert not is_shopify(["https://www.pepsi.com/a.png"])


def test_collect_candidates_order_dedupe_and_shopify_fetch():
    calls: list[tuple[list[str], dict]] = []

    def fetch(urls: list[str], **kw) -> dict:
        calls.append((urls, kw))
        if urls[0].endswith(".json"):
            return {"results": [{"text": json.dumps({"product": {"images": [{"src": "https://cdn.shopify.com/p.png"}]}})}]}
        return {
            "results": [
                {
                    "text": "Runner",
                    "final_url": "https://shop.test/products/runner",
                    "image_links": ["https://cdn.shopify.com/p.png", "https://shop.test/hero.jpg"],
                    "links": [],
                }
            ]
        }

    cands = collect_candidates(
        fetch,
        site="https://shop.test",
        product="Runner",
        agent_url="https://cdn.shopify.com/agent.png",
        product_url="https://shop.test/products/runner",
    )
    assert [(c.source, c.url) for c in cands] == [
        ("agent", "https://cdn.shopify.com/agent.png"),
        ("shopify", "https://cdn.shopify.com/p.png"),
        ("img", "https://shop.test/hero.jpg"),
    ]
    assert calls == [
        (["https://shop.test/products/runner"], {"links": True, "image_links": True}),
        (["https://shop.test/products/runner.json"], {"format": "html"}),
    ]


# -------------------------------------------------------------------------- ranking


class StubClip:
    available = True

    def __init__(self, by_mean: dict[int, list[float]]):
        self.by_mean = by_mean

    def similarity(self, frame_bgr, texts):
        return self.by_mean[int(round(float(frame_bgr.mean())))]


def _clean(name: str, value: int, cutout: bool) -> CleanImage:
    rgba = np.full((600, 600, 4), (value, value, value, 255), np.uint8)
    return CleanImage(Candidate(f"https://x.test/{name}", "img"), rgba, cutout)


def test_rank_with_clip_uses_packshot_minus_lifestyle_or_logo_plus_cutout_bonus():
    lifestyle = _clean("lifestyle", 10, False)
    logo = _clean("logo", 20, True)
    pack = _clean("pack", 30, False)
    pack_cut = _clean("pack_cut", 40, True)
    clip = StubClip(
        {
            10: [0.30, 0.35, 0.10],  # -0.05
            20: [0.25, 0.10, 0.30],  # -0.05 + 0.1 = 0.05
            30: [0.32, 0.12, 0.10],  # 0.20
            40: [0.30, 0.12, 0.10],  # 0.18 + 0.1 = 0.28
        }
    )
    ranked = rank([lifestyle, logo, pack, pack_cut], "Zero", clip)
    assert [im.candidate.url.rsplit("/", 1)[1] for im in ranked] == ["pack_cut", "pack", "logo", "lifestyle"]


def test_rank_without_clip_prefers_cutouts_then_resolution():
    small_cut = CleanImage(Candidate("s", "img"), np.zeros((500, 500, 4), np.uint8), True)
    big_opaque = CleanImage(Candidate("b", "img"), np.zeros((2000, 2000, 4), np.uint8), False)
    big_cut = CleanImage(Candidate("c", "img"), np.zeros((900, 900, 4), np.uint8), True)
    ranked = rank([small_cut, big_opaque, big_cut], "Zero", None)
    assert [im.candidate.url for im in ranked] == ["c", "s", "b"]


# ---------------------------------------------------------------------- end to end


def test_source_product_images_saves_transparent_candidates(tmp_path: Path):
    def fetch(urls, **kw):
        return {"results": [{"final_url": "https://brand.test", "image_links": ["https://cdn.test/small.png"]}]}

    http = client(
        {
            "https://cdn.test/pack.png": httpx.Response(
                200, headers={"content-type": "image/png"}, content=png_bytes(packshot())
            ),
            "https://cdn.test/small.png": httpx.Response(
                200, headers={"content-type": "image/png"}, content=png_bytes(packshot(300, 300))
            ),
        }
    )

    class NoClip:
        available = False

    entries = source_product_images(
        tmp_path,
        fetch=fetch,
        site="https://brand.test",
        product="Zero",
        agent_url="https://cdn.test/pack.png",
        http=http,
        clip=NoClip(),
    )
    assert len(entries) == 1
    e = entries[0]
    assert e["name"] == "candidate_1.png" and e["source"] == "agent" and e["cutout"] is True
    saved = cv2.imread(str(tmp_path / "candidate_1.png"), cv2.IMREAD_UNCHANGED)
    assert saved.shape[2] == 4 and (saved.shape[1], saved.shape[0]) == (e["w"], e["h"])


def test_source_product_images_never_raises(tmp_path: Path):
    def fetch(urls, **kw):
        raise RuntimeError("fetch exploded")

    assert source_product_images(tmp_path, fetch=fetch, site="https://brand.test", product="Zero") == []


def test_name_prior_outweighs_clip_noise_between_flavours():
    zero = CleanImage(Candidate("pepsi_zero", "img", prior=0.2), np.full((600, 600, 4), (30, 30, 30, 255), np.uint8), True)
    cherry = CleanImage(Candidate("wild_cherry_zero", "img", prior=0.15), np.full((600, 600, 4), (40, 40, 40, 255), np.uint8), True)
    # Live CLIP scores: wild cherry edges out plain zero by ~0.015.
    clip = StubClip({30: [0.300, 0.10, 0.10], 40: [0.315, 0.10, 0.10]})
    assert [im.candidate.url for im in rank([cherry, zero], "Pepsi Zero Sugar", clip)] == [
        "pepsi_zero",
        "wild_cherry_zero",
    ]


def test_same_image_on_two_hosts_is_kept_once(tmp_path: Path):
    body = png_bytes(packshot())
    http = client(
        {
            u: httpx.Response(200, headers={"content-type": "image/png"}, content=body)
            for u in ("https://shop.test/cdn/shop/files/a.png", "https://cdn.shopify.com/s/files/a.png")
        }
    )

    def fetch(urls, **kw):
        return {"results": [{"final_url": "https://shop.test", "image_links": ["https://cdn.shopify.com/s/files/a.png"]}]}

    class NoClip:
        available = False

    entries = source_product_images(
        tmp_path,
        fetch=fetch,
        site="https://shop.test",
        product="A",
        agent_url="https://shop.test/cdn/shop/files/a.png",
        http=http,
        clip=NoClip(),
    )
    assert [e["source"] for e in entries] == ["agent"]
