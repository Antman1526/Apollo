# Apollo — Testing Strategy & Test Cases

Apollo's test suite is large (~260 files), flat, and fast: everything lives directly in
`tests/` with no subdirectories, Python tests run under plain `pytest -q`, and the
JavaScript suites run under Node's built-in `node:test` runner with zero browser
dependency. The suite is wired into a single gate script (`scripts/check.sh`) that is
also exactly what CI runs. This document describes the layout, the recurring test
patterns (and why they exist), and representative cases per subsystem.

## 1. Layout and the check gate

- `tests/` — flat directory, ~261 entries. Python tests are `test_*.py`; the four
  JavaScript suites are `test_*.mjs`. Shared fixtures live in `tests/conftest.py`
  and `tests/real_modules.py`.
- The full gate is `scripts/check.sh` (also exposed as `npm run check`):

```bash
# scripts/check.sh
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

"$PYTHON" -m compileall -q app.py companion core routes services src scripts/apollo-ralph scripts/check-paperclip-browser
"$PYTHON" -m pytest -q
npm run test:js
```

Three stages, in order: byte-compile every first-party module (catches syntax errors in
files no test imports), the Python suite, then the JS suite. `package.json` defines the
JS half explicitly — there is no test discovery for `.mjs` files, new suites must be
added to the list:

```json
// package.json
"scripts": {
  "check": "bash scripts/check.sh",
  "test": "npm run test:js",
  "test:js": "node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs tests/test_system_status_actions.mjs tests/test_theme_presets.mjs"
}
```

CI (`.github/workflows/ci.yml`) runs the same three stages on Python 3.12 / Node 20 for
every push to `main` and every pull request.

## 2. Python conventions

### 2.1 Dependency stubbing in conftest.py

`tests/conftest.py` puts the repo root on `sys.path` and stubs heavy optional
dependencies **only when they are not installed** — real FastAPI/Starlette/Pydantic are
never replaced, because route tests import their subpackages:

```python
# tests/conftest.py
for mod_name in [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.types", ...
    "bcrypt", "pyotp", "httpx", "fastapi", ...
]:
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()
```

`tests/real_modules.py` provides `import_real_module(name)` for tests that need to undo
a collection-time stub and import the genuine on-disk module.

### 2.2 Stubbing `core.database` before import

Modules that import ORM models at module level get the DB stubbed *before* the import,
so the test never needs SQLAlchemy or a database file. The canonical pattern is at the
top of `tests/test_localmodels_registry.py`:

```python
# tests/test_localmodels_registry.py
import sys, types
from unittest.mock import MagicMock

# Stub core.database before registry imports it (avoids SQLAlchemy env issues)
if "core.database" not in sys.modules:
    _core_db = types.ModuleType("core.database")
    for _name in [
        "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
        "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
        "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
    ]:
        setattr(_core_db, _name, MagicMock())
    sys.modules["core.database"] = _core_db

from services.localmodels import registry
```

The same file then swaps in tiny hand-rolled fakes (`_FakeSession`, `_FakeQuery`,
`_FakeEP`) via `monkeypatch.setattr(registry, "SessionLocal", lambda: sess)` so the
sync logic is tested against in-memory rows. Note the deliberate class-level
`base_url = None` on `_FakeEP` — SQLAlchemy column comparisons like
`ModelEndpoint.base_url == X` are evaluated eagerly, so the fake must tolerate them.

### 2.3 httpx transports: ASGITransport vs MockTransport

Proxy route tests mount the router against an injected `httpx.AsyncClient`. Two
transports are used depending on what is being exercised
(`tests/test_lmproxy_routes.py`):

```python
# tests/test_lmproxy_routes.py
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
```

- **`ASGITransport` + a stub Starlette upstream** when the real streaming forward path
  must run end-to-end (body, status, headers all travel through `client.send(...,
  stream=True)` — see `test_forwards_chat_to_warm_llama_server`).
- **`MockTransport`** when the upstream is irrelevant (auth rejection tests) or when a
  transport error must be injected:

```python
def test_502_when_upstream_request_fails_mid_flight():
    def boom(request):
        raise httpx.ReadError("connection lost", request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    ...
    assert r.status_code == 502
```

`tests/test_paperclip_routes.py` uses the identical `_app(cfg, upstream_view)` helper
for the `/paperclip/{path}` reverse proxy, including a POST-body round-trip
(`test_proxy_forwards_post_body_and_status`) and the disabled-sidecar 503.

### 2.4 Driving SSE generators directly (infinite streams)

`TestClient` buffers whole response bodies, so a never-ending SSE stream would hang the
test. The Paperclip stream tests instead pull the endpoint function off the router and
iterate its generator manually:

```python
# tests/test_paperclip_routes.py
def _stream_endpoint(app):
    """The live stream never ends, and TestClient buffers whole bodies, so the
    SSE tests drive the endpoint's generator directly."""
    for route in app.routes:
        if getattr(route, "path", "") == "/api/paperclip/stream":
            return route.endpoint
    raise AssertionError("stream route not found")

def test_stream_waits_for_events_when_enabled_but_idle():
    endpoint = _stream_endpoint(_app(_cfg()))
    async def run():
        resp = await endpoint()
        gen = resp.body_iterator
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()
    first = asyncio.run(run())
    assert "paperclip.stream.waiting" in first
```

`test_stream_delivers_events_ingested_after_connect` extends this: it holds the
generator open, POSTs to `/api/paperclip/events` through an `ASGITransport` pointed at
the *same* app (with `PAPERCLIP_EVENTS_TOKEN` monkeypatched), then asserts the next
`__anext__()` yields the ingested `agent.status` event. Always `aclose()` the generator
in `finally` so the hub subscription is released.

### 2.5 Real-websocket integration test

Unit tests inject a fake `ws_connect` into `PaperclipCollector`; one test exercises the
genuine `websockets` library path — URL construction, the `additional_headers`
handshake on websockets≥14, and frame iteration — against a real in-process server
(`tests/test_paperclip_collector_live.py`):

```python
# tests/test_paperclip_collector_live.py
async def run():
    hub = EventHub()
    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        collector = PaperclipCollector(
            _cfg(port), hub.publish,
            token="live-key", company_id="c-live",
            min_backoff=0.05, max_backoff=0.1,
        )
        collector.start()
        for _ in range(400):
            if len(hub.recent) >= 2:
                break
            await asyncio.sleep(0.01)
        await collector.stop()
    return hub.recent
```

The server handler records `connection.request.path` and the `Authorization` header, so
the test asserts both the wire URL (`/api/companies/c-live/events/ws`) and
`Bearer live-key` — things a fake cannot validate. Polling with a deadline (400 × 10ms)
instead of a fixed sleep keeps it fast and non-flaky.

## 3. JavaScript suites (node:test)

All four suites use `node:test` + `node:assert/strict`. Two patterns:

### 3.1 DOM stubbing for module import

`static/js/paperclip.js` touches `document` at import time, so the suite installs a
minimal global stub *before* the dynamic import (`tests/test_paperclip_floor_ui.mjs`):

```javascript
// tests/test_paperclip_floor_ui.mjs
global.document = {
  readyState: 'loading',
  addEventListener() {},
  getElementById() { return null; },
};
global.window = { open() {} };

const paperclip = await import('../static/js/paperclip.js');
```

The floor suite then tests the exported pure model/layout functions:
`createFloorState()` → `applyFloorEvent(state, event)` → `computeWorkspaceLayout(state)`
→ `commitWorkspaceLayout(state, layout)` → `renderWorkspaceHTML(state)`. Representative
cases: event normalization into zones/transcripts, the focus-figure SVG markup
(`assert.doesNotMatch(html, /<script/i)` — an XSS regression guard), and the
walk-once-per-move contract:

```javascript
// A zone change walks once...
const second = paperclip.computeWorkspaceLayout(state);
assert.equal(second.agents[0].moving, true);
paperclip.commitWorkspaceLayout(state, second);
// ...and the next render tick must NOT replay the walk.
const third = paperclip.computeWorkspaceLayout(state);
assert.equal(third.agents[0].moving, false);
```

`tests/test_system_status_card.mjs` / `test_system_status_actions.mjs` import the pure
renderer `renderSystemStatusCardHTML` from `static/js/systemStatusCard.js` and assert on
markup substrings ("8/10 systems ready", next-step strings, action buttons).

### 3.2 Source-level theme contrast tests

`static/js/theme.js` drags in a DOM-heavy module chain, so
`tests/test_theme_presets.mjs` validates the preset table at **source level**: it reads
the file with `fs.readFileSync`, extracts palettes by regex, and does real WCAG math:

```javascript
// tests/test_theme_presets.mjs
function luminance(hex) {
  const c = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}
```

Assertions: every new preset has a complete palette, `contrast(bg, fg) >= 4.5`
(WCAG-ish AA), light themes have `luminance(bg) > 0.5`, dark themes `< 0.2`.

## 4. Repo-hygiene guards

`tests/test_docs_no_orphan_images.py` is a regression guard for stray PR screenshots
committed under `docs/`: it lists git-tracked images under `docs/`
(`git ls-files docs`), concatenates every tracked text file
(`.md/.html/.js/.py/...`) into one haystack, and fails if any image filename appears
nowhere. It `pytest.skip`s cleanly when git is unavailable. Similar guards:
`test_readme_ascii_fenced.py`, `test_searxng_image_pinned.py` (compose tag pin),
`test_app_static_mime.py`.

## 5. Representative tests per subsystem

| Subsystem | Representative files |
|---|---|
| LLM core / streaming | `test_llm_core_streaming.py`, `test_llm_core_fallback.py`, `test_llm_core_concurrency.py`, `test_llm_core_anthropic_cache.py` |
| Chat routes | `test_chat_stream_scope.py`, `test_chat_metrics.py`, `test_agent_loop.py` |
| Local models | `test_localmodels_scanner.py`, `test_localmodels_registry.py`, `test_localmodels_server.py`, `test_localmodels_routes_api.py` |
| Paperclip | `test_paperclip_routes.py`, `test_paperclip_collector.py`, `test_paperclip_collector_live.py`, `test_paperclip_agent_tokens.py`, `test_paperclip_floor_ui.mjs` |
| lmproxy | `test_lmproxy_routes.py` |
| Auth / security | `test_auth_regressions.py`, `test_totp_failclosed.py`, `test_security_regressions.py`, `test_reserved_username_admin_escalation.py`, `test_webhook_ssrf_resilience.py`, `test_null_owner_gates.py` |
| Owner scoping | `test_calendar_owner_scope.py`, `test_email_owner_scope.py`, `test_session_endpoint_owner_scope.py`, `test_skills_manager_owner_isolation.py` |
| MCP | `test_mcp_manager.py`, `test_mcp_cache_invalidation.py`, `test_mcp_reconnect_args.py` |
| Search/research | `test_search_ranking.py`, `test_deep_research_synthesis_resilience.py`, `test_research_service.py` |
| Database | `test_sqlite_foreign_keys.py`, `test_update_database_script.py` |
| Packaging/launchers | `test_node_bootstrap.py`, `test_windows_update_script.py`, `test_platform_compat.py` |
| Frontend (JS-in-Python) | `test_markdown_rendering_js.py`, `test_esc_menu_stack_js.py`, `test_compare_js.py` (extract + assert on JS source) |

## 6. Running subsets

```bash
# Everything (the gate; same as CI)
bash scripts/check.sh            # or: npm run check

# Python only
venv/bin/python -m pytest -q

# One file / one test / keyword match
venv/bin/python -m pytest -q tests/test_paperclip_routes.py
venv/bin/python -m pytest -q tests/test_lmproxy_routes.py::test_503_when_no_warm_model
venv/bin/python -m pytest -q -k "paperclip and not live"

# JS only — all four suites, or a single one
npm run test:js
node --test tests/test_theme_presets.mjs
```

`pytest-asyncio` is in `requirements.txt`, but most async tests simply use
`asyncio.run(...)` inside a sync test function — the suite stays runnable with plain
`pytest -q` and no markers or plugins required.
