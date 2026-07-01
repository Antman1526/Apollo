# Apollo Backend API Specifications

**Project:** Apollo (`/Users/Antman/Apollo`)
**Stack:** FastAPI (Starlette/ASGI) + SQLAlchemy + Playwright (embedded browser) + SSE streaming
**Scope of this document:** the HTTP/WebSocket API surface — router catalog, the
router-registration pattern, the Pydantic request models, the privilege/auth gates,
and the streaming/SSE conventions. All `path:line` references point at real source
as of the commit documented here. Secrets are redacted.

> Conventions used below: `METHOD /path` — handler — gate. "Gate" is the
> authorization check the handler performs (the global `AuthMiddleware` already
> enforces *authentication* for non-exempt paths; see §2).

---

## 1. Router catalog (`routes/`)

`ls routes/` yields ~50 router modules. Each exports a `setup_*_routes(...) -> APIRouter`
factory (a few use a different name; see §3). The full list:

| Module | Factory | Mount prefix / notable paths |
|---|---|---|
| `admin_wipe_routes.py` | `setup_admin_wipe_routes` | admin data-wipe |
| `api_token_routes.py` | `setup_api_token_routes` | `/api/tokens` (bearer-token CRUD) |
| `assistant_routes.py` | `setup_assistant_routes` | scheduled assistants |
| `auth_routes.py` | `setup_auth_routes` | `/api/auth/*` (login, settings, users, integrations) |
| `backup_routes.py` | `setup_backup_routes` | export/import user data |
| `browser_routes.py` | `setup_browser_routes` | `/api/browser/*` + WS `/api/browser/ws` |
| `calendar_routes.py` | `setup_calendar_routes` | CalDAV calendar |
| `chat_routes.py` | `setup_chat_routes` | `/api/chat`, `/api/chat_stream`, resume/stop |
| `cleanup_routes.py` | `setup_cleanup_routes` | session cleanup |
| `compare_routes.py` | `setup_compare_routes` | model A/B compare |
| `contacts_routes.py` | `setup_contacts_routes` | contacts |
| `cookbook_routes.py` | `setup_cookbook_routes` | model download/serve/cache |
| `diagnostics_routes.py` | `setup_diagnostics_routes` | RAG/research diagnostics |
| `document_routes.py` | `setup_document_routes` | artifacts / canvas docs |
| `editor_draft_routes.py` | `setup_editor_draft_routes` | server-backed editor drafts |
| `email_routes.py` | `setup_email_routes` | email (largest module, 155 KB) |
| `embedding_routes.py` | `setup_embedding_routes` | embeddings |
| `emoji_routes.py` | `setup_emoji_routes` | emoji |
| `font_routes.py` | `setup_font_routes` | fonts |
| `gallery_routes.py` | `setup_gallery_routes` | image library |
| `history_routes.py` | `setup_history_routes` | chat history |
| `hwfit_routes.py` | `setup_hwfit_routes` | hardware "What Fits?" |
| `integration_routes.py` | `setup_integration_routes` | integration status |
| `lmproxy_routes.py` | `setup_lmproxy_routes` | `/lmproxy` local-model OpenAI proxy |
| `localmodels_routes.py` | `setup_localmodels_routes` | `/api/local-models/*` |
| `mcp_routes.py` | `setup_mcp_routes` | MCP server management |
| `memory_routes.py` | `setup_memory_routes` | mem0 memory |
| `model_routes.py` | `setup_model_routes` | `/api/models`, `/api/model-endpoints/*` |
| `note_routes.py` | `setup_note_routes` | notes |
| `paperclip_routes.py` | `setup_paperclip_routes` | Paperclip sidecar reverse proxy + WS |
| `personal_routes.py` | `setup_personal_routes` | personal docs / RAG |
| `prefs_routes.py` | `setup_prefs_routes` | user preferences |
| `preset_routes.py` | `setup_preset_routes` | character presets |
| `research_routes.py` | `setup_research_routes` | `/api/research/*` (deep research) |
| `search_routes.py` | `setup_search_routes` | `/api/search/*` incl. SearXNG sidecar |
| `session_routes.py` | `setup_session_routes` | chat sessions CRUD |
| `shell_routes.py` | `setup_shell_routes` | user-facing shell exec |
| `signature_routes.py` | `setup_signature_routes` | signature image stamps |
| `skills_routes.py` | `setup_skills_routes` | skills |
| `stt_routes.py` | `setup_stt_routes` | speech-to-text |
| `system_status_routes.py` | `setup_system_status_routes` | system status dashboard |
| `task_routes.py` | `setup_task_routes` | scheduled tasks + per-task webhooks |
| `tts_routes.py` | `setup_tts_routes` | text-to-speech |
| `upload_routes.py` | `setup_upload_routes` | file uploads |
| `vault_routes.py` | `setup_vault_routes` | secret vault |
| `webhook_routes.py` | `setup_webhook_routes` | outbound webhooks |

Plus helper modules that are **not** routers (imported by the above):
`chat_helpers.py`, `cookbook_helpers.py`, `document_helpers.py`, `email_helpers.py`,
`email_pollers.py`, `gallery_helpers.py`. The `companion/` package
(`companion/routes.py:69` `setup_companion_routes`) adds the mobile-companion API.

> **Note — there is no `settings_routes.py`.** App settings are served by the auth
> router at `routes/auth_routes.py:403` (`GET /api/auth/settings`) and
> `routes/auth_routes.py:414` (`POST /api/auth/settings`). See §10.

---

## 2. Authentication & authorization model

### 2.1 `AuthMiddleware` (request authentication)

Defined and installed in `app.py` (`class AuthMiddleware` at `app.py:277`,
`app.add_middleware(AuthMiddleware)` at `app.py:389`). It runs on every request and:

1. **Exempts** a fixed set of paths so they work pre-login (`app.py:177`):
   `/api/auth/setup`, `/signup`, `/login`, `/logout`, `/status`, `/features`,
   **`/api/auth/settings`**, `/api/auth/integrations/presets`, `/api/health`,
   `/api/version`, `/login`, `/api/paperclip/events`. Prefix exemptions
   (`app.py:199`): `/static`, `/lmproxy`. Pattern exemption (`app.py:209`):
   `^/api/tasks/[^/]+/webhook/[^/]+/?$` (per-task webhook tokens self-authenticate).
2. Honors an **in-process internal-tool token** (`X-Apollo-Internal-Token`, constant
   defined `core/middleware.py:16-17`). When present, valid, *and* the request is a
   trusted **direct** loopback (`_is_trusted_loopback`, `app.py:261` — rejects any
   request carrying proxy/tunnel forwarding headers like `cf-connecting-ip`,
   `x-forwarded-for`), the agent tool layer can hit admin-gated routes. It may set
   `request.state.current_user` to an impersonated owner via `X-Apollo-Owner`
   (`app.py:294`) or fall back to the synthetic user `"internal-tool"`.
3. Supports `LOCALHOST_BYPASS` for direct-loopback service calls (`app.py:309`).
4. Otherwise validates the session cookie (`apollo_session`, see §10) or an API
   bearer token (cached in `app.state._token_cache`, `app.py:225`), stamping
   `request.state.current_user`.

`SecurityHeadersMiddleware` (`core/middleware.py:48`) adds CSP / `X-Frame-Options` /
`X-Content-Type-Options` to every response, with relaxed CSP for research report pages
and tool-render iframes.

### 2.2 Privilege gates (handler-level)

- **`require_admin(request)`** — `core/middleware.py:20`. Raises `403 Admin only`
  unless the caller is an admin, auth is disabled (`AUTH_ENABLED=false`), or the
  internal-tool token/`current_user == "internal-tool"` bypass applies.
- **`require_privilege(request, key)`** — `src/auth_helpers.py:91`. Returns the
  username; raises `403` if `auth.json` has `privileges[key] is False`. **Fail-open
  semantics:** admins get all privileges; a missing key defaults to permitted; empty
  user (single-user / auth-disabled mode) is unenforced (`auth_helpers.py:100-113`).
  Known privilege keys in use: `can_use_browser`, `can_use_research`.
- **`get_current_user(request)`** — `src/auth_helpers.py:8` — reads
  `request.state.current_user`.

WebSockets bypass `BaseHTTPMiddleware` entirely, so WS handlers re-authenticate the
cookie themselves (see §7 browser WS, and the paperclip proxy).

---

## 3. Router-registration pattern (`services/app_startup.py`)

Routers are built and mounted through small guarded helpers so a failure during
startup produces a **labeled** error instead of an opaque stack trace.

`build_and_include_router` (`services/app_startup.py:31`):

```python
def build_and_include_router(app, label, factory, *args, logger=None, **kwargs):
    """Build a router inside the labeled registration guard, then include it."""
    try:
        router = factory(*args, **kwargs)
    except Exception as exc:
        if logger:
            logger.exception("Failed to build %s routes", label)
        raise RuntimeError(f"Failed to build {label} routes") from exc
    return include_router_checked(app, router, label, logger=logger)
```

`include_router_checked` (`services/app_startup.py:18`) wraps
`app.include_router(router)` with the same labeled-error guard.

For batch registration, `RouterSpec` (a frozen dataclass, `app_startup.py:10`) carries
`(label, factory, args, kwargs)` and `register_router_specs` (`app_startup.py:49`)
loops over them, returning `{label: router}`. Example from `app.py:572`:

```python
register_router_specs(app, [
    RouterSpec("Sessions", setup_session_routes, args=(session_manager, session_config),
               kwargs={"webhook_manager": webhook_manager}),
    RouterSpec("Chat", setup_chat_routes, args=(
        session_manager, chat_handler, chat_processor,
        memory_manager, research_handler, upload_handler,
    ), kwargs={"memory_vector": memory_vector,
               "webhook_manager": webhook_manager,
               "skills_manager": skills_manager}),
    RouterSpec("Research", setup_research_routes, args=(research_handler,),
               kwargs={"session_manager": session_manager}),
    RouterSpec("Search", setup_search_routes, args=(config,)),
    RouterSpec("Models", setup_model_routes, args=(model_discovery,)),
    ...
], logger=logger)
```

So each `setup_*_routes` is a **closure factory**: it receives its service
dependencies as arguments, defines route handlers that close over them, and returns the
`APIRouter`. There is no module-global router — dependencies are injected at build time.

### 3.1 WebSocket auth injection (`ws_validate` / `ws_authorize`)

Because WS handlers can't use the HTTP middleware, the *validators* are injected into
the factory as callables. Two routers use this idiom:

**Browser** (`app.py:796`):

```python
build_and_include_router(
    app, "Browser", setup_browser_routes,
    ws_validate=lambda token: (not AUTH_ENABLED) or auth_manager.validate_token(token),
    ws_authorize=lambda token: (not AUTH_ENABLED) or _browser_ws_authorize(token),
    logger=logger,
)
```

`_browser_ws_authorize` (`app.py:780`) mirrors `require_privilege`'s fail-open rules:
anonymous/valid sessions pass, otherwise it checks
`privileges.get("can_use_browser", True)`. With `AUTH_ENABLED=false` both lambdas
short-circuit to `True` (the HTTP middleware isn't even installed in that mode).

**Paperclip sidecar proxy** (`app.py:729`) injects
`ws_validate=lambda token: auth_manager.validate_token(token)` and an event hub.

---

## 4. Pydantic request models (`src/request_models.py`)

Shared request/response models live in `src/request_models.py`; many routers also
define small local models inside their factory.

### `ChatRequest` (`src/request_models.py:7`) — the JSON body for `POST /api/chat`

```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=50000)
    session: str = Field(...)
    attachments: Optional[List[str]] = Field(default=[])
    use_web: Optional[bool] = Field(default=False, description="Enable web search")
    web_access: Optional[str] = Field(
        default=None, description="Web access mode: off | auto | always")
    use_research: Optional[bool] = Field(default=False)
    time_filter: Optional[str] = Field(default=None)
    preset_id: Optional[str] = Field(default=None)
```

- `message` is `.strip()`-validated (`request_models.py:18`).
- `time_filter` is coerced to `None` unless in `{day, week, month, year}`
  (`request_models.py:23`) — invalid values are silently dropped, not rejected.
- **`web_access`** is the tri-state field (`off | auto | always`, default `None`).
  `None` means "fall back to settings / legacy flags". This is the field wired into
  `resolve_web_access` — see §5.2 and §6. Note `ChatRequest` has **no `incognito`
  field**; incognito only flows through the form-based `/api/chat_stream` path
  (`chat_routes.py:290`).

Other shared models: `SessionCreateRequest` (`:31`), `MemoryAddRequest` (`:38`,
validates `category` against a fixed set), `MemoryUpdateRequest` (`:52`),
`PresetUpdateRequest` (`:57`), `DirectoryRequest` (`:97`). Response models:
`ErrorResponse` (`:108`), `UploadResponse` (`:114`), `SessionResponse` (`:124`),
`MemoryResponse` (`:132`).

---

## 5. Chat router (`routes/chat_routes.py`)

Factory `setup_chat_routes(...)` (`chat_routes.py:232`) takes `session_manager`,
`chat_handler`, `chat_processor`, `memory_manager`, `research_handler`,
`upload_handler`, plus optional `memory_vector`, `webhook_manager`, `skills_manager`.
Tag `["chat"]`.

### 5.1 `POST /api/chat` (non-streaming) — `chat_routes.py:248`

- **Body:** `ChatRequest` (JSON). **Response:** `{"response": str}`
  (`response_model=Dict[str, str]`).
- **Gates (in order):** `_verify_session_owner(request, session)` (`:260` — prevents
  posting into another user's chat) → session load (404 if missing) →
  `_clear_orphaned_session_endpoint` / `_recover_empty_session_model` (400 if the
  model endpoint was removed or no model is selected) → `_enforce_chat_privileges`
  (`:282` — same `allowed_models` allowlist + `max_messages_per_day` cap as the stream
  path; mirrored so the non-streaming path can't bypass it).
- Inline memory commands are short-circuited (`:285`).
- **Web wiring** (`:289`): calls `resolve_web_access(chat_request.web_access, "chat",
  message, use_web, None, prev_message=...)`. No `apply_incognito` here (no incognito
  field). Then `build_chat_context(...)` (`:317`) runs preset/preprocess/preface/
  compaction; optional research injection (`:329`); `llm_call_async` (`:342`);
  post-response background tasks (memory/webhook/auto-name) via
  `run_post_response_tasks` (`:359`).

### 5.2 `POST /api/chat_stream` (SSE) — `chat_routes.py:371`

Accepts a **`multipart/form-data`** body (it can also read a JSON body for
attachments). The form is parsed at `chat_routes.py:396`:

```python
form_data = await request.form()
message          = form_data.get("message")
session          = form_data.get("session")
attachments      = form_data.get("attachments")        # JSON-encoded list of IDs
use_web          = form_data.get("use_web")
use_research     = form_data.get("use_research")
time_filter      = form_data.get("time_filter")
preset_id        = form_data.get("preset_id")
allow_bash       = form_data.get("allow_bash")
allow_web_search = form_data.get("allow_web_search")
web_access       = form_data.get("web_access")          # off | auto | always
use_rag          = form_data.get("use_rag")
search_context   = form_data.get("search_context")      # pre-fetched results (compare mode)
compare_mode     = str(form_data.get("compare_mode", "")).lower() == "true"
incognito        = str(form_data.get("incognito", "")).lower() == "true"
chat_mode        = str(form_data.get("mode", "")).lower()   # 'chat' or 'agent'
```

Additional form fields read further down: `active_doc_id` (`:428`), `no_memory`
(`:499`). An `x-tz-offset` **header** (`:389`) is stashed so calendar/notes tools
interpret times in the user's timezone.

**Key behaviors:**

- **Auto-escalation** (`:424`): if `mode=chat` but `_message_needs_tools(message)`
  matches a todo/reminder/calendar intent, the turn is silently promoted to `agent`
  (a *light* promotion — shell/code/file tools are still withheld). `user_requested_agent`
  (`:415`) records whether the user explicitly chose agent mode.
- **Gates:** `coerce_message_and_session` (`:438`, allows empty message when an
  attachment is present) → `_verify_session_owner` (`:443`, after coerce resolves a
  default session, before load) → orphaned-endpoint / empty-model repair (`:446-459`)
  → `_enforce_chat_privileges` (`:473`, must fire **before any token spend**) →
  `resolve_session_auth` (`:476`).
- **Research auto-trigger** (`:479`): `use_research=true` or a session in
  `research_pending` mode triggers deep research.
- **Web wiring** (`:522`):

  ```python
  from src.web_decider import resolve_web_access, apply_incognito
  use_web, allow_web_search, _web_decision = await resolve_web_access(
      web_access, chat_mode, message if isinstance(message, str) else "",
      use_web, allow_web_search, prev_message=_prev_user_msg,
  )
  use_web, _web_decision = apply_incognito(incognito, use_web, _web_decision)
  ```

  `_prev_user_msg` (`:510`) is the previous user turn pulled from `sess.history`
  (extracted *before* `build_chat_context` appends the current message) and feeds the
  follow-up heuristic in the decider.
- **Incognito** (`:617`) additionally denies tools that would expose the user's data
  and suppresses memory writes/RAG.

#### SSE conventions (`/api/chat_stream`)

The body is an `async def stream_with_save()` generator (`:679`) yielded through
`_safe_stream` (`:1125`) and run as a **detached** background task via
`agent_runs.start(...)`; the response is
`StreamingResponse(agent_runs.subscribe(session), media_type="text/event-stream")`
(`chat_routes.py:1140`). Because the run is detached, closing the SSE only drops a
subscriber — the run continues and saves on completion. Reconnect via
`/api/chat/resume/{session_id}`.

Each event is `data: {json}\n\n`. The `type` field discriminates the event. Observed
event types (with source lines):

| `type` | Meaning | Line |
|---|---|---|
| `attachments` | attachment metadata | `:689` |
| `doc_update` | a doc was auto-created/opened | `:696` |
| `rag_sources` | RAG citations | `:700` |
| `web_sources` | web search citations | `:703` |
| `web_search_failed` | web requested but returned nothing (not fired for `auto-skip`) | `:712` |
| `memories_used` | memories injected into context | `:716` |
| `model_info` | model + suffix banner | `:734`, `:864` |
| `research_progress` / `research_sources` / `research_findings` / `research_done` | deep-research lifecycle | `:816`–`:832` |
| `compacted` | context was compacted | `:843` |
| `tool_start` | a tool began (e.g. `generate_image`) | `:875` |
| `metrics` | timing/token metrics | `:895`, `:951` |
| `message_saved` | assistant message persisted, carries `id` | `:990` |
| *(raw delta)* | `{"delta": "..."}` token chunks streamed from the model | `:886`, `:929` |

Stream terminators: `data: [DONE]\n\n` on success (`:833`, `:896`); heartbeats are SSE
comments `: heartbeat {n}\n\n` (`:820`, `:876`); errors are emitted as
`event: error\ndata: {json}\n\n` and passed through unchanged (`:954`, the rewrite path
uses the same convention at `:1358`).

### 5.3 Other chat endpoints

| Endpoint | Handler | Line | Notes |
|---|---|---|---|
| `GET /api/chat/resume/{session_id}` | `chat_resume` | `:1146` | reconnect to a still-running detached run; 404 if none active. Owner-verified. |
| `POST /api/chat/stop/{session_id}` | `chat_stop` | `:1157` | cancels a detached run (Stop button); `{"stopped": bool}`. |
| `GET /api/chat/stream_status/{session_id}` | `chat_stream_status` | `:1166` | reports active/needs-resume. |
| `POST /api/inject_context/{session_id}` | `inject_context` | `:1184` | `context: str = Form(...)`. |
| `GET /api/search` | `search_messages` | `:1199` | search within chat messages. |
| `POST /api/rewrite` | `rewrite_message` | `:1252` | SSE stream, same `event: error` convention. |

---

## 6. Web-access resolution (`src/web_decider.py`) — `resolve_web_access` / `apply_incognito`

`resolve_web_access` (`web_decider.py:268`) maps the tri-state `web_access` onto the
legacy `(use_web, allow_web_search, decision)` triple consumed by the chat pipeline:

```python
async def resolve_web_access(web_access, chat_mode, message, use_web,
                             allow_web_search, prev_message="") -> Tuple:
    mode = (web_access or "").strip().lower()
    if mode not in ("off", "auto", "always"):
        cfg = (load_settings().get("web_access_mode") or "manual").strip().lower()
        _legacy_intent = (str(use_web).lower() == "true"
                          or str(allow_web_search).lower() == "true")
        if cfg not in ("off", "auto", "always") or _legacy_intent:
            return use_web, allow_web_search, None      # manual / legacy — untouched
        mode = cfg
    if mode == "off":
        return False, "false", "off"
    if mode == "always":
        if chat_mode == "agent":
            return use_web, "true", "always"
        return True, allow_web_search, "always"
    # auto
    if chat_mode == "agent":
        return use_web, "true", "auto-tools"            # model decides per-call
    needed = await decide_use_web(message or "", prev_message=prev_message)
    return needed, allow_web_search, ("auto-search" if needed else "auto-skip")
```

Decision values: `None` (legacy manual, flags untouched), `off`, `always`,
`auto-tools`, `auto-search`, `auto-skip`. The chat route logs the decision
(`chat_routes.py:530`) and uses it to drive the `web_sources` / `web_search_failed`
SSE events (§5.2).

`apply_incognito` (`web_decider.py:257`):

```python
def apply_incognito(incognito, use_web, decision):
    if incognito and use_web:
        return False, "incognito-off"   # incognito must never hit a search engine
    return use_web, decision
```

In `auto` chat mode, `decide_use_web` (`web_decider.py:209`) runs a heuristic
(`heuristic_decision`, `:100` — URL/recency/question-shape signals) and falls back to a
utility-model tie-break (`_ask_utility_model`, `:154`); conservative default is "no".

---

## 7. Browser router (`routes/browser_routes.py`)

Factory `setup_browser_routes(ws_validate=None, ws_authorize=None)`
(`browser_routes.py:157`), mounted with `prefix="/api/browser"`, tag `["browser"]`.
Backed by `services/browser/embedded_browser` (a single shared Playwright page). Every
HTTP handler calls `_require_browser_privilege(request)` (`:141`, →
`require_privilege(request, "can_use_browser")`). Errors map through
`_handle_browser_error` (`:145`): `BrowserSecurityError`/`ValueError`→400,
`BrowserUnavailable`→503, timeout→504, else 500.

| Endpoint | Body model | Line |
|---|---|---|
| `GET /api/browser/status` | — | `:171` |
| `POST /api/browser/navigate` | `NavigateRequest{url}` (`:115`) | `:176` |
| `GET /api/browser/current` | — | `:184` |
| `GET /api/browser/html` | — | `:192` |
| `GET /api/browser/text` | — | `:200` |
| `POST /api/browser/execute` | `ScriptRequest{script}` (`:119`) | `:208` |
| `POST /api/browser/screenshot` | `ScreenshotRequest{full_page}` (`:133`) | `:216` |
| `POST /api/browser/wait` | `SelectorRequest{selector,timeout_ms}` (`:123`) | `:224` |
| `POST /api/browser/click` | `SelectorRequest` | `:232` |
| `POST /api/browser/type` | `TypeRequest{selector,text}` (`:128`) | `:240` |
| `GET /api/browser/events` | — | `:248` |
| `POST /api/browser/detect-localhost` | `DetectRequest{text}` (`:137`) | `:253` |
| `GET /api/browser/tools` | — | `:259` (self-describing tool/route map) |

### 7.1 WebSocket `/api/browser/ws` — live screencast + input forwarding

Handler `browser_ws` (`browser_routes.py:287`). Protocol:

1. **Auth handshake** (`:294`): read the `apollo_session` cookie, run
   `ws_validate(token)`; close with **1008 (policy violation)** if invalid. Then
   `ws_authorize(token)` (the `can_use_browser` privilege); close 1008 if not.
   `await websocket.accept()` (`:304`).
2. **Reachability probe** (`:314`): `get_current_url()`; on `BrowserUnavailable` send
   `{"type":"error","message":...}` and close.
3. **Single-viewer takeover** (`:327`): a module-global `_current_viewer` ensures only
   one live connection streams the shared page (CDP allows one screencast per page);
   a new connection stops the previous one (last-one-wins).
4. **Server→client frames** via `_LiveViewer` (`:61`) + `_FrameForwarder` (`:24`),
   which enforces **strict backpressure** — at most one send in flight, dropping
   frames if the previous send is still pending (favours latency over completeness).

**Server→client message types** (JSON text frames):
- `{"type":"frame","data":<base64 jpeg>,"w":int,"h":int}` (`:75`) — a screencast frame.
- `{"type":"url","url":...,"title":...}` (`:91`, `:407`) — address-bar sync.
- `{"type":"error","message":...}` — protocol / replay errors.

**Client→server messages** dispatched by `_dispatch_ws_message` (`browser_routes.py:386`):

| `type` | Fields | Action |
|---|---|---|
| `mouse` | `kind, x, y, button, clicks, dx, dy` | `session.input_mouse(...)` |
| `key` | `kind, key` | `session.input_key(...)` |
| `navigate` | `url` | `session.navigate(...)` → replies `{"type":"url",...}` |
| `back` / `forward` / `reload` | — | history nav → replies `{"type":"url",...}` |

Input/nav replay failures are caught and reported as `error` events but **never** tear
down the stream (`:356-364`). On disconnect/exception the viewer is stopped and
`_current_viewer` cleared (`:369-372`).

---

## 8. Models routers

### 8.1 `routes/model_routes.py`

Factory `setup_model_routes(model_discovery)` (`model_routes.py:656`),
`prefix="/api"`. Maintains a **per-user** 30-second model-list cache keyed by
`(owner, is_admin)` (`:665`, `:874`) and a background re-probe thread
(`_refresh_caches_bg`, `:678`) that probes endpoints in parallel with a 2s timeout and
backs off endpoints that failed 3+ times in 5 min.

Key endpoints:

| Endpoint | Handler | Line | Gate / notes |
|---|---|---|---|
| `GET /api/models?refresh=` | `api_models` | `:841` | auth required (rejects anonymous when configured, `:856`); admins see all endpoints, users see owner-scoped (`:862`); per-user cache. |
| `GET /api/model-endpoints/probe-local` | `probe_local_endpoints` | `:891` | `require_admin`; 8s cache; only probes local endpoints. |
| `GET /api/ping` / `GET /api/probe` | | `:943`, `:1068` | reachability checks. |
| `POST /api/probe-selected` | | `:1017` | |
| `GET /api/providers` / `GET /api/discover` | | `:1141`, `:1153` | provider discovery. |
| `GET /api/model-endpoints` | `list_model_endpoints` | `:1161` | `require_admin`; returns `{id,name,base_url,has_key,is_enabled,models,status,model_type,supports_tools,...}`. `api_key` is never returned (only `has_key`). |
| `POST /api/model-endpoints` | `create_model_endpoint` | `:1209` | `require_admin`; **`Form(...)`** body: `name, base_url(req), api_key, skip_probe, require_models, model_type, supports_tools, container_local, shared`. Normalizes/resolves the URL (Tailscale fallback), dedupes existing rows. |
| `POST /api/model-endpoints/test` | | `:1332` | `require_admin`. |
| `GET /api/model-endpoints/{ep_id}/probe` | | `:1357` | |
| `GET\|PATCH /api/model-endpoints/{ep_id}/models` | | `:1420`, `:1461` | list / hide models. |
| `GET /api/default-chat` | | `:1484` | resolves the default chat endpoint/model. |
| `PATCH /api/model-endpoints/{ep_id}` | | `:1589` | edit endpoint. |
| `GET /api/model-endpoints/{ep_id}/dependents` | | `:1721` | sessions referencing this endpoint. |
| `DELETE /api/model-endpoints/{ep_id}` | | `:1727` | `require_admin`. |
| `GET /api/tools` | `list_tools` | `:1756` | lists agent tool tags with enabled flags. |
| `POST /api/tools` | `update_tools` | `:1770` | `require_admin`; body `ToolsUpdate{disabled: list}` (`:1767`); persists `disabled_tools` to settings. |

### 8.2 `routes/localmodels_routes.py` (on-disk GGUF models)

Factory `setup_localmodels_routes()` (`localmodels_routes.py:26`),
`prefix="/api/local-models"`, tag `["local-models"]`. **Every** route calls
`require_admin(request)` — the module docstring (`:1`) notes these are strictly more
privileged than the model-endpoint routes because they enumerate the filesystem,
change a global scan directory, and launch/kill OS processes.

| Endpoint | Handler | Line | Response |
|---|---|---|---|
| `GET /api/local-models` | `list_models` | `:29` | `{dirs, models:[{...,running:bool}]}` (merges scanner catalog with running-server status). |
| `POST /api/local-models/scan` | `rescan` | `:44` | `{count, models}`. |
| `GET /api/local-models/voices` | `list_voices` | `:50` | Piper TTS voices. |
| `GET /api/local-models/dirs` | `get_dirs` | `:56` | `{dirs}`. |
| `PUT /api/local-models/dirs` | `put_dirs` | `:61` | body `DirsBody{dirs:list[str]}` (`:22`); sets scan dirs + rescans. |
| `POST /api/local-models/{model_id}/start` | `start` | `:68` | `{ok, base_url}` or 400 `{ok:false,error}`. |
| `POST /api/local-models/{model_id}/stop` | `stop` | `:77` | `{ok}`/`{ok:false,error:"not running"}`. |

A separate **local-model OpenAI proxy** (`routes/lmproxy_routes.py`, mounted at
`/lmproxy`, auth-exempt) forwards to whichever GGUF model is warm — consumed by
Paperclip's agents (`app.py:824`).

---

## 9. Search router (`routes/search_routes.py`)

Factory `setup_search_routes(config)` (`search_routes.py:44`), tag `["search"]`.
`_request_values` (`:22`) accepts JSON **or** form **or** query params so both the
FormData-posting UI and the JSON-posting agent tool work (the UI's FormData would
otherwise 422 against `Form(...)`).

| Endpoint | Handler | Line | Gate / notes |
|---|---|---|---|
| `GET /api/search/config` | `get_search_settings` | `:47` | returns `get_search_config()`. |
| `POST /api/search` | `do_web_search` | `:51` | standalone search; `{query\|q, time_filter\|freshness}` → `{context, sources}`. Used by Compare mode to pre-search once. |
| `GET /api/search/providers` | `list_search_providers` | `:73` | `[{id,label,available}]` per `PROVIDER_INFO`. |
| `POST /api/search/query` | `search_with_provider` | `:92` | `{query, provider, count}` → `{results, provider, time}`; count capped at 20. |
| `GET /api/search/searxng/status` | `searxng_status` | `:119` | **`require_admin`**; returns `{status, url, installing, install_ok, log_tail, runtime_log_tail}` — reads the last 20 lines of the sidecar runtime log. |
| `POST /api/search/searxng/install` | `searxng_install` | `:141` | **`require_admin`**; runs `scripts/setup-searxng.{sh,ps1}` in a daemon thread (stops the sidecar, runs the installer, restarts on success); returns `{started:bool}` (or `{started:false,reason:"already running"}`). |

The SearXNG sidecar is a no-Docker, locally-installed managed search backend with a
DDG fallback (see the project's web-access architecture). Install progress is held in a
module-local `_install_state` dict (`:117`) surfaced via the status endpoint.

---

## 10. Auth & settings router (`routes/auth_routes.py`)

Factory `setup_auth_routes(auth_manager)` (`auth_routes.py:76`), `prefix="/api/auth"`,
tag `["auth"]`. Session cookie name is **`apollo_session`** (`auth_routes.py:73`), also
imported by the browser/paperclip WS handlers. Per-IP rate limiters guard login (15/60s),
signup (3/300s), setup (3/300s) (`:79-81`).

### 10.1 Account / session endpoints

| Endpoint | Body | Line | Notes |
|---|---|---|---|
| `POST /api/auth/setup` | `SetupRequest{username,password}` (`:42`) | `:87` | first-run admin; 400 if already configured; password ≥ 8. |
| `POST /api/auth/signup` | `SignupRequest` (`:47`) | `:101` | only if `signup_enabled`; 403 otherwise. |
| `POST /api/auth/login` | `LoginRequest{username,password,remember,totp_code}` (`:35`) | `:119` | verifies password, then TOTP if enabled; sets httponly `apollo_session` cookie (`secure` via `SECURE_COOKIES`, `samesite=lax`, 7-day max-age when `remember`). Returns `{ok:false,requires_totp:true}` when a code is needed. |
| `POST /api/auth/logout` | — | `:151` | revokes token, deletes cookie. |
| `GET /api/auth/status` | — | `:159` | public (auth-exempt); returns config/login status + the caller's effective `privileges`. |
| `POST /api/auth/change-password` | `ChangePasswordRequest` (`:52`) | `:176` | revokes other sessions on success. |
| `POST /api/auth/2fa/setup` | — | `:194` | returns TOTP secret + QR data-URI. |
| `POST /api/auth/2fa/confirm` | `TotpVerifyRequest{code}` (`:214`) | `:217` | returns backup codes. |
| `POST /api/auth/2fa/disable` | `TotpDisableRequest{password}` (`:228`) | `:231` | |
| `GET /api/auth/2fa/status` | — | `:241` | |

### 10.2 User management (admin)

`GET/POST /api/auth/users` (`:250`, `:257`, `CreateUserRequest` `:57`),
`PUT /api/auth/users/{username}/privileges` (`:269`),
`PUT /api/auth/users/{username}/rename` (`:280`),
`DELETE /api/auth/users` (`:370`),
`POST /api/auth/signup-toggle` (deprecated, `:345`) /
`PUT /api/auth/open-signup` (`:361`).

### 10.3 Feature flags & **app settings** (these are the "settings routes")

| Endpoint | Line | Gate |
|---|---|---|
| `GET /api/auth/features` | `:382` | public — which UI features are on. |
| `POST /api/auth/features` | `:387` | admin only; only boolean keys that already exist are updated. |
| `GET /api/auth/settings` | `:403` | **auth-exempt** (frontend needs keybinds/TTS prefs pre-admin). Admins get the full settings; non-admins get `scrub_settings(settings)` with secret keys blanked. |
| `POST /api/auth/settings` | `:414` | **admin only** (`403` otherwise). Only keys present in `DEFAULT_SETTINGS` are written (`:422`), so unknown keys can't be injected. |

```python
@router.post("/settings")
async def set_settings(request: Request):
    user = _get_current_user(request)
    if not user or not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")
    body = await request.json()
    current = _load_settings()
    for key in DEFAULT_SETTINGS:
        if key in body:
            current[key] = body[key]
    _save_settings(current)
    return current
```

This is where `web_access_mode`, `searxng_managed`, `searxng_port`, `disabled_tools`,
etc. are persisted. Settings themselves are loaded/saved by `src/settings.py`
(`load_settings` `:192`, `save_settings` `:210`, `get_setting` `:217`,
`get_user_setting` `:254`).

### 10.4 Integrations CRUD (admin)

`GET /api/auth/integrations` (`:433`, keys masked),
`GET /api/auth/integrations/presets` (`:444`, public/auth-exempt, api_key stripped),
`POST /api/auth/integrations` (`:449`),
`PUT /api/auth/integrations/{id}` (`:459`),
`DELETE /api/auth/integrations/{id}` (`:471`),
`POST /api/auth/integrations/{id}/test` (`:482`). All mutators are admin-gated and
return secrets only through `mask_integration_secret(...)`.

---

## 11. Research router (`routes/research_routes.py`)

Factory `setup_research_routes(research_handler, session_manager=None)`
(`research_routes.py:53`), tag `["research"]`. Deep-research jobs are launched and
streamed.

| Endpoint | Body / model | Line | Gate |
|---|---|---|---|
| `GET /api/research/crawl4ai/status` | — | `:93` | crawl4ai availability. |
| `POST /api/research/crawl4ai/crawl` | `Crawl4AIRequest` (`:87`) | `:98` | |
| `GET /api/research/active` | — | `:162` | |
| `GET /api/research/status/{session_id}` | — | `:181` | |
| `POST /api/research/cancel/{session_id}` | — | `:192` | |
| `POST /api/research/result/{session_id}` | — | `:201` | |
| `GET /api/research/report/{session_id}` | — | `:228` | served as standalone HTML (relaxed CSP, §2.1). |
| `POST /api/research/{session_id}/hide-image` | `HideImageRequest` (`:245`) | `:248` | |
| `POST /api/research/{session_id}/unhide-images` | — | `:260` | |
| `GET /api/research/library` | — | `:271` | saved research list. |
| `GET /api/research/detail/{session_id}` | — | `:325` | |
| `POST /api/research/{session_id}/archive` | — | `:343` | |
| `DELETE /api/research/{session_id}` | — | `:363` | |
| `POST /api/research/start` | `ResearchStartRequest` (`:389`) | `:401` | **`require_privilege(request, "can_use_research")`** (`:405`). For internal-tool callers, re-checks the impersonated `X-Apollo-Owner`'s `can_use_research` (`:407`). Resolves an endpoint via a fallback chain (`research → utility → default → chat → first enabled`, `:449-485`). Returns `{session_id, status:"running", query}`. |
| `GET /api/research/stream/{session_id}` | — | `:507` | SSE progress; owner-verified (404 if not owned). |
| `POST /api/research/result-peek/{session_id}` | — | `:541` | |
| `POST /api/research/spinoff/{session_id}` | — | `:564` | |

`ResearchStartRequest` (`research_routes.py:389`) fields: `query`, `max_rounds`
(0=Auto, capped 20), `search_provider`, `endpoint_id`, `model`, `max_time` (60–1800s),
`extraction_timeout` (15–3600s), `extraction_concurrency` (1–12), `category`.

---

## 12. SSE / streaming conventions (summary)

All Apollo streaming endpoints share these conventions:

- **Transport:** `StreamingResponse(generator, media_type="text/event-stream")`.
- **Frame format:** `data: {json}\n\n`, with a `"type"` discriminator on structured
  events; raw token output uses `{"delta": "..."}`.
- **Terminator:** `data: [DONE]\n\n`.
- **Errors:** `event: error\ndata: {json}\n\n` (the error event line is preserved and
  passed straight through any wrapping generator, e.g. `chat_routes.py:954`).
- **Heartbeats:** SSE comment lines `: heartbeat {n}\n\n` keep proxies from idling out
  long research/agent runs (`chat_routes.py:820`).
- **Detached runs (chat only):** the chat stream is run by `agent_runs` independent of
  the SSE subscriber, so a dropped connection doesn't cancel generation; reconnect via
  `GET /api/chat/resume/{session_id}` and cancel via `POST /api/chat/stop/{session_id}`.
- **Timeout exemptions:** streaming/long-running endpoints are exempt from the request
  timeout middleware (`app.py:111`).

---

## 13. Reviewer, memory-graph, distill/import & skill-pack endpoints

These are the newer endpoints layered on top of the chat/memory subsystems. The
*pure* logic they call is documented in `07-business-logic-core-algorithms.md`;
this section covers the HTTP contract.

### 13.1 `POST /api/review` — adversarial second-model critique (`chat_routes.py:1365`)

Defined inside `setup_chat_routes`, so it shares the chat router's tag/gates.
JSON body `{question, answer}`; `answer` is required (400 if blank). It resolves
the **`reviewer`** endpoint role via `resolve_endpoint("reviewer", owner=owner)`
(`chat_routes.py:1379`) — a *new role* that reads `reviewer_endpoint_id` /
`reviewer_model` from settings and, being neither `utility` nor `default`, falls
back **utility → default** (`src/endpoint_resolver.py:251-256`). 400 if nothing
resolves. It then builds the critique prompt and parses the reply with the pure
`services/review/reviewer.py` helpers:

```python
@router.post("/api/review")
async def review_answer(request: Request):
    from services.review.reviewer import build_review_prompt, parse_review
    ...
    url, model, headers = resolve_endpoint("reviewer", owner=owner)
    if not url or not model:
        raise HTTPException(400, "No reviewer/utility model configured — set one in Settings")
    text = await llm_call_async(url, model, build_review_prompt(question, answer),
                                temperature=0.2, headers=headers, timeout=60)
    return {**parse_review(text), "model": model}
```

**Response:** `{verdict, issues, suggestion, raw, model}` — `verdict` is one of
`accurate|incomplete|incorrect|needs context`, `issues` a list of bullet
strings, `suggestion` a one-line fix (`""` when the model said "none"), `raw` the
unparsed model text, `model` the resolved model id. See §07(f) for the prompt.

### 13.2 Memory graph / distill / import (`routes/memory_routes.py`)

Factory `setup_memory_routes(memory_manager, session_manager, memory_vector=None)`
(`memory_routes.py:37`), `prefix="/api/memory"`, tag `["memory"]`. Every handler
resolves the caller via `get_current_user` and scopes to that owner.

| Endpoint | Handler | Line | Body / notes |
|---|---|---|---|
| `GET /api/memory/graph` | `memory_graph` | `:118` | Owner-scoped knowledge graph. Loads the owner's memories, defines `neighbor_fn` = `memory_vector.search(text, k=6)` (or `[]` when the vector store is unhealthy → session-only edges), then returns `build_graph(mems, neighbor_fn, threshold=0.6, max_neighbors=4, max_nodes=300)` → `{nodes, edges}` (see §07(e)). |
| `POST /api/memory/distill-session` | `distill_session_route` | `:487` | `session_id: str = Form(...)`; `require_privilege(request, "can_manage_memory")`. Calls `brain.distill_session(...)` (loads the session, reads its own `endpoint_url`/`model` for the LLM call, extracts atomic facts, dedups, stores with `source="agent"` + `session_id`). Returns `{ok:true, added, skipped}`; 502 on failure. |
| `POST /api/memory/import-chat-export` | `import_chat_export_route` | `:509` | `file: UploadFile = File(...)`; `require_privilege(..., "can_manage_memory")`. Reads the uploaded JSON, `parse_export(obj)` auto-detects ChatGPT vs Claude and returns conversations; resolves a **utility** endpoint (imports have no session), then `brain.import_conversations(...)` distills+stores each with `source="import"`. Returns `{ok:true, added, skipped, conversations}`; empty/unrecognized → `{ok:true, added:0, ..., message:"No recognizable conversations in export"}`; 400 on non-JSON; 502 on failure. |

`neighbor_fn` (`memory_routes.py:128`):

```python
def neighbor_fn(mem):
    if not (memory_vector and getattr(memory_vector, "healthy", False)):
        return []
    try:
        return memory_vector.search(mem.get("text") or "", k=6)
    except Exception:
        return []
```

(The full memory router also has `add`, `search`, `timeline`, `by-session`,
`extract`, `audit`, `import` (document→facts), `pin`, and the wildcard
`GET/PUT/DELETE /{memory_id}` CRUD — the wildcards are registered **last** so
they don't swallow `/graph`, `/import`, `/distill-session`, etc.
(`memory_routes.py:566`).)

### 13.3 Skill-pack install (`routes/skill_pack_routes.py`)

Factory `setup_skill_pack_routes(skills_manager)`, `prefix="/api/skills/packs"`,
tag `["skills"]`. Both routes are **`require_admin`**. Pure discover/classify/
install logic lives in `services/skills/pack_installer.py` (see §07(g)).

| Endpoint | Body model | Line | Behavior |
|---|---|---|---|
| `POST /api/skills/packs/preview` | `PreviewRequest{source, ref=""}` | `:40` | `pi.fetch_pack(source, ref)` (SSRF-guarded GitHub tarball → temp dir) → `pi.discover_skills(root)`. Returns `{ok:true, root, skills:[{name, description, tier, rel_dir, error}]}` — **nothing is written**. `tier` is `prose` or `script`. |
| `POST /api/skills/packs/install` | `InstallRequest{source, ref="", category="imported", names=[], overwrite=false}` | `:49` | Re-fetches + re-discovers, filters to `names` if given, builds `InstallOpts` (owner = current user, `now_iso` = tz-aware UTC), `pi.install_skills(found, opts, skills_root, src_root=root)`. Returns `{ok:true, installed, skipped, errored}`. |

Provenance frontmatter (`imported_from`, `imported_ref`, `imported_at`,
`imported_tier`) is written into each installed `SKILL.md`; `script`-tier skills
are installed with `status: draft` (quarantined, never auto-run) while `prose`
skills install `published`.

---

## 14. Voicebox TTS/STT provider (`/api/tts/*`, `/api/stt/*`)

Apollo's speech services are multi-provider dispatchers that read
`data/settings.json` on **every** call. Providers: `disabled`, `browser` (client
Web Speech), `local` (Kokoro/Whisper), `piper` (TTS only), `endpoint:<id>`
(OpenAI-compatible), and **`voicebox`** — a local "voice studio" sidecar.

### 14.1 Routes

- **TTS** (`routes/tts_routes.py`, `prefix="/api/tts"`): `GET /stats`,
  `POST /synthesize` (`TTSRequest` body → audio bytes / base64),
  `POST /clear-cache`.
- **STT** (`routes/stt_routes.py`, `prefix="/api/stt"`): `GET /stats`,
  `POST /transcribe` — `file: UploadFile = File(...)` → `{text}`. This is the
  endpoint `voiceCall.js` posts recorded WebM blobs to (`stt_routes.py:23`).

### 14.2 Voicebox provider behavior

Config keys: `tts_provider`/`stt_provider = "voicebox"` and `voicebox_url`
(default `http://127.0.0.1:17493`). Every Voicebox request carries the header
`X-Voicebox-Client-Id: apollo`. The base is normalized with a trailing-slash
strip (`tts_service.py:148`, `stt_service.py:162`).

**Availability probe** — `_voicebox_reachable` GETs `/profiles` with a 2s timeout
and treats HTTP 200 as up (`tts_service.py:151`, `stt_service.py:165`). This is
what `is_available()` returns for the `voicebox` provider.

**TTS synthesis** — `_synthesize_voicebox` (`tts_service.py:186`) POSTs
`/generate` with `{text, profile_id, language:"en"}` and a 120s timeout,
returning the raw audio bytes. When `voice` (the profile id) is blank it fetches
`/profiles` and uses the first profile's id (`_voicebox_profile_id` tolerates a
bare string or a dict with `id`/`profile_id`/`name`/`slug`):

```python
def _synthesize_voicebox(self, text, voice, url=None):
    base = self._voicebox_base(url)
    profile_id = voice
    if not profile_id:
        profiles = self._voicebox_profiles(url)
        if profiles:
            profile_id = self._voicebox_profile_id(profiles[0])
    payload = {"text": text, "profile_id": profile_id, "language": "en"}
    r = httpx.post(base + "/generate", json=payload,
                   headers=self._VOICEBOX_HEADERS, timeout=120)
    r.raise_for_status()
    return r.content
```

**STT transcription** — `_transcribe_voicebox` (`stt_service.py:195`) POSTs
`/transcribe` as multipart (`files={"audio": ("audio.webm", <bytes>, "audio/webm")}`,
`data={"model": model or "base"}`, 120s). The reply is parsed tolerantly by
`_parse_voicebox_text` (`stt_service.py:174`): a bare string, `{"text":...}`,
`{"transcription":...}`, or `{"segments":[{"text":...}]}` (segments joined).

**Profiles for the UI** — `_voicebox_profiles` (`tts_service.py:160`) GETs
`/profiles` (5s) and tolerates a bare list, `{"profiles":[...]}`, or
`{"data":[...]}`. The settings UI hits the same `/profiles` endpoint directly to
populate a voice datalist (`settings.js:1137`).

---

## 15. Secrets handling notes

No secret values appear in this document. Relevant redaction points in the code:
endpoint listing returns `has_key` only, never `api_key` (`model_routes.py:1195`);
non-admin settings are passed through `scrub_settings` (`auth_routes.py:412`);
integrations are masked via `mask_integration_secret` (`auth_routes.py:441`); the
internal-tool token and session cookie are never persisted or returned in responses
(`core/middleware.py:16`, `auth_routes.py:138`).
