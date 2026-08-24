# Apollo — Build & Deployment Pipeline

Apollo ships from one codebase down four paths: Docker Compose, a native
macOS launcher/bundle (`.app`/`.dmg`, two flavors), a native Windows install
(`Apollo.exe` → PowerShell bootstrap, plus a separate zipped-source
distribution), and a raw systemd service. There is no frontend build step —
the UI is hand-authored ES modules under `static/`. Three workflow files live
in `.github/workflows/`: `ci.yml` (every PR/push), `build-windows-exe.yml`
(manual), `dependency-audit.yml` (weekly cron). This document walks `ci.yml`
job-by-job, then the macOS PyInstaller bundle build, then Windows packaging.

## 1. `.github/workflows/ci.yml` — the main gate

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    name: ${{ matrix.os }} / Python 3.12 / Node 20
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install Python dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements-dev.txt
      - name: Verify Python sources and tests
        run: |
          python -m compileall -q app.py companion core routes services src scripts
          python scripts/check_runtime_paths.py --root .
          python scripts/check_module_sizes.py
          python -m pytest -v -rf --tb=short
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - name: Install Node dependencies
        run: npm ci
      - name: Run JavaScript tests
        run: npm run test:js

  e2e:
    name: Ubuntu / browser journeys
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install Python dependencies and Chromium
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements-dev.txt
          python -m playwright install --with-deps chromium
      - name: Run browser journeys
        run: bash scripts/run-e2e.sh
```

This is **exactly four jobs**, matrix-expanded: `test` on `ubuntu-latest`,
`macos-latest`, `windows-latest` (three OS-flavored runs of the same steps,
`fail-fast: false` so one platform's failure doesn't cancel the others mid-run
and hide independent failures) plus the single `e2e` job on `ubuntu-latest`.
The `test` job's comment on `-v -rf --tb=short` is a lesson baked directly
into the workflow: *"-v prints each test result live so a mid-run native
crash (seen on the Windows runner) can't swallow the names of earlier
failures; -rf + short tracebacks keep the tail actionable."* — this is the
Windows-native-crash problem from doc 10 §5 (`python-magic`/`chromadb`
native faults) showing up as a CI-authoring concern: a crash that kills the
pytest process mid-run would otherwise leave only a truncated, uninformative
tail in the logs without `-v`.

Order inside the `test` job matters: `compileall` (syntax-error catch across
every first-party package, including files nothing currently imports) →
`check_runtime_paths.py` → `check_module_sizes.py` → `pytest` → **then**
`setup-node`/`npm ci`/`npm run test:js`. Python gates run and fail fast
before Node tooling is even installed, saving CI minutes on a Python-only
break.

`requirements-dev.txt` (not `requirements.txt`) is installed — it's the
superset that adds `pytest` and `pip-tools` (used for the lock-verification
step, `check_dependency_locks.py`, which appears in `dependency-audit.yml`
territory / lock hygiene rather than this file) on top of the runtime deps.

### 1.1 The module-size ratchet check

`scripts/check_module_sizes.py` (55 lines) enforces two rules on every file
under `static/js/**/*.js`:

```python
# scripts/check_module_sizes.py
BASELINES = {
    "admin.js": 2092, "calendar.js": 3348, "chat.js": 4584,
    "chatRenderer.js": 2105, "cookbook-hwfit.js": 1790, "cookbook.js": 1965,
    "cookbookRunning.js": 3218, "cookbookServe.js": 2086, "document.js": 9453,
    "documentLibrary.js": 3365, "emailLibrary.js": 5217, "gallery.js": 2835,
    "galleryEditor.js": 3798, "modalManager.js": 1550, "notes.js": 5011,
    "sessions.js": 3135, "settings.js": 5043, "skills.js": 2038,
    "slashCommands.js": 5940, "tasks.js": 2709, "theme.js": 2160,
}
MAX_NEW_MODULE_LINES = 1500

def check_modules(static_js: Path) -> list[str]:
    failures = []
    for path in sorted(static_js.rglob("*.js")):
        relative = path.relative_to(static_js).as_posix()
        count = line_count(path)
        baseline = BASELINES.get(relative)
        if baseline is not None and count > baseline:
            failures.append(f"{relative}: {count} lines exceeds ratchet baseline {baseline}")
        elif baseline is None and count > MAX_NEW_MODULE_LINES:
            failures.append(f"{relative}: {count} lines exceeds new-module limit {MAX_NEW_MODULE_LINES}")
    return failures
```

Two distinct policies: (1) **grandfathered files** — the 20 entries above are
existing large modules pinned at their *measured baseline line count at the
time the ratchet was introduced*; a later commit that grows one of them fails
CI, but shrinking one (moving code out) is expected and simply lowers the
number in a follow-up commit — the check itself doesn't auto-update the
baseline, a human edits the dict. (2) **every other module** (new files, or
existing small ones) is hard-capped at 1500 lines from the start — you cannot
grow a brand-new file past 1500 lines even once. This is a one-directional
ratchet: it can only get stricter over time as baselines are edited down, not
looser, short of a deliberate PR raising a number.

### 1.2 `scripts/check_runtime_paths.py`

76-line AST-based static check: rejects string literals equal to `"data"` or
starting with `"data/"` anywhere in first-party Python source outside two
exempted files (`src/runtime_paths.py`, `src/data_migration.py`) and outside
`tests/`/`venv/`/build-artifact directories. This is a regression guard for
the data-directory resolution logic in doc 09 §8 — a hardcoded
checkout-relative `"data/..."` path elsewhere in the codebase would silently
break once an install activates its platform-specific data directory
(`~/Library/Application Support/Apollo`, `%LOCALAPPDATA%\Apollo`, etc.)
instead of the legacy `<repo>/data`.

### 1.3 `scripts/check.sh` — the local mirror

```bash
# scripts/check.sh
"$PYTHON" -m compileall -q app.py companion core routes services src scripts/apollo-ralph scripts/check-paperclip-browser
"$PYTHON" scripts/check_runtime_paths.py --root "$ROOT_DIR"
"$PYTHON" scripts/check_module_sizes.py
"$PYTHON" -m pytest -q
npm run test:js
if [[ "${APOLLO_STARTUP_SMOKE:-0}" == "1" ]]; then
  "$PYTHON" scripts/smoke_startup.py
fi
```
Prefers `venv/bin/python` if present, else bare `python3`. The `compileall`
target list differs slightly from CI's (`scripts/apollo-ralph
scripts/check-paperclip-browser` vs CI's blanket `scripts`) — both compile
the same core packages; the local script additionally names two specific
subpaths under `scripts/` rather than the CI job's flat `scripts` directory
argument, which is a broader (superset) target. An optional fourth gate,
`scripts/smoke_startup.py`, runs only when `APOLLO_STARTUP_SMOKE=1` is set —
not part of either CI job by default.

### 1.4 `.github/workflows/dependency-audit.yml` (weekly, not part of the 4-job gate)

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "17 4 * * 1"          # Mondays 04:17 UTC
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - run: python -m pip install --upgrade pip pip-audit
      - run: python scripts/check_dependency_audit.py
      - if: always()
        run: npm ci
      - if: always()
        run: npm audit --omit=dev --audit-level=high
```
Separate from `ci.yml` entirely — CVE scanning on a schedule (plus manual
`workflow_dispatch`), not a PR gate. The `if: always()` on the npm steps
means an `npm audit` finding is still surfaced even if the Python
`pip-audit`-based check already failed the job.

## 2. macOS — two build scripts, two different products

Both scripts live at the repo root and both call themselves "the launcher" in
comments, but they produce fundamentally different artifacts:

| | `build-macos-app.sh` | `build-macos-bundle.sh` |
|---|---|---|
| Ships Python? | No — drives this repo's `venv` at runtime | Yes — PyInstaller onedir, fully self-contained |
| Install path baked in? | Yes, via `sed` at build time — must rebuild if repo moves | No — runs from anywhere |
| `AUTH_ENABLED` | untouched (app default `true` applies) | `export AUTH_ENABLED="${AUTH_ENABLED:-false}"` |
| `DATABASE_URL` | pinned to `$INSTALL_DIR/data/app.db` | pinned to `$HOME_DIR/data/app.db` under `~/Library/Application Support/Apollo` |
| Default port | 7860 (`APOLLO_PORT` override) | 7860 (`APOLLO_PORT` override) |
| Entry point | `venv/bin/uvicorn app:app` | PyInstaller `apollo` binary → `packaging/apollo_boot.py` → `uvicorn.run(asgi_app, ...)` |

### 2.1 `build-macos-bundle.sh` step-by-step (the self-contained bundle)

```bash
PORT="${APOLLO_PORT:-7860}"
ONEDIR="$DIST/apollo"          # PyInstaller COLLECT output (name=apollo)
PLAYWRIGHT_BROWSERS="$REPO_DIR/packaging/playwright-browsers"
```

**Step 1 — ensure `pyinstaller` in the build venv:**
```bash
if [ ! -x "$VENV/bin/pyinstaller" ]; then
  "$VENV/bin/python" -m pip install --quiet pyinstaller
fi
```

**Step 2 — bundle Chromium ahead of time.** Kept under `packaging/` (not the
global Playwright cache) specifically so PyInstaller's static file collector
picks it up:
```bash
if ! find "$PLAYWRIGHT_BROWSERS" -type f \
    \( -name headless_shell -o -name chrome -o -name "Google Chrome for Testing" \) \
    -print -quit 2>/dev/null | grep -q .; then
  mkdir -p "$PLAYWRIGHT_BROWSERS"
  PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS" "$VENV/bin/python" -m playwright install chromium
fi
```

**Step 3 — PyInstaller onedir build**, invoked via `python -m PyInstaller`
rather than the `pyinstaller` console-script entry point:
```bash
( cd "$REPO_DIR" && "$VENV/bin/python" -m PyInstaller packaging/apollo.spec \
    --noconfirm --distpath "$DIST" --workpath "$REPO_DIR/build" )
```
Comment explains why `-m` and not the script: *"entry-point shebangs cannot
hold a path containing spaces (this checkout lives under `BrainPulse Ventures
LLC`), so `$VENV/bin/pyinstaller` fails with 'bad interpreter' while `-m`
always works."*

**Step 4 — copy bundled Chromium into the onedir AFTER PyInstaller finishes:**
```bash
rm -rf "$ONEDIR/_internal/playwright-browsers"
cp -R "$PLAYWRIGHT_BROWSERS" "$ONEDIR/_internal/playwright-browsers"
```
Comment: *"Chromium is a nested, already-signed macOS application bundle.
Copy it only after PyInstaller finishes: otherwise its binary collector
attempts to re-sign the nested framework structure and rejects it as an
invalid bundle."* `cp -R` (not `cp -aL` / not `rsync`) is the actual
mechanism in this codebase — `cp -R` on macOS copies symlinks it encounters
*as symlinks* rather than resolving them (that would require an explicit
`-L`), so any internal symlinks inside the downloaded Chromium `.app` bundle
survive the copy unresolved, which is what keeps the framework's internal
structure (and its code signature, which covers relative symlink targets)
intact. UNCERTAIN: the task brief's phrasing ("rsync -a not -aL... Chromium
must be real files not symlinks") describes a stricter/different mechanism
than what's actually in `build-macos-bundle.sh` today — a repo-wide `grep
-rn rsync` found **zero** matches. The documented behavior above (`cp -R`,
run post-PyInstaller specifically to dodge the *codesign* re-signing issue)
is what the current source does; there is no `rsync` step anywhere in this
pipeline as of this scan.

**Step 5 — assemble `Apollo.app`:**
```bash
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R "$ONEDIR" "$APP/Contents/Resources/apollo"        # whole onedir, under Resources
cp "$REPO_DIR/packaging/apollo.icns" "$APP/Contents/Resources/apollo.icns"
```
The icon is **not** generated by this script — `packaging/apollo.icns` is a
pre-generated, committed multi-resolution `.icns` built by
`packaging/make-icon.sh` from "the product's own sail mark," specifically to
replace an earlier approach that center-cropped a UI screenshot
(`docs/apollo.jpg`) into a soft, single-resolution icon. `Info.plist` sets
`CFBundleIdentifier com.apollo.bundle` (distinct from `build-macos-app.sh`'s
`com.apollo.launcher`), `LSMinimumSystemVersion 11.0`.

**Step 6 — templated launcher script**, `__PORT__` substituted via `sed`:
```bash
export APOLLO_PORT="$PORT"
export DATABASE_URL="sqlite:///$HOME_DIR/data/app.db"
export AUTH_ENABLED="${AUTH_ENABLED:-false}"
```
Runtime behavior: `curl` probes if the server is already up (just opens the
UI if so); otherwise rotates `apollo-app.log` past 5 MB (keeps the last 2000
lines), starts `"$SERVER" >>"$LOG" 2>&1 &`, traps `TERM`/`INT` to kill the
server on quit, polls `/api/health` for up to 180s (first run downloads the
embedding model), then opens the UI via a probed Chromium browser's
`--app=$URL --new-window` flag (falls back to `open "$URL"`). All GUI errors
surface through `osascript` dialogs (`die_gui`), not terminal output — there
is no visible terminal for a `.app` double-click launch.

**Step 7 — ad-hoc codesign + DMG:**
```bash
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "  codesign:    (skipped)"
...
STAGE="$(mktemp -d)/dmg"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DIST/$APP_NAME.dmg"
```
Ad-hoc (`-sign -`) codesigning is enough for Gatekeeper to allow launch on
the *build* machine but does not produce a distributable, notarizable
signature — UNCERTAIN: no Developer ID signing / notarization step exists
anywhere in this repo's scripts or CI; the DMG is locally-trusted only unless
a maintainer signs it out-of-band.

### 2.2 `packaging/apollo.spec` — the PyInstaller Analysis

```python
# packaging/apollo.spec
for pkg in ("chromadb", "onnxruntime", "fastembed", "tokenizers", "cryptography",
            "pydantic", "pydantic_core", "crawl4ai", "mcp", "caldav", "icalendar",
            "markdown", "qrcode", "pyotp", "huggingface_hub", "tqdm", "certifi"):
    d, b, h = collect_all(pkg)          # datas, binaries, hiddenimports — best-effort per package
    ...

hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "magic",   # upload_handler imports python-magic inside a try — make it
               # explicit so hooks-contrib's hook-magic.py collects libmagic
]

for pkg in ("routes", "services", "core", "src", "companion", "mcp_servers", "config"):
    hiddenimports += collect_submodules(pkg)   # the app imports these dynamically at startup

datas += tree("static")
datas += tree("config")
datas += tree("mcp_servers")   # spawned as SCRIPT FILES (sys.executable mcp_servers/x.py),
                                 # so bytecode-only collection isn't enough — ship the files too
for name in ("presets.json", "features.json", "settings.json", "memory.json", "user_prefs.json"):
    datas.append((os.path.join(REPO, "data", name), "data"))

a = Analysis(
    [os.path.join(REPO, "packaging", "apollo_boot.py")],   # entry point is the boot shim, not app.py
    excludes=["tests", "pytest", "_pytest"],
    ...
)
exe = EXE(..., name="apollo", console=True, target_arch="arm64", ...)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="apollo")
```
Notable choices: (1) whole-package `collect_submodules` for `routes/`,
`services/`, `core/`, `src/`, `companion/`, `mcp_servers/`, `config/` because
Apollo imports large parts of its own tree dynamically (route registration,
plugin-style service discovery) rather than via static top-level imports
PyInstaller's analyzer can trace; (2) `console=True` and `target_arch="arm64"`
— Apple-Silicon-only build, no universal2/x86_64 slice; (3) the `Analysis`
entry point is `packaging/apollo_boot.py`, **not** `app.py` — the boot shim
must run first to redirect every path constant before `app.py`'s module-level
code executes.

### 2.3 `packaging/apollo_boot.py` — why a boot shim is needed at all

Docstring: *"Apollo normally runs out of its cloned repo, deriving
BASE_DIR/DATA_DIR/STATIC_DIR from `core/constants.py`'s `__file__`. Inside a
PyInstaller onedir bundle that `__file__` lives in the read-only app bundle,
so the SQLite DB, chroma store, uploads, settings, etc. cannot be written
where the code expects them, and a fresh install has none of the seed JSON
the app assumes exists."* Five-step fix, all executed **before** `from app
import app` (line ordering matters — `_patch_constants` must run first):

1. **Locate a writable per-user home**: `~/Library/Application Support/Apollo`
   (override: `APOLLO_HOME`).
2. **Seed it on first run** (`_seed_home`): `static/` is *symlinked* into the
   home (falls back to `copytree` if symlinking fails — cheap, since it's
   read-only content); `data/` seed files (`presets.json`, `features.json`,
   `settings.json`, `memory.json`, `user_prefs.json`) are `copy2`'d, **never
   overwriting an existing file** (idempotent across relaunches/upgrades).
   `auth.json` is deliberately **not** seeded — copying a build checkout's
   local accounts into every installed app would leak them and skip
   first-run setup; the auth manager creates an empty file on first use
   instead. Writable subdirs (`uploads`, `personal_docs`, `chroma`,
   `memory_vectors`, `rag`, `skills`, etc.) are pre-`mkdir`'d.
3. **Point Playwright at the bundled Chromium** (`_configure_bundled_playwright`):
   `os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundle / "playwright-browsers"))`
   only if that directory exists in the bundle — a fresh desktop profile has
   no `~/Library/Caches/ms-playwright`, so without this the embedded browser
   panel would need a first-run download.
4. **`chdir` into the writable home** so `StaticFiles(directory="static")`
   and any CWD-relative path resolves there, then monkeypatch
   `core.constants`/`src.constants`' `BASE_DIR`/`STATIC_DIR`/`DATA_DIR` (and
   every derived `*_FILE`/`*_DIR` constant) to point at the home — done
   **before** `app.py` is imported so every `from core.constants import
   DATA_DIR` anywhere in the app binds to the writable location, not the
   read-only bundle path.
5. **Run uvicorn programmatically**, importing the ASGI `app` object directly
   rather than the `"app:app"` import-string form — comment: *"inside a
   frozen PyInstaller bundle uvicorn's string-based re-import can't resolve
   the top-level `app` module, failing with 'Could not import module
   app'... we never use `--reload`, which is the only thing that needs the
   string form."*

A separate, easy-to-miss branch at the very top of `main()` handles **script
re-exec mode**: if `sys.argv[1]` ends in `.py` and exists on disk, it
`runpy.run_path()`s that script instead of booting the server. Comment:
`src/builtin_mcp.py` spawns stdio MCP servers as `sys.executable
mcp_servers/<x>.py`; inside the frozen app, `sys.executable` **is** the
`apollo` binary itself, so without this branch every such spawn would try to
start a second full Apollo server and die on the port bind.

### 2.4 Install-to-`/Applications` flow

There is no separate installer program. Both `.dmg`s are built identically:
stage a copy of the `.app` plus a symlink named `Applications` pointing at
`/Applications`, then `hdiutil create ... -format UDZO`. Opening the `.dmg`
mounts a Finder window showing `Apollo.app` next to an `Applications`
shortcut; the user drags one onto the other — standard macOS drag-install,
no custom installer logic, no post-install script. First launch is what
performs all first-run setup (via `apollo_boot.py`'s `_seed_home` for the
bundle, or the `[ -x "$UVICORN" ]` check's error dialog for the plain
launcher variant telling the user to run `setup.py` manually if the venv
isn't there).

## 3. Windows

### 3.1 `launch-windows.ps1` / `Apollo.exe` (day-to-day native install)

PowerShell 5.1+, `-Port 7000 -BindHost 127.0.0.1` defaults. Locates Python
3.11+ via the `py` launcher or bare `python`, creates `venv\`, `pip install -r
requirements.txt`, runs `setup.py` (prints the admin password on first run —
this is why the console stays visible), warns (doesn't fail) if Git Bash is
absent, then starts `uvicorn app:app --host $BindHost --port $Port`.
`update_windows.bat` handles `git pull` + re-run for updates.

Two different "Apollo.exe" exist in this codebase and must not be confused:

- **`scripts/windows-launcher/apollo_launcher.c`** — a ~60-line native Win32
  program, **cross-compiled from macOS/Linux**:
  ```c
  /* scripts/windows-launcher/apollo_launcher.c
   * Cross-compile from macOS/Linux:
   *   x86_64-w64-mingw32-gcc -O2 -municode -mwindows \
   *     -o Apollo.exe scripts/windows-launcher/apollo_launcher.c
   */
  WCHAR cmd[2 * MAX_PATH + 64];
  wsprintfW(cmd, L"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%s\"", script);
  CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NEW_CONSOLE, NULL, dir, &si, &pi);
  ```
  It does nothing but resolve its own directory, verify `launch-windows.ps1`
  sits beside it (a `MessageBoxW` error otherwise), and spawn that script in
  a **visible** console (`CREATE_NEW_CONSOLE`, required for the printed admin
  password to be seen). This is the Windows parallel to
  `build-macos-app.sh`'s launcher — it drives the repo, not a bundle.
  `-municode` selects the wide-char `wWinMain` entry point; `-mwindows`
  builds a GUI-subsystem binary that has no console of its own by default
  (hence the explicit `CREATE_NEW_CONSOLE` when spawning the child).
- **The PyInstaller-built `Apollo.exe` from `build-windows-exe.yml`** (§3.2
  below) — compiles `scripts/windows_launcher.py` (a Python script with the
  same job description) into a onefile executable via PyInstaller, not the C
  source above. UNCERTAIN: nothing in this repo cross-links which of the two
  is the one actually distributed to end users; both independently achieve
  "double-click, run launch-windows.ps1 in a visible console."

### 3.2 `.github/workflows/build-windows-exe.yml` — manual PyInstaller build

```yaml
name: Build Windows EXE
on:
  workflow_dispatch: {}          # Manual only — kept off push/PR so it never slows normal CI.
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python -m pip install --upgrade pip -r requirements.txt pyinstaller
      - name: Build Apollo.exe (launcher)
        # --console keeps the window open so the first-run admin password and
        # server logs are visible. --onefile produces a single Apollo.exe.
        run: pyinstaller --onefile --console --name Apollo scripts/windows_launcher.py
      - run: dist/Apollo.exe --help
      - run: Get-FileHash dist/Apollo.exe -Algorithm SHA256 | Select-Object Hash, Path | ConvertTo-Json | Set-Content dist/Apollo.exe.sha256.json
      - uses: actions/upload-artifact@v4
        with:
          name: Apollo-windows-exe
          path: [dist/Apollo.exe, dist/Apollo.exe.sha256.json]
          if-no-files-found: error
          retention-days: 90
```
`--onefile` (unlike the macOS bundle's `onedir`) packs everything into one
`.exe` — appropriate since this is a thin *launcher* (drives
`launch-windows.ps1` + a repo `venv`), not a self-contained PyInstaller build
of the whole app; there's no Windows equivalent of `apollo.spec`/
`apollo_boot.py` in this repo. The workflow's only distribution mechanism is
`actions/upload-artifact@v4` with a 90-day retention — downloadable from the
Actions run page, gated behind repo access. **No `gh release` step, no
`softprops/action-gh-release` action, and no automated publish to the
repository's Releases page exist anywhere in this repository** (verified by
a repo-wide `grep -rln "gh release" . --include="*.yml" --include="*.sh"
--include="*.md"`, zero matches). UNCERTAIN: the task brief's description of
"the GitHub release distribution route (windows-latest tag, `gh release
upload --clobber` refresh flow)" does not correspond to anything present in
this codebase as of this scan — either it describes a manual maintainer
workflow that isn't checked in, or it describes tooling that predates or
postdates this snapshot. What's verifiably true: `SECURITY.md` states
*"Security fixes are handled on the default branch until formal releases are
cut"* — i.e. `main` green-on-CI is the de facto release line, and any
Releases-page publishing is a manual, undocumented-in-code step at most.

### 3.3 `scripts/build-windows-zip.sh` — the native source-tree distribution

```bash
# scripts/build-windows-zip.sh [ref] [dest-dir]
#   ref        git ref to package        (default: origin/main)
#   dest-dir   where to write the zip    (default: $HOME/Desktop)
NAME="Apollo-Windows"

# Tracked paths that exist only to run Apollo on macOS/Linux.
DROP_PATHS=(
  start-macos.sh build-macos-app.sh build-macos-bundle.sh
  install-service.sh apollo-ui.service docs
)

# Top-level paths that must NEVER appear in the archive (checked exactly, not
# recursively — so e.g. static/js/editor/build/ is unaffected by "build").
FORBIDDEN_PATHS=(
  .env .git venv data logs dist build
  SECURITY-FIXLIST.local.md .apollo .claude .pytest_cache
)

# Tracked files whose absence has broken a shipped zip before, or that the
# Windows install cannot work without. A missing entry fails the build loudly
# instead of shipping a subtly broken archive.
REQUIRED_FILES=(
  launch-windows.ps1 update_windows.bat WINDOWS-SETUP.md
  requirements.txt app.py setup.py
  services/hwfit/data/hf_models.json
  services/docs/service.py
  static/js/editor/build/toolbar.js
  static/js/editor/build/controls.js
  static/js/editor/build/popups.js
)
```

Build/gate sequence, verbatim logic:

1. **`git archive "$REF" | tar -x -C "$BUILD"`** — only files tracked by git
   at that ref are ever written to the staging directory. Header comment:
   *"Untracked secrets (.env), the virtualenv, runtime data/ and logs/, and
   build artifacts therefore cannot leak into the zip by construction rather
   than by a hand-maintained exclude list that some future edit forgets to
   update."*
2. **`DROP_PATHS` removed** (`rm -rf`) — macOS/Linux-only launcher files and
   the whole `docs/` tree (~19 MB of demo media, not runtime code).
3. **`FORBIDDEN_PATHS` gate** — if any of these top-level paths still exist
   in the staged tree post-drop, fail loudly (`fail=1`, error printed, but
   all remaining checks still run before exiting — every violation is
   reported in one pass, not just the first).
4. **Secret-shaped file scan** (defense-in-depth even if the above logic
   changes): `find "$BUILD" -type f \( -name '*.key' -o -name '*.pem' -o
   -name '.app_key' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db'
   \)` — any hit fails the build.
5. **`REQUIRED_FILES` gate** — this is the *regression guard*, and the
   comment explains the exact incident it guards against: *"an earlier
   hand-rolled build applied .gitignore patterns WITHOUT their `!` negation
   rules and silently dropped tracked runtime files — including
   `static/js/editor/build/*.js`, which `galleryEditor.js` imports, so the
   gallery editor failed to load."* A gitignore-based exclude approach can
   silently drop a `!`-negated tracked file that lives under an otherwise
   ignored directory (`build/` is gitignored generally, but these three
   files are explicitly un-ignored and tracked); `git archive` sidesteps that
   category of bug entirely by construction (step 1), and `REQUIRED_FILES`
   is the belt-and-suspenders check that would have caught the original
   incident even if the archive step regressed.
6. **Package**: `zip -qr "$NAME.zip" "$NAME" -x '*.DS_Store' -x
   '*__pycache__*' -x '*.pyc'`, then `unzip -tq` as an integrity
   self-check before the file is moved to its destination. Final output line
   reports size and tracked-file count:
   ```bash
   printf 'Wrote %s (%s, %s files)\n' "$DEST_DIR/$NAME.zip" \
     "$(du -h "$DEST_DIR/$NAME.zip" | cut -f1 | tr -d ' ')" \
     "$(find "$BUILD" -type f | wc -l | tr -d ' ')"
   ```

This script is **not invoked by any CI workflow** — it's a maintainer-run
local/manual packaging step (`./scripts/build-windows-zip.sh` from a
checkout with `git`, `zip`, `unzip` on `PATH`), consistent with there being
no automated GitHub Release publish step (§3.2). The `[ref]` argument
defaults to `origin/main`, so a maintainer's local remote-tracking branch
must be up to date (`git fetch`) before running it, or it will package a
stale snapshot.

## 4. Other deployment paths (brief, for completeness)

- **Docker Compose**: `Dockerfile` (`python:3.12-slim` + `tmux`,
  `openssh-client`, `git`/`cmake`/`build-essential`, `nodejs`/`npm`, `gosu`)
  builds an image whose `CMD` hardcodes `uvicorn app:app --host 0.0.0.0
  --port 7000` inside the container; `docker-compose.yml` maps
  `${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000` on the host side and wires
  optional `chromadb`, `searxng` (pinned image tag), `ntfy`, and a
  `--profile paperclip` sidecar pair (`paperclip-db` + `paperclip`, the
  latter built from `https://github.com/paperclipai/paperclip.git#v2026.529.0`).
  `docker/entrypoint.sh` implements the PUID/PGID pattern and `exec
  gosu`-drops into uvicorn.
- **systemd**: `apollo-ui.service` is an edit-the-placeholders template
  (`User`, `WorkingDirectory`, `ExecStart=.../venv/bin/uvicorn app:app --port
  7000 --host 0.0.0.0`); `install-service.sh` copies it to
  `/etc/systemd/system/`, then `daemon-reload` → `enable` → `start` →
  `status`.
- **Updates** are git-pull-based on every native path — no installers to
  re-run. `update_windows.bat` (Docker-on-Windows helper) checks
  `git`/`docker`/`docker compose` on `PATH`, then `git pull --ff-only &&
  docker compose up -d --build`.

## 5. Versioning

`APP_VERSION = "1.0.0"` — a single constant in `src/constants.py` (re-exported
by `core/constants.py`, a compatibility shim: `from src.constants import *`),
served by `GET /api/version` (`app.py`, explicitly auth-exempt):
```python
@app.get("/api/version")
def version():
    from core.constants import APP_VERSION
    return {"version": APP_VERSION}
```
`packaging/apollo_boot.py`'s `_patch_constants` monkeypatches path constants
onto both `core.constants` and `src.constants` before `app.py` imports —
`APP_VERSION` itself is not among the patched values (it's not
path-dependent), so it always reflects the literal source constant regardless
of run mode.

## 6. Uncertainties

- UNCERTAIN: the task brief's "rsync -a not -aL" symlink-preservation
  framing does not match the current `build-macos-bundle.sh` source, which
  uses `cp -R` for the post-PyInstaller Chromium copy specifically to avoid
  a codesign re-signing failure, not an rsync flag choice. No `rsync`
  invocation exists anywhere in this repository as of this scan.
- UNCERTAIN: the task brief's "GitHub release distribution route
  (windows-latest tag, `gh release upload --clobber` refresh flow)" has no
  corresponding code in `.github/workflows/`, `scripts/`, or any `.md` file
  searched. `build-windows-exe.yml` distributes only via
  `actions/upload-artifact` (90-day retention, no Releases-page publish).
  Treat any "official" Windows release-page flow as undocumented-in-code /
  maintainer-manual, not automated.
- UNCERTAIN: which of the two "Apollo.exe" build paths (the cross-compiled C
  launcher vs. the PyInstaller-compiled `windows_launcher.py`) is the one
  actually handed to end users was not resolved from repo evidence alone —
  both exist, independently, doing the same job.
- UNCERTAIN: no Developer ID codesigning or notarization step exists for
  either macOS `.dmg` build script — both use ad-hoc (`--sign -`) signing
  only, sufficient for Gatekeeper on the build machine but not for
  distributing a notarized binary to arbitrary Macs without a Gatekeeper
  warning.
