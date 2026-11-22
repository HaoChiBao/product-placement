"""Find, clean and rank product image candidates for a brand (all free; never raises).

Sources, deduped by URL (max 8): the research agent's `image_url`, Shopify's
`/products/<handle>.json`, and image links (TinyFish Fetch `image_links`, largest
variant of each, best file-name match first) from the product page, the homepage
and up to 2 product pages linked from them. When the brand's own site gives
fewer than 3 usable images, up to 3 listings on known retailers (from web
search) are read too. Each image is downloaded directly
(image URLs are not sent to Fetch), checked, keyed to an RGBA PNG when it sits on
a plain light background, ranked (CLIP when available), and the top 3 are saved.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlparse

import cv2
import httpx
import numpy as np

MAX_CANDIDATES = 8
KEEP = 3
EXTRA_PRODUCT_PAGES = 2
DOWNLOAD_TIMEOUT_S = 15.0
MAX_BYTES = 10 * 1024 * 1024
# Opaque photos need a short side of 500 px. Images that already have
# transparency are real packshots, and tall bottles are naturally narrow
# (pepsi.ca serves 261×522), so those need a long side of 400 px instead.
MIN_SHORT_SIDE = 500
MIN_CUTOUT_LONG_SIDE = 400
IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Plain light background: border pixels with std < 8 and mean > 235.
BG_STD_MAX = 8.0
BG_MEAN_MIN = 235.0
FLOOD_DIFF = 12
BORDER_PX = 4


# Prior added to the rank score: the agent read the product page; Shopify lists
# the main packshot first; page images earn it by file-name match. CLIP alone
# can't tell flavours apart (Pepsi Zero vs Wild Cherry Zero score within 0.01).
# The agent read the product page and was asked for this exact packshot, so its
# pick outranks any file-name match (Coke: the agent's 2L bottle vs a can
# named "coca-cola_prod_zero sugar", which earns +0.35 by name).
AGENT_PRIOR = 0.5
SHOPIFY_PRIOR = 0.15
NAME_PRIOR = 0.1  # per point of file-name match
RETAILER_PRIOR = 0.0  # below every brand-site source; the brand's own image wins

# Retailer fallback: only these hosts (and their subdomains/country sites).
# An allowlist, not "any /products/ page": search also returns lookalike
# stores (allbirdshop.co) and resellers (eBay) whose photos aren't the brand's.
RETAILERS = (
    "walmart.com", "walmart.ca", "target.com", "wholefoodsmarket.com", "instacart.com",
    "instacart.ca", "cvs.com", "walgreens.com", "kroger.com", "costco.com", "costco.ca",
    "bestbuy.com", "bestbuy.ca", "homedepot.com", "homedepot.ca", "lowes.com",
    "sainsburys.co.uk", "tesco.com", "asda.com", "ocado.com", "boots.com",
    "morrisons.com", "waitrose.com", "loblaws.ca", "realcanadiansuperstore.ca",
    "shoppersdrugmart.ca", "sobeys.com", "metro.ca", "ulta.com", "sephora.com",
    "nordstrom.com", "zappos.com", "rei.com", "dickssportinggoods.com",
)
RETAILER_PAGES = 3
RETAILER_IMAGES_PER_PAGE = 2

# CLIP can't tell a 12-pack from a single can (scores within ±0.03 on live
# images), but file names and retailer titles say it: "...-8-Pack", "12pk".
_MULTIPACK = re.compile(
    r"(\b\d+\s*-?\s*(pack|pk|ct|count)s?\b|\d+pk\b|\bpack\s+of\s+\d+|multi-?pack|variety\s*pack|\bcase\s+of\b)",
    re.I
)
MULTIPACK_PENALTY = 0.1


def is_multipack(text: str, product: str) -> bool:
    """True when `text` names a pack and the product itself isn't one."""
    norm = re.sub(r"[_\-]+", " ", text)
    return bool(_MULTIPACK.search(norm)) and not _MULTIPACK.search(re.sub(r"[_\-]+", " ", product))


@dataclass
class Candidate:
    url: str
    source: str  # agent | shopify | img | retailer
    prior: float = 0.0
    referer: str = ""  # page the image came from; defaults to the brand site
    host: str = ""  # retailer host, for "Found on walmart.com"
    multipack: bool = False  # named/listed as a pack; ranked below single units
    fallback: str = ""  # URL as given, tried when the full-size `url` won't download


@dataclass
class CleanImage:
    candidate: Candidate
    rgba: np.ndarray  # H×W×4 BGRA
    cutout: bool
    score: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgba.shape[1]), int(self.rgba.shape[0])


class _Clip(Protocol):
    available: bool

    def similarity(self, frame_bgr: np.ndarray, texts: list[str]) -> list[float]: ...


# ------------------------------------------------------------------- page images

# File-name words that say nothing about which product it is.
_GENERIC = {
    "product", "products", "image", "images", "img", "photo", "front", "main", "hero",
    "large", "png", "jpg", "jpeg", "webp", "shop", "cdn", "files", "the", "and", "with",
}
_SKIP = re.compile(r"(logo|icon|favicon|sprite|badge|flag|social|payment|placeholder|banner|tile)", re.I)


def _tokens(text: str) -> set[str]:
    """Lowercase words of 3+ chars, with a plain plural "s" dropped (bottles -> bottle)."""
    words = (t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2)
    return {t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in words}


def name_hits(url: str, product: str) -> int:
    """How many product words the image's file name contains."""
    return len(_tokens(product) & (_tokens(_name_stem(url)) - _GENERIC))


_TRACKING = re.compile(r"/(pixel|beacon|akam|tracking|analytics)\b", re.I)


def _usable_url(url: str) -> bool:
    low = url.lower().split("?")[0]
    return (
        url.startswith("http")
        and not low.endswith((".svg", ".gif", ".ico"))
        and not _TRACKING.search(low)
    )


def _link_url(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("url") or item.get("src") or item.get("href") or "")
    return str(item or "")


_SCALE = re.compile(r"@(\d)x", re.I)  # pepsi@2x.png, pepsi@3x.png
_BUILD_HASH = re.compile(r"\.[0-9a-f]{8,}$", re.I)  # webpack: pepsi@3x.15edcaad74db46f6b3d5
_SHOPIFY_SIZE = re.compile(r"_(?:\d{2,4}x\d{0,4}|\d{0,4}x\d{2,4})$")  # tee_800x.jpg, tee_800x800.jpg
_SIZE_PARAMS = {"width", "height", "odnheight", "odnwidth", "odnbg"}  # Walmart: odn*
_AMAZON_MODS = re.compile(r"\._[A-Za-z0-9_,]+_$")  # 718EEARg3rL._FMwebp__SR600,600_ -> 718EEARg3rL
# Instacart: /image-server/197x197/filters:…/d2lnr5mha7bycj.cloudfront.net/product-image/file/large_x.jpeg
_EMBEDDED_CDN = re.compile(r"/([a-z0-9]+\.cloudfront\.net/.+)$", re.I)


def largest_variant(url: str) -> tuple[str, str, int]:
    """(url for the biggest size, key shared by all sizes of this image, @Nx scale).

    Drops Shopify size suffixes and `width`/`height` query params so the CDN
    serves the original; `@2x`/`@3x` siblings share a key so the caller keeps
    the larger one.
    """
    embedded = _EMBEDDED_CDN.search(urlparse(url).path)
    if "/image-server/" in url or "/store/true):" in url:
        if embedded:
            url = "https://" + embedded.group(1)
    parsed = urlparse(url)
    folder, _, filename = parsed.path.rpartition("/")
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    shopify = "shopify" in parsed.netloc or "/cdn/shop/" in parsed.path
    if shopify:
        stem = _SHOPIFY_SIZE.sub("", stem)
    if "media-amazon.com" in parsed.netloc:
        stem = _AMAZON_MODS.sub("", stem)
    if "scene7.com" in parsed.netloc:
        # Target/Scene7 renders any size on request; ask for the largest it serves.
        query = "wid=1200&hei=1200&qlt=90"
    else:
        query = "&".join(
            part
            for part in parsed.query.split("&")
            if part and part.split("=", 1)[0].lower() not in _SIZE_PARAMS
        )
    new_path = f"{folder}/{stem}{dot}{ext}"
    new_url = parsed._replace(path=new_path, query=query).geturl()
    m = _SCALE.search(stem)
    scale = int(m.group(1)) if m else 1
    key = f"{parsed.netloc}{folder}/{_SCALE.sub('', _BUILD_HASH.sub('', stem)).lower()}"
    return new_url, key, scale


def _name_stem(url: str) -> str:
    stem = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return _SCALE.sub("", _BUILD_HASH.sub("", stem))


def rank_image_links(image_links: list[Any], base_url: str, product: str) -> list[Candidate]:
    """Page image URLs, largest variant only, best product-name match first (logos and icons dropped).

    Scores the file name: +1 per product word, -0.25 per other specific word, so
    `product_image_pepsi_zero_0.png` beats `pepsi_vanilla-cream_zero.png` for "Pepsi Zero".
    """
    want = _tokens(product)
    best: dict[str, tuple[int, int, str]] = {}  # key -> (scale, first index, url)
    for i, item in enumerate(image_links):
        raw = urljoin(base_url, _link_url(item))
        if not _usable_url(raw) or _SKIP.search(_name_stem(raw)):
            continue
        url, key, scale = largest_variant(raw)
        prev = best.get(key)
        if prev is None or scale > prev[0]:
            best[key] = (scale, prev[1] if prev else i, url)
    scored: list[tuple[float, int, str]] = []
    for _, i, url in best.values():
        words = _tokens(_name_stem(url)) - _GENERIC
        score = len(want & words) - 0.25 * len(words - want)
        scored.append((score, i, url))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [
        Candidate(url, "img", NAME_PRIOR * max(0.0, score), multipack=is_multipack(_name_stem(url), product))
        for score, _, url in scored
    ]


_PRODUCT_PATH = re.compile(r"/(products?|p|item|items|shop)/", re.I)


def product_page_links(
    links: list[Any], base_url: str, product: str, exclude: set[str], limit: int
) -> list[str]:
    """Same-site product pages whose URL names the product, best match first."""
    want = _tokens(product)
    host = urlparse(base_url).netloc.lower().removeprefix("www.")
    scored: list[tuple[int, int, str]] = []
    seen = {u.rstrip("/") for u in exclude}
    for i, item in enumerate(links):
        url = urljoin(base_url, _link_url(item)).split("#")[0]
        parsed = urlparse(url)
        if parsed.netloc.lower().removeprefix("www.") != host or not _PRODUCT_PATH.search(parsed.path):
            continue
        if url.rstrip("/") in seen:
            continue
        hits = len(want & (_tokens(parsed.path) - _GENERIC))
        if hits:
            seen.add(url.rstrip("/"))
            scored.append((hits, i, url))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [url for _, _, url in scored[:limit]]


def is_shopify(image_links: list[Any], text: str = "") -> bool:
    urls = " ".join(_link_url(i) for i in image_links)
    return "cdn.shopify.com" in urls or "/cdn/shop/" in urls or "cdn.shopify.com" in text


def shopify_handle(links: list[Any], product_url: str, product: str) -> str:
    """`/products/<handle>` from the product URL, else the best-matching product link."""
    m = re.search(r"/products/([A-Za-z0-9\-_]+)", urlparse(product_url or "").path)
    if m:
        return m.group(1)
    want = _tokens(product)
    best, best_hits = "", 0
    for item in links:
        m = re.search(r"/products/([A-Za-z0-9\-_]+)", _link_url(item))
        if not m:
            continue
        hits = len(want & _tokens(m.group(1)))
        if hits > best_hits:
            best, best_hits = m.group(1), hits
    return best


def parse_shopify_json(text: str, product: str = "") -> list[Candidate]:
    body = text[text.find("{") : text.rfind("}") + 1] if "{" in text else text
    try:
        data = json.loads(body)
    except ValueError:
        # Markdown-escaped JSON (`body\_html`): drop backslashes JSON doesn't allow.
        try:
            data = json.loads(re.sub(r'\\([^"\\/bfnrtu])', r"\1", body))
        except ValueError:
            return []
    product = data.get("product") if isinstance(data, dict) else None
    images = (product or {}).get("images") or []
    out = []
    for img in images:
        src = img.get("src") if isinstance(img, dict) else None
        if isinstance(src, str) and src:
            prior = max(0.0, SHOPIFY_PRIOR - 0.02 * len(out))
            url = "https:" + src if src.startswith("//") else src
            out.append(Candidate(url, "shopify", prior, multipack=is_multipack(_name_stem(url), product)))
    return out


# ------------------------------------------------------------------- download + clean


def download_image(client: httpx.Client, url: str, referer: str) -> bytes | None:
    """Bytes for a png/jpeg/webp under 10 MB, else None."""
    headers = {"User-Agent": USER_AGENT, "Referer": referer, "Accept": "image/*"}
    try:
        with client.stream(
            "GET", url, headers=headers, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True
        ) as resp:
            if resp.status_code != 200:
                return None
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype not in IMAGE_TYPES:
                return None
            declared = int(resp.headers.get("content-length") or 0)
            if declared > MAX_BYTES:
                return None
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    return None
            return bytes(buf)
    except (httpx.HTTPError, ValueError):
        return None


def decode_image(data: bytes) -> np.ndarray | None:
    """BGR or BGRA array, else None if it's too small.

    Transparent images need a long side ≥ 400 px; opaque ones a short side ≥ 500 px.
    """
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None or arr.ndim not in (2, 3):
        return None
    if arr.dtype != np.uint8:
        arr = cv2.convertScaleAbs(arr, alpha=255.0 / max(1.0, float(arr.max())))
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    has_alpha = arr.shape[2] == 4 and int(arr[:, :, 3].min()) < 250
    if has_alpha:
        if max(arr.shape[:2]) < MIN_CUTOUT_LONG_SIDE:
            return None
    elif min(arr.shape[:2]) < MIN_SHORT_SIDE:
        return None
    return arr


def _trim(bgra: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(bgra[:, :, 3] > 8)
    if not len(xs):
        return bgra
    return bgra[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def to_rgba(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """(BGRA, cutout). Trims existing alpha; keys a plain light background; else opaque."""
    if img.shape[2] == 4 and int(img[:, :, 3].min()) < 250:
        return _trim(img), True
    bgr = img[:, :, :3].copy()
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = BORDER_PX
    border = np.concatenate([gray[:b].ravel(), gray[-b:].ravel(), gray[:, :b].ravel(), gray[:, -b:].ravel()])
    if float(border.std()) < BG_STD_MAX and float(border.mean()) > BG_MEAN_MIN:
        mask = np.zeros((h + 2, w + 2), np.uint8)
        diff = (FLOOD_DIFF,) * 3
        flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
        for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            if mask[seed[1] + 1, seed[0] + 1] == 0:
                cv2.floodFill(bgr, mask, seed, (0, 0, 0), diff, diff, flags)
        alpha = 255 - mask[1:-1, 1:-1]
        alpha = cv2.GaussianBlur(alpha, (3, 3), 0)  # ~1 px feathered edge
        bgra = np.dstack([bgr, alpha])
        if int(alpha.max()) > 0:
            return _trim(bgra), True
    alpha = np.full((h, w), 255, np.uint8)
    return np.dstack([bgr, alpha]), False


# --------------------------------------------------------------------------- ranking


def _on_white(bgra: np.ndarray) -> np.ndarray:
    a = bgra[:, :, 3:4].astype(np.float32) / 255.0
    return (bgra[:, :, :3].astype(np.float32) * a + 255.0 * (1 - a)).astype(np.uint8)


def rank(images: list[CleanImage], product: str, clip: _Clip | None) -> list[CleanImage]:
    """CLIP: packshot similarity minus max(lifestyle, logo), +0.1 for a cutout, + source prior,
    -0.1 for a multipack.

    Without CLIP: single units first, then cutouts, then source prior, then resolution.
    """
    if clip is not None and clip.available:
        texts = [
            f"a product photo of {product} on a plain background",
            "a lifestyle photo with people",
            "a logo",
        ]
        for im in images:
            sims = clip.similarity(_on_white(im.rgba), texts)
            if len(sims) == 3:
                im.score = (
                    sims[0]
                    - max(sims[1], sims[2])
                    + (0.1 if im.cutout else 0.0)
                    + im.candidate.prior
                    - (MULTIPACK_PENALTY if im.candidate.multipack else 0.0)
                )
        return sorted(images, key=lambda im: -im.score)
    for im in images:
        im.score = (
            (1.0 if im.cutout else 0.0)
            + im.candidate.prior
            - (MULTIPACK_PENALTY if im.candidate.multipack else 0.0)
        )
    return sorted(
        images,
        key=lambda im: (
            im.candidate.multipack,
            not im.cutout,
            -im.candidate.prior,
            -(im.size[0] * im.size[1]),
        ),
    )


# ---------------------------------------------------------------------- orchestration


def collect_candidates(
    fetch: Callable[..., dict[str, Any]],
    *,
    site: str,
    product: str,
    agent_url: str | None,
    product_url: str | None,
) -> list[Candidate]:
    """Agent URL, then Shopify JSON, then page image links; deduped, max 8.

    Pages: the product page and the homepage in one Fetch, then up to 2 product
    pages they link to whose URL names the product. `fetch(urls, links=True,
    image_links=True)` is TinyFish Fetch; its HTML output is cleaned text, so
    page images come from `image_links`, not from parsing tags.
    """
    found: list[Candidate] = []
    agent: Candidate | None = None
    if agent_url and _usable_url(agent_url):
        # The agent often copies a thumbnail (Shopify `_400x400`, `@2x`); ask for
        # the original, keeping its URL as the fallback.
        big, agent_key, agent_scale = largest_variant(agent_url)
        agent = Candidate(big, "agent", AGENT_PRIOR, fallback=agent_url if big != agent_url else "")
        found.append(agent)

    has_product_url = (product_url or "").startswith("http")
    first = [u for u in dict.fromkeys([product_url if has_product_url else "", site]) if u]
    image_urls: list[str] = []
    page_links: list[tuple[str, list[Any]]] = []
    page_text = ""

    def read(urls: list[str]) -> None:
        nonlocal page_text
        result = fetch(urls, links=True, image_links=True)
        for page in result.get("results") or []:
            base = str(page.get("final_url") or page.get("url") or site)
            image_urls.extend(urljoin(base, _link_url(i)) for i in page.get("image_links") or [])
            page_links.append((base, page.get("links") or []))
            page_text += str(page.get("text") or "")[:2000]

    try:
        read(first)
        extra: list[str] = []
        fetched = set(first)
        for base, links in page_links:
            for url in product_page_links(links, base, product, fetched, EXTRA_PRODUCT_PAGES - len(extra)):
                extra.append(url)
                fetched.add(url)
        if extra:
            read(extra)
        if is_shopify(image_urls, page_text):
            all_links = [link for _, links in page_links for link in links]
            base = page_links[0][0] if page_links else site
            handle = shopify_handle(all_links, product_url or "", product)
            if handle:
                origin = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
                # "html" returns the JSON body as-is; "markdown" escapes `_` in keys.
                js = fetch([f"{origin}/products/{handle}.json"], format="html")
                js_pages = js.get("results") or []
                if js_pages:
                    found += parse_shopify_json(str(js_pages[0].get("text") or ""), product)
    except Exception:
        pass
    if agent is not None:
        # A bigger `@3x` of the agent's `@2x` on the page: take it, with agent credit.
        for raw in image_urls:
            url, key, scale = largest_variant(raw)
            if key == agent_key and scale > agent_scale:
                agent.url, agent.fallback, agent_scale = url, agent_url, scale
    found += rank_image_links(image_urls, site, product)

    seen: set[str] = set()
    out: list[Candidate] = []
    for c in found:
        if c.url in seen:
            continue
        seen.add(c.url)
        out.append(c)
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_retailer(url: str, brand_site: str = "") -> bool:
    host = _host(url)
    if not host or (brand_site and host == _host(brand_site)):
        return False
    return any(host == r or host.endswith("." + r) for r in RETAILERS)


_SEARCH_PAGE = re.compile(
    r"(^/(s|search|browse|c|cp|b|shop/search)(/|$)|/search\b|[?&](k|q|query|searchterm)=)", re.I
)


def retailer_pages(results: list[dict[str, Any]], brand_site: str, limit: int = RETAILER_PAGES) -> list[str]:
    """Retailer product listings from search results, one per retailer, in search order.

    Search-result and category pages (target.com/s/…, instacart …?k=…) are
    skipped: they show many products, not this one.
    """
    out: list[str] = []
    hosts: set[str] = set()
    for r in results:
        url = str(r.get("url") or "")
        parsed = urlparse(url)
        if _SEARCH_PAGE.search(parsed.path + ("?" + parsed.query if parsed.query else "")):
            continue
        if not is_retailer(url, brand_site) or _host(url) in hosts:
            continue
        hosts.add(_host(url))
        out.append(url)
        if len(out) >= limit:
            break
    return out


def collect_retailer_candidates(
    fetch: Callable[..., dict[str, Any]],
    search: Callable[[str], dict[str, Any]] | None,
    *,
    business: str,
    product: str,
    site: str,
    seed_results: list[dict[str, Any]] | None = None,
) -> list[Candidate]:
    """Images from up to 3 retailer listings: the job's own search results first,
    then a shopping search if those had too few retailers. 2 per listing."""
    results = list(seed_results or [])
    pages = retailer_pages(results, site)
    if len(pages) < RETAILER_PAGES and search is not None:
        try:
            more = search(f"{business} {product} buy".strip()).get("results") or []
            pages = retailer_pages(results + list(more), site)
        except Exception:
            pass
    if not pages:
        return []
    out: list[Candidate] = []
    try:
        fetched = fetch(pages, image_links=True)
    except Exception:
        return []
    for page in fetched.get("results") or []:
        base = str(page.get("final_url") or page.get("url") or "")
        if not is_retailer(base, site):
            continue  # redirected off the retailer (login, region picker)
        ranked = rank_image_links(page.get("image_links") or [], base, product)
        # Retailer pages carry many other products and site graphics. Take
        # images named after the product; only if none are (Target's GUEST_…
        # ids), fall back to the first images on the page.
        named = [c for c in ranked if name_hits(c.url, product)]
        picks = named or sorted(ranked, key=lambda c: _page_order(c, page.get("image_links") or [], base))
        listed_as_pack = is_multipack(str(page.get("title") or ""), product)
        for c in picks[:RETAILER_IMAGES_PER_PAGE]:
            out.append(
                Candidate(
                    c.url,
                    "retailer",
                    RETAILER_PRIOR + 0.5 * c.prior,
                    referer=base,
                    host=_host(base),
                    multipack=c.multipack or listed_as_pack,
                )
            )
    return out


def _page_order(c: Candidate, image_links: list[Any], base: str) -> int:
    for i, item in enumerate(image_links):
        if largest_variant(urljoin(base, _link_url(item)))[0] == c.url:
            return i
    return len(image_links)


def _download_clean(
    client: httpx.Client, cands: list[Candidate], site: str, seen: set[str]
) -> list[CleanImage]:
    kept: list[CleanImage] = []
    for c in cands:
        data = download_image(client, c.url, c.referer or site)
        if data is None and c.fallback:
            c = replace(c, url=c.fallback)
            data = download_image(client, c.url, c.referer or site)
        if data is None:
            continue
        # Same file on two hosts (shop.com/cdn/shop/… and cdn.shopify.com/…).
        digest = hashlib.sha1(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        img = decode_image(data)
        if img is None:
            continue
        rgba, cutout = to_rgba(img)
        kept.append(CleanImage(c, rgba, cutout))
    return kept


def source_product_images(
    out_dir: Path,
    *,
    fetch: Callable[..., dict[str, Any]],
    site: str,
    product: str,
    agent_url: str | None = None,
    product_url: str | None = None,
    http: httpx.Client | None = None,
    clip: _Clip | None = None,
    business: str = "",
    search: Callable[[str], dict[str, Any]] | None = None,
    search_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Save the top 3 as `candidate_<n>.png` in `out_dir`; return status entries, best first.

    Retailer listings are only read when the brand's site gives fewer than 3
    usable images.
    """
    try:
        cands = collect_candidates(
            fetch, site=site, product=product, agent_url=agent_url, product_url=product_url
        )
        client = http or httpx.Client()
        seen: set[str] = set()
        kept = _download_clean(client, cands, site, seen)
        if len(kept) < KEEP:
            retail = collect_retailer_candidates(
                fetch,
                search,
                business=business,
                product=product,
                site=site,
                seed_results=search_results,
            )
            kept += _download_clean(client, retail, site, seen)
        if not kept:
            return []
        if clip is None:
            try:
                from product_placement.clip_onnx import ClipOnnx

                clip = ClipOnnx()
            except Exception:
                clip = None
        ranked = rank(kept, product, clip)[:KEEP]
        out_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for n, im in enumerate(ranked, start=1):
            name = f"candidate_{n}.png"
            if not cv2.imwrite(str(out_dir / name), im.rgba):
                continue
            w, h = im.size
            entry = {
                "name": name,
                "source": im.candidate.source,
                "url": im.candidate.url,
                "w": w,
                "h": h,
                "cutout": im.cutout,
                "score": round(float(im.score), 4),
            }
            if im.candidate.host:
                entry["host"] = im.candidate.host
            if im.candidate.multipack:
                entry["multipack"] = True
            entries.append(entry)
        return entries
    except Exception:
        return []
