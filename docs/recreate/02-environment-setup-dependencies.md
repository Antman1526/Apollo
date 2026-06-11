# Apollo — Environment Setup & Dependencies

This document lists every dependency Apollo (`/Users/Antman/Apollo`) needs, what each is for, and the exact install procedures for native macOS, native Windows, and Docker — verified against `requirements*.txt`, `package.json`, `start-macos.sh`, `launch-windows.ps1`, `docker-compose.yml`, `Dockerfile`, and `setup.py` in the repo.

## 1. Runtime prerequisites

- **Python 3.11+** in a repo-local `venv/` (the reference venv runs **3.12.13**). On Apple Silicon the interpreter **must be arm64** (Homebrew, under `/opt/homebrew`) — a universal2/x86 python.org build produces a venv whose compiled extensions load as the wrong architecture when launched from the `.app` bundle ("incompatible architecture" in Cookbook).
- **tmux** — Cookbook runs model downloads/serves in background tmux sessions (not needed to boot the core app).
- **llama.cpp** (`brew install llama.cpp`) — provides a prebuilt Metal-enabled `llama-server` so local GGUF serving needs no compile step. Binary search order (`services/localmodels/server_manager.py`): `llama-server` on PATH, `~/.local/bin/llama-server`, `~/bin/llama-server`, `~/llama.cpp/build/bin/llama-server`, `/opt/homebrew/bin/llama-server`, `/usr/local/bin/llama-server`.
- **Node.js** — only needed on the host to run the JS tests (`node --test`, reference host: v24.15.0). The Paperclip sidecar in native mode auto-downloads a pinned **Node 22.13.0** into `~/.apollo/.node` (`services/paperclip/node_bootstrap.py`: `DEFAULT_NODE_VERSION = "22.13.0"`, index `https://nodejs.org/dist/index.json`, override with `PAPERCLIP_NODE_VERSION`), falling back to system Node if the download fails.
- **Git for Windows** (provides `bash.exe`) — Windows-only, for Cookbook background downloads and the agent shell tool.
- Optional agent-browser runtime: `pip install playwright && python -m playwright install chromium`.

## 2. Core Python dependencies (`requirements.txt`)

The file is intentionally unpinned (except where noted); the installed versions from the working venv are given so a rebuild can pin them.

| Package | Installed | Purpose (from in-file comments / usage) |
|---|---|---|
| fastapi | 0.136.3 | web framework |
| uvicorn | 0.49.0 | ASGI server |
| python-multipart | 0.0.32 | multipart form/file uploads |
| python-dotenv | 1.2.2 | `.env` loading |
| httpx | 0.28.1 | async HTTP client for all LLM/provider calls |
| websockets | 16.0 | WS client for the Paperclip reverse proxy (`routes/paperclip_routes.py`) — explicit core dep |
| pydantic | 2.12.5 | `>=2.0` pin; request/response models |
| pydantic-settings | 2.14.1 | `>=2.0` pin |
| SQLAlchemy | 2.0.50 | ORM over SQLite (`core/database.py`) |
| pypdf | 6.10.2 | MIT PDF text extraction |
| beautifulsoup4 | 4.14.3 | HTML parsing (search/content extraction) |
| charset-normalizer | 3.4.7 | encoding detection |
| numpy | 2.4.6 | vector math |
| chromadb | 1.5.9 | vector store — embedded `PersistentClient` for native installs, `HttpClient` when `CHROMADB_HOST` is set (Docker) |
| fastembed | 0.8.0 | local ONNX embeddings; with chromadb, core agent path (degrades to keyword fallback if missing) |
| youtube-transcript-api | 1.2.4 | YouTube transcript ingestion |
| markdown | 3.10.2 | research report rendering (`src/visual_report.py`), hard core dep |
| icalendar | 7.1.2 | calendar `.ics` import/export (`routes/calendar_routes.py`) |
| python-dateutil | 2.9.0.post0 | `dateutil.rrule` recurrence expansion — explicit even though caldav pulls it in |
| caldav | 3.2.1 | CalDAV sync (`src/caldav_sync.py`): PROPFIND discovery + REPORT fetch (Radicale/Nextcloud/Apple/Fastmail) |
| cryptography | 48.0.0 | Fernet at-rest encryption (`src/secret_storage.py`) |
| bcrypt | 5.0.0 | password + API-token hashing |
| mcp | 1.26.0 | Model Context Protocol client (`src/mcp_manager.py`) |
| pyotp | 2.9.0 | TOTP 2FA |
| qrcode[pil] | 8.2 | 2FA enrollment QR codes |
| croniter | 6.2.2 | cron-expression scheduled tasks |
| pytest / pytest-asyncio | 9.0.3 / 1.4.0 | test runner (shipped in core reqs) |
| crawl4ai | 0.8.9 | mandatory web-agent integration: research/source extraction into Markdown |

## 3. Optional Python dependencies (`requirements-optional.txt`)

Install only for the matching feature; absence yields a clear error on first use.

| Package | Purpose |
|---|---|
| faster-whisper | local STT ("local" provider), CPU via CTranslate2 — add `torch` for CUDA GPU transcription |
| piper-tts | local TTS from Piper ONNX voices ("piper" provider); CPU-only, Apple-Silicon-friendly; needs `*.onnx` + sibling `*.onnx.json` voice files |
| duckduckgo-search | DDG option in the search-provider dropdown |
| PyMuPDF | PDF **form-filling** (AcroForm detect/fill/sign/render). **AGPL-3.0** — brings AGPL obligations for a network-served app; MIT core works without it |
| markitdown[docx,pptx,xlsx,xls]==0.1.5 | Office/EPUB → Markdown for chat attachments + personal-docs RAG; lazy-imported via `src/markitdown_runtime.py`; pinned to a >30-day-old release (issue #485) |

The file's own header states the contract: *"Optional dependencies — install only if you use the corresponding feature. The app handles their absence gracefully (clear error message on first use)."* Notable in-file caveats, verbatim:

```text
# requirements-optional.txt
# Local speech-to-text (microphone -> text) via faster-whisper, for the
# "local" STT provider. Runs on CPU out of the box (CTranslate2 backend, no
# torch needed). ... Optional extra: install `torch` too if you have a CUDA
# GPU and want GPU-accelerated transcription — it's auto-detected.
#
# Local text-to-speech from Piper ONNX voices (the "piper" TTS provider).
# CPU-only and Mac/Metal-friendly (unlike Kokoro, which needs CUDA), so it's the
# local-TTS path on Apple Silicon. Point it at an on-disk `*.onnx` voice (the
# matching `*.onnx.json` must sit beside it). Lazy-imported; absent => clear error.
#
# NOTE: PyMuPDF is AGPL-3.0. Installing it brings AGPL obligations for a
# network-served app — see ACKNOWLEDGMENTS.md. The MIT core (PDF *text*
# extraction via pypdf) works without it; this only unlocks form-filling.
```

## 4. Isolated browser-use env (`requirements-browser-use.txt`)

```text
# browser-use 0.13.0 pins aiohttp==3.13.4, while ChromaDB's Kubernetes
# dependency currently requires aiohttp>=3.13.5. Install this into
# .apollo/browser-use-venv via scripts/setup-browser-use-env.
browser-use==0.13.0
litellm
```

`scripts/setup-browser-use-env` creates `${APOLLO_BROWSER_USE_VENV:-$ROOT/.apollo/browser-use-venv}`, pip-installs the file, then runs `"$VENV_DIR/bin/browser-use" install`. Verify with `scripts/check-paperclip-browser --status` / `--base-url http://127.0.0.1:7000` (uses Apollo's `/lmproxy/v1` by default, so no `BROWSER_USE_API_KEY` needed for local models).

## 5. npm dependencies (`package.json`)

```json
{
  "scripts": {
    "check": "bash scripts/check.sh",
    "test": "npm run test:js",
    "test:js": "node --test tests/test_paperclip_floor_ui.mjs tests/test_system_status_card.mjs tests/test_system_status_actions.mjs tests/test_theme_presets.mjs"
  },
  "devDependencies": { "@antithesishq/bombadil": "^0.3.2" },
  "dependencies":    { "@anthropic-ai/sdk": "^0.98.0" }
}
```

No bundler/transpiler — the frontend is plain ES modules. `@anthropic-ai/sdk` supports Anthropic-provider integrations; bombadil drives the property-test spec `tests/bombadil-spec.ts`.

## 6. Native macOS install (`./start-macos.sh`)

One idempotent command for Apple Silicon (native, not Docker — Docker on macOS cannot reach the Metal GPU):

```bash
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
./start-macos.sh        # launches at http://127.0.0.1:7860
```

What the script does, in order:
1. Parses `.env` (shell-set vars win), then resolves `PORT="${APOLLO_PORT:-${APP_PORT:-7860}}"` and `HOST="${APOLLO_HOST:-${APP_BIND:-127.0.0.1}}"` — **7860, not 7000, because macOS AirPlay Receiver holds 7000**. Fails fast if the port is taken (`/dev/tcp` probe).
2. Requires Homebrew (prints the official install one-liner if missing).
3. Finds an arm64 Python 3.11+: candidates `/opt/homebrew/bin/python3.13|3.12|3.11` on arm64 (plain `python3*` on Intel); installs `python@3.11` via brew if none found.
4. Installs Cookbook system deps only if their command is missing, warn-but-continue on failure (they aren't needed to boot the core app):

```bash
# start-macos.sh
brew_ensure() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "  ✓ $2 already installed"
    return 0
  fi
  echo "  installing $2…"
  if ! brew install "$2"; then
    echo "  ⚠ Couldn't install $2 right now — Cookbook (local model serving) may be limited."
    echo "    You can install it later with:  brew install $2"
  fi
}
brew_ensure tmux tmux
brew_ensure llama-server llama.cpp
```

5. Creates `venv/` if missing, `pip install --upgrade pip`, `pip install -r requirements.txt` (not `--quiet` — it's the slow step).
6. `APOLLO_SKIP_RUN_HINT=1 ./venv/bin/python setup.py` (first-run setup, §9).
7. Background-polls the port for up to 90s and `open`s the browser when ready (skip with `APOLLO_NO_OPEN=1`); prints a Tailscale URL when `HOST=0.0.0.0` and `tailscale` exists.
8. Launches with Paperclip native mode on by default:

```bash
# start-macos.sh
export PAPERCLIP_MODE="${PAPERCLIP_MODE:-native}"
export PAPERCLIP_ENABLED="${PAPERCLIP_ENABLED:-true}"
"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
```

LAN/Tailscale: `APOLLO_HOST=0.0.0.0 ./start-macos.sh`. After setup, `./build-macos-app.sh` produces `dist/Apollo.app` and `dist/Apollo.dmg` — a native launcher that drives the repo's `venv/` (Python is not bundled; rebuild after moving the repo).

Manual alternative (Linux/macOS):

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

## 7. Native Windows install (`launch-windows.ps1`)

```powershell
git clone https://github.com/Antman1526/Apollo.git
cd Apollo
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1            # defaults
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1
```

Steps the script performs: locate Python via the `py` launcher (`-3.13`, `-3.12`, `-3.11`) or a 3.11+ `python` on PATH (hard fail otherwise); create `venv\` if missing; `pip install -r requirements.txt`; run `setup.py`; warn (non-fatal) if Git Bash is absent (searched on PATH then `%ProgramFiles%\Git\{bin,usr\bin}\bash.exe` etc.); set `PAPERCLIP_MODE=native` and `PAPERCLIP_ENABLED=true` if unset; start `python -m uvicorn app:app --host 127.0.0.1 --port 7000`. vLLM/SGLang GPU serving needs Linux/WSL2 — on Windows point Apollo at Ollama (`http://localhost:11434/v1`) in Settings instead. `update_windows.bat` handles updates.

## 8. Docker (`docker-compose.yml` + `Dockerfile`)

```bash
git clone https://github.com/Antman1526/Apollo.git && cd Apollo
cp .env.example .env
docker compose up -d --build      # open http://localhost:7000
```

Image: `FROM python:3.12-slim`; apt installs `build-essential cmake curl git nodejs npm tmux openssh-client gosu` (tmux for Cookbook, git/cmake for in-container llama.cpp builds, nodejs/npm for the optional `@playwright/mcp` browser MCP, gosu so the entrypoint drops to `PUID:PGID` — default `1000:1000` — and chowns `/app/data` + `/app/logs`). `EXPOSE 7000`; CMD `uvicorn app:app --host 0.0.0.0 --port 7000` behind `docker/entrypoint.sh`.

Compose services:
- **apollo** — built from the repo; host bind `"${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000"`; volumes `./data:/app/data`, `./logs:/app/logs`, `./data/ssh:/app/.ssh` (Cookbook SSH identity), `./data/huggingface:/app/.cache/huggingface`, `./data/local:/app/.local` (Cookbook-installed engines survive recreation); `extra_hosts: host.docker.internal:host-gateway`; env overrides `SEARXNG_INSTANCE=http://searxng:8080`, `CHROMADB_HOST=chromadb`, `CHROMADB_PORT=8000`; `depends_on` searxng (healthy) + chromadb (started).
- **chromadb** — `chromadb/chroma:latest`, `"${CHROMADB_BIND:-127.0.0.1}:8100:8000"`, volume `chromadb-data`, `ANONYMIZED_TELEMETRY=FALSE`.
- **searxng** — **pinned** `searxng/searxng:2026.5.31-7159b8aed` (2026.6.2 crashes with `KeyError: 'default_doi_resolver'`, issue #1414); wrapper entrypoint seeds `/etc/searxng/settings.yml` from `config/searxng/settings.yml` substituting `__SEARXNG_SECRET__` (auto-generated if `SEARXNG_SECRET` unset); `127.0.0.1:8080:8080`; `cap_drop: ALL` + `cap_add: CHOWN,SETGID,SETUID,DAC_OVERRIDE` (issue #721); urllib healthcheck every 5s.
- **ntfy** — `binwiederhier/ntfy serve`, `"${NTFY_BIND:-127.0.0.1}:8091:80"`, `NTFY_BASE_URL` default `http://localhost:8091`.
- **paperclip-db / paperclip** — behind `profiles: ["paperclip"]`; Postgres `postgres:17-alpine` (user/pass/db `paperclip`); Paperclip built from `https://github.com/paperclipai/paperclip.git#v2026.529.0` with `PORT=3100`, `SERVE_UI=true`, `PAPERCLIP_DEPLOYMENT_MODE=authenticated`, `BETTER_AUTH_SECRET=${PAPERCLIP_AUTH_SECRET}`, `OPENAI_BASE_URL=${PAPERCLIP_MODEL_BASE_URL:-http://host.docker.internal:11434/v1}`; **no host port** — reached only via Apollo's `/paperclip` reverse proxy. Enable: set `PAPERCLIP_ENABLED=true` + `PAPERCLIP_AUTH_SECRET=$(openssl rand -hex 32)` in `.env`, then `docker compose --profile paperclip up -d --build`.

Environment passed to the apollo container (compose `environment:` block, `${VAR:-default}` form): `LLM_HOST=localhost`, `LLM_HOSTS=`, `OPENAI_API_KEY=`, `OLLAMA_BASE_URL=`, `RESEARCH_LLM_ENDPOINT=`, `HF_TOKEN=`/`HUGGING_FACE_HUB_TOKEN=`, `SEARXNG_INSTANCE=http://searxng:8080` (hard), `CHROMADB_HOST=chromadb`/`CHROMADB_PORT=8000` (hard), `DATABASE_URL=sqlite:///./data/app.db`, `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, `APOLLO_ADMIN_USER=admin`, `APOLLO_ADMIN_PASSWORD=`, `ALLOWED_ORIGINS=http://localhost,http://127.0.0.1`, `SECURE_COOKIES=false`, `EMBEDDING_URL=`/`EMBEDDING_MODEL=`, `FASTEMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2`, `FASTEMBED_CACHE_PATH=`, `CLEANUP_INTERVAL_HOURS=24`, `APOLLO_INPROCESS_POLLERS=1`, `APOLLO_INPROCESS_TASKS=1`, `APOLLO_SCRIPT_HOST=localhost`, search keys (`DATA_BRAVE_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_PSE_CX`, `TAVILY_API_KEY`, `SERPER_API_KEY`), `PUID=1000`/`PGID=1000`, `PAPERCLIP_ENABLED=false`, `PAPERCLIP_MODE=docker` (hard), `PAPERCLIP_URL=http://paperclip:3100` (hard), `PAPERCLIP_MODEL_ENDPOINT=ollama`.

GPU overlays: `COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml` (NVIDIA; diagnose/assist with `scripts/check-docker-gpu.sh [--install-nvidia-toolkit] [--enable-nvidia-overlay]`) or `docker-compose.yml:docker/gpu.amd.yml` + `RENDER_GID=<render group gid>` (AMD; `scripts/check-docker-amd-gpu.sh` is read-only). Overlays expose devices only — CUDA/ROCm userspace still installs via Cookbook → Dependencies. Host Ollama from Docker: add `http://host.docker.internal:11434/v1` in Settings and start Ollama with `OLLAMA_HOST=0.0.0.0:11434 ollama serve`.

Useful Docker checks:

```bash
docker compose ps
docker compose logs --tail=120 apollo
docker compose logs apollo | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
docker compose exec apollo nvidia-smi -L     # NVIDIA passthrough confirmation
```

## 9. First-run behavior (`setup.py`)

`python setup.py` is idempotent and:
1. Creates `data/` plus `data/{uploads,personal_docs,personal_uploads,tts_cache,generated_images,deep_research,chroma,rag,memory_vectors}` and `logs/`.
2. Copies `.env.example` → `.env` if absent.
3. Checks importability of `fastapi uvicorn sqlalchemy bcrypt httpx dotenv`; warns if `tmux` is missing (with per-OS install hints).
4. `Base.metadata.create_all(bind=engine)` with `DATABASE_URL` defaulted to `sqlite:///<repo>/data/app.db`.
5. Creates the initial admin in `data/auth.json` (skipped if the file exists). Credential priority, exactly as coded:

```python
# setup.py — create_default_admin()
username = os.getenv("APOLLO_ADMIN_USER", "").strip().lower()
password = os.getenv("APOLLO_ADMIN_PASSWORD", "").strip()
if username and password:
    pass                                   # both via env — use directly
elif sys.stdin.isatty() and not os.getenv("APOLLO_SKIP_ADMIN_PROMPT"):
    username, password = _prompt_admin_credentials()   # interactive terminal
else:
    username = username or "admin"         # non-interactive (Docker, CI)
    password = password or __import__("secrets").token_urlsafe(18)
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
auth_data = {"users": {username: {"password_hash": hashed, "is_admin": True}}}
```

   In the generated-password branch it prints `Temporary password: <value>` plus "Change it after first login" — this is the line surfaced by `docker compose logs apollo` for Docker first-login.
6. Prints the run hint (`python -m uvicorn app:app --host 127.0.0.1 --port 7000`) unless `APOLLO_SKIP_RUN_HINT` is set (start-macos.sh sets it). Review `data/auth.json` after first boot (disable open signup, keep only your account admin — `README.md` Security Notes).

## 10. Dev loop

```bash
# scripts/check.sh — the full quality gate (also `npm run check`)
"$PYTHON" -m compileall -q app.py companion core routes services src scripts/apollo-ralph scripts/check-paperclip-browser
"$PYTHON" -m pytest -q                 # pyproject.toml: testpaths=["tests"], asyncio_mode="auto"
npm run test:js                        # node --test tests/*.mjs
```

`PYTHON` defaults to `venv/bin/python` when present, else `python3`. Run the server in dev with `./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7000` (static files are served `no-cache`, so a plain browser reload picks up JS/CSS edits — no build step, no watcher needed).

## 11. Post-install verification checklist

```bash
curl -s http://127.0.0.1:7000/api/health      # {"status":"healthy", ...} — liveness
curl -s http://127.0.0.1:7000/api/ready       # 200 only when DB/data-dir/storage whole (503 otherwise)
curl -s http://127.0.0.1:7000/api/version     # {"version":"0.9.1"}
curl -s http://127.0.0.1:7000/api/runtime     # {"in_docker":..., "ollama_base_url":...}
scripts/apollo-integrations agent-workbench --pretty   # Paperclip, embedded browser,
                                                       # browser-use, crawl4ai, Ralph readiness
scripts/check-paperclip-browser --status               # browser-use env readiness
bash scripts/check.sh                                  # full gate: compile + pytest + node tests
```

Optional capability enablement after install:

```bash
# Agent browser control (Playwright Chromium for the embedded panel + browser tool)
pip install playwright && python -m playwright install chromium
# Built-in browser MCP server (page navigation/screenshots) — npx cache must be primed once:
npx -y @playwright/mcp@latest --version        # ~300MB; restart Apollo afterwards
# Isolated browser-use environment (Floor QA verifier)
scripts/setup-browser-use-env
```

Default service ports to keep internal-only (`README.md`): `7000` Apollo, `8080` SearXNG, `8091` ntfy, `8100` ChromaDB host port, `11434` Ollama, `8000-8020` common local model APIs.
