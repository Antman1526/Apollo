# ADR: Runtime Data and Request Identity

## Status

Accepted.

## Context

Apollo runs as a native application, in containers, and from source checkouts.
Persisting state relative to the current working directory caused data to move
between launch modes. Feature routes also needed a consistent interpretation of
cookie, token, local-bypass, and internal-tool callers.

## Decision

- `src.runtime_paths.data_root()` is the canonical resolver for application
  state. `APOLLO_DATA_DIR` takes precedence, then `DATA_DIR`; otherwise Apollo
  uses an activated platform location or preserves an existing legacy `data/`
  directory.
- Authentication state uses that resolver as well. A configured data root
  therefore contains the session and account state for the same runtime.
- Legacy migration is copy-verify-activate. It records a receipt, verifies
  SQLite copies, and keeps the legacy source untouched for rollback.
- Routes resolve ownership through the shared request-identity helpers. A
  caller's authentication mode determines whether owner-scoped data is
  filtered; privileged routes enforce their named privilege explicitly.

## Consequences

- Native launchers, Docker, and E2E runners must set an explicit data root
  whenever they require isolation.
- Backups must cover the resolved data root, including `auth.json`, the
  SQLite database, uploads, and generated application state.
- Operators can roll back a data-root migration by stopping Apollo, retaining
  the activated target as evidence, and restarting against the intact legacy
  source or an explicit backup root.

## Verification

Run `python scripts/check_runtime_paths.py --root .` and the data-migration
tests before shipping a storage or identity change.
