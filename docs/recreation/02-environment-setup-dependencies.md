# 02 — Environment Setup & Dependencies

Every claim in this document was checked against source (`requirements.txt`,
`requirements.in`, `requirements-optional.txt`, `requirements-dev.in`,
`setup.py`, `launch-windows.ps1`, `WINDOWS-SETUP.md`, `start-macos.sh`,
`README.md`, `package.json`) and, for "used for" claims, against a grep of
actual imports in `app.py`, `routes/`, `services/`, `src/`, `core/`,
`companion/`, `mcp_servers/`, `security/`, `config/`, `setup.py`, `scripts/`.

## 1. Python Version

- **Required: Python 3.11+.** CI (`.github/workflows/ci.yml`) runs on
  **Python 3.12**. `requirements.txt`'s own header states it was compiled
  "by pip-compile with Python 3.12".
- `launch-windows.ps1` probes for `py -3.13`, `py -3.12`, `py -3.11` in that
  order (prefers newest), or a bare `python` ≥ 3.11.
- `pyproject.toml` sets `target-version = "py312"` for `ruff`.

## 2. Creating the Dev Environment (native Linux/macOS)

Verbatim from `README.md` "Native Linux / macOS" section:

```bash
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

`app:app` is the ASGI application object defined in `app.py`. `setup.py`
creates data dirs, initializes the SQLite DB, and prints the first-boot
admin password (§6 below). Use `--host 0.0.0.0` only when LAN/reverse-proxy
access is intentional.

For contribution/dev work also install dev deps:
```bash
pip install -r requirements-dev.txt
```
which is generated from `requirements-dev.in`:
```
-r requirements.in
pytest
pytest-asyncio
httpx2
pip-tools
pip-audit
ruff
```

Optional features (STT/TTS/DDG search/PDF forms/Office doc conversion) are
NOT installed by default — see `requirements-optional.txt`, §4.

Optional agent-browser runtime (embedded browser tool, crawl4ai's headless
path):
```bash
pip install playwright && python -m playwright install chromium
```
(source: `services/browser/embedded_browser.py`'s own `install_hint` string
and `scripts/run-e2e.sh`, which fails fast with the same hint if Chromium
isn't present.)

## 3. Full Dependency Table (every package in `requirements.txt`)

`requirements.txt` pins **160 packages** total. Below, packages Apollo's own
code imports **directly** (grep-verified — file path shown) get a real
usage note; packages that are **purely transitive** (pulled in only to
satisfy `chromadb`, `crawl4ai`, `mcp`, `caldav`, or another direct
dependency, per the `# via` comments `pip-compile` writes into
`requirements.txt`, and confirmed by grep finding zero direct imports in
`app.py`/`routes/`/`services/`/`src/`/`core/`/`companion/`/`mcp_servers/`)
are marked "transitive only".

### 3.1 Direct dependencies (from `requirements.in`) — 24 declared, resolving to these pinned versions

| Package | Version | Verified usage in Apollo code |
|---|---|---|
| `fastapi` | 0.139.2 | Web framework; `app.py:80`, all routers. |
| `uvicorn` | 0.51.0 | ASGI server; `python -m uvicorn app:app`. Also imported directly in `scripts/diffusion_server.py`. |
| `uvloop` | 0.22.1 | uvicorn accel, POSIX only (`sys_platform != "win32"` in `requirements.in`). |
| `python-multipart` | 0.0.32 | Multipart upload parsing (FastAPI dependency, no direct top-level import found). |
| `python-dotenv` | 1.2.2 | `.env` loading — `app.py:36` `load_dotenv(encoding="utf-8-sig")`. |
| `httpx` | 0.28.1 | Async HTTP client — 37 files, e.g. `app.py`, `routes/contacts_routes.py`, `routes/paperclip_routes.py`, `routes/mcp_routes.py`. |
| `requests` | 2.33.1 (pinned `<2.34`) | Sync HTTP — `routes/email_pollers.py`, `routes/email_routes.py`, `routes/chat_helpers.py`. Pin reason (comment in `requirements.in`): CalDAV's `niquests` installs `urllib3-future`, and `requests>=2.34` assumes the standard `urllib3` layout and breaks. |
| `websockets` | 16.1.1 | `routes/paperclip_routes.py`, `services/paperclip/collector.py`. |
| `pydantic` | 2.13.4 | Request/response models — 30 files. |
| `pydantic-settings` | 2.14.2 | `src/config.py`. |
| `sqlalchemy` | 2.0.51 | ORM — `core/database.py`; used in 17 route/service files (`routes/email_routes.py`, `routes/note_routes.py`, `routes/email_helpers.py`, `routes/auth_routes.py`, ...). |
| `pypdf` | 6.14.2 | `src/personal_docs.py`, `src/document_processor.py`. |
| `beautifulsoup4` (`bs4`) | 4.15.0 | `services/search/content.py`, `services/search/providers.py`, `src/visual_report.py`, `src/email_thread_parser.py`, `src/search/content.py`. |
| `charset-normalizer` | 3.4.9 | `routes/memory_routes.py`, `src/document_processor.py`. |
| `numpy` | 2.5.1 | `routes/gallery_routes.py`, `services/tts/tts_service.py`, `src/rag_vector.py`, `src/tool_index.py`, `src/embeddings.py`. |
| `chromadb` | 1.5.9 | `src/chroma_client.py`. |
| `fastembed` | 0.8.0 | `routes/embedding_routes.py`, `src/embeddings.py`. |
| `youtube-transcript-api` | 1.2.4 | `services/youtube/youtube_handler.py`, `src/youtube_handler.py`. |
| `markdown` | 3.10.2 | `src/visual_report.py`. |
| `icalendar` | 7.2.0 | `routes/calendar_routes.py`, `src/caldav_sync.py`, `src/caldav_writeback.py`. |
| `python-dateutil` | 2.9.0.post0 | `routes/calendar_routes.py`. |
| `caldav` | 3.2.1 | `src/caldav_sync.py`, `src/caldav_writeback.py`. |
| `cryptography` | 49.0.0 | `src/api_key_manager.py`, `src/webhook_manager.py`, `src/secret_storage.py`. |
| `bcrypt` | 5.0.0 | `app.py` (token hash check), `routes/api_token_routes.py`, `core/auth.py`, `companion/pairing.py`. |
| `mcp` | 1.28.1 | `src/mcp_manager.py`, `mcp_servers/memory_server.py`, `mcp_servers/rag_server.py`, `mcp_servers/image_gen_server.py`. |
| `pyotp` | 2.10.0 | `core/auth.py` (TOTP 2FA). |
| `qrcode[pil]` | 8.2 | `companion/pairing.py` (pairing QR code). |
| `croniter` | 6.2.4 | `routes/task_routes.py`, `src/task_scheduler.py`. |
| `crawl4ai` | 0.9.2 | `services/research/crawl4ai_adapter.py`. |
| `python-magic` | 0.4.27 | `src/upload_handler.py`. |

### 3.2 Transitive-only packages (136 packages, pulled in by the above)

Grouped by which direct dependency needs them (per `requirements.txt`'s own
`# via` annotations) — none of these are imported directly anywhere under
`app.py`/`routes/`/`services/`/`src/`/`core/`/`companion/`/`mcp_servers/`
(grep-verified, zero hits).

**Pulled in by `chromadb` 1.5.9** (vector DB + its optional server/telemetry stack):
`bcrypt`(also direct)`, build==1.5.0, grpcio==1.82.1, importlib-resources==7.1.0, jsonschema==4.26.0, jsonschema-specifications==2025.9.1, kubernetes==36.0.3, mmh3==5.2.1, onnxruntime==1.27.0, opentelemetry-api==1.44.0, opentelemetry-exporter-otlp-proto-common==1.44.0, opentelemetry-exporter-otlp-proto-grpc==1.44.0, opentelemetry-proto==1.44.0, opentelemetry-sdk==1.44.0, opentelemetry-semantic-conventions==0.65b0, orjson==3.11.9, overrides==7.7.0, posthog (not present — telemetry disabled at this pin), pybase64==1.4.3, pypika==0.51.1, referencing==0.37.0, rpds-py==2026.6.3, tenacity==9.1.4, tokenizers==0.23.1, typer==0.27.0, googleapis-common-protos==1.75.0, protobuf==7.35.1, requests-oauthlib==2.0.0, oauthlib==3.3.1, websocket-client==1.9.0, durationpy==0.10`.

**Pulled in by `crawl4ai` 0.9.2** (its content-extraction / browser / geometry stack):
`aiofiles==25.1.0, aiosqlite==0.22.1, alphashape==1.3.1, brotli==1.2.0, chardet==7.4.3, click-log==0.4.0, cssselect==1.4.0, fake-useragent==2.2.0, humanize==4.16.0, lark==1.3.1, lxml==6.1.1, networkx==3.6.1, nltk==3.10.0, patchright==1.61.2, playwright==1.61.0, playwright-stealth==2.0.3, psutil==7.2.2, rank-bm25==0.2.2, rtree==1.4.1, scipy==1.18.0, shapely==2.1.2, snowballstemmer==2.2.0, trimesh==4.12.2, xxhash==3.8.1, unclecode-litellm==1.81.13 (crawl4ai's bundled LiteLLM fork, pulling openai==2.46.0, tiktoken==0.13.0, jinja2==3.1.6, fastuuid==0.14.0), py-rust-stemmers==0.1.8`.

**Pulled in by `mcp` 1.28.1**: `httpx-sse==0.4.3, pyjwt==2.13.0, sse-starlette==3.4.5, starlette==1.3.1` (also required by fastapi).

**Pulled in by `caldav` 3.2.1**: `dnspython==2.8.0, icalendar-searcher==1.0.6, niquests==3.20.1` (pulls `urllib3-future==2.22.901`, `qh3==1.9.4`, `jh2==5.0.13`, `wassima==2.1.2`), `recurring-ical-events==3.8.2, x-wr-timezone==2.0.1`.

**Shared low-level libs** (pulled in by several of the above simultaneously):
`aiohappyeyeballs==2.7.1, aiohttp==3.14.1, aiosignal==1.4.0, annotated-doc==0.0.4, annotated-types==0.7.0, anyio==4.14.2, attrs==26.1.0, certifi==2026.6.17, cffi==2.1.0, click==8.4.2, defusedxml==0.7.1, distro==1.9.0, filelock==3.31.0, flatbuffers==25.12.19, frozenlist==1.8.0, fsspec==2026.6.0, greenlet==3.5.3, h11==0.16.0, h2==4.3.0, hf-xet==1.5.2, hpack==4.2.0, httpcore==1.0.9, httptools==0.8.0, huggingface-hub==1.24.0 (used directly in `scripts/diffusion_server.py`, `scripts/hf_download.py`, `scripts/add_hwfit_models.py` — dev/ops scripts, not the app itself), hyperframe==6.1.0, idna==3.18, importlib-metadata==9.0.0, jiter==0.16.0, joblib==1.5.3, loguru==0.7.3, markdown-it-py==4.2.0, markupsafe==3.0.3, mdurl==0.1.2, multidict==6.7.1, packaging==26.2, pillow==12.3.0 (`PIL` — used directly at `routes/gallery_helpers.py:33`, `routes/upload_routes.py:131`, `routes/gallery_routes.py` multiple), propcache==0.5.2, pycparser==3.0, pydantic-core==2.46.4, pyee==13.0.1, pygments==2.20.0, pyopenssl==26.3.0, pyproject-hooks==1.2.0, regex==2026.7.10, rich==15.0.0, shellingham==1.5.4, six==1.17.0, sniffio==1.3.1, soupsieve==2.8.4, typing-extensions==4.16.0, typing-inspection==0.4.2, tzdata==2026.3, urllib3==2.7.0, watchfiles==1.2.0, yarl==1.24.2, zipp==4.1.0, tqdm==4.69.0 (used directly in `scripts/hf_download.py`), pyyaml==6.0.3 (`yaml` — used directly in `services/skills/pack_installer.py`)`.

UNCERTAIN: A handful of these (e.g. `pyyaml`, `huggingface-hub`, `tqdm`)
are technically imported by first-party code but only inside `scripts/`
(operator tooling) rather than the running app itself — flagged inline
above rather than hidden in the "transitive only" bucket.

## 4. Optional Dependencies (`requirements-optional.txt`)

Install only if the corresponding feature is used; the app degrades
gracefully (clear error on first use) without them. Full text of the file's
own framing comment: *"Optional dependencies — install only if you use the
corresponding feature. The app handles their absence gracefully (clear
error message on first use)."*

| Package | Feature it unlocks | Notes |
|---|---|---|
| `faster-whisper` | Local STT ("local" provider) | CPU (CTranslate2) by default; GPU auto-detected if `torch` is separately installed. |
| `piper-tts` | Local TTS ("piper" provider) | CPU-only, Mac/Metal-friendly (Kokoro alternative needs CUDA). Point at an on-disk `*.onnx` voice + matching `*.onnx.json`. |
| `duckduckgo-search` | DDG search provider | Alternative to SearXNG/Brave/Tavily/Serper/Google PSE. |
| `PyMuPDF` | PDF form-filling (AcroForm fields, stamping, page rendering) | **AGPL-3.0** — intentionally excluded from the default (MIT) install; see `ACKNOWLEDGMENTS.md`. Core PDF text extraction (`pypdf`) works without it. |
| `markitdown[docx,pptx,xlsx,xls]==0.1.5` | Office/EPUB → Markdown for chat attachments + personal-docs RAG | MIT, Microsoft. Lazy-imported via `src/markitdown_runtime.py`; without it those formats show an "install to extract" banner. Pinned intentionally >30 days old per issue #485. Extras avoided: `[all]`/Azure/audio (cloud + heavy). |

Chromadb-client + fastembed used to be optional but "moved to
requirements.txt — RAG, semantic memory, and tool selection are core paths,
so they ship by default now" (comment at the top of
`requirements-optional.txt`).

## 5. Node / Playwright / JS Test Setup

`package.json` (full contents):

```json
{
  "repository": {"type": "git", "url": "https://github.com/Antman1526/Apollo.git"},
  "scripts": {
    "check": "bash scripts/check.sh",
    "test": "npm run test:js",
    "test:js": "node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs ..."
  },
  "devDependencies": {"@antithesishq/bombadil": "^0.3.2"},
  "dependencies": {"@anthropic-ai/sdk": "^0.98.0"}
}
```

- No frontend build step / bundler — `static/js/*.js` is served as-is
  (see `01-project-overview-architecture.md` §4.5's static-file caching
  discussion). Node is used only for **running JS unit tests** via the
  built-in `node --test` runner (no Jest/Mocha), listed explicitly in
  `package.json`'s `test:js` script (16 `.mjs` test files under `tests/`).
- Python-side E2E/browser tests use **Playwright** (`playwright==1.61.0`,
  a transitive pin shared with `crawl4ai`, but Apollo's own tests import it
  too): install with `pip install playwright && python -m playwright install chromium`.
  `scripts/run-e2e.sh` checks Chromium is present and fails with that exact
  hint if not; `pyproject.toml` registers an `e2e` pytest marker: *"real-
  browser tests that start an isolated local application"*.
- CI (`.github/workflows/ci.yml`) installs it with
  `python -m playwright install --with-deps chromium`.
- `static/js/package.json` exists as a second, separate `package.json`
  scoped to `static/js/` — UNCERTAIN of its exact purpose beyond scoping;
  not read in full for this pass.

## 6. macOS Launch Path

Three distinct scripts, all at the repo root, for three different use cases:

### 6.1 `start-macos.sh` — one-command quick start (source checkout)

Purpose (from its own header comment): *"Installs everything Apollo needs
via Homebrew, sets up a local Python environment, and launches the app — so
a generic Mac user can run it without knowing anything about venvs, pip, or
uvicorn. Safe to re-run; it skips work that's already done."* Explicitly
native (not Docker) because *"Docker on macOS is a Linux VM with no access
to the Metal GPU. Running natively lets Cookbook detect and use your Mac's
GPU."*

Behavior:
1. Loads `.env` (if present) into the shell environment, tolerating
   comments/blank lines, without overriding already-exported shell vars.
2. Resolves port/host: `APOLLO_PORT` → `APP_PORT` → default **7860**
   (chosen instead of 7000 because "macOS AirPlay Receiver holds 7000");
   `APOLLO_HOST` → `APP_BIND` → default `127.0.0.1`.
3. Fails fast with a clear message if the chosen port is already bound.
4. Requires Homebrew; if missing, prints the official Homebrew install
   one-liner and exits rather than auto-installing it (needs its own
   interactive confirmation, per the script's comment).
5. Creates the venv, installs `requirements.txt`, runs `setup.py`
   (`APOLLO_SKIP_RUN_HINT` is set so `setup.py` doesn't print a contradictory
   "start the server with..." hint, since this script starts it itself), then
   launches `venv/bin/python -m uvicorn app:app` on the resolved host/port.
6. `trap 'echo; echo "✗ Setup failed above. It is safe to re-run ./start-macos.sh."; exit 1' ERR`
   — every step is meant to be idempotent/re-runnable.

Usage: `./start-macos.sh`, or `APOLLO_HOST=0.0.0.0 ./start-macos.sh` for
trusted-LAN/Tailscale exposure (README explicitly warns to keep
`AUTH_ENABLED=true` and never expose the port directly to the public
internet).

### 6.2 `build-macos-app.sh` — launcher build

*"Launcher build — small, drives THIS repo's venv (Python not bundled).
Best for developers who keep the repo; Cookbook keeps direct Metal-GPU
access."* Produces `dist/Apollo.app` (double-click to start the server +
open the UI) and `dist/Apollo.dmg`. The app bakes in the repo's install
path — must be rebuilt if the repo moves.

### 6.3 `build-macos-bundle.sh` — self-contained PyInstaller build

*"Self-contained bundle — PyInstaller-packs Python + all deps, so the app
runs on any Apple-Silicon Mac WITHOUT the repo or a preinstalled venv."*
Uses `packaging/apollo.spec` + `packaging/apollo_boot.py`, pins the app's
SQLite DB, and waits on a readiness probe (`/api/ready`) before opening the
UI. Needs a working `./venv` with app deps + `pyinstaller` only to *build*
(not to run the resulting bundle). Installs the Playwright Chromium browser
into the bundle at build time (`build-macos-bundle.sh:44`):
```bash
PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS" "$VENV/bin/python" -m playwright install chromium
```
Both build flavors default to port **7860** (override with `APOLLO_PORT`).

## 7. Windows Native Path

### 7.1 `launch-windows.ps1` — auto-installing one-command launcher

Requires PowerShell ≥5.1 (`#Requires -Version 5.1`). Parameters:
`-Port` (default 7000), `-BindHost` (default `127.0.0.1`).

**Exact auto-install flow as implemented** (step numbers match the script's
own comments):

1. **Locate Python 3.11+** (`Resolve-Python`): tries the `py` launcher with
   `-3.13`, `-3.12`, `-3.11` in that preference order; falls back to a bare
   `python` on PATH if its version is ≥3.11. If none found and `winget` is
   available, **asks the user** (`Confirm-Install "Python 3.12"` →
   `Read-Host "Install {0} now via winget? [Y/n]"`, defaults to Yes on
   empty/`Y`/`y`) then runs:
   ```powershell
   winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
   ```
   then calls `Update-SessionPath` (re-reads Machine+User PATH from the
   registry, since a fresh install doesn't reach the current session). If
   Python still isn't found afterward, the script `Fail`s with an explicit
   instruction to open a **new** PowerShell window.
2. **Create the venv** at `venv\` if `venv\Scripts\python.exe` doesn't
   already exist: `& $pyExe @pyArgs -m venv venv`.
3. **Install dependencies**: `pip install --upgrade pip --quiet` then
   `pip install -r requirements.txt`.
4. **First-time setup**: `& $venvPy setup.py` (creates data dirs, DB, `.env`,
   admin user — §8 below).
5. **Optional prerequisite: Git for Windows** — checked via `Find-GitBash`
   (looks for `bash` on PATH, else scans
   `%ProgramFiles%\Git`, `%ProgramW6432%\Git`, `%ProgramFiles(x86)%\Git`,
   `%LocalAppData%\Git`, and two hardcoded fallbacks, for
   `bin\bash.exe`/`usr\bin\bash.exe`). If missing, warns that "the core app
   works without it" but Cookbook background downloads + the agent shell
   tool need it, and offers:
   ```powershell
   winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
   ```
6. **Optional prerequisite: llama.cpp** — checked via
   `Get-Command llama-server`. If missing, warns it's "Only needed to run
   local GGUF models; cloud/remote endpoints work without it" and offers:
   ```powershell
   winget install llama.cpp --accept-package-agreements --accept-source-agreements
   winget upgrade llama.cpp --accept-package-agreements --accept-source-agreements
   ```
   The immediate follow-up `winget upgrade` (not just `install`) is
   deliberate: *"winget's cached manifest can lag, and a stale llama.cpp
   can't load newer model architectures (see WINDOWS-SETUP.md)"*.
7. **Start the server**, forcing native Paperclip defaults if unset:
   ```powershell
   if (-not $env:PAPERCLIP_MODE) { $env:PAPERCLIP_MODE = "native" }
   if (-not $env:PAPERCLIP_ENABLED) { $env:PAPERCLIP_ENABLED = "true" }
   & $venvPy -m uvicorn app:app --host $BindHost --port $Port
   ```

Every auto-install step asks first — the script never installs silently
(confirmed: every `winget install` call for optional tools is gated by
`Confirm-Install`; only the *required* Python step attempts an install
without a prior explicit "may I" — but even that still shows the winget
package name being installed and requires `Test-Winget` to succeed first).

### 7.2 `WINDOWS-SETUP.md` — the three prerequisites

States plainly: *"Apollo needs exactly three things: Python 3.11+, Git for
Windows, and a recent llama.cpp."* Manual install winget IDs (same ones the
launcher uses): `Python.Python.3.12`, `Git.Git`, and `llama.cpp` (bare
package id, no publisher prefix, per the doc's own command).

**"Get a RECENT build" requirement (verbatim reasoning from the doc):**
newer model architectures need newer llama.cpp; a stale package-manager
copy is *"the single most confusing failure here"* — the model appears in
the picker, then refuses to start with:
```
llama_model_load: error loading model: missing tensor 'blk.64.ssm_conv1d.weight'
```
This is explicitly **not** an Apollo bug or a corrupt download — `ssm_*`
tensors are state-space (Mamba-style) layers used by hybrid
attention+SSM models (the doc names Qwen 3.5 / 3.6 / 3.8 as examples), and
older `llama-server` builds simply cannot load them. Fix: `winget upgrade
llama.cpp` (or grab the latest GitHub release build) and restart the model.
If Apollo still picks up an old PATH copy, the doc points to **Settings →
AI → Local Models → llama-server Binary** (or `APOLLO_LLAMA_SERVER` env
var) to pin the exact binary — the status line under that field shows which
binary is actually in use.

Neither Ollama nor LM Studio is required — Apollo serves GGUF files itself
via `llama-server`. If LM Studio is present, its model folder
(`%USERPROFILE%\.lmstudio\models`) is scanned by default (convenience, not
a dependency); Ollama can be added as an additional HTTP endpoint under
Settings → AI → Add Models, never installed/managed by Apollo.

Windows-specific code locations the doc points to (verified to exist):

| File | Role |
|---|---|
| `services/localmodels/config.py` | Windows-aware default scan dirs; `llama_server_path` setting + `APOLLO_LLAMA_SERVER` env var. |
| `services/localmodels/server_manager.py` | Windows `llama-server.exe` auto-detect candidate list; configured path wins; clear launch-error messages. |
| `routes/localmodels_routes.py` | `GET/PUT /api/local-models/binary`. |
| `static/index.html` + `static/js/settingsAiExtras.js` | The "llama-server Binary" Settings field. |

Also relevant from `app.py:21-28` — Windows-specific env vars set at
process start, before any HuggingFace import:
```python
if os.name == "nt":
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
```
because "On a network-share/UNC data dir Windows can't follow HF's symlinks
(`[WinError 1463]`), so the ONNX embedding model fails to load."

## 8. `setup.py` — First-Run Behavior (read in full, 259 lines)

Docstring: *"Apollo — first-time setup script. Creates data directories,
initializes the database, and sets up an initial admin user. Safe to re-run
(skips what already exists)."*

Runs 5 numbered steps from `main()`:

**Step 0 — data storage check** (`prepare_data_storage()`): non-mutating by
default. If neither `APOLLO_DATA_DIR` nor `DATA_DIR` env vars are set, it
checks `src.data_migration.migration_status(source, target)` between the
legacy in-checkout data root and the new platform-standard data root
(`src.runtime_paths.platform_data_root()`), and only migrates if
`APOLLO_MIGRATE_DATA=true` is explicitly set — otherwise it prints an
`[info]` pointer to `scripts/apollo-data-migrate --dry-run`.

**Step 1 — create directories** (`create_dirs()`), the exact list from
`_directories(data_dir)`:
```
<data_dir>/
<data_dir>/uploads
<data_dir>/personal_docs
<data_dir>/personal_uploads
<data_dir>/tts_cache
<data_dir>/generated_images
<data_dir>/deep_research
<data_dir>/chroma
<data_dir>/rag
<data_dir>/memory_vectors
<repo>/logs
```

**Step 2 — `.env` file** (`create_env()`): copies `.env.example` → `.env`
only if `.env` doesn't already exist (`[skip]` message otherwise); warns if
`.env.example` itself is missing.

**Step 3 — dependency check** (`check_deps()`): tries `import` on
`fastapi, uvicorn, sqlalchemy, bcrypt, httpx, dotenv`, prints which are
missing with a `pip install -r requirements.txt` hint. On POSIX, also checks
`tmux` is on PATH (needed by Cookbook for background downloads/serves) and
suggests the right package-manager command (`brew install tmux` on
`darwin`, else `apt`/`pacman`/`dnf` suggestions); skipped on Windows.

**Step 4 — database init** (`init_database()`): sets
`DATABASE_URL=sqlite:///<data_dir>/app.db` (via `os.environ.setdefault`,
so an already-set `DATABASE_URL` wins), imports `core.database.Base` /
`engine`, and calls `Base.metadata.create_all(bind=engine)` — creates every
SQLAlchemy table.

**Step 5 — admin user** (`create_default_admin()`): skipped entirely
(`"[skip] auth.json already exists"`) if `<data_dir>/auth.json` already
exists. Otherwise, credential source priority:
1. `APOLLO_ADMIN_USER` + `APOLLO_ADMIN_PASSWORD` env vars, if both set.
2. Interactive prompt (`_prompt_admin_credentials()` — `getpass`-masked,
   confirms the password twice) if running in a TTY and
   `APOLLO_SKIP_ADMIN_PROMPT` is not set.
3. Otherwise (non-interactive — Docker/CI): username defaults to `"admin"`,
   password is `secrets.token_urlsafe(18)` (random, printed once).

Writes `<data_dir>/auth.json`:
```json
{"users": {"<username>": {"password_hash": "<bcrypt hash>", "is_admin": true}}}
```
If the password was env/random-generated (non-interactive path), the
temporary password is printed to stdout with a "Change it after first
login" warning; the interactive-prompt path never echoes it back (the user
just typed it). If `bcrypt` isn't importable, admin creation is skipped
with a `pip install bcrypt` hint (status `"skipped"`).

**Final output**: unless `APOLLO_SKIP_RUN_HINT` is set (used internally by
`start-macos.sh`, which starts the server itself right after), prints:
```
Start the server with:
  python -m uvicorn app:app --host 127.0.0.1 --port 7000

Then open http://localhost:7000
```
followed by a status-specific closing line (`"created"` → "Login with your
admin credentials."; `"exists"` → "Login with your existing admin
credentials."; `"skipped"`/`"failed"` → actionable remediation text).

## 9. llama.cpp Requirement (cross-reference)

Not a pip dependency — a separate native binary the user installs
themselves (or the launcher installs via winget on Windows; on macOS,
`start-macos.sh` installs it via Homebrew: `brew_ensure llama-server
llama.cpp` at `start-macos.sh:121`, i.e. `brew install llama.cpp` if the
`llama-server` command isn't already found — the script's own comment
describes it as "a prebuilt, Metal-enabled llama-server so Cookbook can
serve" local models). See §7.2 above for the full "must be a recent build"
requirement and its exact failure signature. Apollo never bundles or builds
llama.cpp itself — it only locates and launches an existing `llama-server`
binary (§6.2 of `01-project-overview-architecture.md`).

## 10. Quick-Reference: Getting a Working Dev Box

1. Clone the repo.
2. `python3 -m venv venv && source venv/bin/activate` (`venv\Scripts\Activate.ps1` on Windows, or just run `launch-windows.ps1`).
3. `pip install -r requirements.txt` (add `-r requirements-dev.txt` for tests/linting; add packages from `requirements-optional.txt` as needed for voice/PDF-forms/Office-doc features).
4. `python setup.py` — creates dirs, DB, `.env`, admin user.
5. Install llama.cpp if local GGUF models are wanted (`brew install llama.cpp` / winget / manual release build) — **verify it's recent** (§7.2).
6. `pip install playwright && python -m playwright install chromium` if the embedded-browser agent tool or crawl4ai's rendering path will be used.
7. `python -m uvicorn app:app --host 127.0.0.1 --port 7000`.
8. Log in with the admin credentials `setup.py` printed; change the password in Settings.
