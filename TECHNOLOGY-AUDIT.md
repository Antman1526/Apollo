# Apollo — Technology Audit

Scope: every technology, framework, library, tool, and language actually in use in this repository, verified against source files (requirements files, `package.json`, CI workflows, packaging scripts, source imports). No version is guessed — every pinned version below was read from a lockfile, manifest, Dockerfile, or vendored file header.

---

## 1. Languages

| Name | Version | Role in this project |
|---|---|---|
| Python | 3.12 (`.github/workflows/*.yml` pin `python-version: "3.12"`; `pyproject.toml` `target-version = "py312"`) | Primary backend language — FastAPI app, all `routes/`, `services/`, `src/`, `core/`, `mcp_servers/`, `scripts/`. |
| JavaScript (ES modules) | ES2020+ (native `type="module"` script tags, no transpiler) | Entire frontend in `static/js/` and `static/app.js` — hand-written, unbundled ES modules; no framework. |
| TypeScript | via `@antithesishq/bombadil` devDependency | Used only in `tests/bombadil-spec.ts`, a fuzz/property test spec (see §5). |
| Bash / POSIX shell | — | `start-macos.sh`, `install-service.sh`, `build-macos-app.sh`, `build-macos-bundle.sh`, `docker/entrypoint.sh`, most of `scripts/*.sh`. |
| PowerShell | 5.1+ (`#Requires -Version 5.1` in `launch-windows.ps1`, `scripts/setup-searxng.ps1`) | Native Windows launcher and SearXNG installer. |
| Batch (`.bat`/`.cmd`) | — | `update_windows.bat` — Docker-based Windows update flow (`git pull --ff-only`, `docker compose up -d --build`). |
| SQL (SQLite dialect) | — | Schema/queries via SQLAlchemy Core/ORM in `core/database.py`. |
| YAML | — | `docker-compose.yml`, `docker/gpu.*.yml`, `.github/workflows/*.yml`, `config/searxng/settings.yml`. |

---

## 2. Backend frameworks & libraries (Python)

Evidence source: `requirements.txt` (pip-compile lock from `requirements.in`), `requirements-dev.txt`, `requirements-optional.txt`, `requirements-browser-use.txt`, and direct source imports.

| Name | Version (pinned) | Role in this project |
|---|---|---|
| FastAPI | 0.139.2 | Core ASGI web framework — `app.py` instantiates `FastAPI()`; every module under `routes/` is an `APIRouter`. |
| Starlette | 1.3.1 (transitive, via fastapi/mcp/sse-starlette) | `core/middleware.py` uses `starlette.middleware.base.BaseHTTPMiddleware` directly for security/session middleware. |
| Uvicorn | 0.51.0 | ASGI server — `uvicorn app:app` is the standard entrypoint (Dockerfile CMD, systemd unit, all launch scripts); `packaging/apollo_boot.py` calls `uvicorn.run()` directly for the frozen build. |
| uvloop | 0.22.1 (`sys_platform != "win32"`) | Uvicorn event-loop acceleration on macOS/Linux; excluded on Windows because the upstream package doesn't support it. |
| httptools / websockets | 0.8.0 / 16.1.1 | Uvicorn's HTTP parser and WebSocket protocol implementation. |
| Pydantic / pydantic-settings | 2.13.4 / 2.14.2 | Request/response models (`src/request_models.py`), settings loading (`src/settings.py`). |
| python-multipart | 0.0.32 | Multipart form parsing for file uploads (`routes/upload_routes.py`). |
| python-dotenv | 1.2.2 | Loads `.env` at startup (`app.py`, `load_dotenv(encoding="utf-8-sig")` to tolerate a Windows/Notepad BOM). |
| SQLAlchemy | 2.0.51 | ORM/engine for the app's SQLite database — `core/database.py` (`create_engine`, declarative models, `sessionmaker`). |
| sqlite3 (stdlib) | — | Underlying DB engine; `core/database.py` also uses raw `sqlite3.connect()` for maintenance/PRAGMA operations (`PRAGMA foreign_keys=ON` on every connect). |
| httpx | 0.28.1 | Primary outbound HTTP client across the codebase (LLM calls in `src/llm_core.py`, webhooks in `src/webhook_manager.py`, health checks, etc.) — async-first, used instead of `requests` for app logic. |
| requests | <2.34 (pinned below 2.34 because CalDAV's `niquests`/`urllib3-future` conflicts with 2.34's urllib3 layout) | Secondary sync HTTP client used by a handful of integrations. |
| websockets | 16.1.1 | WebSocket protocol support (uvicorn + `static/js/browserPanel.js` client side for the embedded browser panel). |
| cryptography | 49.0.0 | `src/secret_storage.py` — Fernet symmetric encryption for secrets stored in SQLite (`cryptography.fernet.Fernet`); also backs `pyjwt`/`pyopenssl` transitively. |
| bcrypt | 5.0.0 | Password hashing in `core/auth.py` (`bcrypt.hashpw`/`bcrypt.checkpw`) and pairing-token hashing in `companion/pairing.py`. |
| pyotp | 2.10.0 | TOTP-based 2FA (`core/auth.py` — `pyotp.random_base32()`, `pyotp.TOTP`). |
| qrcode[pil] | 8.2 | Renders 2FA-setup QR codes server-side. |
| croniter | 6.2.4 | Cron-expression scheduling engine for `src/task_scheduler.py`. |
| mcp | 1.28.1 | Model Context Protocol SDK — implements the four bundled MCP servers (`mcp_servers/*.py`, `from mcp.server import Server`, `mcp.server.stdio.stdio_server`) and the client-side manager (`src/mcp_manager.py`) that connects to third-party MCP servers. |
| sse-starlette | 3.4.5 | Backs MCP's SSE transport and is available for `EventSourceResponse`-based streaming. |
| chromadb | 1.5.9 | Embedded vector database — `src/chroma_client.py` (`chromadb.PersistentClient` by default, `chromadb.HttpClient` if a remote host is configured) for RAG and semantic memory. |
| fastembed | 0.8.0 | Local ONNX embedding generation (`src/embeddings.py`, default model `sentence-transformers/all-MiniLM-L6-v2`) — zero-config fallback when no HTTP embedding endpoint (Ollama/vLLM/llama.cpp) is configured. |
| onnxruntime | 1.27.0 (transitive, via fastembed/chromadb) | ONNX inference runtime backing fastembed's local embedding model. |
| pypdf | 6.14.2 | Core (MIT) PDF text extraction — `src/document_processor.py` (`from pypdf import PdfReader`). |
| PyMuPDF (`fitz`) | *optional* (`requirements-optional.txt`, AGPL-3.0) | PDF form-filling (AcroForm detection/rendering) — `src/pdf_forms.py`, `src/pdf_runtime.py`; lazy-imported so the AGPL dependency is opt-in. |
| markitdown[docx,pptx,xlsx,xls] | 0.1.5 *(optional)* | Office/EPUB → Markdown conversion for chat attachments and personal-docs RAG — `src/markitdown_runtime.py`, lazy-imported. |
| faster-whisper | *optional* | Local speech-to-text — `services/stt/stt_service.py` (`from faster_whisper import WhisperModel`), CTranslate2 backend, CPU by default with CUDA auto-detect. |
| piper-tts | *optional* | Local, CPU/Mac-friendly text-to-speech voice — `services/tts/tts_service.py` (Piper `.onnx` voice files). |
| duckduckgo-search | *optional* | DuckDuckGo search-provider backend — `services/search/providers.py`. |
| beautifulsoup4 / soupsieve | 4.15.0 / 2.8.4 | HTML parsing across document processing, search-result content extraction, email HTML sanitization. |
| charset-normalizer | 3.4.9 | Text encoding detection (chosen over LGPL `chardet`, since removed — see ACKNOWLEDGMENTS.md). |
| markdown | 3.10.2 | Server-side Markdown rendering. |
| numpy | 2.5.1 | Numeric backend for embeddings/vector math. |
| icalendar | 7.2.0 | iCalendar (.ics) parsing/generation for the calendar feature (`src/caldav_sync.py`, export routes). |
| caldav | 3.2.1 | CalDAV client — syncs with external calendar servers (`src/caldav_sync.py`, `src/caldav_writeback.py`). |
| recurring-ical-events | 3.8.2 (transitive, via caldav) | Expands recurring calendar events (RRULE) for display. |
| dnspython | 2.8.0 (transitive, via caldav) | DNS resolution support for CalDAV discovery. |
| niquests / urllib3-future | 3.20.1 / 2.22.901 (transitive, via caldav) | HTTP/2-capable client used internally by `caldav`. |
| youtube-transcript-api | 1.2.4 | Fetches YouTube video transcripts — `services/youtube/youtube_handler.py`. |
| python-magic | 0.4.27 | File-type sniffing (`libmagic` bindings) for upload validation; explicitly excluded from the Windows PyInstaller/native path (bundled instead via a PyInstaller hidden-import `"magic"`, since libmagic isn't natively available on Windows) and handled with graceful fallback. |
| crawl4ai | 0.9.2 | Headless-browser web crawling/scraping engine powering Deep Research content extraction (`services/research/crawl4ai_adapter.py`); pulls in Playwright, patchright, and `unclecode-litellm` as its own dependencies. |
| unclecode-litellm | 1.81.13 (transitive, via crawl4ai) | LLM-provider abstraction layer used internally by crawl4ai for its own summarization steps — not Apollo's primary LLM path (that's the hand-rolled adapter in `src/llm_core.py`). |
| litellm | *(separate, `requirements-browser-use.txt`)* | Installed into the isolated `browser-use` venv alongside `browser-use==0.13.0` for that tool's own LLM calls. |
| tiktoken | 0.13.0 (transitive, via unclecode-litellm) | Token counting for LLM context budgeting inside crawl4ai/litellm. |
| Jinja2 | 3.1.6 (transitive, via unclecode-litellm) | Template rendering used internally by litellm. |
| nltk | 3.10.0 (transitive, via crawl4ai) | NLP text processing used by crawl4ai's content-extraction pipeline. |
| rank-bm25 | 0.2.2 (transitive, via crawl4ai) | BM25 keyword ranking, used by crawl4ai's relevance scoring. |
| alphashape / shapely / trimesh / rtree / networkx / scipy | 1.3.1 / 2.1.2 / 4.12.2 / 1.4.1 / 3.6.1 / 1.18.0 (transitive, via crawl4ai) | Geometry/graph utilities crawl4ai uses for layout-aware content extraction. |
| lxml | 6.1.1 | Fast XML/HTML parsing, used by both `caldav` and `crawl4ai`. |
| kubernetes (python client) | 36.0.3 (transitive, via chromadb) | Pulled in by ChromaDB's optional Kubernetes-deployment support; not actively used by Apollo (Apollo runs Chroma embedded, not via Kubernetes). |
| opentelemetry-api / -sdk / -exporter-otlp-proto-grpc | 1.44.0 (transitive, via chromadb) | Chroma's internal telemetry instrumentation; not wired into Apollo's own observability. |
| grpcio | 1.82.1 (transitive) | gRPC transport backing ChromaDB's OTLP exporter. |
| orjson | 3.11.9 (transitive, via chromadb) | Fast JSON serialization used internally by Chroma. |
| tenacity | 9.1.4 (transitive, via chromadb) | Retry/backoff decorator used internally by Chroma. |
| typer | 0.27.0 (transitive, via chromadb) | CLI-framework dependency pulled in by Chroma (not used directly by Apollo's own CLIs, which are plain argparse/bash — see `scripts/apollo-*`). |
| loguru | 0.7.3 (transitive, via fastembed) | Logging library used internally by fastembed. |
| torch | *(not pinned — user-installed for optional local image generation)* | Backs `scripts/diffusion_server.py`, an optional OpenAI-compatible image-generation server using HuggingFace `diffusers` pipelines (Stable Diffusion / Flux). |
| diffusers | *(not pinned — same optional path)* | `scripts/diffusion_server.py` dynamically loads `diffusers.DiffusionPipeline` / model-specific pipeline classes to serve local text-to-image generation. |
| huggingface_hub | 1.24.0 | Model downloads for fastembed/Cookbook (`scripts/hf_download.py` wraps `huggingface_hub.snapshot_download`); `HF_HUB_DISABLE_SYMLINKS` forced on Windows. |
| Fernet (part of `cryptography`) | — | See `cryptography` above; used specifically for at-rest secret encryption. |

---

## 3. Frontend (static/)

Evidence: `static/index.html`, `static/app.js`, `static/js/**`, `static/lib/**`, `static/sw.js`, `ACKNOWLEDGMENTS.md`.

**Architecture**: vanilla JavaScript, no build step, no bundler, no frontend framework. `static/index.html` loads ~35 native ES modules directly via `<script type="module" src="...">`; `static/js/package.json` contains only `{ "type": "module" }`. `package-lock.json` at the repo root has no bundler/React/Vue packages at all — its only real dependencies are `@anthropic-ai/sdk` (0.98.0) and the dev-only `@antithesishq/bombadil` (0.3.2, testing).

| Name | Version | Role in this project |
|---|---|---|
| Highlight.js | 11.9.0 (vendored `static/lib/highlight.min.js`, header-confirmed) | Code syntax highlighting in chat/document rendering. |
| SheetJS (xlsx.js) | header `xlsx.js (C) 2013-present SheetJS`, `static/lib/xlsx.full.min.js` | Client-side `.xlsx` spreadsheet read/write (document tools). |
| docx.js | `static/lib/docx.umd.min.js` | Client-side `.docx` Word document generation. |
| mammoth.js | internal `version="3.7.1"`, `static/lib/mammoth.browser.min.js` | Converts `.docx` → HTML in the browser. |
| html2pdf.js (bundles jsPDF + html2canvas) | `static/lib/html2pdf.bundle.min.js` | HTML → PDF export; jsPDF generates the PDF, html2canvas rasterizes the DOM. |
| node-qrcode | `static/lib/qrcode.min.js` | Client-side QR-code rendering for 2FA setup. |
| KaTeX | 0.16.22 (CDN, `cdn.jsdelivr.net`) | Math typesetting in rendered chat/Markdown content. |
| Mermaid | v11 (CDN, `cdn.jsdelivr.net`) | Renders diagram code blocks (flowcharts, sequence diagrams) inside Markdown output. |
| Pyodide | 0.27.5 (CDN, dynamically injected by `static/js/codeRunner.js`) | In-browser Python runtime for the "run code" chat tool — executes Python client-side via WebAssembly. |
| PDFObject | 2.1.1 (referenced in ACKNOWLEDGMENTS.md; only reachable as an optional export path inside the vendored html2pdf bundle, no direct top-level script tag found) | Inline PDF embedding (conditional/indirect use only). |
| Custom Markdown renderer | `static/js/markdown.js` + `static/js/markdown/tableRow.js` | Hand-written Markdown parser — no `marked`/`markdown-it` dependency. |
| Custom image editor | `static/js/galleryEditor.js` + `static/js/editor/**` (tools: move/crop/lasso/wand/clone/stroke/transform; filters: blur, edge-feather) | Hand-built, dependency-free HTML5-canvas raster image editor (labeled "ALPHA" in the UI) — not a vendored library (confirmed: zero ProseMirror/TipTap/Quill/CodeMirror references anywhere in the repo). |
| Fira Code, Inter, GohuFont | — (`static/fonts/`) | Bundled webfonts: Fira Code (code), Inter (UI text), GohuFont (custom monospace accent). |
| Service Worker (native browser API) | `static/sw.js`, cache `apollo-v340` | PWA offline shell caching — stale-while-revalidate for `/`, network-first for JS/CSS, cache-first for static assets; never intercepts `/api/*`. |
| Web App Manifest | `static/manifest.json` | PWA installability metadata (standalone display, icons, theme color). |
| `EventSource` (native) | `static/js/research/jobs.js` | Server-Sent Events client for streaming Deep Research job progress (`/api/research/stream/{id}`). |
| `WebSocket` (native) | `static/js/browserPanel.js` | Drives the embedded/remote browser automation panel in real time. |
| `@anthropic-ai/sdk` | 0.98.0 (npm) | Anthropic API client available in the Node-level dependency tree (top-level `package.json` dependency). |

---

## 4. AI/ML & model serving

| Name | Role in this project |
|---|---|
| Anthropic API (native) | `src/llm_core.py` / `src/endpoint_resolver.py` implement a hand-written adapter that talks to `api.anthropic.com` directly (`ANTHROPIC_MODELS` list, `anthropic-version: 2023-06-01` header, OpenAI↔Anthropic content-block conversion). |
| Ollama (local or `ollama.com`) | Native `/api/chat`-protocol adapter in `src/llm_core.py` (`_is_ollama_native_url`, `_build_ollama_payload`, `_normalize_ollama_url`) — auto-detects `localhost:11434` or `ollama.com` cloud endpoints. |
| OpenAI-compatible endpoints (LM Studio, vLLM, llama.cpp server, OpenRouter, etc.) | `src/llm_core.py`/`src/endpoint_resolver.py` treat any non-Anthropic/non-Ollama base URL as an OpenAI-schema chat-completions endpoint — this is the generic path for LM Studio, vLLM, and self-hosted llama.cpp servers. |
| llama.cpp / `llama-server` | Managed local inference backend — `local://llama.cpp...` sentinel URLs are rewritten to a live llama-server URL (`src/llm_core.py`); `services/localmodels/server_manager.py` launches/manages the `llama-server` process; installed via Homebrew (`brew install llama.cpp`) on macOS or `winget install llama.cpp` on Windows. |
| GGUF model files | `services/localmodels/gguf_meta.py`, `services/localmodels/scanner.py` — parses GGUF metadata (context length, quantization) from local model files for the Cookbook / hardware-fit UI. |
| ChromaDB | Embedded vector database for RAG and long-term memory (`src/chroma_client.py`, `src/rag_vector.py`, `src/memory_vector.py`) — `PersistentClient` under `data/chroma` by default. A known pre-auth code-injection CVE (PYSEC-2026-311) is tracked as an accepted exception in `security/dependency-audit-exceptions.json` because Apollo never runs Chroma's HTTP server. |
| fastembed (ONNX) | Local embedding fallback — `sentence-transformers/all-MiniLM-L6-v2` by default (`src/embeddings.py`), used when no `EMBEDDING_URL` (Ollama/vLLM/llama.cpp `/v1/embeddings`) is configured. |
| Custom memory subsystem | `services/memory/` (`brain.py`, `distiller.py`, `memory_extractor.py`, `graph.py`, `skills.py`) plus `src/memory.py`, `src/memory_vector.py` — a bespoke long-term-memory pipeline (bullet extraction, distillation, skill/graph memory) built directly on ChromaDB + SQLite. Confirmed **not** a wrapper around mem0 (no `mem0` reference anywhere in the codebase). |
| faster-whisper (CTranslate2) | Local speech-to-text (`services/stt/stt_service.py`), CPU by default with CUDA auto-detection; optional dependency. |
| Kokoro-82M | Local GPU text-to-speech voice (`services/tts/tts_service.py`, `_get_kokoro`) — the default "local" TTS provider on GPU-equipped machines. |
| Piper TTS | CPU/Apple-Silicon-friendly local TTS alternative to Kokoro (`services/tts/tts_service.py`, ONNX voice files). |
| PyTorch + HuggingFace `diffusers` | `scripts/diffusion_server.py` — optional standalone OpenAI-compatible image-generation server (Stable Diffusion/Flux pipelines), explicitly blocks `xformers` via a fake module shim. |
| Tongyi DeepResearch (Alibaba-NLP) — adapted | Multi-step deep-research agent pipeline logic adapted (Apache-2.0, per `ACKNOWLEDGMENTS.md`) into `services/research/`, `routes/research_routes.py`. |
| llmfit (Alex Jones) — adapted | Hardware-aware model-fit scoring engine adapted (MIT) into `services/hwfit/` (`fit.py`, `hardware.py`, `profiles.py`, `models.py`) — the Cookbook's "What Fits?" model-download/serve advisor. |
| opencode — adapted | Agent-loop / tool-execution patterns adapted (MIT) from the opencode project into Apollo's agent runtime (`src/agent_loop.py`, `src/tool_execution.py` and related). |
| Model Context Protocol (MCP) | `mcp` Python SDK — Apollo both hosts four first-party MCP servers (`mcp_servers/email_server.py`, `image_gen_server.py`, `memory_server.py`, `rag_server.py`, each a `mcp.server.Server` over stdio) and acts as an MCP client (`src/mcp_manager.py`) to third-party MCP servers configured by the user. |
| browser-use | Isolated-venv (`.apollo/browser-use-venv`) agentic browser-automation library (`requirements-browser-use.txt`, pinned `0.13.0`) kept out of the main venv because its `aiohttp` pin conflicts with ChromaDB's Kubernetes client dependency; verified via `services/paperclip/browser_use_verifier.py`. |
| Embedded Python kernel worker | `scripts/apollo_kernel_worker.py` — a persistent, JSON-line-protocol Python execution worker for the agent's `python_session` tool (one process per chat session, `exec()`-based, inspired by prime-agent's persistent-IPython approach, deliberately avoiding `ipykernel`/`jupyter_client`). |
| Pyodide | In-browser (WASM) Python runtime for the chat "run code" UI feature — see §3. |

---

## 5. Data & storage

| Name | Version | Role in this project |
|---|---|---|
| SQLite | (stdlib `sqlite3`) | Primary relational store — chat sessions, messages, documents, settings, webhooks, etc. (`core/database.py`); `PRAGMA foreign_keys=ON` enforced on every connection. |
| SQLAlchemy | 2.0.51 | ORM layer over SQLite — declarative models, `sessionmaker`, engine/event hooks in `core/database.py`. |
| ChromaDB | 1.5.9 | Embedded vector store for RAG documents and semantic memory (see §4). |
| fastembed / ONNX Runtime | 0.8.0 / 1.27.0 | Local embedding generation feeding ChromaDB. |
| Fernet (cryptography) | 49.0.0 | At-rest symmetric encryption for sensitive settings/secrets persisted in SQLite (`src/secret_storage.py`). |
| JSON files (flat-file store) | — | Various app config/state (`data/auth.json` for auth config per `core/auth.py`'s own docstring, presets, features, memory seed files referenced in `packaging/apollo.spec`). |
| PostgreSQL | 17-alpine (Docker image, `docker-compose.yml`, `paperclip-db` service, profile `paperclip`) | Backing database for the optional bundled Paperclip agent-management UI — isolated from Apollo's own SQLite data. |
| GGUF model files | — | On-disk local LLM weight format read/managed by `services/localmodels/` (not a "database" but a first-class local data asset type). |

---

## 6. Testing & QA

| Name | Version | Role in this project |
|---|---|---|
| pytest | 9.1.1 | Python test runner — `pyproject.toml` sets `testpaths = ["tests"]`, `asyncio_mode = "auto"`; CI runs `python -m pytest -v -rf --tb=short`. |
| pytest-asyncio | 1.4.0 | Enables `async def test_...` test functions (auto mode). |
| Node.js built-in test runner (`node:test`, `node:assert/strict`) | Node 20 (CI: `actions/setup-node@v4`, `node-version: "20"`) | Runs all `tests/*.mjs` frontend unit tests (`npm run test:js` → `node --test tests/*.mjs`) — no external JS test framework (no Jest/Mocha/Vitest). |
| Playwright (Python) | 1.61.0 | Real-browser end-to-end tests — `tests/e2e/test_browser_smoke.py`, run via `scripts/run-e2e.sh` (starts a live uvicorn instance, drives it with Chromium). Also installed in CI: `python -m playwright install --with-deps chromium`. |
| patchright | 1.61.2 | Stealth-patched Playwright fork used by `crawl4ai` for its own scraping browser sessions (not the same code path as the app's e2e tests). |
| Antithesis `@antithesishq/bombadil` | ^0.3.2 (npm devDependency) | Fuzz/property-style UI spec (`tests/bombadil-spec.ts`) that defines DOM "extractors" (login form, chat input, modals) for autonomous exploration testing against the running app. |
| ruff | 0.15.22 | Python linter — `pyproject.toml` `[tool.ruff]` (`target-version = "py312"`, `select = ["E9","F811","F821","E722"]`). |
| pip-audit | 2.10.1 | Dependency vulnerability scanning — `scripts/check_dependency_audit.py` wraps it with an expiring-exception allowlist (`security/dependency-audit-exceptions.json`); run weekly via `.github/workflows/dependency-audit.yml`. |
| pip-tools | 7.6.0 | Regenerates/verifies `requirements*.txt` locks from `requirements*.in` (`pip-compile`); `scripts/check_dependency_locks.py` enforces the locks stay in sync in CI. |
| npm audit | (built into npm) | JS dependency vulnerability scan — `npm audit --omit=dev --audit-level=high` in `dependency-audit.yml`. |
| Custom static-analysis scripts | — | `scripts/check_runtime_paths.py` (Python `ast`-based check rejecting hardcoded checkout-relative data paths), `scripts/check_module_sizes.py` (line-count ratchet against baselines for `static/js/*.js`), `scripts/check_exception_handlers.py`/`audit_exception_handlers.py`. |
| `python -m compileall` | — | Syntax-checks the entire tree (`app.py companion core routes services src scripts`) as a CI gate. |

---

## 7. Build, packaging & distribution

| Name | Role in this project |
|---|---|
| PyInstaller | Freezes the app into a native binary — `packaging/apollo.spec` (onedir build, entry point `packaging/apollo_boot.py`) collects data/binaries/hidden-imports for `chromadb, onnxruntime, fastembed, tokenizers, cryptography, pydantic, pydantic_core, crawl4ai, mcp, caldav, icalendar, markdown, qrcode, pyotp, huggingface_hub, tqdm, certifi` plus all of `uvicorn`'s dynamic submodules; used by both `build-macos-bundle.sh` and `.github/workflows/build-windows-exe.yml` (`pyinstaller --onefile --console --name Apollo scripts/windows_launcher.py`). |
| `packaging/apollo_boot.py` | Custom PyInstaller boot shim — resolves `sys._MEIPASS`, seeds a writable app-support home directory, monkeypatches path constants, and calls `uvicorn.run()` on the imported ASGI object directly (string-based `"app:app"` re-import doesn't work inside a frozen bundle). |
| rsvg-convert (librsvg) | `packaging/make-icon.sh` renders `packaging/apollo-icon.svg` to PNGs at 10 required Apple icon sizes plus PWA 512/192 icons (`brew install librsvg`). |
| iconutil | macOS-only step in `packaging/make-icon.sh` — assembles the rendered `.iconset` into `packaging/apollo.icns` (`iconutil -c icns`); skipped gracefully on non-macOS. |
| `sips` | `build-macos-app.sh` — resizes/converts `docs/apollo.jpg` into an `.icns` for the lightweight launcher-wrapper `.app` build (best-effort, non-fatal). |
| `hdiutil` | Both `build-macos-app.sh` and `build-macos-bundle.sh` — packages the final `.app` into a compressed `.dmg` (`hdiutil create -format UDZO`). |
| `codesign` | `build-macos-bundle.sh` — ad-hoc signs the frozen `.app` bundle (`codesign --force --deep --sign -`), non-fatal if signing fails. |
| Homebrew (`brew`) | macOS dependency bootstrapper — `start-macos.sh` (`brew_ensure tmux`, `brew_ensure llama-server llama.cpp`), `packaging/make-icon.sh` (librsvg). |
| winget | Windows package manager — `launch-windows.ps1` offers interactive, consent-gated installs of Python 3.12, Git for Windows, and llama.cpp. |
| git (as build/runtime tool) | `scripts/build-windows-zip.sh` uses `git archive` (not a hand-rolled exclude list) to package only tracked files into `Apollo-Windows.zip`, verified with `unzip -tq`; `scripts/setup-searxng.sh`/`.ps1` `git clone` + pinned `git checkout <sha>` for a native SearXNG install; `update_windows.bat` runs `git pull --ff-only`. |
| zip / unzip | `scripts/build-windows-zip.sh` — `zip -qr` to build the archive, `unzip -tq` to integrity-check it. |
| PyInstaller `collect_all`/`collect_submodules`/`collect_data_files` (`PyInstaller.utils.hooks`) | Used inside `apollo.spec` for dependency discovery, described above. |
| Docker (multi-stage-free, single `Dockerfile`) | `FROM python:3.12-slim`; installs `build-essential, cmake, curl, git, nodejs, npm, tmux, openssh-client, gosu` via apt — cmake/git for on-demand llama.cpp builds, nodejs/npm for the bundled Browser MCP server's `npx`, gosu for clean privilege-drop. |
| `gosu` | Docker entrypoint (`docker/entrypoint.sh`) drops from root to the configured `PUID:PGID` before `exec`-ing uvicorn, so container signals reach the process directly (no `su`/`sudo` shell layer). |
| systemd | `apollo-ui.service` unit template (`Type=simple`, `Restart=always`) + `install-service.sh` (`systemctl daemon-reload/enable/start`) for native Linux service installs. |
| Node.js runtime (auto-provisioned) | `services/paperclip/node_bootstrap.py` downloads a pinned Node LTS build (default `22.13.0`) from `nodejs.org/dist` directly into Apollo's data dir on first native launch, so the bundled Paperclip Node app runs with zero user Node prerequisite. |
| ffmpeg / ffprobe | `scripts/encode_previews.sh` — encodes demo screen recordings into VP9/WebM (`libvpx-vp9`) and H.264/MP4 (`libx264 -movflags +faststart`) preview clips for the README/docs. |

---

## 8. CI/CD

| Name | Role in this project |
|---|---|
| GitHub Actions | Three workflows in `.github/workflows/`: `ci.yml` (PR + push-to-main matrix test across `ubuntu-latest/macos-latest/windows-latest`, plus a Ubuntu-only Playwright e2e job), `build-windows-exe.yml` (manual `workflow_dispatch`, PyInstaller build of `Apollo.exe`), `dependency-audit.yml` (weekly cron `17 4 * * 1`, pip-audit + npm audit). |
| `actions/checkout@v4` | Source checkout in every workflow. |
| `actions/setup-python@v5` | Provisions Python 3.12 with pip caching in every workflow. |
| `actions/setup-node@v4` | Provisions Node 20 with npm caching in `ci.yml` and `dependency-audit.yml`. |
| `actions/upload-artifact@v4` | Publishes the built `Apollo.exe` + SHA-256 checksum JSON in `build-windows-exe.yml` (90-day retention). |
| `npm ci` / `npm run test:js` / `npm audit` | JS dependency install, `node --test` suite execution, and vulnerability scan gates in CI. |

---

## 9. Dev & ops tooling

| Name | Role in this project |
|---|---|
| tmux | Required by Apollo's "Cookbook" feature for backgrounded model downloads/serves — installed via Homebrew (`start-macos.sh`) or apt (`Dockerfile`); `routes/cookbook_routes.py`/`routes/cookbook_helpers.py` drive it via `tmux capture-pane` for log streaming. |
| OpenSSH client (`ssh`, `ssh-keygen`, `ssh-copy-id`) | Cookbook's remote-server management (probing, provisioning keys, running downloads/serves on remote hosts) — `routes/cookbook_routes.py` resolves `ssh-keygen` on Windows via the OpenSSH client bundled with Win10+. |
| Tailscale (optional, detected not bundled) | `start-macos.sh` checks `command -v tailscale` / `tailscale ip -4` to surface a LAN-accessible URL for the running server. |
| rsync | Referenced only in test/documentation comments (`tests/test_build_windows_zip.py`) explaining why `git archive` was chosen over a hand-rolled rsync/exclude approach — not actually invoked by any shipped script. |
| Ollama (external, interoperated with) | Companion local-model server Apollo talks to over HTTP — not bundled (MIT-licensed, per `ACKNOWLEDGMENTS.md`). |
| Radicale (external) | Example CardDAV/CalDAV server Apollo's contacts/calendar sync can point at (not bundled, GPL-3.0). |
| Dovecot / isync (mbsync) (external) | Example IMAP server / mailbox-sync tools referenced as companion services for the email feature (not bundled). |
| NVIDIA Container Toolkit / ROCm | GPU passthrough for Docker — `scripts/check-docker-gpu.sh` (NVIDIA, diagnostic + optional `apt`/`nvidia-ctk` install) and `scripts/check-docker-amd-gpu.sh` (AMD ROCm, read-only diagnostic); enabled via `docker/gpu.nvidia.yml` / `docker/gpu.amd.yml` compose overlays. |
| ntfy | Self-hosted push-notification service, bundled as an optional `docker-compose.yml` service (`binwiederhier/ntfy`) — Apollo's reminder/notification code (`src/tool_index.py`, `src/task_scheduler.py`, `src/integrations.py`, `routes/note_routes.py`) can deliver alerts to it. |
| Node.js / npm / npx | Backs both the optional Browser MCP server (`npx`, installed via apt in Docker) and the bundled Paperclip Node application (auto-provisioned per-platform by `services/paperclip/node_bootstrap.py`). |
| `python -m venv` | Standard virtualenv creation in every native launch path (`start-macos.sh`, `launch-windows.ps1`) and the isolated browser-use environment (`scripts/setup-browser-use-env`). |

---

## 10. Third-party services & upstream data sources

| Name | Role in this project |
|---|---|
| Anthropic API | Primary hosted LLM provider integration (native adapter, see §4). |
| OpenAI API / OpenAI-compatible providers | Alternate hosted/self-hosted LLM provider path (chat + `/v1/embeddings`, `/v1/images/generations` compatibility surfaces). |
| Ollama Cloud (`ollama.com`) | Hosted variant of the Ollama native API, auto-detected alongside local Ollama. |
| SearXNG | Default metasearch backend. Two deployment modes: (a) natively installed by `scripts/setup-searxng.sh`/`.ps1` (pinned commit `4dd0bf48670727f6ae1086ffa72e76f6eb869741`, cloned from `github.com/searxng/searxng`, run via `python -m searx.webapp` in a dedicated venv — `services/searxng/runtime.py`/`config.py`), or (b) the Docker Compose service pinned to `searxng/searxng:2026.5.31-7159b8aed` (a specific tag pinned because the following `2026.6.2` release crashes on boot). |
| DuckDuckGo | Search provider option (`duckduckgo-search`, optional Python dependency) and general web-search fallback. |
| Brave Search API | Search provider — `DATA_BRAVE_API_KEY` env var (`docker-compose.yml`, `services/search/providers.py`). |
| Google Programmable Search Engine (PSE) | Search provider — `GOOGLE_API_KEY` / `GOOGLE_PSE_CX` env vars. |
| Tavily | Search provider — `TAVILY_API_KEY` env var. |
| Serper | Search provider — `SERPER_API_KEY` env var. |
| HuggingFace Hub | Model/dataset downloads for fastembed, Cookbook model management (`scripts/hf_download.py`, `services/hwfit/`), and diffusion models. |
| nodejs.org distribution index | Source of the auto-provisioned Node.js runtime bundled for Paperclip (`services/paperclip/node_bootstrap.py` fetches `https://nodejs.org/dist/index.json`). |
| Paperclip (paperclipai/paperclip) | Optional bundled agent-management UI — built from a pinned upstream tag `v2026.529.0` (Docker) or auto-provisioned Node app (native), reverse-proxied at `/paperclip` (`services/paperclip/`, `routes/paperclip_routes.py`). MIT-licensed upstream project, not authored by Apollo. |
| YouTube (`youtube_transcript_api`) | Transcript retrieval for YouTube links shared in chat. |
| CalDAV servers (generic) | Any RFC-4791-compliant calendar server the user configures (Radicale, Nextcloud, Google Calendar's CalDAV endpoint, etc.) — `src/caldav_sync.py`. |
| IMAP/SMTP mail servers (generic) | Any standards-compliant mail provider the user configures for the email feature (`routes/email_routes.py`, `mcp_servers/email_server.py` — `imaplib`/`smtplib`). |
| Odysseus (pewdiepie-archdaemon/odysseus) | Upstream project Apollo is a renamed distribution of — per `ACKNOWLEDGMENTS.md`, "all of the original project's architecture, features, and code are the work of the Odysseus author," MIT-licensed. |
| opencode (anomalyco/opencode) | Source of adapted agent-loop/tool-execution patterns (MIT, see §4). |
| llmfit (AlexsJones/llmfit) | Source of the adapted hardware-fit/model-scoring engine (MIT, see §4). |
| Tongyi DeepResearch (Alibaba-NLP) | Source of the adapted deep-research pipeline (Apache-2.0, see §4). |

---

## Summary counts

| Category | Entries |
|---|---|
| 1. Languages | 7 |
| 2. Backend frameworks & libraries | 43 |
| 3. Frontend | 17 |
| 4. AI/ML & model serving | 18 |
| 5. Data & storage | 7 |
| 6. Testing & QA | 12 |
| 7. Build/packaging/distribution | 15 |
| 8. CI/CD | 6 |
| 9. Dev & ops tooling | 10 |
| 10. Third-party services & upstream data sources | 17 |
| **Total distinct technologies catalogued** | **~152** |

*(Some entries are cross-referenced across categories — e.g. ChromaDB appears in both "Backend" and "Data & storage" because it is both a Python library and a persisted vector store — so this is a count of table rows, not strictly unique names.)*
