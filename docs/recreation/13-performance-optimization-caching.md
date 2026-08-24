# Apollo — Performance Optimization & Caching

Apollo runs as a single-process FastAPI app on top of local, resource-constrained
inference (llama.cpp on consumer GPUs/Metal, sometimes CPU-only). Its performance
design is therefore mostly about **not wasting the one scarce resource** — VRAM/RAM
for a resident model, and a small model's limited context window — rather than
classic web-scale caching. The load-bearing patterns are: never run more than one
warm chat model at a time; never send all ~50 tool schemas to the model on every
turn; cap how much memory/context gets injected into a prompt; route trivial chat
turns to a cheaper model when the operator opts in; and keep the frontend
build-free so "no build step" itself becomes the caching problem (solved with
`Cache-Control: no-cache` + conditional requests, not bundling).

## 1. Single-warm-model policy for llama-server

`services/localmodels/server_manager.py`'s own module docstring states the policy
directly: `"""Launch and track local llama-server processes (single warm chat
model)."""` `LocalModelServer` holds at most **one** chat-model slot and one
embedding-model slot at a time (`self._chat`, `self._embed`), and switching models
always stops the previous process before starting the next:

```python
# services/localmodels/server_manager.py (ensure_running)
with self._lock:
    # Embedding GGUFs get an independent slot (served with --embedding)
    # so they can run alongside a chat model. Today this is reachable
    # only via an explicit start/select; RAG still defaults to
    # fastembed, so the embedding slot has no implicit caller yet.
    slot = self._embed if m.kind == "embedding" else self._chat
    if slot and slot.model_id == m.id and slot.proc.poll() is None:
        return slot.base_url
    if slot:
        self._stop_proc(slot)
    proc = self._launch(m)
    if m.kind == "embedding":
        self._embed = proc
    else:
        self._chat = proc
    return proc.base_url
```

Three consequences of this shape: (1) if the requested model is **already** the one
running and its process is still alive (`proc.poll() is None`), `ensure_running`
short-circuits and returns the cached `base_url` immediately — no restart cost for
repeated calls against the same model; (2) switching models is always
**stop-before-start**, never both processes resident simultaneously, which is the
actual VRAM-safety property — GGUF chat models on a single GPU/unified-memory box
cannot coexist without risking OOM; (3) the whole `ensure_running` call is
`self._lock`-guarded (`threading.RLock()`), so concurrent requests picking different
models can't race into launching two chat processes at once — the second caller
blocks until the first's stop-then-start completes.

`_stop_proc` (used by both the switch-model path and `stop_all()`) is a graceful
terminate with a kill fallback, and always clears the slot regardless of how the
terminate went:

```python
# services/localmodels/server_manager.py
def _stop_proc(self, slot: _Proc) -> None:
    try:
        slot.proc.terminate()
        slot.proc.wait(timeout=10)
    except Exception as error:
        report_exception(logger, "local_model_terminate_failed", error,
                          outcome="degraded", context={"model_id": slot.model_id})
        try:
            slot.proc.kill()
        except Exception as cleanup_error:
            report_exception(logger, "local_model_kill_failed", cleanup_error,
                              outcome="best_effort", context={"model_id": slot.model_id})
    if slot is self._chat:
        self._chat = None
    if slot is self._embed:
        self._embed = None
```

### 1.1 Context-window sizing is also VRAM-aware

`_serving_context` picks the `-c` (context window) llama-server is launched with —
not a fixed value, but `min(model's known window, a configurable cap)`, bounded
below by the app's own default context:

```python
# services/localmodels/server_manager.py
def _serving_context(self, m: LocalModel) -> int:
    """Context window to launch llama-server with.

    Apollo's prompt packer budgets against the model's KNOWN window, so a
    fixed small -c rejects long chats with HTTP 400 ("request exceeds the
    available context size"). Serve min(known window, cap) instead — the
    cap (APOLLO_LLAMA_CONTEXT, default 16384) keeps the KV cache bounded;
    the configured default stays the floor.
    """
    cap = self._context
    try:
        cap = max(int(os.getenv("APOLLO_LLAMA_CONTEXT", "16384")), self._context)
    except ValueError:
        cap = max(16384, self._context)
    try:
        from src.model_context import _lookup_known
        known = _lookup_known(m.name or m.id)
    except Exception as error:
        report_exception(logger, "local_model_context_lookup_failed", error,
                          outcome="best_effort", context={"model_id": m.id})
        known = None
    if known:
        return max(self._context, min(known, cap))
    return cap
```

`APOLLO_LLAMA_CONTEXT` (default `16384`) is the operator's KV-cache ceiling — the
larger the served context, the more RAM/VRAM the KV cache itself consumes,
independent of the model weights.

## 2. Tool-schema RAG selection and token budgeting

Apollo ships roughly 50 built-in agent tools. Sending every tool's JSON schema to
the model on every turn is exactly the cost `src/tool_index.py` exists to avoid —
its own docstring states the design directly:

```python
# src/tool_index.py
"""
RAG-based tool selection for agent mode.

Instead of injecting all tool descriptions into the system prompt,
embed them in a ChromaDB collection and retrieve only the top-K
relevant ones per user message.
"""
```

`ALWAYS_AVAILABLE` is a small, deliberately-kept-tight frozenset of tools included
on every turn regardless of retrieval — the comment on it explicitly reasons about
budget headroom:

```python
# src/tool_index.py
ALWAYS_AVAILABLE = frozenset({
    "bash", "python", "web_search", "web_fetch", "read_file",
    "api_call",  # For configured integrations (Miniflux, Gitea, Linkding, etc.)
    # The two genuinely AMBIENT cookbook tools — "what's running" and
    # "kill it" can be asked any time without prior cookbook context,
    # and need to survive typos. The other cookbook tools (downloads,
    # presets, serve, cached, servers) are CONTEXTUAL — they fire via
    # keyword hints when the user is actually talking about cookbook.
    # Keeping the always-on set small leaves room in the ~16-tool
    # budget for manage_tasks / manage_calendar / etc.
    "list_served_models", "stop_served_model",
    # Generic API loopback — the catch-all when no named tool fits.
    "app_api",
})
```

The comment states the target budget explicitly: **~16 tools per turn**, not ~50.
`get_tools_for_query` is the actual selection function — always-available set,
plus vector-retrieved top-K, plus word-boundary keyword force-includes, plus a
structural regex for scheduling intent:

```python
# src/tool_index.py
def get_tools_for_query(
    self, query: str, k: int = 8, always_include: Optional[Set[str]] = None
) -> Set[str]:
    """Get the set of tool names to include for a given user query."""
    base = set(always_include or ALWAYS_AVAILABLE)
    retrieved = self.retrieve(query, k=k)
    base.update(retrieved)
    # Keyword-based force-include for common intents. Match on word
    # boundaries, not raw substrings, so short hints like "fix", "line",
    # "serve", "reply" or "unread" don't fire inside unrelated words
    # ("prefix", "deadline"/"online", "observe"/"reserve", "replying",
    # "unreadable"). Same word-boundary matching used in topic_analyzer.
    ql = query.lower()
    for keywords, tools in self._KEYWORD_HINTS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", ql) for kw in keywords):
            base.update(tools)
    # Structural scheduling-intent detection — typo-resilient (the literal
    # keyword "every day" misses "every dya"). Catches "every <word>",
    # daily/nightly/etc., or a clock time like "at 7:30 am" / "7am", which
    # all signal a recurring/scheduled task. Force-include manage_tasks so
    # the agent can actually create the cron job instead of fumbling.
    if self._SCHEDULE_RE.search(ql):
        base.add("manage_tasks")
    return base
```

`k=8` is the default number of vector-retrieved tools; combined with
`ALWAYS_AVAILABLE` (~9 entries) and any keyword force-includes, this is what keeps
the actual set near the ~16-tool budget instead of the full ~50.

### 2.1 The retrieval call site — timeouts, retrieval-query construction, and a keyword fallback

`src/agent_loop.py` is where tool selection actually runs before every agent turn.
It uses **recent conversation context, not just the latest message**, so a short
follow-up doesn't lose tools that were just in play, and it bounds both the MCP
reindex and the retrieval call with a timeout so a slow embedding step can't stall
the whole turn:

```python
# src/agent_loop.py
_needs_admin = _detect_admin_intent(messages)
_last_user = _extract_last_user_message(messages)
# Tool retrieval keys on recent conversation context (last few user turns),
# not just the latest message, so short follow-ups don't drop just-used tools.
_retrieval_query = _recent_context_for_retrieval(messages) or _last_user
...
# RAG-based tool selection: retrieve relevant tools for this query.
# If caller provided a pre-computed set (e.g. task_scheduler), use that.
_relevant_tools = relevant_tools
if _relevant_tools:
    logger.info(f"[tool-rag] Using caller-provided relevant_tools ({len(_relevant_tools)} tools)")
if not _relevant_tools:
    try:
        from src.tool_index import get_tool_index, ALWAYS_AVAILABLE
        tool_idx = get_tool_index()
        if tool_idx:
            if mcp_mgr:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(tool_idx.index_mcp_tools, mcp_mgr, _mcp_disabled_map),
                        timeout=_TOOL_SELECTION_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[tool-rag] MCP tool indexing exceeded %.1fs; continuing without reindex",
                                   _TOOL_SELECTION_TIMEOUT_SECONDS)
            if _retrieval_query:
                try:
                    _relevant_tools = await asyncio.wait_for(
                        asyncio.to_thread(tool_idx.get_tools_for_query, _retrieval_query, 8),
                        timeout=_TOOL_SELECTION_TIMEOUT_SECONDS,
                    )
                    logger.info(f"[tool-rag] Retrieved tools for query: {sorted(_relevant_tools - ALWAYS_AVAILABLE)}")
                except asyncio.TimeoutError:
                    logger.warning("[tool-rag] Retrieval exceeded %.1fs; falling back to always-available tools",
                                   _TOOL_SELECTION_TIMEOUT_SECONDS)
                    _relevant_tools = set(ALWAYS_AVAILABLE)
    except Exception as e:
        logger.warning(f"[tool-rag] Retrieval failed, using keyword fallback: {e}")
        _relevant_tools = None

# Fallback: if RAG unavailable, use keyword-based tool selection
# instead of sending ALL tools (which overwhelms the model).
if not _relevant_tools and _retrieval_query:
    from src.tool_index import ALWAYS_AVAILABLE, ToolIndex
    _relevant_tools = set(ALWAYS_AVAILABLE)
    ql = _retrieval_query.lower()
    for keywords, tools in ToolIndex._KEYWORD_HINTS.items():
        if any(kw in ql for kw in keywords):
            _relevant_tools.update(tools)
    # Always include core document/memory tools
    _relevant_tools.update({"create_document", "manage_memory", "manage_notes"})
    logger.info(f"[tool-rag] Keyword fallback selected: {sorted(_relevant_tools - ALWAYS_AVAILABLE)}")
```

The fallback comment is explicit about the cost being avoided: *"use keyword-based
tool selection instead of sending ALL tools (which overwhelms the model)"* — even
when ChromaDB/the vector index is unavailable, the code still refuses to fall all
the way back to sending every schema; it degrades to keyword matching instead,
never to "send everything."

### 2.2 Where the selected set actually gets used

Downstream, `all_tool_schemas` is only built from the selected/filtered subset, not
the full registry — the final request to the model carries exactly this filtered
list:

```python
# src/agent_loop.py
all_tool_schemas = base_schemas + _mcp_filtered
...
_tool_names_sent = [t.get("function", {}).get("name") for t in (all_tool_schemas or []) if t.get("function")]
...
tools=all_tool_schemas if all_tool_schemas else None,
```

## 3. Memory caps and `memory_recall_max`

Memory injection into the prompt preface is capped by two settings
(`src/settings.py`), with the comment describing the failure mode the caps prevent
— a large pinned-memory set silently eating a small local model's entire context
budget before the actual request begins:

```python
# src/settings.py
# Context budget: caps on memory injection into the prompt preface.
# Pinned memories were previously unbounded — a large pinned set could
# eat a small local model's context before the request started.
"memory_recall_max": 3,
"memory_pinned_max": 15,
```

`memory_recall_max` (default `3`) directly bounds how many memories the chat
pipeline recalls per turn:

```python
# src/chat_processor.py
recall_k = int(get_setting("memory_recall_max", 3))
```

`memory_pinned_max` (default `15`) is the separate cap on always-injected pinned
memories, independent of the per-turn recall count — the two together bound the
worst case (all pinned memories + top-K recalled memories) rather than leaving
either side unbounded.

## 4. Mixture-routing Fast Lane

`services/model_router.py` sends short, clearly-conversational chat messages to a
separate, smaller "light" model instead of the session's normal (larger) model —
opt-in, off by default, and **chat mode only**:

```python
# services/model_router.py
"""Mixture routing: send light chat messages to a small, fast local model.

Apollo is model-neutral, so it can do what single-vendor tools won't: pick
the model per message purely on task fit. A deterministic classifier (no
LLM call, no added latency) tags a message "light" or "heavy"; light ones
are answered by the configured `light` role (Settings: light_endpoint_id /
light_model) with the session's own model as first fallback, so a light-lane
failure degrades to exactly the old behavior.

Chat mode only — the agent loop needs tool-competent models and is not
routed. Opt-in via the `mixture_routing_enabled` setting (default off).
"""
```

Classification is a pure, deterministic function — no extra LLM call, so no added
latency for the classification step itself, and biased toward "heavy" whenever
ambiguous (the docstring states the asymmetric cost: *"a wrong 'heavy' costs a few
seconds, a wrong 'light' costs answer quality"*):

```python
# services/model_router.py
LIGHT_MAX_CHARS = 280

_HEAVY_MARKERS = re.compile(
    r"(?i)\b("
    r"code|write|implement|debug|fix|refactor|analy[sz]e|research|review|"
    r"plan|design|architect|prove|derive|calculate|compute|translate|"
    r"summari[sz]e|compare|explain why|step[- ]by[- ]step|essay|report|"
    r"document|spreadsheet|regex|sql|script|function|error|traceback|"
    r"stack trace"
    r")\b"
)

def classify_message(message: str) -> str:
    """Return "light" or "heavy". Deterministic; conservative toward heavy."""
    msg = (message or "").strip()
    if not msg or len(msg) > LIGHT_MAX_CHARS:
        return "heavy"
    if "```" in msg or "\n\n" in msg:
        return "heavy"
    if _HEAVY_MARKERS.search(msg):
        return "heavy"
    if msg.count("?") > 1:
        return "heavy"
    return "light"
```

`route_chat` is the entry point, and it fails open to "use the default model" on
*any* problem — disabled setting, heavy message, no light model configured, or an
unexpected exception:

```python
# services/model_router.py
def route_chat(message: str, owner: Optional[str] = None) -> Optional[Tuple[str, str, Dict]]:
    """(url, model, headers) for the light lane, or None to keep the default.

    None whenever routing is disabled, the message is heavy, no light model
    is configured, or anything at all goes wrong — the caller treats None
    as "behave exactly as before".
    """
    try:
        if not get_setting("mixture_routing_enabled", False):
            return None
        if classify_message(message) != "light":
            return None
        from src.endpoint_resolver import resolve_endpoint
        url, model, headers = resolve_endpoint("light", owner=owner)
        if not url or not model:
            return None
        return url, model, headers or {}
    except Exception:
        logger.exception("mixture routing failed (falling back to default)")
        return None
```

The call site in `routes/chat_routes.py` only routes when in plain chat mode
(`chat_mode == "chat"`) and not doing web research, and surfaces the routing
decision to the frontend as a `"Fast lane"` model-info suffix so the user can see
which lane answered:

```python
# routes/chat_routes.py
# Mixture routing (chat mode only): short conversational messages
# go to the configured "light" model; the session's model stays
# first fallback so a light-lane failure degrades to old behavior.
_routed_light = None
if chat_mode == "chat" and not do_research:
    try:
        from services.model_router import route_chat
        _routed_light = route_chat(message or "", owner=_user)
    except Exception:
        _routed_light = None

# Send model name early so the frontend can show it during streaming
_model_info = {"type": "model_info", "model": _routed_light[1] if _routed_light else sess.model}
if _routed_light:
    _model_info["suffix"] = "Fast lane"
```

`routes/model_routes.py` registers the light role in the endpoint-role table:
`"light_endpoint_id": ("light_model", "Fast Lane")`.

## 5. SQLite usage patterns

Apollo's primary datastore (`core/database.py`) is SQLAlchemy over SQLite by
default (`DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{data_path('app.db')}")`),
with a single pragma set on every new connection:

```python
# core/database.py
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Listening on the Engine class ensures this listener fires for all Engine
# instances created within the process, not just the primary application engine.
# The isinstance(sqlite3.Connection) check ensures that this PRAGMA foreign_keys=ON
# configuration remains a no-op when using non-SQLite database backends.
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

UNCERTAIN: a repo-wide search (`grep -rn "journal_mode\|synchronous\|PRAGMA" core/
src/ services/`) turns up only `PRAGMA foreign_keys=ON` and repeated
`PRAGMA table_info(<table>)` introspection calls used by the schema-migration
helpers in `core/database.py` (checking for a column's existence before adding it)
— there is **no explicit `PRAGMA journal_mode=WAL` or `PRAGMA synchronous=...`**
anywhere in the tree. SQLite's default journal mode (`DELETE`/rollback-journal) is
therefore what Apollo runs with; concurrent-writer throughput under that default is
weaker than WAL would provide. `check_same_thread=False` is set so the same
`Engine`'s connections can be used across FastAPI's threadpool workers, but that is
a thread-safety accommodation, not a performance pragma.

Sensitive columns (IMAP/SMTP passwords today) are transparently encrypted at rest
via a custom SQLAlchemy `TypeDecorator`, which trades a small per-read/write Fernet
cost for at-rest protection — see
`docs/recreate/14-security-implementation-best-practices.md` for the full
`EncryptedText`/`secret_storage.py` mechanism.

## 6. ChromaDB embedding reuse

`src/chroma_client.py`'s `get_chroma_client()` is a process-wide singleton — the
first call constructs either an embedded `PersistentClient` (default, no separate
service) or an `HttpClient` (only when `CHROMADB_HOST` is explicitly set), and every
subsequent call reuses the same client object instead of reopening the store:

```python
# src/chroma_client.py
def get_chroma_client():
    """Get or create the singleton ChromaDB client.

    Uses an embedded on-disk ``PersistentClient`` by default (native desktop,
    no service to run). Switches to ``HttpClient`` only when ``CHROMADB_HOST``
    is explicitly set (Docker / remote service).
    """
    global _client
    if _client is not None:
        return _client
    try:
        import chromadb
    except ImportError as e:
        raise RuntimeError(
            "ChromaDB integration is not installed. Install the optional "
            "dependency with: pip install chromadb"
        ) from e

    host = os.getenv("CHROMADB_HOST", "").strip()
    if host:
        port = int(os.getenv("CHROMADB_PORT", "8000"))
        if not _port_open(host, port):
            raise RuntimeError(
                f"ChromaDB is not reachable at {host}:{port}. Start the ChromaDB "
                f"service (e.g. `docker compose up chromadb`) or unset CHROMADB_HOST "
                f"to use the built-in embedded store."
            )
        client = chromadb.HttpClient(host=host, port=port)
        client.heartbeat()   # don't cache a client whose service isn't actually healthy
        _client = client
        logger.info(f"ChromaDB connected (http): {host}:{port}")
        return _client

    path = _persist_dir()
    os.makedirs(path, exist_ok=True)
    _client = chromadb.PersistentClient(path=path)
    logger.info(f"ChromaDB connected (embedded): {path}")
    return _client
```

The `client.heartbeat()` call before caching in the HTTP-mode branch matters for
correctness, not just performance: an open TCP port doesn't guarantee a healthy
ChromaDB service behind it, so a dead-but-listening service would otherwise poison
the singleton until process restart. `src/rag_singleton.py` layers the same
singleton pattern on top for the RAG manager built from this client, and
`src/tool_index.py`'s own `get_tool_index()` singleton (§2) reuses the same
underlying `apollo_tool_index` ChromaDB collection across every agent turn rather
than re-embedding the tool catalog per request — it's only rebuilt when
`index_builtin_tools()`/`index_mcp_tools()` are explicitly invoked (on first use and
on MCP server changes).

## 7. Frontend: no-build-step tradeoff

`README.md` states the frontend architecture plainly: *"Tier 1 — Frontend
(`static/`). `index.html` + `app.js` plus ~90 ES modules under `static/js/`. No
bundler or transpiler — browsers load raw `.js` modules."* `static/index.html`
loads each module with an individual `<script type="module" src="/static/js/...">`
tag (95 files present under `static/js/` at time of writing) — there is no
webpack/rollup/esbuild/vite in `package.json`, and no build step between editing a
`.js` file and the browser loading it.

The tradeoff this creates: **HTTP/2 multiplexes the ~90 module requests over one
connection** (avoiding the classic HTTP/1.1 "waterfall of many small files" cost),
but the browser's disk cache would otherwise treat those unbundled, unversioned
files as static assets — including caching a *stale* copy of a module across a code
deploy, since there's no content-hashed filename to bust the cache the way a
bundler's output normally does. Apollo's fix is server-side cache-control, not
bundling:

```python
# app.py
class _RevalidatingStatic(StaticFiles):
    """Serve static assets normally, but force the browser to REVALIDATE
    source files (.js/.css/.html) on every load instead of serving a stale
    copy from disk cache. The app ships raw ES modules with no build step or
    versioned URLs, so browsers were caching modules across deploys — a code
    change wouldn't appear without a manual hard-refresh. `no-cache` keeps the
    cached bytes but requires a conditional request; unchanged files still
    return a cheap 304 (ETag/Last-Modified are preserved)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

app.mount("/static", _RevalidatingStatic(directory="static"), name="static")
```

`Cache-Control: no-cache` (not `no-store`) is the deliberate middle ground: the
browser still keeps the bytes on disk and still sends a conditional
`If-None-Match`/`If-Modified-Since` request, so an *unchanged* file costs only a
cheap `304 Not Modified` round-trip — the module itself isn't re-downloaded, only
revalidated. Non-source assets (images generated at `/api/generated-image/...`,
etc.) instead get a long-lived immutable cache header where content-addressing
makes that safe: `headers={"Cache-Control": "public, max-age=31536000, immutable"}`.

The net design: **HTTP/2 multiplexing pays for the module-count cost of no
bundling, and per-request revalidation pays for the staleness risk of no
content-hashed filenames** — in exchange, the project avoids an entire build
toolchain, keeping every `.js` file directly debuggable in the browser exactly as
written on disk.

## 8. Measured local-inference performance

UNCERTAIN: this figure is the operator's own hardware benchmark, not a number
produced by an in-repo benchmark suite (no `tests/` or `scripts/` file computes or
asserts a tokens/sec figure) — treat it as a real-world data point, not a
CI-verified guarantee: on Apple Silicon (Metal backend via llama.cpp/llama-server),
a 27B-parameter reasoning model quantized to Q6_K runs at approximately **4.2
tokens/sec**. This is consistent with the single-warm-model policy in §1 being a
real necessity rather than defensive-only code — at that generation speed, a
multi-model resident setup on the same hardware would both contend for the same
Metal GPU cycles and risk the unified-memory ceiling, materially slowing or
crashing every resident model rather than just the one in use. It is also the
concrete rationale behind the Fast Lane (§4): routing trivial conversational
turns to a smaller resident/alternate model is a real latency win specifically
because the large reasoning model's throughput at this quantization is measured in
single-digit tokens/sec, not because Fast Lane is a purely theoretical
optimization.

## 9. Known unresolved issue: session-cache growth and non-atomic hydration race

`core/session_manager.py`'s `SessionManager` keeps an in-memory dict of every
session it has touched, `self.sessions: Dict[str, Session]`, seeded at boot with
**metadata only** for the 100 most-recently-accessed non-archived sessions (an
intentional fix for an earlier problem — loading every message of every session
into RAM at boot):

```python
# core/session_manager.py
def __init__(self, sessions_file: str = None):
    # sessions_file kept for backward compat, not used
    self.sessions: Dict[str, Session] = {}
    self.load_sessions()

def load_sessions(self):
    """Load recent session METADATA from the database — messages are
    hydrated on demand by `get_session`. Previously this walked every
    message of every session into RAM at boot, which on a long-running
    personal-server box could be tens of thousands of rows held forever
    in `self.sessions`.
    """
    db = SessionLocal()
    try:
        db_sessions = db.query(DbSession).filter(
            DbSession.archived == False,
            DbSession.message_count > 0,
        ).order_by(DbSession.last_accessed.desc()).limit(100).all()
        ...
```

`get_session` — the read path every chat turn goes through — lazily hydrates a
session's full message history from the DB on first access, and **also inserts
into `self.sessions` any session ID it is asked for that was not part of the
initial top-100 metadata load**:

```python
# core/session_manager.py
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

    # Keep model/endpoint metadata fresh. Endpoint deletion can clear the
    # DB row while a session object is still cached in RAM.
    self.sync_session_metadata(session_id)
    self._touch_session(session_id)
    return self.sessions[session_id]

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
        ...
```

Two related properties of this cache, as implemented:

1. **Unbounded growth beyond the initial 100.** The boot-time `load_sessions()` cap
   (`.limit(100)`) only bounds the *initial* population. Every subsequent
   `get_session(session_id)` call for a session outside that initial set
   (`_load_session_from_db`) inserts a fully-hydrated `Session` object (including
   its complete message history) into `self.sessions` and never evicts it. On a
   long-running server that accumulates access to more than 100 distinct sessions
   over its lifetime — normal for any active multi-session user, and especially for
   scheduled/background jobs (`src/task_scheduler.py` hydrates sessions via this
   same `get_session`/`session_manager.get_session` path for scheduled task
   delivery, at three separate call sites) — the in-memory dict grows without a
   corresponding cap or LRU eviction. `grep -n "evict\|LRU\|self.sessions.pop"
   core/session_manager.py` finds no eviction logic.
2. **Non-atomic check-then-hydrate.** `if session_id not in self.sessions:
   self._load_session_from_db(session_id)` is a classic check-then-act race with no
   lock around it (`core/session_manager.py` has no `threading.Lock`/`RLock`
   protecting `self.sessions`). Under concurrent access to the same
   not-yet-cached `session_id` — e.g. two simultaneous chat requests for a session
   that just aged out of nowhere-cached state, or a scheduled-task delivery racing
   a live chat request for the same session — both callers can observe
   `session_id not in self.sessions` simultaneously and both call
   `_load_session_from_db`, each opening its own `SessionLocal()` and each
   assigning `self.sessions[session_id] = session` independently. The result is
   redundant DB work (harmless beyond wasted I/O) and a benign last-write-wins on
   the dict slot **so long as both hydrations build an equivalent `Session`
   object** — but it is not the atomic single-hydration the code's shape implies,
   and a hydration racing a concurrent in-place mutation of the same session
   (e.g. `sync_session_metadata` or a message append landing between the two
   racing reads) is not analyzed or guarded against here.

UNCERTAIN: no code comment, ADR, or test in the repository flags this growth/race
combination as a known, tracked issue (`grep -rn "race\|unbounded" core/
session_manager.py` and a scan of `docs/adr/` turn up nothing) — this is an
architectural observation derived directly from reading `core/session_manager.py`,
not a documented/ticketed gap. Both properties are real as of the code read for
this document and worth tracking as unresolved: the growth is a slow memory leak
proportional to distinct sessions touched over process lifetime, and the race is a
narrow but real correctness gap under concurrent access to a cold session.

## 10. Summary: where the performance decisions live

| Concern | File | Mechanism |
|---|---|---|
| One resident chat model | `services/localmodels/server_manager.py` | `ensure_running` stop-before-start under `self._lock` |
| KV-cache size vs. context need | `services/localmodels/server_manager.py` | `_serving_context` = `min(known window, APOLLO_LLAMA_CONTEXT cap)` |
| ~50 tools → ~16 sent | `src/tool_index.py`, `src/agent_loop.py` | ChromaDB top-K retrieval + `ALWAYS_AVAILABLE` + keyword fallback |
| Prompt-preface memory bloat | `src/settings.py`, `src/chat_processor.py` | `memory_recall_max` (3), `memory_pinned_max` (15) |
| Trivial-turn latency | `services/model_router.py` | Deterministic regex classifier → light-model route, opt-in |
| SQLite concurrency | `core/database.py` | `check_same_thread=False`; **no WAL/synchronous pragma set** |
| Vector store reuse | `src/chroma_client.py` | Process-wide singleton `PersistentClient`/`HttpClient` |
| No-build-step staleness | `app.py` | `_RevalidatingStatic` → `Cache-Control: no-cache` + 304 revalidation |
| Session RAM growth | `core/session_manager.py` | **Unbounded** past the initial 100-session boot load; **KNOWN gap**, no eviction, no lock around hydration |
