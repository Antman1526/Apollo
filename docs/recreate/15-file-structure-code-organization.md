# Apollo — File Structure & Code Organization

This document maps the complete repository layout of `/Users/Antman/Apollo` (excluding `venv/`, `node_modules/`, `data/`, `logs/`, `dist/`, `__pycache__/`, `.git/`, and the runtime `.apollo/` state dir), the naming conventions that make the codebase predictable, and the module-dependency rules a rebuild must preserve. Verified by `find`/`ls` against the working tree.

## 1. Annotated directory tree

```text
Apollo/
├── app.py                      # slim orchestrator: middleware, ~45 router registrations, lifecycle
├── setup.py                    # first-run setup: data dirs, .env copy, DB create_all, admin user
├── pyproject.toml              # [tool.pytest.ini_options] testpaths=["tests"], asyncio_mode="auto"
├── requirements.txt            # core deps  ── requirements-optional.txt / requirements-browser-use.txt
├── package.json                # @anthropic-ai/sdk ^0.98.0; test:js = node --test tests/*.mjs
├── Dockerfile                  # python:3.12-slim + tmux/git/cmake/node/gosu; docker/entrypoint.sh
├── docker-compose.yml          # apollo + chromadb + searxng(pinned) + ntfy + paperclip profile
├── start-macos.sh              # brew deps, arm64 venv, setup, uvicorn on :7860
├── build-macos-app.sh          # dist/Apollo.app + dist/Apollo.dmg (native launcher over venv/)
├── launch-windows.ps1          # venv + deps + setup + uvicorn on :7000   (update_windows.bat)
├── install-service.sh / apollo-ui.service   # systemd deployment
├── README.md  ROADMAP.md  CONTRIBUTING.md  SECURITY.md  THREAT_MODEL.md  ACKNOWLEDGMENTS.md  LICENSE
├── .env.example                # documented deployment defaults (copied to .env by setup.py)
│
├── core/                       # foundation — no domain logic
│   ├── auth.py                 #   AuthManager: data/auth.json users, session tokens, 2FA
│   ├── database.py             #   SQLAlchemy engine + ~25 models + idempotent _migrate_* fns
│   ├── middleware.py           #   SecurityHeadersMiddleware (CSP nonce), require_admin, INTERNAL_TOOL_TOKEN
│   ├── constants.py            #   APP_VERSION, BASE_DIR/DATA_DIR, env-backed defaults
│   ├── session_manager.py      #   chat session persistence layer
│   ├── platform_compat.py      #   Windows/macOS path + shell shims
│   ├── exceptions.py  models.py  atomic_io.py
│
├── routes/                     # 53 files — HTTP layer only (factories returning APIRouter)
│   ├── auth_routes.py chat_routes.py session_routes.py memory_routes.py skills_routes.py
│   ├── research_routes.py history_routes.py search_routes.py preset_routes.py
│   ├── document_routes.py editor_draft_routes.py gallery_routes.py signature_routes.py
│   ├── email_routes.py email_pollers.py contacts_routes.py calendar_routes.py note_routes.py
│   ├── task_routes.py assistant_routes.py webhook_routes.py api_token_routes.py vault_routes.py
│   ├── model_routes.py localmodels_routes.py lmproxy_routes.py cookbook_routes.py hwfit_routes.py
│   ├── compare_routes.py mcp_routes.py shell_routes.py browser_routes.py paperclip_routes.py
│   ├── integration_routes.py system_status_routes.py diagnostics_routes.py cleanup_routes.py
│   ├── personal_routes.py embedding_routes.py upload_routes.py stt_routes.py tts_routes.py
│   ├── prefs_routes.py backup_routes.py font_routes.py emoji_routes.py admin_wipe_routes.py
│   └── *_helpers.py            #   chat_helpers, email_helpers, document_helpers, gallery_helpers, cookbook_helpers
│
├── services/                   # domain packages (may spawn processes, hold state)
│   ├── app_startup.py          #   RouterSpec / build_and_include_router / register_router_specs
│   ├── system_status.py
│   ├── localmodels/            #   config.py (scan dirs), scanner.py (GGUF discovery),
│   │                           #   registry.py (model-picker sync), server_manager.py (warm llama-server),
│   │                           #   lifecycle.py (rescan/startup_scan)
│   ├── paperclip/              #   config.py, events.py (EventHub), collector.py (live WS bridge),
│   │                           #   proxy.py (reverse proxy), runtime.py (native supervisor),
│   │                           #   node_bootstrap.py, agent_tokens.py, browser_use_verifier.py
│   ├── memory/                 #   memory.py, memory_vector.py, memory_extractor.py,
│   │                           #   skills.py, skill_extractor.py, skill_format.py, service.py
│   ├── search/                 #   core.py, providers.py, query.py, ranking.py, content.py,
│   │                           #   cache.py, analytics.py, service.py
│   ├── hwfit/                  #   Cookbook "What Fits?": hardware.py, fit.py, models.py, profiles.py, data/
│   ├── research/               #   crawl4ai_adapter.py
│   ├── browser/                #   embedded_browser.py (shared Playwright Chromium session)
│   ├── integrations/           #   agent_workbench.py (combined readiness status)
│   ├── docs/ shell/ stt/ tts/ youtube/ faces/ cache/
│
├── src/                        # shared engine code (~80 modules, flat)
│   ├── llm_core.py             #   streaming OpenAI-compatible client, provider quirks, fallback
│   ├── agent_loop.py agent_tools.py tool_execution.py tool_implementations.py tool_schemas.py
│   ├── chat_processor.py chat_handler.py context_budget.py context_compactor.py
│   ├── mcp_manager.py builtin_mcp.py
│   ├── task_scheduler.py event_bus.py builtin_actions.py webhook_manager.py
│   ├── rag_manager.py rag_vector.py rag_singleton.py chroma_client.py embeddings.py
│   ├── memory.py memory_vector.py personal_docs.py tool_index.py
│   ├── deep_research.py research_handler.py visual_report.py
│   ├── settings.py secret_storage.py readiness.py url_safety.py prompt_security.py
│   ├── caldav_sync.py caldav_writeback.py upload_handler.py model_discovery.py
│   ├── app_initializer.py app_helpers.py config.py constants.py ralph_loop.py ...
│
├── companion/                  # paired-device companion API (setup_companion_routes, pairing.py)
├── mcp_servers/                # built-in stdio MCP servers: email_server.py, memory_server.py,
│                               #   rag_server.py, image_gen_server.py, _common.py
│
├── static/                     # frontend — raw ES modules, no build step
│   ├── index.html login.html backgrounds.html landing.html
│   ├── app.js style.css sw.js manifest.json icon-192.png icon-512.png
│   ├── js/                     #   79 modules: chat.js, chatStream.js, chatRenderer.js, sessions.js,
│   │   │                       #   cookbook*.js, paperclip.js, browserPanel.js, settings.js, theme.js,
│   │   │                       #   memory.js, skills.js, notes.js, tasks.js, gallery.js, document.js, ...
│   │   ├── calendar/ compare/ research/ emailLibrary/ markdown/ color/ util/
│   │   └── editor/             #   image editor (build/ filters/ fx/ tools/)
│   ├── fonts/ (custom/)  lib/
│
├── scripts/                    # ops + CLI suite (bash/python, no extension)
│   ├── check.sh                #   quality gate: compileall + pytest + npm test:js
│   ├── apollo                  #   dispatcher; apollo-{backup,calendar,contacts,cookbook,docs,gallery,
│   │                           #   integrations,logs,mail,mcp,memory,notes,personal,preset,ralph,
│   │                           #   research,sessions,signature,skills,tasks,theme,webhook}
│   ├── check-paperclip-browser setup-browser-use-env
│   ├── check-docker-gpu.sh check-docker-amd-gpu.sh
│   ├── diffusion_server.py hf_download.py update_database.py index_documents.py ...
│   └── _lib/ _completion/ windows-launcher/ demo_email/
│
├── tests/                      # flat pytest suite (~250 files) + node tests
│   ├── conftest.py real_modules.py
│   ├── test_*.py  test_*.mjs  bombadil-spec.ts
│
├── config/searxng/settings.yml # SearXNG template (secret substituted at container boot)
├── docker/                     # entrypoint.sh, gpu.nvidia.yml, gpu.amd.yml
├── docs/                       # landing page (index.html), clips, OPERATIONS.md, paperclip-floor.md,
│                               #   superpowers/{plans,specs}
├── licenses/  .github/{workflows,ISSUE_TEMPLATE}  .sixth/skills  .claude/
```

## 2. Naming conventions

- **Routes**: `routes/<name>_routes.py` exporting `setup_<name>_routes(...) -> APIRouter`. Dependencies arrive as factory parameters (e.g. `setup_chat_routes(session_manager, chat_handler, chat_processor, memory_manager, research_handler, upload_handler, *, memory_vector, webhook_manager, skills_manager)`). Pure-helper siblings use `<name>_helpers.py` (no router). Registration always goes through `services/app_startup.py` (`RouterSpec` batches or `build_and_include_router` singles) so failures raise `RuntimeError("Failed to build <Label> routes")`.
- **Services**: `services/<domain>/` packages with `__init__.py`; module-level singletons exposed via `get_*()` accessors (`services/localmodels/server_manager.get_server()`, `services/tts.get_tts_service()`, `services/stt.get_stt_service()`). `services/app_startup.py` and `services/system_status.py` are the only top-level service modules.
- **Engine code**: `src/<concern>.py`, flat. Singletons via dedicated modules (`src/rag_singleton.py`) or `set_*` injection (`src/agent_tools.set_mcp_manager`, `src/event_bus.set_task_scheduler`, `src/ai_interaction.set_session_manager`).
- **Tests**: flat `tests/test_<subject>.py` (pytest, `asyncio_mode="auto"`); frontend logic tests are `tests/test_*.mjs` run via `node --test` (listed explicitly in `package.json` `test:js`); `tests/bombadil-spec.ts` for property testing.
- **Scripts**: extensionless `scripts/apollo-<area>` CLIs behind the `scripts/apollo` dispatcher; `.sh` for shell-only utilities; shared code in `scripts/_lib/`.
- **DB migrations**: `_migrate_<change>()` functions in `core/database.py`, each guarded by `PRAGMA table_info` and safe to run on every boot.

## 3. Module dependency rules

```text
static/ (browser)  →  HTTP only
routes/            →  may import services/, src/, core/        (never another route's router)
services/          →  may import src/, core/, other services/  (NEVER routes/)
src/               →  may import core/, src/                   (routes import allowed only lazily*)
core/              →  stdlib + SQLAlchemy only (bottom layer)
```

- Verified: `grep -rn "from routes" services/ core/` → zero hits. The single upward edge in `src/` is `src/tool_implementations.py:3916,3964` doing a **lazy, function-local** `from routes import contacts_routes as cc` (agent tool reusing contact lookups) — keep it local-scope if reproduced, never module-top.
- **`services/paperclip/events.py` exists specifically to break a routes↔collector import cycle.** Its docstring: *"Framework-free so both the API routes (routes/paperclip_routes.py) and the live-events collector (services/paperclip/collector.py) can publish into the same hub without import cycles."* `app.py` constructs the `EventHub` once and hands `hub.publish` to the collector and the hub itself to `setup_paperclip_routes`.
- `app.py` is the only composition root: it builds managers (`src/app_initializer.initialize_managers`), wires cross-cutting singletons, and registers every router. Routes never construct managers themselves.
- Local imports inside route handlers (e.g. `from core.middleware import require_admin  # local import: keep module light` in `routes/paperclip_routes.py:205`) are the accepted pattern for keeping module import time low.

## 4. Feature → file map

| Feature | Route(s) | Service / engine | Frontend |
|---|---|---|---|
| Chat + streaming | `routes/chat_routes.py`, `chat_helpers.py` | `src/chat_processor.py`, `src/chat_handler.py`, `src/llm_core.py`, `src/context_budget.py` | `static/js/chat.js`, `chatStream.js`, `chatRenderer.js` |
| Agent + tools | (chat routes, agent mode) | `src/agent_loop.py`, `agent_tools.py`, `tool_execution.py`, `tool_implementations.py`, `tool_index.py` | `static/js/chat.js` |
| Sessions/history | `session_routes.py`, `history_routes.py`, `cleanup_routes.py` | `core/session_manager.py`, `src/session_actions.py` | `sessions.js`, `search-chat.js` |
| Local GGUF models | `localmodels_routes.py`, `lmproxy_routes.py` | `services/localmodels/*` | `models.js`, `modelPicker.js` |
| Cookbook | `cookbook_routes.py`, `hwfit_routes.py`, `cookbook_helpers.py` | `services/hwfit/*` | `cookbook*.js` |
| Memory & skills | `memory_routes.py`, `skills_routes.py` | `services/memory/*`, `src/memory.py`, `memory_vector.py` | `memory.js`, `skills.js` |
| RAG / personal docs | `personal_routes.py`, `embedding_routes.py` | `src/rag_*.py`, `chroma_client.py`, `embeddings.py`, `personal_docs.py` | `rag.js` |
| Deep research | `research_routes.py` | `src/deep_research.py`, `research_handler.py`, `visual_report.py`, `services/research/crawl4ai_adapter.py` | `static/js/research/`, `researchSynapse.js` |
| Web search | `search_routes.py` | `services/search/*` | `search.js` |
| Documents | `document_routes.py`, `document_helpers.py` | `src/document_actions.py`, `document_processor.py`, `services/docs/service.py` | `document.js`, `documentLibrary.js` |
| Email | `email_routes.py`, `email_pollers.py`, `email_helpers.py` | `src/email_thread_parser.py`, `mcp_servers/email_server.py` | `emailInbox.js`, `emailLibrary.js` |
| Calendar/contacts | `calendar_routes.py`, `contacts_routes.py` | `src/caldav_sync.py`, `caldav_writeback.py` | `calendar.js`, `static/js/calendar/` |
| Notes & tasks | `note_routes.py`, `task_routes.py`, `assistant_routes.py` | `src/task_scheduler.py`, `event_bus.py`, `builtin_actions.py` | `notes.js`, `tasks.js`, `assistant.js` |
| Paperclip | `paperclip_routes.py`, `integration_routes.py` | `services/paperclip/*`, `services/integrations/agent_workbench.py` | `paperclip.js` |
| Embedded browser | `browser_routes.py` | `services/browser/embedded_browser.py` | `browserPanel.js` |
| Gallery/editor | `gallery_routes.py`, `editor_draft_routes.py`, `signature_routes.py` | `src/pdf_forms.py`, `pdf_form_doc.py` | `gallery.js`, `galleryEditor.js`, `static/js/editor/` |
| Compare | `compare_routes.py` | — | `static/js/compare/` |
| TTS/STT | `tts_routes.py`, `stt_routes.py` | `services/tts/tts_service.py`, `services/stt/stt_service.py` | `tts-ai.js`, `voiceRecorder.js` |
| MCP | `mcp_routes.py` | `src/mcp_manager.py`, `builtin_mcp.py`, `mcp_servers/*` | `settings.js` |
| Auth/admin | `auth_routes.py`, `api_token_routes.py`, `webhook_routes.py`, `admin_wipe_routes.py`, `vault_routes.py`, `backup_routes.py`, `prefs_routes.py`, `system_status_routes.py`, `diagnostics_routes.py` | `core/auth.py`, `src/webhook_manager.py`, `secret_storage.py` | `admin.js`, `settings.js`, `systemStatusCard.js`, `systemStatusActions.js` |
| Shell exec | `shell_routes.py` | `services/shell/service.py`, `core/platform_compat.py` | `codeRunner.js` |
| Uploads | `upload_routes.py` | `src/upload_handler.py` | `fileHandler.js` |

## 5. Test suite organization (`tests/`, ~250 files)

All tests live flat in `tests/` — no per-domain subfolders. Subject naming mirrors the module under test (`test_localmodels_scanner.py`, `test_paperclip_collector.py`, `test_llm_core_streaming.py`) or the regression being pinned (`test_security_regressions.py`, `test_webhook_ssrf_resilience.py`, `test_session_ghost_delete.py`). Owner-scoping invariants get dedicated `*_owner_scope.py` files per feature (calendar, email, cleanup, research, sessions, uploads, documents, history).

The key trick is conditional stubbing so the suite runs without heavyweight deps (and without a live DB):

```python
# tests/conftest.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub optional dependencies only when they are not installed. Do not replace
# real FastAPI/Starlette/Pydantic modules: route tests import their subpackages.
for mod_name in [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.types", "sqlalchemy.ext", ...,
    "bcrypt", "pyotp", "httpx", "fastapi", "fastapi.responses", "fastapi.routing",
    "starlette", "starlette.responses", "starlette.middleware", "starlette.middleware.base",
    "pydantic",
]:
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()

if "src.database" not in sys.modules:          # always stub the DB session layer
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["src.database"] = _db
```

`tests/real_modules.py` provides `import_real_module(name)` to drop a collection-time stub and import the real on-disk module when a test needs the genuine article. Node tests use the built-in runner, no framework:

```bash
node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs \
            tests/test_system_status_actions.mjs tests/test_theme_presets.mjs
```

Full gate: `bash scripts/check.sh` = `compileall` (app.py, companion, core, routes, services, src, two scripts) + `pytest -q` + `npm run test:js`.

## 6. scripts/ CLI suite

Every domain has a same-named extensionless CLI sharing helpers from `scripts/_lib/` (tab completion in `scripts/_completion/`), dispatched by `scripts/apollo`: `apollo-backup`, `apollo-calendar`, `apollo-contacts`, `apollo-cookbook`, `apollo-docs`, `apollo-gallery`, `apollo-integrations` (agent-workbench status), `apollo-logs`, `apollo-mail` (incl. `poll-scheduled`/`poll-summary` for cron-driven deployments), `apollo-mcp`, `apollo-memory`, `apollo-notes`, `apollo-personal`, `apollo-preset`, `apollo-ralph` (init/status/next/check/record/run-once loop), `apollo-research` (incl. `crawl <url> --owner <user>`), `apollo-sessions`, `apollo-signature`, `apollo-skills`, `apollo-tasks`, `apollo-theme`, `apollo-webhook`. Standalone utilities: `check-paperclip-browser` (browser-use Floor QA), `setup-browser-use-env`, `check-docker-gpu.sh`, `check-docker-amd-gpu.sh`, `diffusion_server.py`, `hf_download.py`, `update_database.py`, `index_documents.py`, `migrate_faiss_to_chroma.py`, `claim_ownerless.py`, `encode_previews.sh`, `fix_paths.py`, `add_hwfit_models.py`.

## 7. Frontend module inventory (`static/js/`, 79 entries)

Documented in-tree at `static/js/MODULE_SUMMARY.md`. Grouped by concern (all flat unless a subdirectory is named):

- **Bootstrap/shell**: `init.js`, `ui.js`, `sidebar-layout.js`, `section-management.js`, `tileManager.js`, `modalManager.js`, `modalSnap.js`, `escMenuStack.js`, `windowDrag.js`, `windowResize.js`, `keyboard-shortcuts.js`, `keybind` support in `util/`, `platform.js`, `storage.js`, `a11y.js`, `spinner.js`, `dragSort.js`.
- **Chat**: `chat.js`, `chatStream.js` (SSE/stream consumption), `chatRenderer.js`, `markdown.js` + `markdown/`, `codeRunner.js`, `emojiPicker.js`, `slashCommands.js`, `slashAutocomplete.js`, `group.js` (group chat), `censor.js`, `search-chat.js`.
- **Models/providers**: `models.js`, `modelPicker.js`, `modelSort.js`, `providers.js`, `presets.js`.
- **Cookbook**: `cookbook.js`, `cookbookDownload.js`, `cookbookServe.js`, `cookbookRunning.js`, `cookbook-hwfit.js`, `cookbook-diagnosis.js`.
- **Features**: `sessions.js`, `memory.js`, `skills.js`, `notes.js`, `tasks.js`, `assistant.js`, `calendar.js` + `calendar/`, `document.js`, `documentLibrary.js`, `emailInbox.js`, `emailLibrary.js` + `emailLibrary/`, `gallery.js`, `galleryEditor.js` + `editor/{build,filters,fx,tools}/`, `signature.js`, `compare/`, `research/` + `researchSynapse.js`, `rag.js`, `paperclip.js` (Floor renderer), `browserPanel.js`, `fileHandler.js`, `voiceRecorder.js`, `tts-ai.js`.
- **Admin/system**: `admin.js`, `settings.js`, `systemStatusCard.js`, `systemStatusActions.js`, `search.js`.
- **Theming**: `theme.js`, `colorPicker.js`, `color/`, `langIcons.js`, `tourHints.js`, `tourAutoplay.js`.

`static/app.js` is the entry module loaded by `index.html`; `static/sw.js` + `static/manifest.json` provide the installable PWA; `static/login.html` is the standalone auth page; `static/backgrounds.html` is a no-auth background-effects sandbox (served at `/backgrounds`).

## 8. Where each cross-cutting concern lives

- **Identity/ownership**: `request.state.current_user` stamped by AuthMiddleware (`app.py`); helpers in `src/auth_helpers.py`; every user-data table carries an indexed nullable `owner` column; `_migrate_assign_legacy_owner()` (`core/database.py`) sweeps NULLs to the admin at boot + hourly.
- **Secrets at rest**: `src/secret_storage.py` (Fernet, key `data/.app_key`) via the `EncryptedText` column type; Paperclip secrets under `~/.apollo/` (§8 of doc 09).
- **Settings**: only through `src/settings.py` (`get_setting`, `load_settings`, `save_settings`); never read `data/settings.json` directly.
- **Outbound URL safety (SSRF)**: `src/url_safety.py`, applied by webhook/embedding/crawl/CalDAV paths.
- **Platform differences**: `core/platform_compat.py` (Windows `ComSpec`, Git Bash discovery, path bases).
- **Status/observability**: `routes/system_status_routes.py` + `services/system_status.py`; `/api/health`, `/api/ready` (`src/readiness.py`), `/api/runtime`; ops runbook `docs/OPERATIONS.md`.

## 9. Router registration order (`app.py`)

The exact label/factory sequence as registered — a rebuild should preserve this order since later routers depend on managers wired earlier:

```text
 1. Auth (setup_auth_routes, auth_manager)        2. Uploads (returns router + cleanup coroutine)
 3. batch 1 via register_router_specs:
    Emoji · Sessions · Admin wipe · Memory · Skills · Chat · Research · History ·
    Search · Presets · Diagnostics · Cleanup · Personal docs · Embedding · Models · TTS
 4. STT                       5. Documents          6. Signatures        7. Gallery
 8. Editor drafts             9. Tasks (TaskScheduler created here, wired to event_bus)
10. Assistants               11. Calendar          12. Shell            13. Cookbook
14. Hardware fit             15. Local models      16. Compare          17. Preferences
18. Backup                   19. Fonts             20. MCP (McpManager → set_mcp_manager)
21. batch 2: Webhooks · API tokens · Notes · Email · Vault · Contacts · Companion
22. Sidecar proxy (Paperclip cfg + EventHub + collector + agent tokens)
23. Integration status       24. System status     25. Browser
26. Local model proxy (/lmproxy/v1)
   + bare @app.get routes in app.py: /, /notes, /calendar, /cookbook, /email, /memory,
     /gallery, /tasks, /library, /backgrounds, /login, /api/version, /api/health,
     /api/ready, /api/runtime, /api/generated-image/{filename}
```

A rebuild should reproduce this exact skeleton first (empty factories returning bare routers), then fill features in the order: core/ → services/app_startup.py → auth + sessions + chat → everything else, keeping the layering rules in §3 intact.
