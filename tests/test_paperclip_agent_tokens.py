"""Phase 3.4: per-agent lmproxy tokens + Floor activity pulses."""
import os
import warnings

import httpx
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
from services.paperclip.agent_tokens import AgentTokenRegistry

SHARED = "shared-token"


def _registry(tmp_path):
    return AgentTokenRegistry(path=str(tmp_path / "tokens.json"))


def test_registry_mint_lookup_rotate_and_list(tmp_path):
    reg = _registry(tmp_path)
    token = reg.mint("agent-1", "Coder")
    assert token.startswith("pa-")
    assert reg.lookup(token) == {"agent_id": "agent-1", "name": "Coder"}
    assert reg.lookup("nope") is None
    assert reg.lookup("") is None

    # Minting again rotates: the old token stops working.
    token2 = reg.mint("agent-1", "Coder v2")
    assert reg.lookup(token) is None
    assert reg.lookup(token2)["name"] == "Coder v2"

    listed = reg.list()
    assert listed == [{"agent_id": "agent-1", "name": "Coder v2", "token_suffix": token2[-6:]}]
    # Secrets never appear in list output.
    assert token2 not in str(listed)


def test_registry_persists_and_restricts_permissions(tmp_path):
    path = tmp_path / "tokens.json"
    token = AgentTokenRegistry(path=str(path)).mint("agent-9", "Scribe")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    # A fresh instance reads the same mapping back.
    assert AgentTokenRegistry(path=str(path)).lookup(token)["agent_id"] == "agent-9"


def _app(registry, published, pulse_interval=10.0):
    app = FastAPI()

    async def view(request):
        return StarJSON({"ok": True})

    upstream = Starlette(routes=[Route("/{path:path}", view, methods=["GET", "POST"])])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream),
                               base_url="http://warm")
    app.include_router(setup_lmproxy_routes(
        token_provider=lambda: SHARED,
        warm_url_provider=lambda: "http://warm",
        http_client=client,
        agent_lookup=registry.lookup,
        publish_activity=lambda evs: published.extend(evs) or len(evs),
        pulse_interval=pulse_interval,
    ))
    return app


def test_agent_token_authorizes_and_pulses_the_floor(tmp_path):
    reg = _registry(tmp_path)
    token = reg.mint("agent-1", "Coder")
    published = []
    with TestClient(_app(reg, published)) as c:
        r = c.post("/lmproxy/v1/chat/completions",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"model": "x", "messages": []})
        assert r.status_code == 200
        # A second immediate call is debounced — exactly one pulse.
        c.post("/lmproxy/v1/chat/completions",
               headers={"Authorization": f"Bearer {token}"},
               json={"model": "x", "messages": []})
    assert len(published) == 1
    assert published[0]["type"] == "heartbeat.run.event"
    assert published[0]["payload"] == {"agentId": "agent-1", "name": "Coder", "tool": "llm"}


def test_shared_token_still_works_without_pulsing(tmp_path):
    reg = _registry(tmp_path)
    published = []
    with TestClient(_app(reg, published)) as c:
        r = c.post("/lmproxy/v1/chat/completions",
                   headers={"Authorization": f"Bearer {SHARED}"},
                   json={"model": "x", "messages": []})
        assert r.status_code == 200
    assert published == []


def test_unknown_token_is_rejected(tmp_path):
    reg = _registry(tmp_path)
    published = []
    with TestClient(_app(reg, published)) as c:
        r = c.post("/lmproxy/v1/chat/completions",
                   headers={"Authorization": "Bearer pa-bogus"},
                   json={"model": "x", "messages": []})
        assert r.status_code == 401
    assert published == []
