"""Find, clean and rank product image candidates for a brand (all free; never raises).

Sources, deduped by URL (max 6): the research agent's `image_url`, Shopify's
`/products/<handle>.json`, and the page's image links (TinyFish Fetch
`image_links`, best file-name match first). Each image is downloaded directly (image URLs are not
sent to Fetch), checked, keyed to an RGBA PNG when it sits on a plain light
background, ranked (CLIP when available), and the top 3 are saved.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlparse

import cv2
import httpx
import numpy as np

MAX_CANDIDATES = 6
KEEP = 3
DOWNLOAD_TIMEOUT_S = 15.0
MAX_BYTES = 10 * 1024 * 1024
MIN_SHORT_SIDE = 500
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
AGENT_PRIOR = 0.2
SHOPIFY_PRIOR = 0.15
NAME_PRIOR = 0.1  # per point of file-name match


@dataclass
class Candidate:
    url: str
    source: str  # agent | shopify | img
    prior: float = 0.0


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
_SKIP = re.compile(r"(logo|icon|favicon|sprite|badge|flag|social|payment|placeholder)", re.I)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def _usable_url(url: str) -> bool:
    low = url.lower().split("?")[0]
    return url.startswith("http") and not low.endswith((".svg", ".gif", ".ico"))


def _link_url(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("url") or item.get("src") or item.get("href") or "")
    return str(item or "")


def rank_image_links(image_links: list[Any], base_url: str, product: str) -> list[Candidate]:
    """Page image URLs, best product-name match first (logos and icons dropped).

    Scores the file name: +1 per product word, -0.25 per other specific word, so
    `product_image_pepsi_zero_0.png` beats `pepsi_vanilla-cream_zero.png` for "Pepsi Zero".
    """
    want = _tokens(product)
    scored: list[tuple[float, int, str]] = []
    for i, item in enumerate(image_links):
        url = urljoin(base_url, _link_url(item))
        stem = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not _usable_url(url) or _SKIP.search(stem):
            continue
        words = _tokens(stem) - _GENERIC
        score = len(want & words) - 0.25 * len(words - want)
        scored.append((score, i, url))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [Candidate(url, "img", NAME_PRIOR * max(0.0, score)) for score, _, url in scored]


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


def parse_shopify_json(text: str) -> list[Candidate]:
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
            out.append(Candidate("https:" + src if src.startswith("//") else src, "shopify", prior))
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
    """BGR or BGRA array with a short side ≥ 500 px, else None."""
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None or arr.ndim not in (2, 3):
        return None
    if arr.dtype != np.uint8:
        arr = cv2.convertScaleAbs(arr, alpha=255.0 / max(1.0, float(arr.max())))
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if min(arr.shape[:2]) < MIN_SHORT_SIDE:
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
    """CLIP: packshot similarity minus max(lifestyle, logo), +0.1 for a cutout, + source prior.

    Without CLIP: cutouts first, then source prior, then resolution.
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
                    sims[0] - max(sims[1], sims[2]) + (0.1 if im.cutout else 0.0) + im.candidate.prior
                )
        return sorted(images, key=lambda im: -im.score)
    for im in images:
        im.score = (1.0 if im.cutout else 0.0) + im.candidate.prior
    return sorted(
        images,
        key=lambda im: (not im.cutout, -im.candidate.prior, -(im.size[0] * im.size[1])),
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
    """Agent URL, then Shopify JSON, then the page's image links; deduped, max 6.

    `fetch(urls, links=True, image_links=True)` is TinyFish Fetch. Its HTML output
    is cleaned text, so page images come from `image_links`, not from parsing tags.
    """
    found: list[Candidate] = []
    if agent_url and _usable_url(agent_url):
        found.append(Candidate(agent_url, "agent", AGENT_PRIOR))

    page = product_url if (product_url or "").startswith("http") else site
    page_cands: list[Candidate] = []
    try:
        result = fetch([page], links=True, image_links=True)
        pages = result.get("results") or []
        if pages:
            first = pages[0]
            base = str(first.get("final_url") or page)
            images = first.get("image_links") or []
            page_cands = rank_image_links(images, base, product)
            if is_shopify(images, str(first.get("text") or "")):
                handle = shopify_handle(first.get("links") or [], product_url or base, product)
                if handle:
                    origin = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
                    # "html" returns the JSON body as-is; "markdown" escapes `_` in keys.
                    js = fetch([f"{origin}/products/{handle}.json"], format="html")
                    js_pages = js.get("results") or []
                    if js_pages:
                        found += parse_shopify_json(str(js_pages[0].get("text") or ""))
    except Exception:
        pass
    found += page_cands

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
) -> list[dict[str, Any]]:
    """Save the top 3 as `candidate_<n>.png` in `out_dir`; return status entries, best first."""
    try:
        cands = collect_candidates(
            fetch, site=site, product=product, agent_url=agent_url, product_url=product_url
        )
        client = http or httpx.Client()
        kept: list[CleanImage] = []
        seen: set[str] = set()
        for c in cands:
            data = download_image(client, c.url, site)
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
            entries.append(
                {
                    "name": name,
                    "source": im.candidate.source,
                    "url": im.candidate.url,
                    "w": w,
                    "h": h,
                    "cutout": im.cutout,
                    "score": round(float(im.score), 4),
                }
            )
        return entries
    except Exception:
        return []
