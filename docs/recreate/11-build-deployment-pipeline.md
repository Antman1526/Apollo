# Apollo — Build & Deployment Pipeline

Apollo deploys four ways from one codebase: Docker Compose (Linux servers), a native
macOS launcher app (`.app` + `.dmg`), a native Windows launcher (`Apollo.exe` →
PowerShell bootstrap), and a systemd service. There is no frontend build step — the UI
ships as raw ES modules under `static/`, which shapes the cache-busting story (§8).
Every path funnels through the same gate: `scripts/check.sh`.

## 1. The check gate

`scripts/check.sh` is the pre-merge / pre-release gate. It prefers the repo venv,
byte-compiles all first-party Python (catching syntax errors in never-imported files),
runs pytest, then the Node suites:

```bash
# scripts/check.sh
"$PYTHON" -m compileall -q app.py companion core routes services src scripts/apollo-ralph scripts/check-paperclip-browser
"$PYTHON" -m pytest -q
npm run test:js
```

CI (`.github/workflows/ci.yml`) mirrors it exactly — `actions/setup-python@v5` (3.12,
pip cache) → `pip install -r requirements.txt` → the same `compileall` line →
`python -m pytest -q` → `actions/setup-node@v4` (Node 20, npm cache) → `npm ci` →
`npm run test:js`. Triggers: `pull_request` and pushes to `main`.

## 2. Docker path

### 2.1 Dockerfile

`Dockerfile` is `python:3.12-slim` plus system deps each with a stated reason: `tmux`
(Cookbook background downloads/serves), `openssh-client` (Cookbook remote servers),
`git`/`cmake`/`build-essential` (llama.cpp builds inside Docker), `nodejs`/`npm` (the
optional built-in Browser MCP server), and `gosu` for clean privilege drop:

```dockerfile
# Dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data logs services/cache/search
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
EXPOSE 7000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
```

`docker/entrypoint.sh` implements the PUID/PGID pattern: it creates an `apollo`
user/group at `${PUID:-1000}:${PGID:-1000}`, chowns the writable bind mounts on every
start (repairing root-owned files from older runs), and `exec gosu`-drops into uvicorn
so signals reach it directly — no `su`/`sudo` shell layer.

### 2.2 docker-compose.yml services

| Service | Image | Host port | Notes |
|---|---|---|---|
| `apollo` | built from `.` | `${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000` | depends on searxng (healthy) + chromadb (started); `restart: unless-stopped` |
| `chromadb` | `chromadb/chroma:latest` | `127.0.0.1:8100:8000` | volume `chromadb-data` |
| `searxng` | `searxng/searxng:2026.5.31-7159b8aed` (pinned) | `127.0.0.1:8080:8080` | wrapper entrypoint injects `SEARXNG_SECRET` into settings template; healthcheck = Python urlopen, 20 retries; `cap_drop: ALL` + re-add CHOWN/SETGID/SETUID/DAC_OVERRIDE |
| `ntfy` | `binwiederhier/ntfy` | `127.0.0.1:8091:80` | push notifications |
| `paperclip-db` | `postgres:17-alpine` | none | **profile `paperclip`**; healthcheck `pg_isready`, volume `paperclip-pgdata` |
| `paperclip` | built from `https://github.com/paperclipai/paperclip.git#v2026.529.0` | none | **profile `paperclip`**; reached only via Apollo's `/paperclip` reverse proxy |

The searxng tag is pinned deliberately — `apollo` waits on its healthcheck, so a broken
upstream `latest` blocked the whole app from starting (compose comment cites issue
#1414, where `2026.6.2` crashed with `KeyError: 'default_doi_resolver'`).

Apollo's bind mounts: `./data:/app/data`, `./logs:/app/logs`, `./data/ssh:/app/.ssh`
(Cookbook SSH identity), `./data/huggingface:/app/.cache/huggingface`, and
`./data/local:/app/.local` (Cookbook-installed serve engines survive container
recreation). `extra_hosts: host.docker.internal:host-gateway` lets the container reach
host Ollama at `:11434`.

The Paperclip sidecar is opt-in: `PAPERCLIP_ENABLED=true` in `.env` plus
`docker compose --profile paperclip up`. Its env wires
`DATABASE_URL: postgres://paperclip:paperclip@paperclip-db:5432/paperclip`,
`PAPERCLIP_DEPLOYMENT_MODE: authenticated`, `BETTER_AUTH_SECRET:
"${PAPERCLIP_AUTH_SECRET:-}"` (soft default so a plain `up` never fails on a missing
var), and `OPENAI_BASE_URL` defaulting to host Ollama.

```bash
docker compose up -d                          # core stack
docker compose --profile paperclip up -d      # + Paperclip sidecar
```

## 3. Native macOS

### 3.1 build-macos-app.sh

`./build-macos-app.sh` produces `dist/Apollo.app` and `dist/Apollo.dmg`. It is a
**launcher wrapper**, not a bundler: it does not ship Python; the absolute repo path
and port (`APOLLO_PORT`, default 7860 — macOS AirPlay holds 7000) are baked in via
`sed` at build time, so the app must be rebuilt if the repo moves. Walkthrough:

1. **Venv bootstrap (first build only).** If `venv/bin/uvicorn` is missing (and
   `APOLLO_SKIP_VENV` unset), find a Python 3.11+ — on arm64 only under
   `/opt/homebrew/bin` (a universal2/x86 python.org build produces wrong-architecture
   compiled extensions when launched from the .app) — then
   `python -m venv venv`, `pip install -r requirements.txt`, and
   `APOLLO_SKIP_RUN_HINT=1 setup.py`.
2. **Bundle skeleton.** `mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"`.
3. **Icon from docs/apollo.jpg via sips.** Center-crop to square, scale to 512 (sips'
   icns encoder caps there), emit `.icns` directly:

```bash
# build-macos-app.sh
sips -c 720 720 "$REPO_DIR/docs/apollo.jpg" --out "$TMPIMG/sq.png"
sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png"
sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/apollo.icns"
```

4. **Info.plist** — heredoc with `CFBundleIdentifier com.apollo.launcher`,
   `CFBundleExecutable Apollo`, `CFBundleIconFile apollo`,
   `LSMinimumSystemVersion 11.0`, `NSHighResolutionCapable true`.
5. **Launcher executable** (`Contents/MacOS/Apollo`, templated then `chmod +x`):
   exports `PAPERCLIP_MODE=native` / `PAPERCLIP_ENABLED=true`; if
   `curl http://127.0.0.1:$PORT` already answers it just opens the UI; otherwise starts
   `arch -arm64 "$UVICORN" app:app --host 127.0.0.1 --port "$PORT"` with output
   appended to `logs/apollo-app.log`, traps TERM/INT to kill the server (quitting the
   app stops Apollo), polls readiness for up to 120s (first run downloads an embedding
   model), then opens the UI chrome-less via a Chromium browser's `--app=$URL` flag
   (Chrome/Edge/Brave/Chromium probed in `/Applications` and `~/Applications`), falling
   back to `open "$URL"`. GUI errors surface through `osascript` dialogs.
6. **DMG.** Stage the .app plus an `/Applications` symlink, then
   `hdiutil create -volname Apollo -srcfolder "$STAGE" -ov -format UDZO dist/Apollo.dmg`.

### 3.2 start-macos.sh

Terminal-based one-command start, idempotent on re-run. Order of operations: parse
`.env` (shell vars win, then `.env`, then defaults — port resolution is
`APOLLO_PORT → APP_PORT → 7860`, host `APOLLO_HOST → APP_BIND → 127.0.0.1`); fail fast
if the port is taken (`/dev/tcp` probe); require Homebrew (prints the install
one-liner, never auto-installs); pick an arm64 Homebrew Python 3.13/3.12/3.11;
`brew_ensure tmux` and `brew_ensure llama-server llama.cpp` (warn-don't-abort — only
Cookbook needs them); create `venv/` + install requirements; run `setup.py`
(`APOLLO_SKIP_RUN_HINT=1`); background-poll the port and `open "$URL"` when ready
(skippable with `APOLLO_NO_OPEN=1`, prints a Tailscale URL when bound to 0.0.0.0);
finally `exec` into `venv/bin/python3 -m uvicorn app:app` with
`PAPERCLIP_MODE=native PAPERCLIP_ENABLED=true` defaults. The script's header explains
why native, not Docker: Docker on macOS is a Linux VM with no Metal GPU access, so
Cookbook could not serve GGUF models on the GPU.

## 4. Native Windows

### 4.1 launch-windows.ps1

PowerShell 5.1+ bootstrap (`-Port 7000 -BindHost 127.0.0.1` params). Steps: locate
Python 3.11+ via the `py` launcher (`-3.13/-3.12/-3.11`) or bare `python`; create
`venv\` if missing; `pip install -r requirements.txt`; run `setup.py` (first run prints
the admin password); warn if Git Bash is absent (Cookbook background downloads and the
agent shell tool want `bash.exe`, core app works without); start
`& $venvPy -m uvicorn app:app --host $BindHost --port $Port` with the same
`PAPERCLIP_MODE=native` defaults. `update_windows.bat` handles pull-and-update.

### 4.2 The double-clickable Apollo.exe

`scripts/windows-launcher/apollo_launcher.c` is a ~57-line Win32 program: it resolves
its own directory, verifies `launch-windows.ps1` sits next to it (MessageBox error
otherwise), and spawns the bootstrap in a **visible** console
(`CREATE_NEW_CONSOLE` — first run prints the admin password there):

```c
/* scripts/windows-launcher/apollo_launcher.c */
wsprintfW(cmd,
    L"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%s\"",
    script);
CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NEW_CONSOLE, NULL, dir, &si, &pi);
```

Cross-compiled from macOS/Linux exactly as the file header documents:

```bash
x86_64-w64-mingw32-gcc -O2 -municode -mwindows \
  -o Apollo.exe scripts/windows-launcher/apollo_launcher.c
```

(`-municode` for the `wWinMain` wide-char entry point, `-mwindows` for a GUI-subsystem
binary with no console of its own.)

## 5. systemd service (Linux)

`apollo-ui.service` is a template — edit `User`, `WorkingDirectory`, `ExecStart`:

```ini
# apollo-ui.service
[Service]
Type=simple
User=YOURUSER
WorkingDirectory=/home/YOURUSER/apollo-ui
ExecStart=/home/YOURUSER/apollo-ui/venv/bin/uvicorn app:app --port 7000 --host 0.0.0.0
Restart=always
RestartSec=3
EnvironmentFile=-/home/YOURUSER/apollo-ui/.env
```

`install-service.sh` copies it to `/etc/systemd/system/`, then
`daemon-reload` → `enable` → `start` → `status`. The leading `-` on
`EnvironmentFile` makes a missing `.env` non-fatal.

## 6. Releases and versioning

- The app version is a single constant: `APP_VERSION = "0.9.1"` in
  `core/constants.py`, served by `GET /api/version` (auth-exempt) from `app.py`.
- Git tags exist (`v1.0`) but `SECURITY.md` states the actual policy: *"Security fixes
  are handled on the default branch until formal releases are cut."* Treat `main` as
  the release line; CI green on `main` is the release criterion.
- The Paperclip sidecar is version-pinned in compose
  (`paperclip.git#v2026.529.0`), and searxng is tag-pinned — bumps to either are
  deliberate commits, guarded by `tests/test_searxng_image_pinned.py`.

## 7. Updating a deployment

Updates are git-pull-based across every path — there are no installers to re-run:

- **Docker (Windows helper):** `update_windows.bat` checks for `git`, `docker`, and
  `docker compose` on PATH, then runs `git pull --ff-only` followed by
  `docker compose up -d --build`, failing loudly at each step. The `--ff-only` pull
  refuses to merge local edits silently.
- **Docker (manual):** the same two commands by hand; bind-mounted `./data` and
  `./logs` survive the rebuild, and the named volumes (`chromadb-data`,
  `paperclip-pgdata`, …) are untouched.
- **Native macOS/Windows:** `git pull`, then re-run `./start-macos.sh` /
  `launch-windows.ps1` — both are idempotent and re-run
  `pip install -r requirements.txt` to pick up dependency changes. The macOS `.app`
  only needs rebuilding (`./build-macos-app.sh`) if the repo path or port changed,
  since it drives the repo venv live.
- **systemd:** `git pull && venv/bin/pip install -r requirements.txt && sudo
  systemctl restart apollo-ui`.

## 8. First-run setup (`setup.py`)

All three native launchers call `setup.py` after installing requirements. It is
idempotent: creates data directories, the SQLite DB, `.env`, and on the very first run
an admin user whose generated password is printed to the console (the reason the
Windows launcher keeps its console visible). `APOLLO_SKIP_RUN_HINT=1` suppresses its
"now run uvicorn" hint when a launcher will start the server itself.

## 9. Static assets and cache-busting

Two complementary mechanisms, because there is no bundler:

1. **Server-side revalidation** — `app.py` mounts static files through
   `_RevalidatingStatic`, which forces `Cache-Control: no-cache` on `.js/.css/.html` so
   browsers must revalidate (unchanged files still 304 cheaply via ETag/Last-Modified).
2. **`?v=` query-string busting** in `static/index.html` for entries whose URL the
   browser may have pinned, bumped manually when the file changes:

```html
<!-- static/index.html -->
<link rel="stylesheet" href="/static/style.css?v=paperclip-floor-20260611d">
<script type="module" src="/static/js/chat.js?v=20260520m"></script>
<script type="module" src="/static/js/paperclip.js?v=paperclip-floor-20260611d"></script>
```

**Bump procedure:** when changing `style.css` or a versioned JS module, update the
`?v=` token in `static/index.html` in the same commit (convention: a date stamp plus a
letter suffix for same-day bumps, optionally prefixed with the feature name). Generated
images go the other way — content-hashed filenames served with
`Cache-Control: public, max-age=31536000, immutable` (`app.py`,
`/api/generated-image/{filename}`).
