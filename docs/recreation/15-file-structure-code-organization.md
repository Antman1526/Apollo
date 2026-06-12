# Apollo — File Structure & Code Organization

> Source root: `/Users/Antman/Apollo`. Counts (verified): `routes/` = 53 `*.py`, `src/` = 79
> `*.py`, `tests/` = 267 `*.py` + 4 `*.mjs`, `static/js/` ≈ 90 ES modules.

---

## 1. Top-Level Layout

```
Apollo/
├── app.py                       # Slim FastAPI orchestrator (1278 lines): middleware,
│                                #   manager init, ~40 router registrations, lifecycle events
├── setup.py                     # First-run: data dirs, DB, admin account + temp password
├── pyproject.toml               # pytest config only (testpaths, asyncio_mode=auto)
├── requirements.txt             # Core deps (FastAPI, chromadb, fastembed, crawl4ai, …)
├── requirements-optional.txt    # Per-feature extras (faster-whisper, piper-tts, PyMuPDF, …)
├── requirements-browser-use.txt # browser-use==0.13.0 + litellm (separate venv)
├── package.json                 # JS test runner + @anthropic-ai/sdk; NO frontend build
├── package-lock.json
├── docker-compose.yml           # apollo + chromadb + searxng + ntfy + paperclip(+db) profile
├── Dockerfile / .dockerignore
├── start-macos.sh               # Native macOS quick-start (venv + brew deps + uvicorn)
├── launch-windows.ps1           # Native Windows launcher
├── build-macos-app.sh           # Builds dist/Apollo.app + Apollo.dmg
├── install-service.sh / apollo-ui.service   # Linux systemd install
├── update_windows.bat
├── .env / .env.example          # Config (secrets commented out as placeholders)
│
├── routes/      (53 *.py)       # TIER 2 — HTTP boundary (APIRouter factories)
├── src/         (79 *.py)       # TIER 2 — app logic, managers, handlers, agent loop
├── services/    (subsystems)    # TIER 2 — self-contained subsystems w/ own runtimes
├── core/        (10 *.py)       # TIER 2 — cross-cutting primitives (db, auth, session)
├── static/                      # TIER 1 — vanilla-JS frontend (ES modules, no build)
├── mcp_servers/                 # Built-in MCP servers (email, image-gen, memory, rag)
├── companion/                   # Phone-pairing companion (pairing + routes)
├── config/                      # searxng/settings.yml template
├── scripts/                     # apollo-* CLIs + setup/maintenance scripts
├── tests/       (267 *.py)      # pytest suite + 4 *.mjs (node --test)
├── docs/                        # README assets, OPERATIONS.md, landing page (index.html)
├── docker/                      # GPU overlay compose files (gpu.nvidia.yml, gpu.amd.yml)
├── licenses/ · ACKNOWLEDGMENTS.md · LICENSE · SECURITY.md · THREAT_MODEL.md · ROADMAP.md
│
├── data/        (gitignored)    # TIER 3 — SQLite, ChromaDB, JSON state, model caches
├── logs/        (gitignored)    # Runtime logs (searxng.log, …)
├── venv/        (gitignored)    # Main Python virtualenv
├── .apollo/                     # Isolated browser-use venv (.apollo/browser-use-venv)
└── dist/                        # Built desktop artifacts (Apollo.app, Apollo.dmg)
```

---

## 2. `routes/` — HTTP Boundary (53 modules)

**Convention:** every file is `<concern>_routes.py` and exports a `setup_<concern>_routes(...)`
factory that returns a FastAPI `APIRouter`. Shared helpers sit beside them as
`<concern>_helpers.py` (not registered as routers). `app.py` calls each factory through
`services/app_startup.py` (`build_and_include_router` / `RouterSpec`), so dependencies are
**injected**, never imported globally.

```
routes/
├── __init__.py
├── auth_routes.py            api_token_routes.py       prefs_routes.py
├── chat_routes.py            chat_helpers.py           session_routes.py
├── research_routes.py        compare_routes.py         history_routes.py
├── search_routes.py          embedding_routes.py       model_routes.py
├── localmodels_routes.py     lmproxy_routes.py         model ... (proxy + serving)
├── document_routes.py        document_helpers.py       editor_draft_routes.py
├── gallery_routes.py         gallery_helpers.py        upload_routes.py
├── memory_routes.py          skills_routes.py          assistant_routes.py
├── task_routes.py            note_routes.py            calendar_routes.py
├── email_routes.py           email_helpers.py          email_pollers.py
├── contacts_routes.py        personal_routes.py        vault_routes.py
├── mcp_routes.py             webhook_routes.py          integration_routes.py
├── paperclip_routes.py       browser_routes.py          shell_routes.py
├── cookbook_routes.py        cookbook_helpers.py        hwfit_routes.py
├── tts_routes.py             stt_routes.py              signature_routes.py
├── font_routes.py            emoji_routes.py            preset_routes.py
├── backup_routes.py          cleanup_routes.py          admin_wipe_routes.py
├── diagnostics_routes.py     system_status_routes.py    companion (in companion/routes.py)
└── ...
```
**Naming signal:** `*_routes.py` = registered router · `*_helpers.py` = supporting functions ·
`email_pollers.py` = background pollers (not a router).

---

## 3. `src/` — Application Logic & Managers (79 modules)

Holds long-lived **managers** (`*_manager.py`), request **handlers** (`*_handler.py`),
**processors**, and the agent/tool machinery. Imports `core/` freely; never imports `routes/`.

```
src/
│  ── Bootstrap / config ──
├── app_initializer.py        # initialize_managers() — builds all singletons (called by app.py)
├── app_helpers.py            config.py            constants.py            settings.py
├── readiness.py              event_bus.py         exceptions.py           request_models.py
│
│  ── Chat / agent core ──
├── chat_handler.py           chat_processor.py    chat_helpers.py
├── ai_interaction.py         llm_core.py          model_context.py        model_discovery.py
├── agent_loop.py             agent_runs.py        agent_tools.py          ralph_loop.py
├── tool_execution.py         tool_implementations.py  tool_index.py       tool_schemas.py
├── tool_parsing.py           tool_security.py     builtin_actions.py      builtin_mcp.py
├── action_intents.py         endpoint_resolver.py context_budget.py       context_compactor.py
│
│  ── Research / web ──
├── research_handler.py       research_utils.py    deep_research.py        web_decider.py
├── url_safety.py             topic_analyzer.py    visual_report.py        youtube_handler.py
│
│  ── Memory / RAG ──
├── memory.py                 memory_vector.py     rag_manager.py          rag_singleton.py
├── rag_vector.py             chroma_client.py     embeddings.py
│
│  ── Documents / personal ──
├── document_processor.py     document_actions.py  personal_docs.py        upload_handler.py
├── pdf_runtime.py            pdf_forms.py         pdf_form_doc.py          markitdown_runtime.py
│
│  ── Data / scheduling / integrations ──
├── database.py               preset_manager.py    api_key_manager.py      secret_storage.py
├── task_scheduler.py         task_endpoint.py     bg_jobs.py              bg_monitor.py
├── caldav_sync.py            caldav_writeback.py  integrations.py         webhook_manager.py
├── mcp_manager.py            cleanup_service.py   rate_limiter.py         prompt_security.py
├── auth_helpers.py           session_actions.py   email_thread_parser.py  ...
│
├── search/                   # Mirror of services/search (analytics, cache, content, core,
│                             #   providers, query, ranking) — provider chain internals
└── cache/  (content/ search/)
```
**Naming signals:** `*_manager.py` = stateful singleton · `*_handler.py` = orchestrates a
request type · `*_singleton.py` = lazy global accessor (`get_*()`) · `builtin_*` = built-in
agent tools/actions.

---

## 4. `services/` — Self-Contained Subsystems

Each subdirectory owns a `config.py` (dataclass + `load_config()`), a `runtime.py` /
`*_manager.py` (process supervision), and an `__init__.py` re-exporting the public surface.
Pattern is consistent across sidecars (searxng/runtime mirrors paperclip/runtime).

```
services/
├── __init__.py
├── app_startup.py            # RouterSpec + include_router_checked + register_router_specs
├── system_status.py
│
├── search/                   # Provider-chain web search
│   ├── service.py  core.py  providers.py  query.py  ranking.py
│   ├── content.py  cache.py  analytics.py
│
├── searxng/                  # Managed no-Docker SearXNG sidecar (port 8893)
│   ├── config.py             #   SearxngConfig (DEFAULT_PORT=8893, data/searxng/ paths)
│   └── runtime.py            #   SearxngRuntime: spawn/health/watchdog (300s cooldown)
│
├── browser/
│   └── embedded_browser.py   # Playwright Chromium session; scheme allow/block lists
│
├── localmodels/              # Local GGUF serving via llama.cpp
│   ├── server_manager.py     #   LocalModelServer: launch/track llama-server, 1 warm chat model
│   ├── scanner.py  gguf_meta.py  registry.py  lifecycle.py  config.py
│
├── paperclip/                # Agent-management Node sidecar + Floor
│   ├── config.py  runtime.py  proxy.py  collector.py  events.py
│   ├── agent_tokens.py  node_bootstrap.py  browser_use_verifier.py
│
├── memory/                   # Semantic memory + skills
│   ├── memory.py  memory_vector.py  memory_extractor.py  service.py
│   ├── skills.py  skill_extractor.py  skill_format.py
│
├── research/                 # crawl4ai-backed research
│   ├── research_handler.py  service.py  crawl4ai_adapter.py
│
├── integrations/             # agent_workbench.py
├── tts/  tts_service.py       stt/  stt_service.py
├── faces/ · hwfit/ · shell/ · youtube/ · docs/ · cache/
```

---

## 5. `core/` — Cross-Cutting Primitives (10 modules)

Lowest layer. Imported by `src/`, `services/`, and `routes/`; imports nothing from them.

```
core/
├── database.py        # SQLAlchemy engine + ~30 ORM models + SessionLocal; PRAGMA foreign_keys=ON
├── auth.py            # AuthManager (users, token validate, privileges)
├── session_manager.py # SessionManager (sessions.json persistence)
├── middleware.py      # SecurityHeadersMiddleware, INTERNAL_TOOL_HEADER/TOKEN
├── models.py          # Domain models; set_session_manager() wiring for Session.add_message()
├── constants.py       # APP_VERSION, BASE_DIR/STATIC_DIR/DATA_DIR, ports, defaults
├── exceptions.py      # SessionNotFoundError, LLMServiceError, WebSearchError, …
├── atomic_io.py       # Atomic file writes for JSON state
├── platform_compat.py # OS-specific path/behavior shims
└── __init__.py
```
> Note: both `core/constants.py` and `src/constants.py` exist; `core/constants.py` defines
> `APP_VERSION="0.9.1"`, `BASE_DIR`, `DATA_DIR`, and the LLM/SearXNG env defaults consumed at
> startup.

---

## 6. `static/` — Frontend (TIER 1, no build step)

```
static/
├── index.html  app.js  style.css            # entry shell + global bootstrap
├── landing.html  login.html
├── manifest.json  sw.js                       # PWA
├── icon-192.png  icon-512.png  fonts/  lib/
└── js/   (~90 ES modules + feature subdirs)
    ├── init.js  ui.js  storage.js  platform.js  a11y.js  modalManager.js
    ├── chat.js  chatStream.js  chatRenderer.js  search-chat.js  spinner.js
    ├── research/(jobs.js panel.js)  researchSynapse.js
    ├── compare/(state models panes probe scoreboard selector stream vote icons index)
    ├── editor/  (40+ modules: canvas-*, layer-*, ai-* tools, fx/, filters/, tools/, wire-*)
    ├── cookbook.js  cookbook-hwfit.js  cookbook-diagnosis.js  cookbookDownload/Serve/Running.js
    ├── paperclip.js  browserPanel.js              # Floor + embedded browser UI
    ├── memory.js  rag.js  skills.js  presets.js  models.js  modelPicker.js  providers.js
    ├── emailInbox.js  emailLibrary/  calendar.js  calendar/  notes.js  tasks.js
    ├── settings.js  admin.js  theme.js  signature.js  gallery.js  galleryEditor.js
    ├── tts-ai.js  voiceRecorder.js  document.js  documentLibrary.js  fileHandler.js
    ├── slashCommands.js  slashAutocomplete.js  keyboard-shortcuts.js  tourAutoplay.js
    ├── markdown.js  markdown/  color/  util/  systemStatusCard.js  systemStatusActions.js
    └── MODULE_SUMMARY.md                         # in-tree module index
```
**Convention:** flat `camelCase.js` modules; multi-file features get a subdir
(`editor/`, `compare/`, `research/`, `emailLibrary/`). Server-side cache control via
`_RevalidatingStatic` (`app.py:398-414`) gives `.js/.css/.html` `Cache-Control: no-cache`.

---

## 7. `mcp_servers/`, `companion/`, `scripts/`, `tests/`

```
mcp_servers/                 # Built-in Model Context Protocol servers (agent tools)
├── _common.py  email_server.py  image_gen_server.py  memory_server.py  rag_server.py

companion/                   # Phone-pairing companion app
├── pairing.py  routes.py    # setup_companion_routes registered in app.py

scripts/                     # CLIs (apollo-*) + setup/maintenance
├── apollo  apollo-mail  apollo-memory  apollo-notes  apollo-tasks  apollo-calendar
│   apollo-research  apollo-skills  apollo-cookbook  apollo-gallery  apollo-webhook ...
├── setup-searxng.sh  setup-searxng.ps1   # native SearXNG installers
├── setup-browser-use-env                 # builds the isolated browser-use venv
├── diffusion_server.py  hf_download.py  index_documents.py
├── migrate_faiss_to_chroma.py  update_database.py  claim_ownerless.py
├── check.sh  check-docker-gpu.sh  check-docker-amd-gpu.sh  check-paperclip-browser
└── _lib/  _completion/  windows-launcher/  demo_email/

tests/                       # 267 pytest *.py + 4 node *.mjs
├── conftest.py  real_modules.py
├── test_app.py  test_app_startup_helpers.py  test_auth_*.py  test_browser_ws.py
├── test_browser_use_integration.py  test_agent_loop.py  test_caldav_*.py  ...
├── *.mjs  (test_paperclip_floor_ui, test_system_status_card/actions, test_theme_presets)
└── bombadil-spec.ts  (@antithesishq/bombadil fuzz harness)
```
Test naming mirrors the unit under test: `test_<module>.py`. JS UI tests are `*.mjs` run via
`node --test` (see `package.json` `test:js`).

---

## 8. `data/` — TIER 3 State (gitignored)

```
data/
├── app.db  apollo.db  scheduled_emails.db     # SQLite (DATABASE_URL → app.db)
├── chroma/  rag/  memory_vectors/             # ChromaDB persistent vectors
├── fastembed_cache/                           # local ONNX embedding model cache
├── sessions.json (via SESSIONS_FILE)  settings.json  memory.json  presets.json
├── auth.json  features.json
├── searxng/                                   # managed sidecar: src/ (git), venv/, settings.yml
├── personal_docs/  personal_uploads/  uploads/  mail-attachments/
├── generated_images/  tts_cache/  skills/  deep_research/
```

---

## 9. Module-Dependency Rules (observed)

```
        routes/  ──────────────┐
           │                   │  (factories receive injected managers)
           ▼                   ▼
         src/  ◀──────────▶  services/
           │                   │
           └────────┬──────────┘
                    ▼
                  core/   (database, auth, session, constants — depends on nothing internal)
```

- **No upward imports:** `core/` imports neither `src/`, `services/`, nor `routes/`.
- **`routes/` is thin:** parse → call `src`/`services` → shape response; logic does not live here.
- **Dependency injection over globals:** `app.py` builds singletons once via
  `src/app_initializer.initialize_managers()` and passes them into each
  `setup_*_routes(...)` factory (registered through `services/app_startup.py`).
- **Subsystem isolation:** each `services/<x>/` owns its `config.py` + `runtime.py`/`*_manager.py`;
  sidecar runtimes share a common shape (injectable spawn/health, graceful no-op when disabled,
  never raise into startup) — e.g. `services/searxng/runtime.py` mirrors
  `services/paperclip/runtime.py`.
- **Lazy singletons:** vector/RAG/local-model access goes through `get_*()` accessors
  (`src/rag_singleton.py`, `services/localmodels` `get_server()`, `services/searxng` `get_runtime()`)
  so a missing backend degrades to `None`/fallback instead of crashing.

---

## 10. Where Each Concern Lives (quick map)

| Concern | Frontend | Route | Logic | Data |
|---------|----------|-------|-------|------|
| Chat / streaming | `static/js/chat.js`, `chatStream.js` | `routes/chat_routes.py` | `src/chat_handler.py`, `chat_processor.py` | `ChatMessage` (SQLite) |
| Web search | `search-chat.js` | `routes/search_routes.py` | `src/web_decider.py`, `services/search/`, `services/searxng/` | search cache |
| Local models | `cookbook*.js`, `modelPicker.js` | `routes/localmodels_routes.py`, `lmproxy_routes.py` | `services/localmodels/server_manager.py` | GGUF dirs, HF cache |
| Research | `research/`, `researchSynapse.js` | `routes/research_routes.py` | `src/research_handler.py`, `services/research/` | `data/deep_research/` |
| Memory / RAG | `memory.js`, `rag.js` | `routes/memory_routes.py`, `embedding_routes.py` | `src/memory*.py`, `services/memory/` | ChromaDB |
| Agents / Paperclip | `paperclip.js` | `routes/paperclip_routes.py` | `services/paperclip/`, `src/agent_loop.py` | Postgres (Docker) |
| Embedded browser | `browserPanel.js` | `routes/browser_routes.py` | `services/browser/embedded_browser.py` | Playwright |
| Email / Calendar | `emailInbox.js`, `calendar.js` | `email_routes.py`, `calendar_routes.py` | `src/caldav_sync.py`, `email_thread_parser.py` | `EmailAccount`, `CalendarEvent` |
| Auth | `login.html`, `admin.js` | `routes/auth_routes.py` | `core/auth.py`, `app.py:AuthMiddleware` | `auth.json`, `ApiToken` |
