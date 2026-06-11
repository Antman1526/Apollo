# Apollo — Configuration & Environment Variables

Apollo's philosophy (`README.md`): most setup happens **inside the app** (Settings / `/setup`); `.env` is only for deployment-level defaults and pre-seeded secrets. This document lists every variable with purpose, default, and the consuming code, verified by grepping the repo for `os.getenv` / `os.environ`.

## 0. How configuration is loaded

```python
# app.py (top of module, before any other imports read env)
if os.name == "nt":   # Windows: HF/fastembed must COPY, not symlink (WinError 1463 on UNC shares)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from dotenv import load_dotenv
# encoding="utf-8-sig" tolerates a UTF-8 BOM in .env — a common Windows gotcha
# when the file is saved from Notepad. Without this, the first key parses as
# "﻿AUTH_ENABLED" ... and AUTH_ENABLED=false is silently ignored (issue #142).
load_dotenv(encoding="utf-8-sig")
```

Precedence layers, lowest to highest:
1. Code defaults (the `default=` argument of each `os.getenv`).
2. `.env` (loaded by python-dotenv; also parsed by `start-macos.sh` in shell, where already-exported shell vars win).
3. Real environment variables (Docker Compose `environment:` block, systemd unit, shell exports).
4. `data/settings.json` — for keys that have an in-app Settings UI (SMTP/IMAP, CardDAV, embedding endpoint, `local_model_dirs`, …) the settings value **overrides** the env var; the env var is only a seed/fallback.

`setup.py` copies `.env.example` → `.env` on first run if absent.

## 1. Server, bind, and lifecycle

| Variable | Default | Consumer | Effect |
|---|---|---|---|
| `APP_BIND` | `127.0.0.1` | `docker-compose.yml` port bind; `start-macos.sh` HOST fallback | host interface for the web UI |
| `APP_PORT` | `7000` (`7860` in start-macos.sh) | compose bind; `start-macos.sh`; `app.py` lmproxy base URL | host port for the web UI |
| `APOLLO_HOST` / `APOLLO_PORT` | — | `start-macos.sh` only | shell-level overrides that beat `.env` |
| `ALLOWED_ORIGINS` | `http://localhost,http://127.0.0.1` | `app.py` CORSMiddleware | comma-separated CORS origins |
| `REQUEST_HARD_TIMEOUT` | `45` (seconds) | `app.py` `_RequestTimeoutMiddleware` | abort non-exempt requests with 504 (exempt prefixes: `/api/chat`, `/api/shell/stream`, `/api/research`, `/api/model/download`, `/api/model/probe`, `/api/model-endpoints`, `/api/cookbook/setup`, `/api/upload`, `/api/image`) |
| `APOLLO_INPROCESS_TASKS` | `1` | `app.py` startup | `0` disables the in-process scheduled-task runner (drive via cron) |
| `APOLLO_INPROCESS_POLLERS` | `1` | `routes/email_pollers.py:1064` | `0` disables in-process email polling (use `scripts/apollo-mail poll-scheduled/poll-summary`) |
| `APOLLO_SCRIPT_HOST` | `localhost` | scheduled-task `run_script` action | empty/local/localhost = run on app host; SSH alias = run remotely |
| `CLEANUP_ENABLED` | `True` | `core/constants.py` | toggles the cleanup service |
| `CLEANUP_INTERVAL_HOURS` | `24` | `core/constants.py` | cleanup cadence |
| `APOLLO_SKIP_RUN_HINT` / `APOLLO_SKIP_ADMIN_PROMPT` | unset | `setup.py` | suppress run hint / interactive admin prompt |

## 2. Auth & security

| Variable | Default | Consumer | Effect |
|---|---|---|---|
| `AUTH_ENABLED` | `true` | `app.py:153` (`!= "false"`), `core/middleware.py require_admin` | disables ALL auth when `false` — keep on for any network deployment |
| `LOCALHOST_BYPASS` | `false` | `app.py:154` | dev-only: direct loopback requests (no proxy headers) skip login, acting as the admin/first user (`_bypass_user()`) |
| `SECURE_COOKIES` | `false` | `routes/auth_routes.py` | mark session cookies `Secure` (set behind HTTPS proxy) |
| `APOLLO_ADMIN_USER` | `admin` | `setup.py:85` | initial admin username |
| `APOLLO_ADMIN_PASSWORD` | unset → prompt → random | `setup.py:86` | pre-seed first admin password (random `token_urlsafe(18)` printed to terminal otherwise) |
| `APOLLO_INTERNAL_TOKEN` | random `secrets.token_hex(32)` per process | `core/middleware.py:16` `INTERNAL_TOOL_TOKEN` | loopback agent tool calls hit admin routes via `X-Apollo-Internal-Token` header |
| `APOLLO_SINGLE_USER` | `1` | `routes/calendar_routes.py:27` | `0` enables strict per-user calendar scoping |
| `APOLLO_FALLBACK_OWNER` | `owner@localhost` | `routes/calendar_routes.py:26` | owner attribution when no user resolves |
| `DATABASE_URL` | `sqlite:///./data/app.db` | `core/database.py:31` | SQLAlchemy connection string |

## 3. LLM & local models

| Variable | Default | Consumer | Effect |
|---|---|---|---|
| `LLM_HOST` | `localhost` | `core/constants.py` `DEFAULT_HOST` | primary LLM server host |
| `LLM_HOSTS` | empty | `core/constants.py` | comma-separated extra hosts for model discovery (common serve ports incl. 11434 are scanned) |
| `OPENAI_API_KEY` | unset | `core/constants.py` | optional OpenAI key (prefer adding providers in-app) |
| `OLLAMA_BASE_URL` / `OLLAMA_URL` | unset → `http://host.docker.internal:11434/v1` in Docker, `http://127.0.0.1:11434/v1` native | `app.py /api/runtime`, `src/model_discovery.py:92` | Ollama endpoint discovery |
| `LM_STUDIO_URL` | unset | `src/model_discovery.py:92` | LM Studio endpoint discovery |
| `RESEARCH_LLM_ENDPOINT` | unset | declared in `.env.example`/compose only (no current Python consumer) | reserved research LLM endpoint |
| `APOLLO_MODELS_DIRS` | see §7 | `services/localmodels/config.py` | GGUF scan directories (env seed) |
| `APOLLO_LLAMA_CONTEXT` | `16384` | `services/localmodels/server_manager.py:151` | cap on llama-server `-c` context (see §7) |
| `APOLLO_LOCAL_MODEL_ID` | unset | `services/paperclip/browser_use_verifier.py:112` | model id fallback for browser-use checks |
| `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` | unset | `routes/cookbook_routes.py` (exported into download scripts) | gated HuggingFace downloads |
| `HF_HUB_DISABLE_SYMLINKS(_WARNING)` | set to `1` on Windows | `app.py:27` | copy instead of symlink HF files (UNC shares, WinError 1463) |

## 4. Search, embeddings, vector store

| Variable | Default | Consumer | Effect |
|---|---|---|---|
| `SEARXNG_INSTANCE` | `http://localhost:8080` (Docker: `http://searxng:8080`) | `core/constants.py` | SearXNG URL |
| `SEARXNG_SECRET` | generated on first Docker boot | compose searxng entrypoint | cookie/CSRF secret pin |
| `SEARXNG_GENERAL_ENGINES` | `bing,mojeek,presearch` | `services/search/providers.py:134` | engine list for general queries |
| `DATA_BRAVE_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_PSE_CX`, `TAVILY_API_KEY`, `SERPER_API_KEY` | unset | `services/search/providers.py` (compose passthrough) | alternative search providers |
| `EMBEDDING_URL` | unset (falls back to `http://{LLM_HOST}:11434/v1/embeddings`) | `routes/embedding_routes.py:231` | OpenAI-compatible `/v1/embeddings` endpoint |
| `EMBEDDING_MODEL` | unset (`all-minilm:l6-v2` suggested) | `routes/embedding_routes.py:234` | model at that endpoint |
| `EMBEDDING_BLOCK_PRIVATE_IPS` | `false` | `routes/embedding_routes.py:252` | SSRF guard for embedding endpoint tests |
| `FASTEMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | `routes/embedding_routes.py:69`, `src/embeddings.py` | local ONNX fallback model (~50MB first-run download) |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` | `src/embeddings.py` | fastembed model cache |
| `CHROMADB_HOST` | unset → **embedded** PersistentClient | `src/chroma_client.py:69` | when set, use HttpClient (Docker sets `chromadb`) |
| `CHROMADB_PORT` | `8000` | `src/chroma_client.py:73` | HTTP client port (manual host run: 8100) |
| `CHROMADB_CONNECT_TIMEOUT` | `2.0` | `src/chroma_client.py:29` | fast-fail TCP probe so startup never blocks ~60s |
| `CHROMA_PERSIST_DIR` | `<repo>/data/chroma` | `src/chroma_client.py:43` | embedded store location |
| `CHROMADB_BIND`, `NTFY_BIND`, `NTFY_BASE_URL` | `127.0.0.1` / `http://localhost:8091` | compose only | host-bind addresses for bundled services |

## 5. Email, calendar, contacts

Settings.json keys take precedence over these env fallbacks; the resolution pattern is `settings.get("smtp_host", os.environ.get("SMTP_HOST", ""))` throughout `routes/email_helpers.py:596-609` and `routes/contacts_routes.py:45-47`.

| Variable | Default | Consumer | Effect |
|---|---|---|---|
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | empty | `routes/email_helpers.py` | outgoing mail server credentials |
| `SMTP_PORT` | `465` | `routes/email_helpers.py:597` | SMTP port |
| `SMTP_SECURITY` | `ssl` | `routes/email_helpers.py:599` | `ssl` \| `starttls` \| `none` (legacy `SMTP_STARTTLS`/`SMTP_SSL` also read) |
| `IMAP_HOST` / `IMAP_USER` / `IMAP_PASSWORD` | empty | `routes/email_helpers.py:604-607` | inbox credentials |
| `IMAP_PORT` | `993` | `routes/email_helpers.py:605` | IMAP port (legacy `IMAP_STARTTLS`/`IMAP_SSL` also read) |
| `EMAIL_FROM` | empty | `routes/email_helpers.py:609` | From address |
| `TRASH_FOLDER` / `ARCHIVE_FOLDER` | provider defaults | email routes | mailbox names for delete/archive |
| `APOLLO_IMAP_TIMEOUT_SECONDS` | coerced default | `routes/email_helpers.py:649` | IMAP socket timeout |
| `APOLLO_MAIL_ATTACHMENTS_DIR` | `data/mail-attachments` | `routes/email_helpers.py:262`, `routes/document_routes.py:1508` | attachment storage dir |
| `EMAIL_SOCKET_TIMEOUT` | `20` | `mcp_servers/email_server.py:33` | MCP email server socket timeout |
| `CARDDAV_URL` / `CARDDAV_USERNAME` / `CARDDAV_PASSWORD` | empty | `routes/contacts_routes.py:45-47` | contacts sync |
| `APOLLO_ALLOW_PRIVATE_CALDAV` | `0` | `src/caldav_sync.py:50` | allow CalDAV servers on private IPs (`ValueError` otherwise) |

## 6. Feature flags & misc

| Variable | Default | Consumer | Effect |
|---|---|---|---|
| `APOLLO_DISABLE_MCP` | unset | `src/builtin_mcp.py:86` | `1/true/yes` skips built-in MCP server registration |
| `APOLLO_CRAWL4AI_ALLOW_PRIVATE` | `false` | `services/research/crawl4ai_adapter.py:56` | allow crawling private/loopback/link-local targets (trusted dev only) |
| `APOLLO_PERSONAL_UPLOAD_MAX_BYTES` | `26214400` (25 MB) | `routes/personal_routes.py:17` | personal-doc upload cap |
| `APOLLO_BROWSER_USE_MODEL`, `APOLLO_BROWSER_USE_BASE_URL`, `APOLLO_BROWSER_USE_API_KEY`, `APOLLO_BROWSER_USE_LLM_PROVIDER` (`local`\|`browser-use`), `APOLLO_BROWSER_USE_PYTHON`, `APOLLO_BROWSER_USE_USERNAME`, `APOLLO_BROWSER_USE_PASSWORD`, `APOLLO_BROWSER_HEADLESS`, `BROWSER_USE_API_KEY`, `BROWSER_USE_BASE_URL` | provider default `local` | `services/paperclip/browser_use_verifier.py` | browser-use Floor-QA verifier wiring; `local` uses Apollo's `/lmproxy/v1` + proxy token |
| `APOLLO_BROWSER_USE_VENV` | `.apollo/browser-use-venv` | `scripts/setup-browser-use-env` | isolated browser-use venv path |

In-app flags live in JSON, managed by `src/settings.py` (single source of truth, 2s TTL cache): `data/settings.json` (`DEFAULT_SETTINGS` includes `image_gen_enabled: True`, `image_model`, `image_quality: "medium"`, `vision_enabled: True`, `vision_model_fallbacks`, `skill_audit_nightly` (default on), `skill_audit_hour` (2), `skill_audit_batch` (8), `local_model_dirs`, CardDAV/SMTP/IMAP keys, …) and `data/features.json` (per-feature toggles surfaced at `/api/auth/features`). Per-user UI prefs: `data/user_prefs.json` (`routes/prefs_routes.py`, nested per-user under `_users` after migration).

## 7. Local model directories & context cap (`services/localmodels/config.py`)

Resolution order: **settings (`local_model_dirs`) → env `APOLLO_MODELS_DIRS` → built-in defaults**:

```python
# services/localmodels/config.py
ENV_VAR = "APOLLO_MODELS_DIRS"
DEFAULT_DIRS = [
    "/Volumes/MainStore/Development/AI_Models",
    os.path.expanduser("~/Desktop/AI_Models"),
]
def get_local_model_dirs() -> list[str]:
    """Configured dirs (settings) → env seed → built-in defaults."""
    settings = load_settings()
    dirs = [d for d in (settings.get("local_model_dirs") or []) if d and d.strip()]
    if dirs: return dirs
    env = os.getenv(ENV_VAR, "")
    if env.strip(): return _parse_env(env)     # split on os.pathsep if present, else ","
    return list(DEFAULT_DIRS)
```

`set_local_model_dirs()` expands `~`, drops relative/empty entries, persists to settings.json. Serving context (`server_manager.py:_serving_context`): `cap = max(int(os.getenv("APOLLO_LLAMA_CONTEXT", "16384")), self._context)`; if the model's window is known (`src/model_context._lookup_known`) serve `max(default_4096, min(known, cap))`, else serve the cap — keeps the KV cache bounded while avoiding llama.cpp HTTP 400 "request exceeds the available context size".

## 8. Paperclip configuration (`services/paperclip/config.py`)

The `.env.example` block documents the user-facing surface:

```bash
# .env.example
PAPERCLIP_ENABLED=false
PAPERCLIP_MODE=docker            # docker | native | external (native set by desktop launchers)
PAPERCLIP_PUBLIC_URL=http://localhost:7000/paperclip
PAPERCLIP_AUTH_SECRET=           # openssl rand -hex 32 — required for the Docker profile
PAPERCLIP_MODEL_ENDPOINT=ollama  # ollama | apollo | custom
PAPERCLIP_MODEL_BASE_URL=http://host.docker.internal:11434/v1
PAPERCLIP_MODEL_API_KEY=local
PAPERCLIP_MODEL_NAME=
PAPERCLIP_COLLECTOR_ENABLED=true
PAPERCLIP_COLLECTOR_TOKEN=
PAPERCLIP_COMPANY_ID=
```

`PAPERCLIP_ENABLED` (default `false`); `PAPERCLIP_MODE` ∈ `docker` (default; Compose sidecar) | `native` (Apollo supervises `paperclipai run`, auto-provisions Node — set by start-macos.sh / launch-windows.ps1) | `external` (point at an existing instance) | `off`. `PAPERCLIP_PORT` default `3100`. URL defaults: server-side `PAPERCLIP_URL` = `http://paperclip:{port}` in docker mode else `http://localhost:{port}`; browser-facing `PAPERCLIP_BROWSER_URL` = `http://localhost:{port}` (Paperclip can't be iframed under a subpath). Model wiring: `PAPERCLIP_MODEL_ENDPOINT` ∈ `ollama` (default) | `apollo` | `custom`, resolved with `PAPERCLIP_MODEL_BASE_URL` / `PAPERCLIP_MODEL_NAME` (ollama default `http://host.docker.internal:11434/v1` in docker, `http://localhost:11434/v1` native; apollo default `http://apollo:7000/v1` / `http://localhost:7000/v1`). `PAPERCLIP_MODEL_API_KEY` default `local`.

Secrets are env-or-file with auto-generation (`_read_or_make_secret`: read env → read file → write `secrets.token_hex(32)` chmod 0600):

```python
# services/paperclip/config.py
def resolve_auth_secret() -> str:    # Paperclip BETTER_AUTH_SECRET
    return _read_or_make_secret("PAPERCLIP_AUTH_SECRET", "PAPERCLIP_SECRET_FILE",
                                "~/.apollo/paperclip_secret")
def resolve_proxy_token() -> str:    # bearer token for Apollo's /lmproxy/v1
    return _read_or_make_secret("PAPERCLIP_PROXY_TOKEN", "PAPERCLIP_PROXY_TOKEN_FILE",
                                "~/.apollo/paperclip_proxy_token")
```

Related: `PAPERCLIP_PUBLIC_URL` (default `http://localhost:7000/paperclip`), `PAPERCLIP_EVENTS_TOKEN` (shared token for `POST /api/paperclip/events`; loopback-only when unset — `routes/paperclip_routes.py:53`), `PAPERCLIP_COLLECTOR_ENABLED` (default `true`), `PAPERCLIP_COLLECTOR_TOKEN` + `PAPERCLIP_COMPANY_ID` (authenticated live-events collector), `PAPERCLIP_PROXY_BASE_URL` (default `http://localhost:{APP_PORT}/lmproxy/v1`, `app.py:814`), `PAPERCLIP_CLI`, `PAPERCLIP_VERSION` (default `2026.529.0`), `PAPERCLIP_HOME`, `PAPERCLIP_NODE_BIN`/`PAPERCLIP_NPX_BIN` (set by the Node bootstrap), `PAPERCLIP_NODE_VERSION` (default `22.13.0`).

## 9. `data/` directory layout (gitignored)

Created by `setup.py` + on demand at runtime:

```text
data/
├── app.db                 # SQLite — sessions, messages, documents, tasks, endpoints, ...
├── auth.json              # {"users": {name: {password_hash, is_admin, ...}}} + signup settings
├── settings.json          # admin settings (src/settings.py)
├── features.json          # feature toggles (src/settings.py)
├── user_prefs.json        # per-user UI prefs, nested under "_users" (routes/prefs_routes.py)
├── memory.json            # JSON memory store (+ memory_doc.md)
├── presets.json           # prompt presets
├── sessions.json          # legacy session file (core/constants.py SESSIONS_FILE)
├── .app_key               # Fernet key for EncryptedText columns (mode 0600)
├── chroma/                # embedded ChromaDB PersistentClient store
├── memory_vectors/        # memory vector index
├── rag/                   # vector RAG state
├── fastembed_cache/       # ONNX embedding models
├── uploads/               # chat file uploads
├── personal_docs/         # personal-docs RAG corpus (+ runbook/)
├── personal_uploads/      # personal-doc uploads
├── generated_images/      # content-hash-named outputs served at /api/generated-image/
├── tts_cache/             # synthesized audio cache
├── deep_research/         # research run artifacts
├── mail-attachments/      # APOLLO_MAIL_ATTACHMENTS_DIR default
├── scheduled_emails.db    # mail scheduling DB
├── skills/                # SKILL.md library
├── ssh/                   # Cookbook remote-server SSH identity (Docker mount)
├── huggingface/           # Docker: container HF cache bind mount
└── local/                 # Docker: Cookbook-installed CLIs/engines bind mount
```

Sibling state outside `data/`: `logs/` (app logs), `~/.apollo/` (paperclip_secret, paperclip_proxy_token, `.node/` runtime, `browser-use-venv/` when repo-local `.apollo/` is used), `.apollo/ralph/` (Ralph loop state).

## 10. Alphabetical index of all variables

For rebuild verification — every variable read by the codebase (`os.getenv`/`os.environ`), grouped one per line:

```text
ALLOWED_ORIGINS            APOLLO_ADMIN_PASSWORD        APOLLO_ADMIN_USER
APOLLO_ALLOW_PRIVATE_CALDAV APOLLO_BROWSER_HEADLESS     APOLLO_BROWSER_USE_API_KEY
APOLLO_BROWSER_USE_BASE_URL APOLLO_BROWSER_USE_LLM_PROVIDER APOLLO_BROWSER_USE_MODEL
APOLLO_BROWSER_USE_PASSWORD APOLLO_BROWSER_USE_PYTHON   APOLLO_BROWSER_USE_USERNAME
APOLLO_BROWSER_USE_VENV    APOLLO_CRAWL4AI_ALLOW_PRIVATE APOLLO_DISABLE_MCP
APOLLO_FALLBACK_OWNER      APOLLO_HOST                  APOLLO_IMAP_TIMEOUT_SECONDS
APOLLO_INPROCESS_POLLERS   APOLLO_INPROCESS_TASKS       APOLLO_INTERNAL_TOKEN
APOLLO_LLAMA_CONTEXT       APOLLO_LOCAL_MODEL_ID        APOLLO_MAIL_ATTACHMENTS_DIR
APOLLO_MODELS_DIRS         APOLLO_NO_OPEN               APOLLO_PERSONAL_UPLOAD_MAX_BYTES
APOLLO_PORT                APOLLO_SCRIPT_HOST           APOLLO_SINGLE_USER
APOLLO_SKIP_ADMIN_PROMPT   APOLLO_SKIP_RUN_HINT         APP_BIND
APP_PORT                   ARCHIVE_FOLDER               AUTH_ENABLED
BROWSER_USE_API_KEY        BROWSER_USE_BASE_URL         CARDDAV_PASSWORD
CARDDAV_URL                CARDDAV_USERNAME             CHROMADB_BIND
CHROMADB_CONNECT_TIMEOUT   CHROMADB_HOST                CHROMADB_PORT
CHROMA_PERSIST_DIR         CLEANUP_ENABLED              CLEANUP_INTERVAL_HOURS
COMPOSE_FILE               DATABASE_URL                 DATA_BRAVE_API_KEY
DATA_DIR                   EMAIL_FROM                   EMAIL_SOCKET_TIMEOUT
EMBEDDING_BLOCK_PRIVATE_IPS EMBEDDING_MODEL             EMBEDDING_URL
FASTEMBED_CACHE_PATH       FASTEMBED_MODEL              GOOGLE_API_KEY
GOOGLE_PSE_CX              HF_HOME                      HF_HUB_DISABLE_SYMLINKS
HF_TOKEN                   HUGGINGFACE_HUB_CACHE        HUGGING_FACE_HUB_TOKEN
IMAP_HOST  IMAP_PASSWORD  IMAP_PORT  IMAP_SSL  IMAP_STARTTLS  IMAP_USER
LLM_HOST                   LLM_HOSTS                    LM_STUDIO_URL
LOCALHOST_BYPASS           NTFY_BASE_URL                NTFY_BIND
OLLAMA_BASE_URL            OLLAMA_URL                   OPENAI_API_KEY
PAPERCLIP_AUTH_SECRET      PAPERCLIP_BROWSER_URL        PAPERCLIP_CLI
PAPERCLIP_COLLECTOR_ENABLED PAPERCLIP_COLLECTOR_TOKEN   PAPERCLIP_COMPANY_ID
PAPERCLIP_ENABLED          PAPERCLIP_EVENTS_TOKEN       PAPERCLIP_HOME
PAPERCLIP_MODE             PAPERCLIP_MODEL_API_KEY      PAPERCLIP_MODEL_BASE_URL
PAPERCLIP_MODEL_ENDPOINT   PAPERCLIP_MODEL_NAME         PAPERCLIP_NODE_BIN
PAPERCLIP_NODE_VERSION     PAPERCLIP_NPX_BIN            PAPERCLIP_PORT
PAPERCLIP_PROXY_BASE_URL   PAPERCLIP_PROXY_TOKEN        PAPERCLIP_PROXY_TOKEN_FILE
PAPERCLIP_PUBLIC_URL       PAPERCLIP_SECRET_FILE        PAPERCLIP_URL
PAPERCLIP_VERSION          PGID                         PUID
RENDER_GID                 REQUEST_HARD_TIMEOUT         RESEARCH_LLM_ENDPOINT
SEARXNG_GENERAL_ENGINES    SEARXNG_INSTANCE             SEARXNG_SECRET
SECURE_COOKIES             SERPER_API_KEY               SMTP_HOST
SMTP_PASSWORD  SMTP_PORT  SMTP_SECURITY  SMTP_SSL  SMTP_STARTTLS  SMTP_USER
TAVILY_API_KEY             TRASH_FOLDER
```

(`PUID`/`PGID`/`RENDER_GID`/`COMPOSE_FILE` are Docker-only; `APOLLO_HOST`/`APOLLO_PORT`/`APOLLO_NO_OPEN` are start-macos.sh-only; `DATA_DIR` is read by `routes/cookbook_routes.py:56` to relocate `cookbook_state.json`.)
