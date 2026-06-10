import asyncio
import warnings

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.exceptions import StarletteDeprecationWarning
from starlette.responses import JSONResponse as StarJSON, PlainTextResponse
from starlette.routing import Route

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient

from routes.paperclip_routes import setup_paperclip_routes
from services.paperclip.config import PaperclipConfig

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _cfg(enabled=True):
    return PaperclipConfig(
        enabled=enabled, mode="docker", url="http://upstream",
        browser_url="http://localhost:3100", port=3100,
        model_endpoint="ollama", model_base_url="http://x/v1", model_name="",
    )


def _app(cfg, upstream_view=None, ws_validate=None):
    """Mount the proxy. If an upstream_view is given, route the proxy's httpx
    client at a stub ASGI upstream so the real streaming path is exercised."""
    app = FastAPI()
    if upstream_view is not None:
        upstream = Starlette(routes=[Route("/{path:path}", upstream_view, methods=_METHODS)])
        transport = httpx.ASGITransport(app=upstream)
        client = httpx.AsyncClient(transport=transport, base_url="http://upstream")
    else:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    app.include_router(setup_paperclip_routes(cfg, http_client=client, ws_validate=ws_validate))
    return app


def test_status_reports_enabled():
    app = _app(_cfg())
    with TestClient(app) as c:
        r = c.get("/api/paperclip/status")
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert r.json()["url"] == "http://upstream"
        assert r.json()["browser_url"] == "http://localhost:3100"
        assert r.json()["browser_use"]["package"] == "browser-use"
        assert r.json()["agent_workbench"]["components"]["paperclip"]["state"] == "ready"


def test_proxy_forwards_get_and_returns_body():
    async def view(request):
        assert request.url.path == "/dashboard"
        return PlainTextResponse("<html>pc</html>", media_type="text/html")
    app = _app(_cfg(), view)
    with TestClient(app) as c:
        r = c.get("/paperclip/dashboard")
        assert r.status_code == 200
        assert "pc" in r.text
        assert r.headers["content-type"].startswith("text/html")


def test_proxy_forwards_post_body_and_status():
    async def view(request):
        assert request.method == "POST"
        assert await request.body() == b'{"a":1}'
        return StarJSON({"ok": True}, status_code=201)
    app = _app(_cfg(), view)
    with TestClient(app) as c:
        r = c.post("/paperclip/api/things", content=b'{"a":1}',
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 201
        assert r.json() == {"ok": True}


def test_proxy_disabled_returns_503():
    app = _app(_cfg(enabled=False))
    with TestClient(app) as c:
        assert c.get("/paperclip/anything").status_code == 503


def _stream_endpoint(app):
    """The live stream never ends, and TestClient buffers whole bodies, so the
    SSE tests drive the endpoint's generator directly."""
    for route in app.routes:
        if getattr(route, "path", "") == "/api/paperclip/stream":
            return route.endpoint
    raise AssertionError("stream route not found")


def test_stream_waits_for_events_when_enabled_but_idle():
    """An enabled-but-idle stream stays open with a waiting placeholder so the
    Floor can go live once events arrive, instead of closing and stranding the
    UI in preview mode."""
    app = _app(_cfg())
    endpoint = _stream_endpoint(app)

    async def run():
        resp = await endpoint()
        assert resp.media_type == "text/event-stream"
        gen = resp.body_iterator
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()

    first = asyncio.run(run())
    assert "paperclip.stream.waiting" in first


def test_stream_delivers_events_ingested_after_connect(monkeypatch):
    monkeypatch.setenv("PAPERCLIP_EVENTS_TOKEN", "tok")
    app = _app(_cfg())
    endpoint = _stream_endpoint(app)

    async def run():
        resp = await endpoint()
        gen = resp.body_iterator
        try:
            first = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert "paperclip.stream.waiting" in first
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
                r = await ac.post(
                    "/api/paperclip/events",
                    json={"events": [{
                        "type": "agent.status",
                        "payload": {"agentId": "a1", "status": "running"},
                    }]},
                    headers={"X-Paperclip-Events-Token": "tok"},
                )
                assert r.status_code == 200
                assert r.json() == {"accepted": 1, "rejected": 0}
            return await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()

    event_line = asyncio.run(run())
    assert "agent.status" in event_line
    assert '"a1"' in event_line


def test_stream_reports_disabled_when_paperclip_is_disabled():
    app = _app(_cfg(enabled=False))
    with TestClient(app) as c:
        r = c.get("/api/paperclip/stream")
        assert r.status_code == 200
        assert "paperclip.stream.unavailable" in r.text
        assert "disabled" in r.text


def test_ws_requires_auth():
    app = _app(_cfg(), ws_validate=lambda token: token == "good")
    with TestClient(app) as c:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/paperclip/socket"):
                pass  # no session cookie -> rejected with policy-violation close
