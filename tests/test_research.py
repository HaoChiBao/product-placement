"""TinyFish brand research: schema, SSE, polling fallback, mapping, cache, job status (no network)."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx
import pytest

from product_placement.brand import mapped_coco_labels
from product_placement.jobs import run_research_job
from product_placement.research import (
    PROFILE_SCHEMA,
    VOCAB,
    ResearchError,
    TinyFish,
    pick_official_site,
    to_brand_profile,
    write_cache,
    read_cache,
    cache_path,
)

ALLOWED_KEYWORDS = {
    "anyOf",
    "enum",
    "format",
    "items",
    "maxItems",
    "maximum",
    "minItems",
    "minimum",
    "nullable",
    "properties",
    "propertyOrdering",
    "required",
    "type",
}

BRAND_PROFILE_KEYS = {
    "business",
    "website",
    "product",
    "productFacts",
    "location",
    "category",
    "tone",
    "audience",
    "colors",
    "goodScenes",
    "avoidScenes",
    "prefer",
    "avoid",
}

OK_RESULT = {
    "status": "ok",
    "reason": None,
    "productFacts": "355 ml can, zero sugar",
    "category": "Soft drinks",
    "tone": "Bold, energetic",
    "audience": "18–34",
    "location": "Purchase, NY",
    "colors": ["#004B93", "#E32934", "not-a-colour"],
    "goodScenes": ["kitchen table", "desk at work"],
    "avoidScenes": ["bedroom"],
    "prefer": ["cup", "bottle", "dining table", "spaceship"],
    "avoid": ["bed", "bed"],
}


def sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def make_tf(handler: Callable[[httpx.Request], httpx.Response]) -> TinyFish:
    return TinyFish("test-key", client=make_client(handler), sleep=lambda _s: None)


def _walk_schema(node: Any, path: str = "$") -> None:
    assert isinstance(node, dict), path
    unknown = set(node) - ALLOWED_KEYWORDS
    assert not unknown, f"{path}: disallowed keywords {unknown}"
    assert not isinstance(node.get("type"), list), f"{path}: type arrays are rejected"
    if "enum" in node:
        assert node.get("type") == "string", f"{path}: enum needs type string"
    for name, child in (node.get("properties") or {}).items():
        _walk_schema(child, f"{path}.{name}")
    if "items" in node:
        _walk_schema(node["items"], f"{path}[]")
    for i, child in enumerate(node.get("anyOf") or []):
        _walk_schema(child, f"{path}|{i}")


def test_profile_schema_follows_tinyfish_rules():
    _walk_schema(PROFILE_SCHEMA)
    assert PROFILE_SCHEMA["type"] == "object"
    assert set(PROFILE_SCHEMA["required"]) == set(PROFILE_SCHEMA["properties"])
    assert len(json.dumps(PROFILE_SCHEMA).encode()) <= 64 * 1024
    assert PROFILE_SCHEMA["properties"]["prefer"]["items"]["enum"] == VOCAB


def test_sse_progress_reaches_callback_and_complete_returns_result():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-API-Key"] == "test-key"
        body = json.loads(req.content)
        assert body["browser_profile"] == "lite"
        assert body["output_schema"] == PROFILE_SCHEMA
        return httpx.Response(
            200,
            content=sse(
                {"type": "STARTED", "run_id": "run_1"},
                {"type": "PROGRESS", "purpose": "Reading the product page"},
                {"type": "HEARTBEAT"},
                {"type": "COMPLETE", "status": "COMPLETED", "result": OK_RESULT},
            ),
        )

    seen: list[dict[str, Any]] = []
    result = make_tf(handler).agent_sse("https://pepsi.com", "goal", PROFILE_SCHEMA, seen.append)
    assert result == OK_RESULT
    assert {"type": "PROGRESS", "purpose": "Reading the product page"} in seen


def test_sse_complete_failed_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                {"type": "STARTED", "run_id": "run_1"},
                {"type": "COMPLETE", "status": "FAILED", "error": {"message": "browser crashed"}},
            ),
        )

    with pytest.raises(ResearchError, match="browser crashed"):
        make_tf(handler).agent_sse("https://pepsi.com", "goal", PROFILE_SCHEMA)


class _DroppedStream(httpx.SyncByteStream):
    def __iter__(self):
        yield sse({"type": "STARTED", "run_id": "run_9"})
        raise httpx.RemoteProtocolError("peer closed connection")


def test_dropped_stream_falls_back_to_polling_the_run():
    polls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, stream=_DroppedStream())
        assert req.url.path == "/v1/runs/run_9"
        polls.append(req.url.path)
        if len(polls) < 2:
            return httpx.Response(200, json={"status": "RUNNING"})
        return httpx.Response(200, json={"status": "COMPLETED", "result": OK_RESULT, "error": None})

    result = make_tf(handler).agent_sse("https://pepsi.com", "goal", PROFILE_SCHEMA)
    assert result == OK_RESULT
    assert len(polls) == 2


@pytest.mark.parametrize(
    ("code", "needle"),
    [(401, "API key"), (402, "credits"), (429, "rate limited")],
)
def test_http_errors_map_to_clear_messages(code: int, needle: str):
    tf = make_tf(lambda req: httpx.Response(code, json={"error": "nope"}))
    with pytest.raises(ResearchError, match=needle):
        tf.agent_sse("https://pepsi.com", "goal", PROFILE_SCHEMA)


def test_to_brand_profile_drops_unknown_words_and_keeps_keys():
    profile = to_brand_profile(OK_RESULT, "Pepsi", "https://www.pepsi.com", "Pepsi Zero Sugar")
    assert set(profile) == BRAND_PROFILE_KEYS
    assert profile["website"] == "pepsi.com"
    assert profile["prefer"] == ["cup", "bottle", "dining table"]
    assert profile["avoid"] == ["bed"]
    assert profile["colors"] == ["#004B93", "#E32934"]
    assert mapped_coco_labels(profile["prefer"]) == {"cup", "bottle", "dining table"}
    assert mapped_coco_labels(profile["avoid"]) == {"bed"}


def test_pick_official_site_prefers_matching_host():
    results = [
        {"url": "https://en.wikipedia.org/wiki/Pepsi"},
        {"url": "https://www.pepsi.com/en-us/zero-sugar"},
    ]
    assert pick_official_site(results, "Pepsi") == "https://www.pepsi.com"
    assert pick_official_site(results, "Unrelated Co") == "https://en.wikipedia.org"
    assert pick_official_site([], "Pepsi") == ""


def _tinyfish_handler(agent_events: list[dict[str, Any]], calls: list[str]):
    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        calls.append(host)
        if host == "api.search.tinyfish.ai":
            return httpx.Response(
                200,
                json={
                    "query": req.url.params["query"],
                    "results": [{"position": 1, "url": "https://www.pepsi.com/", "title": "Pepsi"}],
                },
            )
        if host == "api.fetch.tinyfish.ai":
            return httpx.Response(
                200,
                json={
                    "results": [{"url": "https://pepsi.com", "final_url": "https://www.pepsi.com/", "text": "hi"}],
                    "errors": [{"url": "https://pepsi.com/about", "error": "404"}],
                },
            )
        if host == "agent.tinyfish.ai":
            return httpx.Response(200, content=sse(*agent_events))
        raise AssertionError(f"unexpected request {req.url}")

    return handler


def _status(tmp_path, job_id: str) -> dict[str, Any]:
    return json.loads((tmp_path / "jobs" / job_id / "status.json").read_text())


def test_job_writes_status_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls: list[str] = []
    events = [
        {"type": "STARTED", "run_id": "run_42"},
        {"type": "STREAMING_URL", "streaming_url": "https://stream.example/run_42"},
        {"type": "PROGRESS", "purpose": "Opening pepsi.com"},
        {"type": "COMPLETE", "status": "COMPLETED", "result": OK_RESULT},
    ]
    client = make_client(_tinyfish_handler(events, calls))
    tf = TinyFish("test-key", client=client)
    run_research_job(
        "job1", business="Pepsi", website="", product="Pepsi Zero Sugar", note="", tinyfish=tf
    )
    st = _status(tmp_path, "job1")
    assert st["type"] == "research"
    assert st["status"] == "completed"
    assert st["step"] == 4
    assert [s["label"] for s in st["steps"]] == [
        "Searching the web",
        "Fetching your website",
        "Reading your pages",
        "Building brand profile",
    ]
    assert st["steps"][1]["detail"] == "pepsi.com · 1 page"
    assert st["steps"][2]["detail"] == "No usable product image"
    assert st["agent_steps"] == 1
    assert [s["kind"] for s in st["sources"]] == ["search", "fetch"]
    assert all(isinstance(s["ms"], int) for s in st["sources"])
    assert st["tinyfish_run_id"] == "run_42"
    assert st["streaming_url"] == "https://stream.example/run_42"
    assert st["cached"] is False
    assert st["error"] is None
    assert st["images"] == []
    assert set(st["profile"]) == BRAND_PROFILE_KEYS
    assert calls.count("agent.tinyfish.ai") == 1


def test_blocked_result_fails_job_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    blocked = {**OK_RESULT, "status": "blocked", "reason": "Login wall on every page"}
    events = [
        {"type": "STARTED", "run_id": "run_1"},
        {"type": "COMPLETE", "status": "COMPLETED", "result": blocked, "error": None},
    ]
    tf = TinyFish("test-key", client=make_client(_tinyfish_handler(events, [])))
    run_research_job(
        "job2", business="Pepsi", website="pepsi.com", product="Zero", tinyfish=tf
    )
    st = _status(tmp_path, "job2")
    assert st["status"] == "failed"
    assert "Login wall on every page" in st["error"]
    assert st["profile"] is None


def test_cache_hit_skips_the_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cached = to_brand_profile(OK_RESULT, "Pepsi", "pepsi.com", "Pepsi Zero Sugar")
    write_cache("https://www.pepsi.com", "pepsi zero sugar", cached)
    calls: list[str] = []
    tf = TinyFish("test-key", client=make_client(_tinyfish_handler([], calls)))
    run_research_job(
        "job3", business="Pepsi", website="pepsi.com", product="Pepsi Zero Sugar", tinyfish=tf
    )
    st = _status(tmp_path, "job3")
    assert st["status"] == "completed"
    assert st["cached"] is True
    assert st["profile"] == cached
    assert "agent.tinyfish.ai" not in calls


def test_missing_key_fails_job_without_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    run_research_job("job4", business="Pepsi", website="pepsi.com", product="Zero")
    st = _status(tmp_path, "job4")
    assert st["status"] == "failed"
    assert "TINYFISH_API_KEY" in st["error"]


def test_to_brand_profile_splits_leaked_json_in_scene_lists():
    # Real Agent output (Liquid Death run): JSON leaked into a string item.
    garbled = {
        **OK_RESULT,
        "goodScenes": [
            'Skate park hangout","Road trip adventure","Backyard BBQ with friends"],',
            "avoidScenes:[",
        ],
        "avoidScenes": ["Kids' birthday party"],
    }
    profile = to_brand_profile(garbled, "Liquid Death", "liquiddeath.com", "Mountain Water")
    assert profile["goodScenes"] == [
        "Skate park hangout",
        "Road trip adventure",
        "Backyard BBQ with friends",
    ]
    assert profile["avoidScenes"] == ["Kids' birthday party"]


def test_cache_without_image_url_is_stale(tmp_path, monkeypatch):
    # Saved before the agent was asked for an image: re-run so it asks.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    profile = to_brand_profile(OK_RESULT, "Pepsi", "pepsi.ca", "pepsi bottle")
    path = cache_path("pepsi.ca", "pepsi bottle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"saved_at": time.time(), "profile": profile}))
    assert read_cache("pepsi.ca", "pepsi bottle") is None
    write_cache("pepsi.ca", "pepsi bottle", profile, image_url=None)
    assert read_cache("pepsi.ca", "pepsi bottle")["profile"] == profile
