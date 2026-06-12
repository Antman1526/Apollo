# 12 — Error Handling & Logging

Apollo's error strategy has two recurring principles:

1. **Never raise into startup or hot paths** — sidecars (SearXNG, Paperclip) and the
   search chain degrade gracefully instead of taking the app down.
2. **Map failures to the right surface** — HTTP routes translate exceptions to status
   codes, SSE streams emit typed `error`/`*_failed` events, and the WS protocol sends
   `{"type": "error"}` frames without killing the socket.

---

## 1. Python logging setup

### Root config (`app.py`)

```python
# app.py:71-75
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)
```

Modules across `app.py`, `routes/`, `services/`, and `src/` each take a
`logging.getLogger(__name__)` and emit through this root config (e.g.
`services/searxng/runtime.py:20`, `services/search/core.py`, `src/settings.py:15`).

### Dedicated search-error logger

`services/search/analytics.py` attaches its own `FileHandler` so search failures are
captured separately from the app log and **don't propagate** to the root logger:

```python
# services/search/analytics.py:13-20
_error_log_path = Path(__file__).resolve().parent.parent / "search_engine_error.log"
_error_handler = logging.FileHandler(_error_log_path, encoding="utf-8")
_error_handler.setLevel(logging.WARNING)
_error_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
error_logger = logging.getLogger("search_engine_error")
error_logger.addHandler(_error_handler)
error_logger.propagate = False
```

It also defines the custom exception hierarchy (`SearchEngineError` and subclasses like
`RateLimitError`, `NetworkError`, `ParseError`) that the search core switches on.

### Log locations (`logs/`)

| File | Writer |
|---|---|
| `logs/apollo-app.log` | uvicorn stdout/stderr from the macOS `.app` launcher (`build-macos-app.sh:118`). |
| `logs/searxng.log` | the SearXNG sidecar's stdout/stderr (`services/searxng/runtime.py:27`). |
| `search_engine_error.log` (repo root) | the dedicated search error logger above. |
| `logs/mcp-*.log` | MCP server logs (puppeteer etc.). |

---

## 2. `_handle_browser_error` — exception → HTTP status mapping

`routes/browser_routes.py:145` centralizes the browser-route error contract; every
browser HTTP handler funnels its `except Exception` through it (`:182, 190, 198, …`):

```python
# routes/browser_routes.py:145-154
def _handle_browser_error(exc: Exception) -> HTTPException:
    if isinstance(exc, embedded_browser.BrowserSecurityError):
        return HTTPException(400, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, embedded_browser.BrowserUnavailable):
        return HTTPException(503, str(exc))
    if isinstance(exc, TimeoutError) or exc.__class__.__name__ == "TimeoutError":
        return HTTPException(504, "Browser operation timed out")
    return HTTPException(500, f"Browser operation failed: {str(exc)[:240]}")
```

- `BrowserSecurityError` / `ValueError` → **400** (bad request / blocked URL).
- `BrowserUnavailable` (no Chromium) → **503**.
- Timeout (by type or class name, covering Playwright's own `TimeoutError`) → **504**.
- Anything else → **500**, message truncated to 240 chars so internals don't leak.

---

## 3. Graceful sidecar failure (never-raise-into-startup)

### SearXNG runtime

`services/searxng/runtime.py` mirrors the Paperclip runtime: injectable spawn/health,
no-ops when disabled/not installed, and **never raises into startup** (module docstring
`:1-6`). Key guards:

- The health probe **fails closed** and swallows all exceptions, so a refused
  connection just reads as "not serving":

  ```python
  # services/searxng/runtime.py:30-41
  def _http_ok(url, timeout=2.0):
      try:
          with urllib.request.urlopen(url, timeout=timeout) as r:
              return r.read(16).startswith(b"OK")
      except Exception:
          return False
  ```

- A spawn failure is logged and turned into `False` (not an exception), with the log
  handle cleaned up (`:152-162`).
- `maybe_restart()` is called from the search hot path, so it is wrapped to "never
  block and never raise" (`:224-245`), rate-limited to one attempt per 300 s cooldown.
- App startup spawns the sidecar on a daemon thread and swallows failures as
  non-critical:

  ```python
  # app.py:899-914
  @app.on_event("startup")
  async def _start_searxng_runtime():
      def _boot():
          try:
              _get_searxng_runtime().start()
          except Exception as e:
              logger.warning("SearXNG sidecar startup failed (non-critical): %s", e)
      threading.Thread(target=_boot, name="searxng-runtime", daemon=True).start()
  ```

### Paperclip runtime

`services/paperclip/runtime.py` follows the same contract ("it never raises into Apollo
startup", `:8`): a start failure is caught and logged as a warning, never re-raised
(`:153-154`), and a missing CLI just leaves Paperclip off (`:144`).

---

## 4. The search fallback degradation chain

`services/search/core.py` turns provider failure into graceful degradation rather than
an error to the user.

### Skip a down sidecar with no timeout penalty

`_searxng_definitely_down()` (`:96`) is true **only** when the managed sidecar is the
target and isn't serving; it returns `False` (let HTTP decide) for a custom
`search_url`, an explicit `SEARXNG_INSTANCE`, or a disabled sidecar, and **fails open**
on any internal error (`:120-121`). When the sidecar is installed but down it also fires
a background restart (`:116-118`). `_build_provider_chain` then drops SearXNG from the
chain entirely so DuckDuckGo answers immediately:

```python
# services/search/core.py:131-134
chain = [primary]
if primary == "searxng" and _searxng_definitely_down():
    logger.info("SearXNG sidecar not serving — skipping straight to fallback providers")
    chain = []
```

### Per-provider retry + fall-through

Each provider gets up to 2 attempts; a `RateLimitError` breaks immediately (an instant
429 retry is pointless), network/parse errors are logged and retried, and the loop falls
through to the next provider (`:192-210`). Errors go to the dedicated `error_logger`:

```python
# services/search/core.py:200-208
except RateLimitError as e:
    error_logger.error(f"{provider_name} rate-limited (attempt {attempt + 1}): {e}")
    break
except (NetworkError, ParseError) as e:
    error_logger.error(f"{provider_name} search error (attempt {attempt + 1}): {e}")
except Exception as e:
    error_logger.error(f"Unexpected error during {provider_name} search (attempt {attempt + 1}): {e}")
```

If every provider fails, the function logs and returns an empty list rather than raising
(`:231-234`) — the caller decides how to surface "no results".

---

## 5. SSE error events

Streaming endpoints emit typed events on the `text/event-stream` so the UI can react.

### `web_search_failed` (`routes/chat_routes.py`)

When web access was explicitly requested (or the auto-decider chose to search) but the
search returned nothing, the chat stream emits a `web_search_failed` event so the UI can
warn instead of silently answering stale. Note the comment: an `auto-skip` (decider
chose *not* to search) is intentional and must **not** fire it:

```python
# routes/chat_routes.py:702-712
if web_sources:
    yield f"data: {json.dumps({'type': 'web_sources', 'data': web_sources})}\n\n"
elif (_web_decision in ("always", "auto-search")
      or str(use_web).lower() == "true"):
    # Web was explicitly requested but returned nothing — surface a failure
    # event so the UI can inform the user rather than silently answering stale.
    yield f"data: {json.dumps({'type': 'web_search_failed'})}\n\n"
```

### `event: error` from the LLM core

`src/llm_core.py` maps upstream LLM failures to SSE `event: error` frames with HTTP-like
status codes (`:1109-1184`): cooldown/unreachable → 503, read timeout → 504, network
error → 502, and upstream HTTP errors pass through the status + a friendly message with
a truncated raw body. The chat rewrite stream emits the same shape on any exception:

```python
# routes/chat_routes.py:1356-1358
except Exception as e:
    logger.error("Rewrite stream error: %s", e)
    yield f'event: error\ndata: {json.dumps({"error": str(e), "status": 500})}\n\n'
```

`src/agent_loop.py` and `src/agent_runs.py` likewise emit/relay `event: error`
(`agent_loop.py:1689`, `agent_runs.py:124`).

---

## 6. WebSocket error frames (embedded browser)

The browser live-view WS (`routes/browser_routes.py`) reports problems as
`{"type": "error", "message": …}` JSON frames and is careful to keep the stream alive:

- Pre-stream reachability check surfaces `BrowserUnavailable` (and any other error) as
  an error frame + clean close, not a dead socket (`:313-325`).
- A screencast-start failure sends an error frame and tears down the viewer (`:337-346`).
- Invalid client JSON sends `{"type":"error","message":"invalid JSON"}` and
  **continues** the loop (`:351-355`).
- **Input/nav replay failures must never kill the stream** — they're logged at debug and
  reported as an error frame while the loop keeps running:

  ```python
  # routes/browser_routes.py:356-364
  try:
      await _dispatch_ws_message(websocket, msg)
  except Exception as exc:
      logger.debug("browser ws message failed: %s", exc)
      await _ws_send(
          websocket,
          {"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:200]}"},
      )
  ```

- `WebSocketDisconnect` is caught and treated as a normal close (`:365-366`).
- All error messages truncate the exception text (`[:200]`) to avoid leaking internals.

---

## Summary of the contract

| Layer | Failure surface | Behavior |
|---|---|---|
| App startup | logger.warning | Sidecars boot on daemon threads; never fatal (`app.py:904`). |
| Sidecar runtime | logger.warning, return False | Spawn/health failures degrade; `_http_ok` fails closed (`runtime.py:30-41,152-162`). |
| Search chain | `error_logger` + empty result | Skip down sidecar, retry, fall through, return `[]` (`core.py:131-234`). |
| Browser HTTP | `HTTPException` 400/503/504/500 | `_handle_browser_error` mapping (`browser_routes.py:145`). |
| Chat / LLM SSE | `web_search_failed`, `event: error` | Typed events with status codes (`chat_routes.py:712,1358`; `llm_core.py:1109-1184`). |
| Browser WS | `{"type":"error"}` frame | Keep the stream alive where possible (`browser_routes.py:316-364`). |
