# Apollo — AI Review Context Pack, Part 1: Overview and Key Code

This is source material for an AI reviewer looking for concrete optimization,
refactoring, and architecture opportunities. It is deliberately dense and
code-heavy. Every snippet below is verbatim from the working tree (paths and
line numbers are as of this scan) — nothing is paraphrased pseudocode.

## 1. What Apollo is

Apollo is a **self-hosted, local-first AI workspace** — README.md's own framing:

> Apollo is a self-hosted, local-first AI workspace for chatting with local or
> remote language models while keeping the workspace, model configuration, and
> application data under operator control.

It is one app that chats with language models (local GGUF via `llama.cpp`, or
any OpenAI-compatible/Anthropic/OpenRouter/Groq/Ollama endpoint) and wraps
that chat in a full workspace: a tool-running agent loop (shell, python, web,
browser, email, calendar, notes, tasks, memory, skills, image tools, MCP
servers), a managed SearXNG-backed web search sidecar, an embedded server-side
Chromium the agent can drive, deep research, ChromaDB-backed persistent
memory/skills, voice call mode (Whisper STT + Piper/Kokoro TTS), a
knowledge-graph memory view, a multi-tab document editor, a hardware-aware
model "Cookbook" for serving GGUFs/vLLM/SGLang, 24 themes, and an installable
PWA.

README.md describes the architecture explicitly:

> It is a **three-tier system**: a **FastAPI backend** (Python 3.11+, one
> `uvicorn` process) exposing ~40 modular routers, a **framework-free
> vanilla-JS frontend** (ES modules, server-sent events, no build step), and a
> **SQLite (SQLAlchemy) + ChromaDB data layer**. Everything runs as that one
> process plus on-demand `llama-server` subprocesses, a managed SearXNG
> sidecar, and the optional Paperclip Node sidecar.

Verified against the tree: `routes/*.py` defines **49** `setup_*_routes(...)`
factory functions (`grep -c "^def setup_.*_routes" routes/*.py`), wired into
the single FastAPI app in `app.py` via 21+ `build_and_include_router(app, ...)`
call sites (some span multiple lines). Companion/mobile routes
(`companion/routes.py`) and MCP server routes add more, consistent with the
"~40-57 routers" figure used to describe this codebase.

**Tech stack** (from `requirements.txt` / `pyproject.toml`): FastAPI + uvicorn,
SQLAlchemy against SQLite (`core/database.py`), ChromaDB (embedded, not a
server) for vectors, `fastembed` ONNX for local embeddings, `bs4`/`httpx` for
web content extraction, `pdfminer.six` for PDF text, `caldav`/`icalendar` for
calendar, no Celery/Redis (background jobs are in-process asyncio —
`src/task_scheduler.py`, `src/bg_jobs.py`), no ORM alternative, no message
queue. The frontend has **zero JS dependencies** at runtime beyond
`highlight.js`/KaTeX/mermaid loaded via plain `<script>` tags — see
`static/index.html`.

## 2. App construction and router wiring

`src/app_initializer.py` builds the manager/handler graph the routers depend
on (`initialize_managers`), then `app.py` wires routers on top of it. A
representative excerpt of the wiring style in `app.py`:

```python
# app.py (excerpt, grep "include_router")
build_and_include_router(app, "Auth", setup_auth_routes, auth_manager, logger=logger)
include_router_checked(app, upload_router, "Uploads", logger=logger)
...
build_and_include_router(app, "STT", setup_stt_routes, stt_service, logger=logger)
build_and_include_router(app, "Documents", setup_document_routes, session_manager, upload_handler, logger=logger)
build_and_include_router(app, "Signatures", setup_signature_routes, logger=logger)
build_and_include_router(app, "Gallery", setup_gallery_routes, logger=logger)
...
build_and_include_router(app, "MCP", setup_mcp_routes, mcp_manager, logger=logger)
build_and_include_router(app, "Integration status", setup_integration_routes, _paperclip_status_for_integrations, logger=logger)
build_and_include_router(app, "System status", setup_system_status_routes, ...)
build_and_include_router(app, "Local model proxy", setup_lmproxy_routes, ...)
```

Each `setup_*_routes(...)` is a **factory that returns an `APIRouter`**,
taking whatever managers it needs as explicit arguments (dependency injection
by function parameter, not a DI framework). `build_and_include_router` (in
`services/app_startup.py`) wraps `app.include_router(...)` with a try/except
so one router's import/construction failure is logged and skipped rather than
crashing the whole app at boot — a deliberate "degrade, don't die" posture
that recurs throughout the codebase (see the memory-vector-store degradation
pattern in `src/app_initializer.py` below).

`initialize_managers()` (`src/app_initializer.py:32-`) is the composition
root. Representative excerpt:

```python
# src/app_initializer.py:45-77
memory_manager = MemoryManager(DATA_DIR)
skills_manager = SkillsManager(DATA_DIR)
session_manager = SessionManager(SESSIONS_FILE)
set_session_manager(session_manager)  # Enable Session.add_message() persistence
upload_handler = UploadHandler(base_dir, UPLOAD_DIR)
personal_docs_manager = PersonalDocsManager(PERSONAL_DIR, rag_manager)
api_key_manager = APIKeyManager(DATA_DIR)
preset_manager = PresetManager(DATA_DIR)

# Initialize memory vector store (share embedding model with RAG if available)
memory_vector = None
try:
    from src.memory_vector import MemoryVectorStore
    embedding_model = getattr(rag_manager, '_model', None) if rag_manager else None
    memory_vector = MemoryVectorStore(DATA_DIR, embedding_model=embedding_model)
    if memory_vector.healthy:
        if memory_vector.count() == 0:
            existing = memory_manager.load()
            if existing:
                memory_vector.rebuild(existing)
        logger.info("MemoryVectorStore initialized")
    else:
        logger.warning("MemoryVectorStore DEGRADED: ChromaDB vector memory unavailable")
        memory_vector = None
except Exception as e:
    logger.warning(f"MemoryVectorStore DEGRADED: {e}")
    memory_vector = None
```

INTENT: managers are plain Python objects constructed once at boot and passed
around by reference — there is no service locator or DI container. The
try/except-and-degrade pattern around `MemoryVectorStore` shows the house
style: a missing optional subsystem should never take down chat.

## 3. The agent loop — `src/agent_loop.py` (2,331 lines)

This is the largest and most architecturally central module in `src/`. Its
job: turn one chat turn into N rounds of (stream LLM output → detect tool
calls → execute tools → feed results back → repeat) until the model produces
a final answer, a round cap is hit, or a "stuck" heuristic bails out.

### 3.1 Tool selection happens before the first LLM call

Before any model call, the loop decides **which tools the model is even told
about** — sending all ~70 tool schemas to every request would blow the prompt
budget and confuse smaller local models:

```python
# src/agent_loop.py:1412-1462 (abridged)
_relevant_tools = relevant_tools
if not _relevant_tools:
    try:
        from src.tool_index import get_tool_index, ALWAYS_AVAILABLE
        tool_idx = get_tool_index()
        if tool_idx:
            if mcp_mgr:
                await asyncio.wait_for(
                    asyncio.to_thread(tool_idx.index_mcp_tools, mcp_mgr, _mcp_disabled_map),
                    timeout=_TOOL_SELECTION_TIMEOUT_SECONDS,
                )
            if _retrieval_query:
                _relevant_tools = await asyncio.wait_for(
                    asyncio.to_thread(tool_idx.get_tools_for_query, _retrieval_query, 8),
                    timeout=_TOOL_SELECTION_TIMEOUT_SECONDS,
                )
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
    _relevant_tools.update({"create_document", "manage_memory", "manage_notes"})
```

INTENT: this is a two-tier degrading retrieval strategy — ChromaDB semantic
search with a hard 1.5s timeout (`_TOOL_SELECTION_TIMEOUT_SECONDS`), falling
back to a hand-maintained keyword-hint dict (`ToolIndex._KEYWORD_HINTS`, ~60
entries — see part 2) if the vector index is unavailable or slow. Both paths
seed from `ALWAYS_AVAILABLE` (bash, python, web_search, web_fetch, read_file,
api_call, list_served_models, stop_served_model, app_api) so a handful of
tools never disappear regardless of retrieval quality.

### 3.2 The round loop itself

```python
# src/agent_loop.py:1621-1699 (abridged — the per-round LLM call)
for round_num in range(1, max_rounds + 1):
    round_response = ""
    native_tool_calls = []
    ...
    if _force_answer:
        all_tool_schemas = []          # loop-breaker: force a final answer
    elif _is_api_model:
        # Filter FUNCTION_TOOL_SCHEMAS + MCP schemas down to _relevant_tools
        ...
    else:
        # Local models: only send MCP schemas if the message hints at MCP use
        _wants_mcp = any(kw in _last_user.lower() for kw in _MCP_KEYWORDS)
        all_tool_schemas = mcp_schemas if (_wants_mcp and mcp_schemas) else []

    _candidates = [(endpoint_url, model, headers)] + list(fallbacks or [])
    _round_deadline = time.time() + max(agent_stream_timeout * 4, 1200)
    async for chunk in stream_llm_with_fallback(
        _candidates, messages, temperature=temperature, max_tokens=max_tokens,
        prompt_type=prompt_type if round_num == 1 else None,
        tools=all_tool_schemas if all_tool_schemas else None,
        timeout=agent_stream_timeout,
    ):
        if time.time() > _round_deadline:
            logger.warning(f"[agent] round {round_num} stream exceeded wall-clock deadline; cutting off")
            break
        ...  # parse SSE chunk: delta text, tool_call_delta (doc streaming),
             # native tool_calls, usage/tps metrics, fallback notices
```

Then, once the round's stream ends:

```python
# src/agent_loop.py:1845, 2058-2124 (abridged — tool resolution + execution)
tool_blocks, used_native = _resolve_tool_blocks(round_response, native_tool_calls, round_num)
...
for i, block in enumerate(tool_blocks):
    if max_tool_calls > 0 and total_tool_calls >= max_tool_calls:
        yield f'data: {json.dumps({"type": "budget_exceeded", ...})}\n\n'
        budget_hit = True
        break
    total_tool_calls += 1
    yield f'data: {json.dumps({"type": "tool_start", "tool": block.tool_type, ...})}\n\n'

    _progress_q: asyncio.Queue = asyncio.Queue()
    async def _push_progress(payload):
        await _progress_q.put(payload)

    async def _run_tool():
        try:
            return await execute_tool_block(
                block, session_id=session_id, disabled_tools=disabled_tools,
                owner=owner, progress_cb=_push_progress,
            )
        finally:
            await _progress_q.put(None)   # sentinel

    _tool_task = asyncio.create_task(_run_tool())
    while True:
        evt = await _progress_q.get()
        if evt is None:
            break
        yield f'data: {json.dumps({"type": "tool_progress", ...})}\n\n'

    try:
        desc, result = await _tool_task
    except Exception as _tool_err:
        # Tool layer is CONTRACTUALLY supposed to return error dicts, not raise.
        # This is the defensive catch-all so one bad tool can't kill the SSE stream.
        logger.exception("tool execution raised unexpectedly: %s", block.tool_type)
        desc = f"{block.tool_type}: internal error"
        result = {"error": f"Tool crashed internally: {_tool_err}", "exit_code": 1}
```

INTENT: tool execution runs as a background `asyncio.Task` while a
`Queue`-based progress channel streams `tool_progress` events (elapsed time +
tail of output) to the frontend for long-running bash/python calls — the
model isn't blocked, and the user sees the tool "still running" instead of a
frozen spinner. The `try/except` around `await _tool_task` is a documented
defensive boundary: tools are supposed to return `{"error": ...}` dicts, not
raise, but the loop no longer trusts that contract blindly (a comment nearby
notes this used to kill the whole SSE stream mid-round).

### 3.3 Loop-breaker and MAX_AGENT_ROUNDS

```python
# src/agent_loop.py:1606-1614
# Loop-breaker state. Small models (e.g. deepseek-v4-flash) can get
# stuck firing the same tool call over and over with no text — burns
# all 20 rounds, looks like the chat "died". Track recent call
# signatures + consecutive no-text tool rounds to bail early.
_recent_call_sigs = collections.deque(maxlen=6)
_stuck_rounds = 0
_tool_type_counts: collections.Counter = collections.Counter()
_force_answer = False  # set by loop-breaker → next round runs with NO tools
```

`MAX_AGENT_ROUNDS = 20` is defined in `src/agent_tools.py:22` — a single
hardcoded cap shared by every session regardless of model capability or task
complexity. When the loop-breaker trips, `_force_answer` is set and the
*next* round is sent with `all_tool_schemas = []`, forcing the model to
either answer in prose or have its (likely hallucinated) tool call discarded:

```python
# src/agent_loop.py:1851-1854
if _force_answer:
    if tool_blocks:
        logger.info(f"[agent] force-answer round {round_num}: discarding {len(tool_blocks)} ignored tool call(s)")
    tool_blocks = []
```

## 4. Tool-call parsing across model dialects — `src/tool_parsing.py`

Local models emit tool calls in wildly different formats depending on their
chat template. Apollo normalizes several dialects into one canonical
`<invoke name="...">` XML form before parsing:

```python
# src/tool_parsing.py:87-108
def _normalize_function_eq(text: str) -> str:
    """Normalize the Qwen/Llama-3 native function-call dialect to <invoke> form.

    Models whose chat template teaches `<function=NAME><parameter=KEY>value
    </parameter></function>` (Qwen 3.x, Llama 3.1+ official templates) emit
    exactly that when driven through the fenced-block prompt — observed live
    from Qwen3-365-A3B, whose reference_search call fell through as plain
    text. Rewrites into the standard <invoke name=...> form so the existing
    parser (with its known-tool guard) handles it; a stray </tool_call>
    closer some models append is dropped when unopened.
    """
    if not isinstance(text, str) or "<function=" not in text:
        return text
    t = text
    t = re.sub(r'<function=["\']?(\w+)["\']?\s*>', r'<invoke name="\1">', t, flags=re.IGNORECASE)
    t = re.sub(r"</function>", "</invoke>", t, flags=re.IGNORECASE)
    t = re.sub(r'<parameter=["\']?(\w+)["\']?\s*>', r'<parameter name="\1">', t, flags=re.IGNORECASE)
    if "<tool_call>" not in t.lower():
        t = re.sub(r"</tool_call>", "", t, flags=re.IGNORECASE)
    return t
```

There is a sibling `_normalize_dsml` (lines 71-85) for DeepSeek's fullwidth-pipe
`<｜｜DSML｜｜tool_calls>` markup, plus separate regex families for
`[TOOL_CALL]{...}` blocks (some fine-tunes), `<tool_code>{...}` blocks
(MiniMax-M2.5 style), and standard fenced ` ```bash ` code blocks. All of them
converge on `parse_tool_blocks()`:

```python
# src/tool_parsing.py:362-397 (abridged)
def parse_tool_blocks(text: str) -> List[ToolBlock]:
    blocks = []
    text = _normalize_dsml(text)
    text = _normalize_function_eq(text)

    # Pattern 1: fenced code blocks — the primary/preferred format
    for m in _TOOL_BLOCK_RE.finditer(text):
        tag = m.group(1).lower()
        content = m.group(2).strip()
        if not content:
            continue
        if '<invoke' in content:
            # Some models wrap an <invoke> call INSIDE a ```python fence.
            invoked = False
            for inv in _XML_INVOKE_RE.finditer(content):
                block = _parse_xml_invoke(inv)
                if block:
                    blocks.append(block)
                    invoked = True
            if invoked:
                continue
        blocks.append(ToolBlock(tag, content))

    if not blocks:                       # Pattern 2: [TOOL_CALL] blocks
        ...
    if not blocks:                       # Pattern 3: <tool_call>/<invoke>
        ...
    if not blocks:                       # Pattern 4: <tool_code> (MiniMax)
        ...
    return blocks
```

INTENT: this is a regex cascade, tried in priority order, that exists purely
to compensate for the fact that local models don't reliably follow the
fenced-block convention Apollo's system prompt teaches. Every new local model
family observed to deviate gets its own normalization pass bolted on here —
see Part 2 for why this is flagged as a fragility risk. `_parse_xml_invoke`
delegates the actual content-shaping to `function_call_to_tool_block` in
`src/tool_schemas.py` — the same converter native OpenAI-style function calls
use — specifically so a tool only needs correct per-tool argument handling in
one place (a comment at `tool_parsing.py:279-290` documents this consolidation
and the bug it fixed: `manage_calendar`/`create_event` calls silently vanishing
because an older hand-rolled XML→text serializer didn't know about them).

## 5. Tool selection index — `src/tool_index.py` (499 lines)

`ToolIndex` is a ChromaDB-backed semantic retriever over hand-written tool
descriptions (`BUILTIN_TOOL_DESCRIPTIONS`, ~65 entries, richer prose than the
system-prompt one-liners, meant for embedding quality). Core retrieval:

```python
# src/tool_index.py:276-297
def retrieve(self, query: str, k: int = 8) -> List[str]:
    """Retrieve the top-K most relevant tool names for a query."""
    try:
        query_embedding = self._embed([query])
        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=min(k, self._collection.count() or k),
            include=["metadatas", "distances"],
        )
        if not results or not results.get("metadatas"):
            return []
        tool_names = []
        for meta_list in results["metadatas"]:
            for meta in meta_list:
                name = meta.get("tool_name", "")
                if name and name not in tool_names:
                    tool_names.append(name)
        return tool_names
    except Exception as e:
        logger.warning(f"Tool retrieval failed: {e}")
        return []
```

`get_tools_for_query()` layers **three signal sources** on top of raw vector
retrieval: `ALWAYS_AVAILABLE` tools, the semantic top-K, and a large
hand-maintained `_KEYWORD_HINTS` dict (frozenset-of-keywords → tool-name-set,
~25 entries covering email, calendar, notes, sessions, tasks, cookbook,
themes, documents, etc.), plus a regex (`_SCHEDULE_RE`) that force-includes
`manage_tasks` whenever the message looks like a recurring-schedule request
("every day", "at 7:30am", typo-tolerant "every dya"):

```python
# src/tool_index.py:447-470 (abridged)
def get_tools_for_query(self, query: str, k: int = 8, always_include=None) -> Set[str]:
    base = set(always_include or ALWAYS_AVAILABLE)
    retrieved = self.retrieve(query, k=k)
    base.update(retrieved)
    ql = query.lower()
    for keywords, tools in self._KEYWORD_HINTS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", ql) for kw in keywords):
            base.update(tools)
    if self._SCHEDULE_RE.search(ql):
        base.add("manage_tasks")
    return base
```

INTENT: pure semantic retrieval on tool descriptions under-triggers for
short, contextless follow-ups ("do it every morning") — the keyword layer is
a hand-tuned patch on top of embeddings, not a replacement for them. This is
the third of four tool registries described in Part 2's pain-points section.

## 6. Local model process management — `services/localmodels/server_manager.py`

`LocalModelServer` owns exactly two subprocess "slots" — `self._chat` and
`self._embed` — enforcing a **single warm chat model at a time**:

```python
# services/localmodels/server_manager.py:142-169
def ensure_running(self, ref: str) -> str:
    m = self._resolve(ref)
    if m is None:
        self.refresh_catalog()
        m = self._resolve(ref)
    if m is None:
        raise LookupError(f"Unknown local model: {ref!r}")
    if m.kind == "unsupported":
        raise ValueError(f"'{m.name}' ... is not a chat-capable model — llama-server cannot serve it")
    with self._lock:
        slot = self._embed if m.kind == "embedding" else self._chat
        if slot and slot.model_id == m.id and slot.proc.poll() is None:
            return slot.base_url          # already running — reuse
        if slot:
            self._stop_proc(slot)         # SWAP: kill the old model first
        proc = self._launch(m)
        if m.kind == "embedding":
            self._embed = proc
        else:
            self._chat = proc
        return proc.base_url
```

`_launch()` picks a free ephemeral port, builds a `llama-server` command line
(`--model`, `--host`, `--port`, `-c <context>`), and blocks on
`_wait_health()` polling `GET /health` with a size-scaled timeout
(`~40s per GB`, since an 8GB GGUF on a slow disk can take minutes to load):

```python
# services/localmodels/server_manager.py:260-284 (abridged)
def _health_timeout_for(self, m: LocalModel) -> float:
    size_gb = (m.size_bytes or 0) / (1024 ** 3)
    return max(self._health_timeout, size_gb * 40.0)

def _wait_health(self, base_url, proc, log_path, timeout=None) -> None:
    deadline = time.monotonic() + (timeout if timeout else self._health_timeout)
    url = base_url + "/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited early (code {proc.returncode}):\n{_tail(log_path)}")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.5)
    raise TimeoutError("llama-server did not become healthy in time")
```

INTENT: this VRAM-driven "swap, don't stack" policy is discussed further in
Part 3. Note the module docstring is explicit about the design: `"""Launch
and track local llama-server processes (single warm chat model)."""`.

## 7. Middleware — `core/middleware.py` (111 lines, quoted near-fully)

Two things live here: the admin gate used by most routers, and the security
headers middleware applied to every response.

```python
# core/middleware.py:17-22
# Per-process token that lets the in-app tool layer hit admin-gated
# routes via HTTP loopback (the agent's tool calls don't carry the
# admin user's session cookie). Set once at import; tools read the
# same value from this module. Never persisted or exposed externally.
INTERNAL_TOOL_TOKEN = os.environ.get("APOLLO_INTERNAL_TOKEN") or secrets.token_hex(32)
INTERNAL_TOOL_HEADER = "X-Apollo-Internal-Token"


def require_admin(request: Request):
    """Raise 403 if the current user isn't an admin.
    Allows access when auth is explicitly disabled, or when the request carries
    the in-process internal-tool token used by loopback agent tools.
    """
    try:
        hdr = request.headers.get(INTERNAL_TOOL_HEADER)
        if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
            return
        if getattr(request.state, "internal_tool", False):
            return
    except Exception as error:
        report_exception(logger, "internal_tool_token_check_failed", error, outcome="best_effort")

    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return
    if not auth_mgr or not auth_mgr.is_configured:
        raise HTTPException(403, "Admin only")
    user = getattr(request.state, "current_user", None)
    if not user or not auth_mgr.is_admin(user):
        raise HTTPException(403, "Admin only")
```

INTENT: this is the mechanism that lets the agent's own tool calls (e.g.
`manage_calendar` hitting `/api/calendar/...` internally) act with admin
privilege without carrying the logged-in user's session cookie — a
constant-time-compared per-process random token instead. `SecurityHeadersMiddleware`
(lines 58-111) stamps `X-Content-Type-Options`, `Referrer-Policy`,
`X-Frame-Options: DENY`, and a nonce-based CSP on every response, with
carve-outs for tool-render iframes and the self-contained research-report
HTML pages. A comment explicitly documents a known compromise: `style-src
'unsafe-inline'` is kept because `static/index.html` ships inline `<style>`
blocks and JS sets runtime `style=""` attributes — "Migrating to nonce-only
requires templating the HTML files + auditing every JS-set style attribute."

## 8. Auth — `routes/auth_routes.py` — `_require_admin_user`

Note this router does **not** delegate to `core.middleware.require_admin` —
it has its own, stricter admin gate, and the reason is documented inline:

```python
# routes/auth_routes.py:88-110
def _require_admin_user(request: Request) -> Optional[str]:
    """Admin gate for this router — strict, plus the desktop-mode allowance.

    Delegating wholesale to core.middleware.require_admin is WRONG here:
    that helper trusts ``request.state.current_user``, which the auth
    middleware populates through loopback/bypass paths. On a direct
    loopback request that turned GET /api/auth/users from 403 into 200
    for an UNAUTHENTICATED caller (verified by A/B against main), leaking
    usernames and privilege flags.

    So keep the strict cookie-validating check exactly as it was, and add
    only the one thing that was missing: the no-login desktop mode
    (AUTH_ENABLED=false) that the macOS bundle launcher ships and that
    every require_admin route already honors. Without this, Settings
    saves, integrations CRUD and the Users panel all 403 in the mode the
    app ships in.
    """
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return None
    user = _get_current_user(request)
    if not user or not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")
    return user
```

INTENT: this is a documented regression fix, not stylistic duplication — the
generic `require_admin` trusts `request.state.current_user`, which the
loopback path can populate for non-admin/unauthenticated internal calls in
ways that are safe for *tool* routes but were unsafe for the *user-listing*
auth route. `_require_admin_user` is called from 15 separate spots in this
573-line file — see Part 3 for the "should this be unified" question.

## 9. Chat streaming route — `routes/chat_routes.py` (1,478 lines)

`POST /api/chat_stream` (line 419) parses the form/JSON body, resolves the
session, decides chat-vs-agent mode (with intent-based auto-escalation from
chat→agent when the message looks like a notes/calendar request), then
branches into two streaming code paths. The **agent-mode branch** wires the
route directly to `stream_agent_loop`:

```python
# routes/chat_routes.py:1113-1128
async for chunk in stream_agent_loop(
    sess.endpoint_url, sess.model, messages,
    headers=sess.headers,
    temperature=ctx.preset.temperature, max_tokens=ctx.preset.max_tokens,
    prompt_type=preset_id,
    max_tool_calls=_tool_budget,
    context_length=ctx.context_length,
    active_document=active_doc,
    session_id=session,
    disabled_tools=disabled_tools if disabled_tools else None,
    owner=_user,
    fallbacks=_fallback_candidates,
):
    if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
        ...  # re-parse the SSE JSON to accumulate full_response, track
             # agent_rounds/tool_calls for metrics, forward the chunk through
```

The route does **not** return `StreamingResponse(stream_with_save(), ...)`
directly. Instead it runs the whole generator as a **detached background
task** and the HTTP response only subscribes to a replay/live buffer:

```python
# routes/chat_routes.py:1213-1228
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
```

INTENT: this is a deliberate architectural choice — `src/agent_runs.py`
decouples the agent's execution lifetime from the HTTP connection lifetime,
so a long tool-using turn (e.g. a multi-minute `bash` build) keeps running
and gets saved to the session even if the user closes the tab; reconnecting
via `GET /api/chat/resume/{session_id}` re-subscribes to the same live run.
This is the mechanism a Part-2 pain point (session map growth) interacts
with, since `agent_runs` and `SessionManager.sessions` are separate
in-memory maps with separate (and separately incomplete) lifecycle handling.

## 10. Frontend — no-framework ES modules

`static/index.html` loads the app as **plain ES modules with no bundler**:

```html
<!-- static/index.html:2696- -->
<script type="module" src="/static/js/storage.js"></script>
<script type="module" src="/static/js/ui.js"></script>
<script type="module" src="/static/js/markdown.js"></script>
<script type="module" src="/static/js/dragSort.js"></script>
<script type="module" src="/static/js/sessions.js"></script>
<script type="module" src="/static/js/memory.js"></script>
<script type="module" src="/static/js/memoryGraph.js"></script>
<script type="module" src="/static/js/skills.js"></script>
...
```

`static/js/` contains 164 `.js` files (`find static/js -name "*.js" | wc -l`),
organized as flat feature modules (`chat.js`, `calendar.js`, `gallery.js`,
`settings.js`, ...) plus a handful of subdirectories for larger features
(`document/`, `email/`, `chat/`, `calendar/`, `compare/`, `markdown/`,
`color/`) and a `MODULE_SUMMARY.md` index file. `static/js/chatStream.js`
(284 lines) is a representative "extracted from the monolith" module — its
own header comment says as much:

```javascript
// static/js/chatStream.js:1-9
// static/js/chatStream.js
// SSE event handlers extracted from chat.js handleChatSubmit
// Handles: ui_control events, background stream management

import uiModule from './ui.js';
import Storage from './storage.js';
import themeModule from './theme.js';
import markdownModule from './markdown.js';
import sessionModule from './sessions.js';

export function handleUIControl(uiData) {
  var uiEvent = uiData.ui_event || uiData;
  var esc = uiModule.esc;
  try {
    if (uiEvent === 'toggle' || uiData.ui_event === 'toggle') {
      var toggleMap = {
        web: 'web-toggle', bash: 'bash-toggle', rag: 'rag-toggle',
        research: 'research-toggle', incognito: 'incognito-toggle',
      };
      ...
      var ts = Storage.getJSON(Storage.KEYS.TOGGLES, {});
      ts[uiData.toggle_name] = !!uiData.state;
      Storage.setJSON(Storage.KEYS.TOGGLES, ts);
    } else if (uiEvent === 'set_mode' || uiData.ui_event === 'set_mode') {
      ...
    }
  } ...
}
```

INTENT: this handles the `ui_control` SSE event type emitted from
`agent_loop.py`'s tool-result branch (Section 3.2 above) — the agent can
toggle UI state (theme, mode, panel visibility) as a *side effect of a tool
call*, and this module is the client-side interpreter for those events. Note
the `var`-based ES5-flavored style even inside an ES module — consistent
across the codebase; there's no build step to run through Babel/TS, so
whatever syntax runs unmodified in evergreen browsers is what's written
directly, and older idioms (`var`, manual DOM lookups, string-built HTML)
persist next to newer syntax (`import`/`export`) without a linter/formatter
pass unifying the two. `document.js` alone is 9,453 lines (see Part 2's
module-size pain point) — the no-bundler constraint means large features
cannot be code-split; every module ships in full on every page load.

## 11. Where the four tool registries live (cross-reference for Part 2)

Part 2 documents the "four separate tool registries" pain point in detail.
For reference, their exact locations:

1. `src/tool_schemas.py:23` — `FUNCTION_TOOL_SCHEMAS` (OpenAI-style JSON
   schemas, sent natively to API models).
2. `src/agent_loop.py:174` — `TOOL_SECTIONS` (prompt text taught to local
   models via fenced-block examples).
3. `src/tool_index.py:63` — `BUILTIN_TOOL_DESCRIPTIONS` (ChromaDB-embedded
   descriptions for RAG tool selection).
4. `src/agent_tools.py:29` — `TOOL_TAGS` (the set that drives the fenced-block
   parsing regex in `tool_parsing.py` — a tool absent here can never even be
   *parsed* out of model output, regardless of what's in the other three).
