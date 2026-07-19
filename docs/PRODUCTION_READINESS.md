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

- `APOLLO_STARTUP_SMOKE=1 bash scripts/check.sh`: 1,933 passed, 3 skipped;
  134 JavaScript tests passed; startup smoke passed.
- `bash scripts/run-e2e.sh`: 4 browser journeys passed in an isolated runtime.
- `dist/Apollo.app` was built and passed `codesign --verify --deep --strict`.
  `dist/Apollo.dmg` is a UDZO image with SHA-256
  `8660656055bcb0aac172214e60ddcbb587a40624e0ac382551a770e482042946`.
- A clean packaged macOS profile passed first-run setup, local-model chat,
  embedded browser navigation and text extraction, native Paperclip startup
  and Floor events, document editing, backup verification, restore, and
  persisted auth/session/document/model state after restart.
- Dependency locks and `pip check` pass. `npm audit --omit=dev --audit-level=high`
  reports zero vulnerabilities. `PYSEC-2026-311` for `chromadb==1.5.9` has no
  fixed upstream release. The default native and Docker configurations use
  `PersistentClient` only and do not start or publish ChromaDB's affected HTTP
  API. `scripts/check_dependency_audit.py` keeps that narrow exception visible,
  exact-versioned, and expiring on 2026-08-31; any other or stale finding fails.

## Hosted Release Evidence

- The [final macOS/Ubuntu CI and browser-journey jobs](https://github.com/Antman1526/Apollo/actions/runs/29665314060)
  passed (macOS: 1,929 passed, 4 skipped). Its Windows unit-test job was
  canceled after it exceeded the normal test duration; it is not a blocker for
  the local macOS release.
- The [Windows executable package workflow](https://github.com/Antman1526/Apollo/actions/runs/29665123196)
  passed installation, PyInstaller build, launcher `--help` smoke, checksum,
  and artifact upload. `Apollo.exe` SHA-256 is
  `ee4cc7379461875c4befd6e629d11a630c4bd31e2d5d886654ebec0d871f274f`.
- The [dependency audit workflow](https://github.com/Antman1526/Apollo/actions/runs/29664596925)
  records the unresolved `chromadb==1.5.9` advisory above and a successful
  `npm audit --omit=dev --audit-level=high` result.

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

## Remaining Optional Checks

- For a local macOS release, code signing, notarization, Windows signing, and
  auto-update are intentionally out of scope.
- Run the Windows launcher on a clean Windows host only when Windows use is
  needed. The packaged launcher build and `--help` smoke are verified, but the
  complete Windows unit-test matrix needs investigation because it exceeded
  the normal duration.
- The Docker Compose deployment was verified on 2026-07-18 in a disposable
  bind-mounted data root: image build, seeded admin login, `/api/ready`,
  embedded ChromaDB persistence with no port `8100` listener, backup verify,
  stopped-container restore, and authenticated restart persistence all passed.
  Default snapshots now persist in the host `./backups/` directory.
- Inspect the browser and Paperclip workspace manually at desktop and mobile
  sizes for clipped controls, focus order, and visual overlap.
- For public distribution later, sign and notarize the macOS app with a
  Developer ID identity, sign the Windows launcher, and add an auto-update
  strategy. The current macOS bundle is ad-hoc signed and Gatekeeper rejects
  it outside the local workflow.

## Residual Risks

Local-model availability, external MCP services, email providers, and optional
Paperclip infrastructure remain environment-dependent. The first native
Paperclip start downloads its pinned runtime and initializes an embedded
database, so it requires network access and can take several minutes. Apollo
exposes degraded state through system status rather than silently treating
missing providers as healthy. These integrations require deployment-specific
credential and connectivity validation before a production launch. The
ChromaDB advisory still needs an upstream fixed release before any deployment
deliberately enables its HTTP server; the default deployment is mitigated by
not running that server.
