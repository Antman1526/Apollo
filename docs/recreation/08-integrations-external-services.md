# Apollo — External Integrations & Services

Apollo is a local-first personal-AI server that brokers many third-party
services behind one interface. This document covers each integration: what it
is, how Apollo talks to it, the config keys involved, and the data formats on
the wire. Every claim has a `path:line` reference into
`/Users/Antman/Apollo`. **No secrets are reproduced** — only the *names* of the
settings / env vars that hold them.

| Category | Services |
|----------|----------|
| LLM endpoints | OpenAI, Anthropic, OpenRouter, Groq, Ollama, local llama.cpp (+ lmproxy) |
| Search providers | SearXNG, DuckDuckGo, Brave, Tavily, Serper, Google PSE |
| Crawling | Crawl4AI |
| Agent sidecar | Paperclip (Node) |
| MCP | built-in stdio servers, NPX servers, remote SSE servers |
| Comms | Email (IMAP/SMTP), CalDAV calendar, ntfy, generic HTTP integrations |

---

## 1. LLM Endpoints — `src/endpoint_resolver.py`, `src/llm_core.py`

### What it is

Apollo can dispatch any chat/research/utility call to a configured
**`ModelEndpoint`** row (stored in the app DB). Supported providers: OpenAI and
all OpenAI-compatible APIs, Anthropic, OpenRouter, Groq, native Ollama, and a
locally-served llama.cpp model. Provider type is **auto-detected from the
endpoint URL's hostname**, not configured explicitly.

### How Apollo talks to it

**Provider detection** — `_detect_provider(url)` (`src/llm_core.py:316`) matches
on hostname (exact or subdomain), defaulting unknown hosts to OpenAI-compatible:

```python
def _detect_provider(url: str) -> str:
    if _is_ollama_native_url(url): return "ollama"
    if _host_match(url, "anthropic.com"):  return "anthropic"
    if _host_match(url, "openrouter.ai"):  return "openrouter"
    if _host_match(url, "groq.com"):       return "groq"
    return "openai"
```

**URL + header shaping** — `src/endpoint_resolver.py` builds the right URL and
auth per provider:

- `build_chat_url(base)` (`src/endpoint_resolver.py:166`):
  Anthropic → `…/v1/messages`; Ollama → `…/api/chat`; everything else →
  `…/chat/completions`.
- `build_models_url(base)` (`:177`): Anthropic → `/v1/models`; Ollama →
  `/tags`; else → `/models`.
- `build_headers(api_key, base)` (`:188`): Anthropic uses
  `x-api-key` + `anthropic-version: 2023-06-01`; everyone else uses
  `Authorization: Bearer <key>`. OpenRouter additionally sets
  `HTTP-Referer` and `X-OpenRouter-Title` (`:199-201`).
- `normalize_base(url)` (`:133`) strips accidental API-path suffixes
  (`/models`, `/chat/completions`, `/api/chat`, `/v1/messages`, …) so the base
  is always bare.

**Payload formats** differ per provider in `src/llm_core.py`:

- OpenAI-compatible: standard `{model, messages, temperature, max_tokens}`.
  Some models require `max_completion_tokens` instead of `max_tokens`
  (`src/llm_core.py:412-416`).
- Anthropic: `_build_anthropic_payload` (`src/llm_core.py:491`) converts
  OpenAI-style messages/content/tools to the Anthropic Messages schema and
  defaults `max_tokens` to 4096 when unset (`:534`).
- Ollama native: a `/api/chat` payload builder (`src/llm_core.py:239-264`)
  maps `max_tokens` → `options.num_predict`.

**Endpoint resolution by role** — `resolve_endpoint(setting_prefix, …)`
(`src/endpoint_resolver.py:205`) resolves a role (`"default"`, `"research"`,
`"task"`, `"utility"`, `"vision"`) to `(chat_url, model, headers)`:

- Reads `{prefix}_endpoint_id` and `{prefix}_model` from settings (user-scoped
  via `get_user_setting`).
- **Utility unset → "same as Default Chat"** (`:244-247`); other roles unset →
  fall back to utility, then to default (`:251-256`).
- Discards a `model` the admin has since **hidden** on the endpoint and
  auto-picks the first **enabled, non-embedding** chat model via
  `_first_chat_model` / `_endpoint_enabled_models` (`:283-289`). The
  `_NON_CHAT_MODEL` filter (`:24-27`) prevents accidentally selecting
  `text-embedding-*`, `tts-*`, `whisper`, `dall-e`, `rerank`, etc.

**Tailscale resolution** — if an endpoint hostname won't resolve via DNS,
`resolve_url` (`:117`) falls back to `tailscale status --json` to find the
peer's IP (`_resolve_tailscale_host`, `:77`), enabling endpoints on a tailnet.

### Local llama.cpp + lmproxy

Local GGUF models are served by **llama-server** (llama.cpp). The
`ServerManager` launches `llama-server --model <path> --host … --port … -c <ctx>`
(`services/localmodels/server_manager.py:176-180`) and health-checks `/health`.
(See `07-business-logic-core-algorithms.md` §d for the capability guard.)

**lmproxy** (`routes/lmproxy_routes.py:1`) is a stable, localhost,
OpenAI-compatible reverse proxy in front of whichever local model is currently
warm. Paperclip's opencode-local agents are pointed at `…/lmproxy/v1` with a
bearer token (passed as `OPENAI_API_KEY`) so a same-host child process can use
the exact GGUF Apollo serves, without an Apollo login
(`routes/lmproxy_routes.py:4-9`). The route is auth-exempt and guarded by the
token instead.

The forwarder `_forward` (`routes/lmproxy_routes.py:84`):
- Validates the bearer (`_resolve_actor`, `:42` — accepts the shared proxy
  token or a per-agent token for attribution).
- Resolves the warm model URL via `warm_url_provider()`; returns **503** with a
  helpful message if no local model is running (`:90-95`).
- Rewrites to `{warm}/v1/{subpath}`, **drops the inbound Authorization** (the
  warm server needs no auth, `:100-102`), and streams the response back
  (`StreamingResponse` over `aiter_raw`, `:116-121`).
- Hop-by-hop headers are stripped via `filter_request_headers` /
  `filter_response_headers` (`services/paperclip/proxy.py:25-30`).

Routes: `GET /lmproxy/v1/models` and `… /lmproxy/v1/{path}`
(`routes/lmproxy_routes.py:123-129`).

### Config keys

- Per-endpoint: `ModelEndpoint` rows (`base_url`, `api_key`, `cached_models`,
  `hidden_models`).
- Role settings: `default_endpoint_id`/`default_model`,
  `utility_endpoint_id`/`utility_model`, `research_*`, `task_*`, `vision_*`,
  and `*_model_fallbacks` chains (`src/endpoint_resolver.py:339-381`).
- `OLLAMA_BASE_URL` (env) for a host Ollama; `APOLLO_LLAMA_CONTEXT` caps the
  local serving context (default 16384,
  `services/localmodels/server_manager.py:156`).

---

## 2. Search Providers — `services/search/providers.py`

All providers normalise to a list of `{title, url, snippet}` dicts (some add
`age`). Dispatch and the fallback chain live in `services/search/core.py`
(documented in `07-…§e`). Per-provider detail:

### SearXNG (self-hosted / managed sidecar)

- **What:** a metasearch engine. Apollo prefers its own **managed native
  sidecar** (no Docker) but can point at any instance.
- **How:** `searxng_search_api` (`services/search/providers.py:171`) GETs
  `{instance}/search` with `format=json`, `language=en`, a SafeSearch level,
  and pinned engines. Default general engines are `bing,mojeek,presearch`
  (`:168`) because the usual general set is rate-limited on self-hosted
  instances. News/fresh queries route to the `news` category with a
  `time_range` (`:196-202`). It cascades fallbacks: news→general, drop
  `language`, drop `engines`, finally an HTML-scrape fallback
  (`searxng_search`, `:284`) parsing `article.result` nodes
  (`:299-310`). Connect timeout is a tight 2 s (`:226`).
- **Instance precedence** — `_get_search_instance` (`:61`): explicit
  `search_url` setting > non-default `SEARXNG_INSTANCE` env > managed sidecar
  (when installed) > built-in default. `_explicit_env_instance` (`:46`) treats
  the legacy `http://localhost:8080` boilerplate as *not* an override
  (`:43-58`).
- **Config keys:** `search_url`, `searxng_managed`, `searxng_port`,
  `SEARXNG_INSTANCE` (env), `SEARXNG_GENERAL_ENGINES` (env), `SEARXNG_SECRET`.

### DuckDuckGo (free, no key — the default fallback)

- **What:** the no-key fallback used when SearXNG is down (`_FALLBACK_ORDER`).
- **How:** `duckduckgo_search` (`services/search/providers.py:418`) prefers the
  `duckduckgo_search` (`DDGS`) library, with an **HTML fallback** that GETs
  `https://html.duckduckgo.com/html/` and parses `.result` nodes
  (`:421-449`). DDG `/l/?uddg=` redirect URLs are unwrapped to their real
  destination (`_resolve_ddg_redirect`, `:398`), and host validation only
  trusts `duckduckgo.com` (`_is_duckduckgo_host`, `:392`).
- **Config keys:** none (keyless).

### Brave Search

- **How:** `brave_search` → `_brave_search_impl`
  (`services/search/providers.py:319, :325`) GETs
  `https://api.search.brave.com/res/v1/web/search` with header
  `X-Subscription-Token: <key>`, params `q/count/safesearch` and a `freshness`
  mapping for time filters (`:338-347`). 429 → `RateLimitError`. Parses
  `data.web.results[]` into `{title, url, snippet, age}` (`:374-384`).
- **Config keys:** `brave_api_key` (setting) or `DATA_BRAVE_API_KEY` (env).

### Tavily

- **How:** `tavily_search` (`services/search/providers.py:554`) POSTs
  `https://api.tavily.com/search` with `Authorization: Bearer <key>`, body
  `{query, max_results, include_answer:false}` and a `days` mapping for time
  filters (`:561-569`). Parses `data.results[]` →
  `{title, url, snippet(content), age(published_date)}` (`:589-599`).
- **Config keys:** `tavily_api_key` or `TAVILY_API_KEY` (env).

### Serper.dev (Google SERP API)

- **How:** `serper_search` (`services/search/providers.py:607`) POSTs
  `https://google.serper.dev/search` with header `X-API-KEY: <key>`, body
  `{q, num}` plus a `tbs: qdr:d|w|m|y` time filter (`:621-624`). Parses
  `data.organic[]` (`:644-654`).
- **Config keys:** `serper_api_key` or `SERPER_API_KEY` (env).

### Google PSE (Programmable Search Engine)

- **How:** `google_pse_search` (`services/search/providers.py:489`) GETs
  `https://www.googleapis.com/customsearch/v1` with params `key`, `cx`, `q`,
  `num` (max 10), `safe`, and a `dateRestrict: d1/w1/m1/y1` time filter
  (`:505-518`). Parses `data.items[]` (`:537-546`). Requires **two** keys.
- **Config keys:** `google_pse_key` + `google_pse_cx` (settings), or
  `GOOGLE_API_KEY` + `GOOGLE_PSE_CX` (env).

### Shared key + SafeSearch resolution

`_get_provider_key(provider)` (`services/search/providers.py:86`) resolves a
provider's key from its setting → legacy shared `search_api_key` → env var.
`_safesearch_for(provider)` (`:142`) translates the canonical
`strict|moderate|off` level into each provider's native value space.

---

## 3. Crawl4AI — `services/research/crawl4ai_adapter.py`

- **What:** a headless-browser crawler that returns **clean Markdown**, used for
  research / source extraction into RAG and reports
  (`services/research/crawl4ai_adapter.py:48`).
- **How:** `crawl_url` (`:75`) constructs `AsyncWebCrawler` with
  `BrowserConfig(headless=True)` and a `CrawlerRunConfig`
  (`word_count_threshold`, `remove_overlay_elements`, `process_iframes`,
  `cache_mode=ENABLED`) (`:88-98`), runs `crawler.arun(url, config)` under an
  `asyncio.wait_for(timeout_seconds)` (default 90 s, `:108`), and returns a
  `Crawl4AIExtract` dataclass with `markdown/title/links/media/status_code/error`
  (`:111-120`). `_markdown_text` prefers `fit_markdown` → `raw_markdown` →
  `markdown` (`:63-72`).
- **SSRF guard:** `validate_public_crawl_url` (`:54`) runs every URL through
  `check_outbound_url` (`src/url_safety.py`); private/internal targets are
  blocked unless `APOLLO_CRAWL4AI_ALLOW_PRIVATE=true`. Blocked URLs raise
  `Crawl4AIBlockedURL`.
- **Availability:** `is_available()` (`:42`) checks the `crawl4ai` package; when
  absent, `status()` (`:45`) yields an install hint
  (`pip install … && python -m playwright install chromium`) and `crawl_url`
  raises `Crawl4AIUnavailable`.
- **Config keys:** `APOLLO_CRAWL4AI_ALLOW_PRIVATE` (env); the package itself is
  optional (`requirements.txt`).

---

## 4. Paperclip Node Sidecar — `services/paperclip/`

- **What:** Paperclip is a bundled **agent-management UI** (a Node app,
  `paperclipai`) embedded in Apollo at `/paperclip` behind Apollo auth. It
  provisions its own embedded Postgres, so Apollo manages only the single Node
  process (`services/paperclip/runtime.py:1-9`).
- **Modes:** `PaperclipConfig.mode` ∈ `docker | native | external | off`
  (`services/paperclip/config.py:24-32`):
  - **native** — Apollo supervises `paperclipai run` itself
    (`services/paperclip/runtime.py`). Node is discovered via `find_node`
    (`:51`) or **auto-provisioned** by `node_bootstrap` which downloads a
    pinned official Node build from nodejs.org into Apollo's data dir on first
    launch, verified by SHA (`services/paperclip/node_bootstrap.py:1-9`).
  - **docker** — a Compose sidecar (Apollo doesn't own the process).
  - **external** — point at an already-running instance via `PAPERCLIP_URL`.
- **Reverse proxy:** Apollo proxies browser traffic to Paperclip, stripping
  hop-by-hop and `host` headers on the way in and
  `content-length`/`content-encoding` on the way out
  (`services/paperclip/proxy.py:7-30`).
- **Model wiring:** Paperclip's opencode-local agents are pointed at a model
  endpoint resolved by `_resolve_model` (`services/paperclip/config.py:38`):
  `ollama` (host Ollama via `host.docker.internal` in Docker, `localhost`
  natively, `:14-19`), `apollo` (Apollo's `/lmproxy/v1`), or `custom`.
- **Live-events collector:** `collector.py` connects to Paperclip's realtime
  WebSocket `/api/companies/{companyId}/events/ws`, normalises each LiveEvent,
  and republishes onto Apollo's "Floor" EventHub
  (`services/paperclip/collector.py:1-8`). Auth depends on Paperclip's
  deployment mode: `local_trusted` (the default) accepts tokenless connections;
  `authenticated` requires an agent API key as a Bearer token
  (`services/paperclip/collector.py:9-19`).
- **Config keys** (`.env.example`): `PAPERCLIP_ENABLED`, `PAPERCLIP_MODE`,
  `PAPERCLIP_PUBLIC_URL`, `PAPERCLIP_AUTH_SECRET`, `PAPERCLIP_MODEL_ENDPOINT`,
  `PAPERCLIP_MODEL_BASE_URL`, `PAPERCLIP_MODEL_API_KEY`, `PAPERCLIP_MODEL_NAME`,
  `PAPERCLIP_COLLECTOR_ENABLED`, `PAPERCLIP_COLLECTOR_TOKEN`,
  `PAPERCLIP_COMPANY_ID`. Default bundled version `2026.529.0`
  (`services/paperclip/runtime.py:24`).

---

## 5. MCP Servers — `mcp_servers/`, `src/builtin_mcp.py`, `src/mcp_manager.py`

- **What:** Apollo speaks the **Model Context Protocol** to expose extra tools.
  Three flavours: built-in Python stdio servers, NPX-launched stdio servers,
  and remote SSE servers.
- **Built-in stdio servers** (`src/builtin_mcp.py:69-74`): `image_gen`,
  `memory`, `rag`, `email` — each a Python script under `mcp_servers/` launched
  as `python <script>` with `PYTHONPATH` set
  (`src/builtin_mcp.py:98-123`). (Trivial `bash`/`python`/`filesystem`/
  `web_search` wrappers were folded into in-process native execution,
  `:62-64`.)
- **NPX server** (`src/builtin_mcp.py:77-83`): `builtin_browser` runs
  `npx -y @playwright/mcp@latest --headless --caps vision`. It is **only**
  started if the package is already in the npx cache — `_is_npx_package_cached`
  probes `npx --no-install <pkg> --version` (`:196-222`) so a fresh install
  doesn't hang trying to download Playwright. `npx` is located across common
  paths / Windows shims (`_find_npx`, `:18-58`).
- **Transports** (`src/mcp_manager.py:72-99`): `connect_server` dispatches to
  `_connect_stdio` (`:101`) or `_connect_sse` (`:192`). Each connection owns its
  transport lifetime in a single asyncio task to keep MCP's task-affine cancel
  scopes intact (`:60-71, :137-177`). stdio child env is sanitised by
  `_stdio_env` to silence npm/npx chatter that would corrupt MCP JSON-RPC on
  stdout (`:35`).
- **Tool calls:** `call_tool(qualified_name, args)` (`:342`) routes a
  `server.tool`-qualified name to the right session; `get_all_openai_schemas`
  (`:443`) flattens every server's tools into OpenAI function schemas for the
  model. Built-in servers can be auto-reconnected (`_reconnect_builtin`, `:412`).
- **Config keys:** `APOLLO_DISABLE_MCP` (env kill-switch,
  `src/builtin_mcp.py:86`); remote/custom MCP servers are stored as DB rows
  consumed by `connect_all_enabled` (`:320`).

---

## 6. Email — IMAP / SMTP (`mcp_servers/email_server.py`)

- **What:** the built-in `email` MCP server exposes tools to list
  unread/unresponded mail, read content, and draft replies. It connects to a
  local IMAP server (Dovecot) and an SMTP server
  (`mcp_servers/email_server.py:1-7`).
- **Multi-account:** accounts live in `data/app.db :: email_accounts`; callers
  pass `account=` (matched by name/user/id) and `_resolve_account` (`:90`)
  resolves it, with fuzzy matching and a fallback to env/settings flat keys for
  legacy single-account setups (`:42-48, :135`).
- **IMAP connect** (`mcp_servers/email_server.py:230-245`): `IMAP4_SSL` when the
  config is SSL (port 993), else plain `IMAP4` with optional `starttls()`, then
  `conn.login(imap_user, imap_password)`. SSL is inferred as `port == 993 and
  not starttls` (`:192`).
- **SMTP send** (`:755-809`): `SMTP` + `starttls()`, or `SMTP_SSL` (port 465),
  selected by the account's `smtp_security` (defaulting to `starttls` for port
  587, `ssl` otherwise, `:195`); then `login` and `send_message(msg,
  from_addr=…, to_addrs=…)`. Headers are CR/LF-sanitised before assignment
  (`_clean_header_value`, `:50`).
- **Secrets:** passwords are stored encrypted; the server decrypts via
  `src.secret_storage.decrypt` when reading DB rows (`:185`). A socket timeout
  is bounded by `EMAIL_SOCKET_TIMEOUT` (default 20 s, `:31`).
- **Config keys (env fallbacks):** `IMAP_HOST` (default `localhost`),
  `IMAP_PORT` (default `31143`), `IMAP_STARTTLS`, `SMTP_HOST`, `SMTP_PORT`
  (default `465`), `SMTP_STARTTLS`, `EMAIL_SOCKET_TIMEOUT` — plus the
  `email_accounts` DB table for the real config (`:142-153`).

---

## 7. CalDAV Calendar — `src/caldav_sync.py`

- **What:** a one-way **pull** (remote → local SQLite) of CalDAV calendars,
  re-wiring the gap left when calendar storage migrated to SQLite
  (`src/caldav_sync.py:1-7`). Works across Radicale / Nextcloud / Apple /
  Fastmail via the pure-Python `caldav` library.
- **How:** the synchronous lib runs in a threadpool
  (`asyncio.to_thread`) so the FastAPI loop stays free (`:11-13`).
  `_sync_blocking` (`:108`) builds
  `caldav.DAVClient(url, username, password)` (`:119`), discovers calendars via
  `principal().calendars()` (`:126-127`), and pulls events in a window of
  **90 days back / 365 forward** (`_LOOKBACK_DAYS`/`_LOOKAHEAD_DAYS`, `:38-40`)
  using `remote_cal.date_search(start, end, expand=False)` (`:190`).
- **Idempotency & deletion propagation:** each remote calendar maps to one
  local `CalendarCal` row keyed by a stable hash of the remote URL; events
  upsert by VEVENT `UID`; locally-stored CalDAV events not seen in the latest
  pull are deleted so remote deletions propagate (`:14-19`). Datetimes are
  normalised to UTC with `is_utc=True` (`:20-22`).
- **SSRF guard:** internal hosts (`localhost`, `metadata.google.internal`, …)
  are blocked (`_BLOCKED_HOSTS`, `:41`); credentials must go in the
  username/password fields, **not** embedded in the URL (`:74-75`).
- **Config keys:** CalDAV `url` / `username` / `password` saved via Settings
  (consumed at `:300-301`).

---

## 8. ntfy & Generic HTTP Integrations — `src/integrations.py`

### ntfy

- **What:** a push-notification service. Registered as a preset integration with
  `auth_type: none` (`src/integrations.py:92-101`).
- **How:** Apollo POSTs to `/{topic}` with the message text as the body and
  `Title` / `Priority` / `Tags` headers, or POSTs JSON `{topic, message,
  title, priority}` to `/`; polling is `GET /{topic}/json?poll=1`
  (`src/integrations.py:96-100`). The call is executed through the generic
  `execute_api_call` machinery below.
- **Config keys:** `NTFY_BIND` (default `127.0.0.1`), `NTFY_BASE_URL`
  (default `http://localhost:8091`) — loopback-only by default, exposable on a
  Tailscale IP (`.env.example`).

### Generic integration HTTP client — `execute_api_call`

`execute_api_call` (`src/integrations.py:298`) is the shared HTTP layer for all
registered integrations (ntfy, Vaultwarden, FreshRSS, Miniflux, Gitea,
Linkding, Home Assistant, …; preset catalog at `src/integrations.py:88-145`):

- Looks up the integration, checks it's enabled, and requires a `base_url`
  (`:308-317`).
- Strips preset-specific API suffixes (e.g. Miniflux `/v1`, Gitea `/api/v1`)
  so the base is bare (`:322-332`); rejects paths that aren't `/`-prefixed or
  contain a scheme (SSRF-ish guard, `:334-338`).
- **Auth modes** (`:348-376`): `header` (named header, with preset defaults like
  Miniflux `X-Auth-Token`), `bearer` (`Authorization: Bearer …`), `query` (key
  in a query param), `basic` (`user:password` → `httpx.BasicAuth`).
- Issues the request with `httpx.AsyncClient(timeout=30)` (`:379-387`), formats
  the response (pretty-prints JSON, strips HTML), truncates at 12 000 chars, and
  returns `{output|error, exit_code}` (`:389-421`).
- Integration secrets are encrypted at rest
  (`_encrypt_integration_secrets`/`_decrypt_integration_secrets`,
  `:150-172`) and masked in API responses (`mask_integration_secret`, `:181`).
- `get_integrations_prompt` (`:428`) surfaces enabled integrations + endpoint
  docs into the model's system prompt so it knows how to call them.

---

## Cross-cutting notes

- **Secrets** are never stored in plaintext docs and are encrypted at rest
  (`src/secret_storage.py`, integration encryption helpers, email password
  decryption). This document references only the *key names*.
- **SSRF defences** recur across outbound integrations: Crawl4AI
  (`check_outbound_url`), CalDAV (`_BLOCKED_HOSTS`), and generic integrations
  (path/scheme validation).
- **Graceful degradation** is a theme: missing Node, absent crawl4ai package,
  uncached npx package, and a down SearXNG sidecar each degrade with a clear
  message instead of crashing the app.
