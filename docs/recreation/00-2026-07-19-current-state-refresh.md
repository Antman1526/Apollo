# Apollo - Current-State Refresh (2026-07-19)

This document is the dated delta for the 15 reconstruction documents in this
directory. It was revalidated against commit `739fcaa` after the production
hardening, Docker, packaging, ownership, observability, frontend-modularity,
and local-model integration work. Read it with documents 01-15; it corrects
older descriptions rather than replacing their detailed subsystem walkthroughs.

## 1. Storage is now a runtime contract, not a checkout assumption

All persistent application state resolves through `src/runtime_paths.py`.
Explicit environment configuration wins, then a verified platform-data
migration, then the legacy checkout-local `data/` directory. This preserves
existing installations while making packaged apps safe to run from read-only
DMGs and future relocatable installs.

```python
# src/runtime_paths.py
def data_root(*, env=None, repo=None, platform=None, home=None) -> Path:
    env = os.environ if env is None else env
    for key in ("APOLLO_DATA_DIR", "DATA_DIR"):
        value = env.get(key)
        if value:
            return _configured_path(value)  # explicit operator override
    platform_root = platform_data_root(platform=platform, env=env, home=home)
    if _platform_root_is_activated(platform_root):
        return platform_root                # verified migration target
    legacy = legacy_data_root(repo)
    if legacy.exists():
        return legacy                       # preserve an existing checkout
    return platform_root                    # clean installs use platform state
```

On macOS the platform root is `~/Library/Application Support/Apollo`; Windows
uses `%LOCALAPPDATA%/Apollo`; Linux uses `$XDG_DATA_HOME/apollo` or
`~/.local/share/apollo`. `APOLLO_DATA_DIR` is the required isolation switch for
tests, previews, and packaged-smoke runs. The self-contained macOS boot shim
sets it to `<Apollo home>/data` before importing the application.

The hard rule for reconstructors is: no user-created cache, log, JSON file,
SQLite database, vector store, upload, or model cache may be derived from a
module's `__file__` path. The July package failure came from that exact error.
Search cache and analytics now use `data_path("search_cache")` and
`data_path("search")`, so a read-only mounted app has no write path inside the
bundle.

## 2. Vector memory defaults to embedded ChromaDB

Native and Docker deployments use `chromadb.PersistentClient` under the
resolved data root. Docker sets `CHROMA_PERSIST_DIR=/app/data/chroma` and no
longer starts or publishes a Chroma HTTP container. An HTTP client is only
selected when an operator explicitly sets `CHROMADB_HOST`.

```python
# src/chroma_client.py
host = os.getenv("CHROMADB_HOST", "").strip()
if host:
    port = int(os.getenv("CHROMADB_PORT", "8000"))
    if not _port_open(host, port):
        raise RuntimeError(f"ChromaDB is not reachable at {host}:{port}.")
    client = chromadb.HttpClient(host=host, port=port)
    client.heartbeat()
else:
    path = _persist_dir()
    os.makedirs(path, exist_ok=True)
    client = chromadb.PersistentClient(path=path)
```

This is both a deployment simplification and a temporary mitigation for
`PYSEC-2026-311` / `CVE-2026-45829`, a pre-auth Chroma HTTP-server advisory
without an upstream fix at the documented lock version. The exception is narrow
and expires on 2026-08-31 in
`security/dependency-audit-exceptions.json`; enabling an external Chroma HTTP
server deliberately reintroduces a review obligation.

## 3. Docker persistence, first-run setup, and recovery are explicit

The Compose service persists `./data`, `./logs`, and `./backups`; the latter is
the default output for `scripts/apollo-backup`. The entrypoint repairs ownership
for the configured `PUID`/`PGID`, validates an optional seeded admin password
(minimum eight characters), and seeds the account only when no account exists.
No Chroma port `8100` is part of the default Compose topology.

`apollo-backup snapshot`, `verify`, `list`, and `restore PATH --yes` are the
operator contract. Backups use SQLite's backup API when possible. Restore is
intentionally destructive, validates tar member paths, supports the Docker bind
mount fallback, and must be performed against a stopped application container
when replacing live state.

## 4. Identity and handled failures have central contracts

Routes should resolve the effective owner through `src.auth_helpers`, not read
ad hoc request fields. `require_user` permits explicit single-user desktop
mode (`AUTH_ENABLED=false`) while still rejecting anonymous multi-user calls.
Owner-scoped queries use `owner_filter` so user rows and shared rows are treated
consistently.

Handled exceptions must use `src.observability.report_exception` with one of
three outcomes: `critical`, `degraded`, or `best_effort`. Diagnostic metadata is
shallow and rejects field names containing `token`, `password`, `secret`,
`body`, or `content` before logging.

```python
# src/observability.py
record = {
    "event": event,
    "outcome": outcome,
    "error_type": type(error).__name__,
    **sanitize_context(context),
}
```

This deliberately omits exception messages from structured records because
upstream libraries can echo URLs, credentials, prompt content, or document data.

## 5. Packaging and verification evidence

There are two macOS build modes: the small repo/venv launcher and the
self-contained PyInstaller build (`build-macos-bundle.sh`). The latter wraps the
frozen runtime in `Apollo.app`, writes state under Application Support, bundles
Chromium for the browser panel, and produces a UDZO DMG. It is ad-hoc signed for
local use only; public distribution still needs Developer ID signing and
notarization.

The self-contained bundle was tested from a **read-only mounted DMG** after the
search-path correction. `/api/health` returned healthy and search cache,
analytics, and log files were created under the temporary writable app home,
not under `/Volumes/.../Apollo.app`.

Fresh local evidence is recorded in `docs/PRODUCTION_READINESS.md`:

- `APOLLO_STARTUP_SMOKE=1 bash scripts/check.sh`: 1,934 Python passed, 3 skipped.
- `npm run test:js`: 134 JavaScript tests passed.
- `bash scripts/run-e2e.sh`: four isolated browser journeys.
- Docker: image build, seeded login, `/api/ready`, no Chroma port listener,
  backup verify/restore, and restart persistence were exercised.

## 6. Current repository shape and main trade-offs

`app.py` is a 1,296-line orchestrator, not the historical multi-thousand-line
description found in older review notes. The backend currently has 55 Python
files under `routes/`, 84 top-level `src/` modules, and 23 CSS files under
`static/css/`; the frontend remains vanilla ES modules with no bundler.

The July refactors extracted pure frontend seams for chat lifecycle, document
editing, notes drafts, email attachment/reader state, settings model policy,
Paperclip floor rendering, and CSS layout bands. The deliberate trade-off is
still a no-build static deployment, so service-worker cache naming and module
loading are manually managed. The strongest remaining architectural candidates
for review are provider normalization, long-running agent/subprocess lifecycle,
SQLite/Chroma consistency, and a future asset pipeline that removes manual PWA
precache coordination.

## 7. Reconstructor checklist

1. Generate runtime storage through `data_path`, never source-relative paths.
2. Keep the default vector store embedded and persisted with the application
   data directory.
3. Treat `AUTH_ENABLED=false` as a local single-user mode only; bind remote
   deployments deliberately with auth enabled.
4. Run the full check script, E2E runner, lock verifier, dependency audit, and
   read-only package smoke before claiming a desktop artifact works.
5. Keep secrets out of logs and child process environments; use existing
   `build_agent_env` and observability helpers rather than raw `os.environ` or
   raw exception logging.
