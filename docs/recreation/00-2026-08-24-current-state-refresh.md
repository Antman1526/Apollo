# Current-state refresh — 2026-08-24

The numbered recreation documents (01–15) and the technology audit were
**regenerated from the live codebase at commit `a4d106e`** on 2026-08-24,
replacing the 2026-07-19 set (whose text remains available in git history, and
whose own state-refresh note is kept beside this one). Everything below is the
delta a reader of the old set needs.

## What changed since 2026-07-19

Merged via PRs #7 and #11–#20:

- **Windows is first-class.** Windows-aware GGUF scan-dir defaults,
  `llama-server.exe` auto-detect candidates, a `llama_server_path` setting +
  `APOLLO_LLAMA_SERVER` env var, `GET/PUT /api/local-models/binary`, and a
  "llama-server Binary" field in Settings → AI. `launch-windows.ps1` detects
  missing prerequisites (Python 3.11+ / Git for Windows / llama.cpp) and offers
  consent-gated winget installs (`Python.Python.3.12`, `Git.Git`, `llama.cpp`),
  refreshing the session PATH from the registry after each install.
  Distribution: `scripts/build-windows-zip.sh` (git-archive, tracked files
  only) published to the `windows-latest` GitHub release.
- **Windows CI un-broken (PR #7).** `UploadHandler.__init__` called
  `magic.Magic(mime=True)`; python-magic's libmagic DLL load is a native fault
  on Windows that `except Exception` cannot catch. Fixed by skipping the magic
  import entirely on `os.name == "nt"`. Windows CI green since (~3m30s).
- **Reference Library (PR #12).** Fourth knowledge store:
  `services/reference_library.py` + `ReferenceEntry` model +
  `/api/hub/reference/*` + the read-only `reference_search` agent tool.
  Four curated catalogs (~5,900 entries), fetched catalog-markdown-only through
  the SSRF-guarded `src.search.content._get_public_url` path.
- **Ecosystem integrations (PR #11).** Community skill-pack/MCP catalog,
  agency-agents persona importer, persistent per-chat `python_session` kernel
  tool, MCP/skill config security scanner.
- **Local-model agent reliability (PR #18).** Tool registration requires FOUR
  registries (`src/tool_schemas.py` FUNCTION_TOOL_SCHEMAS, `src/agent_loop.py`
  TOOL_SECTIONS, `src/tool_index.py` BUILTIN_TOOL_DESCRIPTIONS + keyword hints,
  `src/agent_tools.py` TOOL_TAGS); `reference_search`/`python_session` were
  added to all four. New `_normalize_function_eq()` in `src/tool_parsing.py`
  converts the Qwen/Llama-3 `<function=NAME><parameter=KEY>value</parameter>`
  dialect (including stray `</tool_call>`) into canonical invoke blocks.
- **Desktop-mode settings fix (PR #19).** `routes/auth_routes.py` gained
  `_require_admin_user(request)`: explicit `AUTH_ENABLED=false` bypass, else a
  strict cookie-validated admin check. Deliberately does NOT delegate to
  `core.middleware.require_admin` — that variant trusts
  `request.state.current_user` (loopback-populated) and would let
  unauthenticated loopback callers read `GET /api/auth/users`.
  Regression-locked by `tests/test_settings_desktop_mode.py`.
- **Agent flight recorder.** Activity ledger (tool executions + exit codes,
  "Agent History" panel), per-write undo, whole-session rollback bundles,
  `agent_autonomy` dial (auto/observe).
- **Memory portability.** Export/import packs, per-memory provenance endpoint,
  folder-based Memory Sync (`memory_pack_sync_dir`).
- **Fast Lane.** `mixture_routing_enabled` + light model role; chat-mode only;
  off by default.
- **Icon.** New sail mark (`packaging/apollo-icon.svg` → `apollo.icns` via
  `packaging/make-icon.sh`); `static/icon-512.png` regenerated to match.
- **datetime.utcnow() eliminated** repo-wide (PRs #13, #15).

## Operational facts as of 2026-08-24

- Test suite: **2,075 passed, 3 skipped** (pytest), 134 JS tests; all four CI
  jobs green (ubuntu/macos/windows pytest + Ubuntu browser journeys).
- llama.cpp: hybrid attention+SSM models (Qwen 3.5/3.6/3.8 — `ssm_*` tensors)
  require a RECENT llama-server; older builds fail with
  `missing tensor 'blk.64.ssm_conv1d.weight'`. See WINDOWS-SETUP.md §1.
- Doc 14 (Security Implementation) is regenerated with the set but remains
  **local-only by policy** (it enumerates residual weaknesses; the repo is
  public). It is gitignored, not absent.

## Known gaps carried forward (honest list)

- In-memory session map: no eviction; non-atomic hydration (documented in
  doc 13 and the AI-review pack).
- No agent filesystem sandbox: `bash`/`write_file` have full user reach;
  mitigations are the env scrub, the ledger (audit + undo), and the autonomy
  dial — not containment.
- "Confirm" autonomy tier not implemented (needs agent-loop pause/approve
  plumbing).
- Tool-call parsing is regex/dialect-based per model family — functional
  (see PR #18) but fragile by construction.
