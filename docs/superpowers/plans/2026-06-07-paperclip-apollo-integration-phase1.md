# Paperclip ⨉ Apollo Integration — Phase 1 (Docker) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle Paperclip v2026.529.0 inside Apollo for Docker deployments — reachable as a `/paperclip/*` iframe tab behind Apollo's auth, with its `opencode-local` agents driven by a configurable local model endpoint (default Ollama).

**Architecture:** Add `paperclip` + `paperclip-db` (Postgres) services to `docker-compose.yml`. Apollo gains a reverse-proxy route (`/paperclip/*`, HTTP + websocket) that forwards to the Paperclip container, a small `services/paperclip/` config/helper module, a Settings panel + nav iframe tab, and license attribution. Native macOS (process + Homebrew Postgres lifecycle) is **out of scope** — that is Phase 2.

**Tech Stack:** Python 3.11+ / FastAPI / Starlette / httpx / `websockets`; Docker Compose; Paperclip (Node + Postgres); vanilla JS static UI; pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-07-paperclip-apollo-integration-design.md`

---

## Reference facts (verified against `paperclipai/paperclip@v2026.529.0`)

- Paperclip server listens on `PORT` (default `3100`), `HOST` configurable, `SERVE_UI=true` serves the React UI from the same server.
- Requires Postgres: `DATABASE_URL=postgres://paperclip:paperclip@db:5432/paperclip`. Reference compose uses `postgres:17-alpine` with a `pg_isready` healthcheck.
- Auth: better-auth, `BETTER_AUTH_SECRET` required; deployment env `PAPERCLIP_DEPLOYMENT_MODE=authenticated`, `PAPERCLIP_DEPLOYMENT_EXPOSURE=private`, plus `PAPERCLIP_PUBLIC_URL`. Fresh private deployments use a one-time browser first-admin claim.
- Data dir: `PAPERCLIP_HOME` (volume).
- "Models" are agent-runtime adapters. `opencode-local` (`type = "opencode_local"`) accepts model ids in `provider/model` form; OpenCode performs provider routing. Apollo's own Agent is opencode-based.
- Apollo enforces auth via a global `AuthMiddleware` (`app.py`) with `AUTH_EXEMPT_PREFIXES = ["/static"]`; **websockets bypass `BaseHTTPMiddleware`** and must be authenticated explicitly.
- Apollo routes use a `setup_<name>_routes(...) -> APIRouter` factory registered with `app.include_router(...)`. Tests live flat in `tests/test_*.py` and stub `core.database` to avoid SQLAlchemy import side-effects.

---

## File structure

| File | Responsibility |
| --- | --- |
| `services/paperclip/__init__.py` | Package marker. |
| `services/paperclip/config.py` | Pure config resolution: deployment mode, upstream URL/port, model-endpoint resolution (`ollama`/`apollo`/`custom` → base_url + model id), auth-secret read/generate. No I/O beyond env + secret file. |
| `services/paperclip/proxy.py` | Pure helpers: build upstream URL from a subpath + query, filter hop-by-hop headers for request and response. Unit-testable, no network. |
| `routes/paperclip_routes.py` | `setup_paperclip_routes(...)` — `/paperclip/{path}` HTTP reverse proxy, `/paperclip` websocket proxy (with explicit cookie auth), and `/api/paperclip/status`. |
| `tests/test_paperclip_config.py` | Tests for `config.py`. |
| `tests/test_paperclip_proxy.py` | Tests for `proxy.py` helpers. |
| `tests/test_paperclip_routes.py` | Integration tests for the proxy + status route against a stub upstream. |
| `docker-compose.yml` (modify) | Add `paperclip-db` + `paperclip` services; pass `PAPERCLIP_URL` and model env to `apollo`. |
| `.env.example` (modify) | New `PAPERCLIP_*` variables. |
| `static/` (modify) | Nav "Paperclip" iframe tab + Settings panel (enable, endpoint selector, status). |
| `ACKNOWLEDGMENTS.md`, `licenses/paperclip-LICENSE` (modify/create) | MIT attribution. |
| `README.md` (modify) | Document the Paperclip feature + first-admin claim. |
| `docs/superpowers/plans/2026-06-07-...-phase1.md` (this file, append) | Spike S1/S3 decision record. |

---

## Task 1: Spike — pin subpath behavior (S1) and opencode local-model wiring (S3)

This is research that produces a written decision record consumed by Tasks 4, 5, and 8. No production code. Run Paperclip locally once to observe behavior.

**Files:**
- Modify (append decision record): `docs/superpowers/plans/2026-06-07-paperclip-apollo-integration-phase1.md`

- [ ] **Step 1: Boot Paperclip with Postgres locally**

```bash
cd /tmp && rm -rf pc-spike && mkdir pc-spike && cd pc-spike
git clone --depth 1 --branch v2026.529.0 https://github.com/paperclipai/paperclip.git
cd paperclip
export BETTER_AUTH_SECRET="$(openssl rand -hex 32)"
export PAPERCLIP_PUBLIC_URL="http://localhost:3100/paperclip"
docker compose -f docker/docker-compose.yml up -d --build
```

Expected: `db` and `server` become healthy; `curl -sI http://localhost:3100/` returns 200/302.

- [ ] **Step 2: Probe subpath behavior (S1)**

Determine whether the UI + assets work when the public URL has a `/paperclip` path prefix.

```bash
curl -s http://localhost:3100/ | grep -Eo '(src|href)="[^"]+"' | head -20
curl -sI http://localhost:3100/assets/ 2>/dev/null | head -3
```

Record one of:
- **A. Native base path supported** — asset URLs are relative or already include the public-URL prefix → reverse proxy can strip-prefix only. (Preferred.)
- **B. Absolute root paths** — assets reference `/assets/...` at the root → the proxy must ALSO proxy the root asset paths Paperclip emits, or rewrite HTML. Note exactly which root prefixes appear (`/assets`, `/api`, `/socket`, etc.).

- [ ] **Step 3: Probe the realtime transport (for Task 5)**

```bash
curl -s http://localhost:3100/ | grep -Eoi '(ws|wss|socket\.io|/socket|EventSource|/api/.*stream)' | sort -u
docker compose -f docker/docker-compose.yml logs server | grep -iE 'listen|websocket|socket|sse|upgrade' | head
```

Record the realtime mechanism (websocket path(s) and/or SSE endpoints).

- [ ] **Step 4: Probe opencode local-model wiring (S3)**

Inspect how the `opencode-local` adapter selects a provider base URL, and whether OpenCode honors `OPENAI_BASE_URL` for the `openai/<model>` provider.

```bash
sed -n '1,200p' packages/adapters/opencode-local/src/server/runtime-config.ts
sed -n '1,120p' packages/adapters/opencode-local/src/server/models.ts
grep -rniE 'OPENAI_BASE_URL|baseURL|base_url|provider|OPENCODE_CONFIG|opencode\.json|process\.env' packages/adapters/opencode-local/src | head -40
```

Record the concrete mechanism to point opencode at a local OpenAI-compatible endpoint. Expected default to confirm: OpenCode's `openai` provider honors `OPENAI_BASE_URL` (+ a dummy `OPENAI_API_KEY`), and an agent uses model id `openai/<name>`. Note the actual env var names / config-file path discovered.

- [ ] **Step 5: Tear down and write the decision record**

```bash
docker compose -f docker/docker-compose.yml down -v
```

Append a `## Spike decision record (Task 1)` section to this plan file with: S1 outcome (A or B + exact root prefixes if B), the realtime transport, and the S3 wiring (exact env vars / config path + model-id format). Tasks 4/5/8 reference this section.

- [ ] **Step 6: Commit**

```bash
git -C /Users/Antman/Apollo add docs/superpowers/plans/2026-06-07-paperclip-apollo-integration-phase1.md
git -C /Users/Antman/Apollo commit -m "docs(paperclip): record S1/S3 spike findings for integration"
```

---

## Task 2: `services/paperclip/config.py` — config resolution

**Files:**
- Create: `services/paperclip/__init__.py`
- Create: `services/paperclip/config.py`
- Test: `tests/test_paperclip_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paperclip_config.py
import importlib

import pytest


def _fresh(monkeypatch, env):
    for k in list(env):
        monkeypatch.setenv(k, env[k])
    import services.paperclip.config as cfg
    importlib.reload(cfg)
    return cfg


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PAPERCLIP_ENABLED", raising=False)
    cfg = _fresh(monkeypatch, {})
    c = cfg.load_config()
    assert c.enabled is False


def test_docker_defaults(monkeypatch):
    cfg = _fresh(monkeypatch, {"PAPERCLIP_ENABLED": "true"})
    c = cfg.load_config()
    assert c.enabled is True
    assert c.mode == "docker"
    assert c.url == "http://paperclip:3100"
    assert c.port == 3100


def test_model_endpoint_ollama_default(monkeypatch):
    cfg = _fresh(monkeypatch, {"PAPERCLIP_ENABLED": "true"})
    c = cfg.load_config()
    assert c.model_endpoint == "ollama"
    # In Docker, Ollama on the host is reached via host.docker.internal.
    assert c.model_base_url == "http://host.docker.internal:11434/v1"


def test_model_endpoint_custom_overrides(monkeypatch):
    cfg = _fresh(monkeypatch, {
        "PAPERCLIP_ENABLED": "true",
        "PAPERCLIP_MODEL_ENDPOINT": "custom",
        "PAPERCLIP_MODEL_BASE_URL": "http://example:9000/v1",
        "PAPERCLIP_MODEL_NAME": "openai/my-model",
    })
    c = cfg.load_config()
    assert c.model_endpoint == "custom"
    assert c.model_base_url == "http://example:9000/v1"
    assert c.model_name == "openai/my-model"


def test_auth_secret_generated_and_persisted(tmp_path, monkeypatch):
    secret_file = tmp_path / "paperclip_secret"
    cfg = _fresh(monkeypatch, {
        "PAPERCLIP_ENABLED": "true",
        "PAPERCLIP_SECRET_FILE": str(secret_file),
    })
    monkeypatch.delenv("PAPERCLIP_AUTH_SECRET", raising=False)
    s1 = cfg.resolve_auth_secret()
    s2 = cfg.resolve_auth_secret()
    assert s1 and len(s1) >= 32
    assert s1 == s2  # persisted, stable across calls
    assert secret_file.read_text().strip() == s1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paperclip_config.py -v`
Expected: FAIL — `ModuleNotFoundError: services.paperclip.config`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/paperclip/__init__.py
"""Paperclip sidecar integration (config, reverse-proxy helpers)."""
```

```python
# services/paperclip/config.py
"""Resolve Paperclip integration settings from environment + a secret file.

Pure-ish: only touches env and an on-disk secret file. No network, no DB.
Mirrors the env-driven style of services/localmodels/config.py.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}

# Default model endpoints. In Docker the Apollo/Paperclip containers reach an
# Ollama running on the host via host.docker.internal (Mac/Windows; Linux gets
# an extra_hosts mapping in docker-compose.yml).
_OLLAMA_DOCKER = "http://host.docker.internal:11434/v1"


@dataclass(frozen=True)
class PaperclipConfig:
    enabled: bool
    mode: str            # docker | native | external | off
    url: str             # upstream base, no trailing slash
    port: int
    model_endpoint: str  # ollama | apollo | custom
    model_base_url: str
    model_name: str


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _resolve_model(endpoint: str) -> tuple[str, str]:
    """Return (base_url, model_name) for the selected endpoint."""
    if endpoint == "custom":
        return (
            os.getenv("PAPERCLIP_MODEL_BASE_URL", ""),
            os.getenv("PAPERCLIP_MODEL_NAME", ""),
        )
    if endpoint == "apollo":
        # Phase 3 adds the Apollo /v1 proxy; default to the in-cluster apollo host.
        return (
            os.getenv("PAPERCLIP_MODEL_BASE_URL", "http://apollo:7000/v1"),
            os.getenv("PAPERCLIP_MODEL_NAME", ""),
        )
    # ollama (default)
    return (
        os.getenv("PAPERCLIP_MODEL_BASE_URL", _OLLAMA_DOCKER),
        os.getenv("PAPERCLIP_MODEL_NAME", ""),
    )


def load_config() -> PaperclipConfig:
    enabled = _bool("PAPERCLIP_ENABLED", False)
    mode = os.getenv("PAPERCLIP_MODE", "docker").strip().lower()
    port = int(os.getenv("PAPERCLIP_PORT", "3100"))
    url = os.getenv("PAPERCLIP_URL", f"http://paperclip:{port}").rstrip("/")
    endpoint = os.getenv("PAPERCLIP_MODEL_ENDPOINT", "ollama").strip().lower()
    base_url, model_name = _resolve_model(endpoint)
    return PaperclipConfig(
        enabled=enabled, mode=mode, url=url, port=port,
        model_endpoint=endpoint, model_base_url=base_url, model_name=model_name,
    )


def resolve_auth_secret() -> str:
    """Return a stable BETTER_AUTH_SECRET, generating + persisting one if unset."""
    env = os.getenv("PAPERCLIP_AUTH_SECRET")
    if env:
        return env
    path = os.getenv("PAPERCLIP_SECRET_FILE", os.path.expanduser("~/.apollo/paperclip_secret"))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read().strip()
            if existing:
                return existing
    except FileNotFoundError:
        pass
    secret = secrets.token_hex(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paperclip_config.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/paperclip/__init__.py services/paperclip/config.py tests/test_paperclip_config.py
git commit -m "feat(paperclip): config resolution for sidecar + model endpoint"
```

---

## Task 3: `services/paperclip/proxy.py` — pure reverse-proxy helpers

**Files:**
- Create: `services/paperclip/proxy.py`
- Test: `tests/test_paperclip_proxy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paperclip_proxy.py
from services.paperclip.proxy import build_upstream_url, filter_request_headers, filter_response_headers


def test_build_upstream_url_joins_subpath_and_query():
    url = build_upstream_url("http://paperclip:3100", "assets/app.js", "v=2")
    assert url == "http://paperclip:3100/assets/app.js?v=2"


def test_build_upstream_url_root():
    assert build_upstream_url("http://paperclip:3100", "", "") == "http://paperclip:3100/"


def test_build_upstream_url_strips_leading_slash_on_subpath():
    assert build_upstream_url("http://paperclip:3100", "/api/x", "") == "http://paperclip:3100/api/x"


def test_filter_request_headers_drops_hop_by_hop_and_host():
    src = {"Host": "apollo", "Connection": "keep-alive", "Cookie": "a=1", "X-Real": "y"}
    out = filter_request_headers(src)
    assert "host" not in {k.lower() for k in out}
    assert "connection" not in {k.lower() for k in out}
    assert out["Cookie"] == "a=1"
    assert out["X-Real"] == "y"


def test_filter_response_headers_drops_hop_by_hop_and_encoding():
    src = {"Transfer-Encoding": "chunked", "Content-Length": "10", "Content-Type": "text/html"}
    out = filter_response_headers(src)
    keys = {k.lower() for k in out}
    assert "transfer-encoding" not in keys
    assert "content-length" not in keys  # re-derived by the response layer
    assert out["Content-Type"] == "text/html"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paperclip_proxy.py -v`
Expected: FAIL — `ModuleNotFoundError: services.paperclip.proxy`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/paperclip/proxy.py
"""Pure helpers for the Paperclip reverse proxy: URL + header hygiene."""
from __future__ import annotations

from typing import Dict, Mapping

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
_DROP_REQUEST = _HOP_BY_HOP | {"host"}
# Let the response layer recompute framing/length from the streamed body.
_DROP_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding"}


def build_upstream_url(base: str, subpath: str, query: str) -> str:
    base = base.rstrip("/")
    path = subpath.lstrip("/")
    url = f"{base}/{path}"
    if query:
        url = f"{url}?{query}"
    return url


def filter_request_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _DROP_REQUEST}


def filter_response_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _DROP_RESPONSE}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paperclip_proxy.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/paperclip/proxy.py tests/test_paperclip_proxy.py
git commit -m "feat(paperclip): pure reverse-proxy url/header helpers"
```

---

## Task 4: `routes/paperclip_routes.py` — HTTP reverse proxy + status

**Files:**
- Create: `routes/paperclip_routes.py`
- Test: `tests/test_paperclip_routes.py`
- Modify: `app.py` (register the router)

> If Task 1 recorded S1 outcome **B** (absolute root asset paths), this route must also catch the recorded root prefixes (e.g. add a second mount for `/assets/{path}`). The code below covers the `/paperclip/{path}` case; add the extra mounts only if the spike requires them.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paperclip_routes.py
import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from routes.paperclip_routes import setup_paperclip_routes
from services.paperclip.config import PaperclipConfig


def _cfg(enabled=True):
    return PaperclipConfig(
        enabled=enabled, mode="docker", url="http://upstream", port=3100,
        model_endpoint="ollama", model_base_url="http://x/v1", model_name="",
    )


def _app_with_stub(cfg, stub_handler):
    """Mount the proxy with an httpx client whose transport is an in-memory stub."""
    app = FastAPI()
    transport = httpx.MockTransport(stub_handler)
    client = httpx.AsyncClient(transport=transport)
    app.include_router(setup_paperclip_routes(cfg, http_client=client))
    return app


def test_status_reports_enabled():
    app = _app_with_stub(_cfg(), lambda req: httpx.Response(200))
    with TestClient(app) as c:
        r = c.get("/api/paperclip/status")
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert r.json()["url"] == "http://upstream"


def test_proxy_forwards_get_and_returns_body():
    def handler(request):
        assert request.url.path == "/dashboard"
        return httpx.Response(200, text="<html>pc</html>", headers={"Content-Type": "text/html"})
    app = _app_with_stub(_cfg(), handler)
    with TestClient(app) as c:
        r = c.get("/paperclip/dashboard")
        assert r.status_code == 200
        assert "pc" in r.text
        assert r.headers["content-type"].startswith("text/html")


def test_proxy_forwards_post_body_and_status():
    def handler(request):
        assert request.method == "POST"
        assert request.content == b'{"a":1}'
        return httpx.Response(201, json={"ok": True})
    app = _app_with_stub(_cfg(), handler)
    with TestClient(app) as c:
        r = c.post("/paperclip/api/things", content=b'{"a":1}',
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 201
        assert r.json() == {"ok": True}


def test_proxy_disabled_returns_503():
    app = _app_with_stub(_cfg(enabled=False), lambda req: httpx.Response(200))
    with TestClient(app) as c:
        assert c.get("/paperclip/anything").status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paperclip_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: routes.paperclip_routes`.

- [ ] **Step 3: Write minimal implementation**

```python
# routes/paperclip_routes.py
"""Reverse proxy + status for the bundled Paperclip sidecar.

Mounted at /paperclip/* (HTTP) and /paperclip (websocket). The global
AuthMiddleware in app.py already gates /paperclip/* for HTTP. Websockets bypass
BaseHTTPMiddleware, so the websocket handler authenticates the session cookie
itself (wired in Task 5).
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.paperclip.config import PaperclipConfig
from services.paperclip.proxy import (
    build_upstream_url, filter_request_headers, filter_response_headers,
)

logger = logging.getLogger(__name__)


def setup_paperclip_routes(cfg: PaperclipConfig, http_client: httpx.AsyncClient | None = None) -> APIRouter:
    router = APIRouter(tags=["paperclip"])
    client = http_client or httpx.AsyncClient(timeout=None)

    @router.get("/api/paperclip/status")
    async def status():
        return {"enabled": cfg.enabled, "mode": cfg.mode, "url": cfg.url,
                "model_endpoint": cfg.model_endpoint}

    @router.api_route(
        "/paperclip/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy(path: str, request: Request):
        if not cfg.enabled:
            return JSONResponse({"detail": "Paperclip is disabled"}, status_code=503)
        url = build_upstream_url(cfg.url, path, request.url.query)
        headers = filter_request_headers(request.headers)
        body = await request.body()
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.ConnectError:
            return JSONResponse({"detail": "Paperclip is not reachable"}, status_code=502)
        resp_headers = filter_response_headers(upstream.headers)
        if request.method == "HEAD":
            await upstream.aclose()
            return Response(status_code=upstream.status_code, headers=resp_headers)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=resp_headers,
            background=_Closer(upstream),
        )

    return router


class _Closer:
    """Starlette BackgroundTask-compatible callable that closes the upstream stream."""
    def __init__(self, response: httpx.Response):
        self._response = response

    async def __call__(self):
        await self._response.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paperclip_routes.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Register the router in `app.py`**

Add near the other `app.include_router(...)` calls (after the auth router so auth middleware applies):

```python
# Paperclip sidecar reverse proxy (bundled agent-management UI).
from routes.paperclip_routes import setup_paperclip_routes
from services.paperclip.config import load_config as _load_paperclip_config
_paperclip_cfg = _load_paperclip_config()
app.include_router(setup_paperclip_routes(_paperclip_cfg))
```

- [ ] **Step 6: Verify the app imports and route is present**

Run: `python -c "import app; print([r.path for r in app.app.routes if 'paperclip' in r.path])"`
Expected: prints `['/api/paperclip/status', '/paperclip/{path}']` (order may vary).

- [ ] **Step 7: Commit**

```bash
git add routes/paperclip_routes.py tests/test_paperclip_routes.py app.py
git commit -m "feat(paperclip): HTTP reverse proxy + status route"
```

---

## Task 5: Websocket proxy with explicit cookie auth

**Files:**
- Modify: `routes/paperclip_routes.py`
- Test: `tests/test_paperclip_routes.py`
- Modify: `requirements.txt` (ensure `websockets`)

> Implement only if Task 1 recorded a websocket transport. If Paperclip uses SSE only, SSE rides the HTTP proxy from Task 4 (it streams) — skip the WS handler and note that in the commit.

- [ ] **Step 1: Ensure the websockets client lib is available**

Add to `requirements.txt` (httpx pulls it in transitively for some setups, but make it explicit):

```
websockets
```

Run: `./venv/bin/pip install websockets`
Expected: installed / already satisfied.

- [ ] **Step 2: Write the failing test (auth rejection without cookie)**

```python
# append to tests/test_paperclip_routes.py
def test_ws_requires_auth(monkeypatch):
    from routes.paperclip_routes import setup_paperclip_routes
    app = FastAPI()
    cfg = _cfg()

    def _validate(token):
        return token == "good"

    app.include_router(setup_paperclip_routes(cfg, ws_validate=_validate))
    with TestClient(app) as c:
        import pytest as _pytest
        from starlette.websockets import WebSocketDisconnect
        with _pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/paperclip/socket"):
                pass  # no session cookie -> rejected with policy-violation close
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_paperclip_routes.py::test_ws_requires_auth -v`
Expected: FAIL — `setup_paperclip_routes() got an unexpected keyword argument 'ws_validate'`.

- [ ] **Step 4: Add the websocket handler**

Extend `setup_paperclip_routes` signature and body:

```python
# signature
def setup_paperclip_routes(
    cfg: PaperclipConfig,
    http_client: httpx.AsyncClient | None = None,
    ws_validate=None,
) -> APIRouter:
    ...
    from routes.auth_routes import SESSION_COOKIE  # local import: avoid cycle

    @router.websocket("/paperclip/{path:path}")
    async def proxy_ws(websocket, path: str):
        # Websockets bypass BaseHTTPMiddleware; authenticate here.
        token = websocket.cookies.get(SESSION_COOKIE)
        ok = ws_validate(token) if ws_validate is not None else bool(token)
        if not cfg.enabled or not ok:
            await websocket.close(code=1008)  # policy violation
            return
        import websockets as _ws
        upstream_url = cfg.url.replace("http", "ws", 1) + "/" + path.lstrip("/")
        if websocket.url.query:
            upstream_url += "?" + websocket.url.query
        await websocket.accept()
        async with _ws.connect(upstream_url) as upstream:
            import asyncio

            async def c2u():
                try:
                    while True:
                        await upstream.send(await websocket.receive_text())
                except Exception:
                    await upstream.close()

            async def u2c():
                try:
                    async for msg in upstream:
                        await websocket.send_text(msg)
                except Exception:
                    await websocket.close()

            await asyncio.gather(c2u(), u2c())
```

In production, `ws_validate` is wired to Apollo's auth manager in `app.py` (Step 6).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_paperclip_routes.py::test_ws_requires_auth -v`
Expected: PASS.

- [ ] **Step 6: Wire real auth in `app.py`**

Update the registration from Task 4 to pass the validator:

```python
app.include_router(setup_paperclip_routes(
    _paperclip_cfg,
    ws_validate=lambda token: auth_manager.validate_token(token),
))
```

(`auth_manager` is the existing `AuthManager` instance in `app.py`.)

- [ ] **Step 7: Run the full proxy test file**

Run: `pytest tests/test_paperclip_routes.py -v`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add routes/paperclip_routes.py tests/test_paperclip_routes.py app.py requirements.txt
git commit -m "feat(paperclip): authenticated websocket reverse proxy"
```

---

## Task 6: Docker Compose — Paperclip + Postgres services

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the services**

Add under `services:` (model the `paperclip` build on Paperclip's own `docker/docker-compose.yml`). Use the version-pinned source build:

```yaml
  paperclip-db:
    image: docker.io/postgres:17-alpine
    environment:
      POSTGRES_USER: paperclip
      POSTGRES_PASSWORD: paperclip
      POSTGRES_DB: paperclip
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U paperclip -d paperclip"]
      interval: 2s
      timeout: 5s
      retries: 30
    volumes:
      - paperclip-pgdata:/var/lib/postgresql/data
    restart: unless-stopped

  paperclip:
    build:
      context: https://github.com/paperclipai/paperclip.git#v2026.529.0
      dockerfile: Dockerfile
    environment:
      DATABASE_URL: postgres://paperclip:paperclip@paperclip-db:5432/paperclip
      PORT: "3100"
      HOST: "0.0.0.0"
      SERVE_UI: "true"
      PAPERCLIP_DEPLOYMENT_MODE: "authenticated"
      PAPERCLIP_DEPLOYMENT_EXPOSURE: "private"
      PAPERCLIP_PUBLIC_URL: "${PAPERCLIP_PUBLIC_URL:-http://localhost:${APP_PORT:-7000}/paperclip}"
      BETTER_AUTH_SECRET: "${PAPERCLIP_AUTH_SECRET:?set PAPERCLIP_AUTH_SECRET in .env}"
      # opencode-local -> local model (Ollama on host). Confirm exact keys per spike S3.
      OPENAI_BASE_URL: "${PAPERCLIP_MODEL_BASE_URL:-http://host.docker.internal:11434/v1}"
      OPENAI_API_KEY: "${PAPERCLIP_MODEL_API_KEY:-local}"
    extra_hosts:
      - "host.docker.internal:host-gateway"   # Linux: reach host Ollama
    volumes:
      - paperclip-data:/paperclip
    depends_on:
      paperclip-db:
        condition: service_healthy
    restart: unless-stopped
```

Add the new named volumes to the existing `volumes:` block:

```yaml
  paperclip-pgdata:
  paperclip-data:
```

- [ ] **Step 2: Point Apollo at the sidecar**

In the existing `apollo` service `environment:` block, add:

```yaml
      PAPERCLIP_ENABLED: "${PAPERCLIP_ENABLED:-false}"
      PAPERCLIP_MODE: "docker"
      PAPERCLIP_URL: "http://paperclip:3100"
      PAPERCLIP_MODEL_ENDPOINT: "${PAPERCLIP_MODEL_ENDPOINT:-ollama}"
```

> Note: Paperclip is intentionally NOT given a host `ports:` mapping — it is reachable only through Apollo's `/paperclip/*` proxy.

- [ ] **Step 3: Validate compose config**

Run: `docker compose config >/dev/null && echo OK`
Expected: `OK` (no YAML/interpolation errors). Set `PAPERCLIP_AUTH_SECRET=test` in the shell first to satisfy the `:?` guard: `PAPERCLIP_AUTH_SECRET=test docker compose config >/dev/null && echo OK`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(paperclip): bundle paperclip + postgres services in compose"
```

---

## Task 7: `.env.example` — document the new variables

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append the Paperclip block**

```bash
# ── Paperclip (bundled agent-management UI) ──────────────────────────────
# Opt-in. When enabled (Docker), Paperclip runs as a sidecar reachable inside
# Apollo at /paperclip (behind Apollo auth). See docs + first-admin claim flow.
PAPERCLIP_ENABLED=false
PAPERCLIP_MODE=docker
# Public URL Paperclip advertises — must match how the browser reaches it
# (through Apollo's reverse proxy).
PAPERCLIP_PUBLIC_URL=http://localhost:7000/paperclip
# Stable secret for Paperclip's session auth. Generate once, keep it secret:
#   openssl rand -hex 32
PAPERCLIP_AUTH_SECRET=
# Local model wiring for Paperclip's opencode-local agents.
PAPERCLIP_MODEL_ENDPOINT=ollama          # ollama | apollo | custom
PAPERCLIP_MODEL_BASE_URL=http://host.docker.internal:11434/v1
PAPERCLIP_MODEL_API_KEY=local
PAPERCLIP_MODEL_NAME=
```

- [ ] **Step 2: Verify it parses**

Run: `python -c "from dotenv import dotenv_values; v=dotenv_values('.env.example'); print(v['PAPERCLIP_ENABLED'], v['PAPERCLIP_MODE'])"`
Expected: `false docker`.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(paperclip): document PAPERCLIP_* environment variables"
```

---

## Task 8: Verify model wiring end-to-end (opencode-local → local model)

This task confirms the S3 wiring actually drives a local model. It depends on Task 6 and the Task 1 decision record.

**Files:**
- Modify (if S3 differs from the assumed `OPENAI_BASE_URL`): `docker-compose.yml`

- [ ] **Step 1: Start the stack with Paperclip enabled**

```bash
export PAPERCLIP_AUTH_SECRET="$(openssl rand -hex 32)"
PAPERCLIP_ENABLED=true docker compose up -d --build apollo paperclip paperclip-db
```

Ensure Ollama is running on the host with a model pulled (e.g. `ollama pull qwen2.5:7b`).

- [ ] **Step 2: Confirm Paperclip is reachable through Apollo**

```bash
curl -sI http://localhost:7000/paperclip/ | head -1
```

Expected: `HTTP/1.1 200 OK` or `302` (auth redirect if you are not logged in — that proves the gate works).

- [ ] **Step 3: Reconcile S3**

If the Task 1 spike found opencode reads a different env var or a config file (not `OPENAI_BASE_URL`), update the `paperclip` service env in `docker-compose.yml` to match, then `docker compose up -d --build paperclip`.

- [ ] **Step 4: Functional check (manual, recorded)**

Log into Apollo → open the Paperclip tab → complete the first-admin claim → create an agent with adapter `opencode-local`, model id `openai/<your-ollama-model>` → assign a trivial issue ("reply with OK") → confirm the run completes using the local model (watch `ollama` logs / `docker compose logs paperclip`).

Record the working model-id format in the Task 1 decision record.

- [ ] **Step 5: Commit (only if compose changed)**

```bash
git add docker-compose.yml
git commit -m "fix(paperclip): align opencode-local provider env with verified S3 wiring"
```

---

## Task 9: UI — nav tab + Settings panel

**Files:**
- Modify: `static/` (nav + settings; exact files identified in Step 1)

- [ ] **Step 1: Locate the nav + settings insertion points**

Run:
```bash
grep -rln "data-view\|nav-item\|sidebar\|Settings" static/ --include=*.html --include=*.js | head
grep -rln "Settings → AI\|localmodels\|model picker\|endpoint" static/ --include=*.js | head
```
Identify the nav list and the Settings AI section used by the existing Local Models UI; follow that exact pattern.

- [ ] **Step 2: Add a "Paperclip" nav entry that opens an iframe view**

In the nav markup, add an item (matching the existing item structure) that activates a view containing:

```html
<iframe id="paperclip-frame" src="/paperclip/" title="Paperclip"
        style="width:100%;height:100%;border:0;"></iframe>
```

Hide the nav item unless enabled — fetch `/api/paperclip/status` on load and toggle visibility on `enabled`.

- [ ] **Step 3: Add a Settings → AI "Paperclip" subsection**

Mirror the Local Models settings block. Fields: enabled (read-only status from `/api/paperclip/status`), model endpoint (`ollama`/`apollo`/`custom`), custom base URL + model name (shown when `custom`), and a "Open Paperclip" button. Persist via the same prefs mechanism the Local Models section uses (identified in Step 1).

- [ ] **Step 4: Verify the nav + iframe render**

Run: `python -c "import app" && echo OK` then manually load `http://localhost:7000`, confirm the Paperclip tab appears only when enabled and the iframe loads `/paperclip/`.

- [ ] **Step 5: Commit**

```bash
git add static/
git commit -m "feat(paperclip): nav iframe tab + settings panel"
```

---

## Task 10: License attribution

**Files:**
- Create: `licenses/paperclip-LICENSE`
- Modify: `ACKNOWLEDGMENTS.md`

- [ ] **Step 1: Fetch Paperclip's MIT license**

```bash
curl -s "https://raw.githubusercontent.com/paperclipai/paperclip/v2026.529.0/LICENSE" -o licenses/paperclip-LICENSE
test -s licenses/paperclip-LICENSE && head -1 licenses/paperclip-LICENSE
```
Expected: a non-empty MIT license file.

- [ ] **Step 2: Add an acknowledgment entry**

Append to `ACKNOWLEDGMENTS.md` (matching the file's existing entry style):

```markdown
### Paperclip
Apollo bundles **[Paperclip](https://github.com/paperclipai/paperclip)** (v2026.529.0,
MIT) as an optional agent-management sidecar. License: `licenses/paperclip-LICENSE`.
```

- [ ] **Step 3: Commit**

```bash
git add licenses/paperclip-LICENSE ACKNOWLEDGMENTS.md
git commit -m "docs(paperclip): MIT attribution for bundled Paperclip"
```

---

## Task 11: README + end-to-end acceptance

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the feature**

Add a "Paperclip (agent management)" subsection under Features/Docker describing: it's opt-in (`PAPERCLIP_ENABLED=true`), set `PAPERCLIP_AUTH_SECRET`, it appears as a tab inside Apollo, the first-admin claim on first open, and that agents use `opencode-local` against your local model (default Ollama).

- [ ] **Step 2: Full acceptance run**

```bash
export PAPERCLIP_AUTH_SECRET="$(openssl rand -hex 32)"
PAPERCLIP_ENABLED=true docker compose up -d --build
pytest tests/test_paperclip_config.py tests/test_paperclip_proxy.py tests/test_paperclip_routes.py -v
```
Expected: all paperclip tests PASS; `http://localhost:7000/paperclip/` reachable behind auth; claim flow → create opencode-local agent on a local model → trivial task completes.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(paperclip): README usage for bundled Paperclip"
```

---

## Out of scope (Phase 2 / Phase 3 — separate plans)

- **Phase 2 (native macOS):** `services/paperclip/prereqs.py`, `postgres.py` (Homebrew PG cluster), `server_manager.py` (supervised `paperclipai@2026.529.0` process), lifecycle wiring into `src/app_initializer.py`, graceful degradation, `start-macos.sh`/`build-macos-app.sh` integration.
- **Phase 3 (polish):** Apollo `/v1/chat/completions` + `/v1/models` proxy (so `PAPERCLIP_MODEL_ENDPOINT=apollo` works against the warm llama-server), richer endpoint-selector UI, optional SSO, optional first-class "Apollo local" Paperclip adapter.

## Self-review notes

- **Spec coverage:** §5–§10 of the spec map to Tasks 2–11; §8.2 (native) and §6.3 (Apollo `/v1` proxy) are explicitly deferred to Phase 2/3 above. §6 model wiring → Tasks 2 + 6 + 8. §9 auth → global middleware (HTTP) + Task 5 (WS). §13 attribution → Task 10.
- **Type consistency:** `PaperclipConfig` fields used identically across config.py, routes, and tests; `setup_paperclip_routes(cfg, http_client=None, ws_validate=None)` signature consistent in Tasks 4–5 and app.py.
- **Spike-gated items** are flagged in Tasks 4, 5, and 8 with concrete fallbacks; the assumed S3 wiring (`OPENAI_BASE_URL` + `openai/<model>`) is implemented concretely and verified in Task 8.

---

## Spike decision record (Task 1) — 2026-06-07

Probed a **live, natively-running** Paperclip v2026.529.0 (`npx paperclipai run`
on :3100; `/api/health` → version 2026.529.0, deploymentMode `local_trusted`,
authReady true). Docker engine was unavailable, so verification used this
native instance instead of a Docker build.

- **S1 (subpath) = Outcome B (absolute root paths).** The UI references
  `/assets/index-*.js`, `/assets/index-*.css`, `/favicon.ico`,
  `/site.webmanifest`, `/apple-touch-icon.png` at the **root**, with no base-href
  / PUBLIC_URL prefix. The compiled bundle calls its API at absolute `/api/*`
  (`/api/auth/get-session`, `/api/auth/sign-in/email`, `/api/health`, …) via
  better-auth. **Apollo also owns `/api/*`**, so a same-origin subpath embed
  under `/paperclip/` collides irreparably (the SPA's `/api` + `/assets`
  resolve to Apollo, not Paperclip). Vite `base` is build-time → no runtime fix.
  **Conclusion:** the reverse-proxy-at-subpath UI seam is NOT viable. Pivot the
  UI to a **direct iframe to Paperclip's own origin** (Paperclip ships its own
  better-auth + deployment modes, so it self-protects). Keep config, lifecycle,
  model wiring, settings, and attribution.
- **Realtime:** Paperclip is an Express server (`X-Powered-By: Express`); auth +
  data over `/api/*`. (No separate WS path observed from the shell; the direct
  iframe makes Apollo-side WS proxying moot anyway.)
- **S3 (opencode wiring):** `opencode-local` adapter (`type=opencode_local`)
  uses `provider/model` ids (default `openai/gpt-5.2-codex`); OpenCode performs
  provider routing. Local wiring = point OpenCode's `openai` provider at a local
  base URL (Ollama `…:11434/v1`) and use `openai/<model>`. To be confirmed
  against a running agent once an end-to-end run is possible.

**Impact on plan:** Task 4's reverse proxy + Task 5's WS proxy are superseded for
the UI by the direct-iframe pivot. Task 6 must expose Paperclip's port to the
browser; Task 9's iframe `src` becomes the Paperclip origin URL; config gains a
browser-facing URL. Pending user approval of the pivot.
