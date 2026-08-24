# Apollo — Testing Strategy & Test Cases

Apollo's test suite spans three layers: Python `pytest` (the bulk of it,
unit + route-level integration), Node `node:test` (frontend ES-module logic,
run headless with no bundler/DOM framework), and Playwright browser journeys
(`tests/e2e/`, real Chromium against a real running server). All four
converge on one local gate, `scripts/check.sh`, and one CI workflow,
`.github/workflows/ci.yml`.

## 1. Layout and scale

```
tests/                 328 *.py files (flat — no subpackages except e2e/)
tests/e2e/              1 Python file (test_browser_smoke.py) + Playwright fixtures
tests/*.mjs             15 files exercised by `npm run test:js` (node:test)
tests/conftest.py       shared pytest bootstrap (43 lines)
tests/real_modules.py   helper for tests that need genuine (non-stubbed) imports
tests/css_source.py     helper for CSS-source-derived assertions
tests/bombadil-spec.ts  (see §7 — not a runtime test file)
```

Counted directly: `find tests -name "*.py" | wc -l` → **328**, matching the
task brief's "~327" almost exactly (off by one depending on whether
`conftest.py` itself is counted — it is here).

Collection count, measured in this environment (system Python 3.14, **no
project `venv/` present** — see §2 for why this matters):

```
$ python3 -m pytest --collect-only -q
...
2037 tests collected, 12 errors in 1.23s
```

`grep -c "^def test_" tests/*.py` totals **1352** top-level test functions;
the difference (2037 vs 1352) is `@pytest.mark.parametrize` expansion and
class-scoped tests. The 12 collection errors are an artifact of this
environment lacking a fully-installed `sqlalchemy` (see §2) — not a code
defect; they all point at the same root cause (`core.database` importing
`sqlalchemy.engine`, which resolves to a broken/partial `sqlalchemy`
install here) and would collect cleanly inside the project's own `venv`.
UNCERTAIN: the true clean-`venv` count is not verified in this pass — no
`venv/` exists in this worktree. Treat "2037 collected, 12 errored" as the
accurate figure for *this* sandbox and "~327-328 files, ~2000+ tests" as the
safe approximate claim otherwise.

## 2. `tests/conftest.py` — shared bootstrap

```python
# tests/conftest.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _has_module(mod_name: str) -> bool:
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ImportError, ValueError):
        return False

# Stub optional dependencies only when they are not installed. Do not replace
# real FastAPI/Starlette/Pydantic modules: route tests import their subpackages.
for mod_name in [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.types", "sqlalchemy.ext",
    "sqlalchemy.ext.declarative", "sqlalchemy.ext.hybrid", "sqlalchemy.sql",
    "sqlalchemy.sql.expression", "sqlalchemy.sql.sqltypes", "bcrypt", "pyotp",
    "httpx", "fastapi", "fastapi.responses", "fastapi.routing",
    "starlette", "starlette.responses", "starlette.middleware",
    "starlette.middleware.base", "pydantic",
]:
    if mod_name not in sys.modules and not _has_module(mod_name):
        sys.modules[mod_name] = MagicMock()

# On Windows, importing chromadb (only test_chroma_client's importorskip does)
# loads native runtimes (onnxruntime and friends) whose background threads
# crash the process mid-suite with "Windows fatal exception: access violation",
# truncating the run and hiding later results. Block the import so
# importorskip skips cleanly instead...
if os.name == "nt":
    sys.modules.setdefault("chromadb", None)

if "src.database" not in sys.modules:
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["src.database"] = _db
```

Three deliberate design choices baked into this file:

1. **Conditional stubbing, not blanket mocking.** Heavy/optional deps
   (`sqlalchemy`, `bcrypt`, `pyotp`, `httpx`, and — notably — `fastapi`,
   `starlette`, `pydantic` themselves) are only replaced with a `MagicMock()`
   module **if genuinely absent** from the environment (`_has_module`). A CI
   runner with a full `pip install -r requirements-dev.txt` never hits the
   stub branch for FastAPI/Starlette/Pydantic — those are always real,
   because route-level tests import their actual subpackages
   (`fastapi.testclient.TestClient`, etc.) and a mocked FastAPI would make
   every route test worthless. This is exactly the mechanism that explains
   §1's collection errors in a bare-system-Python run: `sqlalchemy` **was**
   importable (`find_spec` succeeded) so conftest left it alone, and the
   partially-broken real install then failed deeper inside
   `core/database.py`.
2. **A synthetic `src.database` module** is always installed (regardless of
   whether the real one is importable) with `SessionLocal` and
   `ModelEndpoint` as `MagicMock()` — most tests never need a real SQLite
   session, so this keeps import-time cheap and hermetic by default; tests
   that do need the real DB layer import it explicitly and/or use
   `tests/real_modules.py`.
3. **Windows-specific `chromadb` poisoning.** Setting
   `sys.modules.setdefault("chromadb", None)` makes any subsequent `import
   chromadb` raise `ImportError` immediately (Python's import system treats a
   `None` entry in `sys.modules` as "known to fail") instead of actually
   attempting the import — see §5 for the exact same class of native-crash
   problem this avoids (there it's `python-magic`/libmagic; here it's
   `chromadb`'s ONNX runtime dependency chain).

## 3. Running the suite

```bash
# Full local gate (mirrors CI) — from repo root, with venv/ populated:
scripts/check.sh

# Which runs, in order:
python -m compileall -q app.py companion core routes services src \
    scripts/apollo-ralph scripts/check-paperclip-browser
python scripts/check_runtime_paths.py --root .
python scripts/check_module_sizes.py
python -m pytest -q
npm run test:js
# Plus, only if APOLLO_STARTUP_SMOKE=1:
python scripts/smoke_startup.py
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "e2e: real-browser tests that start an isolated local application",
]
```
`asyncio_mode = "auto"` means `async def test_...` functions run without a
per-test `@pytest.mark.asyncio` decorator — most of the FastAPI route tests
are async. Running a single file: `python -m pytest -q
tests/test_settings_desktop_mode.py -v`.

`scripts/check_runtime_paths.py` is a separate static-analysis gate (76
lines, AST-based): it rejects checkout-relative string literals like
`"data"` / `"data/..."` appearing anywhere in production Python code outside
an exemption list (`src/runtime_paths.py`, `src/data_migration.py`) — a
regression guard for the `data_root()` resolution logic in doc 09 §8 (a
hardcoded `"data/..."` path would silently break once the platform-directory
migration activates).

## 4. JavaScript tests (`node:test`, no framework/bundler)

`package.json`:
```json
"scripts": {
  "check": "bash scripts/check.sh",
  "test": "npm run test:js",
  "test:js": "node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs tests/test_system_status_actions.mjs tests/test_theme_presets.mjs tests/test_voice_vad.mjs tests/test_voice_call_machine.mjs tests/test_graph_layout.mjs tests/test_model_meta.mjs tests/test_command_parse.mjs tests/test_censor_detect.mjs tests/test_module_boundaries.mjs tests/test_document_modules.mjs tests/test_email_library_modules.mjs tests/test_chat_lifecycle.mjs tests/test_notes_modules.mjs tests/test_settings_modules.mjs"
}
```
Fifteen `.mjs` files, explicitly listed (not glob-discovered) and run with
Node's built-in `node --test` runner — no Jest/Mocha/Vitest dependency at
all. Each test file directly `import()`s the real ES module from
`static/js/...` (no DOM, no jsdom — these test pure logic functions
extracted into standalone modules) using Node's native `assert/strict`:

```js
// tests/test_settings_modules.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

const models = await import('../static/js/settings/models.js');

test('settings model policy labels endpoint availability and filters chat models', () => {
  assert.equal(models.endpointLabel({ name: 'Local', online: false }), 'Local (offline)');
  assert.deepEqual(models.selectableModels(['embed', 'chat', 'unsupported'], {
    embed: { kind: 'embedding' }, unsupported: { kind: 'unsupported' },
  }, { chatOnly: true }), [ /* ... */ ]);
});
```
`test_module_boundaries.mjs` is a meta-test — it almost certainly enforces
the module-size ratchet's counterpart at the import-graph level (which
modules may depend on which), complementing `scripts/check_module_sizes.py`'s
line-count ratchet (doc 11 §1).

## 5. The Windows CI hang lesson — `UploadHandler` / `python-magic`

`src/upload_handler.py` (constructor, ~line 90-107):
```python
# Initialize file detector. The pinned dependency is `python-magic`
# (a ctypes wrapper around libmagic), not `python-magic-bin` — Windows
# ships no libmagic, so ctypes' DLL search/load there is a native
# fault, not a Python exception: it has produced both a "Windows
# fatal exception: access violation" crash and (nondeterministically,
# depending on loader-lock timing) an indefinite hang, neither of
# which `except Exception` can catch. Skip the attempt entirely on
# Windows and use the basic-detection fallback below instead.
if os.name == "nt":
    self.file_detector = None
else:
    try:
        import magic
        self.file_detector = magic.Magic(mime=True)
    except Exception:
        self.file_detector = None
        logger.warning("python-magic not available, falling back to basic detection")
```
This is the load-bearing detail: a native DLL load failure inside `ctypes`
cannot be caught by Python's `except Exception` — it manifests as a hard
process crash or an indefinite hang *inside the CI runner*, which is why the
guard is an `os.name == "nt"` branch around the `import magic` attempt
entirely, not a `try/except` around it. `tests/conftest.py`'s Windows
`chromadb` poisoning (§2) guards against the same class of failure for a
different native dependency (ONNX runtime via chromadb) using the same
"never even attempt the import on Windows" strategy rather than trusting a
`try/except` to catch a native fault.

Tests exercising the upload path (`tests/test_upload_handler_observability.py`,
`test_upload_handler_atomicity.py`, `test_upload_error_surfaced.py`,
`test_upload_multifile.py`, `test_upload_id_validation.py`,
`test_upload_routes_owner_scope.py`, `test_personal_upload_isolation.py`) run
on all three CI platforms because they never need `magic.Magic` to actually
succeed — they exercise `UploadHandler` with `file_detector` legitimately
`None`, which is exactly the state Windows always starts in.

Other `os.name == "nt"` test-level skips, for context (none of these are the
magic/libmagic issue — they're POSIX-tool dependencies):
- `tests/test_cookbook_helpers.py` — `skipif(os.name == "nt", reason="executes generated POSIX shell via bash")`
- `tests/test_build_windows_zip.py` — `skipif(os.name == "nt", reason="requires a POSIX bash")`
- `tests/test_amd_gpu_check_args.py` — module-level `pytestmark`, same POSIX-bash reason
- `tests/test_apollo_dispatcher.py`, `tests/test_ralph_loop.py` — Windows-conditional branches
- `tests/test_security_regressions.py:100` — one test `skipif(sys.platform == "win32", ...)`

## 6. Notable regression suites

### 6.1 `tests/test_settings_desktop_mode.py` (153 lines)

Docstring states the bug directly: *"The macOS bundle launcher ships
`AUTH_ENABLED=false` as the default desktop experience. Every `require_admin`
route honors that mode, but `auth_routes`' admin endpoints did their own `if
not user or not is_admin(user)` check — which 403s when auth is CONFIGURED
(users exist) but DISABLED, breaking every Settings save, integrations CRUD,
and the Users panel in exactly the mode the app ships in."`

Five tests, each proving one edge of the fix:

- `test_settings_post_allowed_in_desktop_mode` — `AUTH_ENABLED=false` +
  configured users → `POST /api/auth/settings` succeeds (200), persists.
- `test_settings_get_unscrubbed_in_desktop_mode` — desktop mode sees the
  **full** settings document (secrets included), not the scrubbed copy —
  because desktop mode is the single-user local operator, not an anonymous
  caller.
- `test_users_list_allowed_in_desktop_mode` — `GET /api/auth/users` works
  without a session cookie.
- `test_still_403_when_auth_enabled_and_unauthenticated` — the fix must
  **not** open the admin surface when auth is genuinely on: same request,
  `AUTH_ENABLED=true`, asserts 403 and that the write never landed
  (`"default_model" not in store`).
- `test_admin_routes_do_not_trust_middleware_state` — **the interesting
  one**, a regression-of-the-regression-fix. The first attempted fix
  delegated to `core.middleware.require_admin`, which trusts
  `request.state.current_user` — a value the auth middleware populates via
  loopback/bypass paths. On a direct loopback request that turned `GET
  /api/auth/users` from 403 into 200 for an **unauthenticated** caller,
  leaking usernames and privilege flags. The test recreates exactly that
  attack shape: it registers a Starlette middleware that stamps
  `request.state.current_user = "antman"` / `request.state.internal_tool =
  False` (precisely what the real loopback-bypass path in `app.py` does) but
  presents **no session cookie**, then asserts every admin surface still
  403s — proving the gate validates the cookie itself and never trusts a
  middleware-stamped identity alone:

```python
@app.middleware("http")
async def _stamp_admin_identity(request, call_next):
    request.state.current_user = "antman"
    request.state.internal_tool = False
    return await call_next(request)
...
for path in ("/api/auth/users", "/api/auth/integrations"):
    assert c.get(path).status_code == 403, (
        f"{path} leaked to an unauthenticated caller carrying a "
        f"middleware-stamped identity"
    )
```

The whole file is gated: `pytestmark = pytest.mark.skipif(not _REAL, reason="needs
real fastapi+bcrypt+cryptography installed")` where `_REAL = all(_has_real(m)
for m in ("fastapi", "bcrypt", "cryptography"))` — it explicitly requires the
non-stubbed dependencies since it's testing real FastAPI request/response
wiring, not logic that tolerates mocks.

### 6.2 `tests/test_auth_regressions.py` (385 lines)

Helper at the top of the file, `_fake_auth_request` (line 103), builds a
minimal `SimpleNamespace` standing in for a Starlette `Request` — enough
surface for `require_admin`/route handlers to read without booting a real
ASGI app:

```python
def _fake_auth_request(token="session-token", auth_manager=None, user="admin"):
    from routes.auth_routes import SESSION_COOKIE
    req = SimpleNamespace()
    req.cookies = {SESSION_COOKIE: token}
    req.client = SimpleNamespace(host="127.0.0.1")
    req.headers = {}
    req.state = SimpleNamespace(current_user=user, internal_tool=False)
    req.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
    return req
```
Comment on `req.headers`/`req.state`: *"A real Starlette Request always
carries these. The admin auth routes now delegate to
`core.middleware.require_admin` (the single admin policy source, so the
no-login desktop mode works)... The admin decision still comes from
`auth_manager.is_admin()`, so tests control the outcome exactly as before."*
— i.e. this helper predates and complements the desktop-mode fix in §6.1;
it's the lightweight (no `TestClient`, no real HTTP) way to exercise the same
`require_admin` policy against handler functions directly.

Other coverage in this file: `_auth_route_endpoint(path, method)` (line 92)
resolves a route object straight out of `setup_auth_routes(...).routes` by
path+method for direct invocation; signup-toggle idempotency tests
(`test_set_signup_enabled_true/false_is_idempotent`,
`test_set_signup_enabled_requires_admin`); research-endpoint ownership tests
(`test_research_status_rejects_anonymous/rejects_wrong_owner`, `..._cancel_`,
`..._delete_`, `..._spinoff_`); notification owner-scoping
(`test_pop_notifications_owner_filtered`); and task/notification default
behavior (`test_admin_only_actions_set_contains_shell_runners`,
`test_task_create_notification_default_allows_action_specific_defaults`,
`test_ship_paused_housekeeping_stays_paused_by_default`).

### 6.3 `tests/test_security_regressions.py` (1043 lines, the largest single test file)

Covers a broad security surface in one file rather than many small ones.
Notable groups (function names verbatim, `grep -n "^def test_"`):

- **Secret-at-rest** (`test_secret_storage_roundtrip`,
  `test_secret_storage_is_encrypted`,
  `test_secret_storage_legacy_plaintext_passes_through`,
  `test_secret_storage_corrupt_token_returns_empty`,
  `test_secret_storage_key_created_with_safe_mode`) — the encryption-key
  file must be created with restrictive permissions and a corrupt/undecodable
  token must fail closed (empty string), not raise.
- **Compose network posture** (`test_docker_compose_binds_web_ui_to_loopback_by_default`,
  `test_readme_native_quickstart_uses_loopback`,
  `test_ollama_cookbook_runner_does_not_force_public_bind`) — these
  literally parse `docker-compose.yml`/`README.md` text and assert loopback
  defaults, e.g. `assert "${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000" in
  compose` and `assert '"${APP_PORT:-7000}:7000"' not in compose` (i.e. a
  regression that dropped the `APP_BIND` prefix, exposing the port on all
  interfaces, would fail this test).
- **Integrations-at-rest encryption + migration**
  (`test_integrations_api_keys_are_encrypted_at_rest`,
  `test_integrations_plaintext_keys_migrate_on_load`).
- **Shell-quoting / injection** (`test_q_plain_name`, `test_q_name_with_spaces`,
  `test_q_escapes_backslash`, `test_q_escapes_double_quote`, `test_q_empty_input`)
  and **path traversal** (`test_path_name_strips_traversal`, parametrized).
- **Cross-owner attachment/upload isolation** — a dense cluster:
  `test_upload_resolver_rejects_cross_owner_upload_ids`,
  `test_build_user_content_skips_cross_owner_attachments`,
  `test_chat_preprocess_does_not_surface_cross_owner_attachment`,
  `test_document_upload_lookup_rejects_cross_owner_marker`,
  `test_find_source_upload_id_rejects_path_traversal_marker`,
  `test_pdf_marker_write_rejects_cross_owner_upload`,
  `test_pdf_marker_render_lookup_denies_cross_owner_without_doc_leak`.
- **Core auth-gate matrix** — `test_require_user_rejects_unauthenticated`,
  `test_require_user_accepts_loopback_when_unconfigured`,
  `test_require_user_accepts_anyone_when_auth_disabled`,
  `test_require_user_localhost_bypass_admits_loopback`,
  `test_require_user_localhost_bypass_still_rejects_lan`,
  `test_require_admin_rejects_unconfigured_public_api`,
  `test_require_admin_allows_when_auth_explicitly_disabled`,
  `test_internal_tool_owner_header_logic_requires_known_user`,
  `test_auth_manager_migrates_legacy_admin_role` — this cluster is the
  authoritative behavioral spec for every `AUTH_ENABLED`/`LOCALHOST_BYPASS`
  combination described in doc 09 §2.1.
- **SSRF guards** — `test_web_content_fetcher_blocks_private_url`,
  `test_web_content_fetcher_blocks_dns_to_private`,
  `test_web_fetch_guard_blocks_private_and_bad_schemes` (parametrized),
  `test_web_fetch_guard_allows_public_ip`,
  `test_web_fetch_guard_blocks_dns_resolving_to_private`,
  `test_web_fetch_guard_fails_closed_on_empty_resolution`,
  `test_web_fetch_guard_blocks_redirect_into_private` — this last one
  matters because a naive SSRF guard that only checks the initial URL misses
  a 30x redirect into a private address.
- **HTML/XSS sanitization** — `test_email_thread_rendering_sanitizes_body_html`,
  `test_session_html_export_escapes_name`, `test_mcp_oauth_page_escapes_reflected_values`.
- **Filename sanitization** — `test_export_filename_sanitizer_blocks_header_and_path_chars`,
  `test_export_filename_sanitizer_preserves_safe_names`,
  `test_gallery_replace_filename_sanitizer_uses_basename`,
  `test_gallery_replace_filename_sanitizer_falls_back_when_empty`.
- **Untrusted-context prompt injection guard** —
  `test_untrusted_context_message_is_not_system_role`,
  `test_untrusted_context_policy_marks_sources_as_data` — fetched/RAG content
  must never be injected as a `system`-role message.

One test in this file is platform-gated: line 100,
`sys.platform == "win32"` skip (paired with the POSIX-tooling skips in §5).

## 7. Browser-journey Playwright tests (`tests/e2e/`)

Marker declared in `pyproject.toml`: `"e2e: real-browser tests that start an
isolated local application"`. Unlike the rest of the suite (which stubs/mocks
liberally), these tests boot a **real** `uvicorn` server and drive it with a
**real** Chromium via Playwright's sync API — closer to a smoke/acceptance
suite than a unit suite, which is why they're a separate CI job (§ doc 11)
rather than folded into the main pytest run.

`scripts/run-e2e.sh` is the harness:

```bash
# scripts/run-e2e.sh (essentials)
PORT="$($PYTHON - <<'PY'
import socket
s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()
PY
)"                                            # OS-assigned free port — no collisions between runs
CHROMIUM="${APOLLO_E2E_CHROMIUM:-$(find "$HOME/Library/Caches/ms-playwright" \
  -type f -name 'Google Chrome for Testing' -print -quit 2>/dev/null || true)}"
[[ -n "$CHROMIUM" ]] || CHROMIUM="$($PYTHON -c \
  'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); \
   print(p.chromium.executable_path); p.stop()' 2>/dev/null || true)"

AUTH_ENABLED=true \
APOLLO_DATA_DIR="$TMP_DIR/data" \
DATA_DIR="$TMP_DIR/data" \
DATABASE_URL="sqlite:///$TMP_DIR/apollo.db" \
APOLLO_BROWSER_EXECUTABLE_PATH="$CHROMIUM" \
APOLLO_DISABLE_MCP=true \
PAPERCLIP_ENABLED=true \
PAPERCLIP_MODE=external \
PAPERCLIP_PORT=3199 \
PAPERCLIP_SECRET_FILE="$TMP_DIR/paperclip_secret" \
PAPERCLIP_PROXY_TOKEN_FILE="$TMP_DIR/paperclip_proxy_token" \
"$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$PORT" >"$TMP_DIR/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null || { cat "$TMP_DIR/server.log"; exit 1; }
APOLLO_E2E_BASE_URL="http://127.0.0.1:$PORT/" APOLLO_E2E_CHROMIUM="$CHROMIUM" \
  "$PYTHON" -m pytest -q tests/e2e
```

Key mechanics:
- **Isolated everything**: a fresh temp dir becomes `APOLLO_DATA_DIR`, a
  throwaway SQLite file becomes `DATABASE_URL`, `APOLLO_DISABLE_MCP=true`
  keeps stdio MCP servers from spawning (not needed for a UI smoke test and
  one less native-process failure mode), `PAPERCLIP_MODE=external` on a port
  nothing is actually listening on (`3199`) so the Paperclip proxy paths
  exist without requiring the sidecar itself.
- **`AUTH_ENABLED=true`** — deliberately the opposite of the desktop-mode
  default; §6.1's whole regression class ("does the real login flow work")
  is only exercisable with auth genuinely on.
- 60 one-second polls against `/` before giving up and dumping
  `server.log` — a slow first-run model/index download would otherwise fail
  the harness with a useless "connection refused".
- Cleanup trap keeps the temp dir only on failure (`APOLLO_E2E_KEEP_ARTIFACTS=true`
  or non-zero exit) so a passing run leaves nothing behind but a failing one
  is diagnosable.
- `APOLLO_E2E_RESULT_FILE`, if set, receives the raw pytest exit code — lets
  a CI wrapper step read the result without relying on shell `set -e`
  propagation through the trap.

`tests/e2e/test_browser_smoke.py` (261 lines) test functions (`grep -n "^def
test_"`):
- `test_landing_page_renders_without_console_errors` — loads `/`, asserts no
  browser console errors.
- `test_document_create_edit_and_save` — full document-editor round trip
  through the real UI.
- `test_browser_panel_and_agent_browser_local_fixture` — exercises the
  embedded-browser panel against a local fixture page (not a live external
  site, keeping the test hermetic/offline-safe).
- `test_paperclip_floor_renders_preview_and_live_agent_activity` — the
  Paperclip Floor UI (isometric SVG stage) renders and reflects activity.

Helper functions above them: `_authenticated_session(base_url)` (line 19),
`_authenticated_page(browser, viewport, console_errors=None)` (line 42),
`_browser_fixture_server()` (line 68), `_browser_api(page, path, *,
method="GET", body=None)` (line 96) — the pattern is "log in once via a real
HTTP session, hand a logged-in `page` to each test, capture console errors
into a passed-in list so a test can assert on them directly." Chromium
executable resolution inside the test file itself falls back through
`os.getenv("APOLLO_E2E_CHROMIUM")` → `os.environ["APOLLO_E2E_CHROMIUM"]`
(hard-required by the time actual browser launches happen) — see doc 09
§2.7.

Other browser-adjacent (but non-Playwright, non-`e2e`-marked, run in the
main suite) files: `tests/test_embedded_browser_integration.py`,
`tests/test_browser_use_integration.py`, `tests/test_browser_ws.py` — these
exercise `services/browser/embedded_browser.py` and the Paperclip
`browser-use` verifier at the unit/mock level, not through a real browser.

## 8. `tests/bombadil-spec.ts` — not a test file

`package.json` lists `@antithesishq/bombadil` as a devDependency.
`tests/bombadil-spec.ts` is a TypeScript spec for Antithesis' Bombadil
fuzzing/property-testing harness, not something `npm run test:js` or pytest
executes. UNCERTAIN: no CI job in `.github/workflows/` invokes it — it
appears to be present for optional/manual fuzzing runs outside the
committed CI pipeline. Treat it as out of scope for "the test suite that
runs on every PR."

## 9. Uncertainties

- UNCERTAIN: exact clean-`venv` `pytest --collect-only` total not verified
  (no `venv/` in this worktree); the 2037-collected/12-errored figure is from
  a bare system-Python run and the 12 errors are an environment artifact
  (broken `sqlalchemy` install), not a code defect — see §1-2.
- UNCERTAIN: whether `tests/real_modules.py` / `tests/css_source.py` are
  pytest fixture modules, standalone helper libraries, or something else was
  not traced in depth in this pass — inferred from filename/import
  conventions only.
- UNCERTAIN: `test_module_boundaries.mjs`'s exact enforcement rules were not
  read line-by-line; its relationship to `scripts/check_module_sizes.py` is
  inferred from naming and CI adjacency, not confirmed by reading both
  files side by side.
