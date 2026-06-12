# 11 — Build & Deployment Pipeline

Apollo ships three deployment modes that all run the same ASGI app
(`uvicorn app:app`):

1. **Native desktop** — macOS `.app`/`.dmg` and Windows PowerShell launcher (no Docker).
2. **Native service** — Linux systemd unit / macOS launchd helper for headless hosts.
3. **Docker Compose** — multi-service stack with a SearXNG sidecar, ChromaDB, ntfy,
   and optional Paperclip.

The managed **SearXNG sidecar** is installed natively (no Docker) via
`scripts/setup-searxng.{sh,ps1}` for modes 1 and 2.

---

## 1. `build-macos-app.sh` — `.app` launcher + `.dmg`

Source: `/Users/Antman/Apollo/build-macos-app.sh`. Builds a **launcher wrapper**, not a
Python bundle — it drives the repo's `venv` at runtime, so the install path is baked in
at build time (`:11-13`).

- Resolves `REPO_DIR`/`INSTALL_DIR` to the repo, port defaults to `7860` (override
  `APOLLO_PORT`) (`:16-21`).
- **Bootstraps the venv** on a fresh clone if `venv/bin/uvicorn` is missing — prefers
  an arm64 Homebrew Python 3.13/3.12/3.11 on Apple Silicon, installs `requirements.txt`,
  runs `setup.py` (`:30-56`). Skip with `APOLLO_SKIP_VENV=1`.
- Builds the bundle skeleton `Apollo.app/Contents/{MacOS,Resources}` (`:62`), a
  center-cropped `.icns` from `docs/apollo.jpg` via `sips` (`:65-80`), and an
  `Info.plist` (`com.apollo.launcher`, min macOS 11.0) (`:83-101`).
- Writes the launcher script from a heredoc template, `sed`-substituting `__INSTALL_DIR__`
  and `__PORT__` (`:104-191`). The launcher:
  - exports `PAPERCLIP_MODE=native`, `PAPERCLIP_ENABLED=true` by default (`:114-115`).
  - shows a GUI dialog if the venv isn't set up (`:126-131`).
  - opens the UI in a chrome-less Chromium window (`--app=$URL --new-window`) else the
    default browser (`:134-149`).
  - if already running (`curl` probe), just opens the UI (`:153-157`).
  - else starts uvicorn on `127.0.0.1:$PORT` (under `arch -arm64` on Apple Silicon),
    traps TERM/INT to kill the server, and waits up to ~2 min for readiness — first run
    downloads an embedding model (`:159-185`).
- Packages the `.dmg` with `hdiutil … -format UDZO` and a drag-to-`/Applications`
  symlink (`:196-204`).

```bash
# build-macos-app.sh:162-165 — start uvicorn
if [ "$(uname -m)" = "arm64" ]; then
  arch -arm64 "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
else
  "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1 &
fi
```

---

## 2. `start-macos.sh` — one-command native quick start

Source: `/Users/Antman/Apollo/start-macos.sh`. For users who don't know venvs/pip.
Native (not Docker) because Cookbook serves models on the host GPU and Docker on macOS
is a Linux VM with no Metal access (`:11-13`).

- Loads `.env` so `APP_PORT`/`APP_BIND` work without retyping; shell env wins over
  `.env` (`:22-31`). Precedence: `APOLLO_PORT` → `APP_PORT` → `7860`; `APOLLO_HOST` →
  `APP_BIND` → `127.0.0.1` (`:35-36`). 7860 chosen because AirPlay holds 7000.
- Fails fast if the port is already in use via a `/dev/tcp` probe (`:48-52`).
- Requires Homebrew (points the user at the installer if missing) (`:56-63`); finds an
  arm64 Python 3.11+ under `/opt/homebrew` on Apple Silicon — a universal2/x86 Python
  produces a venv that loads extensions as the wrong architecture (`:65-84`).
- `brew_ensure` installs `tmux` and `llama.cpp` (Cookbook deps) idempotently; failures
  warn but don't abort (`:100-121`). Missing Python is fatal.
- Creates `venv/`, installs `requirements.txt`, runs `setup.py` (prints an admin
  password on first run) (`:129-146`).
- Background poller opens the browser once the port answers; prints a Tailscale URL when
  bound to `0.0.0.0` and Tailscale is present (`:156-185`).
- Launches with `PAPERCLIP_MODE=native`, `PAPERCLIP_ENABLED=true`, then
  `venv/bin/python -m uvicorn app:app --host "$HOST" --port "$PORT"` (`:202-204`).

---

## 3. `launch-windows.ps1` — native Windows launcher (no Docker)

Source: `/Users/Antman/Apollo/launch-windows.ps1`. `param([int]$Port = 7000,
[string]$BindHost = "127.0.0.1")` (`:16-19`).

- Locates Python 3.11+ via the `py` launcher (`-3.13/-3.12/-3.11`) then bare `python`
  (`:53-99`).
- Creates `venv\` if missing (`:102-110`), installs `requirements.txt` (`:112-116`),
  runs `setup.py` for first-time setup (`:118-121`).
- Warns (non-fatal) if Git Bash isn't on PATH — needed for full Cookbook downloads and
  the agent shell tool (`:123-130`).
- Sets `PAPERCLIP_MODE=native`, `PAPERCLIP_ENABLED=true`, then starts via
  `python -m uvicorn app:app --host $BindHost --port $Port` (bare `uvicorn` may not be
  on PATH) (`:132-141`).

`update_windows.bat` is the companion update helper.

---

## 4. Native service installers

### Linux systemd — `apollo-ui.service` + `install-service.sh`

`apollo-ui.service` is a template (`:1-2` say to copy to `/etc/systemd/system/` and edit
`User`/paths):

```ini
# apollo-ui.service:6-16
[Service]
Type=simple
User=YOURUSER
WorkingDirectory=/home/YOURUSER/apollo-ui
ExecStart=/home/YOURUSER/apollo-ui/venv/bin/uvicorn app:app --port 7000 --host 0.0.0.0
Restart=always
RestartSec=3
EnvironmentFile=-/home/YOURUSER/apollo-ui/.env
```

`install-service.sh` copies the unit, then `daemon-reload`, `enable`, `start`, `status`
(`install-service.sh:16-20`). It errors out if the unit file is missing (`:7-10`).

### macOS launchd

`scripts/` contains the launchd plist/helper used by the global memory note's
`install-service` flow for headless macOS; the desktop path is the `.app` launcher
above. (The `.app` launcher itself supervises Paperclip in native mode.)

---

## 5. Docker / Docker Compose

### `Dockerfile`

Source: `/Users/Antman/Apollo/Dockerfile`. `FROM python:3.12-slim`. System deps:
`build-essential`, `cmake`, `curl`, `git`, `nodejs`, `npm`, `tmux`, `openssh-client`,
`gosu` — needed by Cookbook (background serves, remote SSH, llama.cpp builds), the
Browser MCP (`npx`), and privilege-dropping (`gosu`) (`:11-21`). Installs
`requirements.txt` in a cache layer, copies the app, creates `data logs
services/cache/search` (`:25-33`), `EXPOSE 7000`, and runs through
`docker/entrypoint.sh` which drops to `PUID/PGID` and chowns the bind mounts so
host-editable files don't end up root-owned (`:35-47`).

```dockerfile
# Dockerfile:46-47
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
```

### `docker-compose.yml`

Source: `/Users/Antman/Apollo/docker-compose.yml`. Services:

- **`apollo`** — built from the Dockerfile; published as
  `${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000` (`:4-5`). Bind-mounts `./data`,
  `./logs`, SSH identity, HF cache, and Cookbook-installed CLIs (`:6-18`).
  `host.docker.internal` extra host reaches host Ollama (`:19-22`). Sets
  `SEARXNG_INSTANCE=http://searxng:8080`, `CHROMADB_HOST=chromadb`, plus all the
  pass-through provider keys and `PUID/PGID` (`:23-70`). `depends_on` waits for
  `searxng` **healthy** and `chromadb` started (`:71-76`).
- **`chromadb`** — `chromadb/chroma:latest`, vector store, host port 8100→8000,
  telemetry off (`:78-86`).
- **`searxng`** — the sidecar in Docker mode (see below) (`:88-136`).
- **`ntfy`** — `binwiederhier/ntfy serve` for push notifications, 8091→80 (`:138-147`).
- **`paperclip-db` + `paperclip`** — opt-in `profiles: ["paperclip"]`: Postgres 17 +
  the Paperclip app built from a pinned GitHub ref, reached only through Apollo's
  `/paperclip` reverse proxy (no host port) (`:149-196`).

#### SearXNG sidecar (Docker)

Pinned image `searxng/searxng:2026.5.31-7159b8aed`, **not** `:latest` — because
`apollo` blocks on its healthcheck, a broken `latest` would block the whole app
(`:88-94`; the comment cites `2026.6.2` crashing on `KeyError: 'default_doi_resolver'`).
A wrapper entrypoint renders `settings.yml` from a template, generating a secret if
`SEARXNG_SECRET` is unset (`:95-107`). It drops all caps and adds only
`CHOWN/SETGID/SETUID/DAC_OVERRIDE` for first-boot setup (`:123-129`), with a urllib-based
healthcheck (`:130-135`).

---

## 6. SearXNG native sidecar installers

### `scripts/setup-searxng.sh` (macOS/Linux)

Source: `/Users/Antman/Apollo/scripts/setup-searxng.sh`. Installs SearXNG into
`data/searxng/` — idempotent (`:2-4`).

- `PORT=${SEARXNG_PORT:-8893}`, validated as an integer (`:14-19`).
- `REF=${SEARXNG_GIT_REF:-4dd0bf48670727f6ae1086ffa72e76f6eb869741}` — a **pinned**
  commit smoke-tested healthz + JSON search on 2026-06-11 (`:21-24`).
- Clones `searxng/searxng` into `data/searxng/src`, prefers the local object on offline
  re-runs and only fetches the bare SHA when missing (`:28-38`).
- Builds `data/searxng/venv`, installs SearXNG's `requirements.txt`, then the package
  with `--use-pep517 --no-build-isolation` (`:40-50`).
- On first install only, writes a localhost-only `settings.yml` with a random
  `secret_key`, `bind_address: 127.0.0.1`, the port, `limiter: false`,
  `public_instance: false`, and both `html` + `json` formats (`:52-69`).

```bash
# scripts/setup-searxng.sh:52-68 (excerpt)
SECRET="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"
cat > "$HOME_DIR/settings.yml" <<EOF
use_default_settings: true
server:
  secret_key: "$SECRET"
  bind_address: "127.0.0.1"
  port: $PORT
  limiter: false
  public_instance: false
search:
  formats: [html, json]
EOF
```

### `scripts/setup-searxng.ps1` (Windows)

Source: `/Users/Antman/Apollo/scripts/setup-searxng.ps1`. Same logic in PowerShell:
same default port 8893 and same pinned `REF`
`4dd0bf48670727f6ae1086ffa72e76f6eb869741` (`:21,38`), integer-validated port
(`:30-33`), clone + pinned checkout with offline-first fetch (`:45-62`), Python 3.10+
discovery via the `py` launcher (`:64-100`), venv + `--use-pep517 --no-build-isolation`
install (`:102-115`), and a first-install-only `settings.yml` written as UTF-8
(`:117-138`).

Once installed, **Apollo starts the sidecar itself** on FastAPI startup — a daemon
thread calls `get_runtime().start()`, and shutdown calls `stop()`
(`app.py:899-914`). The installers print a manual-start hint
(`setup-searxng.sh:72`, `setup-searxng.ps1:141`).

---

## 7. CI (`.github/workflows/ci.yml`)

The same workflow that gates tests also acts as the build/integration check: on every
PR and `push` to `main` it sets up Python 3.12, installs deps, **compiles** the package
(`python -m compileall …`, `ci.yml:29`) as a fast syntax gate, runs `pytest -q`, then
sets up Node 20 and runs `npm run test:js` (`ci.yml:1-44`). There is no separate
artifact-publishing job — the desktop bundles (`.app`/`.dmg`) are produced locally by
`build-macos-app.sh`.
