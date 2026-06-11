# 08 — Integration Points & External Services

Apollo integrates a Node sidecar (Paperclip), multiple LLM providers, MCP tool servers, email, calendar, web search, browser agents, crawlers, push notifications, and outgoing webhooks. This document maps each integration to its source files and documents the data exchanged.

---

## 1. Paperclip Sidecar (in depth)

Paperclip is a multi-agent control plane that Apollo embeds as a sidecar. The integration spans config resolution, native process supervision, a reverse proxy, a live-events collector, an in-process event hub with SSE fan-out, and per-agent LLM attribution.

### 1.1 Config resolution (`services/paperclip/config.py`)

`load_config()` produces a frozen `PaperclipConfig(enabled, mode, url, browser_url, port, model_endpoint, model_base_url, model_name)` from env only:

- `PAPERCLIP_ENABLED` (bool), `PAPERCLIP_MODE` ∈ `docker | native | external | off` (default `docker`), `PAPERCLIP_PORT` (default `3100`).
- `url` — the server-side base Apollo can reach: defaults to `http://paperclip:{port}` under Docker (Compose service name) vs `http://localhost:{port}` otherwise; override with `PAPERCLIP_URL`.
- `browser_url` — the origin the browser iframes **directly** (`PAPERCLIP_BROWSER_URL`, default `http://localhost:{port}`); Paperclip's Vite build and API are rooted at `/`, so it cannot live under an Apollo subpath.
- `PAPERCLIP_MODEL_ENDPOINT` ∈ `ollama | apollo | custom` selects the model base for Paperclip's agents via `_resolve_model` — Ollama defaults differ by mode (`http://host.docker.internal:11434/v1` in Docker, `http://localhost:11434/v1` natively); `apollo` points at Apollo's `/v1` proxy.

Two secrets are minted on demand by `_read_or_make_secret` (env → file → generate `secrets.token_hex(32)`, persisted `chmod 0o600`): `resolve_auth_secret()` (`PAPERCLIP_AUTH_SECRET` / `~/.apollo/paperclip_secret`, Paperclip's `BETTER_AUTH_SECRET`) and `resolve_proxy_token()` (`PAPERCLIP_PROXY_TOKEN` / `~/.apollo/paperclip_proxy_token`, the bearer guarding Apollo's local-model proxy).

### 1.2 Native runtime supervision (`services/paperclip/runtime.py`)

`PaperclipRuntime.start()` is a no-op unless `enabled && mode == "native"`. It first **reuses** an already-healthy instance (`GET {url}/api/health` < 500) instead of spawning a duplicate or colliding on the port. Otherwise it locates Node (`PAPERCLIP_NODE_BIN` → `shutil.which("node")` → `/opt/homebrew/bin/node`, `/usr/local/bin/node`, `~/.local/bin/node`) and runs:

```python
# services/paperclip/runtime.py
DEFAULT_VERSION = "2026.529.0"

def build_command(node, npx, cli, version=DEFAULT_VERSION):
    if cli:                                  # PAPERCLIP_CLI points at a local checkout
        return [node, cli, "run"]
    return [npx, "-y", f"paperclipai@{version}", "run"]

def build_env(cfg, proxy_token, proxy_base, base_env=None):
    env = dict(base_env if base_env is not None else os.environ)
    env["PORT"] = str(cfg.port)
    env["HOST"] = "127.0.0.1"
    # opencode-local → Apollo local-model proxy (OpenAI-compatible).
    env["OPENAI_BASE_URL"] = proxy_base
    env["OPENAI_API_KEY"] = proxy_token
    env["OPENCODE_ALLOW_ALL_MODELS"] = "true"
    return env
```

Missing Node "degrades gracefully — it never raises into Apollo startup". `wait_healthy(timeout=60)` polls health; `stop()` terminates with a kill+reap fallback; `status()` reports `{mode, managed, running, reused, url}`.

**Node auto-provision** (`services/paperclip/node_bootstrap.py`): for the packaged desktop app with no Node prerequisite, `ensure_node(install_dir)` downloads an official build into `{install_dir}/.node/node-v{ver}-{os}-{arch}` — version from `PAPERCLIP_NODE_VERSION`, else the highest LTS from `https://nodejs.org/dist/index.json` (`pick_lts`), else `DEFAULT_NODE_VERSION = "22.13.0"`. Downloads are verified against nodejs.org's `SHASUMS256.txt` with `hmac.compare_digest` before extraction, and tarballs are extracted with `tarfile.extractall(dest, filter="data")` to neutralize path-traversal members; failure returns `None` and the caller falls back to a PATH Node.

### 1.3 Reverse proxy + websocket proxy (`routes/paperclip_routes.py`, `proxy.py`)

`/paperclip/{path:path}` forwards all HTTP methods to `cfg.url` as a streaming proxy (`client.send(..., stream=True)` + `StreamingResponse(upstream.aiter_raw(), background=BackgroundTask(upstream.aclose))`). Header hygiene lives in `services/paperclip/proxy.py`: requests drop RFC 7230 hop-by-hop headers plus `host`; responses additionally drop `content-length`/`content-encoding` so the framing is recomputed for the streamed body. The HTTP side is gated by Apollo's global AuthMiddleware; **websockets bypass `BaseHTTPMiddleware`**, so the WS route authenticates itself — it validates the Apollo session cookie via `ws_validate` (wired to `auth_manager.validate_token` in `app.py`), then bridges client↔upstream with two `asyncio.gather`-ed pump coroutines.

### 1.4 Live-events collector (`services/paperclip/collector.py`)

The collector bridges Paperclip's realtime WebSocket onto the Floor without any external process having to POST events.

- URL: `ws_events_url(base, company_id)` → `ws(s)://…/api/companies/{companyId}/events/ws`.
- **Company discovery**: `discover_companies()` GETs `{cfg.url}/api/companies` and collects ids; `PAPERCLIP_COMPANY_ID` pins one instead.
- **Auth**: Paperclip's default `local_trusted` deployment accepts tokenless REST/WS (implicit instance-admin "board" actor); `authenticated` mode requires an agent API key as Bearer — set `PAPERCLIP_COLLECTOR_TOKEN` (verified against Paperclip's `server/src/realtime/live-events-ws.ts` and `middleware/auth.ts`).
- **Normalization**: Paperclip emits `LiveEvent` objects shaped `{id, companyId, type, createdAt, payload}`. `normalize_live_event` keeps only types in `FLOOR_EVENT_TYPES` and reduces to the Floor shape:

```json
{ "type": "heartbeat.run.status",
  "payload": { "agentId": "agent-7", "status": "running", "task": "Fix proxy tests" },
  "received_at": 1765432100.512 }
```

- **Backoff**: the `run()` loop reconnects with exponential backoff `min_backoff=1.0 → max_backoff=60.0` (doubling), resetting to minimum after any session that survived >30 s; first failure logs a warning, repeats log at debug.

### 1.5 EventHub + HTTP ingest + SSE out (`services/paperclip/events.py`, `routes/paperclip_routes.py`)

```python
# services/paperclip/events.py
FLOOR_EVENT_TYPES = frozenset({
    "agent.status", "heartbeat.run.queued", "heartbeat.run.status",
    "heartbeat.run.log", "heartbeat.run.event", "activity.logged",
})

class EventHub:
    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: deque = deque(maxlen=history)
        self._seq = 0
```

`publish()` stamps a monotonic `seq`, appends to the 200-entry replay buffer, and `put_nowait`s to every subscriber queue (maxsize 500) — slow subscribers drop events rather than back-pressuring publishers.

**Ingest** — `POST /api/paperclip/events` accepts `{"events": [...]}` (or a single bare event), max batch `_MAX_INGEST_BATCH = 100`, validating `type ∈ FLOOR_EVENT_TYPES` and dict payloads. Auth is self-contained: if `PAPERCLIP_EVENTS_TOKEN` is set, the `X-Paperclip-Events-Token` header must match (`hmac.compare_digest`); otherwise only loopback clients are accepted, and any `X-Forwarded-For` (proxied request) is refused since loopback trust is void behind a reverse proxy. Example exchange (this is how the DeeperCode Ralph loop reports itself onto the Floor):

```json
POST /api/paperclip/events
X-Paperclip-Events-Token: <token, when configured>

{ "events": [
    { "type": "agent.status",
      "payload": { "agentId": "ralph-1", "name": "Ralph", "role": "coding",
                   "status": "running", "task": "Fix proxy tests" } },
    { "type": "heartbeat.run.log",
      "payload": { "agentId": "ralph-1", "chunk": "pytest tests/test_proxy.py -q" } }
] }

→ 200 { "accepted": 2, "rejected": 0 }
```

**Stream** — `GET /api/paperclip/stream` is the UI-facing SSE feed. When the sidecar is disabled and nothing was ever ingested it emits one terminal control event; when enabled but idle it parks the connection on a waiting event; otherwise it replays the buffer and goes live:

```text
data: {"type":"paperclip.stream.waiting","payload":{"reason":"no_events_yet"}}

data: {"type":"agent.status","payload":{"agentId":"ralph-1","status":"running",...},"received_at":1765432100.5}

: keepalive
```

The subscriber queue is registered **before** snapshotting the replay buffer; the per-event `seq` watermark (`if seq <= last_seq: continue`) deduplicates events that land in both. Keepalive comments flow every 25 s of silence. `paperclip.stream.unavailable` (`{"reason": "disabled"}`) tells the Floor to fall back to its demo preview.

**Status** — `GET /api/paperclip/status` (reachability is a server-side 2 s health ping, avoiding browser CORS):

```json
{ "enabled": true, "mode": "native",
  "url": "http://localhost:3100", "browser_url": "http://localhost:3100",
  "model_endpoint": "ollama", "reachable": true,
  "browser_use": { "available": false, "package": "browser-use", "...": "..." },
  "collector": { "running": true, "connected": true, "authenticated": false },
  "agent_workbench": { "components": { "paperclip": { "state": "ready", "...": "..." } } } }
```

The `agent_workbench` block (`services/integrations/agent_workbench.py`) is a no-side-effect readiness summary composing Paperclip, browser-use, the embedded browser, crawl4ai, and Ralph-loop state into per-component `{state, ready, …}` entries.

### 1.6 Per-agent LLM attribution (`agent_tokens.py` + `routes/lmproxy_routes.py`)

`/lmproxy/v1/*` is a stable, auth-exempt (token-guarded) OpenAI-compatible proxy in front of whatever GGUF llama-server Apollo currently has warm (`_warm_chat_base_url()` in `app.py` reads the chat slot from `services/localmodels/server_manager`). Paperclip's opencode agents call it with `OPENAI_API_KEY` set to either the shared proxy token or a **per-agent token** minted by admins via `POST /api/paperclip/agent-tokens` (`{"agent_id": "...", "name": "..."}` → `{"agent_id","name","token": "pa-…"}`). `AgentTokenRegistry` stores one token per agent (minting again rotates) in `~/.apollo/paperclip_agent_tokens.json` (0600), and listing only exposes a `token_suffix`.

When a request carries an agent token, the proxy **pulses the Floor**, debounced to one event per agent per 10 s so token streams don't flood the hub:

```python
# routes/lmproxy_routes.py
def _pulse(actor: dict) -> None:
    now = time.monotonic()
    if now - last_pulse.get(agent_id, float("-inf")) < pulse_interval:  # 10.0s
        return
    last_pulse[agent_id] = now
    publish_activity([{
        "type": "heartbeat.run.event",
        "payload": {"agentId": agent_id, "name": actor.get("name") or agent_id, "tool": "llm"},
        "received_at": time.time(),
    }])
```

The inbound bearer is stripped before forwarding (the warm llama-server needs no auth), and a 503 explains "no local model is currently running; serve one in Apollo (Settings → AI / model picker) first" when the warm slot is empty. Wire view of the whole loop:

```text
Paperclip agent (opencode-local, OPENAI_API_KEY=pa-…)
  → POST http://127.0.0.1:7000/lmproxy/v1/chat/completions   (auth-exempt route, token-checked)
      _resolve_actor: shared token → {"agent_id": ""}; per-agent token → registry lookup
      _pulse: heartbeat.run.event onto the EventHub (≤ 1 per agent / 10 s)
  → forwarded to http://127.0.0.1:<warm-port>/v1/chat/completions  (llama-server)
  → streamed back raw (filter_response_headers)
```

### 1.7 Browser-use verifier (`services/paperclip/browser_use_verifier.py`)

A self-test harness that drives the **browser-use** Python agent against Apollo's own UI: `verify_paperclip_floor(base_url)` builds a natural-language task ("Open the Paperclip sidebar tab… Verify the Floor view renders, that small Lego-like agents are visible…") and executes it in an isolated venv (`.apollo/browser-use-venv`, overridable via `APOLLO_BROWSER_USE_PYTHON`). The LLM defaults to `provider == "local"` through the lmproxy (`{scheme}://{host}/lmproxy/v1` with the proxy token via `ChatLiteLLM`); `APOLLO_BROWSER_USE_LLM_PROVIDER=browser-use` switches to the hosted `ChatBrowserUse` (model `bu-2-0`, `BROWSER_USE_API_KEY`). Results come back as `BrowserUseRun{ok, task, returncode, output, timed_out}` and can be written to JSON.

---

## 2. LLM Provider Endpoints

`src/llm_core.py` routes by hostname (`_detect_provider`, doc 07 §1): **Anthropic** (`/v1/messages`, `x-api-key` + `anthropic-version: 2023-06-01`, prompt-cache breakpoints), **Ollama** native (`/api/chat`) including Ollama Cloud, **OpenRouter** (default headers `HTTP-Referer: https://github.com/Antman1526/Apollo`, `X-OpenRouter-Title: Apollo`; no `stream_options`), **Groq** (OpenAI shape, no `stream_options`), and generic **OpenAI-compatible** for everything else (OpenAI, xAI, Mistral, DeepSeek, Together, Fireworks, local llama-server/vLLM). `_provider_label` maps hosts to friendly names for error text, and `_format_upstream_error` turns 401/403/404/429/5xx bodies into actionable sentences ("Anthropic rejected the API key — … Check Model Endpoints → Anthropic and re-paste the key.").

Representative request bodies emitted by the adapters (both built from the same canonical OpenAI-style message list):

```json
// Anthropic (_build_anthropic_payload): system hoisted into a cacheable block
{ "model": "claude-sonnet-4-5", "max_tokens": 4096, "temperature": 1.0,
  "system": [ { "type": "text", "text": "…long stable prefix…",
                "cache_control": { "type": "ephemeral" } } ],
  "messages": [ { "role": "user", "content": "…" } ],
  "stream": true,
  "tools": [ { "name": "web_search", "description": "…",
               "input_schema": { "type": "object", "properties": {} },
               "cache_control": { "type": "ephemeral" } } ] }
```

```json
// Native Ollama (_build_ollama_payload): options carry the real context window
{ "model": "qwen3-14b", "stream": true,
  "messages": [ { "role": "user", "content": "…" } ],
  "options": { "temperature": 1.0, "num_predict": 2048, "num_ctx": 131072 },
  "tools": [ { "type": "function", "function": { "name": "…", "parameters": {} } } ] }
```

---

## 3. MCP Servers (`src/mcp_manager.py` + `routes/mcp_routes.py`)

`McpManager` maintains per-server connection state, discovered tool schemas, and `ClientSession`s for two transports:

- **stdio** — spawns `command args…` with `_stdio_env` silencing npm/npx chatter (`NPM_CONFIG_LOGLEVEL=silent`, `NO_UPDATE_NOTIFIER=1`) that would corrupt the JSON-RPC stream. Registering a stdio server is treated as arbitrary code execution, so `POST /api/mcp/servers` is admin-gated.
- **SSE/HTTP** — `_connect_sse(server_id, name, url)` for remote servers.

Tools are namespaced per server and exported to the agent loop via `get_all_openai_schemas(disabled_map)`; `call_tool("server.tool", args)` routes back, with `_reconnect_builtin` recovering dropped built-in servers. `_format_mcp_connection_error` special-cases `@playwright/mcp` with the "cache the package once" fix.

`GET /api/mcp/servers` reports per-server status including `transport`, `has_oauth`, and `needs_oauth` (true while the OAuth token file is missing), so the UI can show a "Connect Google" button only when needed.

**Google OAuth flow** (`routes/mcp_routes.py`): a server may carry `oauth_config` (JSON, includes a `token_file` path). The flow:

1. `GET /api/mcp/oauth/authorize/{server_id}` builds the consent URL against `https://accounts.google.com/o/oauth2/v2/auth` with redirect URI `http://localhost:7000/api/mcp/oauth/callback`, and serves `_oauth_authorize_page` — a helper page whose form posts the pasted callback URL to `POST http://{host}/api/mcp/oauth/exchange/{server_id}` (covers the case where the callback lands on a different host than the browser).
2. `GET /api/mcp/oauth/callback?code=…&state=…` or the manual exchange both funnel into `_exchange_and_connect`, which POSTs the code to `https://oauth2.googleapis.com/token`, writes the token JSON to the configured `token_file`, and reconnects the server.

`POST /api/mcp/servers` (`add_server`) can also write the Google client-credentials JSON itself: the `oauth_file` form field carries `{"dir": "...", ...}` and the route synthesizes the standard installed-app structure (`redirect_uris: ["http://localhost"]`, `auth_uri`/`token_uri` pointing at Google) into that directory before first connect.

---

## 4. Email — IMAP/SMTP (`routes/email_routes.py`, `email_helpers.py`, `email_pollers.py`)

Multi-account IMAP with a per-account **connection pool** (`_IMAP_POOL: account_id → (conn, last_used_at)`) plus an in-memory message cache and prefetcher. `_open_imap_connection(host, port, starttls, timeout=_IMAP_TIMEOUT_SECONDS)` (timeout via `APOLLO_IMAP_TIMEOUT_SECONDS`, helper in `routes/email_helpers.py`) handles SSL vs STARTTLS; helpers cover UID-safe search/fetch (`_imap_uid_search`, `_imap_uid_fetch`), folder-role resolution (`_resolve_mail_folder`, `_folder_role_from_name`), and flag/move operations (`_store_email_flag`, `_move_email_message`).

Inbound mail feeds the automation layer: `_record_email_received_events(owner, account_id, folder, emails)` baselines the inbox on first sight, then fires the event-bus event `email_received` per new arrival (capped at 50 per poll) — any `ScheduledTask` with `trigger_event == "email_received"` counts toward its threshold (doc 07 §6).

Outbound mail resolves an SMTP-capable account via `_resolve_send_config` (falls back from a receive-only default to the first SMTP-capable account, raising `"No SMTP-capable email account configured"` otherwise), renders Markdown to email HTML (`_md_to_email_html`), and tags messages with Apollo identification headers (`_apply_apollo_headers`, carrying a kind + reference id so replies can be threaded back). Inbound HTML is sanitized through `_EmailHtmlSanitizer`, an allowlisting `html.parser.HTMLParser` subclass that strips scripts/styles and dangerous attributes before rendering in the SPA. iOS auto-linkification of digits inside rendered bodies is disabled at the page level (`<meta name="format-detection" content="telephone=no, …">` in `static/index.html`).

---

## 5. CalDAV Calendar (`routes/calendar_routes.py` + `src/caldav_sync.py`)

Events live locally in SQLite (`CalendarCal`, `CalendarEvent`) with RRULE expansion (`_expand_rrule`) and ICS escaping. Remote sync is **pull-based**: per-user CalDAV config `{url, username, password}` is stored in prefs with the password encrypted (`decrypt` on read; `GET /api/calendar/config` only reports `has_password`); `validate_caldav_url` (SSRF guard) checks the URL on save, and `sync_caldav(owner)` in `src/caldav_sync.py` pulls remote events into the local tables. Chat tools interpret natural-language times in the **user's** timezone via the `x-tz-offset` header captured per request (`set_user_tz_offset` / `parse_due_for_user`).

The API event shape (`_event_to_dict` — UTC-flagged timed events get a trailing `Z` so the frontend's `new Date()` renders them in local time; legacy naive rows stay naive to avoid shifting existing events):

```json
{ "uid": "evt-42@apollo", "summary": "Dentist",
  "dtstart": "2026-06-12T15:30:00Z", "dtend": "2026-06-12T16:00:00Z",
  "all_day": false, "is_utc": true,
  "description": "", "location": "", "rrule": "FREQ=MONTHLY",
  "calendar": "Personal", "calendar_href": "cal-1",
  "color": "#4facfe", "event_type": null, "importance": "normal" }
```

---

## 6. Web Search — SearXNG and friends (`services/search/providers.py`)

The provider registry includes `searxng` (self-hosted, no key). The instance comes from settings or `SEARXNG_INSTANCE` (default `http://localhost:8080`, `src/constants.py`). `searxng_search_api(query, count, categories, time_filter)` calls the JSON API:

```python
# services/search/providers.py
params = {
    "q": query,
    "format": "json",
    "language": "en",                       # pinned — stops geo-located foreign-SEO bleed
    "safesearch": _safesearch_for("searxng"),
}
is_news = time_filter is not None or any(h in q_lc for h in _NEWS_HINTS)
if is_news and categories == "general":
    params["categories"] = "news"
    if time_filter in ("day", "week", "month", "year"):
        # 'day' is too sparse on most SearXNG news engines — widen to a week
        params["time_range"] = "week" if time_filter in ("day", "week") else time_filter
```

General queries are constrained to `_GENERAL_ENGINES` (the default engine set returns zero on the reference instance). Results normalize to `[{"title": "...", "url": "...", "snippet": "..."}]` — the common shape shared by every provider in the module. `searxng_search` is the HTML-scraping fallback when JSON is unavailable. Sibling providers in the same file: `brave_search`, `duckduckgo_search` (redirect resolution + HTML fallback), `google_pse_search`, `tavily_search`, `serper_search`. A local SearXNG config ships under `config/searxng/`.

---

## 7. Crawl4AI (`services/research/crawl4ai_adapter.py`)

Optional research-grade extraction using Crawl4AI's documented `AsyncWebCrawler` + `BrowserConfig`/`CrawlerRunConfig` shape. `is_available()` probes the import; `status()` returns `{available, package, install_hint, purpose}`. Every URL passes `validate_public_crawl_url`, which enforces the SSRF policy via `check_outbound_url(block_private=…)` unless `APOLLO_CRAWL4AI_ALLOW_PRIVATE=true`, raising `Crawl4AIBlockedURL` otherwise. Results are returned as `Crawl4AIExtract{url, success, status_code, markdown, title, links, media, error}` — clean Markdown destined for RAG and reports.

---

## 8. ntfy Push Notifications (`routes/note_routes.py`, settings `reminder_ntfy_topic`)

Reminders fan out over a channel setting (`browser` / `email` / `ntfy`). The ntfy path looks up the enabled integration with `preset == "ntfy"` from `load_integrations()` and POSTs plain text to `{base_url}/{topic}`:

```python
# routes/note_routes.py
topic = settings.get("reminder_ntfy_topic") or "reminders"
hdrs = {"Title": title or "Reminder", "Priority": "high", "Tags": "bell"}
if api_key:
    hdrs["Authorization"] = f"Bearer {api_key}"
async with httpx.AsyncClient(timeout=10.0) as client:
    resp = await client.post(f"{base}/{topic}", content=ntfy_body, headers=hdrs)
```

The in-app browser notification **always** fires regardless of channel (the frontend polls `/api/tasks/notifications`).

---

## 9. Outgoing Webhooks (`src/webhook_manager.py` + `routes/webhook_routes.py`)

`WebhookManager.fire(event, payload)` delivers to every enabled `Webhook` row subscribed to the event. `ALLOWED_EVENTS = {"session.created", "chat.completed", "chat.message", "webhook.test"}`. URLs are validated against private/internal networks at registration **and again at delivery** (`validate_webhook_url` resolves the hostname and rejects RFC-1918/loopback/link-local/ULA ranges — SSRF defense even if the DB is tampered with). The wire format:

```json
POST <webhook url>
Content-Type: application/json
X-Apollo-Event: chat.completed
X-Apollo-Signature: <hex hmac-sha256 of body with the webhook secret>
User-Agent: Apollo-Webhook/1.0

{ "event": "chat.completed",
  "timestamp": "2026-06-10T21:14:03.512000",
  "data": { "session_id": "abc123", "message": "…", "response": "…" } }
```

Delivery results (`last_triggered_at`, `last_status_code`, sanitized `last_error`) are written back to the row; `fire_and_forget` bridges sync callers onto the loop via `run_coroutine_threadsafe`.

---

## 10. Wiring Summary (`app.py`)

`app.py` composes the Paperclip pieces: one shared `EventHub` instance is fed by HTTP ingest, the `PaperclipCollector` (constructed with `token=os.getenv("PAPERCLIP_COLLECTOR_TOKEN")`, `company_id=os.getenv("PAPERCLIP_COMPANY_ID")`, publishing via `hub.publish`), and the lmproxy pulses — and drained by `/api/paperclip/stream`. Auth exemptions are explicit: `/api/paperclip/events` proves identity itself, and `AUTH_EXEMPT_PREFIXES = ["/static", "/lmproxy"]` lets same-host child processes (Paperclip agents, browser-use) reach the model proxy with only the bearer token. The integration status panel (`routes/integration_routes.py`) and system status routes surface reachability for all of the above.
