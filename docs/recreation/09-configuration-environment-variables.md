# Apollo — Configuration & Environment Variables

Apollo has three independent configuration layers that a recreation must keep
separate:

1. **Process environment variables** — read once (mostly at import time) via
   `os.getenv`/`os.environ`. These are deployment-level: host binds, ports,
   auth on/off, data-directory location, secrets that must exist before the
   app can boot.
2. **`data/settings.json`** (via `src/settings.py`) — a single JSON document,
   merged over `DEFAULT_SETTINGS`, editable at runtime from the Settings UI or
   `POST /api/auth/settings`. This is product behavior: model routing, TTS/STT
   providers, search providers, memory budgets, keybinds.
3. **`data/features.json`** — a small boolean feature-flag document, merged
   over `DEFAULT_FEATURES`.

`.env` (loaded by `python-dotenv`) only ever feeds layer 1. There is no code
path where `settings.json` values flow back into `os.environ`, and no code
path where editing `.env` changes `settings.json` — the two are read by
different modules for different concerns, though a handful of settings keys
and env vars cover the same knob with the env var acting as a fallback
default (documented per-key below, e.g. `llama_server_path` /
`APOLLO_LLAMA_SERVER`).

## 1. `.env` handling

`app.py`:

```python
# app.py
from dotenv import load_dotenv
...
load_dotenv(encoding="utf-8-sig")
```

This runs at module import time, before `core.constants`/`src.constants` (and
therefore before most `os.getenv(...)` default resolution) executes, so `.env`
values are visible to every subsequent `os.getenv` call in the process.
`encoding="utf-8-sig"` strips a UTF-8 BOM some Windows editors add when saving
`.env`. Precedence is standard `python-dotenv` behavior: **variables already
present in the shell environment win**; `.env` only fills in what's unset.
`.env.example` at the repo root is the annotated template (`cp .env.example
.env`); it ships commented-out with inline explanations rather than live
defaults, so a fresh `.env` file that's just a copy is a no-op until values
are uncommented.

`start-macos.sh` re-implements a small `.env` parser in bash (see §7) so the
shell-level port probe / Homebrew steps see the same `APP_PORT`/`APP_BIND`
before Python ever starts; `launch-windows.ps1` does not parse `.env` itself —
`.env` is picked up once Python (`app.py`/`setup.py`) runs.

## 2. Environment variables — exhaustive list

Enumerated by grepping `os.getenv(` / `os.environ.get(` / `os.environ[` across
the repository (excluding `tests/`). Grouped by concern; file:line cites the
first/primary read site.

### 2.1 Auth & security

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `AUTH_ENABLED` | `"true"` | `.lower() != "false"` gate. `false` disables login entirely — every `require_user`/`require_admin` check short-circuits to "allowed". This is the desktop-mode switch. | `app.py:155`, `core/middleware.py:49`, `routes/auth_routes.py:105`, `routes/session_routes.py:26`, `src/auth_helpers.py:125` (five independent read sites — no single shared constant; each module calls `os.getenv` itself) |
| `LOCALHOST_BYPASS` | `"false"` | `.lower() == "true"`. When true AND `AUTH_ENABLED` is otherwise on, requests from loopback (`127.0.0.1`) are treated as authenticated without a session cookie. Dev-only; explicitly documented in `.env.example` as "Keep false for Docker, LAN, reverse proxy, and any shared deployment." | `app.py:156`, `src/auth_helpers.py:106` |
| `SECURE_COOKIES` | `"false"` | `.lower() == "true"`. Sets the `Secure` flag on the session cookie in `routes/auth_routes.py`. Must be true when served over HTTPS via a reverse proxy; a plain-HTTP deployment with this true would drop the cookie. | `routes/auth_routes.py:168` |
| `ALLOWED_ORIGINS` | `"http://localhost,http://127.0.0.1"` | Comma-split into the CORS allow-list. | `app.py:87` |
| `APOLLO_INTERNAL_TOKEN` | `secrets.token_hex(32)` (generated if unset) | Per-process bearer token (`X-Apollo-Internal-Token` header) letting the agent's own tool-call HTTP loopback hit admin-gated routes without a session cookie. Never persisted; regenerates every process start unless pinned. | `core/middleware.py:21` |
| `APOLLO_ADMIN_USER` | `""` | Pre-seeds the first-run admin username in `setup.py` (skips the interactive prompt if both user+password env vars are set). | `setup.py:119` |
| `APOLLO_ADMIN_PASSWORD` | `""` | Pre-seeds the first-run admin password. If unset and stdin is a TTY, `setup.py` prompts; if unset and non-interactive, a random password is generated and printed once. | `setup.py:120,146,150` |
| `APOLLO_SKIP_ADMIN_PROMPT` | unset | If set (any value) and stdin is a TTY, `setup.py` skips the interactive admin-creation prompt. | `setup.py:125` |
| `APOLLO_SKIP_RUN_HINT` | unset | Suppresses `setup.py`'s "now run uvicorn…" hint — every launcher script sets this since it starts the server itself right after. | `setup.py:239` |

### 2.2 Networking / process

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `APP_PORT` | `"7000"` | Docker Compose host-port mapping (`${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000` — the *container's* internal uvicorn port is hardcoded `7000` in the `Dockerfile` `CMD`; `APP_PORT` only remaps the host side). Also read directly by `app.py:864` to build the local lmproxy base URL, and by `companion/pairing.py:26`, `services/paperclip/browser_use_verifier.py:104`. | `docker-compose.yml`, `app.py:864` |
| `APOLLO_PORT` | `"7860"` (bundle/mac) / `"7000"` (Windows launcher) | Overrides the port a native launcher binds to. Precedence in `scripts/windows_launcher.py`: `APOLLO_PORT` → `APP_PORT` → `"7000"`. In `packaging/apollo_boot.py` (macOS PyInstaller bundle): `APOLLO_PORT` → `"7860"`. | `packaging/apollo_boot.py:208`, `scripts/windows_launcher.py:29` |
| `APOLLO_HOST` | `"127.0.0.1"` | Bind host for native launchers. Precedence in `windows_launcher.py`: `APOLLO_HOST` → `APP_BIND` → `"127.0.0.1"`. | `packaging/apollo_boot.py:209`, `scripts/windows_launcher.py:30` |
| `APP_BIND` | `"127.0.0.1"` | Docker Compose host-bind address for the web UI port mapping; also a fallback source for `APOLLO_HOST` in the Windows launcher. | `docker-compose.yml`, `scripts/windows_launcher.py:30` |
| `REQUEST_HARD_TIMEOUT` | `"45"` | `float(...)`. Hard wall-clock ceiling (seconds) applied somewhere in the request pipeline (distinct from the per-setting `agent_stream_timeout_seconds`). | `app.py:120` |
| `PORT` | n/a (set, not read, by Apollo) | Apollo does **not** read a bare `PORT` for its own bind. `services/paperclip/runtime.py:74` *sets* `env["PORT"] = str(cfg.port)` when spawning the Paperclip Node sidecar subprocess — that `PORT` belongs to Paperclip's own server, not Apollo's. UNCERTAIN: no code path in this repo has Apollo itself reading a bare `PORT` env var; the task brief's "PORT" likely refers to `APP_PORT`/`APOLLO_PORT` above. |

### 2.3 LLM hosts / model discovery / local-model runtime

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `LLM_HOST` | `"localhost"` | Primary LLM server hostname used to build default endpoint/embedding URLs. | `src/constants.py:30`, `src/embeddings.py:46` |
| `LLM_HOSTS` | `""` | Comma-separated extra hosts scanned for model discovery (`src/model_discovery.py` probes common serve ports incl. Ollama's 11434). | `src/constants.py:31`, `src/model_discovery.py:108` |
| `OPENAI_API_KEY` | `None` | Optional OpenAI key module-level constant; prefer adding providers in-app. | `src/constants.py:32` |
| `OLLAMA_BASE_URL` / `OLLAMA_URL` | unset | Either name accepted (first-match) as an Ollama base URL override. | `app.py:1033-1034` |
| `APOLLO_MODELS_DIRS` | unset | `,` or `os.pathsep`-separated directories scanned for on-disk GGUF chat/embedding models. Only consulted when the `local_model_dirs` setting is empty (settings → env → built-in per-platform defaults). | `services/localmodels/config.py:8,42` |
| `APOLLO_LLAMA_SERVER` | unset | Path to the `llama-server` binary. Only consulted when the `llama_server_path` setting is empty (settings → env → auto-detect ""). This key is **not** listed in `DEFAULT_SETTINGS` — it's read/written directly as a raw dict key by `get_llama_server_path`/`set_llama_server_path`. | `services/localmodels/config.py:9,73` |
| `APOLLO_LLAMA_CONTEXT` | `"16384"` | `int(...)`; context-length cap fed into `max(..., self._context)` when sizing the llama-server process. | `services/localmodels/server_manager.py:182` |
| `APOLLO_LOCAL_MODEL_ID` | `""` | Fallback source for the browser-use verifier's model id when `APOLLO_BROWSER_USE_MODEL`/`PAPERCLIP_MODEL_NAME` are unset. | `services/paperclip/browser_use_verifier.py:129` |
| `APOLLO_LOCAL_MODEL_IT` | unset | Test-only opt-in gate (`!= "1"` → skip) for an integration test that needs a real local model server running. | `tests/test_localmodels_integration.py:6` |
| `HF_HOME` | set by `apollo_boot.py` to `<home>/data/hf_cache` in the bundle | HuggingFace cache root. | `packaging/apollo_boot.py`, `scripts/diffusion_server.py:101` |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | unset | Either name accepted for a HF auth token (gated model downloads). | `scripts/diffusion_server.py:101` |
| `HUGGINGFACE_HUB_CACHE` | unset | Alternate HF cache override, read inside a generated snippet in `routes/cookbook_routes.py:1918`. | `routes/cookbook_routes.py:1918` |
| `HF_HUB_DOWNLOAD_MAX_WORKERS` | `"8"` | Parallel download workers for `scripts/hf_download.py`. | `scripts/hf_download.py:167` |
| `HF_HUB_DISABLE_PROGRESS_BARS` | set to `"0"` by the script itself | Forces progress bars on for `hf_download.py`'s CLI output. | `scripts/hf_download.py:151` |

### 2.4 Embeddings / vector store

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `EMBEDDING_URL` | `http://{LLM_HOST}:11434/v1/embeddings` | OpenAI-compatible embeddings endpoint. Also **written** at runtime (`os.environ["EMBEDDING_URL"] = url`) when the embedding provider is changed via `routes/embedding_routes.py`, so later same-process reads see the new value without a restart. | `src/embeddings.py:44`, `routes/embedding_routes.py:238,287` |
| `EMBEDDING_MODEL` | `_DEFAULT_MODEL` (`sentence-transformers/all-MiniLM-L6-v2` family) | Model name at the embeddings endpoint. Also written at runtime alongside `EMBEDDING_URL`. | `src/embeddings.py:48`, `routes/embedding_routes.py:241,289` |
| `EMBEDDING_BLOCK_PRIVATE_IPS` | `"false"` | `.lower() == "true"`; when true, blocks the embedding client from resolving to RFC1918/loopback addresses (SSRF hardening for a user-supplied embedding URL). | `routes/embedding_routes.py:259` |
| `FASTEMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local ONNX fallback embedding model when no HTTP embeddings API is reachable. | `src/embeddings.py:116`, `routes/embedding_routes.py:70` |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` (unset) | Cache dir for the fastembed ONNX model download. Overridden to `<home>/data/fastembed_cache` inside the macOS bundle. | `src/embeddings.py:120`, `routes/embedding_routes.py:39`, `packaging/apollo_boot.py` |
| `CHROMADB_HOST` / `CHROMADB_PORT` | `""` / `"8000"` | Optional external ChromaDB server; unset uses the embedded on-disk store. | `src/chroma_client.py:73,77` |
| `CHROMA_PERSIST_DIR` | `""` | Explicit on-disk persistence dir override for the embedded Chroma client. | `src/chroma_client.py:45` |
| `CHROMADB_CONNECT_TIMEOUT` | `"2.0"` | `float(...)` connect timeout for the external-server client. | `src/chroma_client.py:31` |

### 2.5 Database & data location

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `DATABASE_URL` | `sqlite:///{data_path('app.db')}` | SQLAlchemy connection string. `core/database.py` auto-`makedirs`s the parent dir of a `sqlite:///` path so a fresh checkout doesn't fail on a missing `data/`. The macOS bundle launcher pins this explicitly (see §7) so a stray dev-machine `DATABASE_URL` (e.g. Postgres) isn't inherited. | `core/database.py:33`, `packaging/apollo_boot.py` (`setdefault`) |
| `APOLLO_DATA_DIR` / `DATA_DIR` | resolved by `src/runtime_paths.data_root()` — see §8 | Explicit override of the entire data root; checked in that order (`APOLLO_DATA_DIR` first). Also read directly (not through `data_path()`) by several modules: `src/bg_jobs.py:41` (`DATA_DIR`, default `"data"`), `routes/cookbook_routes.py:63` (`DATA_DIR`, default `"data"`), `setup.py:46,51`. | `src/runtime_paths.py:78-79` |
| `APOLLO_MIGRATE_DATA` | `"false"` | `.lower() == "true"`; when the platform data root is "pending" migration status, opts into `setup.py` performing the legacy→platform data migration on this run. | `setup.py:51` |
| `APOLLO_HOME` | `~/Library/Application Support/Apollo` (macOS bundle) | Root of the writable per-user home for the PyInstaller bundle (seeded from the read-only bundle on first run — see doc 11 §3). | `packaging/apollo_boot.py:48` |
| `APOLLO_MAIL_ATTACHMENTS_DIR` | `<DATA_DIR>/mail-attachments` | Override for where downloaded email attachments are written/served from. | `routes/email_helpers.py:236`, `routes/document_routes.py:1531` |
| `APOLLO_FALLBACK_OWNER` | `"owner@localhost"` | Owner attributed to calendar events with no resolvable user (single-user-mode fallback). | `routes/calendar_routes.py:27` |
| `APOLLO_SINGLE_USER` | `"1"` | `!= "0"`; gates single-user calendar behavior. | `routes/calendar_routes.py:28` |
| `TMPDIR` | OS default | Read (not set) to locate the system temp dir for tool execution scratch space. | `src/tool_execution.py:114` |

### 2.6 Search

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `SEARXNG_INSTANCE` | `http://localhost:8080` | External SearXNG override. Native installs ignore this in favor of the managed sidecar on `searxng_port` (setting, default `8893`); Docker Compose sets it to `http://searxng:8080`. | `src/constants.py:33`, `services/search/providers.py:57` |
| `SEARXNG_GENERAL_ENGINES` | `"bing,mojeek,presearch"` | Engine list for SearXNG's general-search category. | `services/search/providers.py:170` |
| `SEARXNG_SECRET` | generated on first Docker boot if blank | SearXNG cookie/CSRF secret, injected into its settings template by the compose wrapper entrypoint. | `docker-compose.yml` / `.env.example` |
| `DATA_BRAVE_API_KEY` | `""` | Fallback source for the Brave Search API key when the `brave_api_key` setting is unset. | `services/search/providers.py:323,334` |
| `GOOGLE_API_KEY` / `GOOGLE_PSE_CX` | `""` | Google Programmable Search Engine key/CX fallback. | `services/search/providers.py:500,501` |
| `TAVILY_API_KEY` | `""` | Tavily API key fallback. | `services/search/providers.py:558` |
| `SERPER_API_KEY` | `""` | Serper.dev API key fallback. | `services/search/providers.py:611` |
| `APOLLO_CRAWL4AI_ALLOW_PRIVATE` | `"false"` | `.lower() == "true"`; when false (default), blocks crawl4ai fetches from resolving to private/loopback addresses (SSRF guard). | `services/research/crawl4ai_adapter.py:56` |
| `APOLLO_ALLOW_PRIVATE_CALDAV` | `"0"` | `.lower() in {"1","true","yes"}`; opts a CalDAV sync target into allowing private/loopback URLs (default blocked, same SSRF class as crawl4ai). | `src/caldav_sync.py:52` |

### 2.7 Browser / embedded Chromium

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `APOLLO_BROWSER_HEADLESS` | `"true"` | `.lower() != "false"`; embedded-browser (agent browser API + Browser panel) headless toggle. | `services/browser/embedded_browser.py:83,242` |
| `APOLLO_BROWSER_EXECUTABLE_PATH` | `""` | Explicit Chromium binary path — set by the macOS bundle boot shim and by `scripts/run-e2e.sh` to point at Playwright's cached/bundled Chromium. | `services/browser/embedded_browser.py:84,244` |
| `PLAYWRIGHT_BROWSERS_PATH` | unset | Standard Playwright var; `apollo_boot.py` sets it (`setdefault`) to the bundle's `playwright-browsers/` dir if present, so the frozen app doesn't need a global Playwright cache. | `packaging/apollo_boot.py` (`_configure_bundled_playwright`) |
| `APOLLO_E2E_CHROMIUM` / `APOLLO_E2E_BASE_URL` | required for `tests/e2e` | Set by `scripts/run-e2e.sh`; the Chromium executable path and the running server's base URL the Playwright journeys drive against. | `tests/e2e/test_browser_smoke.py` |

### 2.8 Paperclip sidecar

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `PAPERCLIP_ENABLED` | `"false"` | Master opt-in for the bundled Paperclip agent-management UI. | `.env.example`, `app.py:897` |
| `PAPERCLIP_MODE` | `"docker"` | `docker` (Compose sidecar) / `native` (Apollo supervises `paperclipai` itself + auto-provisions Node — set by the macOS `.app` and Windows launcher) / `external` (point at an already-running Paperclip via `PAPERCLIP_URL`). | `services/paperclip/config.py:66` |
| `PAPERCLIP_URL` | derived from mode | Base URL Apollo's reverse proxy forwards `/paperclip` to. | `services/paperclip/config.py:71` |
| `PAPERCLIP_BROWSER_URL` | `http://localhost:{PAPERCLIP_PORT}` | URL used by the browser-facing proxy path specifically. | `services/paperclip/config.py:74` |
| `PAPERCLIP_PORT` | `"3100"` | Port the Paperclip sidecar listens on (also injected as `PORT` into the Paperclip subprocess env — see §2.2). | `services/paperclip/config.py:67` |
| `PAPERCLIP_MODEL_ENDPOINT` | `"ollama"` | `ollama` \| `apollo` \| `custom` — which local-model wiring Paperclip's opencode-local agents use. | `services/paperclip/config.py:75` |
| `PAPERCLIP_MODEL_BASE_URL` / `PAPERCLIP_MODEL_NAME` | unset | Base URL / model name for the endpoint above. | `services/paperclip/config.py:45-60` |
| `PAPERCLIP_MODEL_API_KEY` | `"local"` (from `.env.example`) | API key handed to Paperclip's local-model client. | `.env.example` |
| `PAPERCLIP_PROXY_TOKEN` | `""` | Fallback source for the browser-use verifier's model API key. | `services/paperclip/browser_use_verifier.py:122` |
| `PAPERCLIP_AUTH_SECRET` | `""` | Stable session-auth secret for the Paperclip sidecar; compose maps it to `BETTER_AUTH_SECRET` with a soft empty default so a plain `up` never hard-fails. | `.env.example`, `docker-compose.yml` |
| `PAPERCLIP_COLLECTOR_TOKEN` / `PAPERCLIP_COMPANY_ID` | `""` | Live-events collector auth for streaming real Paperclip agent activity onto the Floor UI; tokenless works against Paperclip's `local_trusted` mode. | `app.py:743-744` |
| `PAPERCLIP_EVENTS_TOKEN` | `""` | Token expected on inbound Paperclip event-ingest HTTP calls. | `routes/paperclip_routes.py:54` |
| `PAPERCLIP_HOME` | unset | If set, forwarded into the Paperclip subprocess environment verbatim. | `services/paperclip/runtime.py:76-77` |
| `PAPERCLIP_CLI` / `PAPERCLIP_VERSION` | unset / `DEFAULT_VERSION` | Override the `paperclipai` CLI path / pinned version used by the native-mode runtime bootstrap. | `services/paperclip/runtime.py:180,185` |
| `PAPERCLIP_NODE_VERSION` | unset | Override the Node version `services/paperclip/node_bootstrap.py` provisions for native mode. | `services/paperclip/node_bootstrap.py:147` |
| `APOLLO_BROWSER_USE_*` (`PYTHON`, `MODEL`, `USERNAME`, `PASSWORD`, `LLM_PROVIDER`, `API_KEY`, `BASE_URL`) | mostly unset, `LLM_PROVIDER` defaults `"local"` | Configuration for the isolated `browser-use` venv Paperclip's verifier drives. | `services/paperclip/browser_use_verifier.py` (multiple lines 46-189) |
| `BROWSER_USE_API_KEY` / `BROWSER_USE_BASE_URL` | unset | Non-`APOLLO_`-prefixed fallbacks for the same. | `services/paperclip/browser_use_verifier.py:182-183` |

### 2.9 Email (MCP email server + routes)

All in `mcp_servers/email_server.py` unless noted; these configure the built-in IMAP/SMTP MCP server used for email tools when no per-account settings are saved (settings take precedence — see `routes/email_helpers.py:570-583`).

| Variable | Default |
|---|---|
| `IMAP_HOST` | `"localhost"` |
| `IMAP_PORT` | `"31143"` |
| `IMAP_USER` / `IMAP_PASSWORD` | `""` |
| `IMAP_SSL` | `"false"` |
| `IMAP_STARTTLS` | `"true"` |
| `SMTP_HOST` | `""` |
| `SMTP_PORT` | `"465"` |
| `SMTP_SECURITY` | `""` |
| `SMTP_USER` / `SMTP_PASSWORD` | `""` |
| `SMTP_STARTTLS` | `"false"` |
| `SMTP_SSL` | `"true"` |
| `EMAIL_FROM` | `""` |
| `ARCHIVE_FOLDER` | `"Archive"` |
| `TRASH_FOLDER` | `"Trash"` |
| `EMAIL_SOCKET_TIMEOUT` | `"20"` (float) |
| `APOLLO_IMAP_TIMEOUT_SECONDS` | unset | coerced via `_coerce_imap_timeout_seconds`, `routes/email_helpers.py:623` |
| `APOLLO_INPROCESS_POLLERS` | `"1"` | `.strip().lower()`; set to `0` to drive polling externally (cron/systemd) instead of in-process, avoiding a double-poll race on SQLite. `routes/email_pollers.py:1072` |

### 2.10 Contacts / calendar

| Variable | Default | Effect |
|---|---|---|
| `CARDDAV_URL` / `CARDDAV_USERNAME` / `CARDDAV_PASSWORD` | `""` | Fallback source when no per-account CardDAV settings are saved. `routes/contacts_routes.py:47-49` |

### 2.11 Task scheduling / cleanup / misc

| Variable | Default | Effect | Read at |
|---|---|---|---|
| `APOLLO_INPROCESS_TASKS` | `"1"` | `.strip().lower()`; `0` disables the in-process scheduled-task runner. | `app.py:1215` |
| `APOLLO_SCRIPT_HOST` | `"localhost"` | Host the built-in `run_script` scheduled-task action executes on; set to an SSH alias to run remotely. | `src/builtin_actions.py:604` |
| `CLEANUP_ENABLED` | `"True"` | `.lower() == "true"`. | `src/constants.py:37` |
| `CLEANUP_INTERVAL_HOURS` | `"24"` | `int(...)`. | `src/constants.py:38` |
| `APOLLO_PERSONAL_UPLOAD_MAX_BYTES` | `str(25*1024*1024)` (25 MB) | Max upload size for the Personal docs upload route. | `routes/personal_routes.py:17` |
| `APOLLO_DISABLE_MCP` | unset | `.lower() in ("1","true","yes")`; disables the built-in stdio MCP server layer (`src/builtin_mcp.py`) — used by `scripts/run-e2e.sh` to keep the E2E server lean. | `src/builtin_mcp.py:86` |
| `LOG_LEVEL` | `"WARNING"` | `.upper()`; root log level for `scripts/_lib/cli.py`-based scripts. | `scripts/_lib/cli.py:53` |
| `DEMO_IMAP_HOST/PORT/USER/PASSWORD`, `DEMO_ALLOW_WIPE` | `localhost`/`31143`/`demo@apollo.local`/`demodemo` / unset | Demo-data email seeding script only. | `scripts/demo_email/seed_demo_emails.py` |

### 2.12 Passed-through / native-tool environment (not read by Apollo's own config)

`src/subproc_env.py` builds a minimal allowlisted environment for every
agent-spawned subprocess (bash/python tools, background jobs, shell service,
MCP stdio servers) — default-deny plus a secret-shaped denylist scrub. `_PASS`
is the allowlist of host env vars forwarded verbatim (`PATH`, `HOME`, etc. —
read at `src/subproc_env.py:68`); `services/searxng/runtime.py:122` does the
analogous pass-through for the SearXNG sidecar subprocess. `ComSpec` (Windows
shell path, default `"cmd.exe"`) is read directly by `core/platform_compat.py:235`,
`routes/cookbook_routes.py:396`, `routes/shell_routes.py:705`, and
`src/bg_jobs.py:128` wherever a Windows script needs to be invoked via `cmd
/c`. `APPDATA` is read by `routes/vault_routes.py:39` and `src/builtin_mcp.py:30`
for Windows per-user config dirs.

## 3. `data/settings.json` — full `DEFAULT_SETTINGS`

Single source of truth: `src/settings.py`. `load_settings()` does
`{**DEFAULT_SETTINGS, **saved}` — the file only needs to contain the keys a
user has actually changed; anything absent falls back to the default below.
Reads are cached for 2 seconds (`_CACHE_TTL`) so hot paths (every chat
message, every preprocess step) don't re-parse the file on each call;
`save_settings()` invalidates the cache immediately via `atomic_write_json`
(`core/atomic_io.py` — temp-file + `fsync` + `os.replace`, so a crash mid-save
can't truncate `settings.json`).

```python
# src/settings.py — DEFAULT_SETTINGS (abridged comments kept, values verbatim)
DEFAULT_SETTINGS = {
    "activity_ledger_enabled": True,
    "activity_ledger_max_events": 10000,
    "agent_autonomy": "auto",                 # "auto" | "observe" (blocks mutating tools)
    "memory_pack_sync_dir": "",                # Memory Sync housekeeping; "" = disabled
    "mixture_routing_enabled": False,          # route short chat msgs to a "light" model
    "light_endpoint_id": "",
    "light_model": "",
    "memory_recall_max": 3,
    "memory_pinned_max": 15,
    "image_gen_enabled": True,
    "image_model": "",
    "image_quality": "medium",
    "vision_model": "",
    "vision_enabled": True,
    "vision_model_fallbacks": [],
    "app_public_url": "",                      # base URL for links in outgoing alerts
    "tts_enabled": True,
    "tts_provider": "disabled",
    "tts_model": "tts-1",
    "tts_voice": "alloy",
    "tts_speed": "1",
    "stt_enabled": False,
    "stt_provider": "disabled",
    "stt_model": "base",
    "stt_language": "",
    "voicebox_url": "http://127.0.0.1:17493", # local Voicebox app, shared by TTS+STT
    "search_provider": "searxng",
    "search_fallback_chain": ["duckduckgo"],
    "searxng_managed": True,                   # managed no-Docker sidecar
    "searxng_port": 8893,
    "web_access_mode": "manual",                # "manual" | "auto" | "always"
    "search_url": "",
    "search_result_count": 5,
    "search_safesearch": "strict",              # "strict" | "moderate" | "off"
    "brave_api_key": "", "google_pse_key": "", "google_pse_cx": "",
    "tavily_api_key": "", "serper_api_key": "",
    "research_endpoint_id": "", "research_model": "", "research_search_provider": "",
    "research_max_tokens": 16384,
    "research_extraction_timeout_seconds": 90,
    "research_extraction_concurrency": 3,
    "research_run_timeout_seconds": 1800,       # 0 = unlimited; bounded [60, 86400] otherwise
    "agent_max_tool_calls": 0,
    "agent_input_token_budget": 6000,
    "agent_input_token_hard_max": 200_000,      # ceiling on the AUTO-derived budget only
    "agent_stream_timeout_seconds": 300,
    "tool_path_extra_roots": [],                # extra read_file/write_file roots
    "task_endpoint_id": "", "task_model": "",
    "local_model_dirs": [],                     # falls back to APOLLO_MODELS_DIRS env
    "default_endpoint_id": "", "default_model": "",
    "default_model_fallbacks": [],               # [{"endpoint_id":..,"model":..}, ...]
    "utility_endpoint_id": "", "utility_model": "",
    "utility_model_fallbacks": [],
    "reviewer_endpoint_id": "", "reviewer_model": "",  # falls back to Utility, then Default
    "teacher_model": "", "teacher_enabled": False,
    "skill_autosave_min_confidence": 0.85,
    "skill_max_injected": 3,
    "reminder_channel": "browser",               # "browser" | "email" | "ntfy"
    "reminder_llm_synthesis": False,
    "reminder_ntfy_topic": "Reminders",
    "reminder_email_to": "",
    "urgent_email_prompt": "Flag as urgent: ...", # long default triage prompt, see source
    "keybinds": {
        "search": "ctrl+k", "toggle_sidebar": "ctrl+b", "new_session": "ctrl+alt+n",
        "star_session": "ctrl+alt+s", "delete_session": "ctrl+alt+d",
        "admin_panel": "ctrl+shift+u", "cancel": "escape",
    },
}
```

Two keys the task brief calls out by name are handled specially:

- **`memory_pack_sync_dir`** — present in `DEFAULT_SETTINGS` (`""`), consumed
  by the Memory Sync housekeeping task; empty string means the feature is off.
- **`llama_server_path`** — **not** present in `DEFAULT_SETTINGS`. It's
  managed as a raw dict key by `services/localmodels/config.py`'s
  `get_llama_server_path()`/`set_llama_server_path()`, which reads/writes it
  via `load_settings()`/`save_settings()` directly rather than through
  `get_setting()`. Because `load_settings()` merges over `DEFAULT_SETTINGS`
  but doesn't require every persisted key to be a default, this works, but it
  means `get_setting("llama_server_path")` on an untouched install returns
  `None` (not `""`) — callers use `.get("llama_server_path") or ""`.
  Resolution order: settings key → `APOLLO_LLAMA_SERVER` env → `""`
  (auto-detect).

### 3.1 Per-user setting overrides

`get_user_setting(key, owner, default)` (`src/settings.py:286`) resolves a
whitelisted subset of keys from the caller's per-user prefs
(`routes/prefs_routes.py:_load_for_user`) before falling back to the global
setting:

```python
_PER_USER_KEYS = {
    "vision_model", "vision_enabled", "vision_model_fallbacks",
    "image_model", "image_gen_enabled", "image_quality",
    "default_endpoint_id", "default_model", "default_model_fallbacks",
    "utility_endpoint_id", "utility_model", "utility_model_fallbacks",
    "research_endpoint_id", "research_model",
    "reviewer_endpoint_id", "reviewer_model",
}
```

Any other key ignores `owner` and behaves exactly like `get_setting(key)`. If
the prefs module import fails (circular import during early boot), the
per-user lookup degrades to the global setting rather than raising.

### 3.2 `is_setting_overridden(key)`

Reads the raw saved file (bypassing the `DEFAULT_SETTINGS` merge) to
distinguish "explicitly set to the default value" from "never touched" — used
by adaptive-budget logic that needs to know whether the *user* chose a value
or is riding the default.

## 4. `data/features.json` — `DEFAULT_FEATURES`

```python
# src/settings.py
DEFAULT_FEATURES = {
    "web_search": True, "web_fetch": True, "deep_research": False,
    "browser": True, "memory": True, "document_editor": True,
    "rag": True, "sensitive_filter": True, "gallery": True,
}
```

Same load/save/cache mechanics as settings, via `load_features()` /
`save_features()`. `POST /api/auth/features` (admin-only) only updates keys
already present in the loaded dict, and only if the incoming value is a
`bool` — an unrecognized or non-bool key in the request body is silently
ignored, not added.

## 5. The auth settings endpoint (`routes/auth_routes.py`)

```python
@router.get("/settings")
async def get_settings(request: Request):
    settings = _load_settings()
    try:
        _require_admin_user(request)
        return settings
    except HTTPException:
        return scrub_settings(settings)

@router.post("/settings")
async def set_settings(request: Request):
    _require_admin_user(request)          # admin-only to write
    body = await request.json()
    current = _load_settings()
    for key in DEFAULT_SETTINGS:          # only known keys can be written
        if key in body:
            current[key] = body[key]
    _save_settings(current)
    return current
```

`GET /api/auth/settings` is deliberately **auth-exempt at the route level**
(the frontend and the pre-login page both need it for keybinds + TTS prefs)
but branches internally: a real admin, or a caller in desktop mode
(`AUTH_ENABLED=false`, handled identically by `require_admin` — see doc 06),
gets the full unscrubbed document; anyone else gets `scrub_settings(settings)`.
`POST` always requires admin — no desktop-mode-only path bypasses that.

### 5.1 `src/settings_scrub.py` — secret masking

Dependency-light (stdlib only), deliberately separable from the FastAPI/auth
import chain so it's unit-testable in isolation. Masks any key matching a
secret-shaped suffix, recursing into nested dicts/lists so a secret buried
under a non-secret parent key (`{"email_account": {"smtp_password": "..."}}`)
is still blanked:

```python
_SECRET_KEY_PATTERNS = (
    "_api_key", "_apikey", "_password", "_passwd", "_pass", "_pwd",
    "_secret", "_client_secret", "_token", "_access_token", "_refresh_token",
    "_credential", "_credentials", "_key",
)
_SECRET_KEY_ALLOW = ("google_pse_cx",)  # public identifier, not a secret
```

Only non-empty **string** values are blanked (to `""`); presence of the key
is preserved so the frontend can still render "configured" vs "not
configured" without ever receiving the value.

## 6. Feature flags vs. settings vs. env — which layer owns what

| Concern | Layer |
|---|---|
| Is auth required at all | env (`AUTH_ENABLED`) — infra decision, not user-editable in the UI |
| Which chat model is default | settings (`default_model`, `default_endpoint_id`) |
| Is web search available at all | features (`web_search`) — coarse on/off, admin-only |
| Which search provider / API keys | settings (`search_provider`, `*_api_key`) |
| Data directory location | env (`APOLLO_DATA_DIR`/`DATA_DIR`) — must be known before any JSON file can even be located |
| Local model scan directories | settings (`local_model_dirs`) with env (`APOLLO_MODELS_DIRS`) as a fallback default |
| llama-server binary path | settings (`llama_server_path`, not in `DEFAULT_SETTINGS`) with env (`APOLLO_LLAMA_SERVER`) as fallback |

## 7. Per-platform defaults

### 7.1 macOS — `start-macos.sh` (terminal quick-start)

```bash
# start-macos.sh
PORT="${APOLLO_PORT:-${APP_PORT:-7860}}"   # 7860, not 7000 — macOS AirPlay Receiver holds 7000.
HOST="${APOLLO_HOST:-${APP_BIND:-127.0.0.1}}"
```
Ends by `exec`-ing `venv/bin/python3 -m uvicorn app:app` with
`PAPERCLIP_MODE=native PAPERCLIP_ENABLED=true` exported as defaults (only if
not already set):
```bash
export PAPERCLIP_MODE="${PAPERCLIP_MODE:-native}"
export PAPERCLIP_ENABLED="${PAPERCLIP_ENABLED:-true}"
```
`AUTH_ENABLED` is **not** touched by `start-macos.sh` — that script leaves the
app's own default (`true`) in place; only the packaged `.app`/`.dmg` bundle
below flips it off.

### 7.2 macOS — packaged `.app`/`.dmg` launcher (`build-macos-bundle.sh`)

The generated launcher script embedded in `Apollo.app/Contents/MacOS/Apollo`
(templated by `build-macos-bundle.sh`, `__PORT__` substituted at build time):

```bash
export APOLLO_PORT="$PORT"                 # 7860 by default
export DATABASE_URL="sqlite:///$HOME_DIR/data/app.db"
export AUTH_ENABLED="${AUTH_ENABLED:-false}"
```

Comment in the source explains why: *"The desktop bundle serves 127.0.0.1
only — a login screen on a single-user local app is pure friction, so auth is
off unless the user opts back in (export AUTH_ENABLED=true before launch, e.g.
when reverse-proxying). Server/Docker deployments are unaffected: their
default stays auth-on."* This is the literal source of the task brief's "mac
launcher exports `AUTH_ENABLED=false` `PORT=7860`" — confirmed exactly, with
the caveat that it's the **bundle** launcher (`build-macos-bundle.sh`), not
the plain dev-mode `start-macos.sh`, that sets `AUTH_ENABLED`.

`build-macos-app.sh` (the non-PyInstaller, repo-driving launcher variant) sets
`PAPERCLIP_MODE=native`/`PAPERCLIP_ENABLED=true` the same way `start-macos.sh`
does but does **not** set `AUTH_ENABLED` at all — confirmed by reading its
launcher template in full; only `build-macos-bundle.sh`'s PyInstaller-based
`.app` ships the no-login desktop default. See doc 11 for the build-script
walkthrough.

### 7.3 Windows — `launch-windows.ps1` / `scripts/windows_launcher.py`

```powershell
# launch-windows.ps1
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1"
)
```
```python
# scripts/windows_launcher.py — Apollo.exe (PyInstaller onefile)
PORT = int(os.environ.get("APOLLO_PORT", os.environ.get("APP_PORT", "7000")))
HOST = os.environ.get("APOLLO_HOST", os.environ.get("APP_BIND", "127.0.0.1"))
```
Windows default port is **7000** (no AirPlay conflict on that platform) —
confirmed both in the PowerShell param default and the compiled launcher's
env fallback chain. `AUTH_ENABLED` is untouched by either Windows launcher;
the app's own default (`true`) applies unless the operator sets it in `.env`.

### 7.4 Docker Compose

```yaml
# docker-compose.yml (paraphrased port mapping)
ports:
  - "${APP_BIND:-127.0.0.1}:${APP_PORT:-7000}:7000"
```
Container-internal uvicorn always listens on `7000` (`Dockerfile CMD`); only
the host-side bind/port are configurable via `.env`. `AUTH_ENABLED` defaults
to `true` here too — Docker/server deployments are the case the bundle
launcher's comment explicitly says stays auth-on.

## 8. Data directory resolution (`src/runtime_paths.py`)

```python
def data_root(*, env=None, repo=None, platform=None, home=None) -> Path:
    env = os.environ if env is None else env
    for key in ("APOLLO_DATA_DIR", "DATA_DIR"):
        value = env.get(key)
        if value:
            return _configured_path(value)          # explicit override wins outright
    platform_root = platform_data_root(platform=platform, env=env, home=home)
    if _platform_root_is_activated(platform_root):    # verified-migration receipt present
        return platform_root
    legacy = legacy_data_root(repo)                   # <repo>/data
    if legacy.exists():
        return legacy                                  # existing checkouts keep working
    return platform_root                               # new installs go straight to platform dir
```

Platform defaults (`platform_data_root`):

| Platform | Path |
|---|---|
| macOS (`darwin`) | `~/Library/Application Support/Apollo` |
| Windows (`win`) | `%LOCALAPPDATA%\Apollo` (falls back to `~\AppData\Local\Apollo` if `LOCALAPPDATA` unset) |
| Linux/other | `$XDG_DATA_HOME/apollo` (falls back to `~/.local/share/apollo`) |

A "migration receipt" JSON (`apollo-data-migration.json`, sibling to the
platform dir) with `{"status": "activated", "target": "<path>"}` is what
flips a fresh/legacy-less install onto the platform directory; without it, an
existing repo-local `data/` directory continues to be used indefinitely for
backward compatibility. `APOLLO_MIGRATE_DATA=true` (env, §2.5) plus
`setup.py`'s pending-migration check is what performs that one-time move.

## 9. Uncertainties

- UNCERTAIN: no automated test asserts the *exact* string list in
  `DEFAULT_SETTINGS`/`DEFAULT_FEATURES` stays in sync with this document long
  term — both dicts are quoted verbatim from `src/settings.py` as read on
  2026-08-24; future edits will drift this doc from the source of truth.
- UNCERTAIN: a bare `PORT` env var is not read anywhere in Apollo's own
  config resolution (only set, for the Paperclip subprocess). If a
  recreation needs a `PORT`-driven convention (e.g. for a PaaS), it does not
  exist in this codebase today.
