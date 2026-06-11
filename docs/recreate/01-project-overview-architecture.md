# Apollo — Project Overview & Architecture

Apollo (repo: `/Users/Antman/Apollo`, GitHub `https://github.com/Antman1526/Apollo`, `core/constants.py` → `APP_VERSION = "0.9.1"`, FastAPI app version string `1.0.0`) is a self-hosted AI workspace — the local-first, privacy-first equivalent of the ChatGPT/Claude UI experience, running entirely on your own hardware. It is a renamed distribution of **Odysseus** by pewdiepie-archdaemon (MIT licensed; see `ACKNOWLEDGMENTS.md`). This document specifies the system precisely enough that another AI can rebuild it from scratch.

## 1. Purpose

A single FastAPI process serves a vanilla-JS single-page app, talks to any OpenAI-compatible LLM (vLLM, llama.cpp, Ollama, OpenRouter, OpenAI), persists everything in SQLite + JSON files under `data/`, and optionally supervises a bundled **Paperclip** agent-management sidecar. There is no frontend build step: `static/` ships raw ES modules. The design priorities are: localhost-by-default networking, auth-on-by-default, graceful degradation when optional deps (ChromaDB, playwright, PyMuPDF, …) are missing, and "treat it like an admin console" security posture.

## 2. Feature list (from `README.md`)

- **Chat** — any local model or API (vLLM, llama.cpp, Ollama, OpenRouter, OpenAI).
- **Local Models** — point Apollo at folders of GGUF models; auto-discovered, auto-served via llama.cpp when picked (single warm chat model; dirs configurable in Settings → AI).
- **Agent** — tool-running agent built on opencode concepts: MCP, web, files, shell, skills, memory.
- **Ralph Loop** — opt-in PRD/task loop (`scripts/apollo-ralph`): `prd.json`, `progress.md`, `AGENTS.learning.md`, quality gates, state under `.apollo/ralph/`.
- **Browser + Crawl Agents** — browser-use verifies real UI workflows (Paperclip Floor QA); crawl4ai turns web sources into research-ready Markdown.
- **Cookbook** — hardware scan, model recommendations (built on llmfit), VRAM-aware fit scoring, GGUF/FP8/AWQ download + serve via vLLM/llama.cpp (tmux-backed background jobs).
- **Deep Research** — multi-step gather/read/synthesize runs producing a visual report (adapted from Tongyi DeepResearch).
- **Compare** — blind side-by-side A/B model comparison with synthesis.
- **Documents** — multi-tab editor (markdown/HTML/CSV), AI edits & suggestions, versioned.
- **Memory / Skills** — persistent memory + skills: ChromaDB, fastembed (ONNX), vector + keyword retrieval, import/export.
- **Email** — IMAP/SMTP inbox with AI triage: urgency reminders, auto-tag, auto-summary, auto-reply drafts, auto-spam; per-account routing, CalDAV-aware.
- **Notes & Tasks** — notes with reminders, todo list, cron-style scheduled tasks; ntfy / browser / email notification channels.
- **Calendar** — local-first calendar with CalDAV sync (Radicale / Nextcloud / Apple / Fastmail), `.ics` import/export.
- **Mobile / PWA** — responsive, installable, touch gestures (`static/manifest.json`, `static/sw.js`).
- **Paperclip sidecar** — agent-management UI with three views: **Floor** (isometric animated office, SSE-fed), **Board** (kanban), **Classic** (iframe of Paperclip's own UI).
- **Embedded browser panel** — sandboxed in-app panel + shared Playwright Chromium session; agent `browser` tool and `/api/browser/*` HTTP contract; only `http://`/`https://` navigation allowed.
- **Extras** — image editor, theme editor, file uploads (vision + PDF), web search (SearXNG/Brave/Tavily/Serper/Google PSE/DDG), presets, sessions, 2FA (pyotp), API tokens, webhooks, vault, gallery, TTS/STT.

## 3. Technology stack with versions

Versions below are the declared specs from `requirements.txt` (mostly unpinned) with the **actually-installed versions** from the repo's working `venv/` (Python 3.12.13) — use these for a faithful rebuild.

| Layer | Package | Installed version | Role |
|---|---|---|---|
| Web | fastapi | 0.136.3 | ASGI app, routers, middleware |
| Web | uvicorn | 0.49.0 | ASGI server (`python -m uvicorn app:app`) |
| Web | python-multipart | 0.0.32 | form/file uploads |
| Web | websockets | 16.0 | Paperclip reverse-proxy WS (`routes/paperclip_routes.py`) |
| Config | python-dotenv | 1.2.2 | `.env` loading (`utf-8-sig`) |
| Models | pydantic / pydantic-settings | 2.12.5 / 2.14.1 | request models, settings (`>=2.0` pins) |
| Data | SQLAlchemy | 2.0.50 | ORM over SQLite (`core/database.py`) |
| HTTP | httpx | 0.28.1 | all outbound LLM/API calls |
| Vector | chromadb | 1.5.9 | embedded PersistentClient or HttpClient |
| Embeds | fastembed | 0.8.0 | local ONNX embeddings fallback |
| Parse | pypdf 6.10.2, beautifulsoup4 4.14.3, charset-normalizer 3.4.7, markdown 3.10.2 | — | PDF text, HTML scrape, report rendering |
| Calendar | icalendar 7.1.2, python-dateutil 2.9.0.post0, caldav 3.2.1 | — | .ics, rrule expansion, CalDAV sync |
| Security | cryptography 48.0.0, bcrypt 5.0.0, pyotp 2.9.0, qrcode 8.2 | — | Fernet at-rest encryption, password hashing, 2FA |
| Agent | mcp 1.26.0 | — | Model Context Protocol client |
| Sched | croniter 6.2.2 | — | cron-expression tasks |
| Test | pytest 9.0.3, pytest-asyncio 1.4.0 | — | test suite (`asyncio_mode = "auto"` in `pyproject.toml`) |
| Research | Crawl4AI 0.8.9 | — | mandatory web→Markdown import |
| Misc | numpy 2.4.6, youtube-transcript-api 1.2.4 | — | vectors, YT transcripts |

Optional (`requirements-optional.txt`): `faster-whisper` (local STT), `piper-tts` (local TTS, Mac/Metal-friendly), `duckduckgo-search`, `PyMuPDF` (AGPL — PDF form-filling only), `markitdown[docx,pptx,xlsx,xls]==0.1.5` (Office/EPUB extraction). Isolated (`requirements-browser-use.txt`, installed into `.apollo/browser-use-venv` because browser-use 0.13.0 pins `aiohttp==3.13.4` while ChromaDB needs `>=3.13.5`): `browser-use==0.13.0`, `litellm`.

`package.json` — no build tooling, only: dependencies `@anthropic-ai/sdk ^0.98.0`; devDependencies `@antithesishq/bombadil ^0.3.2`; scripts `check` → `bash scripts/check.sh`, `test:js` → `node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs tests/test_system_status_actions.mjs tests/test_theme_presets.mjs`.

System: Python 3.11+ required (Homebrew `python@3.11`+ on macOS arm64 — must be an arm64 interpreter); `tmux` (Cookbook background jobs); `llama.cpp` (`brew install llama.cpp` → `llama-server` binary); Node (host Node only needed for JS tests; the Paperclip sidecar auto-downloads pinned Node **22.13.0** into `~/.apollo/.node`, `services/paperclip/node_bootstrap.py`); Paperclip itself pinned to `v2026.529.0` (`services/paperclip/runtime.py` `DEFAULT_VERSION`, and Docker build context `https://github.com/paperclipai/paperclip.git#v2026.529.0`).

## 4. Three-tier architecture

```
Tier 1 — Frontend  static/            raw ES modules, no bundler
  index.html, login.html, app.js, style.css, sw.js, manifest.json
  static/js/ (79 modules: chat.js, cookbook.js, paperclip.js, calendar/, editor/, ...)

Tier 2 — Backend   app.py             slim orchestrator (~1230 lines)
  routes/    53 files — HTTP endpoint factories (setup_<name>_routes)
  services/  domain packages (localmodels/, paperclip/, memory/, search/, hwfit/, ...)
  src/       shared engine code (llm_core, agent_loop, agent_tools, chat_processor,
             task_scheduler, mcp_manager, rag_*, settings, secret_storage, ...)
  core/      auth.py, database.py, middleware.py, constants.py, session_manager.py,
             platform_compat.py, exceptions.py, atomic_io.py
  companion/ pairing/companion routes; mcp_servers/ built-in MCP stdio servers

Tier 3 — Data      data/ (gitignored)
  app.db (SQLite via SQLAlchemy), auth.json, settings.json, features.json,
  memory.json, chroma/ (embedded vector store), memory_vectors/, uploads/, ...
```

### 4.1 Data tier detail

`core/database.py` defines the SQLite engine and all models:

```python
# core/database.py
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@event.listens_for(Engine, "connect")          # fires for ALL engines in the process
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

Tables (`__tablename__`): `sessions`, `chat_messages`, `documents`, `document_versions`, `gallery_albums`, `gallery_images`, `email_accounts`, `model_endpoints`, `mcp_servers`, `comparisons`, `signatures`, `api_tokens`, `webhooks`, `user_tools`, `user_tool_data`, `crew_members`, `scheduled_tasks`, `task_runs`, `editor_drafts`, `memories` (plus notes/calendar/integration tables added by migrations). Conventions: string UUID primary keys, `TimestampMixin` (`created_at`/`updated_at` naive-UTC), an indexed nullable `owner` column on every user-data table (NULL = legacy/shared), composite indexes for hot queries (`ix_messages_session_time`, `ix_scheduled_tasks_due`, …), `ondelete="CASCADE"` for true children (messages, versions, task runs) and `ondelete="SET NULL"` for soft links (documents→session, images→album).

Schema evolution is done with dozens of idempotent `_migrate_*` functions (each guarded by `PRAGMA table_info`, safe on every boot) rather than Alembic, plus `_migrate_assign_legacy_owner()` which sweeps NULL-owner rows to the admin at boot and hourly. Sensitive columns use the `EncryptedText` TypeDecorator:

```python
# core/database.py
class EncryptedText(TypeDecorator):
    """Text column transparently encrypted at rest via src.secret_storage."""
    impl = Text
    cache_ok = True
    def process_bind_param(self, value, dialect):
        if value is None: return None
        from src.secret_storage import encrypt
        return encrypt(value)                  # Fernet, "enc:" prefix
    def process_result_value(self, value, dialect):
        if value is None: return None
        from src.secret_storage import decrypt
        return decrypt(value)
```

Used for `ModelEndpoint.api_key`, `Signature.data_png`/`svg`, and email passwords. Key lives at `data/.app_key` (mode 0600, gitignored); threat model is "stolen SQLite backup", not live-process compromise.

## 5. Architectural patterns

### 5.1 Router factories registered through `services/app_startup.py`

Every endpoint module exports `setup_<name>_routes(...)` returning an `APIRouter`. `app.py` registers them through labeled guards so a failing module produces a named startup error:

```python
# services/app_startup.py
@dataclass(frozen=True)
class RouterSpec:
    label: str
    factory: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)

def build_and_include_router(app, label, factory, *args, logger=None, **kwargs):
    """Build a router inside the labeled registration guard, then include it."""
    try:
        router = factory(*args, **kwargs)
    except Exception as exc:
        if logger:
            logger.exception("Failed to build %s routes", label)
        raise RuntimeError(f"Failed to build {label} routes") from exc
    return include_router_checked(app, router, label, logger=logger)
```

Usage in `app.py` (dependencies are injected as factory args — no global DI container):

```python
# app.py
register_router_specs(app, [
    RouterSpec("Emoji", setup_emoji_routes),
    RouterSpec("Sessions", setup_session_routes, args=(session_manager, session_config),
               kwargs={"webhook_manager": webhook_manager}),
    RouterSpec("Memory", setup_memory_routes, args=(memory_manager, session_manager),
               kwargs={"memory_vector": memory_vector}),
    ...
], logger=logger)
build_and_include_router(app, "Cookbook", setup_cookbook_routes, logger=logger)
```

### 5.2 AuthMiddleware with exemption lists

Auth is a single `BaseHTTPMiddleware` in `app.py` (only when `AUTH_ENABLED != "false"`). Three credential paths: internal-tool token (loopback only), `Bearer ody_*` API tokens (bcrypt-checked against an in-memory prefix cache, invalidated via `app.state.invalidate_token_cache`), and the session cookie (`SESSION_COOKIE` from `routes/auth_routes.py`). Exemptions:

```python
# app.py
AUTH_EXEMPT_EXACT = {
    "/api/auth/setup", "/api/auth/signup", "/api/auth/login", "/api/auth/logout",
    "/api/auth/status", "/api/auth/features", "/api/auth/settings",
    "/api/auth/integrations/presets", "/api/health", "/api/version", "/login",
    "/api/paperclip/events",   # handler self-authenticates (PAPERCLIP_EVENTS_TOKEN / loopback)
}
AUTH_EXEMPT_PREFIXES = ["/static", "/lmproxy"]  # /lmproxy guarded by its own bearer token
AUTH_EXEMPT_PATTERNS = [re.compile(r"^/api/tasks/[^/]+/webhook/[^/]+/?$")]  # path is the credential
```

`_is_trusted_loopback()` only trusts direct `127.0.0.1`/`::1` connections carrying **no** proxy-forwarding headers (`cf-connecting-ip`, `x-forwarded-for`, …) so a Cloudflare tunnel cannot inherit `LOCALHOST_BYPASS`. WebSockets bypass HTTP middleware, so the Paperclip WS proxy validates the session cookie itself via `ws_validate=lambda token: auth_manager.validate_token(token)`.

### 5.3 Event hub fan-out for the Paperclip Floor

`services/paperclip/events.py` is deliberately framework-free so routes and the live-events collector can both publish without an import cycle:

```python
# services/paperclip/events.py
class EventHub:
    """Fan-out hub feeding /api/paperclip/stream."""
    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: deque = deque(maxlen=history)   # replay buffer
        self._seq = 0
    def publish(self, events: list[dict]) -> int:
        for event in events:
            self._seq += 1
            entry = (self._seq, event)
            self._recent.append(entry)
            for queue in list(self._subscribers):
                try: queue.put_nowait(entry)
                except asyncio.QueueFull: pass        # slow subscribers drop, never backpressure
```

Producers: `POST /api/paperclip/events` (HTTP ingest) and `PaperclipCollector` (bridges Paperclip's realtime websocket). Consumer: `GET /api/paperclip/stream` SSE. Recognized `FLOOR_EVENT_TYPES`: `agent.status`, `heartbeat.run.queued|status|log|event`, `activity.logged`. While no events have been ingested the Floor plays a demo preview with the stream held open in a waiting state, switching to live automatically on first agent activity; with the sidecar disabled it falls back to the preview entirely. The lmproxy also publishes into the same hub (`publish_activity=_paperclip_hub.publish`) so the Floor pulses while an agent is generating tokens.

### 5.5 Labeled exception handling and the lmproxy

Domain exceptions (`core/exceptions.py`) map to stable JSON error envelopes via FastAPI exception handlers in `app.py`:

```python
# app.py
@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": "SESSION_NOT_FOUND", "message": str(exc)})
# InvalidFileUploadError → 400 INVALID_FILE_UPLOAD
# LLMServiceError       → 502 LLM_SERVICE_ERROR
# WebSearchError        → 502 WEB_SEARCH_ERROR
```

The local-model proxy resolves the warm model dynamically — Paperclip agents get one stable URL regardless of which GGUF is currently serving:

```python
# app.py
def _warm_chat_base_url():
    try:
        from services.localmodels.server_manager import get_server
        for slot in get_server().status().values():
            if slot.get("kind") == "chat" and slot.get("running"):
                return slot.get("base_url")
    except Exception as e:
        logger.debug("Warm chat base URL detection failed: %s", e, exc_info=True)
    return None

build_and_include_router(app, "Local model proxy", setup_lmproxy_routes,
    token_provider=_paperclip_proxy_token,     # bearer from ~/.apollo/paperclip_proxy_token
    warm_url_provider=_warm_chat_base_url,
    agent_lookup=_paperclip_agent_tokens.lookup,   # per-agent tokens (attribution + Floor pulse)
    publish_activity=_paperclip_hub.publish,
    logger=logger,
)
```

In native mode (`PAPERCLIP_MODE=native`) the startup hook spawns `paperclipai run` in a supervised thread, reusing an already-running instance and bootstrapping a pinned Node 22.13.0 into `~/.apollo/.node` first (`services/paperclip/runtime.py`, `node_bootstrap.py`); shutdown stops collector and runtime.

### 5.4 Single-warm-model llama.cpp serving

`services/localmodels/server_manager.py` ("Launch and track local llama-server processes (single warm chat model)") keeps exactly one warm chat process and one embedding process: selecting a different GGUF stops the old `llama-server` and launches `[binary, --model, path, --host, 127.0.0.1, --port, <free>, -c, <ctx>]` where ctx = `min(known model window, max(APOLLO_LLAMA_CONTEXT=16384, configured default 4096))`. Binary lookup tries `llama-server` on PATH, `~/.local/bin`, `~/llama.cpp/build/bin`, `/opt/homebrew/bin`, `/usr/local/bin`. `app.py` starts a non-blocking `startup_scan()` thread; `routes/lmproxy_routes.py` exposes the warm model as a stable OpenAI-compatible `/lmproxy/v1` for Paperclip agents (bearer token from `~/.apollo/paperclip_proxy_token`).

## 6. Component relationship diagram

```
 Browser (static/ SPA, PWA)                      external callers (Zapier/n8n/curl)
   │  fetch /api/*, SSE, WS                          │ Bearer ody_* / webhook_token
   ▼                                                 ▼
┌──────────────────────────── uvicorn / FastAPI (app.py) ───────────────────────────┐
│ AuthMiddleware → RequestTimeout(45s) → SecurityHeaders(CSP nonce) → CORS          │
│                                                                                    │
│ routes/ (53 setup_*_routes factories)                                              │
│   chat ─► src/chat_processor ─► src/llm_core ──httpx──► LLM endpoints              │
│   agent ─► src/agent_loop ─► src/agent_tools ─► src/mcp_manager ─► MCP servers     │
│   localmodels ─► services/localmodels (scanner→registry→server_manager)            │
│                                   └─spawns─► llama-server (Metal/CUDA, 1 warm)     │
│   lmproxy /lmproxy/v1 ─► warm llama-server  ◄──── Paperclip opencode agents        │
│   paperclip /paperclip/* ──reverse proxy──► Paperclip sidecar (Node, :3100)        │
│   paperclip /api/paperclip/stream ◄── EventHub ◄── collector WS / HTTP ingest      │
│   memory/skills ─► services/memory ─► chromadb (embedded data/chroma | HTTP)       │
│   search ─► services/search ─► SearXNG :8080 / Brave / Tavily / Serper / DDG       │
│   email ─► IMAP/SMTP   calendar ─► CalDAV   notes/tasks ─► src/task_scheduler      │
│                                                                                    │
│ core/database.py ─► SQLite data/app.db     core/auth.py ─► data/auth.json          │
└────────────────────────────────────────────────────────────────────────────────────┘
   Docker compose peers: chromadb :8100→8000, searxng :8080, ntfy :8091,
   paperclip + paperclip-db (postgres:17-alpine) behind --profile paperclip
```

## 7. Request lifecycle walkthrough

Middleware executes in **reverse registration order** (Starlette): registered CORS → SecurityHeaders → RequestTimeout → Auth, so an inbound request flows **AuthMiddleware → _RequestTimeoutMiddleware → SecurityHeadersMiddleware → CORSMiddleware → router**.

1. **Process boot**: `app.py` registers `.js/.mjs` MIME types, sets `HF_HUB_DISABLE_SYMLINKS=1` on Windows, then `load_dotenv(encoding="utf-8-sig")` (tolerates Notepad BOM — issue #142). FastAPI app + middleware are built, ~45 routers registered, `data/` managers initialized via `src/app_initializer.initialize_managers()`, Paperclip runtime/collector wired, local-model scan thread started. The `startup` event purges incognito sessions, starts the bg-job monitor, connects MCP servers (20s cap, non-blocking), pre-warms the RAG tool index, pings LLM endpoints (60s keepalive), reconciles default scheduled tasks, starts the task scheduler (unless `APOLLO_INPROCESS_TASKS=0`), and schedules the hourly null-owner sweep plus the ~02:00 nightly skill audit.
2. **A chat message** (`POST /api/chat`): AuthMiddleware resolves identity → `request.state.current_user`. Path is in `_TIMEOUT_EXEMPT_PREFIXES`, so the 45s `REQUEST_HARD_TIMEOUT` is skipped (streaming). `routes/chat_routes.py` hands off to `chat_processor` (context budgeting, memory recall, tool selection via the pre-warmed `src/tool_index`) → `src/llm_core` streams from the endpoint over httpx → tokens are relayed to the browser; `session_manager` persists `ChatMessage` rows and bumps `last_message_at`; `memory_manager`/`webhook_manager` hooks fire after completion.
3. **Static/UI routes** (`/`, `/notes`, `/cookbook`, …) all serve `static/index.html` with the per-request CSP nonce substituted into `{{CSP_NONCE}}`; `_RevalidatingStatic` adds `Cache-Control: no-cache` to `.js/.css/.html` so the no-build frontend revalidates every load (unchanged files 304). Generated images are served content-hashed with `public, max-age=31536000, immutable` after an ownership check.
4. **Shutdown**: cancels the upload-cleanup task, stops scheduler/webhooks/MCP, then `get_server().stop_all()` kills any `llama-server` children so they never outlive the app.

Liveness vs readiness: `GET /api/health` always 200; `GET /api/ready` runs `src/readiness.check_readiness()` and returns 503 unless DB, data dir, and local-first storage are whole; `GET /api/runtime` reports Docker detection + the effective Ollama URL.
