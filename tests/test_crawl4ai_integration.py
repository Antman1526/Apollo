import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.research_routes import setup_research_routes
from services.research import crawl4ai_adapter


class _Markdown:
    fit_markdown = "# Fit markdown"
    raw_markdown = "# Raw markdown"


class _Result:
    success = True
    status_code = 200
    markdown = _Markdown()
    title = "Example"
    links = {"internal": [{"href": "https://example.com/a"}], "external": []}
    media = {"images": []}
    error_message = ""


class _FakeCrawler:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def arun(self, url, config=None):
        self.url = url
        return _Result()


def _request(user="alice"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def test_crawl4ai_blocks_private_urls():
    with pytest.raises(crawl4ai_adapter.Crawl4AIBlockedURL):
        crawl4ai_adapter.validate_public_crawl_url("http://127.0.0.1:7000")


def test_crawl4ai_adapter_extracts_markdown(monkeypatch):
    monkeypatch.setattr(crawl4ai_adapter, "validate_public_crawl_url", lambda url: url)

    result = asyncio.run(crawl4ai_adapter.crawl_url(
        "https://example.com",
        crawler_factory=lambda: _FakeCrawler(),
    ))

    assert result.success is True
    assert result.status_code == 200
    assert result.markdown == "# Fit markdown"
    assert result.links["internal"][0]["href"] == "https://example.com/a"


def test_research_crawl4ai_route_saves_owned_report(tmp_path, monkeypatch):
    data_root = tmp_path / "apollo-data"
    monkeypatch.setenv("APOLLO_DATA_DIR", str(data_root))

    async def fake_crawl_url(url, **kwargs):
        return crawl4ai_adapter.Crawl4AIExtract(
            url=url,
            success=True,
            status_code=200,
            markdown="# Imported",
            title="Imported title",
            links={},
            media={},
            error="",
        )

    monkeypatch.setattr(crawl4ai_adapter, "crawl_url", fake_crawl_url)
    router = setup_research_routes(SimpleNamespace(_active_tasks={}))
    target = _route(router, "/api/research/crawl4ai/crawl", "POST")

    # The model is scoped inside setup_research_routes, so build a compatible
    # object rather than importing it.
    body = SimpleNamespace(url="https://example.com", word_count_threshold=10, timeout_seconds=30, save=True)
    out = asyncio.run(target(body=body, request=_request("alice")))

    assert out["ok"] is True
    assert out["saved"] is True
    path = data_root / "deep_research" / f"{out['session_id']}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["owner"] == "alice"
    assert saved["category"] == "crawl4ai"
    assert saved["result"] == "# Imported"
