# Apollo Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the six production-readiness improvements identified on 2026-07-17: secure temporary model-runner secrets, portable application data, consistent request ownership, reproducible cross-platform delivery, observable failures, and smaller frontend modules with end-to-end workflow coverage.

**Architecture:** Establish shared security, path, identity, and observability contracts before migrating feature code. Every migration is test-first, backward compatible, and independently committable. Existing user data remains untouched until a verified copy succeeds, and every phase ends with the full Apollo check.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest, JavaScript ES modules, Node test runner, Playwright/Chromium, GitHub Actions, pip-tools, Ruff.

---

## Terra Execution Contract

1. Work only in `/Users/Antman/Desktop/Apollo` on branch `codex/apollo-production-hardening`.
2. Do not delete, rename, or rewrite `data/`. It is ignored by Git and currently contains about 286 MB of local user state.
3. Preserve unrelated user changes. At plan creation, the expected changes are the deterministic Docker-loopback test in `tests/test_model_routes.py` and this untracked plan file.
4. Use test-driven development: add a failing test, run it and capture the expected failure, implement the smallest complete change, then rerun targeted and full checks.
5. Commit after each numbered task. Never combine tasks from different phases in one commit.
6. Never place tokens, passwords, `.env` contents, email bodies, or user data in logs, test fixtures, commits, screenshots, or completion receipts.
7. Stop a phase if a migration cannot prove rollback, if owner isolation regresses, or if a platform job fails. Fix the phase before proceeding.
8. Update the checkbox in this file only after the task's verification commands pass.

## Baseline and Completion Metrics

| Metric | Required baseline | Completion gate |
|---|---:|---:|
| Python suite | 1,811 passed, 2 skipped | No regressions; new tests included |
| JavaScript suite | 119 passed | No regressions; extracted modules tested |
| Broken Python dependencies | 0 | 0 |
| npm production vulnerabilities | 0 | 0 high/critical; document lower severities |
| Old virtualenv path references | 0 | 0 |
| Unclassified `except ...: pass` handlers | inventory in Phase 5 | 0 |
| Raw production `Path("data")`/`os.path.join("data", ...)` uses | inventory in Phase 2 | 0 outside the migration compatibility layer |
| Supported CI hosts | Ubuntu only | Ubuntu, macOS, Windows |

## Dependency Order

```text
Baseline
  -> secure runner files
  -> canonical data root -> verified data migration -> path consumers
  -> canonical request identity -> owner-scoped features
  -> locked dependencies -> cross-platform CI/startup smoke
  -> observability contract -> critical-path exception migration
  -> frontend module extractions -> browser workflow tests
  -> release audit and production-readiness receipt
```

---

## Phase 0: Preserve the Known-Good Baseline

### Task 0: Create the branch and preserve the Docker test correction

**Files:**
- Modify: `tests/test_model_routes.py:423`

- [ ] Verify that the only tracked change is the added `_container_loopback_reachable` monkeypatch:

```bash
cd /Users/Antman/Desktop/Apollo
git status --short --branch
git diff -- tests/test_model_routes.py
```

Expected: one modified tracked test plus this untracked plan file; no source or user-data changes.

- [ ] Create the implementation branch without discarding the local change:

```bash
git switch -c codex/apollo-production-hardening
```

- [ ] Preserve the approved plan before implementation:

```bash
git add docs/superpowers/plans/2026-07-17-apollo-production-hardening.md
git commit -m "docs: add Apollo production hardening plan"
```

- [ ] Run the isolated regression test and the full check:

```bash
venv/bin/pytest -q tests/test_model_routes.py::TestDockerLoopbackRewrite
bash scripts/check.sh
```

Expected: `3 passed`; then `1811 passed, 2 skipped` and `119` JavaScript tests passed.

- [ ] Commit only the deterministic test correction:

```bash
git add tests/test_model_routes.py
git commit -m "test(models): isolate Docker loopback rewrite from host ports"
```

**Acceptance criteria:** The branch exists, the repository is clean, the current baseline passes, and no files under `data/` were touched.

---

## Phase 1: Secure Temporary Model-Runner Secrets

### Task 1: Add a private temporary-file primitive

**Files:**
- Create: `src/secure_temp.py`
- Create: `tests/test_secure_temp.py`

- [ ] Write tests proving that a private directory is mode `0700`, secret files are mode `0600`, executable scripts are mode `0700`, replacement truncates old content, and cleanup tolerates an already-removed file. Skip POSIX mode assertions on Windows while still testing lifecycle behavior.

- [ ] Run the tests before implementation:

```bash
venv/bin/pytest -q tests/test_secure_temp.py
```

Expected: import failure for `src.secure_temp`.

- [ ] Implement this public interface using `os.open` with explicit modes and no shell commands:

```python
def ensure_private_dir(path: Path) -> Path: ...
def write_private_text(path: Path, content: str, *, executable: bool = False) -> Path: ...
def remove_private_file(path: Path | None) -> None: ...
```

`write_private_text` must use owner-only permissions from creation time, not `write_text()` followed by `chmod()`.

- [ ] Run:

```bash
venv/bin/pytest -q tests/test_secure_temp.py
venv/bin/python -m compileall -q src/secure_temp.py
```

- [ ] Commit:

```bash
git add src/secure_temp.py tests/test_secure_temp.py
git commit -m "feat(security): add private temporary-file helpers"
```

**Acceptance criteria:** No secret file is briefly created with process-default permissions, and all tests pass on POSIX and Windows.

### Task 2: Remove Hugging Face tokens from generated runner scripts

**Files:**
- Create: `routes/cookbook_runner_files.py`
- Create: `tests/test_cookbook_runner_security.py`
- Modify: `routes/cookbook_routes.py`
- Modify: `routes/shell_routes.py:354`

- [ ] Add failing tests for local POSIX, remote POSIX, and remote Windows runner generation. Assert that runner text, setup commands, and logged command strings never contain the token value; secret sidecar files are private; local sidecars are removed in success and failure paths; remote sidecars are removed by the remote runner before the model command starts.

- [ ] Run:

```bash
venv/bin/pytest -q tests/test_cookbook_runner_security.py
```

Expected: failures showing that `export HF_TOKEN='...'` or `$env:HF_TOKEN = '...'` is embedded in generated scripts.

- [ ] Move runner-file construction into `routes/cookbook_runner_files.py`. Use a private sidecar containing only the environment assignment. Generated runners must source/read the sidecar, delete it immediately, and then execute the download or serve command. Quote all paths with the existing platform-specific quoting helpers.

- [ ] Change `TMUX_LOG_DIR` initialization to call `ensure_private_dir`. Replace every runner `write_text`/`chmod(0o755)` pair with `write_private_text(..., executable=True)`. Local SCP staging files must be mode `0600`; remote commands must apply owner-only permissions.

- [ ] Wrap setup execution in `try/finally` so local runner and secret staging files are removed after launch succeeds or fails. Keep runtime log files, PID files, and user-visible diagnostics.

- [ ] Confirm logs contain identifiers but not secrets:

```bash
venv/bin/pytest -q tests/test_cookbook_runner_security.py tests/test_cookbook_helpers.py
rg -n "export HF_TOKEN=|env:HF_TOKEN" routes/cookbook_routes.py routes/cookbook_runner_files.py
```

Expected: tests pass; the search finds only constant, non-secret loader logic or test assertions, never interpolation of `req.hf_token` into runner text.

- [ ] Run `bash scripts/check.sh` and commit:

```bash
git add routes/cookbook_runner_files.py routes/cookbook_routes.py routes/shell_routes.py tests/test_cookbook_runner_security.py
git commit -m "fix(security): keep model tokens out of runner scripts"
```

**Acceptance criteria:** Token values are absent from generated scripts, command arguments, and logs; private sidecars are owner-only and deterministically removed.

### Phase 1 Checkpoint

- [ ] `bash scripts/check.sh` passes.
- [ ] `venv/bin/python -m pip check` reports no broken requirements.
- [ ] A repository search finds no direct secret interpolation into runner scripts.
- [ ] `git status --short` is clean.

---

## Phase 2: Portable Application Data and Safe Migration

### Task 3: Define one canonical runtime-path contract

**Files:**
- Create: `src/runtime_paths.py`
- Create: `tests/test_runtime_paths.py`
- Modify: `src/constants.py`
- Modify: `core/constants.py`
- Modify: `src/config.py`

- [ ] Write parameterized tests for macOS, Windows, Linux/XDG, Docker/explicit override, and legacy `DATA_DIR`. Test priority in this exact order: `APOLLO_DATA_DIR`, legacy `DATA_DIR`, platform default.

- [ ] Define this interface:

```python
def repo_root() -> Path: ...
def legacy_data_root() -> Path: ...
def platform_data_root(*, platform: str | None = None, env: Mapping[str, str] | None = None) -> Path: ...
def data_root() -> Path: ...
def data_path(*parts: str) -> Path: ...
```

Platform defaults:
- macOS: `~/Library/Application Support/Apollo`
- Windows: `%LOCALAPPDATA%/Apollo`
- Linux: `$XDG_DATA_HOME/apollo`, otherwise `~/.local/share/apollo`

- [ ] Make `src.constants` derive every data path from `data_root()`. Turn `core.constants` into a compatibility re-export so it cannot drift to a second version or path definition.

- [ ] Make `DataConfig` defaults absolute and derived from the same contract. Keep both environment variables for backward compatibility, but document `APOLLO_DATA_DIR` as canonical.

- [ ] Run:

```bash
venv/bin/pytest -q tests/test_runtime_paths.py tests/test_app_startup_helpers.py
venv/bin/python -m compileall -q src/runtime_paths.py src/constants.py core/constants.py src/config.py
```

- [ ] Commit:

```bash
git add src/runtime_paths.py src/constants.py core/constants.py src/config.py tests/test_runtime_paths.py
git commit -m "refactor(storage): define canonical Apollo data paths"
```

**Acceptance criteria:** All callers can obtain an absolute data path without relying on the current working directory, and existing environment overrides still work.

### Task 4: Implement a copy-verify-activate legacy data migration

**Files:**
- Create: `src/data_migration.py`
- Create: `scripts/apollo-data-migrate`
- Create: `tests/test_data_migration.py`
- Modify: `setup.py`
- Modify: `services/app_startup.py`

- [ ] Write tests for dry-run, empty legacy root, successful copy, interrupted copy, existing target, filename collision, SQLite integrity failure, and idempotent rerun. Use `tmp_path`; never point tests at Apollo's real `data/`.

- [ ] Implement a migration manifest containing source, target, UTC timestamp, file count, total bytes, and per-file relative path/size. Verification must compare the manifest and run `PRAGMA integrity_check` on copied SQLite files.

- [ ] Migration behavior must be:
  1. Explicit data-directory environment variables disable automatic migration.
  2. Existing activated target wins.
  3. Legacy data is copied to a sibling staging directory.
  4. Verification runs before activation.
  5. Activation is an atomic directory rename plus an `apollo-data-migration.json` receipt.
  6. Legacy data remains untouched as rollback material.
  7. Any failure logs one actionable warning and continues against the legacy root.

- [ ] Add CLI operations `--dry-run`, `--copy`, `--verify`, and `--status`. Do not add a deletion operation.

- [ ] Call the migration check from setup/startup before database initialization. Surface migration state in startup logs without exposing filenames from private user content.

- [ ] Run:

```bash
venv/bin/pytest -q tests/test_data_migration.py tests/test_app_startup_helpers.py
venv/bin/python scripts/apollo-data-migrate --dry-run
```

Expected: tests pass; dry-run reports source, destination, count, and bytes without copying.

- [ ] Commit:

```bash
git add src/data_migration.py scripts/apollo-data-migrate tests/test_data_migration.py setup.py services/app_startup.py
git commit -m "feat(storage): add verified legacy data migration"
```

**Acceptance criteria:** Migration cannot delete or partially activate user data, is resumable, and produces a machine-readable rollback receipt.

### Task 5: Migrate all production data-path consumers

**Files:**
- Create: `scripts/check_runtime_paths.py`
- Create: `tests/test_no_relative_runtime_paths.py`
- Modify: `routes/research_routes.py`
- Modify: `src/research_handler.py`
- Modify: `services/research/research_handler.py`
- Modify: `routes/email_helpers.py`
- Modify: `core/database.py`
- Modify: `src/bg_jobs.py`
- Modify: `src/tools/research_contacts.py`
- Modify: remaining production files reported by the guard

- [ ] Write an AST-based guard that rejects production calls equivalent to `Path("data")`, `Path("data/...")`, `os.path.join("data", ...)`, and default arguments equal to raw `"data"`. Exempt tests, migration compatibility code, and shipped static fixtures only.

- [ ] Run the guard before migration and save its file/line inventory in the task receipt. Expected: failure with the current raw path sites.

- [ ] Replace each site with `data_root()` or `data_path(...)`. Do this in three bounded batches, running focused tests after each:
  1. database, auth, settings, memory, and startup;
  2. research, background jobs, tools, and integrations;
  3. email attachments/scheduling, gallery, vault, and remaining routes.

- [ ] Update Docker and native launch configuration to set or display the resolved data root. Docker volumes must continue to map to the explicit container data directory.

- [ ] Run:

```bash
venv/bin/python scripts/check_runtime_paths.py
venv/bin/pytest -q tests/test_no_relative_runtime_paths.py tests/test_research_owner_scope_routes.py tests/test_email_owner_scope.py
bash scripts/check.sh
```

Expected: zero unapproved relative runtime paths and the full suite passes.

- [ ] Commit:

```bash
git add scripts/check_runtime_paths.py tests/test_no_relative_runtime_paths.py \
  app.py core/database.py routes/cookbook_routes.py routes/email_helpers.py \
  routes/gallery_routes.py routes/prefs_routes.py routes/research_routes.py \
  routes/task_routes.py routes/vault_routes.py services/research/research_handler.py \
  src/ai_interaction.py src/bg_jobs.py src/builtin_actions.py src/config.py \
  src/research_handler.py src/tools/research_contacts.py src/tools/vault.py \
  docker-compose.yml start-macos.sh launch-windows.ps1
git commit -m "refactor(storage): route persisted state through the canonical data root"
```

Before committing, compare `git diff --name-only --cached` with the Task 5 inventory. Omit listed files that did not change and add any additional production file reported by the AST guard; do not stage unrelated files.

**Acceptance criteria:** Launching Apollo from any working directory reads the same user data, and the guard prevents future relative-path regressions.

### Phase 2 Checkpoint

- [ ] Run migration tests and the full check.
- [ ] Run a dry-run against the real legacy data, but do not copy it without the user's explicit approval.
- [ ] Start Apollo once from the repository root and once from `/tmp`; both report the same resolved data root.
- [ ] Confirm the legacy `data/` directory is unchanged by comparing count and total bytes before/after.

---

## Phase 3: Canonical Identity and Owner Isolation

### Task 6: Define and test the request identity matrix

**Files:**
- Modify: `src/auth_helpers.py`
- Create: `tests/test_request_identity.py`

- [ ] Add a frozen `RequestIdentity` value with `principal`, `owner`, `auth_mode`, `is_authenticated`, `is_admin`, and `is_local_bypass`. Add `resolve_identity(request)` as the only function that reads raw request-state identity fields.

- [ ] Parameterize tests for cookie user, owned API token, ownerless API token, internal tool, auth-disabled mode, configured anonymous request, first-run loopback, localhost bypass, and non-loopback anonymous request.

- [ ] Preserve existing public helpers as wrappers:

```python
get_current_user(request)     # compatibility principal
effective_user(request)       # canonical owner
require_user(request)         # gate, then return canonical owner
require_privilege(request, key)
```

- [ ] Ensure ownerless API tokens never gain another user's ownership, and internal-tool identity is explicit rather than inferred from a username string in feature routes.

- [ ] Run and commit:

```bash
venv/bin/pytest -q tests/test_request_identity.py tests/test_auth_regressions.py tests/test_auth_session_revocation.py
git add src/auth_helpers.py tests/test_request_identity.py
git commit -m "refactor(auth): centralize request identity resolution"
```

**Acceptance criteria:** Every supported auth mode has one documented principal/owner result and one test.

### Task 7: Migrate research ownership to the shared identity contract

**Files:**
- Modify: `routes/research_routes.py`
- Modify: `src/research_handler.py`
- Modify: `src/tools/research_contacts.py`
- Modify: `tests/test_research_owner_scope_routes.py`
- Modify: `tests/test_research_chat_stream_owner.py`

- [ ] Add failing tests proving an owned API token sees the same research library as its cookie owner, another user receives `404`, anonymous configured requests receive `401`, and single-user mode retains access to legacy null-owner reports only through an explicit legacy policy.

- [ ] Delete the route-local `_require_user`. Use shared `require_user` for the gate and canonical owner for creation, listing, detail, report, archive, delete, stream, and contact extraction.

- [ ] Centralize persisted research read/write and ownership checks in the handler so route and agent-tool paths cannot diverge.

- [ ] Run and commit:

```bash
venv/bin/pytest -q tests/test_research_owner_scope_routes.py tests/test_research_chat_stream_owner.py tests/test_research_service.py
git add routes/research_routes.py src/research_handler.py src/tools/research_contacts.py tests/test_research_owner_scope_routes.py tests/test_research_chat_stream_owner.py
git commit -m "fix(research): unify persisted report ownership"
```

**Acceptance criteria:** Research ownership is identical across browser, API-token, and agent-tool entry points.

### Task 8: Migrate email, sessions, and owner-scoped tools

**Files:**
- Modify: `routes/email_helpers.py`
- Modify: `routes/email_routes.py`
- Modify: `routes/session_routes.py`
- Modify: `src/tool_execution.py`
- Modify: `src/tools/`
- Modify: `tests/test_email_owner_scope.py`
- Modify: `tests/test_document_tool_owner_scope.py`
- Create: `tests/test_owner_scope_matrix.py`

- [ ] Add one reusable test matrix that exercises cookie and owned-token access for sessions, documents, notes, tasks, email accounts, scheduled email, and tool-created resources. Include cross-owner denial and ownerless-token denial.

- [ ] Remove email's duplicate auth gate and import the shared contract. Keep account-specific `_assert_owns_account` checks, but feed them the canonical owner.

- [ ] Replace mixed `get_current_user`/`effective_user` ownership decisions in session routes with the canonical owner. Keep principal use only where audit attribution intentionally differs from data ownership.

- [ ] Search all production routes for direct reads of `request.state.current_user`. Migrate owner-scoped sites; document the small set of middleware/admin-only principal checks that remain.

- [ ] Run:

```bash
venv/bin/pytest -q tests/test_owner_scope_matrix.py tests/test_email_owner_scope.py tests/test_document_tool_owner_scope.py tests/test_owned_document_query.py
bash scripts/check.sh
```

- [ ] Commit:

```bash
git add routes/email_helpers.py routes/email_routes.py routes/session_routes.py src/tool_execution.py src/tools tests/test_owner_scope_matrix.py tests/test_email_owner_scope.py tests/test_document_tool_owner_scope.py
git commit -m "fix(auth): align owner-scoped routes and tools"
```

**Acceptance criteria:** The same human sees the same data through UI, API token, and agent tools, while cross-owner access consistently fails.

### Phase 3 Checkpoint

- [ ] Full suite passes.
- [ ] Identity matrix passes under `AUTH_ENABLED=true` and `AUTH_ENABLED=false`.
- [ ] Research and email owner-scope suites pass independently.
- [ ] No route-local copy of `require_user` remains.

---

## Phase 4: Reproducible Dependencies and Cross-Platform Delivery

### Task 9: Introduce deterministic Python dependency inputs and locks

**Files:**
- Create: `requirements.in`
- Create: `requirements-dev.in`
- Regenerate: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `requirements-optional.txt`
- Modify: `requirements-browser-use.txt`
- Create: `scripts/check_dependency_locks.py`
- Modify: `docs/OPERATIONS.md`

- [ ] Move human-maintained direct runtime requirements to `requirements.in`. Put pytest, pytest-asyncio, httpx2, pip-tools, pip-audit, and Ruff in `requirements-dev.in`; keep optional integrations in their existing files.

- [ ] Compile locks with Python 3.12 and include the generated header:

```bash
venv/bin/python -m pip install pip-tools
venv/bin/pip-compile --resolver=backtracking --strip-extras -o requirements.txt requirements.in
venv/bin/pip-compile --resolver=backtracking --strip-extras -o requirements-dev.txt requirements-dev.in
```

- [ ] Add a check that recompiles to temporary files and fails when committed locks differ. It must not modify the working tree.

- [ ] Build a fresh temporary virtual environment and prove install/import health:

```bash
python3.12 -m venv /tmp/apollo-lock-smoke
/tmp/apollo-lock-smoke/bin/python -m pip install -r requirements.txt
/tmp/apollo-lock-smoke/bin/python -m pip check
rm -rf /tmp/apollo-lock-smoke
```

- [ ] Run `bash scripts/check.sh` and commit.

**Acceptance criteria:** A fresh install resolves to committed versions, direct versus transitive dependencies are clear, and lock drift is machine-detectable.

### Task 10: Add a hermetic startup smoke test

**Files:**
- Create: `scripts/smoke_startup.py`
- Create: `tests/test_smoke_startup_script.py`
- Modify: `scripts/check.sh`

- [ ] Implement a smoke runner that creates a temporary data root, sets `AUTH_ENABLED=false`, disables optional external services, starts Apollo on an ephemeral loopback port, waits for the health endpoint, requests the main page and OpenAPI document, then terminates the full process tree.

- [ ] Assert no real `data/` file changes by recording its count/bytes before and after the smoke.

- [ ] Add timeout diagnostics that print only process status and sanitized log tails.

- [ ] Run:

```bash
venv/bin/pytest -q tests/test_smoke_startup_script.py
venv/bin/python scripts/smoke_startup.py
```

- [ ] Add the smoke to `scripts/check.sh` behind `APOLLO_STARTUP_SMOKE=1`, keeping the default local check fast.

- [ ] Commit.

**Acceptance criteria:** Apollo can prove a clean boot and shutdown without model providers, network credentials, or real user data.

### Task 11: Expand CI and packaging verification to Ubuntu, macOS, and Windows

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/build-windows-exe.yml`
- Create: `.github/workflows/dependency-audit.yml`
- Modify: `scripts/check.sh`
- Modify: `package.json`

- [ ] Convert the main test job to an OS matrix: `ubuntu-latest`, `macos-latest`, `windows-latest`, Python `3.12`, Node `20`. Use platform-native commands or a Python check wrapper rather than assuming Bash on Windows.

- [ ] Run compile, Python tests, JavaScript tests, dependency-lock verification, pip check, and hermetic startup smoke on every host. Cache only downloaded packages, never runtime data.

- [ ] Make the Windows build install runtime requirements, build the launcher, execute `Apollo.exe --help` or an equivalent noninteractive smoke, and upload checksums with the artifact.

- [ ] Add a weekly/manual audit workflow running `pip-audit` and `npm audit --omit=dev`. High or critical production vulnerabilities fail the job.

- [ ] Validate workflow syntax locally where possible, push the branch, and wait for all matrix jobs. Capture job URLs and results in the task receipt.

- [ ] Commit:

```bash
git add .github/workflows scripts/check.sh package.json
git commit -m "ci: verify Apollo across Linux macOS and Windows"
```

**Acceptance criteria:** All three operating systems install, test, start, and stop Apollo from a clean checkout; Windows packaging has a runtime smoke and checksum.

### Phase 4 Checkpoint

- [ ] Dependency locks reproduce.
- [ ] Local full suite and startup smoke pass.
- [ ] All GitHub Actions matrix jobs pass.
- [ ] Production npm audit and Python audit have no unresolved high/critical findings.

---

## Phase 5: Observable Failures Instead of Silent Exceptions

### Task 12: Add an exception-classification and logging contract

**Files:**
- Create: `src/observability.py`
- Create: `scripts/audit_exception_handlers.py`
- Create: `tests/test_observability.py`
- Create: `tests/test_exception_handler_audit.py`
- Modify: `pyproject.toml`

- [ ] Define three outcomes: `critical` logs error and returns/fails; `degraded` logs warning and exposes component health; `best_effort` logs debug with `exc_info=True`. No caught exception may silently `pass`.

- [ ] Implement a helper that accepts logger, event name, exception, severity, and a sanitized context dictionary. Reject context keys containing token/password/secret/body/content.

- [ ] Implement an AST audit that reports file, line, and handler type for silent broad catches. It must fail on new or unclassified handlers and accept handlers that log or deliberately re-raise.

- [ ] Add Ruff with a narrow initial policy for syntax, undefined names, duplicate imports, and bare exceptions. Do not reformat the entire repository in this task.

- [ ] Run and commit:

```bash
venv/bin/pytest -q tests/test_observability.py tests/test_exception_handler_audit.py
venv/bin/ruff check app.py core routes services src scripts
git add src/observability.py scripts/audit_exception_handlers.py tests/test_observability.py tests/test_exception_handler_audit.py pyproject.toml
git commit -m "feat(observability): classify and audit caught failures"
```

**Acceptance criteria:** The repository has a tested logging policy and a machine-generated inventory of every remaining silent catch.

### Task 13: Instrument scheduler and agent-loop failures

**Files:**
- Modify: `src/task_scheduler.py`
- Modify: `src/agent_loop.py`
- Modify: `src/tool_execution.py`
- Modify: `tests/test_task_scheduler_cancel.py`
- Modify: `tests/test_task_scheduler_session_delivery.py`
- Modify: `tests/test_agent_loop.py`

- [ ] Convert persistence, task state-transition, delivery, tool-execution, and model-response failures to `critical` or `degraded`. Cleanup/UI-log failures may be `best_effort` but must be visible at debug level.

- [ ] Add tests that inject database, delivery, and tool failures and assert the task/agent receives a stable failed or degraded state instead of hanging or reporting success.

- [ ] Include correlation fields `task_id`, `session_id`, `tool_name`, and `owner` only when present; never log prompts or tool payload bodies.

- [ ] Run focused tests, the exception audit, and the full check; commit as `fix(observability): surface scheduler and agent failures`.

**Acceptance criteria:** Agent and scheduler failures always terminate in an observable state with a sanitized diagnostic.

### Task 14: Instrument research, email, shell, model, and startup failures

**Files:**
- Modify: `src/research_handler.py`
- Modify: `routes/research_routes.py`
- Modify: `routes/email_helpers.py`
- Modify: `routes/email_routes.py`
- Modify: `routes/shell_routes.py`
- Modify: `routes/model_routes.py`
- Modify: `services/app_startup.py`
- Modify: focused tests for each subsystem

- [ ] Classify each caught failure by user impact. Network/provider failures return actionable degraded responses; state corruption and ownership failures are critical; cleanup remains best-effort with debug traceback.

- [ ] Add fault-injection tests for research persistence, IMAP/SMTP timeout, shell launch, provider probing, and startup component failure. Assert stable HTTP/status behavior and sanitized logs.

- [ ] Run the exception audit. Continue through remaining production files in bounded batches until it reports zero unclassified silent handlers.

- [ ] Run `bash scripts/check.sh` and commit as `fix(observability): expose integration failure states`.

**Acceptance criteria:** `scripts/audit_exception_handlers.py` reports zero unclassified silent catches across `app.py`, `core/`, `routes/`, `services/`, and `src/`.

### Phase 5 Checkpoint

- [ ] Exception audit passes with zero unclassified handlers.
- [ ] Ruff's configured policy passes.
- [ ] Fault-injection tests prove critical workflows fail closed or degrade explicitly.
- [ ] Full suite passes.

---

## Phase 6: Smaller Frontend Modules and End-to-End Workflow Proof

### Task 15: Establish extraction tests and module-size guardrails

**Files:**
- Create: `scripts/check_module_sizes.py`
- Create: `tests/test_module_boundaries.mjs`
- Modify: `package.json`
- Modify: `static/js/MODULE_SUMMARY.md`

- [ ] Add a module-size report with ratcheting limits based on current line counts. Existing oversized entry modules may keep a temporary baseline, but every extraction must lower it; no new JavaScript module may exceed 1,500 lines.

- [ ] Add Node tests that import each new pure module without browser globals and verify its public exports. Add the test file to `npm run test:js`.

- [ ] Document state ownership, API dependencies, DOM responsibilities, and allowed import direction for `document/`, `emailLibrary/`, `settings/`, `notes/`, and `chat/`.

- [ ] Run and commit as `test(frontend): add module boundary guardrails`.

**Acceptance criteria:** Module extraction has a measurable ratchet and importable seams before behavior moves.

### Task 16: Split document editing, export, review, and version history

**Files:**
- Create: `static/js/document/state.js`
- Create: `static/js/document/export.js`
- Create: `static/js/document/suggestions.js`
- Create: `static/js/document/diff.js`
- Create: `static/js/document/versionHistory.js`
- Modify: `static/js/document.js`
- Create: `tests/test_document_modules.mjs`

- [ ] Extract pure state first, then export functions around the current export section, then suggestion/diff behavior, then version-history behavior. Pass dependencies explicitly; do not create new window globals.

- [ ] After each extraction, run `node --test tests/test_document_modules.mjs` and the existing document Python regression tests.

- [ ] Preserve public exports from `document.js` so callers do not change in the same commit. Confirm create/load/save/delete, autosave, PDF/DOCX/HTML export, suggestion accept/reject, diff review, and version restore.

- [ ] Run the full check and commit as `refactor(document): split editor feature modules`.

**Acceptance criteria:** `document.js` becomes orchestration rather than implementation, no extracted module exceeds 1,500 lines, and behavior tests pass.

### Task 17: Split email library rendering and reader-window behavior

**Files:**
- Create: `static/js/emailLibrary/threadRendering.js`
- Create: `static/js/emailLibrary/attachments.js`
- Create: `static/js/emailLibrary/readerWindows.js`
- Create: `static/js/emailLibrary/bulkActions.js`
- Modify: `static/js/emailLibrary.js`
- Create: `tests/test_email_library_modules.mjs`

- [ ] Extract thread/plaintext rendering as pure functions, then attachment filtering/rendering, reader tab/window state, and bulk-action state. Reuse existing `state.js`, `utils.js`, `signatureFold.js`, and `replyRecipients.js` rather than duplicating helpers.

- [ ] Preserve inbox search cancellation sequencing, reader selection, summary collapse preference, attachment actions, tab numbering, and owner-scoped API requests.

- [ ] Run Node module tests plus `tests/test_email_library_bulk_actions.py`, `tests/test_email_owner_scope.py`, and email security/timeout tests after every extraction.

- [ ] Run the full check and commit as `refactor(email): split library rendering and window modules`.

**Acceptance criteria:** Email behavior is unchanged, reader/window state has one owner, and `emailLibrary.js` is below its ratcheted baseline.

### Task 18: Split settings, notes, and chat by existing feature boundaries

**Files:**
- Create: `static/js/settings/models.js`
- Create: `static/js/settings/search.js`
- Create: `static/js/settings/appearance.js`
- Create: `static/js/notes/reminders.js`
- Create: `static/js/notes/drafts.js`
- Create: `static/js/notes/drawing.js`
- Create: `static/js/chat/requestLifecycle.js`
- Modify: `static/js/settings.js`
- Modify: `static/js/notes.js`
- Modify: `static/js/chat.js`
- Create: `tests/test_settings_modules.mjs`
- Create: `tests/test_notes_modules.mjs`
- Create: `tests/test_chat_lifecycle.mjs`

- [ ] Extract settings at its existing model/search/appearance section boundaries. Keep one settings initializer and one endpoint-refresh registry.

- [ ] Extract notes reminders, draft persistence, and drawing behavior. Keep panel/render orchestration in `notes.js`; do not duplicate note state.

- [ ] Move chat request state, abort behavior, timeout/stall watchdog, and terminal-state transitions into `requestLifecycle.js`. Keep rendering in existing `chatRenderer.js` and transport parsing in existing `chatStream.js`.

- [ ] Add deterministic fake-clock tests for reminders, draft debounce, abort, timeout, stream completion, and retry. Run all JavaScript tests after each feature extraction.

- [ ] Run the full check and commit as `refactor(frontend): split settings notes and chat lifecycles`.

**Acceptance criteria:** Each entry module has a single orchestration responsibility, state is not duplicated, and module-size ratchets decrease.

### Task 19: Add browser-level critical-journey tests

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_first_run_login.py`
- Create: `tests/e2e/test_document_workflow.py`
- Create: `tests/e2e/test_agent_integrations.py`
- Create: `tests/e2e/test_embedded_browser.py`
- Create: `tests/e2e/test_paperclip_floor.py`
- Create: `scripts/run-e2e.sh`
- Modify: `.github/workflows/ci.yml`

- [ ] Build a session-scoped fixture that starts Apollo against a temporary data root and a deterministic local fake model/MCP endpoint. Capture sanitized server logs, screenshots on failure, and browser console errors.

- [ ] Test these complete journeys:
  1. first-run admin creation, login, logout, invalid login, and session expiry;
  2. create/open/edit/autosave/reload/export/delete a document;
  3. agent calls a deterministic tool, records task status, and exposes a recoverable MCP disconnect;
  4. embedded browser opens a local page, reads visible text, receives console output, and blocks a disallowed scheme;
  5. Paperclip preview/live floor renders agents walking, talking, sitting, completing work, and remains usable at desktop and mobile viewport sizes.

- [ ] Assert dropdowns, modal focus, disabled/loading/error button states, keyboard navigation, and absence of incoherent overlaps in the tested workflows.

- [ ] Run locally:

```bash
bash scripts/run-e2e.sh
```

- [ ] Add an Ubuntu CI job with browser caching and uploaded failure artifacts. Keep secrets absent and use only local fixtures.

- [ ] Commit as `test(e2e): cover Apollo critical user journeys`.

**Acceptance criteria:** The core product flow is proved in a real browser, including agent, browser, and Paperclip integration, with actionable failure artifacts.

### Phase 6 Checkpoint

- [ ] All Node and Python tests pass.
- [ ] E2E suite passes at desktop and mobile viewports.
- [ ] Module-size guard passes and every ratcheted baseline decreased.
- [ ] No browser console errors occur in critical journeys.
- [ ] Manual smoke confirms no visual overlap, clipped control text, broken focus order, or disconnected loading state.

---

## Phase 7: Final Production-Readiness Audit

### Task 20: Produce the release receipt and operator documentation

**Files:**
- Create: `docs/PRODUCTION_READINESS.md`
- Create: `docs/adr/2026-07-17-runtime-data-and-identity.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `README.md`
- Modify: `static/js/MODULE_SUMMARY.md`

- [ ] Document data-root resolution, migration/rollback, backup expectations, request identity modes, runner-secret lifecycle, log severity, dependency updates, CI support, and E2E commands.

- [ ] Record exact final verification results, CI run URLs, supported operating systems, skipped tests and reasons, remaining manual checks, and any accepted residual risks.

- [ ] Run the final gate from a clean working tree:

```bash
bash scripts/check.sh
APOLLO_STARTUP_SMOKE=1 bash scripts/check.sh
venv/bin/python scripts/check_runtime_paths.py
venv/bin/python scripts/audit_exception_handlers.py
venv/bin/python scripts/check_dependency_locks.py
venv/bin/python scripts/check_module_sizes.py
bash scripts/run-e2e.sh
venv/bin/python -m pip check
npm audit --omit=dev
git status --short
```

- [ ] Verify the actual native launchers on macOS and Windows and the Docker path. Keep automated evidence separate from manual device/platform evidence.

- [ ] Commit documentation as `docs: add Apollo production-readiness receipt`.

**Acceptance criteria:** Every completion metric is met, all automated checks are green, platform evidence is recorded, rollback is documented, and the branch is ready for review without uncommitted changes.

---

## Risk Controls

| Risk | Impact | Required control |
|---|---|---|
| Data migration loses or partially activates state | Critical | Copy, manifest, SQLite integrity check, atomic activation, preserve legacy source |
| Identity refactor leaks another user's data | Critical | Matrix tests for cookie/token/tool/anonymous modes and cross-owner `404`/`403` behavior |
| Token appears in scripts or logs | Critical | Private sidecar, source/log assertions, guaranteed cleanup, no command-line secret |
| Dependency lock breaks another OS | High | Fresh installs and startup smoke on Ubuntu, macOS, Windows before merge |
| Exception cleanup floods logs | Medium | Severity contract, sanitized context, debug-only best-effort tracebacks |
| Frontend split changes behavior | High | One extraction per boundary, pure-module tests, browser journeys, size ratchets |
| E2E tests touch real user data | Critical | Temporary data root and local fake providers enforced by fixture assertions |

## Terra Completion Report Template

Terra must finish with one concise report containing:

```text
Branch:
Final commit:
Tasks completed: 0-20
Python tests:
JavaScript tests:
E2E tests:
CI matrix:
Dependency audits:
Runtime path audit:
Exception audit:
Module size audit:
User data changed: no
Manual platform checks still required:
Residual risks:
```

Do not report completion while any checkbox, failed CI job, uncommitted file, undocumented skip, migration ambiguity, or manual platform requirement is hidden.
