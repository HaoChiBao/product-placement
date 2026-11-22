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
    collect_retailer_candidates,
    is_multipack,
    is_retailer,
    largest_variant,
    product_page_links,
    retailer_pages,
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
        (["https://shop.test/products/runner", "https://shop.test"], {"links": True, "image_links": True}),
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


# ------------------------------------------------------- sizing, variants, more pages


def _alpha_png(w: int, h: int) -> bytes:
    img = np.zeros((h, w, 4), np.uint8)
    img[h // 8 : -h // 8, w // 8 : -w // 8] = (20, 40, 200, 255)
    return png_bytes(img)


def test_transparent_packshots_use_long_side_opaque_use_short_side():
    # pepsi.ca bottles: 261×522 @3x passes, 174×348 @2x doesn't.
    assert decode_image(_alpha_png(261, 522)) is not None
    assert decode_image(_alpha_png(174, 348)) is None
    # The same size without transparency still needs a 500 px short side.
    assert decode_image(png_bytes(packshot(261, 522))) is None
    assert decode_image(png_bytes(packshot(500, 900))) is not None


def test_largest_variant_keeps_3x_and_ignores_build_hash_in_name_score():
    links = [
        "https://www.pepsi.ca/static/media/PepsiLogo.82b6ee32c66958b07575b426f377def6.svg",
        "https://www.pepsi.ca/static/media/pepsi@2x.ba5a0f26e34d8c10af14.png",
        "https://www.pepsi.ca/static/media/diet-pepsi@2x.21a4ad7bcea9cc8d7403.png",
        "https://www.pepsi.ca/static/media/pepsi@3x.15edcaad74db46f6b3d5.png",
        "https://www.pepsi.ca/static/media/diet-pepsi@3x.3bd62dae6c710f203a35.png",
    ]
    urls = [c.url for c in rank_image_links(links, "https://www.pepsi.ca/", "pepsi bottle")]
    assert urls == [
        "https://www.pepsi.ca/static/media/pepsi@3x.15edcaad74db46f6b3d5.png",
        "https://www.pepsi.ca/static/media/diet-pepsi@3x.3bd62dae6c710f203a35.png",
    ]


def test_largest_variant_strips_shopify_and_width_params():
    url, _, _ = largest_variant("https://shop.test/cdn/shop/files/tee_800x.jpg?v=12&width=640")
    assert url == "https://shop.test/cdn/shop/files/tee.jpg?v=12"
    url, _, _ = largest_variant("https://cdn.shopify.com/s/files/1/a/tee_800x800.png")
    assert url == "https://cdn.shopify.com/s/files/1/a/tee.png"
    # Non-Shopify names keep their suffix; only width/height params go.
    url, _, _ = largest_variant("https://brand.test/img/photo_800x.jpg?height=300&q=80")
    assert url == "https://brand.test/img/photo_800x.jpg?q=80"


def test_product_page_links_same_site_and_named():
    links = [
        "https://www.pepsi.ca/product/pepsi",
        "https://www.pepsi.ca/product/diet-pepsi",
        "https://www.pepsi.ca/about",
        "https://other.test/product/pepsi",
        "/product/pepsi-zero-sugar",
    ]
    got = product_page_links(links, "https://www.pepsi.ca/", "pepsi bottle", {"https://www.pepsi.ca"}, 2)
    assert got == ["https://www.pepsi.ca/product/pepsi", "https://www.pepsi.ca/product/diet-pepsi"]


def test_collect_candidates_reads_linked_product_pages():
    calls: list[list[str]] = []

    def fetch(urls, **kw):
        calls.append(urls)
        if urls == ["https://brand.test"]:
            return {"results": [{"final_url": "https://brand.test/", "image_links": [],
                                 "links": ["/products/zero-can", "/about"]}]}
        return {"results": [{"final_url": urls[0], "image_links": ["/img/zero-can-front.png"], "links": []}]}

    cands = collect_candidates(fetch, site="https://brand.test", product="Zero can", agent_url=None, product_url=None)
    assert calls == [["https://brand.test"], ["https://brand.test/products/zero-can"]]
    assert [c.url for c in cands] == ["https://brand.test/img/zero-can-front.png"]


def test_agent_thumbnail_is_upgraded_to_the_original():
    # Olipop: the agent copied the `_400x400` thumbnail (246×397 once decoded).
    thumb = "https://cdn.shopify.com/s/files/1/0034/files/Vintage-Cola_main_400x400.webp"
    original = "https://cdn.shopify.com/s/files/1/0034/files/Vintage-Cola_main.webp"

    def fetch(urls, **kw):
        return {"results": [{"final_url": "https://brand.test", "image_links": [original], "links": []}]}

    cands = collect_candidates(fetch, site="https://brand.test", product="Vintage Cola", agent_url=thumb, product_url=None)
    assert [(c.source, c.url, c.fallback) for c in cands] == [("agent", original, thumb)]


def test_agent_2x_takes_the_3x_on_the_page():
    def fetch(urls, **kw):
        return {
            "results": [
                {
                    "final_url": "https://www.pepsi.ca/",
                    "image_links": ["/static/media/pepsi@2x.15edcaad.png", "/static/media/pepsi@3x.9b1c0d2e.png"],
                    "links": [],
                }
            ]
        }

    agent = "https://www.pepsi.ca/static/media/pepsi@2x.15edcaad.png"
    cands = collect_candidates(fetch, site="https://www.pepsi.ca", product="Pepsi", agent_url=agent, product_url=None)
    assert [(c.source, c.url, c.fallback) for c in cands] == [
        ("agent", "https://www.pepsi.ca/static/media/pepsi@3x.9b1c0d2e.png", agent)
    ]


def test_agent_falls_back_to_its_own_url_when_the_original_is_missing(tmp_path: Path):
    thumb = "https://cdn.shopify.com/files/can_400x400.png"

    def fetch(urls, **kw):
        return {"results": [{"final_url": "https://brand.test", "image_links": [], "links": []}]}

    http = client(
        {thumb: httpx.Response(200, headers={"content-type": "image/png"}, content=png_bytes(packshot()))}
    )

    class NoClip:
        available = False

    entries = source_product_images(
        tmp_path, fetch=fetch, site="https://brand.test", product="Can", agent_url=thumb, http=http, clip=NoClip()
    )
    assert [(e["source"], e["url"]) for e in entries] == [("agent", thumb)]


# ------------------------------------------------------------------ retailer fallback


def test_retailer_allowlist_skips_lookalikes_resellers_and_the_brand():
    assert is_retailer("https://www.walmart.com/ip/Pepsi/13424347")
    assert is_retailer("https://target.com/p/pepsi/-/A-1")
    assert not is_retailer("https://allbirdshop.co/product/mens-tree-runners")  # lookalike store
    assert not is_retailer("https://www.ebay.com/b/pepsi-bottle/bn_1")  # reseller photos
    assert not is_retailer("https://notwalmart.com/ip/1")
    assert not is_retailer("https://www.target.com/p/x", brand_site="https://target.com")
    results = [
        {"url": "https://www.pepsi.com/"},
        {"url": "https://www.walmart.com/ip/a/1"},
        {"url": "https://www.walmart.com/ip/b/2"},
        {"url": "https://www.ebay.com/b/pepsi"},
        {"url": "https://target.com/p/c/-/A-3"},
    ]
    assert retailer_pages(results, "https://pepsi.com") == [
        "https://www.walmart.com/ip/a/1",
        "https://target.com/p/c/-/A-3",
    ]


def test_largest_variant_for_retailer_cdns():
    url, _, _ = largest_variant(
        "https://i5.walmartimages.com/asr/ef07734e.4416f.jpeg?odnHeight=117&odnWidth=117&odnBg=FFFFFF"
    )
    assert url == "https://i5.walmartimages.com/asr/ef07734e.4416f.jpeg"
    url, _, _ = largest_variant("https://m.media-amazon.com/images/I/718EEARg3rL._FMwebp__SR600,600_.jpg")
    assert url == "https://m.media-amazon.com/images/I/718EEARg3rL.jpg"
    a = largest_variant("https://target.scene7.com/is/image/Target/GUEST_60ff?wid=300&hei=300&qlt=80")
    b = largest_variant("https://target.scene7.com/is/image/Target/GUEST_60ff?wid=800&hei=800&qlt=80&fmt=pjpeg")
    assert a[0] == b[0] == "https://target.scene7.com/is/image/Target/GUEST_60ff?wid=1200&hei=1200&qlt=90"


def test_collect_retailer_candidates_uses_job_search_then_shopping_search():
    searches: list[str] = []
    fetched: list[list[str]] = []

    def search(q):
        searches.append(q)
        return {"results": [{"url": "https://www.target.com/p/pepsi/-/A-1"}]}

    def fetch(urls, **kw):
        fetched.append(urls)
        return {
            "results": [
                {
                    "final_url": u,
                    "image_links": [
                        "https://cdn.test/site-banner.png",
                        "https://cdn.test/Pepsi-Cola-2L-Bottle.jpeg",
                        "https://cdn.test/other.jpeg",
                    ],
                }
                for u in urls
            ]
        }

    cands = collect_retailer_candidates(
        fetch,
        search,
        business="Pepsi",
        product="Pepsi bottle",
        site="https://pepsi.ca",
        seed_results=[{"url": "https://www.walmart.com/ip/Pepsi/1"}, {"url": "https://www.pepsi.ca"}],
    )
    assert searches == ["Pepsi Pepsi bottle buy"]
    assert fetched == [["https://www.walmart.com/ip/Pepsi/1", "https://www.target.com/p/pepsi/-/A-1"]]
    # Only the image named after the product, from each listing.
    assert [c.url for c in cands] == ["https://cdn.test/Pepsi-Cola-2L-Bottle.jpeg"] * 2
    assert {c.host for c in cands} == {"walmart.com", "target.com"}
    assert all(c.source == "retailer" and c.referer.startswith("https://") for c in cands)


def test_retailers_only_when_brand_site_comes_up_short(tmp_path: Path):
    body = png_bytes(packshot())
    other = png_bytes(packshot(900, 900, bg=250))

    class NoClip:
        available = False

    def run(brand_images: list[str]) -> tuple[list[dict], list[str]]:
        searches: list[str] = []

        def fetch(urls, **kw):
            if urls[0].startswith("https://www.walmart.com"):
                return {"results": [{"final_url": urls[0], "image_links": ["https://cdn.test/walmart.png"]}]}
            return {"results": [{"final_url": "https://brand.test", "image_links": brand_images}]}

        routes = {
            f"https://cdn.test/{n}.png": httpx.Response(200, headers={"content-type": "image/png"}, content=c)
            for n, c in [("a", body), ("walmart", other)]
        }
        for i, n in enumerate(["b", "c"]):
            img = packshot()
            img[0, 0] = (i, i, i)  # distinct bytes
            routes[f"https://cdn.test/{n}.png"] = httpx.Response(
                200, headers={"content-type": "image/png"}, content=png_bytes(img)
            )
        entries = source_product_images(
            tmp_path,
            fetch=fetch,
            site="https://brand.test",
            product="Zero",
            http=client(routes),
            clip=NoClip(),
            business="Brand",
            search=lambda q: searches.append(q) or {"results": []},
            search_results=[{"url": "https://www.walmart.com/ip/zero/1"}],
        )
        return entries, searches

    full, searches = run(["https://cdn.test/a.png", "https://cdn.test/b.png", "https://cdn.test/c.png"])
    assert [e["source"] for e in full] == ["img", "img", "img"] and searches == []

    short, _ = run(["https://cdn.test/a.png"])
    assert [e["source"] for e in short] == ["img", "retailer"]
    assert short[1]["host"] == "walmart.com"


def test_retailer_search_pages_tracking_and_instacart_unwrap():
    results = [
        {"url": "https://www.target.com/s/pepsi+bottle"},
        {"url": "https://www.instacart.com/store/s?k=pepsi+glass+bottle"},
        {"url": "https://www.walmart.com/ip/Pepsi-Original/13424347"},
        {"url": "https://www.target.com/p/pepsi-cola-2-l-bottle/-/A-13044196"},
    ]
    assert retailer_pages(results, "https://pepsi.ca") == [
        "https://www.walmart.com/ip/Pepsi-Original/13424347",
        "https://www.target.com/p/pepsi-cola-2-l-bottle/-/A-13044196",
    ]
    assert rank_image_links(["https://www.walmart.com/akam/13/pixel_22cb4d0c?a=1"], "", "pepsi") == []
    assert rank_image_links(["https://brand.test/coca-cola-fall-hockey-big-wins-banner-dt.png"], "", "coca-cola") == []
    url, _, _ = largest_variant(
        "https://www.instacart.com/image-server/197x197/filters:fill(FFFFFF,true):format(jpg)/"
        "d2lnr5mha7bycj.cloudfront.net/product-image/file/large_f67a7679.jpeg"
    )
    assert url == "https://d2lnr5mha7bycj.cloudfront.net/product-image/file/large_f67a7679.jpeg"


def test_retailer_prefers_named_images_else_page_order():
    def links_for(url):
        if "walmart" in url:
            return [
                "https://i5.walmartimages.com/dfw/4ff9c6c9/k2-_0a671c38.v1.png",
                "https://i5.walmartimages.com/seo/Pepsi-Original-Cola-12-fl-oz-Bottles_c54b.c169.jpeg?odnHeight=640",
                "https://i5.walmartimages.com/asr/ef07734e.4416f.jpeg?odnHeight=117",
            ]
        return [
            "https://target.scene7.com/is/image/Target/GUEST_60ff?wid=800&hei=800",
            "https://target.scene7.com/is/image/Target/GUEST_f788?wid=800&hei=800",
            "https://target.scene7.com/is/image/Target/GUEST_aaaa?wid=800&hei=800",
        ]

    def fetch(urls, **kw):
        return {"results": [{"final_url": u, "image_links": links_for(u)} for u in urls]}

    cands = collect_retailer_candidates(
        fetch,
        None,
        business="Pepsi",
        product="Pepsi bottle",
        site="https://pepsi.ca",
        seed_results=[{"url": "https://www.walmart.com/ip/Pepsi/1"}, {"url": "https://www.target.com/p/pepsi/-/A-1"}],
    )
    walmart = [c.url for c in cands if c.host == "walmart.com"]
    target = [c.url for c in cands if c.host == "target.com"]
    assert walmart == ["https://i5.walmartimages.com/seo/Pepsi-Original-Cola-12-fl-oz-Bottles_c54b.c169.jpeg"]
    assert target == [
        "https://target.scene7.com/is/image/Target/GUEST_60ff?wid=1200&hei=1200&qlt=90",
        "https://target.scene7.com/is/image/Target/GUEST_f788?wid=1200&hei=1200&qlt=90",
    ]


def test_multipack_detection_and_ranking():
    assert is_multipack("Pepsi-Original-Cola-Soda-Pop-12-fl-oz-Bottles-8-Pack", "pepsi bottle")
    assert is_multipack("LD-Core-StillWater_12oz_6pk_FRONT", "Mountain Water can")
    assert is_multipack("Pack-of-2-Coca-Cola-Zero-Sugar-Soda-Bottle-2-liter", "Coca-Cola Zero Sugar 2L bottle")
    assert is_multipack("Liquid Death Still Mountain Water, 16.9 oz Tallboys, 12 pack", "Mountain Water")
    assert not is_multipack("product_image_pepsi_zero_0", "Pepsi Zero Sugar 355 ml can")
    assert not is_multipack("12oz_can_front", "Mountain Water can")  # ounces aren't a pack
    assert not is_multipack("Pepsi-12-Pack", "Pepsi 12 pack")  # the product is the pack

    single = CleanImage(Candidate("single", "retailer"), np.zeros((600, 600, 4), np.uint8), True)
    pack = CleanImage(Candidate("pack", "retailer", prior=0.05, multipack=True), np.zeros((900, 900, 4), np.uint8), True)
    assert [im.candidate.url for im in rank([pack, single], "Pepsi", None)] == ["single", "pack"]
    clip = StubClip({255: [0.30, 0.10, 0.10]})  # transparent pixels render white
    assert [im.candidate.url for im in rank([pack, single], "Pepsi", clip)] == ["single", "pack"]


def test_retailer_title_marks_multipack():
    def fetch(urls, **kw):
        return {"results": [{"final_url": urls[0], "title": "Pepsi Cola, 12 fl oz Bottles, 8 Pack",
                             "image_links": ["https://cdn.test/Pepsi-Cola.jpeg"]}]}

    cands = collect_retailer_candidates(
        fetch, None, business="Pepsi", product="Pepsi bottle", site="https://pepsi.ca",
        seed_results=[{"url": "https://www.walmart.com/ip/Pepsi/1"}],
    )
    assert [c.multipack for c in cands] == [True]


def test_agent_pick_beats_a_well_named_site_image():
    agent = CleanImage(Candidate("agent_2l_bottle", "agent", prior=0.5), np.full((600, 600, 4), (30, 30, 30, 255), np.uint8), True)
    can = CleanImage(Candidate("coca-cola_prod_zero_sugar", "img", prior=0.35), np.full((600, 600, 4), (40, 40, 40, 255), np.uint8), True)
    clip = StubClip({30: [0.28, 0.10, 0.10], 40: [0.30, 0.10, 0.10]})  # CLIP slightly prefers the can
    assert [im.candidate.source for im in rank([can, agent], "Coca-Cola Zero Sugar 2L bottle", clip)] == ["agent", "img"]
