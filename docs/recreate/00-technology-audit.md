# Apollo — Complete Technology Audit

Every language, framework, library, tool, and service used in the Apollo codebase
(`github.com/Antman1526/Apollo`, a renamed distribution of `pewdiepie-archdaemon/odysseus`),
with its **specific role in this project**. Compiled from `requirements*.txt`, `package.json`,
`docker-compose.yml`, `Dockerfile`, shell/PowerShell launchers, and the source itself.

---

## 1. Languages

| Technology | Role in this project |
|---|---|
| **Python 3.11+ (3.12 in the dev venv)** | The entire backend: FastAPI app (`app.py`, ~53k lines), 40+ route modules (`routes/`), service layer (`services/`), core domain (`core/`, `src/`). |
| **JavaScript (ES modules, no framework)** | The whole frontend under `static/js/` — direct DOM manipulation, innerHTML rendering, ES-module imports. No React/Vue/bundler. |
| **CSS (single 36k-line `static/style.css`)** | All styling; theming via CSS custom properties and `color-mix()`; `prefers-reduced-motion` support. |
| **HTML (`static/index.html`, single page)** | The one-page shell: sidebar, icon rail, and every modal (settings, theme editor, Paperclip floor, etc.). |
| **C (Win32)** | `scripts/windows-launcher/apollo_launcher.c` — the double-clickable `Apollo.exe` Windows launcher, cross-compiled with mingw-w64. |
| **Bash** | `start-macos.sh`, `build-macos-app.sh`, `scripts/check.sh` (test gate), `install-service.sh`. |
| **PowerShell** | `launch-windows.ps1` — Windows bootstrap (venv creation, deps, first-run setup, server start). |
| **SQL (via ORM)** | SQLite schema managed through SQLAlchemy models in `core/database.py`; ad-hoc `ALTER TABLE` migration helpers. |

## 2. Backend framework & HTTP stack

| Technology | Role |
|---|---|
| **FastAPI** | The web framework: routers built by `setup_<name>_routes()` factories, registered via `services/app_startup.py::build_and_include_router`. Pydantic request models in `src/request_models.py`. |
| **Starlette** | FastAPI's foundation, used directly for `StreamingResponse` (SSE chat + Paperclip floor stream), `BaseHTTPMiddleware` (AuthMiddleware), `BackgroundTask` (closing proxied upstream streams), and `TestClient` in tests. |
| **Uvicorn** | The ASGI server in every run mode (native scripts, Docker, systemd unit `apollo-ui.service`). |
| **httpx** | All outbound HTTP: LLM provider calls (`src/llm_core.py`), the Paperclip reverse proxy and `/lmproxy/v1` local-model proxy (streaming via `client.send(..., stream=True)` + `aiter_raw`), endpoint probing, collector company discovery. Also the test transport layer (`ASGITransport`, `MockTransport`). |
| **websockets** | Two roles: client for the Paperclip live-events collector (`services/paperclip/collector.py`) and for the `/paperclip/{path}` websocket reverse proxy; real server in the collector integration test. |
| **pydantic v2 (+ pydantic-settings)** | Request/response validation (`ChatRequest`, `SessionResponse`, …) and settings parsing. |
| **python-multipart** | Form-encoded endpoints (e.g. `POST /api/session` uses `Form(...)` fields). |
| **python-dotenv** | Loads `.env` deployment overrides (APP_PORT, AUTH_ENABLED, PAPERCLIP_*, …). |

## 3. Data & persistence

| Technology | Role |
|---|---|
| **SQLite** | Primary store at `data/app.db`: sessions, chat messages, documents, model endpoints, MCP servers, notes, calendars, scheduled tasks, gallery, memories, email accounts, API tokens, webhooks, etc. |
| **SQLAlchemy** | ORM + engine; `SessionLocal()` session-per-operation pattern; column-add migrations at startup. |
| **ChromaDB** | Vector store at `data/chroma` for semantic memory/RAG; embedded on-disk client natively, `HttpClient` when `CHROMADB_HOST` is set (Docker). |
| **fastembed (ONNX)** | Local embedding generation for memory/RAG/tool-selection — no torch, CPU-friendly. |
| **JSON file stores** | `data/auth.json` (users/2FA), `data/user_prefs.json` (per-user prefs incl. custom themes + `local_model_dirs`), `~/.apollo/*` (generated secrets, 0600: `paperclip_secret`, `paperclip_proxy_token`, `paperclip_agent_tokens.json`). |

## 4. LLM & AI runtime

| Technology | Role |
|---|---|
| **llama.cpp (`llama-server`)** | Serves local GGUF models. Apollo discovers GGUFs in configured dirs (`services/localmodels/scanner.py`), keeps a **single warm chat slot** (`server_manager.py`), launches `llama-server --model … -c <min(known-window, APOLLO_LLAMA_CONTEXT)>` on a free port, and evicts on model switch. |
| **GGUF model files** | The model format scanned/served (user dirs: `~/Desktop/AI_Models` primary, `/Volumes/MainStore/Development/AI_Models` secondary). |
| **Ollama** | Optional OpenAI-compatible endpoint (native URL detection in `_detect_provider`); default model endpoint for Paperclip agents in Docker (`host.docker.internal:11434/v1`). |
| **OpenAI / OpenRouter / Anthropic / Groq APIs** | Remote providers, auto-detected by hostname in `src/llm_core.py::_detect_provider`; OpenRouter gets referer/title headers. |
| **@anthropic-ai/sdk (npm)** | Node-side Anthropic SDK dependency (agent tooling). |
| **MCP (`mcp` pip package)** | Model Context Protocol client — `src/mcp_manager.py` manages stdio/HTTP MCP servers, tool listing/invocation, OAuth for Google MCP servers (`routes/mcp_routes.py`). |
| **faster-whisper** *(optional)* | Local CPU STT (CTranslate2 backend) for the "local" speech-to-text provider. |
| **piper-tts** *(optional)* | Local ONNX TTS voices (Apple-Silicon-friendly); voices discovered next to GGUF dirs (`discover_piper_voices`). |
| **crawl4ai** | Web → research-ready Markdown extraction for Deep Research / source imports. |
| **browser-use 0.13.0 + litellm** *(isolated venv)* | Browser-agent verification (Paperclip Floor QA, Ralph task verification) in `.apollo/browser-use-venv` — isolated because its `aiohttp` pin conflicts with ChromaDB's. |
| **llmfit (Cookbook)** | Hardware scan → model recommendation → download/serve flow ("Cookbook" feature, adapted vendor code). |
| **Tongyi DeepResearch (adapted)** | Multi-step research pipeline behind Deep Research reports. |

## 5. Paperclip agent platform (bundled sidecar)

| Technology | Role |
|---|---|
| **Paperclip (`paperclipai`, Node/React, MIT)** | Bundled agent-management platform. Native mode: Apollo supervises `paperclipai run` (`services/paperclip/runtime.py`), reusing healthy instances. Docker mode: own container + Postgres behind a Compose profile. |
| **Node.js (pinned, auto-provisioned)** | Downloaded from nodejs.org into `~/.apollo/.node` on first native launch (`node_bootstrap.py`) with **SHASUMS256 verification** and `tarfile filter="data"`; falls back to system Node. |
| **embedded Postgres (`@embedded-postgres/*`)** | Paperclip's own DB under `~/.paperclip` (native mode) — Apollo never manages it. |
| **PostgreSQL (Docker)** | `paperclip-db` Compose service backing the sidecar in Docker mode. |
| **Paperclip live-events WebSocket** | `/api/companies/{id}/events/ws` — consumed by Apollo's collector to feed the isometric "Floor" office visualization (`agent.status`, `heartbeat.run.*`, `activity.logged`). |

## 6. Frontend technologies

| Technology | Role |
|---|---|
| **Server-Sent Events (EventSource)** | Chat streaming (`/api/chat_stream`) and the Paperclip Floor live feed (`/api/paperclip/stream` with `waiting`/`unavailable` control events, reconnect tolerance, retry). |
| **SVG (hand-generated)** | The entire isometric Floor scene: furniture, walls, windows, exit door, and Lego-minifig agents, depth-sorted in one paint list for true occlusion (`static/js/paperclip.js`). |
| **CSS custom properties + `color-mix()`** | The token-based theme system: 25 preset themes (incl. light modes) + custom themes, syntax-highlight palette derivation (`static/js/theme.js`). |
| **localStorage** | Theme persistence, UI toggles, sidebar state (`static/js/storage.js` central key registry). |
| **Fira Code (self-hosted woff2)** | Default monospace UI font (`static/fonts/`). |
| **Canvas/CSS background effects** | Theme background patterns (dots, rain, embers, petals, sparkles…) with per-theme defaults/intensity. |
| **PWA (installable, responsive)** | Mobile support: dynamic viewport units, touch gestures, installable manifest. |

## 7. Email / calendar / personal-data integrations

| Technology | Role |
|---|---|
| **IMAP/SMTP (stdlib)** | Multi-account email with AI triage (urgency, auto-tag/summary/reply drafts/spam) — `routes/email_*`. |
| **caldav + icalendar + python-dateutil** | CalDAV sync (Radicale/Nextcloud/Apple/Fastmail), `.ics` import/export, recurrence expansion. |
| **SearXNG** | Self-hosted metasearch instance (`SEARXNG_INSTANCE=http://localhost:8080`) for web search. |
| **duckduckgo-search** *(optional)* | Alternative search provider (others: Brave, Tavily, Serper, Google PSE via keys). |
| **ntfy** | Push-notification channel for notes/task reminders (besides browser + email). |
| **youtube-transcript-api** | Pulls YouTube transcripts as chat/research source material. |
| **Webhooks** | Outbound event delivery managed by webhook manager; task webhooks are auth-exempt and self-authenticating. |

## 8. Documents & media

| Technology | Role |
|---|---|
| **pypdf** | PDF text extraction (MIT core path). |
| **PyMuPDF** *(optional, AGPL)* | PDF **form** filling: AcroForm detection, field stamping, page rendering. |
| **beautifulsoup4 + charset-normalizer** | HTML parsing/cleanup for web content ingestion. |
| **markdown** | Renders Deep Research visual reports (`src/visual_report.py`). |
| **qrcode[pil]** | TOTP enrollment QR codes for 2FA. |
| **numpy** | Vector math support for embeddings/RAG. |

## 9. Security & auth

| Technology | Role |
|---|---|
| **bcrypt** | Password hashing for local accounts (`data/auth.json`). |
| **pyotp** | TOTP two-factor authentication. |
| **cryptography** | Encryption utilities (tokens/secrets). |
| **hmac.compare_digest** | Constant-time comparisons for every token check (proxy tokens, ingest token, internal-tool token). |
| **croniter** | Cron-expression parsing for scheduled tasks. |

## 10. Testing & quality

| Technology | Role |
|---|---|
| **pytest + pytest-asyncio** | ~1,580 Python tests in flat `tests/`; conventions: stub `core.database` pre-import, `httpx.ASGITransport` for streaming routes, drive SSE generators directly (TestClient buffers infinite streams). |
| **node:test (built-in)** | JS suites (`tests/*.mjs`): Paperclip floor engine/renderer, system-status card, theme-preset contrast math (WCAG 4.5:1 checks). |
| **compileall** | Syntax gate across the codebase in `scripts/check.sh`. |
| **@antithesishq/bombadil (npm dev)** | Antithesis testing hook. |
| **Playwright MCP / browser-use** | Browser-level verification harnesses (floor QA, UI walkthroughs). |

## 11. Build, packaging & deployment

| Technology | Role |
|---|---|
| **Docker + docker-compose** | Containerized deployment; `paperclip` Compose profile adds the sidecar + Postgres; `host.docker.internal` extra-host mapping for Linux. |
| **hdiutil + sips (macOS)** | `build-macos-app.sh` builds `dist/Apollo.app` (launcher wrapper) and `dist/Apollo.dmg`; `sips` converts `docs/apollo.jpg` → `.icns`. |
| **mingw-w64** | Cross-compiles `Apollo.exe` (PE32+ GUI launcher) from macOS: `x86_64-w64-mingw32-gcc -O2 -municode -mwindows`. |
| **systemd** | `apollo-ui.service` + `install-service.sh` for Linux server installs. |
| **npm** | JS test runner scripts + the two Node deps. |
| **git / GitHub** | Source control; repo `Antman1526/Apollo` (distribution of `pewdiepie-archdaemon/odysseus`). |

## 12. Internal architecture primitives (project-specific)

| Component | Role |
|---|---|
| **EventHub (`services/paperclip/events.py`)** | Bounded fan-out hub (200-entry replay deque, seq-watermark dedupe, drop-don't-backpressure queues) feeding the Floor SSE stream from HTTP ingest, the WS collector, and lmproxy pulses. |
| **PaperclipCollector** | Reconnecting WS client (capped backoff, `local_trusted` tokenless or agent-API-key Bearer auth) normalizing Paperclip LiveEvents → Floor events. |
| **AgentTokenRegistry** | Per-agent `pa-…` lmproxy tokens (0600 JSON file, rotation on re-mint) powering per-agent LLM attribution + debounced Floor activity pulses. |
| **LocalModelServer** | Single-warm-slot llama-server supervisor: scan → catalog → ensure_running(model) with eviction, context sizing, 40s/GB health timeouts. |
| **event_bus (`src/event_bus.py`)** | Counter-threshold triggers that fire scheduled tasks on app events (e.g. `message_sent`). |
| **Ralph Loop** | PRD/task iteration loop (`scripts/apollo-ralph`) with quality gates, feeding agent activity into the Floor via `/api/paperclip/events`. |
