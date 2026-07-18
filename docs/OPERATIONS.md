# Operations

This note covers the runtime checks and recovery paths that are useful before
shipping or debugging a local deployment.

## Health Checks

- `GET /api/health` is a liveness check. It answers whether the process is up.
- `GET /api/ready` is the critical readiness check for storage and data paths.
- `GET /api/system/status` is the operator view. It is admin-gated and reports
  storage, auth, memory, email, documents, models, search indexes, external tool
  servers, terminal capability, and background work.

Use `/api/system/status` first when the product feels disconnected: it shows
which subsystem is degraded and includes the next practical step where the app
can infer one.

## Startup Diagnostics

Route registration is wrapped with labeled startup errors. If startup fails,
look for a message like:

```text
Failed to register <name> routes
Failed to build <name> routes
```

That label narrows the failure to a route group before you inspect the traceback.

## Verification

Run the full local quality gate before handing off changes:

```bash
bash scripts/check.sh
```

That compiles Python, runs the Python suite, and runs the JavaScript smoke tests.

Dependency inputs live in `requirements.in` and `requirements-dev.in`; their
fully pinned outputs are `requirements.txt` and `requirements-dev.txt`. Verify
that locks are current without modifying the checkout:

```bash
python -m pip install pip-tools
python scripts/check_dependency_locks.py
```

## Logs

List known logs:

```bash
scripts/apollo-logs list
```

Tail a log:

```bash
scripts/apollo-logs tail apollo-app --lines 120
```

Clean temporary run logs older than seven days:

```bash
scripts/apollo-logs clean
```

Runtime/app/browser log cleanup is dry-run by default:

```bash
scripts/apollo-logs clean --scope runtime
scripts/apollo-logs clean --scope all --apply
```

Use `--apply` only after reviewing the dry-run list.

## Recovery Order

1. Check `/api/system/status`.
2. Run `bash scripts/check.sh` if code changed.
3. Inspect the route label if startup failed.
4. Tail the relevant log with `scripts/apollo-logs`.
5. For background work, inspect queued/running runs and stuck-run counts in the
   system status payload before retrying or cancelling tasks.
