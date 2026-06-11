# Apollo — Error Handling & Logging

Apollo's error philosophy: **degrade, don't die**. Optional subsystems that fail at
startup log and continue; proxies translate transport failures into honest 502/503s;
streaming routes carry errors in-band over SSE because HTTP status codes are already
spent; client disconnects save partial work. Logging is plain stdlib `logging` with
per-module loggers — no structured-logging framework.

## 1. Logging setup

The root configuration lives at the top of `app.py`; every other module just takes a
named logger:

```python
# app.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)
```

```python
# e.g. services/paperclip/collector.py, routes/lmproxy_routes.py, core/database.py
logger = logging.getLogger(__name__)
```

Log destinations (uvicorn writes to stdout/stderr; files appear where a supervisor
redirects them):

| Location | Written by |
|---|---|
| `logs/apollo-app.log` | macOS `Apollo.app` launcher (`>>"$LOG" 2>&1`) |
| `logs/` (e.g. `compound.log`) | app-level logs; bind-mounted in Docker (`./logs:/app/logs`) |
| `/tmp/apollo-llama-<port>.log` | each launched `llama-server` (`services/localmodels/server_manager.py`) |
| `/tmp/apollo-tmux/*.log` | per-tmux-session Cookbook download/serve logs |
| journald | systemd deployments (`apollo-ui.service`) |

The `scripts/apollo-logs` CLI unifies them: `apollo-logs list`, `apollo-logs tail NAME`,
`apollo-logs clean --scope all --apply`.

## 2. Route error patterns

### 2.1 Proxy transport failures → 502

Both reverse proxies map connection failures to a 502 with a terse, non-leaky message —
`ConnectError` gets a friendly body, any other `RequestError` reports only the
exception class name (regression-tested by
`tests/test_lmproxy_routes.py::test_502_when_upstream_request_fails_mid_flight`):

```python
# routes/lmproxy_routes.py (_forward)
try:
    upstream = await client.send(
        client.build_request(request.method, url, headers=headers, content=body),
        stream=True,
    )
except httpx.ConnectError:
    return JSONResponse({"error": "local model server unreachable"}, status_code=502)
except httpx.RequestError as exc:
    return JSONResponse(
        {"error": f"local model request failed: {exc.__class__.__name__}"},
        status_code=502,
    )
```

`routes/paperclip_routes.py` (`proxy`) is identical in shape
(`"Paperclip is not reachable"` / `"Paperclip request failed: <ClassName>"`), and both
return 503 *before* dialing when the feature is off or no warm model exists
(`{"error": "no local model is currently running; serve one in Apollo ... first"}`).

### 2.2 Per-row corruption guards

List endpoints never let one bad row poison the collection. The canonical example is
`list_servers` in `routes/mcp_routes.py` — JSON columns are parsed per row, and a
corrupt row is *reported as an item* rather than thrown:

```python
# routes/mcp_routes.py (list_servers)
try:
    oauth_cfg = json.loads(srv.oauth_config) if srv.oauth_config else None
    disabled_list = json.loads(srv.disabled_tools) if srv.disabled_tools else []
    args = json.loads(srv.args) if srv.args else []
    env = json.loads(srv.env) if srv.env else {}
except (json.JSONDecodeError, TypeError) as exc:
    # One corrupted row must not take down the whole list.
    logger.warning("MCP server %s has corrupted config: %s", srv.id, exc)
    result.append({
        "id": srv.id, "name": srv.name, ...,
        "status": "error",
        "error": f"corrupted configuration: {exc}",
    })
    continue
```

### 2.3 HTTPException conventions

- **400** — malformed input validated up front: `raise HTTPException(400, "command is
  required for stdio transport")` (`mcp_routes.py`); regex-validated filenames in
  `app.py` (`/api/generated-image/{filename}` → `Invalid filename`).
- **403** — `core/middleware.py:require_admin` raises `HTTPException(403, "Admin
  only")`; ownership failures also use 403 (`"Choose a registered model endpoint"` in
  `routes/session_routes.py`).
- **404** — both "not found" and "exists but not yours" (don't confirm existence):
  the generated-image route returns 404 when a gallery row has a different owner.
- **503** — feature disabled / dependency not up (Paperclip disabled, no warm model,
  ChromaDB unreachable).
- **504** — the global `_RequestTimeoutMiddleware` in `app.py` aborts any non-exempt
  request after `REQUEST_HARD_TIMEOUT` (45s default): *"a single hung subprocess.run
  or missing-timeout httpx call locks up the entire server for everyone."* Streaming,
  research, uploads, probes, etc. are whitelisted via `_TIMEOUT_EXEMPT_PREFIXES`.

## 3. Streaming error channel (`event: error` SSE frames)

Once a stream has begun, the HTTP status is spent — so `src/llm_core.py` delivers
errors as named SSE events carrying a status code in the payload, and the front-end
renders them in-line:

```python
# src/llm_core.py (stream_llm; the docstring lists the channels)
#   - event: error                       — errors
...
except httpx.ConnectError:
    _cooled = _mark_host_dead(target_url)
    ...
    yield f'event: error\ndata: {json.dumps({"error": f"Cannot reach {_host_key(target_url)}", "status": 503})}\n\n'
except httpx.ReadTimeout:
    yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
except httpx.RequestError:
    yield f'event: error\ndata: {json.dumps({"error": "Network error", "status": 502})}\n\n'
```

Non-2xx upstream responses also become error frames with the upstream status and a
truncated raw body: `{"status": r.status_code, "text": friendly, "raw": raw[:500]}`.
`stream_llm_with_fallback` watches for `event: error` chunks that arrive **before any
content** and retries the next candidate endpoint; mid-stream errors pass through
unchanged (a half-answer must not silently restart).

### Dead-host cooldown

A failed connect marks the host so subsequent calls fail instantly instead of waiting
out the connect timeout — but only after consecutive failures, so a single blip does
not lock a healthy model out:

```python
# src/llm_core.py
DEAD_HOST_COOLDOWN = 20.0
_HOST_FAIL_THRESHOLD = 2
_dead_hosts: Dict[str, float] = {}
_host_fails: Dict[str, int] = {}
_host_health_lock = threading.Lock()   # maps are mutated from threadpool AND event loop

def _mark_host_dead(url: str) -> bool:
    key = _host_key(url)
    with _host_health_lock:
        n = _host_fails.get(key, 0) + 1
        _host_fails[key] = n
        if n >= _HOST_FAIL_THRESHOLD:
            _dead_hosts[key] = time.time() + DEAD_HOST_COOLDOWN
            return True
        return False
```

Any success calls `_clear_host_dead`, resetting both maps. The lock exists because sync
`llm_call()` runs in FastAPI's threadpool while `llm_call_async()` runs on the event
loop — the unlocked read-modify-write lost failure counts (issue #659). While cooled,
non-stream calls raise `HTTPException(503, "...marked unreachable (cooldown active)")`
and streams emit a 503 error frame.

## 4. CancelledError discipline in chat streams

When the browser disconnects mid-generation, FastAPI cancels the generator. Both chat
mode and agent mode in `routes/chat_routes.py` catch
`(asyncio.CancelledError, GeneratorExit)`, save the partial response, and — critically
— wrap the save in its **own** try so a save failure cannot mask the original
cancellation (which would skip the outer `finally` and leak `_active_streams` state):

```python
# routes/chat_routes.py (chat mode; agent mode at ~line 1037 is identical in shape)
except (asyncio.CancelledError, GeneratorExit):
    # Guard the save so a failure inside add_message /
    # save_sessions can't mask the original CancelledError
    try:
        if full_response:
            logger.info("Client disconnected mid-stream (chat mode) for session %s, saving partial (%d chars)", session, len(full_response))
            _stopped_content, _stopped_md = clean_thinking_for_save(full_response, {"stopped": True, "model": sess.model})
            sess.add_message(ChatMessage("assistant", _stopped_content, metadata=_stopped_md))
            if not incognito:
                session_manager.save_sessions()
    except Exception:
        logger.exception("Failed to save partial response on disconnect (chat mode, session %s)", session)
    raise
finally:
    _active_streams.pop(session, None)
```

The saved message is tagged `{"stopped": True}` in metadata, and reasoning tokens
(flagged `thinking: true` in deltas) are forwarded live but excluded from
`full_response`, so the partial save contains only visible output.

## 5. Warn-once-then-debug retry logging

Long-running reconnect loops must not spam the log. The Paperclip collector
(`services/paperclip/collector.py`) warns on the *first* failure, downgrades repeats to
debug, and resets the flag on a successful connect:

```python
# services/paperclip/collector.py (run loop)
except Exception as exc:
    if not self._warned:
        logger.warning("Paperclip collector unavailable (will retry): %s", exc)
        self._warned = True
    else:
        logger.debug("Paperclip collector retry failed: %s", exc)
```

Reconnects use capped exponential backoff (`min_backoff` → ×2 → `max_backoff`, default
1s→60s), with a session that survived >30s resetting the backoff; `_consume` clears
`self._warned = False` once connected so the *next* outage warns once again.

## 6. Startup-degradation philosophy

`app.py` registers ~40 routers through labeled guards from
`services/app_startup.py`, so a broken subsystem fails with its name attached instead
of a bare traceback mid-file:

```python
# services/app_startup.py
def build_and_include_router(app, label, factory, *args, logger=None, **kwargs):
    try:
        router = factory(*args, **kwargs)
    except Exception as exc:
        if logger:
            logger.exception("Failed to build %s routes", label)
        raise RuntimeError(f"Failed to build {label} routes") from exc
    return include_router_checked(app, router, label, logger=logger)
```

Optional dependencies degrade with a logged warning, never a crash:

- **Vector RAG:** if ChromaDB is unreachable at boot, `get_rag_manager()` returns
  `None` and `app.py` logs *"Vector document RAG not available at startup (ChromaDB may
  not be reachable yet — routes will retry lazily)"*; personal-doc routes return a
  clean 503 instead of busy-retrying.
- **Local model scan:** runs on a daemon thread; `services/localmodels/lifecycle.py`
  wraps it — `logger.warning("Local model startup scan failed: %s", e)`.
- **Registry sync:** `services/localmodels/registry.py` catches everything —
  `# never let a scan crash the caller` → `logger.warning("Failed to sync managed
  local endpoint: %s", e)`.
- **Node bootstrap / Paperclip runtime:** failures log
  `"Paperclip Node bootstrap skipped: %s"` and fall back to a PATH Node
  (`services/paperclip/node_bootstrap.py` logs `"Node bootstrap failed (%s); will try
  a system Node"`).
- **LOCALHOST_BYPASS** logs a prominent `logger.warning(...)` at import so the
  insecure mode is visible in every boot log.

## 7. Diagnostics routes

`routes/diagnostics_routes.py` (all admin-gated via `require_admin`):

- `GET /api/db/stats` — `core.database.get_detailed_stats()`; 500 with a generic body
  on failure (`logger.error` keeps the detail server-side).
- `GET /api/rag/stats` — RAG stats or `{"error": "RAG system not available"}`.
- `GET /api/test/youtube?url=` and `POST /api/test-research` — end-to-end probes that
  return `{"error": ...}` payloads rather than raising.

Plus the unauthenticated basics in `app.py`: `GET /api/health`, `GET /api/ready`,
`GET /api/version`, and the system-status card backend
(`routes/system_status_routes.py`) which aggregates component readiness with
`next_step` hints.

## 8. Where to look when X breaks

| Symptom | First place to look |
|---|---|
| App won't boot, names a subsystem | `RuntimeError("Failed to build <Label> routes")` — the label maps to a `build_and_include_router` call in `app.py` |
| Chat returns instant 503 "cooldown active" | Dead-host cooldown (`src/llm_core.py`); wait 20s or fix the endpoint, success auto-clears |
| Local model won't start / times out | `/tmp/apollo-llama-<port>.log` (the health-wait error embeds its tail); `services/localmodels/server_manager.py` |
| Model picker missing local models | startup-scan warning in app log; `GET /api/localmodels` rescan; `services/localmodels/lifecycle.py` |
| `/paperclip` 502/503 | `GET /api/paperclip/status` (`reachable`, `collector`, `agent_workbench` fields); compose profile running? |
| Floor shows nothing | Collector warn-once line in app log; `PAPERCLIP_EVENTS_TOKEN` mismatch returns 401 on ingest |
| Random 504s on slow endpoints | `_RequestTimeoutMiddleware` in `app.py` — add the path to `_TIMEOUT_EXEMPT_PREFIXES` or raise `REQUEST_HARD_TIMEOUT` |
| MCP server row shows `status: "error"` | `corrupted configuration` warning in log; fix the JSON columns for that `srv.id` |
| Personal docs 503 | ChromaDB not reachable (`CHROMADB_HOST/PORT`); RAG init log line at startup |
| Desktop app dies silently (macOS) | `logs/apollo-app.log`; the launcher's `die_gui` dialog quotes the path |
