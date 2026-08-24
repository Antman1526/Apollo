# Apollo — Error Handling & Logging

Apollo's error philosophy: **degrade, don't die**. Optional subsystems that fail at
startup log and continue with a reduced feature set; proxies translate transport
failures into honest 502/503s instead of leaking stack traces; streaming routes carry
errors in-band over SSE because the HTTP status line is already spent by the time an
error happens; client disconnects save partial work instead of losing it; and a
best-effort audit ledger (`services/activity_ledger.py`) records every agent tool call
with an exit code and an undo payload, independent of whether the call itself
succeeded. Logging is plain stdlib `logging` with per-module loggers — there is no
structured-logging framework, no Sentry/OTel wiring in the open-source tree.

## 1. Logging setup

The root configuration lives at the top of `app.py`, before any router import:

```python
# app.py
import logging
...
# ========= LOGGING =========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)
```

`logging.basicConfig` attaches a `StreamHandler` to the root logger writing to
**stderr** (Python's default) — there is no `FileHandler`, no `RotatingFileHandler`,
and no log-file path baked into the app itself. Apollo relies on whatever launched
the process to capture that stream:

| Wrapper | What it does with stdout/stderr |
|---|---|
| `start-macos.sh` | plain `uvicorn app:app --host "$HOST" --port "$PORT"` — inherits the terminal's stdout/stderr; no redirection in the script itself |
| `build-macos-app.sh` (the thin `.app` launcher, not the self-contained PyInstaller bundle) | `LOG="$INSTALL_DIR/logs/apollo-app.log"`; starts uvicorn with `>>"$LOG" 2>&1 &`, and `die_gui` quotes `$LOG`'s path in its failure dialog if the server never becomes ready |
| `docker-compose.yml` | `apollo` service stdout is captured by the Docker log driver; `docker compose logs apollo` / `docker compose logs --tail=120 apollo` (documented in `README.md`) |
| `apollo-ui.service` (systemd) | inherited by journald; `journalctl -u apollo-ui` |
| `launch-windows.ps1` | `& $venvPy -m uvicorn app:app --host $BindHost --port $Port` — inherits the PowerShell console |

Every other module just grabs a named logger — there is no per-module
`basicConfig` call anywhere else in the tree:

```python
# e.g. services/localmodels/server_manager.py, src/observability.py, core/database.py
logger = logging.getLogger(__name__)
```

Two locations write to disk explicitly, outside the root logging stream:

```python
# services/localmodels/server_manager.py (_launch)
log_path = os.path.join(tempfile.gettempdir(), f"apollo-llama-{port}.log")
logf = open(log_path, "w")
logger.info("Starting llama-server: %s", " ".join(cmd))
try:
    proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
finally:
    # The child owns its own copy of the descriptor; keeping the
    # parent's open leaks one fd per model launch.
    logf.close()
```

Each launched `llama-server` process gets its own log file in the OS temp dir
(`/tmp/apollo-llama-<port>.log` on POSIX), named by the port it was assigned. This is
where the raw llama.cpp startup/inference output lands — if a model fails its health
check, the exception path below reads this file's tail into the error it raises.
The SearXNG sidecar similarly writes `logs/searxng.log` under the resolved Apollo
data directory (`README.md`: "Sidecar output is logged to `logs/searxng.log` and its
tail is visible in the status panel").

`src/runtime_paths.py` (`data_path`) resolves the base directory these files sit
under — `APOLLO_DATA_DIR`/`DATA_DIR` env override, else a platform-appropriate data
root, else the legacy in-repo `data/` folder for existing checkouts:

```python
# src/runtime_paths.py
def data_root(*, env=None, repo=None, platform=None, home=None) -> Path:
    env = os.environ if env is None else env
    for key in ("APOLLO_DATA_DIR", "DATA_DIR"):
        value = env.get(key)
        if value:
            return _configured_path(value)
    platform_root = platform_data_root(platform=platform, env=env, home=home)
    if _platform_root_is_activated(platform_root):
        return platform_root
    legacy = legacy_data_root(repo)
    if legacy.exists():
        return legacy
    return platform_root
```

### 1.1 `report_exception` — the structured half of logging

`src/observability.py` is the one place that imposes any structure on log output. It
exists specifically so handled-but-noteworthy failures carry a consistent severity
contract and never leak prompt/credential text into logs:

```python
# src/observability.py
Outcome = Literal["critical", "degraded", "best_effort"]
_SENSITIVE_CONTEXT_PARTS = ("token", "password", "secret", "body", "content")

def sanitize_context(context=None) -> dict:
    if context is None:
        return {}
    safe = {}
    for key, value in context.items():
        normalized = str(key).lower()
        if any(part in normalized for part in _SENSITIVE_CONTEXT_PARTS):
            raise ValueError(f"unsafe observability context key: {key}")
        safe[str(key)] = value
    return safe

def report_exception(logger, event, error, *, outcome, context=None) -> dict:
    if outcome not in {"critical", "degraded", "best_effort"}:
        raise ValueError(f"unknown observability outcome: {outcome}")
    safe_context = sanitize_context(context)
    record = {"event": event, "outcome": outcome,
               "error_type": type(error).__name__, **safe_context}
    message = "event=%s outcome=%s error_type=%s context=%s"
    args = (event, outcome, type(error).__name__, safe_context)
    if outcome == "critical":
        logger.error(message, *args)
    elif outcome == "degraded":
        logger.warning(message, *args)
    else:
        logger.debug(message, *args, exc_info=(type(error), error, error.__traceback__))
    return record
```

Three deliberate design choices: (1) `sanitize_context` **raises** rather than
silently dropping an unsafe key — a developer who passes `context={"prompt_body":
...}` gets a loud failure in dev instead of a quiet leak in prod; (2) the returned
record **excludes the exception message itself** — upstream library exceptions
sometimes interpolate the failing value (a URL with a credential, a request body)
into `str(error)`, so only the type name is logged; (3) `best_effort` failures log at
`debug` (with a full traceback via `exc_info`) rather than `warning`/`error`, so a
`best_effort` outcome doesn't page anyone but is still fully diagnosable when logging
is turned up. It's called from `services/localmodels/server_manager.py`,
`routes/chat_routes.py`, `routes/auth_routes.py`, and dozens of other call sites —
grep `report_exception(logger,` for the full list.

## 2. Launch-error UX for local models

`services/localmodels/server_manager.py` (`LocalModelServer`) is Apollo's llama.cpp
process supervisor. Its binary-resolution and launch-error paths are written to give
the user an actionable message instead of a bare `FileNotFoundError`, and — the part
worth calling out — they **distinguish a wrong configured path from no binary at
all**:

```python
# services/localmodels/server_manager.py
def find_binary(self) -> Optional[str]:
    # An explicitly configured path (Settings → AI or APOLLO_LLAMA_SERVER)
    # wins outright — and if it's set but wrong we return None rather than
    # silently auto-detecting a different binary than the one asked for.
    configured = get_llama_server_path()
    if configured:
        return configured if os.path.isfile(configured) else None
    for cand in _BIN_CANDIDATES:
        if os.sep in cand:
            if os.path.exists(cand) and os.access(cand, os.X_OK):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None
```

```python
# services/localmodels/server_manager.py (_launch)
def _launch(self, m: LocalModel) -> _Proc:
    binary = self.find_binary()
    if not binary:
        configured = get_llama_server_path()
        if configured:
            raise RuntimeError(
                f"Configured llama-server path does not exist: {configured}. "
                "Fix it in Settings → AI → Local Models (or unset "
                "APOLLO_LLAMA_SERVER to auto-detect)."
            )
        hint = (
            "winget install llama.cpp (or download a release build), then set "
            "the binary path in Settings → AI → Local Models"
            if os.name == "nt"
            else "e.g. `brew install llama.cpp`, or build it via the Cookbook"
        )
        raise RuntimeError(f"llama-server not found. Install llama.cpp ({hint}).")
```

Two distinct, deliberately different error messages come out of the same
`if not binary:` branch:

1. **Wrong binary path** (`get_llama_server_path()` returns something, but
   `find_binary()` returned `None` because `os.path.isfile(configured)` was false) →
   *"Configured llama-server path does not exist: `<path>`. Fix it in Settings → AI →
   Local Models (or unset `APOLLO_LLAMA_SERVER` to auto-detect)."* This tells the
   user their setting is stale/typo'd and gives them the exact two ways out — edit
   the setting, or clear the env override so auto-detection resumes.
2. **Not installed at all** (no configured path, and none of `_BIN_CANDIDATES`
   resolved) → *"llama-server not found. Install llama.cpp (\<platform hint\>)."* The
   hint itself branches on `os.name == "nt"` — Windows users get a `winget install`
   suggestion plus the Settings path, POSIX users get a `brew install` / Cookbook-build
   suggestion. Neither message is the generic "file not found" a raw
   `FileNotFoundError` would have produced from `subprocess.Popen`.

`find_binary()`'s own comment states the design rule explicitly: an explicitly
configured path "wins outright — and if it's set but wrong we return `None` rather
than silently auto-detecting a different binary than the one asked for". Silent
auto-detect-around-a-typo would be a worse UX than a clear error, because the user
would end up running a model they didn't choose.

Health-wait failures during launch go through `report_exception` with
`outcome="critical"` and are cleaned up (terminate the half-started process) before
re-raising:

```python
# services/localmodels/server_manager.py (_launch, continued)
try:
    self._wait_health(base_url, proc, log_path, timeout=self._health_timeout_for(m))
except Exception as error:
    report_exception(logger, "local_model_health_wait_failed", error,
                      outcome="critical", context={"model_id": m.id})
    try:
        proc.terminate()
    except Exception as cleanup_error:
        ...
    raise
```

`_wait_health` polls the process's `/health` endpoint and raises
`TimeoutError("llama-server did not become healthy in time")` if the model never
comes up — that message, plus the `/tmp/apollo-llama-<port>.log` tail, is what
surfaces to the frontend when a GGUF fails to load (OOM, wrong quant for the
hardware, corrupt file).

## 3. Frontend error surfacing (toast/notification system)

`static/js/ui.js` owns the single toast element (`#toast` in `index.html`) used
app-wide for both informational and error notices. `showError` is the error-specific
entry point — 3-second auto-dismiss, red styling via the `.error` class:

```javascript
// static/js/ui.js
export function showError(msg) {
  if (!toastEl) {
    toastEl = document.getElementById('toast');
  }
  _wireToastSwipe(toastEl);
  toastEl.textContent = msg;
  toastEl.classList.add('error');
  toastEl.style.left = '';
  toastEl.style.transform = '';
  toastEl.classList.remove('exiting');
  toastEl.classList.add('show');
  clearTimeout(toastEl._hideTimer);
  toastEl._hideTimer = setTimeout(() => {
    toastEl.classList.add('exiting');
    toastEl.classList.remove('show');
  }, 3000);
}
```

`showToast` (the sibling, non-error function a few lines above) supports an optional
**action button** — used for things like an Undo affordance tied directly to the
activity ledger (section 6 below). It flips `pointer-events` on so the button is
clickable even though the toast itself is `pointer-events: none` by default (so
toasts never block clicks on the content beneath them):

```javascript
// static/js/ui.js (showToast, action-button branch)
btn.style.cssText = 'padding:2px 10px;border:1px solid var(--fg);border-radius:4px;' +
  'background:none;color:var(--fg);cursor:pointer;font-size:12px;pointer-events:auto;' +
  'display:inline-flex;align-items:center;';
btn.addEventListener('click', (e) => {
  e.stopPropagation();
  e.preventDefault();
  toastEl.classList.remove('show');
  onAction();
});
```

Every feature module imports `showError`/`showToast` from `ui.js` rather than
rolling its own notification UI — `static/js/documentLibrary.js`,
`static/js/document.js`, `static/js/presets.js`, `static/js/voiceRecorder.js`, and
`static/js/settings.js` all call it, e.g.:

```javascript
// static/js/document.js
} catch { if (uiModule) uiModule.showError('Export failed: ' + e.message); }
```

```javascript
// static/js/settings.js
uiModule.showError ? uiModule.showError('Export failed') : alert('Export failed');
```

That last line is the fallback pattern used in a couple of call sites where the
`uiModule` reference might not be wired yet — `alert()` as a last resort, never as
the primary path.

## 4. SSE streaming — client-side error and disconnect handling

Chat streaming (`static/js/chat.js`) uses the standard `fetch` + `AbortController` +
`ReadableStream.getReader()` trio. There is **no explicit `reader.cancel()` call**
anywhere in the client (`grep -rn "reader.cancel" static/js/` returns nothing) — the
convention Apollo actually uses is to abort the underlying `fetch` via
`AbortController.abort()`. Aborting the fetch's signal causes the browser to reject
the in-flight `reader.read()` promise, which is functionally equivalent to
cancelling the reader directly, without a second explicit call.

### 4.1 AbortController setup and timeout race

```javascript
// static/js/chat.js
const abortCtrl = new AbortController();
abortCtrl._reason = '';
currentAbort = abortCtrl;

// Timeout: 6 min for research and agent mode, 3 min otherwise
const timeoutMs = el('research-toggle').checked || _isAgent ? RESEARCH_TIMEOUT_MS : DEFAULT_TIMEOUT_MS;
const timeoutId = setTimeout(() => {
  if (!abortCtrl.signal.aborted) {
    timedOut = true;
    abortCtrl._reason = 'timeout';
    abortCtrl.abort();
  }
}, timeoutMs);
```

`abortCtrl._reason` is an ad hoc field bolted onto the standard `AbortController` —
not part of the Fetch spec — used purely so the `catch` block downstream can tell
*why* the signal fired (`'timeout'`, `'offline'`, `'recovery'`, or unset for an
explicit user Stop / navigation abort) and render a different message for each:

```javascript
// static/js/chat.js (fetch call)
const res = await fetch(`${API_BASE}/api/chat_stream`, {
  method: 'POST',
  body: fd,
  headers: { 'X-Tz-Offset': String(_tzOffsetMin) },
  signal: abortCtrl.signal
});
```

### 4.2 Reading the stream and handling a non-OK response

```javascript
// static/js/chat.js
if (!res.ok) {
  if (res.status === 404) {
    // Session was deleted (e.g. by AI) — reload and go to welcome
    holder.remove();
    if (sessionModule) await sessionModule.loadSessions();
    return;
  }
  let errText = `Error ${res.status}`;
  try {
    const errBody = await res.text();
    const m = errBody.match(/"message"\s*:\s*"([^"]+)"/);
    if (m) errText = m[1].replace(/\\"/g, '"');
    else if (errBody.length < 200) errText = errBody;
  } catch {}
  // Auto-switch to chat mode for tool-related errors
  if (errText.includes('tool') || errText.includes('auto')) {
    errText = 'This model doesn\'t support agent tools — switched to Chat mode. Try again.';
    ...
  }
  typewriterInto(holder.querySelector('.body'), errText);
  enableResearchBtn();
  return;
}
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = '';
```

The 404-on-session-deleted branch and the "model doesn't support tools → auto-switch
to chat mode" branch are both UX recoveries for server states the client can't
prevent — a session removed by an agent action mid-stream, or a model picked that
turns out not to support tool calling.

### 4.3 Interpreting an abort by reason, once the stream ends

```javascript
// static/js/chat.js
if (currentAbort && currentAbort.signal.aborted) {
  const abortReason = currentAbort._reason || '';
  if (timedOut || abortReason === 'timeout') {
    const timeoutMsg = _isAgent
      ? 'Agent response timed out. Try again, switch to a faster model, or reduce tool usage.'
      : 'Response timed out. Try again.';
    ...
    currentAbort = null;
    return;
  }
  if (abortReason === 'offline') {
    const offlineMsg = 'Endpoint offline — switch model or try again.';
    ...
  }
  if (abortReason === 'recovery') {
    const recoveryMsg = 'Streaming was interrupted after the tab went inactive. Partial output was preserved.';
    ...
  }
  // User-initiated stop (or browser navigation abort).
  // Stopped before any text arrived — keep the bubble as a
  // "Cancelled by user" record (so it survives a refresh).
  if (holder && !accumulated) {
    _renderCancelledBubble(holder);
  }
}
```

### 4.4 The Stop button: abort the client stream WITHOUT killing a detached server run

```javascript
// static/js/chat.js
// stopServer=true ONLY for an explicit user Stop. The run is now DETACHED
// (survives tab close / navigation), so the generic abort used by cleanup
// paths (session switch, delete, reader teardown on tab close) must NOT stop
// the server run — otherwise closing the tab would kill the background task,
// defeating the whole point. Only the Stop button cancels the server run.
export function abortCurrentRequest(stopServer = false) {
  if (currentAbort) {
    currentAbort.abort();
    // Don't set to null here - let catch block handle it
  }
  if (stopServer) {
    try {
      const _sid = _streamSessionId
        || (window.sessionModule && window.sessionModule.getCurrentSessionId && window.sessionModule.getCurrentSessionId());
      if (_sid) {
        fetch(`/api/chat/stop/${encodeURIComponent(_sid)}`, { method: 'POST', credentials: 'same-origin' }).catch(() => {});
      }
    } catch (_) {}
  }
}
```

This is a two-tier disconnect model: aborting the local `AbortController` only tears
down *this tab's* SSE subscription; killing the server-side run requires the
explicit `POST /api/chat/stop/<session_id>` call, fired only when `stopServer` is
`true` (wired to the Stop button click, not to tab-close/session-switch cleanup).
`static/js/compare/stream.js` uses the equivalent pattern for the model-compare
panes — a per-pane `AbortController`, an idle-timeout `setTimeout` that re-arms on
every chunk (`_resetIdleTimeout()`), and a `finally` block that always clears the
timer and nulls the controller regardless of outcome.

## 5. SSE — server-side error events and disconnect handling

### 5.1 In-band error frames

Once a stream has begun, the HTTP status is spent — so `src/llm_core.py` delivers
transport errors as named SSE events carrying a status code in the JSON payload,
which the frontend renders inline instead of via the (already-sent) HTTP status:

```python
# src/llm_core.py (stream_llm)
except httpx.ConnectError:
    _cooled = _mark_host_dead(target_url)
    ...
    yield f'event: error\ndata: {json.dumps({"error": f"Cannot reach {_host_key(target_url)}", "status": 503})}\n\n'
except httpx.ReadTimeout:
    yield f'event: error\ndata: {json.dumps({"error": "Read timeout", "status": 504})}\n\n'
except httpx.RequestError:
    yield f'event: error\ndata: {json.dumps({"error": "Network error", "status": 502})}\n\n'
```

`stream_llm_with_fallback` watches for an `event: error` chunk that arrives
**before any content** and retries the next fallback endpoint in the configured
chain; an error that arrives mid-stream passes through unchanged instead — a
half-delivered answer must not silently restart from scratch.

### 5.2 Detecting client disconnect: `CancelledError` / `GeneratorExit`

When the browser aborts (Stop button, tab close, navigation, or the client-side
timeout above), FastAPI cancels the async generator backing the `StreamingResponse`.
`routes/chat_routes.py` catches this in both chat mode and agent mode, saves the
partial assistant response, and — the detail worth keeping — wraps the save in its
**own** nested `try` so a save failure can't mask the original cancellation (which
would skip the outer `finally` and leave `_active_streams` with a stale entry):

```python
# routes/chat_routes.py (stream_with_save)
except (asyncio.CancelledError, GeneratorExit):
    # Guard the save so an exception inside add_message / save_sessions
    # can't mask the original CancelledError (which prevented the outer
    # finally from running and left _active_streams with a stale entry).
    try:
        if full_response:
            logger.info("Client disconnected mid-stream for session %s, saving partial response (%d chars)", session, len(full_response))
            _stopped_content2, _stopped_md2 = clean_thinking_for_save(full_response, {"stopped": True, "model": sess.model})
            sess.add_message(ChatMessage("assistant", _stopped_content2, metadata=_stopped_md2))
            if not incognito:
                session_manager.save_sessions()
    except Exception:
        logger.exception("Failed to save partial response on disconnect (session %s)", session)
    raise
finally:
    _active_streams.pop(session, None)
```

The saved message is tagged `{"stopped": True}` in its metadata so the UI can render
it as a cancelled/partial turn on reload.

### 5.3 Detached runs: the SSE connection is a subscriber, not the run itself

The agent-mode path takes disconnect tolerance one step further: the actual
generation runs as a **detached background task** (`agent_runs.start`), and the
`StreamingResponse` merely subscribes to its output. Closing the tab does not kill
the run — the assistant message still gets saved on completion, and the client can
reconnect via `GET /api/chat/resume/<session_id>`:

```python
# routes/chat_routes.py
async def _safe_stream() -> AsyncGenerator[str, None]:
    """Wrapper that guarantees _active_streams cleanup even if stream_with_save
    raises before reaching a mode-specific finally block."""
    try:
        async for chunk in stream_with_save():
            yield chunk
    finally:
        _active_streams.pop(session, None)

# Run the stream as a DETACHED background task so it survives the client
# closing the tab / navigating away (true terminal-agent behavior). The
# SSE response just subscribes (replay buffered output + live); dropping
# the SSE only removes a subscriber — the run keeps going and saves the
# assistant message on completion regardless. Reconnect via /api/chat/resume.
agent_runs.start(session, _safe_stream())
return StreamingResponse(agent_runs.subscribe(session), media_type="text/event-stream")

@router.get("/api/chat/resume/{session_id}")
async def chat_resume(request: Request, session_id: str) -> StreamingResponse:
    _verify_session_owner(request, session_id)
    if not agent_runs.is_active(session_id):
        raise HTTPException(404, "No active run for this session")
    return StreamingResponse(agent_runs.subscribe(session_id), media_type="text/event-stream")
```

This is exactly the server-side counterpart of the client's `stopServer=false`
default in §4.4: an ordinary abort only drops the SSE subscription; only the
explicit `POST /api/chat/stop/<session_id>` (which calls into `agent_runs`
separately) actually cancels the underlying task.

### 5.4 Dead-host cooldown (repeated-failure suppression)

A connect failure marks the target host "dead" so subsequent calls fail instantly
instead of paying the full connect-timeout again — but only after **consecutive**
failures, so one transient blip doesn't lock out a healthy endpoint:

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

The lock exists because the sync `llm_call()` path runs inside FastAPI's
threadpool while `llm_call_async()` runs on the event loop — an earlier unlocked
read-modify-write on these dicts lost failure counts under concurrent access
(referenced in-repo as issue #659). Any successful call resets both maps via
`_clear_host_dead`. While cooled, non-streaming calls raise
`HTTPException(503, "...marked unreachable (cooldown active)")` and streaming calls
emit the `event: error` / `status: 503` frame shown above.

## 6. The activity ledger as an audit + undo mechanism

`services/activity_ledger.py` is an append-only audit log of every tool call the
agent makes, persisted to the `activity_events` table (`core/database.py`). It
exists so the user has "a searchable 'computer history' (what the agent ran, wrote,
fetched, and when) plus per-write undo," and it is explicitly best-effort: a ledger
write failure must never break the tool call it's recording.

### 6.1 Recording an event (with exit code)

```python
# services/activity_ledger.py
def record_event(
    *, tool: str, summary: str = "", input_text: str = "",
    result: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None,
    owner: Optional[str] = None, duration_ms: Optional[int] = None,
    path: Optional[str] = None, before: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert one ledger row. Never raises; returns the event id or None."""
    if not enabled() or tool in _SKIP_TOOLS:
        return None
    try:
        result = result or {}
        before = before or {}
        db = SessionLocal()
        try:
            ev = ActivityEvent(
                id=str(uuid.uuid4()),
                session_id=session_id, owner=owner, tool=tool,
                summary=_clip(summary, 500),
                input_preview=_clip(input_text, INPUT_PREVIEW_CHARS),
                output_preview=_clip(result.get("output") or result.get("error") or "", OUTPUT_PREVIEW_CHARS),
                exit_code=result.get("exit_code"),
                duration_ms=duration_ms, path=path,
                before_content=before.get("before_content"),
                before_existed=before.get("before_existed"),
            )
            db.add(ev)
            db.commit()
            _prune(db)
            return ev.id
        finally:
            db.close()
    except Exception:
        logger.exception("activity ledger: record failed (ignored)")
        return None
```

`exit_code` is pulled straight from the tool's own result dict (`bash`'s process
exit status, or `0`/nonzero for other tools), so `activity_events` is queryable by
success/failure — used by `recent_tool_events(..., only_success=True)` for
pattern-mining features that must only learn from commands that actually worked:

```python
# services/activity_ledger.py
def recent_tool_events(days: int = 7, tools: tuple = ("bash",), only_success: bool = True):
    ...
    q = db.query(ActivityEvent).filter(
        ActivityEvent.tool.in_(list(tools)),
        ActivityEvent.created_at >= cutoff,
    )
    if only_success:
        q = q.filter(ActivityEvent.exit_code == 0)
    return [{"session_id": r.session_id, "tool": r.tool, "input": r.input_preview}
            for r in q.order_by(ActivityEvent.created_at.asc()).all()]
```

`_SKIP_TOOLS = {"read_file", "manage_memory"}` — pure reads of the agent's own
state are excluded from the ledger, "buries the actions the user actually cares
about ('what did it DO?')".

### 6.2 Undo: snapshot-before-write, restore-on-demand

Before a write-capable tool touches a file, `capture_before(path)` snapshots its
prior content (bounded at `BEFORE_CONTENT_CHARS = 512_000` — larger files are
recorded as "existed" but without an undo payload, so the DB can't be blown up by
one huge file):

```python
# services/activity_ledger.py
def capture_before(path: str) -> Dict[str, Any]:
    """Snapshot a file's pre-write state for undo. Returns {} on any failure."""
    try:
        if not os.path.exists(path):
            return {"before_existed": False, "before_content": ""}
        if os.path.getsize(path) > BEFORE_CONTENT_CHARS:
            return {"before_existed": True, "before_content": None}  # too big to undo
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {"before_existed": True, "before_content": f.read()}
    except OSError:
        return {}
```

An event is `undoable` only when it has a `path`, a non-`None` `before_content`, and
hasn't already been undone:

```python
# services/activity_ledger.py (_to_dict)
"undoable": bool(r.path and r.before_content is not None and not r.undone),
"undone": bool(r.undone),
```

`undo_session(session_id)` rolls back **every** still-undoable write from one
session as a bundle, newest-first (so a file written twice steps back through each
snapshot in order and lands on its true original content); per-event failures don't
abort the whole bundle:

```python
# services/activity_ledger.py
def undo_session(session_id: str) -> Dict[str, Any]:
    """Roll back every still-undoable file write from one session, as a bundle.
    Undoes newest-first so a file written twice steps back through each
    snapshot and lands on its original content. Per-event failures don't
    abort the bundle — the result reports both counts."""
    ...
    rows = (db.query(ActivityEvent)
              .filter(ActivityEvent.session_id == session_id,
                      ActivityEvent.undone.is_(False),
                      ActivityEvent.path.isnot(None),
                      ActivityEvent.before_content.isnot(None))
              .order_by(ActivityEvent.created_at.desc()).all())
    ...
```

Each successful undo is itself recorded as a new `tool="undo"` ledger row
(`result={"output": action, "exit_code": 0}`), so the undo action is auditable too —
the ledger records its own corrections.

### 6.3 Bounded growth

`_prune(db)` runs after every insert and deletes the oldest rows once the table
exceeds `activity_ledger_max_events` (default `DEFAULT_MAX_EVENTS = 10_000`,
configurable via settings):

```python
# services/activity_ledger.py
def _prune(db) -> None:
    try:
        cap = int(get_setting("activity_ledger_max_events", DEFAULT_MAX_EVENTS))
        count = db.query(ActivityEvent).count()
        if count > cap:
            old = (db.query(ActivityEvent).order_by(ActivityEvent.created_at.asc())
                     .limit(count - cap).all())
            for row in old:
                db.delete(row)
            db.commit()
    except Exception:
        logger.exception("activity ledger: prune failed (ignored)")
```

This gives Apollo a bounded audit trail without an operator having to manage log
rotation for it separately, and it directly mitigates the "agent has full
filesystem/shell reach with no sandbox" gap documented in
`docs/recreate/14-security-implementation-best-practices.md` — the ledger is the
compensating control (visibility + reversibility) for a capability the app does not
otherwise confine.

## 7. Known native-fault lesson: libmagic on Windows

`src/upload_handler.py`'s `UploadHandler.__init__` originally tried, on every
platform, to construct a `magic.Magic(mime=True)` file-type detector, falling back
to basic detection inside a plain `try/except Exception`. That fallback **never
engaged on Windows** — the pinned dependency is `python-magic` (a ctypes wrapper
around the native `libmagic` shared library), not `python-magic-bin`, and Windows
ships no `libmagic` at all. ctypes' DLL search/load in that situation is a **native
fault** — a Windows SEH access violation, or (nondeterministically, depending on
loader-lock timing) an indefinite hang — neither of which is a Python exception, so
`except Exception` cannot catch it. In CI this manifested as
`tests/test_security_regressions.py::test_upload_resolver_rejects_cross_owner_upload_ids`
stalling 16+ minutes on `windows-latest` with no forward progress, and in a real
desktop install (Apollo runs natively on Windows, not just in CI) it was a genuine
crash/hang on first upload.

The fix skips the `magic` import entirely on Windows, going straight to the
basic-detection fallback that was supposed to be reached anyway:

```python
# src/upload_handler.py (UploadHandler.__init__)
# Initialize file detector. The pinned dependency is `python-magic`
# (a ctypes wrapper around libmagic), not `python-magic-bin` — Windows
# ships no libmagic, so ctypes' DLL search/load there is a native
# fault, not a Python exception: it has produced both a "Windows
# fatal exception: access violation" crash and (nondeterministically,
# depending on loader-lock timing) an indefinite hang, neither of
# which `except Exception` can catch. Skip the attempt entirely on
# Windows and use the basic-detection fallback below instead.
if os.name == "nt":
    self.file_detector = None
else:
    try:
        import magic
        self.file_detector = magic.Magic(mime=True)
    except Exception:
        self.file_detector = None
        logger.warning("python-magic not available, falling back to basic detection")
```

The general lesson this encodes for the rest of the codebase: **`except Exception`
is not a safety net against a foreign-library crash.** A ctypes/DLL/native-extension
load can fault below the interpreter, and the only reliable mitigation is to avoid
the call entirely on the platform where it's known to be unsafe — exactly the
`os.name == "nt"` pattern used here, and in `src/embeddings.py` /
`app.py` (Windows symlink handling for HuggingFace model downloads) for a related
class of Windows-only native/filesystem quirks.

## 8. HTTPException conventions

- **400** — malformed input caught up front, e.g. `raise HTTPException(400, "command
  is required for stdio transport")` (`routes/mcp_routes.py`), or the regex-validated
  filename check in `app.py`'s `/api/generated-image/{filename}` route
  (`raise HTTPException(status_code=400, detail="Invalid filename")`).
- **403** — `core/middleware.py:require_admin` raises `HTTPException(403, "Admin
  only")`; `routes/auth_routes.py:_require_admin_user` raises the same for the
  auth-router's own admin routes (see
  `docs/recreate/14-security-implementation-best-practices.md` for why it does not
  simply call `require_admin`).
- **404** — both genuine "not found" and "exists but you don't own it" cases use 404
  rather than 403, so an unauthenticated/unauthorized caller can't distinguish
  "doesn't exist" from "isn't yours" by response code
  (`raise HTTPException(status_code=404, detail="Image not found")` in `app.py`).
- **429** — rate limiting on `/api/auth/login`, `/api/auth/signup`, `/api/auth/setup`
  via `src.rate_limiter.RateLimiter` (`raise HTTPException(429, "Too many requests —
  try again later")`).
- **502/503** — proxy transport failures (`routes/lmproxy_routes.py`,
  `routes/paperclip_routes.py`) and feature-disabled/dependency-unreachable states
  (no warm local model, ChromaDB down).
- **504** — a global `_RequestTimeoutMiddleware` in `app.py` aborts any
  non-exempt request after `REQUEST_HARD_TIMEOUT` seconds so a single hung
  `subprocess.run` or a missing-timeout `httpx` call can't lock up the whole server;
  streaming, research, upload, and probe routes are whitelisted via
  `_TIMEOUT_EXEMPT_PREFIXES`.

## 9. Registered exception handlers, startup degradation, and quiet retries

**Custom exception types** (`src/exceptions.py`: `SessionNotFoundError`,
`InvalidFileUploadError`, `LLMServiceError`, `WebSearchError` — each carries a
`message` plus one identifying field, e.g. `SessionNotFoundError.session_id`) get
dedicated `app.py` handlers so raising them anywhere in a route body produces a
consistent typed JSON body instead of an unhandled-exception 500:

```python
# app.py
@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(status_code=404, content={"error": "SESSION_NOT_FOUND", "message": str(exc)})

@app.exception_handler(InvalidFileUploadError)
async def invalid_file_upload_handler(request: Request, exc: InvalidFileUploadError):
    return JSONResponse(status_code=400, content={"error": "INVALID_FILE_UPLOAD", "message": str(exc)})

@app.exception_handler(LLMServiceError)
async def llm_service_error_handler(request: Request, exc: LLMServiceError):
    return JSONResponse(status_code=502, content={"error": "LLM_SERVICE_ERROR", "message": str(exc)})

@app.exception_handler(WebSearchError)
async def web_search_error_handler(request: Request, exc: WebSearchError):
    return JSONResponse(status_code=502, content={"error": "WEB_SEARCH_ERROR", "message": str(exc)})
```

Each body carries a machine-readable `error` code plus a human-readable `message`,
giving the frontend a stable contract instead of parsed prose.

**Startup degradation.** `app.py` registers ~40 routers through a labeled guard so a
broken subsystem's construction failure names itself instead of a bare traceback:

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

Optional dependencies degrade with a logged warning instead of crashing the process:
**Vector RAG** — unreachable ChromaDB at boot → `get_rag_manager()` returns `None`,
`app.py` logs *"Vector document RAG not available at startup..."*, personal-doc
routes return a clean 503 rather than busy-retrying. **Local model scan** — runs on
a daemon thread (`services/localmodels/lifecycle.py`):
`logger.warning("Local model startup scan failed: %s", e)`. **Registry sync**
(`services/localmodels/registry.py`, comment `# never let a scan crash the caller`):
`logger.warning("Failed to sync managed local endpoint: %s", e)`. **Node
bootstrap/Paperclip** — falls back to a PATH-resolved Node, logging `"Node bootstrap
failed (%s); will try a system Node"`. **LOCALHOST_BYPASS** logs a prominent
`logger.warning(...)` at import time so the insecure mode is visible in every boot
log, not just discoverable by reading `.env`.

**Warn-once-then-debug retries.** Long-running reconnect loops must not spam the log
forever. The Paperclip collector (`services/paperclip/collector.py`) warns on the
*first* connection failure, downgrades repeats to `debug`, and resets on success:

```python
# services/paperclip/collector.py (run loop)
except Exception as exc:
    if not self._warned:
        logger.warning("Paperclip collector unavailable (will retry): %s", exc)
        self._warned = True
    else:
        logger.debug("Paperclip collector retry failed: %s", exc)
```

Reconnects use capped exponential backoff (`min_backoff`→`max_backoff`, default
1s→60s, doubling per attempt); a session alive >30s resets the backoff, and
`self._warned` resets to `False` on success so the next outage warns once again.

## 10. Where to look when X breaks

| Symptom | First place to look |
|---|---|
| App won't boot, names a subsystem | `RuntimeError("Failed to build <Label> routes")` — the label maps to a `build_and_include_router` call in `app.py` |
| Chat returns instant 503 "cooldown active" | Dead-host cooldown (`src/llm_core.py`, §5.4); wait 20s or fix the endpoint, a success auto-clears it |
| Local model won't start / times out | `/tmp/apollo-llama-<port>.log` — the health-wait `TimeoutError` embeds its tail; `services/localmodels/server_manager.py` |
| "llama-server not found" vs "Configured path does not exist" | §2 — read the exact message; the two cases have different fixes (install vs fix the setting) |
| Model picker missing local models | startup-scan warning in app log; `GET /api/localmodels` forces a rescan; `services/localmodels/lifecycle.py` |
| `/paperclip` 502/503 | `GET /api/paperclip/status` (`reachable`, `collector`, `agent_workbench` fields); is the compose profile running? |
| Floor / event feed shows nothing | Collector warn-once line in the app log (§9); a `PAPERCLIP_EVENTS_TOKEN` mismatch returns 401 on ingest |
| Random 504s on a slow endpoint | `_RequestTimeoutMiddleware` in `app.py` — add the path to `_TIMEOUT_EXEMPT_PREFIXES` or raise `REQUEST_HARD_TIMEOUT` |
| Windows-only hang/crash on first upload | The libmagic native-fault class (§7) — check whether the fix's `os.name == "nt"` guard regressed |
| Stream shows "Cancelled by user" with no text | Client-side abort with no `_reason` (§4.3) — either an explicit Stop click or a browser navigation abort |
| A file the agent wrote needs reverting | Activity ledger (§6) — `undo_session`/`undo_event`; check `undoable` on the event first (large files > 512KB have no snapshot) |
| Desktop app dies silently (macOS bundle) | `logs/apollo-app.log` under the install dir (`build-macos-app.sh`'s `$LOG`); the launcher's `die_gui` failure dialog quotes this exact path |
