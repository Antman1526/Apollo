# 15 — File Structure & Code Organization

Tree verified with `ls`/`wc -l` against the repository on disk. Depth is 2-3
levels for most top-level directories, deeper for `routes/`, `services/`,
`src/`, `core/`, and `static/js/` per the task brief.

## 1. Repository Root

```
Apollo/
├── app.py                     # 59,669 bytes — slim orchestrator; wires middleware, auth,
│                               #   ~50 routers, lifecycle hooks. See doc 01 §5 for full startup order.
├── setup.py                   # First-run setup: dirs, .env, DB, admin user. See doc 02 §8.
├── pyproject.toml             # pytest config (testpaths=tests, asyncio_mode=auto, "e2e" marker);
│                               #   ruff config (target-version py312, select=[E9,F811,F821,E722]).
├── requirements.in / .txt     # Direct deps / pip-compile-locked full dep tree (160 pkgs).
├── requirements-dev.in / .txt # + pytest, pytest-asyncio, httpx2, pip-tools, pip-audit, ruff.
├── requirements-optional.txt  # faster-whisper, piper-tts, duckduckgo-search, PyMuPDF, markitdown.
├── package.json               # Node side: node --test runner for tests/*.mjs; @anthropic-ai/sdk dep.
├── docker-compose.yml, Dockerfile, .dockerignore
├── docker/                    # entrypoint.sh, gpu.amd.yml, gpu.nvidia.yml (compose overlays)
├── start-macos.sh             # Homebrew-based one-command native macOS quick start (port 7860).
├── build-macos-app.sh         # PyInstaller-free "launcher" .app/.dmg build (drives repo's venv).
├── build-macos-bundle.sh      # Self-contained PyInstaller .app/.dmg build (no repo/venv needed to run).
├── launch-windows.ps1         # Native Windows one-command launcher (venv + deps + setup + winget auto-install).
├── update_windows.bat         # Windows update helper.
├── WINDOWS-SETUP.md           # Windows local-model (llama.cpp) setup guide.
├── apollo-ui.service          # systemd unit for a Linux service install.
├── install-service.sh         # Installs the systemd unit.
├── .env.example                # 8,733 bytes — every recognized env var, documented inline.
│
├── routes/                    # 58 files — HTTP layer. §2 below.
├── services/                  # 29 top-level entries — business/service layer. §3 below.
├── src/                       # 88 files — agent core, LLM/tool machinery, cross-cutting logic. §4 below.
├── core/                      # 10 files — DB models, auth primitives, low-level constants/middleware. §5 below.
├── companion/                 # Device-pairing companion API: __init__.py, pairing.py, routes.py, README.md.
├── mcp_servers/                # Bundled MCP servers Apollo can spawn for itself: _common.py,
│                               #   memory_server.py, rag_server.py, image_gen_server.py, email_server.py.
├── static/                    # 81+ JS modules, CSS, HTML — the whole frontend. §6 below.
├── scripts/                   # 55 files — dev/ops tooling (module-size check, data migration, HF downloads, etc.).
├── security/                  # dependency-audit-exceptions.json.
├── config/                    # searxng/ (bundled SearXNG config for the optional sidecar).
├── packaging/                 # apollo.spec, apollo_boot.py, apollo-icon.svg, apollo.icns, make-icon.sh — PyInstaller bundle build.
├── licenses/                  # Third-party license texts (bundled-app compliance).
├── tests/                     # 327 Python test files + tests/e2e/ + *.mjs JS tests + conftest.py.
└── docs/                      # OPERATIONS.md, PRODUCTION_READINESS.md, adr/, recreate/, recreation/, superpowers/, ai-review/.
```

## 2. `routes/` — HTTP Layer (58 files, flat directory)

**Naming convention**: `<domain>_routes.py`, each exporting exactly one
factory function `setup_<domain>_routes(...) -> APIRouter`. A handful of
files are *not* routers — helper/support modules colocated by domain
because they're only used by that domain's router:

| File | Kind |
|---|---|
| `chat_helpers.py`, `cookbook_helpers.py`, `cookbook_runner_files.py`, `document_helpers.py`, `email_helpers.py`, `email_pollers.py`, `gallery_helpers.py` | Support modules, not routers |
| everything else matching `*_routes.py` (51 files) | Router factory modules |

Full router inventory (grouped roughly by domain, all present in `routes/`):
`activity_routes, admin_wipe_routes, api_token_routes, assistant_routes,
auth_routes, backup_routes, browser_routes, calendar_routes, chat_routes,
cleanup_routes, compare_routes, contacts_routes, cookbook_routes,
diagnostics_routes, document_routes, editor_draft_routes, embedding_routes,
emoji_routes, font_routes, gallery_routes, history_routes, hub_routes,
hwfit_routes, integration_routes, lmproxy_routes, localmodels_routes,
mcp_routes, memory_routes, model_routes, note_routes, paperclip_routes,
personal_routes, prefs_routes, preset_routes, research_routes,
search_routes, session_routes, shell_routes, signature_routes,
skill_pack_routes, skills_routes, stt_routes, system_status_routes,
task_routes, tts_routes, upload_routes, vault_routes, webhook_routes`.

`routes/__init__.py` is empty (no re-exports) — every router is imported
individually where it's registered, always in `app.py` (never
cross-imported router-to-router).

## 3. `services/` — Service Layer (29 top-level entries)

`services/__init__.py` docstring: *"Service layer — plug-in capabilities
for the chat core. Each service: Does one thing well / Exposes a clean
async interface / Can run in-process or as a standalone HTTP service."* It
re-exports a curated subset as top-level classes:
```python
from .search import SearchService, SearchResult, SearchResponse
from .docs import DocsService, DocChunk, IndexResult
from .research import ResearchService, ResearchResult, ResearchSource
from .memory import MemoryService, Memory, MemorySearchResult
from .shell import ShellService, ShellResult
```

Flat files: `activity_ledger.py, app_startup.py (RouterSpec pattern — see
doc 01 §4.1), config_scanner.py, connector_catalog.py, model_hub.py,
model_router.py, persona_importer.py, python_kernel.py,
reference_library.py, system_status.py`.

Package subdirectories:

| Package | Contents |
|---|---|
| `browser/` | `embedded_browser.py` — Playwright-driven agent browser tool. |
| `docs/` | `service.py` — `DocsService`. |
| `faces/` | (empty besides `__init__.py` at time of writing). |
| `hwfit/` | `fit.py, hardware.py, image_models.py, models.py, profiles.py, data/` — Cookbook "What Fits?" hardware-sizing feature. |
| `integrations/` | `agent_workbench.py`. |
| `localmodels/` | `config.py, gguf_meta.py, lifecycle.py, registry.py, scanner.py, server_manager.py` — the `llama-server` subprocess manager (doc 01 §6.2). |
| `memory/` | `brain.py, chat_import.py, distiller.py, graph.py, memory.py, memory_extractor.py, memory_vector.py, service.py, skill_extractor.py, skill_format.py, skills.py` — memory pipeline + skills library. |
| `paperclip/` | `agent_tokens.py, browser_use_verifier.py, collector.py, config.py, events.py, node_bootstrap.py, proxy.py, runtime.py` — the Paperclip sidecar integration (doc 01 §6.3). |
| `research/` | `crawl4ai_adapter.py, research_handler.py, service.py`. |
| `review/` | `reviewer.py`. |
| `search/` | `analytics.py, cache.py, content.py, core.py, providers.py, query.py, ranking.py, service.py`. |
| `searxng/` | `config.py, runtime.py` — optional SearXNG sidecar (doc 01 §6.4). |
| `shell/` | `service.py` — `ShellService`. |
| `skills/` | `pack_installer.py`. |
| `stt/` | `stt_service.py`. |
| `tts/` | `tts_service.py`. |
| `youtube/` | `youtube_handler.py`. |

Note: `services/search/` (8 files) mirrors `src/search/` (8 files, same
filenames) — **not a duplication bug to "fix"**; `src/search/` re-exports
or wraps the service-layer implementation for agent-tool consumption (the
codebase deliberately keeps the search *service* in `services/` and a
thinner *tool-facing* layer in `src/`). UNCERTAIN: the exact re-export
relationship between the two `search/` trees was not traced line-by-line in
this pass — treat `services/search/` as the canonical implementation.

## 4. `src/` — Agent Core & Cross-Cutting Logic (88 files + 2 subpackages)

The largest and most heterogeneous tier. Roughly four categories of files:

**a) App wiring**: `app_helpers.py, app_initializer.py (see doc 01 §5 step 10),
constants.py (BASE_DIR/STATIC_DIR/DATA_DIR and all path constants — core/constants.py
just re-exports this via `from src.constants import *`), config.py, runtime_paths.py,
data_migration.py, observability.py, exceptions.py, request_models.py`.

**b) Agent/tool machinery** (see doc 01 §4.3 for the layering):
`agent_loop.py (2331 lines — the conversation loop + TOOL_SECTIONS +
AGENT_SYSTEM_PROMPT), agent_runs.py, agent_tools.py (139 lines — facade +
TOOL_TAGS), tool_schemas.py (1284 lines — FUNCTION_TOOL_SCHEMAS),
tool_index.py (499 lines — RAG tool selection, BUILTIN_TOOL_DESCRIPTIONS),
tool_parsing.py, tool_execution.py, tool_implementations.py,
tool_security.py, action_intents.py, builtin_actions.py, builtin_mcp.py,
mcp_manager.py, ai_interaction.py, ralph_loop.py, teacher_escalation.py`.

**c) Chat/LLM/RAG/memory pipeline**: `chat_handler.py, chat_helpers.py,
chat_processor.py, llm_core.py, model_context.py, model_discovery.py,
endpoint_resolver.py, context_budget.py, context_compactor.py,
memory.py, memory_vector.py, embeddings.py, chroma_client.py,
rag_manager.py, rag_singleton.py, rag_vector.py, personal_docs.py,
document_processor.py, document_actions.py, deep_research.py,
research_handler.py, research_utils.py, goal_based_extractor.py,
topic_analyzer.py, web_decider.py`.

**d) Domain/infra support**: `auth_helpers.py, api_key_manager.py,
secret_storage.py, secure_temp.py, prompt_security.py, rate_limiter.py,
readiness.py, settings.py, settings_scrub.py, session_actions.py,
task_scheduler.py, task_endpoint.py, event_bus.py, webhook_manager.py,
upload_handler.py, url_safety.py, subproc_env.py, caldav_sync.py,
caldav_writeback.py, email_thread_parser.py, youtube_handler.py,
markitdown_runtime.py, pdf_runtime.py, pdf_forms.py, pdf_form_doc.py,
visual_report.py, text_helpers.py, assistant_log.py, bg_jobs.py,
bg_monitor.py, database.py (37-line re-export shim only: `from core.database
import *` plus an explicit re-export list — `core/database.py` (1948 lines)
is the canonical SQLAlchemy module; `src/database.py` exists purely so
`from src.database import X` keeps working for callers in `src/`, per its
own header comment)`.

Subpackages:

| Package | Contents |
|---|---|
| `src/tools/` | `_common.py, _state.py, admin.py, chats.py, cookbook.py, documents.py, media.py, notes_calendar.py, research_contacts.py, skills_tasks.py, vault.py, web.py` — the actual `do_*` tool implementations, grouped by domain, called from `src/tool_implementations.py`/`src/tool_execution.py`. |
| `src/search/` | `analytics.py, cache.py, content.py, core.py, providers.py, query.py, ranking.py` — mirrors `services/search/` (§3 note above). |

## 5. `core/` — Low-Level Primitives (10 files, 3,664 lines total)

Deliberately small and stable — the layer everything else depends on but
that depends on almost nothing itself:

| File | Role |
|---|---|
| `database.py` | SQLAlchemy `Base`, `engine`, `SessionLocal`, and every ORM model (`ApiToken`, `Session`, `ChatMessage`, `ScheduledTask`, `GalleryImage`, ...). |
| `auth.py` | `AuthManager` — session tokens, TOTP (`pyotp`), password hashing (`bcrypt`). |
| `middleware.py` | `SecurityHeadersMiddleware`, `INTERNAL_TOOL_HEADER`/`INTERNAL_TOOL_TOKEN` constants used by `app.py`'s internal-tool bypass. |
| `session_manager.py` | `SessionManager` — chat session persistence, constructed in `src/app_initializer.py`. |
| `models.py` | Shared model helpers; `set_session_manager()` hook used to enable `Session.add_message()` persistence. |
| `exceptions.py` | `SessionNotFoundError, InvalidFileUploadError, LLMServiceError, WebSearchError` — the four exception types `app.py` registers handlers for. |
| `constants.py` | 8-line compatibility shim: `from src.constants import *` — "retaining this module avoids two divergent copies of the same runtime configuration" (its own docstring). |
| `atomic_io.py` | Atomic file write helpers (avoid partial writes on crash). |
| `platform_compat.py` | OS-specific compatibility shims. |
| `__init__.py` | Empty/marker. |

## 6. Naming Conventions

| Pattern | Meaning | Example |
|---|---|---|
| `routes/<domain>_routes.py` exporting `setup_<domain>_routes()` | One router factory per domain | `routes/gallery_routes.py` → `setup_gallery_routes` |
| `routes/<domain>_helpers.py` | Non-router support code for one router | `routes/email_helpers.py` |
| `services/<domain>/service.py` or `services/<domain>.py` | Service-layer entry point | `services/shell/service.py` → `ShellService` |
| `src/tool_*.py` | Tool subsystem infrastructure (schema/index/execution/parsing) — never domain-specific | `src/tool_schemas.py` |
| `src/tools/<domain>.py` | Domain-grouped tool *implementations* | `src/tools/cookbook.py` |
| `core/*.py` | Framework-adjacent primitives with no upward imports | `core/database.py` |
| `static/js/<feature>.js` + `static/js/<feature>/*.js` | A large feature gets one entry-point module plus a same-named subdirectory for its internal split-out pieces | `static/js/document.js` + `static/js/document/{diff,export,state,suggestions,versionHistory}.js` |
| `test_<module_or_feature>.py` | pytest file mirrors the thing under test | `tests/test_agent_loop.py` |

## 7. Module Dependency Rules

**Primary direction: `routes/` → `services/` → `core/`**, with `src/`
consumed by both `routes/` and `services/` for agent/LLM/cross-cutting
logic. Concretely, verified against `app.py`'s own import list and spot
checks:

- `routes/*.py` imports from `services/*`, `src/*`, and `core/*` — never
  from another `routes/*.py` file (each router factory is self-contained;
  `app.py` is the only place that sees multiple routers at once).
- `services/*.py` imports from `core/*` and `src/*`, and occasionally from
  sibling `services/*` packages (e.g. `services/paperclip/runtime.py`
  reading config from `services/paperclip/config.py`), but never imports
  from `routes/`.
- `core/*.py` is the dependency floor — `core/database.py`,
  `core/auth.py`, `core/middleware.py`, `core/session_manager.py` are
  imported by `src/` and `services/` but themselves only import from `src/constants.py`/`src/runtime_paths.py`
  (as seen in `core/constants.py`'s explicit re-export) and standard
  library / third-party packages — not from `routes/` or `services/`.
- `src/*.py` is mixed: the "app wiring" and "chat/LLM/RAG" files import
  from `core/*` freely; the *agent tool* files (`tool_schemas.py`,
  `agent_loop.py`, `tool_index.py`, `agent_tools.py`) import from each
  other in a specific layered order (§8 below) but not from `routes/`.

### 7.1 `src/` agent-stack internal layering

```
src/tool_parsing.py  ──┐
                        ├──> src/agent_tools.py (facade: TOOL_TAGS, ToolBlock, MCP manager singleton)
src/tool_schemas.py  ──┤        │  (imports ToolBlock + TOOL_TAGS FROM agent_tools.py — see
                        │        │   tool_schemas.py:15 "from src.agent_tools import ToolBlock, TOOL_TAGS")
src/tool_execution.py──┤        ▼
                        │   src/agent_loop.py (TOOL_SECTIONS, AGENT_SYSTEM_PROMPT, the round-loop)
src/tool_implementations.py     │
                        │        ▼
                        └──> src/tool_index.py (RAG selection over BUILTIN_TOOL_DESCRIPTIONS,
                                                 reads TOOL_SECTIONS-adjacent metadata to embed)
```
`agent_tools.py`'s own module docstring makes the facade relationship
explicit: *"Re-exports tool parsing, schemas, execution, and
implementations for backward compatibility. All importers continue to work
unchanged."*

## 8. The Four Tool-Registration Registries (mandatory checklist for adding an agent tool)

Adding a new agent-invokable tool requires updating **all four** of these,
independently — there is no single source of truth the others derive from.
This is stated explicitly in the code itself, at `src/agent_tools.py:58-61`
(a comment inside the `TOOL_TAGS` set literal):

> *"Reference Library + persistent Python. TOOL_TAGS is the FOURTH place a
> tool must be registered (schemas, TOOL_SECTIONS, tool_index descriptions,
> and here) — the fenced-block regex is built from this set, so an unlisted
> tag can never parse."*

| # | File | Registry | What it controls | Consequence if omitted |
|---|---|---|---|---|
| 1 | `src/tool_schemas.py` | `FUNCTION_TOOL_SCHEMAS` (list, starts at line 23) | OpenAI-compatible function-calling JSON schema — name, description, JSON-schema parameters — for models using native tool/function calling. | Native function-calling models can never see or invoke the tool. |
| 2 | `src/agent_loop.py` | `TOOL_SECTIONS` (dict, starts at line 174) | The fenced-code-block prompt text shown to models that use the ```` ```tool_name ```` text-block calling convention (not native function calling) — keyed by tool name, e.g. `TOOL_SECTIONS["bash"]`, `TOOL_SECTIONS["python"]`. Also drives `AGENT_SYSTEM_PROMPT` assembly (`_assemble_prompt(set(TOOL_SECTIONS.keys()))`, line 461) and the compact/management-tool split (lines 1018-1026). | Text-block-calling models never learn the tool exists or how to invoke it. |
| 3 | `src/tool_index.py` | `BUILTIN_TOOL_DESCRIPTIONS` (`Dict[str, str]`, starts at line 63) | Richer, retrieval-oriented description embedded into the ChromaDB `apollo_tool_index` collection; used by RAG-based tool *selection* (top-K per message) instead of injecting every tool description into every prompt. | The tool is never retrieved into context for a relevant user message (unless it's also in `ALWAYS_AVAILABLE`/`ASSISTANT_ALWAYS_AVAILABLE`, lines 26-56 of the same file). |
| 4 | `src/agent_tools.py` | `TOOL_TAGS` (set, starts at line 29) | The set of fenced-block tags the parser recognizes at all — "the fenced-block regex is built from this set, so an unlisted tag can never parse" (verbatim source comment). | A ```` ```tool_name ```` block for an unregistered tag is never recognized as a tool call — it's just inert text in the response. |

Practical implication: a new tool named e.g. `manage_widgets` needs an
entry in `FUNCTION_TOOL_SCHEMAS` (schema), `TOOL_SECTIONS["manage_widgets"]`
(prompt text), `BUILTIN_TOOL_DESCRIPTIONS["manage_widgets"]` (retrieval
description), and `"manage_widgets"` added to the `TOOL_TAGS` set literal —
plus, separately, its actual `do_manage_widgets(...)` implementation
(typically in `src/tools/<domain>.py`) wired into
`src/tool_implementations.py`/`src/tool_execution.py`'s dispatch, and
likely membership in `ALWAYS_AVAILABLE` or `ASSISTANT_ALWAYS_AVAILABLE` in
`src/tool_index.py` if it should never depend on RAG retrieval.

## 9. Module-Size Ratchet (`scripts/check_module_sizes.py`, 56 lines, read in full)

Purpose (docstring): *"Keep JavaScript feature modules small and ratchet
existing entry points down."* Applies only to `static/js/**/*.js`.

**Mechanism**: two limit types.

1. **Grandfathered baselines** (`BASELINES` dict) — files that already
   existed above the general cap when the ratchet was introduced get an
   exact per-file line-count ceiling equal to their measured size at
   baseline time. The check fails if the file grows **past** its baseline;
   shrinking it is expected and does not update the baseline automatically
   (a later commit must lower the constant by hand). Exact baselines as of
   this pass:

   | File | Baseline (lines) | File | Baseline (lines) |
   |---|---|---|---|
   | `admin.js` | 2092 | `gallery.js` | 2835 |
   | `calendar.js` | 3348 | `galleryEditor.js` | 3798 |
   | `chat.js` | 4584 | `modalManager.js` | 1550 |
   | `chatRenderer.js` | 2105 | `notes.js` | 5011 |
   | `cookbook-hwfit.js` | 1790 | `sessions.js` | 3135 |
   | `cookbook.js` | 1965 | `settings.js` | 5043 |
   | `cookbookRunning.js` | 3218 | `skills.js` | 2038 |
   | `cookbookServe.js` | 2086 | `slashCommands.js` | 5940 |
   | `document.js` | 9453 | `tasks.js` | 2709 |
   | `documentLibrary.js` | 3365 | `theme.js` | 2160 |
   | `emailLibrary.js` | 5217 | | |

2. **Hard cap for every other module**: `MAX_NEW_MODULE_LINES = 1500` —
   any `.js` file under `static/js/` *not* in `BASELINES` must stay at or
   under 1500 lines.

**Check logic** (`check_modules()`): walks `static_js.rglob("*.js")`
(recursive — covers the subdirectories too, e.g. `static/js/document/*.js`,
`static/js/editor/**/*.js`), compares each file's line count
(`len(path.read_text(encoding="utf-8").splitlines())`) against its baseline
if listed, else against the 1500-line hard cap. Prints
`module-size-check-failed` + a list of offending files (relative path,
actual count, exceeded limit) and returns exit code 1 on any violation;
otherwise prints `module-size-check-ok` and exits 0. Runnable standalone:
```bash
python scripts/check_module_sizes.py [--static-js PATH]
```
(default `--static-js` resolves to `<repo>/static/js` via
`Path(__file__).resolve().parents[1] / "static" / "js"`).

## 10. `static/js/` Organization

`static/` top level: `app.js, css/, fonts/, icon-192.png, icon-512.png,
index.html, js/, landing.html, lib/, login.html, manifest.json, style.css,
sw.js` (a service worker — PWA support).

`static/lib/` — vendored third-party JS pulled in as plain files (no npm
bundler): `docx.umd.min.js, highlight.min.js, html2pdf.bundle.min.js,
mammoth.browser.min.js, qrcode.min.js, xlsx.full.min.js`.

`static/css/` — one stylesheet per concern, no preprocessor:
`admin.css, agent-thread.css, base.css, calendar.css, chat-components.css,
controls.css, cookbook.css, doc-editor.css, email.css,
gallery-compare.css, layout-chat.css, layout-mobile.css,
layout-sidebar-sections.css, layout-sidebar.css, memory.css,
mobile-overrides.css, notes.css, overlays.css, paperclip-floor.css,
research.css, settings-tasks.css, theme-extras.css, variables.css`.

`static/js/` — **81 top-level `.js` files**, ES modules, no bundler/build
step (loaded directly via `<script type="module">`, served as-is by the
`_RevalidatingStatic` mount in `app.py` with forced `Cache-Control:
no-cache`). `static/js/MODULE_SUMMARY.md` documents the module map by hand.
`static/js/package.json` is a second, JS-tooling-scoped `package.json`
(separate from the repo-root one) — UNCERTAIN of its exact scope/purpose
beyond namespacing; not read in full this pass.

**Large-feature split pattern**: a feature that outgrows one file gets a
same-named subdirectory holding its internal pieces, while the top-level
`<feature>.js` remains the entry point (and the thing measured/ratcheted
by `check_module_sizes.py`, §9):

| Entry point | Subdirectory | Split-out pieces |
|---|---|---|
| `document.js` (9453-line baseline — the single largest module) | `document/` | `diff.js, export.js, state.js, suggestions.js, versionHistory.js` |
| `emailLibrary.js` | `emailLibrary/` | `attachments.js, readerWindows.js, replyRecipients.js, signatureFold.js, state.js, utils.js` |
| `compare.js` (no top-level file — fully split) | `compare/` | `icons.js, index.js, models.js, panes.js, probe.js, scoreboard.js, selector.js, state.js, stream.js, vote.js` |
| `notes.js` | `notes/` | `drafts.js` |
| `calendar.js` | `calendar/` | `utils.js` |
| `research*.js` (`researchSynapse.js` etc.) | `research/` | `jobs.js, panel.js` |
| `settings.js` | `settings/` | `models.js` |
| `chat.js` | `chat/` | `requestLifecycle.js` |
| `markdown.js` | `markdown/` | `tableRow.js` |
| `color`-related modules | `color/` | `hex.js` |
| the image editor (no single `editor.js` entry — the largest sub-area) | `editor/` | `ai-inpaint.js, ai-models.js, ai-rembg.js, ai-tool-runner.js, ai-tools-misc.js, canvas-coords.js, canvas-events.js, canvas-transforms.js, checkerboard.js, clipboard-and-drop.js, composite-helpers.js, harmonize-masks.js, history-panel.js, keyboard-shortcuts.js, layer-helpers.js, layer-panel.js, mask-utils.js, shortcuts-popover.js, slider-ux.js, snap.js, state.js, stroke-pipeline.js, stroke-tool-sliders.js, wire-*.js (7 files)`, plus further nested `build/`, `filters/`, `fx/`, `tools/` subdirectories |

Everything else in `static/js/` is a flat, single-purpose module: e.g.
`a11y.js, activity.js, admin.js, assistant.js, browserPanel.js, censor.js,
codeRunner.js, colorPicker.js, commandParse.js, cookbook-diagnosis.js,
cookbook-hwfit.js, cookbook.js, cookbookDownload.js, cookbookRunning.js,
cookbookServe.js, documentLibrary.js, dragSort.js, ecosystemHub.js,
emailInbox.js, emojiPicker.js, escMenuStack.js, fileHandler.js,
galleryEditor.js, graphLayout.js, group.js, init.js,
keyboard-shortcuts.js, langIcons.js, memory.js, memoryGraph.js,
modalManager.js, modalSnap.js, modelMeta.js, modelPicker.js,
modelSort.js, models.js, paperclip.js, platform.js, presets.js,
providers.js, rag.js, review.js, search-chat.js, search.js,
section-management.js, sessions.js, settings.js, settingsAiExtras.js,
sidebar-layout.js, signature.js, skills.js, slashAutocomplete.js,
slashCommands.js, spinner.js, storage.js, systemStatusActions.js,
systemStatusCard.js, theme.js`.

## 11. `tests/` Organization (327 files)

- `tests/test_<thing>.py` — one file per unit under test, pytest-based
  (`pyproject.toml`: `testpaths = ["tests"]`, `asyncio_mode = "auto"`).
- `tests/e2e/` — real-browser tests marked with the custom `e2e` pytest
  marker ("real-browser tests that start an isolated local application" —
  `pyproject.toml`), gated behind Chromium being installed
  (`scripts/run-e2e.sh`).
- `tests/*.mjs` — 16 files run via Node's built-in test runner
  (`node --test ...`, wired through `package.json`'s `test:js` script), for
  frontend logic that doesn't need a full browser (state machines, parsing,
  layout math) — e.g. `test_paperclip_floor_ui.mjs,
  test_system_status_card.mjs, test_voice_vad.mjs, test_graph_layout.mjs,
  test_module_boundaries.mjs, test_document_modules.mjs`.
- `tests/conftest.py`, `tests/real_modules.py`, `tests/css_source.py` —
  shared fixtures/helpers.
- `tests/bombadil-spec.ts` — spec file for the `@antithesishq/bombadil`
  devDependency (property-based/fuzz-style testing tool per its package
  name — UNCERTAIN of the exact scope of what it fuzzes without reading
  the file).

## 12. `docs/` Organization (top-level, relevant to this doc set)

- `docs/recreate/` — this document set (00 through 15, technology audit +
  14 numbered recreation docs).
- `docs/recreation/` — a **separate**, independently-maintained doc set
  covering similar ground (project overview, env setup, DB schema, API
  specs, frontend, auth, business logic, integrations, config, testing,
  build/deploy, error handling, performance, file structure) plus its own
  `TECHNOLOGY-AUDIT.md` and a dated refresh note
  (`00-2026-07-19-current-state-refresh.md`). UNCERTAIN: the exact
  relationship between `docs/recreate/` and `docs/recreation/` (which is
  authoritative, whether one supersedes the other) was not established in
  this pass — both exist on disk simultaneously and were not diffed
  against each other.
- `docs/adr/` — architecture decision records (e.g.
  `2026-07-17-runtime-data-and-identity.md`).
- `docs/superpowers/` — `PAPERCLIP-HANDOFF.md`, `plans/`, `specs/` —
  internal planning documents, including at least one dated plan file
  referencing local-model web access work.
- `docs/OPERATIONS.md`, `docs/PRODUCTION_READINESS.md` — ops runbooks.
- `docs/paperclip-floor.md` + several `.gif`/`.webm`/`.png` — feature demo
  media referenced from `README.md`'s collapsible feature-showcase
  sections.
