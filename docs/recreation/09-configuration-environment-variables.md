# 09 — Configuration & Environment Variables

Apollo is configured from three layers, in precedence order:

1. **Process environment / `.env`** — read at import time (`os.getenv`) for host/port,
   auth, database, API keys, and Docker wiring. Shell env always wins over `.env`.
2. **`data/settings.json`** — admin-editable runtime settings, merged over
   `DEFAULT_SETTINGS` (`src/settings.py:31`). The single source of truth for
   provider keys, model selection, web-access behavior, and the SearXNG sidecar.
3. **`data/features.json`** — boolean feature flags merged over `DEFAULT_FEATURES`
   (`src/settings.py:178`).

All secret values below are shown as `<REDACTED>`; the real `.env.example` ships them
commented-out / blank.

---

## 1. `.env.example` — environment variables

Source: `/Users/Antman/Apollo/.env.example`. Every documented variable, grouped as
the file groups them.

### LLM configuration (`.env.example:4`)

| Var | Default | Purpose |
|---|---|---|
| `LLM_HOST` | `localhost` | Primary LLM host (`.env.example:9`; read at `src/constants.py:28`). |
| `LLM_HOSTS` | _(unset)_ | Comma-separated extra hosts for model discovery; Apollo scans common serve ports incl. Ollama's 11434 (`.env.example:13`; parsed `src/constants.py:29`). |
| `APOLLO_MODELS_DIRS` | _(unset)_ | Dirs scanned for on-disk GGUF chat/embedding models; overridden by the `local_model_dirs` setting (`.env.example:17`). |
| `OLLAMA_BASE_URL` | _(unset)_ | Optional Ollama base URL; in Docker `http://host.docker.internal:11434/v1` (`.env.example:21`). |
| `LM_STUDIO_URL` | _(unset)_ | Optional LM Studio URL (`.env.example:25`). |
| `OPENAI_API_KEY` | `<REDACTED>` | Only needed for OpenAI models; kept commented (`.env.example:29`; `src/constants.py:30`). |
| `RESEARCH_LLM_ENDPOINT` | _(unset)_ | Research-service LLM endpoint (`.env.example:32`). |

### Search & web (`.env.example:34`)

| Var | Default | Purpose |
|---|---|---|
| `SEARXNG_INSTANCE` | _(unset; constant default `http://localhost:8080`)_ | **Only** set to point at your own external instance. Native installs use the managed sidecar (`data/searxng/`, port 8893) automatically. Docker Compose sets `http://searxng:8080`. The legacy default value is treated as boilerplate and ignored (`.env.example:38`; constant `src/constants.py:31`). |
| `SEARXNG_SECRET` | `<REDACTED>` | Optional SearXNG cookie/CSRF secret; Docker generates one on first boot if blank (`.env.example:47`). |
| `SEARXNG_GIT_REF` | `4dd0bf48670727f6ae1086ffa72e76f6eb869741` | Pinned SearXNG checkout for the native sidecar installer (not in `.env.example`; `scripts/setup-searxng.sh:24` and `scripts/setup-searxng.ps1:38`). |
| `SEARXNG_PORT` | `8893` | Sidecar port at install time only (`scripts/setup-searxng.sh:14`). |

### Database (`.env.example:49`)

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLite DB path (`.env.example:54`). |

### Auth & security (`.env.example:56`)

| Var | Default | Purpose |
|---|---|---|
| `AUTH_ENABLED` | `true` | Enable authentication (`.env.example:61`). |
| `APP_BIND` | `127.0.0.1` | Docker Compose host bind address. Keep on loopback unless LAN/proxy intended (`.env.example:65`). |
| `APP_PORT` | `7000` | Web UI port; change if 7000 is taken (macOS AirPlay often holds it) (`.env.example:67`). |
| `LOCALHOST_BYPASS` | `false` | Dev-only auth bypass for loopback requests. Keep false for any shared deployment (`.env.example:71`). |
| `SECURE_COOKIES` | `false` (set `true` behind HTTPS) | Mark session cookies `Secure` (`.env.example:75`). |
| `APOLLO_ADMIN_PASSWORD` | `<REDACTED>` | Optional pre-seed of the first admin password during setup (`.env.example:79`). |
| `ALLOWED_ORIGINS` | `http://localhost,http://127.0.0.1` | CORS allowed origins; restrict in production (`.env.example:82`; consumed `app.py:85`). |

### ChromaDB vector store (`.env.example:84`)

| Var | Default | Purpose |
|---|---|---|
| `CHROMADB_HOST` | `localhost` | Manual run `localhost:8100`; Docker Compose overrides to `chromadb:8000` (`.env.example:91`). |
| `CHROMADB_PORT` | `8100` | (`.env.example:91`). |
| `CHROMADB_BIND` | `127.0.0.1` | Compose host-port bind (`.env.example:97`). |
| `NTFY_BIND` | `127.0.0.1` | Compose ntfy bind; set to a Tailscale IP to expose (`.env.example:98`). |
| `NTFY_BASE_URL` | `http://localhost:8091` | Public URL ntfy advertises (`.env.example:99`). |

### RAG / embeddings (`.env.example:104`)

| Var | Default | Purpose |
|---|---|---|
| `EMBEDDING_URL` | `http://{LLM_HOST}:11434/v1/embeddings` | OpenAI-compatible embeddings endpoint (`.env.example:110`). |
| `EMBEDDING_MODEL` | `all-minilm:l6-v2` | Embedding model at that endpoint (`.env.example:113`). |
| `FASTEMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local ONNX fallback model, ~50MB on first run (`.env.example:117`). |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` | (`.env.example:118`). |

### Misc runtime gates (`.env.example:120`)

| Var | Default | Purpose |
|---|---|---|
| `CLEANUP_INTERVAL_HOURS` | `24` | Cleanup cadence (`.env.example:125`; `src/constants.py:36`). |
| `CLEANUP_ENABLED` | `True` | (`src/constants.py:35`). |
| `APOLLO_INPROCESS_POLLERS` | `1` | In-process email pollers; set `0` if cron/systemd drives polling (avoid SQLite races) (`.env.example:131`). |
| `APOLLO_INPROCESS_TASKS` | `1` | In-process scheduled-task runner (`.env.example:137`). |
| `APOLLO_SCRIPT_HOST` | `localhost` | Host for the `run_script` scheduled-task action (`.env.example:142`). |

### GPU support (Docker Compose) (`.env.example:144`)

`COMPOSE_FILE` is the native `docker compose` colon-separated overlay list. Pick one GPU
overlay or leave commented for CPU:
- NVIDIA: `COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml` (`.env.example:154`).
- AMD ROCm: `COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml` + `RENDER_GID` (`.env.example:158-160`).

### Paperclip sidecar (`.env.example:166`)

| Var | Default | Purpose |
|---|---|---|
| `PAPERCLIP_ENABLED` | `false` | Opt-in bundled agent-management UI (`.env.example:169`). |
| `PAPERCLIP_MODE` | `docker` | `docker` \| `native` \| `external`. The macOS `.app` / Windows launcher set `native` (`.env.example:173`; see also `start-macos.sh:202`). |
| `PAPERCLIP_PUBLIC_URL` | `http://localhost:7000/paperclip` | URL Paperclip advertises (`.env.example:176`). |
| `PAPERCLIP_AUTH_SECRET` | `<REDACTED>` | Stable session secret; `openssl rand -hex 32` (`.env.example:179`). |
| `PAPERCLIP_MODEL_ENDPOINT` | `ollama` | `ollama` \| `apollo` \| `custom` (`.env.example:181`). |
| `PAPERCLIP_MODEL_BASE_URL` | `http://host.docker.internal:11434/v1` | (`.env.example:182`). |
| `PAPERCLIP_MODEL_API_KEY` | `local` | (`.env.example:183`). |
| `PAPERCLIP_MODEL_NAME` | _(unset)_ | (`.env.example:184`). |
| `PAPERCLIP_COLLECTOR_ENABLED` | `true` | Streams agent activity onto the Floor (`.env.example:188`). |
| `PAPERCLIP_COLLECTOR_TOKEN` | `<REDACTED>` | Agent API key for an authenticated Paperclip (`.env.example:189`). |
| `PAPERCLIP_COMPANY_ID` | _(unset)_ | (`.env.example:190`). |

### Docker-only vars not in `.env.example` but read by `docker-compose.yml`

Provider keys are passed straight through to the container (`docker-compose.yml:49-53`):
`HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `DATA_BRAVE_API_KEY`, `GOOGLE_API_KEY`,
`GOOGLE_PSE_CX`, `TAVILY_API_KEY`, `SERPER_API_KEY` (all `<REDACTED>`), plus
`PUID`/`PGID` (default `1000`, `docker-compose.yml:62`) and `APOLLO_ADMIN_USER`
(default `admin`).

---

## 2. `src/constants.py` — paths & in-code defaults

Source: `/Users/Antman/Apollo/src/constants.py`.

- `APP_VERSION = "1.0.0"` (`:5`).
- `BASE_DIR` = repo root (parent of `src/`), trailing slash (`:8`). `STATIC_DIR`,
  `DATA_DIR` derive from it (`:9-10`).
- Data file paths under `DATA_DIR` (`:13-20`): `sessions.json`, `memory.json`,
  `memory_doc.md`, `personal_docs/`, `personal_docs/runbook/`, `uploads/`,
  `features.json`, `settings.json`.
- API config: `MAX_CONTEXT_MESSAGES = 90`, `REQUEST_TIMEOUT = 20`,
  `OPENAI_COMPAT_PATH = "/v1/chat/completions"` (`:23-25`).
- Env-derived: `DEFAULT_HOST`, `LLM_HOSTS`, `OPENAI_API_KEY`, `SEARXNG_INSTANCE`
  (`:28-31`).
- Defaults: `DEFAULT_TEMPERATURE = 1.0`, `DEFAULT_MAX_TOKENS = 0` (`:39-40`).

---

## 3. `src/settings.py` — `DEFAULT_SETTINGS` & `DEFAULT_FEATURES`

Source: `/Users/Antman/Apollo/src/settings.py`.

### `DEFAULT_SETTINGS` (`:31`) — full key list

**Media / vision / TTS / STT**
`image_gen_enabled`(True), `image_model`(""), `image_quality`("medium"),
`vision_model`(""), `vision_enabled`(True), `vision_model_fallbacks`([]),
`app_public_url`(""), `tts_enabled`(True), `tts_provider`("disabled"),
`tts_model`("tts-1"), `tts_voice`("alloy"), `tts_speed`("1"),
`stt_enabled`(False), `stt_provider`("disabled"), `stt_model`("base"),
`stt_language`(""), `voicebox_url`("http://127.0.0.1:17493").

- `tts_provider` values (`services/tts/tts_service.py`): `disabled`, `browser`, `local`
  (Kokoro-82M, GPU), `piper` (piper-tts, ONNX CPU/Metal — voice = an on-disk `*.onnx` path),
  `voicebox`, `endpoint:<id>` (OpenAI-compatible `/audio/speech`).
- `stt_provider` values (`services/stt/stt_service.py`): `disabled`, `browser`, `local`
  (faster-whisper, CTranslate2 CPU/CUDA), `voicebox`, `endpoint:<id>`
  (`/audio/transcriptions`).
- `voicebox_url` (`:53`) is the base URL for the optional **Voicebox** local voice studio,
  shared by both the `voicebox` TTS and STT providers; the Voicebox desktop app must be running
  for those providers to report `available`.

**Search & web access** — the core of the no-Docker SearXNG architecture:
- `search_provider` = `"searxng"` (`:51`).
- `search_fallback_chain` = `["duckduckgo"]` (`:55`) — tried when the primary
  provider fails/rate-limits; free, no key, safe to ship on.
- `searxng_managed` = `True` (`:60`) — Apollo installs SearXNG into `data/searxng/`
  and supervises it; when not running, the provider chain skips straight to fallback
  with no timeout penalty.
- `searxng_port` = `8893` (`:61`).
- `web_access_mode` = `"manual"` (`:66`) — default when the client sends no
  `web_access`: `"manual"` (per-message toggles), `"auto"` (decider per message),
  `"always"` (pre-search every message).
- `search_url` = `""` (`:67`), `search_result_count` = `5` (`:68`).
- `search_safesearch` = `"strict"` (`:88`) — translated per-provider in
  `src/search/providers.py:_safesearch_for` (`:78`). Honored by SearXNG, Brave,
  DuckDuckGo, Google PSE, Serper; not Tavily or custom `search_url` backends.
- Provider keys (all `<REDACTED>`/blank by default): `brave_api_key`,
  `google_pse_key`, `google_pse_cx`, `tavily_api_key`, `serper_api_key` (`:89-93`).

**Research** (`:94-107`): `research_endpoint_id`, `research_model`,
`research_search_provider`, `research_max_tokens`(16384),
`research_extraction_timeout_seconds`(90), `research_extraction_concurrency`(3),
`research_run_timeout_seconds`(1800; bounded `[60, 86400]`, 0 = unlimited).

**Agent budgets** (`:108-118`): `agent_max_tool_calls`(0),
`agent_input_token_budget`(6000), `agent_input_token_hard_max`(200_000),
`agent_stream_timeout_seconds`(300).

**Tool sandbox / models** (`:123-140`): `tool_path_extra_roots`([]),
`task_endpoint_id`, `task_model`, `local_model_dirs`([]), `default_endpoint_id`,
`default_model`, `default_model_fallbacks`([] of `{endpoint_id, model}`),
`utility_endpoint_id`, `utility_model`, `utility_model_fallbacks`,
`teacher_model`, `teacher_enabled`(False).

**Skills** (`:147-150`): `skill_autosave_min_confidence`(0.85),
`skill_max_injected`(3).

**Adversarial reviewer endpoint** — not seeded in `DEFAULT_SETTINGS`; the keys
`reviewer_endpoint_id` / `reviewer_model` are optional overrides an admin sets in Settings
(registered as the "Adversarial Reviewer" endpoint role in
`routes/model_routes.py:42`). `POST /api/review` resolves the model via
`resolve_endpoint("reviewer", owner=…)`, which reads those keys and **falls back to the utility
model** when unset (`src/endpoint_resolver.py:245-253`) — so the reviewer works out of the box
with no reviewer-specific config once a utility model exists.

**Reminders & email triage** (`:152-165`): `reminder_channel`("browser"),
`reminder_llm_synthesis`(False), `reminder_ntfy_topic`("Reminders"),
`reminder_email_to`(""), `urgent_email_prompt`(long default).

**Keybinds** (`:167-175`): `search`(ctrl+k), `toggle_sidebar`(ctrl+b),
`new_session`(ctrl+alt+n), `star_session`(ctrl+alt+s),
`delete_session`(ctrl+alt+d), `admin_panel`(ctrl+shift+u), `cancel`(escape).

### `DEFAULT_FEATURES` (`:178`)

`web_search`(True), `web_fetch`(True), `deep_research`(False), `memory`(True),
`document_editor`(True), `rag`(True), `sensitive_filter`(True), `gallery`(True).

---

## 4. `data/settings.json` + `data/features.json` structure

Both are plain JSON objects holding only the keys an admin has overridden — they are
sparse. At read time each is **merged over** the defaults so callers always get a
complete dict:

```python
# src/settings.py:198-207
with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
    saved = json.load(f)
if not isinstance(saved, dict):
    raise ValueError("settings must be an object")
merged = {**DEFAULT_SETTINGS, **saved}
```

A missing file, invalid JSON, or non-dict payload falls back to a copy of the defaults
(`:204-205`). Writes are atomic via `core.atomic_io.atomic_write_json` (`:210-213`).
`features.json` follows the identical pattern (`load_features` `:275`, `save_features`
`:293`).

`is_setting_overridden(key)` (`:222`) re-reads the **raw** file to tell an explicit
admin choice apart from a default-equal value — used by adaptive budgets.

### Per-user override layer

`get_user_setting(key, owner)` (`:254`) resolves a small whitelist of keys
(`_PER_USER_KEYS`, `:242`: vision/image model + enable flags, default/utility/research
endpoint + model + fallbacks) from the caller's per-user prefs first
(`routes.prefs_routes._load_for_user`), falling back to the global setting. Any other
key is equivalent to `get_setting`.

---

## 5. The `load_settings` TTL cache

`src/settings.py:18-27` defines a 2-second TTL cache because `get_setting()` is called
on hot paths (every chat, every preprocess):

```python
_CACHE_TTL = 2.0
_settings_cache: tuple[float, dict] | None = None
_features_cache: tuple[float, dict] | None = None
```

`load_settings()` returns the cached merged dict if the entry is younger than
`_CACHE_TTL` (`:195-197`), otherwise re-parses and re-caches (`:206`). Both
`save_settings` and `save_features` call `_invalidate_caches()` (`:24`) so admin edits
through the UI take effect immediately; hand edits to the JSON files are picked up
within 2 seconds. `load_features` uses the same cache mechanism (`:278-280`).

### How the SearXNG settings flow downstream

`services/searxng/config.py:load_config()` (`:43`) reads `searxng_managed` and
`searxng_port` from `load_settings()` to build the immutable `SearxngConfig`
dataclass, which derives `venv_python`, `settings_path`, `url`, and `installed`
(`config.py:25-40`). The search provider chain reads the same settings to decide
whether to probe or skip SearXNG (`services/search/core.py:96-145`).

---

## 6. Environment pinned by the desktop launchers

The macOS/Windows desktop entry points set environment **before** the app imports, because a
stray value inherited from the GUI/login environment would otherwise break startup:

| Var | Set by | Value / reason |
|---|---|---|
| `DATABASE_URL` | `build-macos-app.sh:114`, `build-macos-bundle.sh:109`, Windows launcher | Hard-pinned to the app's own `sqlite:///…/data/app.db`. The desktop app is a self-contained SQLite install; a leftover `DATABASE_URL` (e.g. a dev machine's prisma/postgres var) would crash startup with a SQLAlchemy `NoSuchModuleError`. The frozen `apollo_boot.py` instead uses `setdefault` (`apollo_boot.py:146`), so an unset-or-correct value is required. |
| `PAPERCLIP_MODE` / `PAPERCLIP_ENABLED` | launcher scripts | `native` / `true` by default in the desktop app (Apollo supervises Paperclip + auto-provisions Node). Override with `PAPERCLIP_ENABLED=false`. |
| `APOLLO_PORT` | launchers | `7860` (macOS AirPlay holds 7000). |
| `HF_HOME`, `FASTEMBED_CACHE_PATH` | `apollo_boot.py:150-151` | Defaulted into the writable app home so model/embedding downloads stay inside `~/Library/Application Support/Apollo`. |
| `AUTH_ENABLED` | `.env` / process env (`app.py`) | Auth middleware is installed unless `AUTH_ENABLED == "false"` (`app.py:130`); default `true`. Loaded BOM-tolerantly via `load_dotenv(encoding="utf-8-sig")` (`app.py:37`) so `AUTH_ENABLED=false` written by Notepad isn't silently ignored. |

### Service Worker cache version (frontend)

`static/sw.js` pins `CACHE_NAME` (currently `apollo-v329`). It is **bumped whenever the
precache list or SW logic changes** — including when call-mode assets (`voiceCall.js`,
`vad.js`) or other precached modules change — so clients pick up new code instead of serving a
stale cached bundle. Old caches are deleted on `activate` (`sw.js:88`).
