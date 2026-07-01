# Apollo — Project Overview & Architecture

> Source root: `/Users/Antman/Apollo` · App version: `APP_VERSION = "0.9.1"` (`core/constants.py:5`) · FastAPI `version="1.0.0"` (`app.py:80`)

Apollo is a **self-hosted, local-first AI workspace** — a ChatGPT/Claude-style UI that runs
entirely on the user's own hardware, data, and models, with no telemetry. It is a renamed
distribution of *Odysseus* (`README.md:46`).

It is explicitly a **three-tier system** (`README.md:48-52`):

1. **FastAPI backend** (Python 3.11+) exposing ~40 modular routers.
2. **Framework-free vanilla-JS frontend** — ES modules, server-sent events, **no build step**.
3. **SQLite + ChromaDB data layer**.

> "Everything runs as one `uvicorn` process plus on-demand `llama-server` subprocesses and
> the optional Paperclip Node sidecar." (`README.md:51`)

---

## 1. The Three Tiers

```
┌──────────────────────────────────────────────────────────────────────────┐
│  TIER 1 — FRONTEND  (static/, vanilla ES modules, NO build step)           │
│  index.html · app.js · static/js/*.js  (chat.js, research, editor/,        │
│  compare/, cookbook*, paperclip.js, browserPanel.js, settings.js …)        │
│  Served by Starlette StaticFiles; SSE + WebSocket for live streams.        │
└───────────────┬──────────────────────────────────────────────────────────┘
                │  HTTP / SSE / WebSocket  (cookie or Bearer ody_… token)
┌───────────────▼──────────────────────────────────────────────────────────┐
│  TIER 2 — BACKEND  (FastAPI, one uvicorn process — app.py)                  │
│                                                                            │
│   middleware stack (outer→inner):                                          │
│     CORSMiddleware → SecurityHeadersMiddleware →                           │
│     _RequestTimeoutMiddleware → AuthMiddleware                             │
│                                                                            │
│   routes/  (~53 *_routes.py)   →   src/  (handlers, managers, agent loop)  │
│        │                              │                                    │
│        └──────────────┬───────────────┘                                   │
│                       ▼                                                    │
│              services/  (subsystems: search, searxng, browser,            │
│              localmodels, paperclip, memory, research, tts, stt …)        │
│                       │                                                    │
│              core/  (database, auth, session_manager, middleware,         │
│              models, constants, atomic_io, platform_compat)               │
└───────┬───────────────────────────────────────────────┬──────────────────┘
        │                                                │
┌───────▼─────────────────┐                  ┌───────────▼──────────────────┐
│ TIER 3 — DATA            │                  │  SIDECARS / SUBPROCESSES      │
│  SQLite   data/app.db    │                  │  llama-server (GGUF, on-demand)│
│   (SQLAlchemy ORM,       │                  │  Paperclip (Node, opt-in)     │
│    core/database.py)     │                  │  SearXNG  (Python venv, :8893)│
│  ChromaDB data/chroma/   │                  │  ChromaDB (embedded or HTTP)  │
│   (vectors: RAG, memory) │                  │  ntfy / Playwright Chromium   │
│  JSON  sessions.json,    │                  └───────────────────────────────┘
│   settings.json, …       │
└─────────────────────────┘
```

### Tier 1 — Frontend (`static/`)
- Entry: `static/index.html` + `static/app.js`; ~90 ES modules under `static/js/`
  (`chat.js`, `chatStream.js`, `research/`, `editor/`, `compare/`, `cookbook*.js`,
  `paperclip.js`, `browserPanel.js`, `memory.js`, `settings.js`, `theme.js`,
  `voiceCall.js` + `vad.js` (hands-free call mode), `review.js` (adversarial reviewer UI),
  `memoryGraph.js` + `graphLayout.js` (knowledge-graph tab), …).
- No bundler/transpiler. Browsers load raw `.js` modules directly.
- Cache discipline is handled server-side by `_RevalidatingStatic` (`app.py:398-414`):
  `.js/.css/.html` get `Cache-Control: no-cache` so a code change appears without a hard
  refresh (no versioned URLs exist because there is no build step).
- Installable PWA (`static/manifest.json`, `static/sw.js`).

### Tier 2 — Backend (FastAPI)
- `app.py` (1278 lines) is a **slim orchestrator**: it builds middleware, initializes
  managers, and registers every router. The actual logic lives in `routes/`, `src/`,
  `services/`, and `core/`.

### Tier 3 — Data
- **SQLite** via SQLAlchemy: `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")`
  (`core/database.py:31`), `check_same_thread=False`, `PRAGMA foreign_keys=ON` enforced on
  every connection (`core/database.py:55`). ~30 ORM models: `Session`, `ChatMessage`,
  `Document`, `GalleryImage`, `EmailAccount`, `ModelEndpoint`, `McpServer`, `ApiToken`,
  `Webhook`, `ScheduledTask`, `Memory`, `Note`, `CalendarEvent`, `Integration`, … (model
  list at `core/database.py:87-1429`).
- **ChromaDB** for vectors: VectorRAG personal-doc search and semantic memory. Embedded
  on-disk for native installs, `HttpClient` when `CHROMADB_HOST` is set (Docker)
  (`requirements.txt:19-23`). Local ONNX embeddings via **fastembed**
  (`FASTEMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2`).
- **JSON files** under `data/`: `sessions.json`, `settings.json`, `memory.json`,
  `presets.json`, `auth.json`, `features.json`.

---

## 2. How `routes/` ↔ `src/` ↔ `services/` ↔ `core/` Relate

| Layer        | Role                                                                 |
|--------------|---------------------------------------------------------------------|
| `routes/`    | HTTP boundary. Each `*_routes.py` exposes a `setup_*_routes(...)` factory returning an `APIRouter`. Thin — parse request, call into `src`/`services`, shape the response. |
| `src/`       | Application logic & long-lived managers: `chat_handler.py`, `chat_processor.py`, `research_handler.py`, `agent_loop.py`, `memory.py`, `preset_manager.py`, `model_discovery.py`, `web_decider.py`, `rag_manager.py`, plus `app_initializer.py`. |
| `services/`  | Self-contained subsystems with their own runtimes/config: `search/`, `searxng/`, `browser/`, `localmodels/`, `paperclip/`, `memory/`, `research/`, `tts/`, `stt/`, `integrations/`. |
| `core/`      | Cross-cutting primitives: `database.py` (ORM/engine), `auth.py` (`AuthManager`), `session_manager.py`, `middleware.py` (`SecurityHeadersMiddleware`, internal-tool token), `models.py`, `constants.py`, `atomic_io.py`, `platform_compat.py`. |

**Dependency direction** (no cycles): `routes → src/services → core`. Routers receive their
dependencies by injection — `app.py` constructs the managers once via
`initialize_managers()` and passes them into each `setup_*` factory.

### Manager construction (`src/app_initializer.py:32-114`)
`initialize_managers(BASE_DIR, rag_manager)` creates directories then builds and returns a
dict of singletons: `memory_manager` (`MemoryManager(DATA_DIR)`), `skills_manager`,
`session_manager` (`SessionManager(SESSIONS_FILE)`; also wired into `core.models` so
`Session.add_message()` persists), `upload_handler`, `personal_docs_manager`,
`api_key_manager`, `preset_manager`, `chat_processor`, `research_handler`, `chat_handler`,
`model_discovery`, and (best-effort) `memory_vector` (a `MemoryVectorStore` that degrades to
`None` if ChromaDB is unreachable, `app_initializer.py:55-74`).

---

## 3. Request Lifecycle

### Startup (import-time, top→bottom in `app.py`)
1. **MIME registration** (`app.py:5-18`) — force `.js`/`.mjs` to JS MIME types (Windows
   registry-staleness fix).
2. **Windows HF symlink guard** (`app.py:24-27`) — set before huggingface_hub imports.
3. `load_dotenv(encoding="utf-8-sig")` (`app.py:37`) — tolerant of a UTF-8 BOM in `.env`.
4. **FastAPI app** constructed (`app.py:79`).
5. **Middleware added** (executes in reverse of add order — last added runs first):
   - `CORSMiddleware` — origins from `ALLOWED_ORIGINS` (`app.py:84-104`).
   - `SecurityHeadersMiddleware` (`app.py:105`, from `core/middleware.py`).
   - `_RequestTimeoutMiddleware` (`app.py:132-146`) — aborts any request past
     `REQUEST_HARD_TIMEOUT` (default **45s**) with HTTP 504, **except** streaming/long-running
     prefixes (`/api/chat`, `/api/shell/stream`, `/api/research`, `/api/model/download`,
     `/api/upload`, `/api/image`, …) (`app.py:122-130`).
   - `AuthMiddleware` (`app.py:277-389`, only when `AUTH_ENABLED != "false"`).
6. **VectorRAG init** — `get_rag_manager()` (`app.py:783-792`), lazy; `None` if ChromaDB down.
7. **Managers built** — `initialize_managers(...)` (`app.py:786-810`).
8. **Routers registered** (`app.py:544-846`, see §4).
9. **Static mount** — `app.mount("/static", _RevalidatingStatic(...))` (`app.py:414`).
10. **`@app.on_event("startup")`** handlers fire (async, after the server is up):
    - Paperclip native runtime + collector (`app.py:849-878`).
    - SearXNG sidecar boot in a daemon thread (`app.py:899-906`).
    - Background: bg-monitor, MCP connections (timeboxed), tool-index warmup, endpoint
      warmup, keepalive, null-owner sweep, nightly skill audit — all appended to
      `app.state._startup_tasks` so they aren't GC'd (`app.py:1023-1246`).
    - A non-blocking local-model directory scan thread (`app.py:888-891`).

### Per-request path (auth enabled)
```
request → CORS → SecurityHeaders → RequestTimeout(45s) → AuthMiddleware.dispatch:
  1. _is_auth_exempt(path)?  → pass through   (/api/auth/*, /api/health, /login,
                                                /static, /lmproxy, task webhooks …)
  2. X-Apollo-Internal-Token + trusted loopback?  → request.state.current_user =
        impersonated owner or "internal-tool"  (agent loopback to admin routes)
  3. LOCALHOST_BYPASS + direct loopback?  → act as _bypass_user() (admin/first user)
  4. Bearer "ody_…" token?  → bcrypt-check against the prefix-keyed in-memory token
        cache (refreshed lazily on _token_cache_dirty); set api_token state, last_used
        touched off the hot path
  5. else cookie session  → auth_manager.validate_token(cookie) → set current_user
        (401 for /api/*, else 302 → /login)
  → route handler → src/services → core/database / ChromaDB / sidecar
```
**Loopback-trust hardening** (`app.py:259-276`): `_is_trusted_loopback()` returns true only
for a *direct* `127.0.0.1`/`::1` client with **no** proxy-forwarding headers
(`cf-connecting-ip`, `x-forwarded-for`, `forwarded`, …), so a Cloudflare-tunnel visitor
cannot inherit local trust and bypass auth.

### Exception handlers (`app.py:813-830`)
Domain exceptions map to clean JSON: `SessionNotFoundError`→404, `InvalidFileUploadError`→400,
`LLMServiceError`→502, `WebSearchError`→502.

### Shutdown (`app.py:1248-1278`)
Stops task scheduler, webhook manager, MCP, local-model server; stops Paperclip and SearXNG
runtimes.

---

## 4. Router Registration (`services/app_startup.py`)

Registration is centralized and **labeled** so a failure points at the exact subsystem.
`services/app_startup.py` provides three helpers:

- `include_router_checked(app, router, label, logger)` — `app.include_router(...)` wrapped so
  any failure raises `RuntimeError("Failed to register {label} routes")` (`app_startup.py:18-28`).
- `build_and_include_router(app, label, factory, *args, **kwargs)` — calls the `setup_*`
  factory inside the guard, then includes the result (`app_startup.py:31-46`).
- `register_router_specs(app, [RouterSpec(...)])` — batch register a sequence;
  `RouterSpec(label, factory, args, kwargs)` is a frozen dataclass (`app_startup.py:10-65`).

`app.py` uses these to register ~40 routers, e.g.:
```python
build_and_include_router(app, "Auth", setup_auth_routes, auth_manager, logger=logger)  # app.py:544
register_router_specs(app, [
    RouterSpec("Sessions", setup_session_routes, args=(session_manager, session_config), …),
    RouterSpec("Memory",   setup_memory_routes,  args=(memory_manager, session_manager), …),
    RouterSpec("Chat",     setup_chat_routes,    args=(…)),
    RouterSpec("Research", setup_research_routes, args=(research_handler,), …),
    …
], logger=logger)                                                              # app.py:572-601
```
Router inventory (selection, from `routes/`): auth, uploads, sessions, memory, skills,
skill-packs (`skill_pack_routes.py`), chat, research, history, search, presets, diagnostics,
cleanup, personal, embedding, models, tts, stt, documents, signatures, gallery, editor drafts,
tasks, assistants, calendar, shell, cookbook, hwfit, localmodels, compare, prefs, backup,
fonts, mcp, webhooks, api-tokens, notes, email, vault, contacts, companion, paperclip
(sidecar proxy), integration status, system status, browser, lmproxy.

The **adversarial reviewer** endpoint lives on the chat router rather than in its own file:
`POST /api/review` (`routes/chat_routes.py:1365`) critiques an assistant answer with a second
model. It resolves the LLM via `resolve_endpoint("reviewer", owner=…)` — reading the
`reviewer_endpoint_id` / `reviewer_model` settings and **falling back to the utility model**
when the reviewer role is unset (`src/endpoint_resolver.py:205-253`) — then feeds the pure
prompt builder/parser in `services/review/reviewer.py` (`build_review_prompt` → LLM →
`parse_review` yields `{verdict, issues, suggestion}`).

---

## 5. Sidecars & Subprocesses

### llama-server (local GGUF models) — `services/localmodels/`
- `server_manager.py` launches and tracks `llama-server` processes; **one warm chat model at
  a time**, swapped automatically when another is selected.
- Binary auto-discovery: `_BIN_CANDIDATES` searches PATH, `~/.local/bin`, `~/llama.cpp/build/bin`,
  `/opt/homebrew/bin`, `/usr/local/bin` (`server_manager.py:19-27`).
- Each served slot tracks `model_id, name, kind, port, proc, base_url` (`_Proc`,
  `server_manager.py:30-37`); a free port is grabbed via an ephemeral bind (`_free_port`).
- Supporting modules: `scanner.py` (folder scan for GGUFs), `gguf_meta.py`,
  `registry.py`, `lifecycle.py` (`startup_scan` warms the catalog in a daemon thread,
  `app.py:888-891`).
- **Why native, not Docker** (`start-macos.sh:11-13`): Docker on macOS is a Linux VM with no
  Metal access, so Apollo runs natively to use the GPU.

### Local-model OpenAI proxy — `routes/lmproxy_routes.py` (`/lmproxy/v1`)
A stable localhost OpenAI-compatible endpoint that forwards to whichever GGUF model is
currently warm (`_warm_chat_base_url`, `app.py:813-822`). Consumed by Paperclip's agents; it
has its own bearer token and is auth-exempt from the cookie middleware (`app.py:200-203`).

### Paperclip (agent-management UI) — `services/paperclip/`
- Opt-in Node sidecar. Apollo **reverse-proxies** it at `/paperclip/*` behind `AuthMiddleware`
  (WebSocket upgrades validate the session cookie directly, since WS bypasses the HTTP
  middleware — `app.py:706-743`).
- Modes (`PAPERCLIP_MODE`): `docker` (compose sidecar), `native` (Apollo supervises
  `paperclipai run`, auto-provisions a pinned Node into `~/.apollo/.node` via
  `node_bootstrap.ensure_node`, `app.py:849-876`), or `external`.
- `PaperclipCollector` bridges Paperclip's realtime WS onto an `EventHub`, drained by
  `/api/paperclip/stream` to render "The Floor" (isometric agent office).
- Agents call local models through `/lmproxy/v1`; per-agent tokens
  (`AgentTokenRegistry`) attribute LLM calls.

### SearXNG (web search) — `services/searxng/`
- Managed **no-Docker** sidecar: a git checkout of `searxng/searxng` + a dedicated venv under
  `data/searxng/`, run as `python -m searx.webapp`, bound to **`127.0.0.1:8893`**
  (`searxng/config.py:6-16`, `DEFAULT_PORT = 8893`).
- `SearxngRuntime` (`searxng/runtime.py`) supervises the process with an injectable
  spawn/health for tests, a **2s health TTL** consulted on every search call, a **300s restart
  cooldown** watchdog, and log truncation to `logs/searxng.log` (`runtime.py:27-55`). Health is
  fail-closed: `/healthz` body must start with `OK` (`runtime.py:30-44`).
- Started in a daemon thread on the `startup` event (`app.py:899-906`); reuses an already-running
  instance.

### Web access pipeline — `src/web_decider.py` + `services/search/`
- **Tri-state `web_access`** per chat: `off | auto | always` (`web_decider.py`).
  `resolve_web_access(web_access, chat_mode, message, …)` maps the tri-state onto the legacy
  `use_web`/`allow_web_search` flags (`web_decider.py:1-9`).
- **Auto** runs `heuristic_decision(message)` — an instant regex pass returning
  `yes | no | ambiguous` from force-search verbs, strong/weak recency signals, freshness
  co-signals, URL detection, and self-contained-work negatives (`web_decider.py:24-66`) — then
  an async tie-break for the ambiguous case.
- The **provider chain** (`services/search/`, `services/search/providers.py:PROVIDER_INFO`)
  tries SearXNG → DuckDuckGo fallback → user-added Brave/Tavily/Serper/Google PSE, tags each
  result with the answering provider, and **skips a down sidecar with no timeout penalty**.
- Incognito chats never hit search engines (`README.md` Web Access section).

### Embedded browser — `services/browser/embedded_browser.py`
- Agent-controllable browser session over a shared **Playwright Chromium** page; the UI panel
  is a sandboxed iframe + canvas screencast (`embedded_browser.py:1-8`).
- Scheme allowlist (`http`/`https` only) with a hard `BLOCKED_SCHEMES` set
  (`file`, `data`, `javascript`, `chrome-extension`, `node`, …) for SSRF/exfiltration safety
  (`embedded_browser.py:23-43`).
- Wired at `/api/browser/*` with WS auth that honors `AUTH_ENABLED=false` and a
  `can_use_browser` privilege gate (`app.py:766-808`).

### Voice: STT / TTS providers — `services/stt/`, `services/tts/`
- **STT** (`services/stt/stt_service.py`) is a multi-provider dispatcher chosen by the
  `stt_provider` setting: `disabled`, `browser` (client Web Speech API), `local`
  (**faster-whisper** on CTranslate2 — CPU `int8` by default, `cuda`/`float16` only if a torch
  probe finds CUDA, `stt_service.py:79-87`), `voicebox` (local Voicebox app), or `endpoint:<id>`
  (OpenAI-compatible `/audio/transcriptions`). `POST /api/stt/transcribe` returns `{text}`.
- **TTS** (`services/tts/tts_service.py`) mirrors this: `disabled`, `browser`, `local`
  (**Kokoro-82M**, GPU), `piper` (**piper-tts**, ONNX voices, CPU/Metal — the local-TTS path on
  Apple Silicon, `_PiperPipeline`), `voicebox`, or `endpoint:<id>` (`/audio/speech`). Synthesized
  audio is SHA-256-cached under `data/tts_cache/`.
- Both read config fresh from `data/settings.json` on every call; the shared
  `voicebox_url` setting (default `http://127.0.0.1:17493`) points at the optional Voicebox
  desktop app when either provider is set to `voicebox`.

### Voice call mode (hands-free) — `static/js/voiceCall.js` + `vad.js`
A pure-frontend orchestration over the STT/TTS routes, layered so the core logic is
Node-unit-testable (no DOM/mic touched at import):
- `createVadGate()` (`vad.js`) is a pure RMS→event gate emitting `speechstart`/`speechend`;
  `createMicVad()` wraps it with Web Audio, reading live mic RMS on a `requestAnimationFrame`
  loop. Web Audio globals are referenced only inside the function body.
- `createCallMachine()` (`voiceCall.js`) is a pure state machine:
  `idle → listening → capturing → transcribing → thinking → speaking → listening`, driving
  injected effects (`startCapture`/`stopCapture`/`submitMessage`/`speak`/`teardown`). It supports
  barge-in (a `speechStart` during `speaking` stops TTS and re-captures).
- `startCall()`/`endCall()` wire it to the browser: `getUserMedia` (HTTPS/localhost only),
  a `MediaRecorder` → `POST /api/stt/transcribe`, `window.apolloSendMessage(text)`
  (`static/app.js:3851`) to submit, and `window.aiTTSManager` to speak. The reply is triggered
  by the **`apollo:assistant-complete`** CustomEvent that `chat.js` fires unconditionally at
  stream end (`chat.js:2501`) so the machine advances past `thinking` even when TTS is off.
- Overlay lives in `static/index.html` (`#voice-call-overlay`, `#vc-state-label`,
  `#vc-transcript`, `#vc-mute-btn`, `#vc-end-btn`); the `#call-mode-btn` entry button is always
  visible but gated on STT being enabled (`app.js:3862-3870`). SW precache version is bumped
  when call-mode assets change (`static/sw.js` `CACHE_NAME`).

### Second brain — semantic memory distillation & knowledge graph (`services/memory/`)
- **Distiller** (`distiller.py`): a pure `distill_transcript(transcript, llm_caller)` that asks
  a model to extract atomic durable facts (one per line, `NONE` if nothing worth keeping) and
  parses them.
- **Brain orchestrator** (`brain.py`): `distill_and_store(...)` dedupes facts
  (`find_duplicates`), stores new ones via `MemoryManager.add_entry`, tags them with a
  `session_id`, and best-effort indexes them in the ChromaDB `memory_vector` store — all
  collaborators injected, so infra-free/unit-testable. `distill_session(...)` is the thin
  side-effecting entry (`POST /api/memory/distill-session`).
- **Chat import** (`chat_import.py`): `parse_export(obj)` auto-detects and normalizes ChatGPT
  (`mapping`) and Claude (`conversations[].chat_messages`) export archives into a common
  `{title, messages:[{role,text}]}` shape; `import_conversations(...)` distills each into
  memories (`POST /api/memory/import-chat-export`).
- **Knowledge graph** (`graph.py`): pure `build_graph(memories, neighbor_fn, …)` builds nodes
  (facts) and edges — **semantic** (thresholded top-N similarity via injected neighbor lookup)
  + **session-shared** (chain within a source session). Served owner-scoped at
  `GET /api/memory/graph` (`routes/memory_routes.py:118`). The Graph tab renders it with a pure
  force layout (`static/js/graphLayout.js`, deterministic LCG seed) into a self-contained,
  CSP-nonce-safe SVG — no D3/CDN (`static/js/memoryGraph.js`).

### Skill-pack installer — `services/skills/pack_installer.py` + `routes/skill_pack_routes.py`
Admin-gated import of external Agent Skills packs into the `SKILL.md` store:
- `POST /api/skills/packs/preview` and `/install` (`require_admin`). `fetch_pack(source, ref)`
  maps a `github.com/<owner>/<repo>` URL to the API tarball, downloads it **SSRF-guarded**
  (`src.search.content._get_public_url`), and extracts with hardened `safe_extract_tar`
  (member cap 5000, 50 MB limit, path-traversal + `filter="data"` symlink/device guards).
- **Safe-by-default classification**: `classify_tier()` inspects file names only (never
  executes) — a pack that ships code (`scripts/`, `hooks/`, `.mcp.json`, or code extensions)
  is `script`-tier and installed as a quarantined **draft**; prose-only skills install
  `published`. `install_skills()` slugifies the category to keep writes inside `skills_root`.

### Security: agent-subprocess env scrub — `src/subproc_env.py`
`build_agent_env()` builds an **allowlisted, default-deny** environment for every
agent-spawned subprocess (bash/python tools, background jobs, the shell service, MCP stdio
servers), which previously inherited the full host `os.environ` (every provider key,
`DATABASE_URL`, decrypted SMTP/IMAP passwords, `SEARXNG_SECRET`). It copies only a `_PASS`
allowlist of non-secret POSIX/Windows/toolchain vars, plus an optional admin `passthrough`
list — each still denylist-scrubbed (`is_secret_env`: `DATABASE_URL`, `SMTP_*`/`IMAP_*`
prefixes, and `settings_scrub.is_secret_key` suffix rules) as defense in depth.

### Other Docker-only sidecars (`docker-compose.yml`)
`chromadb` (vector store, :8100→8000), `ntfy` (push notifications, :8091), and a
`paperclip` + `paperclip-db` (Postgres) pair behind the `paperclip` Compose profile.

---

## 6. Component Interaction — Chat with Web Access (concrete flow)

```
Browser (chat.js / chatStream.js)
   │  POST /api/chat  { message, web_access:"auto", mode, session_id }  (SSE)
   ▼
AuthMiddleware → routes/chat_routes.py (setup_chat_routes)
   │  resolve_web_access() → heuristic_decision(message)         [src/web_decider.py]
   │        "ambiguous" → async tie-break
   ▼ (web needed)
services/search SearchService.search(query)
   │  provider chain: SearXNG(127.0.0.1:8893) ─fail→ DuckDuckGo ─→ Brave/Tavily/…
   │  results tagged with provider; crawl4ai fetches page → Markdown
   ▼
src/chat_handler.py / chat_processor.py
   │  inject sources as context + recall semantic memory (ChromaDB via memory_vector)
   │  build prompt → LLM
   ▼
LLM endpoint:
   • local GGUF  → services/localmodels llama-server (warm model, /v1/chat/completions)
   • or remote OpenAI/Anthropic/Ollama/OpenRouter endpoint (model_endpoints table)
   ▼
SSE token stream  → chatStream.js → rendered with "Searched the web" + provider badge
   │
   └─ persist: ChatMessage rows (SQLite) + memory writes (ChromaDB)
```

---

## 7. Key Files Index

| Concern                | File(s) |
|------------------------|---------|
| App orchestrator       | `app.py` |
| Router registration    | `services/app_startup.py` |
| Manager construction   | `src/app_initializer.py` |
| ORM / engine           | `core/database.py` |
| Auth manager           | `core/auth.py`, `routes/auth_routes.py` |
| Security headers / internal token | `core/middleware.py` |
| Constants / paths      | `core/constants.py`, `src/constants.py` |
| Chat pipeline          | `routes/chat_routes.py`, `src/chat_handler.py`, `src/chat_processor.py` |
| Web decider            | `src/web_decider.py` |
| Search subsystem       | `services/search/`, `services/searxng/` |
| Local models           | `services/localmodels/server_manager.py` |
| Paperclip              | `services/paperclip/`, `routes/paperclip_routes.py` |
| Embedded browser       | `services/browser/embedded_browser.py`, `routes/browser_routes.py` |
| Voice STT / TTS        | `services/stt/stt_service.py`, `services/tts/tts_service.py`, `routes/stt_routes.py`, `routes/tts_routes.py` |
| Voice call mode        | `static/js/voiceCall.js`, `static/js/vad.js`, `#voice-call-overlay` in `static/index.html` |
| Adversarial reviewer   | `services/review/reviewer.py`, `POST /api/review` (`routes/chat_routes.py`), `static/js/review.js` |
| Second brain / memory  | `services/memory/{distiller,brain,chat_import,graph}.py`, `routes/memory_routes.py` |
| Knowledge graph UI     | `static/js/memoryGraph.js`, `static/js/graphLayout.js` |
| Skill-pack installer   | `services/skills/pack_installer.py`, `routes/skill_pack_routes.py` |
| Agent env scrub        | `src/subproc_env.py` (`build_agent_env`) |
| Desktop builds         | `build-macos-app.sh` (launcher), `build-macos-bundle.sh` + `packaging/apollo.spec` + `packaging/apollo_boot.py` (self-contained PyInstaller) |
| Frontend entry         | `static/index.html`, `static/app.js`, `static/js/` |
