# 11 — Build & Deployment Pipeline

Apollo ships three deployment modes that all run the same ASGI app
(`uvicorn app:app`):

1. **Native desktop** — macOS launcher `.app`/`.dmg` (§1), a **self-contained
   PyInstaller** `.app`/`.dmg` (§1b), and a Windows PowerShell launcher / `Apollo.exe`
   (§1b, §3) — all no-Docker.
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

> **Two macOS build scripts, two purposes.** `build-macos-app.sh` (this section)
> produces a *thin launcher* `.app`/`.dmg` that drives **this repo's `venv`** at
> runtime — the install path is baked in, so the target machine must have the repo.
> `build-macos-bundle.sh` (§1b) produces a **self-contained** PyInstaller bundle
> that ships Python + every dependency and runs on any Apple-Silicon Mac with no
> repo and no preinstalled venv.

---

## 1b. `build-macos-bundle.sh` + `packaging/` — self-contained PyInstaller bundle

> **Which build to ship.** `build-macos-bundle.sh` (this section) is the
> **distributable** one: a self-contained PyInstaller `.dmg` (~233 MB) that ships
> Python + every dependency and runs on any Apple-Silicon Mac with **no repo and no
> preinstalled venv**. Ship this to end users. `build-macos-app.sh` (§1) is a *thin
> launcher* that drives this repo's `venv` at runtime — use it for **local dev only**,
> not distribution. Before handing the self-contained `.dmg` to other Macs it needs a
> real **Developer-ID signature + notarization** (the build only ad-hoc signs, which
> just satisfies Gatekeeper on the build machine — see §1b signing note below).

Source: `/Users/Antman/Apollo/build-macos-bundle.sh`,
`/Users/Antman/Apollo/packaging/apollo.spec`,
`/Users/Antman/Apollo/packaging/apollo_boot.py`. Produces
`dist/Apollo.app` (**~534 MB**) and `dist/Apollo.dmg` (**~233 MB**) that run
**without the repo or a preinstalled venv** — Python and all deps are frozen in
(`build-macos-bundle.sh:1-15`).

### The PyInstaller onedir (`packaging/apollo.spec`)

PyInstaller's static analysis misses native/data-heavy packages, so the spec
`collect_all(...)`s each one explicitly (`apollo.spec:16-44`):

```python
# packaging/apollo.spec:18-44 (excerpt)
for pkg in (
    "chromadb", "onnxruntime", "fastembed", "tokenizers",
    "cryptography", "pydantic", "pydantic_core", "crawl4ai", "mcp",
    "caldav", "icalendar", "markdown", "qrcode", "pyotp",
    "huggingface_hub", "tqdm", "certifi",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:
        print(f"[apollo.spec] collect_all({pkg!r}) skipped: {exc}")
```

- **uvicorn**'s dynamically-imported protocol/lifespan/loop workers are pinned as
  hidden imports (`apollo.spec:45-57`).
- The app imports `routes`/`services`/`core`/`src`/`companion`/`mcp_servers`/`config`
  at startup, so whole trees are pulled via `collect_submodules` (`:59-62`).
- Resource trees `static/` and `config/` plus the small seed JSON
  (`auth.json`, `presets.json`, `features.json`, `settings.json`, `memory.json`,
  `user_prefs.json`) ship as `datas` so CWD-/BASE_DIR-relative lookups resolve
  (`:64-85`).
- The `EXE`/`COLLECT` target is `name="apollo"`, `console=True`,
  `target_arch="arm64"` (`:106-131`). Entry point is `packaging/apollo_boot.py`
  (`:90`).

### The boot shim (`packaging/apollo_boot.py`)

A read-only app bundle can't write its DB/chroma/uploads where `core/constants.py`
expects (next to `__file__`), so the shim relocates everything to a writable
per-user home **without editing any application source** (`apollo_boot.py:1-26`):

1. **Writable home** = `~/Library/Application Support/Apollo` (override with
   `APOLLO_HOME`) (`:47-52`).
2. **First-run seed** — `static/` is symlinked into the read-only bundle (falling
   back to a copy); the small `data/` seed JSON is copied (never clobbering an
   existing home); writable subdirs (`uploads`, `chroma`, `rag`, `skills`, …) are
   created (`_seed_home`, `:55-105`).
3. **`os.chdir(home)`** then **monkeypatch `core.constants`** BASE_DIR/DATA_DIR/
   STATIC_DIR (and the derived SESSIONS_FILE/MEMORY_FILE/UPLOAD_DIR/… constants)
   **before the app module is imported**, so every `from core.constants import
   DATA_DIR` binds to the writable path (`_patch_constants`, `:108-125`).
4. **DB / caches** — `DATABASE_URL` is `setdefault`-ed to
   `sqlite:///<home>/data/app.db`; `HF_HOME` and `FASTEMBED_CACHE_PATH` likewise
   default inside the home (`:145-151`).
5. **Run uvicorn programmatically** by importing the **ASGI app object** directly
   (`from app import app as asgi_app`) rather than the `"app:app"` import string,
   because a frozen bundle can't re-import the top-level `app` module by string
   (`:155-166`):

```python
# packaging/apollo_boot.py:162-166
from app import app as asgi_app
port = int(os.environ.get("APOLLO_PORT", "7860"))
host = os.environ.get("APOLLO_HOST", "127.0.0.1")
uvicorn.run(asgi_app, host=host, port=port, log_level="info")
```

### Assembling `Apollo.app` + `.dmg` (`build-macos-bundle.sh`)

- Ensures `pyinstaller` is in the venv, then runs the onedir build
  (`pyinstaller packaging/apollo.spec --noconfirm --distpath dist --workpath build`,
  `build-macos-bundle.sh:31-47`).
- Copies the onedir under `Apollo.app/Contents/Resources/apollo`, builds a
  center-cropped `.icns` from `docs/apollo.jpg` via `sips`, and writes an
  `Info.plist` (`com.apollo.bundle`, min macOS 11.0) (`:49-90`).
- Writes the launcher script from a heredoc, `sed`-substituting `__PORT__`. The
  launcher exports `APOLLO_PORT` and **pins `DATABASE_URL`** to the app's own
  SQLite DB so a stray `DATABASE_URL` in the GUI/login environment can't be
  inherited (the boot shim uses `setdefault`, so this must be set or the frozen app
  crashes with `NoSuchModuleError`) (`:104-109`):

  ```bash
  # build-macos-bundle.sh:104-109
  export APOLLO_PORT="$PORT"
  export DATABASE_URL="sqlite:///$HOME_DIR/data/app.db"
  ```

  It then health-checks `$URL/api/health` for up to ~3 min on first run (the
  FastEmbed model download), opens the UI in a chrome-less Chromium window, and
  kills the server on TERM/INT (`:111-163`).
- **Ad-hoc signs** the app (`codesign --force --deep --sign -`) so Gatekeeper
  allows launch **on the build machine** (`:171-173`). *Note: ad-hoc signing is not
  enough for distribution — a real Developer-ID signature + notarization is needed
  to run on other users' machines without Gatekeeper prompts.*
- Packages the `.dmg` with `hdiutil … -format UDZO` and a drag-to-`/Applications`
  symlink (`:178-187`).

### Windows launcher (`scripts/windows_launcher.py` + CI)

Source: `/Users/Antman/Apollo/scripts/windows_launcher.py`. The Windows parallel is
a **launcher** (like the macOS `.dmg` launcher), *not* a self-contained bundle. It
locates the Apollo project dir, runs the maintained `launch-windows.ps1` (venv +
install + first-run `setup.py` + start), and opens the browser once the port
answers (`windows_launcher.py:1-16, 74-100`). It falls back to starting uvicorn
from an existing venv if the PS1 is missing (`:88-100`).

It is compiled to a single `Apollo.exe` by a **manual-only** GitHub Actions job
(`.github/workflows/build-windows-exe.yml`) on `windows-latest`:

```yaml
# .github/workflows/build-windows-exe.yml:26-29
- name: Build Apollo.exe (launcher)
  # --console keeps the window open so the first-run admin password and
  # server logs are visible. --onefile produces a single Apollo.exe.
  run: pyinstaller --onefile --console --name Apollo scripts/windows_launcher.py
```

The workflow is `workflow_dispatch`-only (kept off push/PR so it never slows normal
CI) and uploads `dist/Apollo.exe` as a 90-day artifact (`:5-6, 31-37`).

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
artifact-publishing job on the main CI — the macOS desktop bundles (`.app`/`.dmg`)
are produced locally by `build-macos-app.sh` (launcher) and `build-macos-bundle.sh`
(self-contained). The one build job that *does* publish an artifact is the
**manual-only** `build-windows-exe.yml` (§1b), which compiles `Apollo.exe` on
`windows-latest` on demand.

## 8. 2026-07-19 release-path refresh

The self-contained macOS bundle was verified from a mounted read-only DMG:
runtime state, search cache, analytics, and logs write below the application
data root, not the bundle. `build-macos-bundle.sh` remains Apple-Silicon and
ad-hoc signed for local use; Developer ID signing/notarization is still a
separate public-distribution gate. Docker default deployments persist data,
logs, and backups as host bind mounts, use embedded ChromaDB, and were tested
through seeded login, `/api/ready`, backup verify/restore, and restart
persistence. See `docs/PRODUCTION_READINESS.md` for checksums and evidence.
