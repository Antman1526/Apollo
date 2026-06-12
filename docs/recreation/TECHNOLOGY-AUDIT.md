# Apollo — Technology Audit

Source of truth: `requirements.txt`, `requirements-optional.txt`, `requirements-browser-use.txt`, `pyproject.toml`, `package.json`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `scripts/`, `mcp_servers/`, `static/`, and import statements across `app.py`, `core/`, `src/`, `services/`, `routes/`, `companion/`.

Apollo is a self-hosted, multi-provider AI assistant: a FastAPI backend serving a vanilla-JS single-page UI, with an agent/tool loop, local-and-remote LLM serving (llama.cpp "Cookbook"), RAG, semantic memory, web search, research, podcasts/TTS/STT, calendar/mail integrations, browser automation, and an optional bundled "Paperclip" agent-management sidecar. Ships natively (macOS `.app`/`.dmg`, Windows PowerShell launcher) and via Docker Compose.

Conventions: **Version** is shown only when pinned in a manifest; `unpinned` means the dependency is listed without a version constraint; `transitive`/`runtime-detected` means it is discovered/used but not declared in a manifest.

---

## 1. Languages

| Name | Version | Role in Apollo |
|---|---|---|
| **Python** | 3.12 (Docker base `python:3.12-slim`; CI uses 3.12; native build prefers 3.11–3.13) | Primary backend language — entire FastAPI app, agent loop, services, MCP servers, and CLI tools. |
| **JavaScript (ES modules, vanilla)** | ES2020+ | Entire frontend SPA under `static/js/` (no framework/bundler) loaded as `<script>` modules from `static/index.html`. |
| **TypeScript** | — | Antithesis Bombadil test spec only (`tests/bombadil-spec.ts`). |
| **Bash** | — | `start-macos.sh`, `build-macos-app.sh`, `install-service.sh`, `scripts/check.sh`, `scripts/setup-searxng.sh`, `scripts/setup-browser-use-env`, GPU-check scripts, `docker/entrypoint.sh`. |
| **PowerShell** | — | `launch-windows.ps1`, `scripts/setup-searxng.ps1` (Windows install/launch path). |
| **Batch (.bat)** | — | `update_windows.bat` (Windows updater). |
| **HTML / CSS** | — | `static/index.html`, `landing.html`, `login.html`, `style.css` — app shell and styling. |
| **YAML** | — | `docker-compose.yml`, CI workflow, SearXNG settings template (`config/searxng/settings.yml`). |
| **SQL (SQLite dialect)** | — | Raw `sqlite3` queries alongside SQLAlchemy ORM across the codebase. |

---

## 2. Backend frameworks & libraries (core — `requirements.txt`)

| Name | Version | Role in Apollo |
|---|---|---|
| **FastAPI** | unpinned | Web framework for the whole HTTP/WS API (`app.py` + `routes/`). |
| **Uvicorn** | unpinned | ASGI server that runs `app:app` (Docker CMD, native launchers, CI). |
| **python-multipart** | unpinned | Multipart form parsing for file uploads (documents, attachments, images). |
| **python-dotenv** | unpinned | Loads `.env` config at startup. |
| **httpx** | unpinned | Primary async HTTP client — LLM provider calls, search providers, webhooks, Paperclip proxy, CalDAV, etc. (54 import sites). |
| **websockets** | unpinned | WebSocket client/handling for the Paperclip reverse proxy (`routes/paperclip_routes.py`) and embedded-browser screencast. |
| **Pydantic** | >=2.0 | Request/response models and validation across routes (`src/request_models.py`). |
| **pydantic-settings** | >=2.0 | Typed settings loading. |
| **SQLAlchemy** | unpinned | ORM + engine for the app database (`core/database.py`, `src/database.py`); tables created in `setup.py`. |
| **Starlette** | transitive (via FastAPI) | Middleware base — `AuthMiddleware`, CORS, BaseHTTPMiddleware (`core/middleware.py`). |
| **pypdf** | unpinned | Pure-MIT PDF *text* extraction (chat attachments, RAG ingest). |
| **pdfminer.six** | transitive | Alternate PDF text extraction path (`pdfminer.high_level.extract_text`) in document/search content handlers. |
| **beautifulsoup4 (bs4)** | unpinned | HTML parsing for scraped/search content and email bodies. |
| **charset-normalizer** | unpinned | Encoding detection for fetched/uploaded text. |
| **NumPy** | unpinned | Vector math for embeddings/RAG, TTS audio buffers, image ops. |
| **markdown** | unpinned | Renders research reports to HTML (`src/visual_report.py`). |
| **icalendar** | unpinned | `.ics` calendar import/export (`routes/calendar_routes.py`). |
| **python-dateutil** | unpinned | Recurrence-rule (RRULE) expansion for calendar events. |
| **caldav** | unpinned | CalDAV sync (PROPFIND/REPORT) across Radicale/Nextcloud/Apple/Fastmail (`src/caldav_sync.py`). |
| **cryptography** | unpinned | Encryption of stored secrets (API keys, webhook signing secrets) — `src/secret_storage.py`. |
| **bcrypt** | unpinned | Password hashing for the admin/user auth store (`data/auth.json`, `core/auth.py`, `setup.py`). |
| **pyotp** | unpinned | TOTP 2-factor authentication. |
| **qrcode[pil]** | unpinned | Renders TOTP enrollment QR codes (and companion pairing). |
| **croniter** | unpinned | Cron expression evaluation for the task scheduler (`src/task_scheduler.py`). |
| **defusedxml** | transitive | Hardened XML parsing (SSRF/XXE-safe parsing of feeds/responses). |
| **python-magic / magika** | transitive (magika via fastembed) | File-type sniffing for uploads. |

---

## 3. AI / ML

| Name | Version | Role in Apollo |
|---|---|---|
| **Anthropic native API** | — (adapter in `src/llm_core.py`) | First-class chat provider — native Anthropic Messages API adapter (`ANTHROPIC_MODELS`, `api.anthropic.com`). |
| **@anthropic-ai/sdk** | ^0.98.0 (npm, package.json) | Declared dependency; **not imported** in any JS/TS (declared-but-unused at audit time). |
| **llama.cpp (`llama-server`)** | external binary, runtime-detected | Local model serving engine ("Cookbook" / `Local (llama.cpp)`); `services/localmodels/server_manager.py` launches/tracks a warm `llama-server` from PATH/Homebrew/`~/llama.cpp/build`. |
| **llama-cpp-python / vLLM** | Cookbook-installed at runtime | Serve engines installed by the Cookbook flow into `/app/.local` (per docker-compose comments); not pinned in repo manifests. |
| **GGUF (format + parser)** | — | Custom minimal GGUF header reader (`services/localmodels/gguf_meta.py`) reads `general.architecture` to classify chat/embedding/unsupported models. |
| **ChromaDB** | unpinned | Vector store for RAG, semantic memory, and tool selection — embedded on-disk for native installs, `HttpClient` when `CHROMADB_HOST` set (`src/chroma_client.py`). |
| **fastembed** | unpinned | Local ONNX embeddings (default `sentence-transformers/all-MiniLM-L6-v2`) — pulls `onnxruntime`. |
| **sentence-transformers/all-MiniLM-L6-v2** | model id (default `FASTEMBED_MODEL`) | Default embedding model name used by fastembed. |
| **crawl4ai** | unpinned (core) | Mandatory web-agent integration — research / source extraction into Markdown. |
| **faster-whisper** | unpinned (optional) | Local speech-to-text (CTranslate2 backend, CPU default) — the "local" STT provider (`services/stt/stt_service.py`). |
| **CTranslate2** | transitive (via faster-whisper) | Inference backend for faster-whisper. |
| **piper-tts** | unpinned (optional) | Local CPU/Metal text-to-speech from Piper ONNX voices ("piper" TTS provider). |
| **Kokoro (Kokoro-82M)** | runtime-detected (optional) | Local GPU TTS provider ("local" TTS, `services/tts/tts_service.py`). |
| **diffusers** | runtime-detected | Image generation — `scripts/diffusion_server.py` (OpenAI-compatible image API; copied to remote Cookbook hosts). Loads SD/SDXL/SD3 pipelines. |
| **PyTorch (torch)** | runtime-detected (optional GPU) | GPU tensor backend for diffusers / GPU-accelerated whisper. |
| **transformers** | runtime-detected | Used in the ML serving path (1 import site). |
| **MCP (Model Context Protocol SDK)** | unpinned | `mcp` Python SDK — both the in-process MCP servers (`mcp_servers/`) and client manager (`src/mcp_manager.py`, `src/builtin_mcp.py`). |
| **litellm** | unpinned (browser-use venv) | LLM routing for browser-use agent. |
| **youtube-transcript-api** | unpinned | Pulls YouTube transcripts (`src/youtube_handler.py`). |
| **OpenAI-compatible adapters** | — | Generic adapter in `src/llm_core.py` is the fallback for any OpenAI-shaped endpoint (covers vLLM, LM Studio, etc.). |

> Note: tool *descriptions* in `src/tool_*` reference upscalers (RealESRGAN, GFPGAN, basicsr) and `rembg` as Cookbook-serveable models, but these are **not** direct Python dependencies of Apollo.

---

## 4. Data stores

| Name | Version | Role in Apollo |
|---|---|---|
| **SQLite** | via stdlib `sqlite3` + SQLAlchemy | Primary app DB (`DATABASE_URL=sqlite:///./data/app.db`) — sessions, webhooks, tasks, settings, mail metadata, etc. |
| **ChromaDB** | unpinned / `chromadb/chroma:latest` (Docker) | Vector store (RAG, memory vectors, tool index) — embedded (`data/chroma`) or service. |
| **JSON flat files** | — | `data/auth.json` (users/hashes), settings/prefs, skills, presets, caches under `data/`, `services/cache/`. |
| **PostgreSQL** | `postgres:17-alpine` (Docker) | Backing store for the **Paperclip** sidecar only (`paperclip-db`, `profiles: [paperclip]`). |
| **HuggingFace cache** | on-disk | `data/huggingface` — model cache for Cookbook/local serving. |
| **FAISS** | legacy (migration only) | `scripts/migrate_faiss_to_chroma.py` migrates old FAISS indexes to ChromaDB (not a live dep). |

---

## 5. Frontend

| Name | Version | Role in Apollo |
|---|---|---|
| **Vanilla JS ES modules** | — | ~90 hand-written modules in `static/js/` (chat, RAG, models, settings, cookbook, paperclip, calendar, gallery, compare, research, embedded browser panel, a11y, theming). No framework, no bundler. |
| **Service Worker / PWA** | — | `static/sw.js` + `static/manifest.json` + icons — installable PWA. |
| **highlight.js** | v11.9.0 (`static/lib/highlight.min.js`) | Code-block syntax highlighting in chat. |
| **KaTeX** | 0.16.22 (jsDelivr CDN) | Math/LaTeX rendering in chat. |
| **Mermaid** | 11.x (jsDelivr CDN) | Diagram rendering from fenced code blocks. |
| **SheetJS (xlsx.full.min.js)** | bundled | Client-side spreadsheet (.xlsx) parsing/export. |
| **mammoth.browser.min.js** | bundled | Client-side .docx → HTML conversion. |
| **docx.umd.min.js** | bundled | Client-side .docx generation/export. |
| **html2pdf.bundle.min.js** | bundled | Client-side HTML → PDF export of reports. |
| **qrcode.min.js** | bundled | Client-side QR rendering (pairing / 2FA display). |

---

## 6. Search

| Name | Version | Role in Apollo |
|---|---|---|
| **SearXNG** | `searxng/searxng:2026.5.31-7159b8aed` (Docker, pinned) / managed sidecar | Primary metasearch provider; aggregates engines (default `bing,mojeek,presearch`). Apollo can run it as a managed sidecar (`services/searxng/runtime.py`, `setup-searxng.sh/.ps1`). |
| **duckduckgo-search** | unpinned (optional) | DuckDuckGo provider (`duckduckgo_lib`) + immediate fallback when SearXNG is down. |
| **DuckDuckGo HTML** | — | `duckduckgo_html` scrape fallback provider. |
| **Brave Search API** | API (`DATA_BRAVE_API_KEY`) | Optional keyed search provider. |
| **Tavily** | API (`TAVILY_API_KEY`) | Optional keyed search provider. |
| **Serper** | API (`SERPER_API_KEY`) | Optional keyed Google-results provider. |
| **Google Programmable Search (PSE)** | API (`GOOGLE_API_KEY` + `GOOGLE_PSE_CX`) | Optional keyed search provider. |
| **Web decider** | — | `src/web_decider.py` heuristics decide when to auto-trigger web search (tri-state `web_access`). |

Provider registry: `services/search/providers.py` (`SearXNG, Brave, DuckDuckGo, Google PSE, Tavily, Serper`).

---

## 7. Browser automation

| Name | Version | Role in Apollo |
|---|---|---|
| **browser-use** | ==0.13.0 (isolated venv) | Agentic browser automation; isolated into `.apollo/browser-use-venv` due to an `aiohttp` pin conflict with ChromaDB. Used for Paperclip Floor verification (`scripts/check-paperclip-browser`, `services/paperclip/browser_use_verifier.py`). |
| **Playwright (Python)** | runtime-detected | Browser driver behind browser-use and `services/browser/embedded_browser.py`; `browser-use install` provisions browsers. |
| **CDP screencast + input forwarding** | — | Embedded interactive browser viewport over authenticated WebSocket (`services/browser/embedded_browser.py`, `static/js/browserPanel.js`). |
| **Browser MCP (npx)** | runtime (Node/npx) | Optional built-in Browser MCP server launched via `npx` (Dockerfile installs nodejs/npm for this). |

---

## 8. Sidecars & containers (docker-compose services)

| Name | Image / Source | Role in Apollo |
|---|---|---|
| **apollo** | built from local `Dockerfile` (`python:3.12-slim`) | Main app container; uvicorn on :7000. |
| **chromadb** | `docker.io/chromadb/chroma:latest` | Vector store service (Compose deployment). |
| **searxng** | `docker.io/searxng/searxng:2026.5.31-7159b8aed` (pinned) | Metasearch sidecar with custom entrypoint that templates `settings.yml` + secret; healthchecked. |
| **ntfy** | `docker.io/binwiederhier/ntfy` | Push-notification server sidecar (`serve`); also usable as an outbound notification integration. |
| **paperclip** | built from `github.com/paperclipai/paperclip.git#v2026.529.0` | Bundled agent-management UI/control plane, reverse-proxied at `/paperclip` (opt-in `profiles: [paperclip]`). |
| **paperclip-db** | `docker.io/postgres:17-alpine` | Postgres for Paperclip (opt-in). |
| **Node (Paperclip runtime, native)** | Node **22.13.0** auto-downloaded from nodejs.org | Native (non-Docker) Paperclip runtime — `services/paperclip/node_bootstrap.py` downloads + checksum-verifies an official Node build into the data dir. |
| **opencode** | via Paperclip | Paperclip's agent model runner; points at local Ollama (`OPENAI_BASE_URL=.../v1`). |
| **better-auth** | via Paperclip | Auth layer in the Paperclip service (`BETTER_AUTH_SECRET`). |

---

## 9. MCP servers (`mcp_servers/`)

All built on the Python **mcp** SDK; exposed as in-process tool servers.

| Name | File | Role |
|---|---|---|
| **email_server** | `mcp_servers/email_server.py` | Email tools (list unread/unresponded, send) over IMAP/SMTP (reads from local Dovecot IMAP + AI summary cache). |
| **image_gen_server** | `mcp_servers/image_gen_server.py` | Image generation via OpenAI-compatible image APIs. |
| **memory_server** | `mcp_servers/memory_server.py` | Semantic memory CRUD/search (wraps `MemoryManager` + `MemoryVectorStore`). |
| **rag_server** | `mcp_servers/rag_server.py` | RAG document management (list / add-directory / remove-directory). |
| **_common.py / _completion** | helpers | Shared MCP server scaffolding. |
| **Built-in / external MCP client** | `src/mcp_manager.py`, `src/builtin_mcp.py` | Connects Apollo to external MCP servers (incl. npx-launched Browser MCP). |

---

## 10. Dev / test / CI

| Name | Version | Role in Apollo |
|---|---|---|
| **pytest** | unpinned | Python test runner (`pyproject.toml` `[tool.pytest.ini_options]`, `testpaths=["tests"]`). |
| **pytest-asyncio** | unpinned (`asyncio_mode=auto`) | Async test support. |
| **node --test** | Node 20 (CI) | Native Node test runner for JS UI tests (`tests/test_*.mjs`) via `npm run test:js`. |
| **@antithesishq/bombadil** | ^0.3.2 (npm devDep) | Antithesis Bombadil autonomous-testing spec (`tests/bombadil-spec.ts`). |
| **compileall** | stdlib | CI byte-compiles `app.py companion core routes services src ...` as a smoke check. |
| **GitHub Actions** | `checkout@v4`, `setup-python@v5` (3.12), `setup-node@v4` (20) | CI pipeline (`.github/workflows/ci.yml`): install deps → compileall → pytest → npm test:js. |
| **scripts/check.sh** | — | Local equivalent of CI (compileall + pytest + js tests). |

---

## 11. Build / packaging / runtime

| Name | Role in Apollo |
|---|---|
| **hdiutil** | macOS `.dmg` creation in `build-macos-app.sh` (`hdiutil create ... -format UDZO`). |
| **macOS `.app` launcher** | `build-macos-app.sh` builds a clickable `.app` wrapper that drives the repo venv (does not bundle Python). |
| **Python venv** | Native installs use a repo-local `venv/` (bootstrapped by `start-macos.sh` / build script; Windows launcher creates its own). |
| **PowerShell launcher** | `launch-windows.ps1` locates Python 3.11+, sets up venv, launches uvicorn on Windows. |
| **Docker / Docker Compose** | Container build (multi-service) and orchestration. |
| **gosu** | Drops container privileges to PUID/PGID in `docker/entrypoint.sh`. |
| **tmux** | Required by Cookbook for background model downloads/serves (installed in Docker; checked in `setup.py`). |
| **openssh-client** | Cookbook remote-server setup/probes/serves over SSH. |
| **git + cmake + build-essential** | Building llama.cpp on first launch inside Docker. |
| **systemd unit** | `apollo-ui.service` + `install-service.sh` for Linux service install. |
| **GPU compose overlays** | `docker/gpu.amd.yml`, `docker/gpu.nvidia.yml` + `scripts/check-docker-*-gpu.sh` for GPU passthrough. |
| **apollo* CLI tools** | ~30 `scripts/apollo-*` Python CLIs (`#!/usr/bin/env python3`) — backup, calendar, contacts, cookbook, mail, memory, notes, research, sessions, skills, tasks, theme, webhook, ralph (loop), etc., with shell completions under `scripts/_completion`. |

---

## 12. Third-party APIs & external services

### LLM providers (adapters in `src/llm_core.py`, `src/model_discovery.py`, `src/endpoint_resolver.py`)
| Provider | Detection / config | Role |
|---|---|---|
| **OpenAI** | `api.openai.com` (`OPENAI_API_KEY`) | OpenAI-compatible chat/embeddings/image/audio; also the default adapter for unknown OpenAI-shaped hosts. |
| **Anthropic** | `api.anthropic.com` | Native Messages API adapter (Claude models). |
| **OpenRouter** | `openrouter.ai` | Aggregated model routing. |
| **Groq** | `groq.com` | Fast inference provider. |
| **Ollama** | `:11434` / `ollama.com` (`OLLAMA_BASE_URL`) | Local + Ollama Cloud native `/api/chat`; reached at `host.docker.internal:11434` from Docker. |
| **vLLM / LM Studio / generic** | `RESEARCH_LLM_ENDPOINT`, `LLM_HOST(S)`, `EMBEDDING_URL` | Any OpenAI-compatible local/remote serve endpoint. |

### Search APIs
Brave (`DATA_BRAVE_API_KEY`), Tavily (`TAVILY_API_KEY`), Serper (`SERPER_API_KEY`), Google PSE (`GOOGLE_API_KEY` + `GOOGLE_PSE_CX`), self-hosted SearXNG, DuckDuckGo. *(See §6.)*

### Mail / calendar / contacts
| Service | Where | Role |
|---|---|---|
| **IMAP** (`imaplib`) | `mcp_servers/email_server.py` | Reads mail (local Dovecot IMAP). |
| **SMTP** (`smtplib`) | email server | Sends mail. |
| **CalDAV** (`caldav`) | `src/caldav_sync.py`, `caldav_writeback.py` | Two-way calendar sync (Radicale/Nextcloud/Apple/Fastmail). |

### Notifications & integrations (`src/integrations.py`, `src/webhook_manager.py`)
| Service | Role |
|---|---|
| **ntfy** | Push-notification integration (and bundled sidecar) — keyed/topic endpoints. |
| **Outbound webhooks** | HMAC-signed HTTP POSTs fired on app events (`webhook.*`), URL-validated against SSRF. |

### Model hosting / downloads
| Service | Role |
|---|---|
| **HuggingFace Hub** | Model/voice/embedding downloads (`HF_TOKEN`/`HUGGING_FACE_HUB_TOKEN`, `scripts/hf_download.py`, cached under `data/huggingface`). |
| **nodejs.org** | Native Paperclip Node runtime download (checksum-verified). |
| **github.com/paperclipai/paperclip** | Paperclip sidecar source (pinned tag `v2026.529.0`). |

---

## 13. Optional document/media extraction

| Name | Version | Role |
|---|---|---|
| **PyMuPDF (fitz)** | unpinned (optional, **AGPL-3.0**) | PDF form-filling (AcroForm detection, field/value/signature stamping, page render) — `src/pdf_forms.py`, `pdf_form_doc.py`. Core stays pure-MIT without it. |
| **markitdown[docx,pptx,xlsx,xls]** | ==0.1.5 (optional, MIT) | Office/EPUB → Markdown for chat attachments + personal-docs RAG (`src/markitdown_runtime.py`); pulls mammoth/lxml/python-pptx/pandas/openpyxl/xlrd + magika/onnxruntime. |
| **OpenCV (cv2)** | runtime-detected | Image processing (referenced in import scan; ML/image path). |

---

## Security / redaction note
No secrets are reproduced here. `.env` exists in the repo root but its values were **not** copied. API-key and auth material are referenced only by their **environment-variable names** (e.g. `OPENAI_API_KEY`, `DATA_BRAVE_API_KEY`, `TAVILY_API_KEY`, `SERPER_API_KEY`, `GOOGLE_API_KEY`, `HF_TOKEN`, `PAPERCLIP_AUTH_SECRET`, `SEARXNG_SECRET`). Admin password hashing is bcrypt; stored secrets are encrypted via `cryptography` (`src/secret_storage.py`).
