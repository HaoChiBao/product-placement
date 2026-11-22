"""Brand research via TinyFish: web search, page fetch, one Agent run → BrandProfile."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlparse

import httpx

from product_placement.brand import _CLASS_ALIASES
from product_placement.vision import COCO_CLASSES

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
AGENT_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"
RUN_URL = "https://agent.tinyfish.ai/v1/runs/{run_id}"

SEARCH_TIMEOUT_S = 10.0
FETCH_TIMEOUT_S = 150.0
# max_duration_seconds doesn't stop a run mid-step, so the client waits longer.
AGENT_MAX_DURATION_S = 150
AGENT_TIMEOUT_S = 240.0
POLL_INTERVAL_S = 4.0
CACHE_TTL_S = 7 * 24 * 3600

TERMINAL_RUN_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}

# Only words brand scoring understands (parse_brief / mapped_coco_labels).
VOCAB = sorted(set(_CLASS_ALIASES) | set(COCO_CLASSES))
_VOCAB_SET = set(VOCAB)

_STRING = {"type": "string"}
_PROFILE_PROPERTIES: dict[str, Any] = {
    "status": {"type": "string", "enum": ["ok", "blocked", "not_found"]},
    "reason": {"type": "string", "nullable": True},
    "productFacts": _STRING,
    "category": _STRING,
    "tone": _STRING,
    "audience": _STRING,
    "location": {"type": "string", "nullable": True},
    "colors": {"type": "array", "items": _STRING, "maxItems": 5},
    "goodScenes": {"type": "array", "items": _STRING, "maxItems": 6},
    "avoidScenes": {"type": "array", "items": _STRING, "maxItems": 6},
    "prefer": {
        "type": "array",
        "items": {"type": "string", "enum": VOCAB},
        "maxItems": 6,
    },
    "avoid": {
        "type": "array",
        "items": {"type": "string", "enum": VOCAB},
        "maxItems": 6,
    },
}
PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": _PROFILE_PROPERTIES,
    "propertyOrdering": list(_PROFILE_PROPERTIES),
    "required": list(_PROFILE_PROPERTIES),
}

_STATUS_MESSAGES = {
    401: "TinyFish rejected the API key (401). Check TINYFISH_API_KEY.",
    402: "TinyFish is out of credits (402). Top up the account and try again.",
    429: "TinyFish is rate limited or already running 2 agents (429). Wait a minute and try again.",
}


class ResearchError(RuntimeError):
    """A failure with a message that is safe to show the user."""


def _raise_for_status(resp: httpx.Response, what: str) -> None:
    if resp.status_code < 400:
        return
    if resp.status_code in _STATUS_MESSAGES:
        raise ResearchError(_STATUS_MESSAGES[resp.status_code])
    raise ResearchError(f"TinyFish {what} failed (HTTP {resp.status_code}).")


def iter_sse(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Yield the JSON payload of each `data:` event; skips comments and non-JSON.

    A complete JSON object on one data line is yielded right away (so a dropped
    connection loses nothing); multi-line data is joined at the blank separator.
    """
    data: list[str] = []

    def parse(raw: str) -> dict[str, Any] | None:
        try:
            event = json.loads(raw)
        except ValueError:
            return None
        return event if isinstance(event, dict) else None

    for line in lines:
        line = line.rstrip("\r")
        if not line:
            if data:
                event = parse("\n".join(data))
                data.clear()
                if event is not None:
                    yield event
            continue
        if not line.startswith("data:"):
            continue
        data.append(line[5:].lstrip())
        event = parse("\n".join(data))
        if event is not None:
            data.clear()
            yield event
    if data:
        event = parse("\n".join(data))
        if event is not None:
            yield event


def _run_result(status: str, result: Any, error: Any) -> dict[str, Any]:
    if status != "COMPLETED":
        detail = error.get("message") if isinstance(error, dict) else error
        raise ResearchError(
            f"TinyFish agent run {status.lower() or 'ended'}"
            + (f": {detail}" if detail else ".")
        )
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    if not isinstance(result, dict):
        raise ResearchError("TinyFish agent finished without a profile.")
    return result


class TinyFish:
    """Minimal TinyFish client. Pass `client` (e.g. an httpx.MockTransport client) in tests."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        timeout_s: float = AGENT_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get("TINYFISH_API_KEY", "")).strip()
        if not key:
            raise ResearchError("TINYFISH_API_KEY is not set.")
        self._headers = {"X-API-Key": key}
        self._client = client or httpx.Client()
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        self._sleep = sleep

    def search(self, query: str) -> dict[str, Any]:
        resp = self._client.get(
            SEARCH_URL,
            params={"query": query},
            headers=self._headers,
            timeout=SEARCH_TIMEOUT_S,
        )
        _raise_for_status(resp, "search")
        return resp.json()

    def fetch(self, urls: list[str]) -> dict[str, Any]:
        resp = self._client.post(
            FETCH_URL,
            json={"urls": urls[:10], "format": "markdown"},
            headers=self._headers,
            timeout=FETCH_TIMEOUT_S,
        )
        _raise_for_status(resp, "fetch")
        return resp.json()

    def agent_sse(
        self,
        url: str,
        goal: str,
        schema: dict[str, Any],
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run the Agent once and return its schema-shaped result.

        Streams SSE; if the stream drops after STARTED, polls the run instead.
        Never retries the (paid) run itself.
        """
        body = {
            "url": url,
            "goal": goal,
            "output_schema": schema,
            "browser_profile": "lite",
            "agent_config": {"max_duration_seconds": AGENT_MAX_DURATION_S},
        }
        deadline = time.monotonic() + self._timeout_s
        run_id = ""
        try:
            with self._client.stream(
                "POST",
                AGENT_URL,
                json=body,
                headers={**self._headers, "Accept": "text/event-stream"},
                timeout=httpx.Timeout(self._timeout_s, connect=15.0),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    _raise_for_status(resp, "agent run")
                for event in iter_sse(resp.iter_lines()):
                    if on_event:
                        on_event(event)
                    kind = event.get("type")
                    if kind == "STARTED":
                        run_id = str(event.get("run_id") or run_id)
                    elif kind == "COMPLETE":
                        return _run_result(
                            str(event.get("status") or ""),
                            event.get("result"),
                            event.get("error"),
                        )
                    if time.monotonic() > deadline:
                        break
        except httpx.TransportError as exc:
            if not run_id:
                raise ResearchError(
                    "Couldn't reach the TinyFish agent. Check the connection and try again."
                ) from exc
        if not run_id:
            raise ResearchError("The TinyFish agent stream ended before the run started.")
        return self._poll_run(run_id, deadline)

    def _poll_run(self, run_id: str, deadline: float) -> dict[str, Any]:
        while True:
            try:
                resp = self._client.get(
                    RUN_URL.format(run_id=run_id),
                    headers=self._headers,
                    timeout=30.0,
                )
                _raise_for_status(resp, "run lookup")
                run = resp.json()
                status = str(run.get("status") or "").upper()
                if status in TERMINAL_RUN_STATUSES:
                    return _run_result(status, run.get("result"), run.get("error"))
            except httpx.TransportError:
                pass  # transient; keep polling until the deadline
            if time.monotonic() + self._poll_interval_s > deadline:
                raise ResearchError(
                    f"The TinyFish agent timed out after {int(self._timeout_s)} s."
                )
            self._sleep(self._poll_interval_s)


def build_goal(business: str, product: str, note: str = "") -> str:
    lines = [
        f"Build a brand profile for {business}, focused on their product “{product}”.",
        "Return:",
        f"- productFacts: concrete facts about {product} (size, flavour, packaging, price if shown).",
        "- category, tone (brand voice), audience (who it targets), and location "
        "(headquarters, only if the site states it, else null).",
        "- colors: up to 5 brand colours as hex codes like #004B93.",
        "- goodScenes: short descriptions of scenes where this product naturally appears.",
        "- avoidScenes: scenes the brand would not want to appear in.",
        "- prefer: up to 6 everyday objects the product would naturally sit on or next to "
        "in a shot (for example cup, bottle, table, desk, counter, fridge).",
        "- avoid: only objects that clash with the brand or its avoidScenes (for example "
        "bed for a breakfast cereal, wine glass for a kids' drink). Leave it empty if "
        "nothing clashes; never fill it just to reach a count.",
        "- prefer and avoid may only use values the output schema allows.",
        "- status: ok when you could read the site. If a login wall, bot check or "
        "missing page stops you, set status to blocked or not_found and explain why "
        "in reason.",
    ]
    if note.strip():
        lines.append(f"Extra guidance from the brand, verbatim: {note.strip()}")
    return "\n".join(lines)


def host_of(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_site(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return f"https://{host}" if host else ""


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def pick_official_site(results: list[dict[str, Any]], business: str) -> str:
    """Prefer a result whose host contains the business name; else the first result."""
    urls = [str(r.get("url") or "") for r in results if r.get("url")]
    name = _compact(business)
    if name:
        for url in urls:
            if name in _compact(host_of(url)):
                return normalize_site(url)
    return normalize_site(urls[0]) if urls else ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(values: Any, limit: int) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        s = _text(v)
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out[:limit]


def _vocab_words(values: Any) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        word = _text(v).lower()
        if word in _VOCAB_SET and word not in out:
            out.append(word)
    return out


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def to_brand_profile(
    result: dict[str, Any], business: str, website: str, product: str
) -> dict[str, Any]:
    """Map an Agent result onto the web `BrandProfile` shape (see web/src/lib/research.ts)."""
    avoid = _vocab_words(result.get("avoid"))[:6]
    prefer = [w for w in _vocab_words(result.get("prefer")) if w not in avoid][:6]
    colors = [c for c in _strings(result.get("colors"), 10) if _HEX_RE.match(c)][:5]
    return {
        "business": business,
        "website": host_of(website),
        "product": product,
        "productFacts": _text(result.get("productFacts")),
        "location": _text(result.get("location")),
        "category": _text(result.get("category")),
        "tone": _text(result.get("tone")),
        "audience": _text(result.get("audience")),
        "colors": colors,
        "goodScenes": _strings(result.get("goodScenes"), 6),
        "avoidScenes": _strings(result.get("avoidScenes"), 6),
        "prefer": prefer,
        "avoid": avoid,
    }


def _data_root() -> Path:
    raw = (os.environ.get("DATA_DIR") or "").strip()
    return Path(raw) if raw else Path("runs") / "ui"


def cache_path(website: str, product: str) -> Path:
    key = f"{host_of(website)}|{(product or '').strip().lower()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _data_root() / "research_cache" / f"{digest}.json"


def read_cache(website: str, product: str, *, now: float | None = None) -> dict[str, Any] | None:
    path = cache_path(website, product)
    if not path.is_file():
        return None
    try:
        entry = json.loads(path.read_text())
    except ValueError:
        return None
    age = (now if now is not None else time.time()) - float(entry.get("saved_at") or 0)
    profile = entry.get("profile")
    if age < CACHE_TTL_S and isinstance(profile, dict):
        return profile
    return None


def write_cache(website: str, product: str, profile: dict[str, Any]) -> None:
    path = cache_path(website, product)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"saved_at": time.time(), "profile": profile}, indent=2) + "\n")
