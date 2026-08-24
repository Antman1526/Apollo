# Apollo — AI Review Context Pack, Part 2: Dataflow, Dependencies, Pain Points

Continuation of Part 1. This document traces data through the system end to
end, maps external dependencies and the DB schema, then documents concrete,
evidenced pain points — quoted from the code, not speculated.

## 1. Chat message dataflow, end to end

**Hop 1 — browser → server.** The frontend posts a `FormData` body to
`POST /api/chat_stream` and consumes the response as SSE (fetch + a manual
`ReadableStream` reader is the pattern used across `static/js/chat*.js`, not
`EventSource`, because the request is a POST with a body). `static/js/chatStream.js`
is the SSE event *interpreter* on the client (see Part 1 §10) — it does not
issue the fetch itself (that lives in `chat.js`/`chat/` submodules) but reacts
to `delta`, `tool_start`, `tool_output`, `ui_control`, `doc_stream_*`, and
`metrics` event types.

**Hop 2 — route resolves session and mode.** `routes/chat_routes.py:419
chat_stream()` resolves the session via `session_manager.get_session(session)`
(Part 2 §2 below), verifies ownership, resolves the model, and decides
chat-vs-agent mode, including an intent-based auto-escalation:

```python
# routes/chat_routes.py:474-477
if chat_mode == "chat" and isinstance(message, str) and _message_needs_tools(message):
    chat_mode = "agent"
    auto_escalated = True
    logger.info("chat→agent auto-escalation: message matched tool-intent pattern")
```

**Hop 3 — detached background run.** As documented in Part 1 §9, the route
does not stream directly; it starts `stream_with_save()` as a background task
via `agent_runs.start(session, _safe_stream())` and the HTTP response merely
`agent_runs.subscribe(session)`s to it. This decouples the agent's lifetime
from the HTTP connection.

**Hop 4 — agent loop.** For agent mode, `stream_agent_loop()` (`src/agent_loop.py`,
Part 1 §3) runs the RAG tool-selection → system-prompt assembly → per-round
LLM-call loop.

**Hop 5 — LLM call.** Each round calls `stream_llm_with_fallback()`
(`src/llm_core.py`), which normalizes provider-specific streaming formats
(OpenAI SSE, Anthropic SSE, Ollama NDJSON, llama.cpp's own format) into the
same internal `data: {"delta": ...}` / `{"type": "tool_calls", ...}` /
`{"type": "usage", ...}` envelope the agent loop consumes.

**Hop 6 — tool dispatch.** When the model's output (or native `tool_calls`)
resolves to one or more `ToolBlock`s, `execute_tool_block()`
(`src/tool_execution.py:735`) dispatches by `tool_type` — either to an
in-process handler (`_direct_fallback`, bash/python/web_fetch/etc.) or to an
MCP server (`_call_mcp_tool`). Results stream back through the same SSE
channel as `tool_start`/`tool_progress`/`tool_output` events (Part 1 §3.2).

**Hop 7 — completion, save, resume.** On `data: [DONE]`, the route persists
the assistant message via `save_assistant_response()` and runs
`run_post_response_tasks()` (memory extraction, skill extraction, webhook
firing) — all *after* the SSE `[DONE]` marker, so these don't add latency to
the visible response but do mean a client that disconnects right at `[DONE]`
could theoretically miss post-processing side effects tied to that specific
request path (mitigated by the detached-background-task design: the save
still happens because the generator isn't cancelled, only unsubscribed).

## 2. Settings persistence flow

Settings are **not** a DB table — they're a single JSON file with an
in-process TTL cache and atomic writes:

```python
# src/settings.py:223-250
def load_settings() -> dict:
    """Load settings merged with defaults. Always returns a complete dict."""
    global _settings_cache
    now = time.monotonic()
    if _settings_cache and (now - _settings_cache[0]) < _CACHE_TTL:
        return _settings_cache[1]
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("settings must be an object")
        merged = {**DEFAULT_SETTINGS, **saved}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        merged = dict(DEFAULT_SETTINGS)
    _settings_cache = (now, merged)
    return merged


def save_settings(settings: dict):
    """Persist settings to disk (atomic; see core.atomic_io)."""
    from core.atomic_io import atomic_write_json
    atomic_write_json(SETTINGS_FILE, settings, indent=2)
    _invalidate_caches()


def get_setting(key: str, default: Any = None) -> Any:
    """Read a single setting value."""
    return load_settings().get(key, default)
```

`is_setting_overridden(key)` (lines 253-266) exists specifically because the
merge-with-defaults strategy makes "explicitly set to the default value"
indistinguishable from "never set" through `get_setting` alone — several
call sites (e.g. the agent's context-token-budget logic in `agent_loop.py`)
need that distinction to decide whether to apply an adaptive default or
respect an explicit user choice. A `_PER_USER_KEYS` set (line 273-) layers
per-user overrides (currently `vision_model`, `vision_enabled`, and related
image-model keys) on top of the single global file — an unusual half-multi-
tenant model given everything else is effectively single-admin-owned.

## 3. Memory write/recall flow

Memory is a **dual store**: a flat JSON-backed `MemoryManager`
(`src/memory.py`) holding structured entries (text, source, category,
timestamps, use counts), plus a ChromaDB-backed `MemoryVectorStore`
(`src/memory_vector.py`) for semantic recall — the two are kept in sync by
callers, not atomically:

```python
# src/memory.py — public surface (signatures)
class MemoryManager:
    def extract_memory_from_chat(self, chat_history, session_id=None) -> List[Dict]: ...
    def load(self, owner: str = None) -> List[Dict]: ...
    def add_entry(self, text, source="user", category="fact", owner=None) -> Dict: ...
    def find_duplicates(self, text, entries=None) -> List[Dict]: ...
    def get_relevant_memories(self, query, memories, threshold=0.05, max_items=8): ...
```

```python
# src/memory_vector.py — public surface (signatures)
class MemoryVectorStore:
    def add(self, memory_id: str, text: str): ...
    def remove(self, memory_id: str): ...
    def search(self, query: str, k: int = 8) -> List[Dict]: ...
    def find_similar(self, text: str, threshold: float = 0.92) -> Optional[str]: ...
    def rebuild(self, memories: List[Dict]): ...
```

`get_relevant_memories` in `MemoryManager` uses a **text-similarity**
fallback (`get_text_similarity`, token-overlap based, `threshold=0.05`) while
`MemoryVectorStore.search` uses real embeddings — two different relevance
algorithms exist side by side, and `src/app_initializer.py` (Part 1 §2) shows
the vector store is optional/degradable: if ChromaDB or the embedder is
unavailable, memory silently falls back to the cruder text-similarity path
with no user-visible signal that recall quality has degraded.

## 4. Local model lifecycle

`services/localmodels/` contains `scanner.py` (walks configured directories
for `.gguf` files, classifies `kind` — chat/embedding/unsupported — from
metadata), `config.py` (resolves `get_llama_server_path()` /
`get_local_model_dirs()` from settings/env), and `server_manager.py` (Part 1
§6). There's no formal state-machine enum; process state is read directly off
`subprocess.Popen.poll()` (`None` = running) inside `LocalModelServer.status()`:

```python
# services/localmodels/server_manager.py:319-328
def status(self) -> dict:
    with self._lock:
        out = {}
        for slot in (self._chat, self._embed):
            if slot:
                out[slot.model_id] = {
                    "name": slot.name, "kind": slot.kind, "port": slot.port,
                    "running": slot.proc.poll() is None, "base_url": slot.base_url,
                }
        return out
```

The Cookbook subsystem (`services/hwfit`, `routes/cookbook_routes.py`, 2,159
lines) layers a much richer lifecycle (loading/ready/idle/error, tmux-session
tracking for remote GPU boxes, download progress) on top of this for
vLLM/SGLang/Diffusers serving — that is a separate, larger subsystem from the
single-warm-model `llama-server` path described in Part 1 §6.

## 5. External services and dependency graph

| Service | Role | Managed how |
|---|---|---|
| `llama-server` (llama.cpp) | Local GGUF chat/embedding inference | Subprocess, spawned/health-checked/torn down by `services/localmodels/server_manager.py` |
| Ollama | Alternative local inference backend | Treated as just another OpenAI-compatible endpoint (`_API_HOSTS` includes `ollama.com`; local Ollama is autodetected via port scan per `tests/test_ollama_port_detection.py`) |
| SearXNG | Metasearch for `web_search` | Docker sidecar in `docker-compose.yml` (`services.searxng`, image `docker.io/searxng/searxng:2026.5.31-...`) *or* a native no-Docker managed process (`services/searxng/`) — README calls out "a managed no-Docker SearXNG sidecar" as a first-class deployment mode |
| Paperclip | External agent-coordination platform integration | `services/paperclip/`, `routes/paperclip_routes.py`; has its own Postgres sidecar (`paperclip-db`, `postgres:17-alpine`) in `docker-compose.yml` |
| GitHub-hosted catalogs | Model/skill/reference-library catalogs pulled at runtime | `services/model_hub.py`, `services/connector_catalog.py`, `services/reference_library.py` |
| ntfy | Push notification channel for reminders/tasks | Docker sidecar (`docker-compose.yml: services.ntfy`) |
| ChromaDB | Vector store for RAG, memory, tool index | **Embedded**, not a server — `src/chroma_client.py`; SECURITY.md is explicit that "the default Docker topology uses an embedded ChromaDB store and does not run a ChromaDB HTTP server" |

Layer dependency graph (informal): `routes/*` → `src/*` (business logic) →
`core/*` (database, auth, middleware primitives) and `services/*` (external
process/integration management). `src/agent_loop.py` is the hub most other
`src/` modules feed into (tool_parsing, tool_index, tool_execution, llm_core,
context_compactor, context_budget, prompt_security). `static/js/*` talks to
the backend exclusively over `/api/*` HTTP + SSE — there is no shared code or
schema validation between the Python and JS layers beyond convention (no
OpenAPI-client codegen consumed by the frontend, despite FastAPI generating
an OpenAPI schema for free).

## 6. DB schema highlights

`core/database.py` defines 25+ SQLAlchemy models under one `Base`, all in one
SQLite file (`data/app.db` by default). Notable ones: `Session` / `ChatMessage`
(chat history), `Document` / `DocumentVersion` (versioned editor documents),
`GalleryAlbum` / `GalleryImage`, `EmailAccount`, `ModelEndpoint`, `McpServer`,
`Comparison`, `Signature`, `ApiToken`, `ActivityEvent` (the audit ledger —
§9 below), `ReferenceEntry`, `Webhook`, `UserTool` / `UserToolData`,
`CrewMember`, `ScheduledTask` / `TaskRun`, `EditorDraft`, `Memory` (SQL-backed
memory metadata, distinct from the JSON `MemoryManager` store in §3 — worth a
reviewer double-checking which is authoritative), `Note`, `CalendarCal` /
`CalendarEvent`, `Integration`. An `EncryptedText` `TypeDecorator` (line 64)
exists for at-rest encryption of sensitive columns (e.g. email credentials).

`SessionManager.delete_session()` shows a deliberate cascade/detach choice —
documents are **not** deleted when their owning session is:

```python
# core/session_manager.py:479-505 (abridged)
def delete_session(self, session_id: str) -> bool:
    db = SessionLocal()
    try:
        # Detach documents so they survive as orphans in the library
        db.query(DbDocument).filter(DbDocument.session_id == session_id).update(
            {DbDocument.session_id: None}, synchronize_session=False
        )
        db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).delete()
        db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if db_session:
            db.delete(db_session)
        # Drop the in-memory copy even when there is no DB row. A "ghost"
        # session lives only here (never persisted, or its row was removed
        # out-of-band); without this it can never be cleared and keeps
        # 404ing on every operation (issue #1044).
        removed_in_memory = self.sessions.pop(session_id, None) is not None
        if db_session or removed_in_memory:
            db.commit()
            return True
        return False
    ...
```

The engine itself: `core/database.py:43-45` — `create_engine(DATABASE_URL,
connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else
{})`, plus an event listener enabling `PRAGMA foreign_keys=ON` per connection
(line 57). **No `PRAGMA journal_mode=WAL` is set anywhere in the codebase**
(grepped — absent). See Part 3 for the SQLite-over-server-DB design
discussion and the concurrency question this raises.

## 7. Pain point (a): the in-memory session map never evicts, and hydration is not atomic

`core/session_manager.py`'s `SessionManager.__init__` holds every touched
session forever in a plain dict:

```python
# core/session_manager.py:58-61
def __init__(self, sessions_file: str = None):
    # sessions_file kept for backward compat, not used
    self.sessions: Dict[str, Session] = {}
    self.load_sessions()
```

`load_sessions()` bounds the *boot-time* load to the 100 most-recently-active
non-archived sessions with `message_count > 0` (a real improvement over an
earlier version, per its own docstring: *"Previously this walked every
message of every session into RAM at boot ... tens of thousands of rows held
forever"*) — but nothing bounds **runtime growth**. Every session touched
after boot (`get_session`, `create_session`) is added to `self.sessions` and
**never removed** unless `delete_session()` is called explicitly:

```python
# core/session_manager.py:334-355
def get_session(self, session_id: str) -> Session:
    """Get a session by ID, loading from DB if needed.

    Sessions seeded by `load_sessions` start with empty history. The
    first read here hydrates them with the message rows.
    """
    if session_id not in self.sessions:
        self._load_session_from_db(session_id)
    else:
        cached = self.sessions[session_id]
        # Lazy hydrate: metadata-only entries get their messages on first read.
        if not cached.history and getattr(cached, "message_count", 0) > 0:
            self._load_session_from_db(session_id)
    self.sync_session_metadata(session_id)
    self._touch_session(session_id)
    return self.sessions[session_id]
```

The only cleanup path is `cleanup_empty_sessions(auto_archive_days=30)`
(lines 599-637), which **deletes empty sessions** and **archives** (sets a
flag on) old ones — archiving does not remove the entry from `self.sessions`,
it just flips `archived=True` on the cached object. There is no evidence this
method runs on a schedule in the reviewed portion of `src/task_scheduler.py`
/ `src/bg_jobs.py` beyond being invokable; a long-running personal server
with heavy daily use will accumulate one `Session` object (each holding full
`ChatMessage` history once hydrated) per distinct session ever opened, for
the lifetime of the process. This directly reintroduces, at a slower rate,
the exact problem the `load_sessions()` docstring says was fixed at boot
time.

**Non-atomic hydration race.** `get_session()`'s `if session_id not in
self.sessions` is a plain dict check with no lock, and `_load_session_from_db`
does a separate DB read then a plain dict assignment:

```python
# core/session_manager.py:389-406
def _load_session_from_db(self, session_id: str):
    """Hydrate a single session (with messages) from the database."""
    db = SessionLocal()
    try:
        db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if db_session is None:
            raise KeyError(f"Session {session_id} not found")
        session = self._db_to_session(db_session, db)
        if session:
            self.sessions[session_id] = session
        else:
            meta = self._db_to_session_meta(db_session)
            ...
            self.sessions[session_id] = meta
    ...
```

Two coroutines calling `get_session(same_id)` concurrently (e.g. a
just-created session hit by two rapid requests, or a lazily-hydrated
metadata-only entry read by two concurrent chat turns) can both observe
"not hydrated" and both run `_load_session_from_db`, each doing a full DB
round-trip and then **unconditionally overwriting** `self.sessions[session_id]`
with its own freshly-built `Session` object. If an `add_message()` call
(which does `session = self.get_session(...); session.history.append(...)`)
interleaves with a concurrent hydration — the append happens against one
`Session` object instance, then a hydration that started before the append
completes replaces `self.sessions[session_id]` with a *different* object
built from a DB snapshot that may not include the just-appended (but
already-persisted-to-DB, since `_persist_message` commits independently)
message. The message itself survives in the DB either way, but the in-memory
copy callers are handed can silently diverge from what's on disk for the
remainder of that object's lifetime, until the next explicit reload. There is
no `asyncio.Lock`/`threading.Lock` guarding this check-then-act sequence
anywhere in `SessionManager`.

## 8. Pain point (b): four separate tool registries, manually kept in sync

Cross-referenced from Part 1 §11. The registries:

```python
# 1. src/tool_schemas.py:23
FUNCTION_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command (full access)", ...}},
    ...
]

# 2. src/agent_loop.py:174
TOOL_SECTIONS = {
    "bash": """```bash\n<shell command>\n```\nRun any shell command. ...""",
    ...
}

# 3. src/tool_index.py:63
BUILTIN_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "bash": "Run shell commands on the server. Install packages, check files, git operations, curl, system info, process management, networking.",
    ...
}

# 4. src/agent_tools.py:29
TOOL_TAGS = {"bash", "python", "web_search", "web_fetch", "browser", "builtin_browser", "read_file", "write_file",
             "create_document", "update_document", "edit_document", ...
             # Reference Library + persistent Python. TOOL_TAGS is the FOURTH
             # place a tool must be registered (schemas, TOOL_SECTIONS,
             # tool_index descriptions, and here) — the fenced-block regex is
             # built from this set, so an unlisted tag can never parse.
             "reference_search", "python_session",
             "app_api"}
```

The comment at `src/agent_tools.py:58-61` is the codebase **admitting the
problem in its own source** — it names all four registries and states the
consequence explicitly. There is a regression test guarding **one** of the
three pairwise relationships — schema ↔ local-prompt parity, not all four:

```python
# tests/test_local_tool_parity.py:1-12, 36-43
"""Every agent tool must be teachable to LOCAL models, not just API models.

API models learn tools from FUNCTION_TOOL_SCHEMAS (native function calling).
Local models learn them from agent_loop.TOOL_SECTIONS (fenced-block prompt
text). A tool present only in the schemas is INVISIBLE to local models — in a
local-first app that is a real feature gap, and exactly how reference_search
and python_session shipped unusable by local models: an 8B model asked to use
reference_search simply hallucinated a weather result.

New tools must either get a TOOL_SECTIONS entry or be explicitly added to the
known-gap list below with a reason.
"""
...
def test_schema_tools_have_local_prompt_sections():
    schema_names, sections = _load()
    missing = schema_names - sections - KNOWN_SCHEMA_ONLY
    assert not missing, (...)
```

That test's own docstring documents a **real, shipped bug** caused by exactly
this fragmentation (`reference_search`/`python_session` missing from
`TOOL_SECTIONS`, so an 8B local model hallucinated a weather API result
instead of calling the tool). Notably absent: no test asserts `TOOL_TAGS ⊇
FUNCTION_TOOL_SCHEMAS names` (a tool with a schema but no `TOOL_TAGS` entry
would successfully get sent to an API model, which might call it — but the
fenced-block parser would never recognize a same-named tag from a *local*
model, and more subtly, several dispatch tables in `tool_execution.py` key
off `TOOL_TAGS`-adjacent structures independently), nor does anything assert
`BUILTIN_TOOL_DESCRIPTIONS` (registry #3, used for RAG retrieval) stays in
sync — a tool present in schemas/sections but absent from
`BUILTIN_TOOL_DESCRIPTIONS` would never be *semantically retrievable*, only
reachable via `ALWAYS_AVAILABLE` or a matching `_KEYWORD_HINTS` entry. This
is the same class of bug the test file's docstring already documents having
shipped once, for the two registries that *are* checked.

## 9. Pain point (c): no filesystem/process sandbox for agent tools

`tool_execution.py`'s `bash` handler runs the model's command through a real
shell with no containment beyond a scrubbed environment:

```python
# src/tool_execution.py:487-523 (abridged)
# Minimal allowlisted env — do NOT inherit the host's secrets (provider API
# keys, DATABASE_URL, decrypted mail passwords, etc.). A prompt-injected
# agent could otherwise `env | curl` them out (SECURITY-FIXLIST P1 #2).
_subproc_env = build_agent_env(extra={"TERM": "xterm-256color", "COLUMNS": "120", "LINES": "40"})

if tool == "bash":
    proc = await asyncio.create_subprocess_shell(
        content,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_subproc_env,
    )
    stdout, stderr, rc, timed_out = await _run_subprocess_streaming(proc, timeout=DEFAULT_BASH_TIMEOUT, progress_cb=progress_cb)
```

There is no chroot, container, namespace isolation, or seccomp filter — the
command runs as the Apollo process's own OS user, with the same filesystem
and network reach that user has, minus the env-var secret scrubbing quoted
above. **Note the asymmetry**: `read_file`/`write_file` *are* confined to an
explicit allowlist of directory roots (`_tool_path_roots()` — `DATA_DIR`,
system temp dirs, plus opt-in `tool_path_extra_roots` from settings) via
`_resolve_tool_path()`, which resolves symlinks and checks containment. But
`bash` bypasses that entirely — a `bash` call can `cat` any file the OS user
can read, `curl` any reachable host (including the other "internal-only"
services SECURITY.md says to firewall — SearXNG, ChromaDB if ever exposed as
a server, Ollama), or write anywhere the OS user can write, regardless of
`_tool_path_roots()`.

What Apollo *does* have instead is audit + undo, not prevention —
`services/activity_ledger.py`:

```python
# services/activity_ledger.py:1-8, 20-27
"""Append-only activity ledger for agent tool executions.

Records every tool call the agent makes into the `activity_events` table so
the user has a searchable "computer history" (what the agent ran, wrote,
fetched, and when) plus per-write undo. Recording is strictly best-effort:
a ledger failure must never break tool execution, so `record_event` swallows
and logs its own errors.
"""
...
BEFORE_CONTENT_CHARS = 512_000
DEFAULT_MAX_EVENTS = 10_000
```

`capture_before(path)` snapshots a file's pre-write contents (capped at 512KB)
specifically to support **undo after the fact** — this is forensic/recovery
tooling, not a prevention control; a destructive or exfiltrating `bash` call
still executes to completion before anything is recorded. `THREAT_MODEL.md`
itself lists this as the **first** acknowledged "Known Gap":

> 1. **No shell/filesystem sandbox.** The agent `bash` and
> `read_file`/`write_file` tools run as the app process user with no network
> egress filtering or filesystem confinement. A successful prompt-injection
> reaching a shell-enabled admin session can make outbound requests to
> internal services. See #1058 for the sandbox proposal.

(That gap statement slightly overstates the symmetry — as shown above,
`read_file`/`write_file` *do* have path confinement via `_tool_path_roots`;
only `bash`, and code run via the `python` tool's arbitrary `exec`, are
fully unconfined. A reviewer should treat "no sandbox" as accurate for
process/network egress in all cases, but only accurate for filesystem
confinement in the `bash`/`python` case, not the `read_file`/`write_file`
case.)

## 10. Pain point (d): module-size ratchet pressure

`scripts/check_module_sizes.py` enforces a **JS-only** size ratchet — new
modules cap at 1,500 lines, but pre-existing large modules are grandfathered
at their measured baseline with a comment that moving code out must reduce
the baseline, and adding code above it fails CI:

```python
# scripts/check_module_sizes.py:9-19
# Existing entry points are intentionally grandfathered at their measured
# baseline. Moving code out must reduce these values in a later commit; adding
# code above a baseline fails this check. Every other module has the hard cap.
BASELINES = {
    "admin.js": 2092, "calendar.js": 3348, "chat.js": 4584,
    "chatRenderer.js": 2105, "cookbook-hwfit.js": 1790, "cookbook.js": 1965,
    "cookbookRunning.js": 3218, "cookbookServe.js": 2086, "document.js": 9453,
    "documentLibrary.js": 3365, "emailLibrary.js": 5217, "gallery.js": 2835,
    "galleryEditor.js": 3798, "modalManager.js": 1550, "notes.js": 5011,
    "sessions.js": 3135, "settings.js": 5043, "skills.js": 2038,
    "slashCommands.js": 5940, "tasks.js": 2709, "theme.js": 2160,
}
MAX_NEW_MODULE_LINES = 1500
```

`document.js` at **9,453 lines** is nearly 6.3x the new-module cap;
`slashCommands.js` (5,940), `emailLibrary.js` (5,217), `settings.js` (5,043),
and `notes.js` (5,011) are all 3.3-4x it. These are explicitly acknowledged
technical debt — grandfathered, not endorsed — and the ratchet mechanism only
stops them from growing further; it does not shrink them.

There is **no equivalent automated check for Python module size**. Using
`wc -l` as a proxy, the largest Python modules in the tree are:

```
3259  routes/email_routes.py
2547  src/builtin_actions.py
2331  src/agent_loop.py
2260  src/task_scheduler.py
2159  routes/cookbook_routes.py
1869  src/visual_report.py
1836  routes/gallery_routes.py
1823  src/ai_interaction.py
1798  routes/model_routes.py
1712  routes/document_routes.py
1563  routes/skills_routes.py
1506  src/llm_core.py
1478  routes/chat_routes.py
1368  routes/email_helpers.py
1317  routes/calendar_routes.py
1284  src/tool_schemas.py
1258  routes/session_routes.py
1187  src/tool_execution.py
1114  routes/email_pollers.py
```

`routes/email_routes.py` at 3,259 lines and `src/agent_loop.py` at 2,331
lines (documented in depth in Part 1) are the two standout candidates for
decomposition on the Python side — neither is currently gated by any
automated size check, unlike the JS side.

## 11. Pain point (e): local-model dialect fragility

Cross-referenced from Part 1 §4 (`src/tool_parsing.py`). The regex cascade —
`_normalize_dsml` (lines 71-85) for DeepSeek's fullwidth-pipe DSML markup,
`_normalize_function_eq` (lines 87-108) for the Qwen/Llama-3
`<function=NAME>` dialect, plus separate parsers for `[TOOL_CALL]{...}`,
bare `<invoke>`, and MiniMax's `<tool_code>{...}` — is fundamentally reactive:
each new local model family that deviates from the taught fenced-block
convention gets its own hand-written normalization pass, discovered by
observing a specific model fail in practice (the `_normalize_function_eq`
docstring cites "observed live from Qwen3-365-A3B, whose reference_search
call fell through as plain text" as the origin of that fix). This is a
maintenance-cost pattern more than a bug per se — see Part 3's "Areas for
Review" for the grammar-constrained-decoding alternative question.

## 12. Pain point (f): `web_fetch` cannot read raw JSON responses

Confirmed directly in code, and matches what was observed live in testing —
an agent needing JSON from an API had to fall back to reading an HTML page
instead. `web_fetch`'s dispatch (`src/tool_execution.py:648-711`) delegates
content extraction entirely to `fetch_webpage_content()`
(`src/search/content.py:214-`), which special-cases exactly one non-HTML
content type — PDF — and otherwise **always** parses the response body as
HTML, regardless of the actual `Content-Type` header:

```python
# src/search/content.py:258-296 (abridged)
# PDF handling
content_type = response.headers.get("Content-Type", "").lower()
if "application/pdf" in content_type or url.lower().endswith(".pdf"):
    ...
    result = {"url": url, "title": ..., "content": pdf_text, ...}
    _cache_result(cache_file, cache_key, result, url)
    return result

# HTML handling
try:
    soup = BeautifulSoup(response.text, "html.parser")
except Exception as e:
    error_logger.error(f"ParseError parsing HTML from {url} (attempt {retry_attempt}): {e}")
    result = _empty_result(url, f"ParseError: {e}")
    _cache_result(cache_file, cache_key, result, url)
    return result
```

There is no `"application/json" in content_type` branch. A JSON response
fed through `BeautifulSoup(response.text, "html.parser")` typically yields
little or no extractable "text" content (JSON has no tag structure for the
HTML parser to find headings/paragraphs in), which surfaces back in
`tool_execution.py` as:

```python
# src/tool_execution.py:700-705
if not text:
    if err:
        return {"error": f"web_fetch: {url}: {err}", "exit_code": 1}
    return {"error": f"web_fetch: {url}: no readable text content (not HTML, or the page needs JS/login)", "exit_code": 1}
```

The error message itself ("not HTML, or the page needs JS/login") reveals
the tool has no concept of "this was valid JSON I should have returned
verbatim" — a JSON API response is misclassified into the same bucket as a
JS-rendered SPA shell, and the agent has to route around it (e.g. via the
`browser` tool, or as observed, by reading an HTML page that happens to embed
the same data) instead of getting the JSON directly.

## 13. Other pain points found with evidence

- **SSRF gap acknowledged, partially fixed.** `THREAT_MODEL.md`'s "Known
  Gaps" #2: *"SSRF via `/api/v1/chat` `base_url` parameter. A chat-scoped API
  token can supply an arbitrary `base_url`; the server forwards the LLM
  request to that host without validating the scheme or address. PR #1039
  fixes this."* — the document itself flags this as fixed by a specific PR;
  a reviewer should verify #1039 landed and the check is actually present in
  this working tree rather than trusting the changelog note.

- **Duplicated `search` module tree, documented as intentional-but-fragile.**
  `THREAT_MODEL.md`'s Known Gap #3: *"`src/search/` partial consolidation.
  `src.search.core` and `src.search.providers` correctly alias
  `services.search` via `sys.modules` replacement. `analytics`, `cache`,
  `content`, `query`, and `ranking` are still independent copies that can
  drift."* — `src/search/content.py` (quoted in §12 above, and the file
  `web_fetch` depends on) is one of the modules explicitly called out as an
  unconsolidated copy at risk of drifting from `services/search/`'s version.

- **Coarse API token scopes.** `THREAT_MODEL.md` Known Gap #4: *"Token scopes
  are coarse. There is no way to grant a session a subset of the owning
  user's privileges. Companion/mobile tokens carry either `chat` or `admin`
  scope with no per-capability granularity."*

- **No `TODO`/`FIXME`/`HACK`/`XXX` debt markers found in `src/`/`routes/`.**
  A grep across both directories for these tags returned nothing but one
  false positive (a Chinese-text comment containing the literal substring
  "XXX" as part of "XXX 写道:" — a forwarded-email quoting marker, unrelated
  to code debt, in `src/email_thread_parser.py:93`). This is a genuinely
  positive signal — either debt is tracked elsewhere (GitHub issues, per the
  `#1058`/`#1039`/`#1044` references seen throughout) or the codebase
  resolves things before they accumulate as inline markers; a reviewer
  should not assume the absence of `TODO` comments means the absence of
  debt, given the size/registry/sandbox issues documented above that carry
  no inline marker at all.

- **Two independent memory-relevance algorithms** (§3): `MemoryManager`'s
  token-overlap `get_text_similarity` vs. `MemoryVectorStore`'s embedding
  search, invoked from different call sites with no shared abstraction and
  no test asserting they'd rank the same memory as "most relevant" for a
  given query.

- **SQLite has no WAL mode configured** (§6) despite the app being explicitly
  designed around concurrent agent tool execution (background bash/python
  tasks), a detached background chat-stream task per session
  (`agent_runs.start`), and a task scheduler running independent cron jobs —
  all potentially issuing writes to the same `app.db` concurrently. See Part
  3's Areas for Review for the concurrency-safety question this raises.
