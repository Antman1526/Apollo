import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import JSONResponse as StarJSON, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from routes.paperclip_routes import setup_paperclip_routes
from services.paperclip.config import PaperclipConfig

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _cfg(enabled=True):
    return PaperclipConfig(
        enabled=enabled, mode="docker", url="http://upstream", port=3100,
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


def test_ws_requires_auth():
    app = _app(_cfg(), ws_validate=lambda token: token == "good")
    with TestClient(app) as c:
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/paperclip/socket"):
                pass  # no session cookie -> rejected with policy-violation close
