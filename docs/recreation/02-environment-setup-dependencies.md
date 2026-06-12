# Apollo — Environment Setup & Dependencies

> Source root: `/Users/Antman/Apollo`. Apollo runs as **one `uvicorn` process** plus on-demand
> subprocesses. Native install is the default path for desktop (macOS/Windows); Docker Compose
> is the recommended path for servers.

---

## 1. Runtime Prerequisites

| Requirement | Detail | Source |
|-------------|--------|--------|
| **Python** | **3.11+** required. macOS Apple Silicon requires an **arm64** interpreter (Homebrew `/opt/homebrew`); a universal2/x86 Python produces a venv whose compiled extensions load as the wrong architecture inside the `.app`. | `start-macos.sh:65-84`, `launch-windows.ps1:53-98` |
| **Node.js** | Only for the **Paperclip** sidecar (native mode auto-provisions a pinned Node into `~/.apollo/.node`) and the JS test runner. Frontend itself needs **no** Node. | `app.py:849-876`, `package.json` |
| **tmux** | Used by Cookbook to run model downloads/serves in the background (macOS). Non-fatal if missing. | `start-macos.sh:120` |
| **llama.cpp** (`llama-server`) | Prebuilt, Metal-enabled binary so Cookbook serves GGUF models on GPU with no compile step. Auto-discovered from PATH / `~/.local/bin` / `/opt/homebrew/bin` / `/usr/local/bin`. Non-fatal if missing. | `start-macos.sh:121`, `services/localmodels/server_manager.py:19-27` |
| **Git Bash** (Windows) | Optional — needed only for full Cookbook background downloads and the agent shell tool. Core app works without it. | `launch-windows.ps1:123-130` |
| **Homebrew** (macOS) | Required to install Python/tmux/llama.cpp; the script will not auto-install it. | `start-macos.sh:54-63` |
| **Docker + Compose** | Only for the Docker install path. | `docker-compose.yml` |

> No `ffmpeg` requirement was found in the setup scripts. Audio features use
> `faster-whisper` (CTranslate2, CPU, no torch) for STT and `piper-tts` (ONNX) for TTS — both
> pure-Python optional installs (`requirements-optional.txt:7-19`).

`pyproject.toml` only configures pytest (no build-system / package metadata there):
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

---

## 2. The Virtual Environment

A repo-local `venv/` is created and reused by every entry point (manual steps,
`start-macos.sh`, `build-macos-app.sh`, and the clickable `.app`).

- **macOS** (`start-macos.sh:129-140`): `"$PY" -m venv venv`; `VENV_PY=./venv/bin/python3`;
  `pip install --upgrade pip`; `pip install -r requirements.txt`.
- **Windows** (`launch-windows.ps1:102-116`): `& $pyExe -m venv venv`;
  `venv\Scripts\python.exe`; `pip install -r requirements.txt`.
- **browser-use** lives in a **separate** venv `.apollo/browser-use-venv` to avoid a hard
  dependency conflict (see §6).

---

## 3. `requirements.txt` — Core Dependencies (installed by default)

These are not version-pinned in the file (pins are mostly transitive). Key packages
(`requirements.txt:1-48`):

| Package | Pin | Purpose |
|---------|-----|---------|
| `fastapi`, `uvicorn` | — | ASGI app + server (single process). |
| `python-multipart` | — | Form/file uploads. |
| `python-dotenv` | — | `.env` loading (BOM-tolerant in `app.py`). |
| `httpx` | — | Async HTTP client (LLM endpoints, sidecar probes). |
| `websockets` | — | Explicit core dep for the Paperclip reverse proxy. |
| `pydantic` | `>=2.0` | Models/validation. |
| `pydantic-settings` | `>=2.0` | Settings. |
| `SQLAlchemy` | — | ORM (`core/database.py`). |
| `pypdf` | — | PDF **text** extraction (MIT core path). |
| `beautifulsoup4`, `charset-normalizer` | — | HTML parsing / encoding detection. |
| `numpy` | — | Vector math. |
| `chromadb` | — | Vector store (embedded on-disk **or** `HttpClient` when `CHROMADB_HOST` set). |
| `fastembed` | — | Local ONNX embeddings (RAG, semantic memory, tool selection). |
| `youtube-transcript-api` | — | YouTube transcript ingestion. |
| `markdown` | — | Research report rendering (`src/visual_report.py`). |
| `icalendar`, `python-dateutil` | — | Calendar `.ics` import/export + RRULE expansion. |
| `caldav` | — | CalDAV sync (Radicale/Nextcloud/Apple/Fastmail). |
| `cryptography`, `bcrypt` | — | `EncryptedText` columns + password/token hashing. |
| `mcp` | — | Model Context Protocol client. |
| `pyotp`, `qrcode[pil]` | — | 2FA (TOTP + QR). |
| `croniter` | — | Scheduled-task cron parsing. |
| `pytest`, `pytest-asyncio` | — | Test runner. |
| `crawl4ai` | — | **Mandatory** web-agent for research/source extraction → Markdown. |

> Note: `chromadb` + `fastembed` were promoted from optional to core because RAG, semantic
> memory, and tool selection are core agent paths (`requirements-optional.txt:4-5`). The app
> still degrades to keyword fallback if a vector dep is missing.

---

## 4. `requirements-optional.txt` — Per-Feature Extras

Install only if you use the feature; the app gives a clear "install to use" message otherwise
(`requirements-optional.txt:1-43`):

| Package | Pin | Feature / Notes |
|---------|-----|-----------------|
| `faster-whisper` | — | Local STT ("local" provider). CPU-only via CTranslate2 (no torch). Add `torch` for CUDA GPU accel. |
| `piper-tts` | — | Local TTS ("piper" provider). CPU-only, Mac/Metal-friendly; point at an on-disk `*.onnx` voice. |
| `duckduckgo-search` | — | DDG as a search provider option / fallback. |
| `PyMuPDF` | — | PDF **form-filling** (AcroForm detection, stamping, rendering). **AGPL-3.0** — see `ACKNOWLEDGMENTS.md`; the MIT core (pypdf text) works without it. |
| `markitdown[docx,pptx,xlsx,xls]` | `==0.1.5` | Office/EPUB text extraction for chat attachments + personal-docs RAG. Only file in this set with a hard pin (release >30 days old per dependency-age policy). Lazy-imported via `src/markitdown_runtime.py`. |

---

## 5. `requirements-browser-use.txt` — Isolated Browser Agent

Installed into a **separate** venv (`.apollo/browser-use-venv`) via
`scripts/setup-browser-use-env`, intentionally isolated from the main venv
(`requirements-browser-use.txt:1-6`):

```
browser-use==0.13.0    # pins aiohttp==3.13.4
litellm
```

> **Why isolated** (`requirements-browser-use.txt:1-4`): `browser-use 0.13.0` pins
> `aiohttp==3.13.4`, while ChromaDB's Kubernetes dependency requires `aiohttp>=3.13.5` — a hard
> conflict, so they cannot share a venv.

---

## 6. Node Dependencies (`package.json`)

Frontend ships as raw ES modules — Node is **not** part of the runtime. `package.json` exists
only for tooling/tests:

```json
{
  "scripts": {
    "check":   "bash scripts/check.sh",
    "test":    "npm run test:js",
    "test:js": "node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs tests/test_system_status_actions.mjs tests/test_theme_presets.mjs"
  },
  "devDependencies": { "@antithesishq/bombadil": "^0.3.2" },
  "dependencies":    { "@anthropic-ai/sdk": "^0.98.0" }
}
```
- `@anthropic-ai/sdk ^0.98.0` — Anthropic SDK.
- `@antithesishq/bombadil ^0.3.2` — fuzz/property-test harness (`tests/bombadil-spec.ts`).
- JS tests run via Node's built-in test runner (`node --test`), no extra framework.

---

## 7. How to Run

### macOS (native, recommended for desktop) — `start-macos.sh`
One command, idempotent (`start-macos.sh`):
```bash
./start-macos.sh
```
Sequence: check Homebrew → find arm64 Python 3.11+ (install `python@3.11` if missing) →
`brew_ensure tmux`, `brew_ensure llama.cpp` (warn-only) → create `venv/` → `pip install -r
requirements.txt` → `setup.py` first-run (prints temp admin password) → launch
`uvicorn app:app --host $HOST --port $PORT`.
- **Port default `7860`** (not 7000 — macOS AirPlay Receiver holds 7000) (`start-macos.sh:35`).
- **Host default `127.0.0.1`**; set `APP_BIND=0.0.0.0` for LAN/Tailscale (`start-macos.sh:36`).
- Override at launch: `APOLLO_PORT=7900 ./start-macos.sh`; skip auto-open with `APOLLO_NO_OPEN=1`.
- Exports `PAPERCLIP_MODE=native`, `PAPERCLIP_ENABLED=true` by default (`start-macos.sh:202-204`).

### Windows (native, no Docker) — `launch-windows.ps1`
```powershell
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1
```
Sequence: locate Python 3.11+ (`py -3.13/-3.12/-3.11` or `python`) → create `venv` →
`pip install -r requirements.txt` → `setup.py` → `uvicorn app:app`. Defaults `-Port 7000
-BindHost 127.0.0.1`. Sets `PAPERCLIP_MODE=native`, `PAPERCLIP_ENABLED=true`
(`launch-windows.ps1:136-141`).

### Manual
```bash
python3.11 -m venv venv
./venv/bin/python -m pip install -U pip
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python setup.py                       # first-run: data dirs + admin password
./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7860
```

### Packaged desktop app
`build-macos-app.sh` builds `dist/Apollo.app` / `dist/Apollo.dmg` (both present in `dist/`).
Windows: `update_windows.bat`, `scripts/windows-launcher`. systemd unit `apollo-ui.service` +
`install-service.sh` for Linux service installs.

---

## 8. Docker Compose (`docker-compose.yml`)

```bash
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
cp .env.example .env
docker compose up -d --build
# open http://localhost:7000
```

Services defined (`docker-compose.yml`):

| Service | Image / Build | Port (host:container) | Notes |
|---------|---------------|------------------------|-------|
| `apollo` | `build: .` (`Dockerfile`) | `${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000` | Drops to `PUID/PGID` (default 1000); volumes `./data`, `./logs`, `./data/ssh`, HF + `.local` caches. |
| `chromadb` | `chromadb/chroma:latest` | `${CHROMADB_BIND:-127.0.0.1}:8100:8000` | `ANONYMIZED_TELEMETRY=FALSE`; named volume `chromadb-data`. |
| `searxng` | `searxng/searxng:2026.5.31-7159b8aed` (**pinned**) | `127.0.0.1:8080:8080` | Pinned because `apollo` waits on its healthcheck; `:latest` 2026.6.2 crashes (`KeyError: 'default_doi_resolver'`). Generates `SEARXNG_SECRET` on first boot if blank. `cap_drop: ALL` + minimal `CHOWN/SETGID/SETUID/DAC_OVERRIDE`. |
| `ntfy` | `binwiederhier/ntfy` | `${NTFY_BIND:-127.0.0.1}:8091:80` | Push notification channel. |
| `paperclip` + `paperclip-db` | build from `github.com/paperclipai/paperclip.git#v2026.529.0` + `postgres:17-alpine` | **no host port** | Behind the `paperclip` Compose profile (`profiles: ["paperclip"]`); reached only via Apollo's `/paperclip` reverse proxy. Enable with `--profile paperclip up`. |

`apollo` `depends_on` `searxng` (`service_healthy`) and `chromadb` (`service_started`)
(`docker-compose.yml:71-76`). In Docker the app's env overrides `SEARXNG_INSTANCE=http://searxng:8080`,
`CHROMADB_HOST=chromadb`, `CHROMADB_PORT=8000` (`docker-compose.yml:31-34`).

**GPU overlays** (`.env.example:144-164`): set `COMPOSE_FILE` to merge
`docker/gpu.nvidia.yml` (needs nvidia-container-toolkit) or `docker/gpu.amd.yml` (ROCm +
`RENDER_GID`). Overlays only expose the device; the slim image still needs CUDA/ROCm userspace
via Cookbook.

---

## 9. `.env` Configuration

Copy `.env.example` → `.env`. Defaults work out of the box; only edit for deployment-level
overrides (`README.md:104-107`). **The committed `.env.example` and the local `.env` contain no
live secrets** — every secret-bearing line (`OPENAI_API_KEY`, `SEARXNG_SECRET`,
`APOLLO_ADMIN_PASSWORD`) is commented out as a placeholder. Treat the following as
`<REDACTED>` if ever populated.

Key variables (`.env.example`):

```ini
# LLM
LLM_HOST=localhost
# LLM_HOSTS=llm-host.local,backup-llm.local      # comma-separated, scanned for serve ports
# APOLLO_MODELS_DIRS=/path/to/AI_Models,~/Desktop/AI_Models   # GGUF scan dirs
# OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
# LM_STUDIO_URL=http://host.docker.internal:1234
# OPENAI_API_KEY=<REDACTED>                       # commented; only if using OpenAI
# RESEARCH_LLM_ENDPOINT=http://localhost:8000/v1/chat/completions

# Search & Web
# SEARXNG_INSTANCE=...     # leave UNSET for native (managed sidecar on :8893 auto-used)
# SEARXNG_SECRET=<REDACTED>

# Database
# DATABASE_URL=sqlite:///./data/app.db

# Auth & Security
# AUTH_ENABLED=true
# APP_BIND=127.0.0.1
# APP_PORT=7000
# LOCALHOST_BYPASS=false   # dev-only loopback auth bypass; keep false when exposed
# SECURE_COOKIES=true      # set when served over HTTPS via trusted proxy
# APOLLO_ADMIN_PASSWORD=<REDACTED>   # optional pre-seed of first admin password
# ALLOWED_ORIGINS=http://localhost:7000,http://localhost:8000

# ChromaDB
# CHROMADB_HOST=localhost
# CHROMADB_PORT=8100       # 8100 for manual `docker run -p 8100:8000`; Compose → chromadb:8000

# RAG / Embeddings
# EMBEDDING_URL=http://localhost:11434/v1/embeddings
# EMBEDDING_MODEL=all-minilm:l6-v2
# FASTEMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2   # local ONNX fallback (~50MB)

# Misc
# CLEANUP_INTERVAL_HOURS=24
# APOLLO_INPROCESS_POLLERS=1     # email pollers; set 0 if cron/systemd drives polling
# APOLLO_INPROCESS_TASKS=1       # scheduled-task runner
# APOLLO_SCRIPT_HOST=localhost   # host for the run_script scheduled-task action

# Paperclip (opt-in agent UI)
PAPERCLIP_ENABLED=false
PAPERCLIP_MODE=docker            # docker | native | external
PAPERCLIP_PUBLIC_URL=http://localhost:7000/paperclip
PAPERCLIP_AUTH_SECRET=<REDACTED> # openssl rand -hex 32
PAPERCLIP_MODEL_ENDPOINT=ollama  # ollama | apollo | custom
PAPERCLIP_MODEL_BASE_URL=http://host.docker.internal:11434/v1
PAPERCLIP_MODEL_API_KEY=local
PAPERCLIP_COLLECTOR_ENABLED=true
PAPERCLIP_COLLECTOR_TOKEN=<REDACTED>
PAPERCLIP_COMPANY_ID=
```

**`.env` parsing gotchas handled in code:**
- `load_dotenv(encoding="utf-8-sig")` (`app.py:37`) tolerates a UTF-8 BOM from Notepad —
  without it `AUTH_ENABLED=false` would be silently ignored.
- `start-macos.sh:22-31` re-parses `.env` for shell vars; already-set shell vars win.

---

## 10. First-Run Setup (`setup.py`)

`setup.py` runs once at launch (idempotent): creates data dirs, the SQLite DB, an admin
account (`admin` unless `APOLLO_ADMIN_USER` is set) and **prints a temporary admin password**
to the terminal (`docker compose logs apollo` in Docker). Change it in Settings after first
login (`README.md:109-117`).

---

## 11. Ports Summary

| Port | Service | Bind |
|------|---------|------|
| **7860** | Apollo web UI (macOS native default) | `127.0.0.1` (AirPlay holds 7000) |
| **7000** | Apollo web UI (Windows / Docker default) | `127.0.0.1` |
| **8893** | Managed SearXNG sidecar (native) | `127.0.0.1` only (`searxng/config.py:16`) |
| **8080** | SearXNG (Docker) | `127.0.0.1` |
| **8100→8000** | ChromaDB (Docker) | `127.0.0.1` |
| **8091→80** | ntfy (Docker) | `127.0.0.1` |
| **3100** | Paperclip (Docker, internal only — no host port) | internal network |
| ephemeral | `llama-server` per warm model | `127.0.0.1` (`_free_port`) |
