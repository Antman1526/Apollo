import warnings

import httpx
import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.exceptions import StarletteDeprecationWarning
from starlette.responses import JSONResponse as StarJSON
from starlette.routing import Route

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=StarletteDeprecationWarning,
)

from starlette.testclient import TestClient

from routes.lmproxy_routes import setup_lmproxy_routes

TOKEN = "secret-token"


def _app(warm_url="http://warm", upstream_view=None):
    app = FastAPI()
    if upstream_view is not None:
        upstream = Starlette(routes=[Route("/{path:path}", upstream_view,
                             methods=["GET", "POST"])])
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream),
                                   base_url="http://warm")
    else:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    app.include_router(setup_lmproxy_routes(
        token_provider=lambda: TOKEN,
        warm_url_provider=lambda: warm_url,
        http_client=client,
    ))
    return app


def test_rejects_without_token():
    with TestClient(_app()) as c:
        r = c.post("/lmproxy/v1/chat/completions", json={"model": "x", "messages": []})
        assert r.status_code == 401


def test_rejects_wrong_token():
    with TestClient(_app()) as c:
        r = c.post("/lmproxy/v1/chat/completions",
                   headers={"Authorization": "Bearer nope"},
                   json={"model": "x", "messages": []})
        assert r.status_code == 401


def test_503_when_no_warm_model():
    with TestClient(_app(warm_url=None)) as c:
        r = c.post("/lmproxy/v1/chat/completions",
                   headers={"Authorization": f"Bearer {TOKEN}"},
                   json={"model": "x", "messages": []})
        assert r.status_code == 503


def test_forwards_chat_to_warm_llama_server():
    def view(request):
        assert request.url.path == "/v1/chat/completions"
        return StarJSON({"choices": [{"message": {"content": "hi"}}]})
    with TestClient(_app(upstream_view=view)) as c:
        r = c.post("/lmproxy/v1/chat/completions",
                   headers={"Authorization": f"Bearer {TOKEN}"},
                   json={"model": "openai/foo", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "hi"


def test_models_lists_warm_model():
    def view(request):
        # llama-server /v1/models
        return StarJSON({"object": "list", "data": [{"id": "local-model", "object": "model"}]})
    with TestClient(_app(upstream_view=view)) as c:
        r = c.get("/lmproxy/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert r.json()["data"][0]["id"] == "local-model"
