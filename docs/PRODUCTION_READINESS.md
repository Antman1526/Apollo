# Production Readiness

## Scope

This receipt records the production-hardening checks for the current source
branch. It distinguishes automated repository evidence from deployment and
device checks that cannot be proven from a local checkout.

## Automated Gates

Run from the repository root with the project environment active:

```bash
bash scripts/check.sh
APOLLO_STARTUP_SMOKE=1 bash scripts/check.sh
python scripts/check_runtime_paths.py --root .
python scripts/check_dependency_locks.py
python scripts/check_module_sizes.py
python scripts/audit_exception_handlers.py --root .
python -m pip check
npm audit --omit=dev
bash scripts/run-e2e.sh
```

`scripts/run-e2e.sh` starts Apollo on an ephemeral loopback port with a
temporary SQLite database, temporary application data root, temporary
Paperclip secrets, browser automation enabled, and built-in MCP registration
disabled. It never uses a developer's real Apollo state. Set
`APOLLO_E2E_KEEP_ARTIFACTS=true` to retain the temporary server log after a
failure.

The runner exercises authenticated workspace rendering at desktop and mobile
sizes, document persistence, browser-panel scheme blocking, agent-browser DOM
access and console piping, and preview/live agent-floor rendering.

## Latest Local Evidence

Verified on 2026-07-18 from this branch:

- `APOLLO_STARTUP_SMOKE=1 bash scripts/check.sh`: 1,908 passed, 3 skipped;
  134 JavaScript tests passed; startup smoke passed.
- `bash scripts/run-e2e.sh`: 4 browser journeys passed in an isolated runtime.
- Dependency locks, `pip check`, and the production dependency audit completed
  without reported issues.

## Operational Contract

- `/api/health` proves the process is alive. `/api/ready` checks critical local
  storage readiness. `/api/system/status` is the operator diagnostic surface.
- Runtime state resolves through `APOLLO_DATA_DIR`, then `DATA_DIR`, then the
  verified migration-compatible default. Do not point an integration test or
  preview at production state.
- Authentication and session files live under the same resolved data root.
- Use `scripts/apollo-backup snapshot` before a migration or upgrade. Verify a
  backup before restore; restore intentionally overwrites the current data.
- Structured logs use the observability contract and must not contain tokens,
  passwords, browser script output, or private document contents.

## Dependency and CI Policy

- Python lock inputs are `requirements.in` and `requirements-dev.in`; generated
  locks are verified with `scripts/check_dependency_locks.py`.
- JavaScript dependencies are installed with `npm ci`; `npm audit --omit=dev`
  is a release gate requiring operator review of findings.
- CI runs Python and JavaScript checks across the supported matrix and an
  Ubuntu browser-journey job that installs Chromium before invoking the E2E
  runner.

## Remaining Manual Release Gates

- Launch the native macOS app and validate first-run setup, restore, and a
  normal workspace session on the packaged artifact.
- Run the Windows launcher on a clean Windows host.
- Build and start the Docker path with its configured volume, then validate
  `/api/ready`, backup/restore, and authenticated access behind the intended
  network boundary.
- Inspect the browser and Paperclip workspace manually at desktop and mobile
  sizes for clipped controls, focus order, and visual overlap.
- Record CI run URLs and dependency-audit output with the release candidate.

## Residual Risks

Local-model availability, external MCP services, email providers, and optional
Paperclip infrastructure remain environment-dependent. Apollo exposes their
degraded state through system status rather than silently treating missing
providers as healthy. These integrations require deployment-specific
credential and connectivity validation before a production launch.
