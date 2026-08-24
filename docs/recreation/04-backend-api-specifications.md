# 04 — Backend API Specifications

Apollo's HTTP surface is assembled in `app.py` from **49 router modules**
under `routes/` (57 files total in that directory; 8 are shared helper
modules with no `@router` decorators of their own: `__init__.py`,
`chat_helpers.py`, `cookbook_helpers.py`, `cookbook_runner_files.py`,
`document_helpers.py`, `email_helpers.py`, `email_pollers.py`,
`gallery_helpers.py`). Endpoint counts below come from
`grep -cE '^\s*@router\.(get|post|put|delete|patch|websocket)\(' routes/*.py`
— **465 endpoints** total, verified against the live decorators, not
inferred.

---

## 1. Authentication & authorization model

### 1.1 Two operating modes

Set by `AUTH_ENABLED` (`app.py:155`, default `"true"`):

```python
# app.py:155-158
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
LOCALHOST_BYPASS = os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"
```

- **`AUTH_ENABLED=false` — loopback desktop mode.** `app.add_middleware(AuthMiddleware)`
  is skipped entirely (`app.py:402-405`); no cookie/session/token check ever
  runs. This is the mode the macOS `.dmg` launcher ships in — the app talks
  to itself over `127.0.0.1` with no login screen. Every `Depends`-style
  admin check (`core.middleware.require_admin`, `routes/auth_routes.py`'s
  `_require_admin_user`) has an explicit `if os.getenv("AUTH_ENABLED", "true").lower() == "false": return`
  early-out (or `return None`) so admin-gated routes still work with no
  logged-in user.
- **`AUTH_ENABLED=true` (default) — cookie-session mode.** `AuthMiddleware`
  (a `BaseHTTPMiddleware`, `app.py:280-402`) runs on every request and
  resolves `request.state.current_user` through one of four paths, in order:

```python
# app.py:280-400 (structure, not verbatim — see exact code below for each branch)
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if _is_auth_exempt(path):               # public paths — see 1.2
            return await call_next(request)
        # (a) internal-tool token — loopback agent tool calls
        # (b) LOCALHOST_BYPASS + direct loopback — no-login desktop testing
        # (c) Bearer ody_... — API token (external integrations)
        # (d) apollo_session cookie — normal browser session
```

**(a) Internal-tool bypass** (`app.py:289-310`): a per-process random token
(`core.middleware.INTERNAL_TOOL_TOKEN = os.environ.get("APOLLO_INTERNAL_TOKEN") or secrets.token_hex(32)`,
`core/middleware.py:21`) lets the agent's own tool layer (e.g. the `app_api`
tool) call admin-gated routes over HTTP loopback without a session cookie.
Requires the `X-Apollo-Internal-Token` header to match AND
`_is_trusted_loopback(request)` (direct `127.0.0.1`/`::1` connection with
**none** of `cf-connecting-ip`, `x-forwarded-for`, etc. — blocks a Cloudflare
tunnel or reverse proxy from inheriting loopback trust). Sets
`request.state.current_user = "internal-tool"` (or impersonates a real user
via `X-Apollo-Owner` if that header names an existing account) and
`request.state.internal_tool = True`.

**(b) `LOCALHOST_BYPASS=true` + trusted loopback** (`app.py:316-324`): acts as
a real user (`_bypass_user()` — prefers an admin, else the first user, else
`""`) so ownership-scoped routes (sessions, documents) don't 403 with no
login. **Do not set this on a network-exposed instance.**

**(c) Bearer token** (`app.py:332-386`): `Authorization: Bearer ody_<43 base64
chars>`. Prefix-sharded, bcrypt-checked against an in-memory cache of active
`ApiToken` rows (invalidated on token create/revoke via
`app.state.invalidate_token_cache`). Sets `request.state.current_user = "api"`,
`request.state.api_token = True`, `request.state.api_token_owner`, and
`request.state.api_token_scopes`.

**(d) Session cookie** (`app.py:388-400`): `apollo_session` cookie, validated
via `auth_manager.validate_token(token)`. Sets
`request.state.current_user = auth_manager.get_username_for_token(token)`,
`request.state.auth_mode = "cookie"`.

Any request that matches none of (a)-(d) gets `401 {"error": "Not authenticated"}`
for `/api/*` paths, or a `302` redirect to `/login` otherwise.

### 1.2 Auth-exempt paths (only meaningful when `AUTH_ENABLED=true`)

```python
# app.py:180-214
AUTH_EXEMPT_EXACT = {
    "/api/auth/setup", "/api/auth/signup", "/api/auth/login", "/api/auth/logout",
    "/api/auth/status", "/api/auth/features", "/api/auth/settings",
    "/api/auth/integrations/presets", "/api/health", "/api/version", "/login",
    "/api/paperclip/events",   # self-authenticates via PAPERCLIP_EVENTS_TOKEN
}
AUTH_EXEMPT_PREFIXES = ["/static", "/lmproxy"]   # lmproxy has its own bearer token
AUTH_EXEMPT_PATTERNS = [re.compile(r"^/api/tasks/[^/]+/webhook/[^/]+/?$")]  # path IS the credential
```

Note `/api/auth/settings` is exempt at the *middleware* level (so the request
reaches the handler without a cookie) but the handler itself
(`GET /api/auth/settings`, §5.1) still gates the *full* settings payload
behind `_require_admin_user` — non-admins/unauthenticated callers get
`scrub_settings(settings)` instead of a 401.

### 1.3 `require_admin` vs `_require_admin_user` — two different gates, on purpose

**`core/middleware.py:25-55` — `require_admin(request)`.** Used by most
admin-gated routers (`hub_routes`, `localmodels_routes`, `embedding_routes`,
`preset_routes`, `contacts_routes`, `activity_routes`, `upload_routes`,
`admin_wipe_routes`, `diagnostics_routes`, `system_status_routes`,
`personal_routes`, `model_routes`'s `require_admin`-style checks, etc.):

```python
# core/middleware.py:25-55
def require_admin(request: Request):
    hdr = request.headers.get(INTERNAL_TOOL_HEADER)
    if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
        return
    if getattr(request.state, "internal_tool", False):
        return
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return
    if not auth_mgr or not auth_mgr.is_configured:
        raise HTTPException(403, "Admin only")
    user = getattr(request.state, "current_user", None)
    if not user or not auth_mgr.is_admin(user):
        raise HTTPException(403, "Admin only")
```

It **trusts `request.state.current_user`**, which `AuthMiddleware` populates
through *every* branch in §1.1 — including the internal-tool and
`LOCALHOST_BYPASS` paths. That's correct for routes where "an authenticated
loopback caller acting as an admin" is the desired behavior (most admin
CRUD — creating a model endpoint, scanning local models, etc.).

**`routes/auth_routes.py:88-110` — `_require_admin_user(request)`.** Used
**only** inside `auth_routes.py` itself (user list/create/delete/rename,
settings, integrations, features-write). Deliberately does **not** delegate
to `core.middleware.require_admin`:

```python
# routes/auth_routes.py:88-110
def _require_admin_user(request: Request) -> Optional[str]:
    """Admin gate for this router — strict, plus the desktop-mode allowance.

    Delegating wholesale to core.middleware.require_admin is WRONG here:
    that helper trusts ``request.state.current_user``, which the auth
    middleware populates through loopback/bypass paths. On a direct
    loopback request that turned GET /api/auth/users from 403 into 200
    for an UNAUTHENTICATED caller (verified by A/B against main), leaking
    usernames and privilege flags.

    So keep the strict cookie-validating check exactly as it was, and add
    only the one thing that was missing: the no-login desktop mode
    (AUTH_ENABLED=false) that the macOS bundle launcher ships and that
    every require_admin route already honors.
    """
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return None
    user = _get_current_user(request)   # reads the apollo_session cookie DIRECTLY
    if not user or not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")
    return user
```

**Why the split matters, concretely:** `GET /api/auth/users` returns every
username plus `is_admin`/privilege flags. If it used `require_admin`, an
internal-tool-token loopback call (or a `LOCALHOST_BYPASS` connection with no
real login) would sail through and dump the full user list to whatever
process holds the internal token — normally fine for "create a model
endpoint," never fine for "list every account and its admin flag." So
`_require_admin_user` re-reads the cookie itself (`_get_current_user`,
bypassing `request.state.current_user` entirely) and only adds the one
allowance every other admin route already has: `AUTH_ENABLED=false` desktop
mode returns `None` (no gate) so the no-login launcher still works. **Rule of
thumb for reimplementation:** any route that returns *account/privilege
metadata itself* (not just performs an admin action) must use the strict
cookie-only check, not the loopback-trusting one.

### 1.4 Verified endpoints with no auth gate at all

A second, independent read of every handler body (not just router-level
imports) confirms these routes have **no** `require_admin`/`require_user`/
`require_owner`/`require_privilege` call anywhere in the function — they are
reachable by anyone who can reach the global middleware (i.e. anyone with a
valid session in cookie mode, or literally anyone in `AUTH_ENABLED=false`
desktop mode), regardless of what the rest of their router does:

- **`GET /api/tools`** (`model_routes.py::list_tools`) — no auth, while the
  sibling `POST /api/tools` is admin-gated. Read-only tool-list leak, low
  severity but inconsistent with its own file.
- **`POST /api/image/sharpen`** (`gallery_routes.py::sharpen_image`) — every
  other `/api/image/*` op in the same file requires `can_generate_images`;
  this one doesn't.
- **`POST /api/presets/expand`** (`preset_routes.py::expand_character_prompt`)
  — an AI-backed endpoint (spends an LLM call) with no gate.
- **`POST /api/tasks/parse`** (`task_routes.py::parse_task`) — an LLM-backed
  NL→task-draft endpoint, no gate (draft-only output limits blast radius).
- **`POST /api/upload`** (`upload_routes.py::api_upload`) — no identity
  check, only per-IP rate limiting (§5.9).
- **`GET /api/upload/{file_id}`** and **`GET/PUT /api/upload/{file_id}/vision`**
  — gated only by upload-id format validation; the owner check inside only
  applies `if auth_configured`, so it's a no-op in desktop/unconfigured mode.
- **`POST /api/tasks/{task_id}/webhook/{token}`** — intentionally open by
  design (§5.8): the token in the path is the credential.
- `lmproxy_routes.py` and `POST /api/paperclip/events` use their own bearer
  tokens instead of the session system (by design, §1.2).

### 1.5 Owner scoping

Most domain tables carry a nullable `owner` column (see `03-database-schema-data-models.md`).
`NULL` owner = legacy/shared, visible to everyone; a non-null owner scopes a
row to one user, with admins generally seeing everything. `src/auth_helpers.py`
provides `get_current_user`, `require_user`, `effective_user` (the *canonical*
owner — resolves API-token requests to the token's real owner instead of the
literal `"api"` principal, which several routers explicitly call out as a
past cross-tenant leak, e.g. `routes/memory_routes.py:43-48`,
`routes/task_routes.py:188-193`), and `require_privilege(request, "can_manage_memory")`
for the fine-grained non-admin privilege system (`auth_manager.get_privileges(user)`).

---

## 2. SSE streaming — `chat_stream` and friends

### 2.1 `POST /api/chat_stream` (`routes/chat_routes.py:419`)

Request is **multipart form**, not JSON:

| Field | Type | Notes |
|---|---|---|
| `message` | str | user message |
| `session` | str | session id (or resolved default) |
| `attachments` | str | JSON-encoded list of upload IDs |
| `mode` | str | `"chat"` \| `"agent"` (auto-escalates chat→agent on tool-intent patterns) |
| `use_web`, `use_research`, `allow_bash`, `allow_web_search`, `use_rag` | str (`"true"`/`"false"`) | feature toggles |
| `preset_id`, `active_doc_id`, `time_filter`, `search_context` | str | optional |
| `compare_mode`, `incognito` | str | mode flags |

Response: `StreamingResponse(..., media_type="text/event-stream")`. Every
event is `data: <json>\n\n` (SSE "data" framing); the payload's `type` field
(or bare `delta` for a plain text token) discriminates the event:

| `type` | Payload keys | Meaning |
|---|---|---|
| *(none, bare)* | `{"delta": str, "thinking"?: bool}` | one streamed text token; `thinking:true` = reasoning-model `<think>` content, forwarded but not saved |
| `model_info` | `model`, `suffix?`, `character_name?` | sent early so the UI shows the model before tokens arrive |
| `attachments` | `data` | attachment metadata for this turn |
| `rag_sources` / `web_sources` | `data` | citations used to answer |
| `web_search_failed` | — | web search was attempted and failed |
| `memories_used` | `data` | memory entries injected into context |
| `compacted` | `context_length` | auto-compaction fired for this turn |
| `tool_start` | `tool`, `command` | agent-mode tool call beginning |
| `tool_output` | `tool`, `command`, `output`, `exit_code` | agent-mode tool call finished |
| `fallback` | `answered_by` | the session's primary model failed; a configured fallback answered instead |
| `usage` / `metrics` | `data: {input_tokens, output_tokens, tokens_per_second, context_percent, response_time, model, ...}` | token/perf stats, sent once near the end |
| `research_progress` / `research_sources` / `research_findings` / `research_done` | varies | deep-research sub-pipeline progress (`use_research=true`) |

Plain SSE comment lines (`: heartbeat\n\n`) are sent during long-running
non-streaming work (e.g. image generation) to keep the connection alive
through proxies that time out idle SSE. Named error events use
`event: error\ndata: {json}\n\n` (distinct from the bare `data:` frames).
The stream **always** terminates with the literal line `data: [DONE]\n\n`.

### 2.2 Detached-run architecture — survives client disconnect

`chat_stream` does **not** stream directly from the HTTP handler. It starts
the generator as a **background asyncio task** and the HTTP response merely
*subscribes* to it:

```python
# routes/chat_routes.py:1222-1228
# Run the stream as a DETACHED background task so it survives the client
# closing the tab / navigating away (true terminal-agent behavior). The
# SSE response just subscribes (replay buffered output + live); dropping
# the SSE only removes a subscriber — the run keeps going and saves the
# assistant message on completion regardless. Reconnect via /api/chat/resume.
agent_runs.start(session, _safe_stream())
return StreamingResponse(agent_runs.subscribe(session), media_type="text/event-stream")
```

`src/agent_runs.py` (`_Run` class) keeps an ordered in-memory replay buffer
per session plus a set of subscriber queues; closing the SSE connection just
drops one subscriber, the drain task (and the underlying LLM call) keeps
running. A finished run's buffer is retained for `_EVICT_GRACE_S = 180`
seconds after the last subscriber leaves, so a reconnect within that window
replays everything. **Durability is in-memory only** — a server restart
loses any in-flight run.

- `GET /api/chat/resume/{session_id}` — reconnect to a still-running detached
  stream; `404` if none is active (`agent_runs.is_active`).
- `POST /api/chat/stop/{session_id}` — cancel an in-progress run.
- `GET /api/chat/stream_status/{session_id}` — poll whether a run is active
  without opening an SSE connection.

On genuine client-side cancellation (`asyncio.CancelledError`/`GeneratorExit`
inside the generator itself — not just an SSE disconnect, which the detached
architecture above absorbs), the partial response is still saved to the
session with `metadata={"stopped": True, ...}` (`routes/chat_routes.py:1193-1209`).

### 2.3 Other SSE endpoints

`POST /api/rewrite` (`routes/chat_routes.py:1345`) streams the same
`data: {...}` / `event: error` / `data: [DONE]` framing for regenerating one
assistant message. `GET /api/research/stream/{session_id}`
(`routes/research_routes.py:453`) streams deep-research progress with the
same SSE conventions. `POST /api/shell/stream`
(`routes/shell_routes.py:777`) streams live command output.

---

## 3. Router index

Prefix is the router's own `APIRouter(prefix=...)`; an empty prefix means
every path below already has the full `/api/...` baked in (shown in §4 as
the literal decorator path).

| Router file | Prefix | Purpose | Endpoints |
|---|---|---|---|
| `activity_routes.py` | `/api/activity` | Agent activity ledger ("computer history") — admin-only, undo file writes | 5 |
| `admin_wipe_routes.py` | `/api/admin` | Per-category "Danger Zone" data wipes | 1 |
| `api_token_routes.py` | `/api` | API bearer-token management for external integrations | 3 |
| `assistant_routes.py` | `/api/assistant` | Personal-assistant singleton (a flagged CrewMember + 3 daily check-in tasks) | 6 |
| `auth_routes.py` | `/api/auth` | Login/logout/signup, 2FA, users, settings, integrations CRUD | 27 |
| `backup_routes.py` | *(none)* | Export/import user data bundle | 2 |
| `browser_routes.py` | `/api/browser` | Agent-controlled browser panel + websocket | 14 |
| `calendar_routes.py` | `/api/calendar` | Local SQLite calendar CRUD, RRULE, ICS import/export | 15 |
| `chat_routes.py` | *(none)* | `/api/chat`, `/api/chat_stream`, search, rewrite, review | 9 |
| `cleanup_routes.py` | `/api/cleanup` | Old-session cleanup preview + execute | 2 |
| `compare_routes.py` | `/api/compare` | A/B model comparison | 5 |
| `contacts_routes.py` | `/api/contacts` | CardDAV (Radicale) contacts integration | 10 |
| `cookbook_routes.py` | *(none)* | Model download/serve/cache scanning ("Cookbook") | 12 |
| `diagnostics_routes.py` | *(none)* | DB/RAG stats, YouTube transcript test, research test | 4 |
| `document_routes.py` | *(none)* | Living-document CRUD + PDF fill/export/versions | 23 |
| `editor_draft_routes.py` | *(none)* | Persisted gallery-editor drafts | 5 |
| `email_routes.py` | `/api/email` | IMAP/SMTP email client (largest router) | 41 |
| `embedding_routes.py` | `/api/embeddings` | Local fastembed model + custom embedding endpoint mgmt | 7 |
| `emoji_routes.py` | `/api/emoji` | Same-origin emoji SVG proxy/cache | 1 |
| `font_routes.py` | `/api/fonts` | Custom font file discovery | 1 |
| `gallery_routes.py` | *(none)* | Photo/AI-image library + editing ops | 32 |
| `history_routes.py` | *(none)* | Session history: truncate, edit, fork, compact, topics | 11 |
| `hub_routes.py` | `/api/hub` | Model hub: free cloud models, HF GGUF pulls, personas, reference library | 15 |
| `hwfit_routes.py` | `/api/hwfit` | Hardware-fit model recommendation simulator | 4 |
| `integration_routes.py` | *(none)* | Cross-integration status (agent workbench) | 1 |
| `lmproxy_routes.py` | *(none)* | Localhost OpenAI-compatible proxy for Paperclip's local agents | 1 |
| `localmodels_routes.py` | `/api/local-models` | On-disk GGUF model discovery + llama-server lifecycle | 9 |
| `mcp_routes.py` | `/api/mcp` | MCP server management + OAuth | 11 |
| `memory_routes.py` | `/api/memory` | Long-term memory CRUD, extraction, import/export, graph | 19 |
| `model_routes.py` | `/api` | Model/provider endpoint management, `/models`, `/model-endpoints` | 19 |
| `note_routes.py` | `/api/notes` | Google-Keep-style notes/checklists | 10 |
| `paperclip_routes.py` | *(none)* | Reverse proxy + status for the bundled Paperclip sidecar | 6 |
| `personal_routes.py` | `/api/personal` | Personal-document RAG indexing | 6 |
| `prefs_routes.py` | `/api/prefs` | Per-user JSON key/value preference store | 3 |
| `preset_routes.py` | *(none)* | Chat persona/system-prompt presets + templates | 8 |
| `research_routes.py` | *(none)* | Deep-research background task lifecycle | 17 |
| `search_routes.py` | *(none)* | Web search config/providers/query, SearXNG install | 6 |
| `session_routes.py` | `/api` | Session CRUD, archive, export, fork-adjacent ops | 19 |
| `shell_routes.py` | *(none)* | User-facing shell command execution (sync + SSE) | 4 |
| `signature_routes.py` | *(none)* | Saved visual-signature CRUD | 3 |
| `skill_pack_routes.py` | `/api/skills/packs` | Install Agent Skills packs from GitHub/zip | 2 |
| `skills_routes.py` | `/api/skills` | Skills system CRUD, testing, audit | 18 |
| `stt_routes.py` | `/api/stt` | Speech-to-text (multi-provider) | 2 |
| `system_status_routes.py` | *(none)* | Unified system status + admin actions | 2 |
| `task_routes.py` | `/api/tasks` | Scheduled/event/webhook task CRUD + runs | 23 |
| `tts_routes.py` | `/api/tts` | Text-to-speech (multi-provider) | 3 |
| `upload_routes.py` | `/api/upload` | File upload, download, vision-OCR cache | 6 |
| `vault_routes.py` | `/api/vault` | Vaultwarden/Bitwarden CLI integration | 6 |
| `webhook_routes.py` | `/api` | Outgoing webhooks + a `/v1/chat` compat endpoint | 6 |

---

## 4. Full endpoint reference

Grouped by router; path is the full route (prefix + decorator path). Auth
column: **Admin** = gated by `require_admin`/`_require_admin_user`/equivalent
role check; **User** = requires any authenticated identity
(`require_user`/`get_current_user`/owner check) when `AUTH_ENABLED=true`;
**Open** = no explicit gate beyond the global middleware (still requires a
valid session/token in cookie-session mode, since the global `AuthMiddleware`
covers all non-exempt `/api/*` paths by default); **Public** = in
`AUTH_EXEMPT_EXACT`/prefix, reachable with zero credentials;
**Self-auth** = the handler authenticates itself independently of Apollo's
session system (webhook token, internal bearer token, path secret).

### activity_routes.py — Admin (all routes call `require_admin`)
| Method | Path |
|---|---|
| GET | `/api/activity` |
| POST | `/api/activity/undo-session/{session_id}` |
| GET | `/api/activity/autonomy` |
| PUT | `/api/activity/autonomy` |
| POST | `/api/activity/{event_id}/undo` |

### admin_wipe_routes.py — Admin
| Method | Path |
|---|---|
| DELETE | `/api/admin/wipe/{kind}` — `kind` ∈ chats/memory/skills/notes/tasks/documents/gallery/calendar |

### api_token_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| GET | `/api/tokens` |
| POST | `/api/tokens` |
| DELETE | `/api/tokens/{token_id}` |

### assistant_routes.py — User
| Method | Path |
|---|---|
| GET | `/api/assistant/session` |
| GET | `/api/assistant/settings` |
| PATCH | `/api/assistant/settings` |
| POST | `/api/assistant/run/{task_id}` |
| GET | `/api/assistant/run-status/{task_id}` |
| GET | `/api/assistant/available-timezones` |

### auth_routes.py — see §5.1 for full detail
| Method | Path | Auth |
|---|---|---|
| POST | `/api/auth/setup` | Public (only works pre-configuration) |
| POST | `/api/auth/signup` | Public (only if signup enabled) |
| POST | `/api/auth/login` | Public |
| POST | `/api/auth/logout` | User |
| GET | `/api/auth/status` | Public |
| POST | `/api/auth/change-password` | User |
| POST | `/api/auth/2fa/setup` | User |
| POST | `/api/auth/2fa/confirm` | User |
| POST | `/api/auth/2fa/disable` | User |
| GET | `/api/auth/2fa/status` | User |
| GET | `/api/auth/users` | Admin (strict) |
| POST | `/api/auth/users` | Admin (strict) |
| PUT | `/api/auth/users/{username}/privileges` | Admin (strict) |
| PUT | `/api/auth/users/{username}/rename` | Admin (strict) |
| POST | `/api/auth/signup-toggle` *(deprecated)* | Admin (strict) |
| PUT | `/api/auth/open-signup` | Admin (strict) |
| DELETE | `/api/auth/users` | Admin (strict) |
| GET | `/api/auth/features` | Public |
| POST | `/api/auth/features` | Admin (strict) |
| GET | `/api/auth/settings` | Public path, admin-scrubbed response |
| POST | `/api/auth/settings` | Admin (strict) |
| GET | `/api/auth/integrations` | Admin (strict) |
| GET | `/api/auth/integrations/presets` | Public |
| POST | `/api/auth/integrations` | Admin (strict) |
| PUT | `/api/auth/integrations/{integration_id}` | Admin (strict) |
| DELETE | `/api/auth/integrations/{integration_id}` | Admin (strict) |
| POST | `/api/auth/integrations/{integration_id}/test` | Admin (strict) |

### backup_routes.py — Admin
| Method | Path |
|---|---|
| GET | `/api/export` |
| POST | `/api/import` |

### browser_routes.py — Privilege-gated (`require_privilege(request, "can_use_browser")` on every route)
| Method | Path |
|---|---|
| GET | `/api/browser/status` |
| POST | `/api/browser/navigate` |
| GET | `/api/browser/current` |
| GET | `/api/browser/html` |
| GET | `/api/browser/text` |
| POST | `/api/browser/execute` |
| POST | `/api/browser/screenshot` |
| POST | `/api/browser/wait` |
| POST | `/api/browser/click` |
| POST | `/api/browser/type` |
| GET | `/api/browser/events` |
| POST | `/api/browser/detect-localhost` |
| GET | `/api/browser/tools` |
| WS | `/api/browser/ws` |

### calendar_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| GET | `/api/calendar/config` |
| POST | `/api/calendar/config` |
| POST | `/api/calendar/test` |
| POST | `/api/calendar/sync` |
| GET | `/api/calendar/calendars` |
| GET | `/api/calendar/events` |
| POST | `/api/calendar/events` |
| PUT | `/api/calendar/events/{uid}` |
| DELETE | `/api/calendar/events/{uid}` |
| POST | `/api/calendar/calendars` |
| PUT | `/api/calendar/calendars/{cal_id}` |
| DELETE | `/api/calendar/calendars/{cal_id}` |
| POST | `/api/calendar/import` |
| GET | `/api/calendar/export/{cal_id}` |
| POST | `/api/calendar/quick-parse` |

### chat_routes.py — see §5.2
| Method | Path |
|---|---|
| POST | `/api/chat` |
| POST | `/api/chat_stream` |
| GET | `/api/chat/resume/{session_id}` |
| POST | `/api/chat/stop/{session_id}` |
| GET | `/api/chat/stream_status/{session_id}` |
| POST | `/api/inject_context/{session_id}` |
| GET | `/api/search` |
| POST | `/api/rewrite` |
| POST | `/api/review` |

### cleanup_routes.py — User
| Method | Path |
|---|---|
| GET | `/api/cleanup/preview` |
| POST | `/api/cleanup` |

### compare_routes.py — User
| Method | Path |
|---|---|
| POST | `/api/compare/start` |
| POST | `/api/compare/{comp_id}/vote` |
| POST | `/api/compare/record` |
| GET | `/api/compare/history` |
| DELETE | `/api/compare/{comp_id}` |

### contacts_routes.py — Admin
| Method | Path |
|---|---|
| GET | `/api/contacts/list` |
| GET | `/api/contacts/search` |
| POST | `/api/contacts/add` |
| POST | `/api/contacts/import` |
| GET | `/api/contacts/export` |
| GET | `/api/contacts/config` |
| PUT | `/api/contacts/config` |
| DELETE | `/api/contacts/clear` |
| PUT | `/api/contacts/{uid}` |
| DELETE | `/api/contacts/{uid}` |

### cookbook_routes.py — User (some sub-actions admin-gated internally)
| Method | Path |
|---|---|
| GET | `/api/cookbook/ssh-key` |
| POST | `/api/cookbook/ssh-key` |
| POST | `/api/model/download` |
| GET | `/api/model/cached` |
| POST | `/api/model/serve` |
| POST | `/api/cookbook/setup` |
| GET | `/api/cookbook/gpus` |
| POST | `/api/cookbook/kill-pid` |
| GET | `/api/cookbook/state` |
| POST | `/api/cookbook/state` |
| GET | `/api/cookbook/hf-latest` |
| GET | `/api/cookbook/tasks/status` |

### diagnostics_routes.py — Admin (`require_admin` imported; used on the mutating/expensive routes)
| Method | Path |
|---|---|
| GET | `/api/db/stats` |
| GET | `/api/rag/stats` |
| GET | `/api/test/youtube` |
| POST | `/api/test-research` |

### document_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| POST | `/api/document` |
| POST | `/api/documents/import-pdf` |
| GET | `/api/documents/library` |
| GET | `/api/documents/{session_id}` |
| GET | `/api/document/{doc_id}` |
| POST | `/api/document/{doc_id}/archive` |
| POST | `/api/document/{doc_id}/extract-pdf-text` |
| POST | `/api/documents/export-zip` |
| PUT | `/api/document/{doc_id}` |
| PATCH | `/api/document/{doc_id}` |
| DELETE | `/api/document/{doc_id}` |
| GET | `/api/document/{doc_id}/versions` |
| GET | `/api/document/{doc_id}/version/{num}` |
| POST | `/api/document/{doc_id}/restore/{num}` |
| POST | `/api/documents/tidy` |
| POST | `/api/documents/ai-tidy` |
| POST | `/api/document/{doc_id}/export-pdf/preview` |
| GET | `/api/document/{doc_id}/render-pages` |
| GET | `/api/document/{doc_id}/page/{page_no}.png` |
| POST | `/api/document/{doc_id}/ai-fill-annotations` |
| GET | `/api/document/{doc_id}/render-pdf` |
| GET | `/api/document/{doc_id}/export-pdf` |
| POST | `/api/document/{doc_id}/prepare-signed-reply` |

### editor_draft_routes.py — User
| Method | Path |
|---|---|
| GET | `/api/editor-drafts` |
| GET | `/api/editor-drafts/{draft_id}` |
| POST | `/api/editor-drafts` |
| PUT | `/api/editor-drafts/{draft_id}` |
| DELETE | `/api/editor-drafts/{draft_id}` |

### email_routes.py — User (`require_owner`/`require_user`, owner-scoped throughout)
| Method | Path |
|---|---|
| GET | `/api/email/list` |
| POST | `/api/email/{uid}/unflag-spam` |
| GET | `/api/email/contacts` |
| GET | `/api/email/search` |
| GET | `/api/email/read/{uid}` |
| GET | `/api/email/attachments/{uid}` |
| GET | `/api/email/attachment/{uid}/{index}` |
| POST | `/api/email/attachment-as-doc/{uid}/{index}` |
| POST | `/api/email/attachment-path/{uid}/{index}` |
| POST | `/api/email/mark-unread/{uid}` |
| POST | `/api/email/mark-read/{uid}` |
| POST | `/api/email/archive/{uid}` |
| DELETE | `/api/email/delete/{uid}` |
| DELETE | `/api/email/delete-permanent/{uid}` |
| DELETE | `/api/email/apollo/reminders` |
| POST | `/api/email/move/{uid}` |
| GET | `/api/email/folders` |
| POST | `/api/email/mark-answered/{uid}` |
| POST | `/api/email/clear-answered/{uid}` |
| POST | `/api/email/compose-upload` |
| DELETE | `/api/email/compose-upload/{token}` |
| POST | `/api/email/schedule` |
| GET | `/api/email/scheduled` |
| DELETE | `/api/email/scheduled/{sid}` |
| GET | `/api/email/resolve-contact` |
| POST | `/api/email/send` |
| POST | `/api/email/draft` |
| POST | `/api/email/extract-style` |
| POST | `/api/email/summarize` |
| POST | `/api/email/ai-reply` |
| GET | `/api/email/style` |
| PUT | `/api/email/style` |
| GET | `/api/email/config` |
| PUT | `/api/email/config` |
| GET | `/api/email/urgency-state` |
| GET | `/api/email/accounts` |
| POST | `/api/email/accounts` |
| PUT | `/api/email/accounts/{account_id}` |
| DELETE | `/api/email/accounts/{account_id}` |
| POST | `/api/email/accounts/test` |
| POST | `/api/email/accounts/{account_id}/set-default` |

### embedding_routes.py — Admin (`Depends(require_admin)`)
| Method | Path |
|---|---|
| GET | `/api/embeddings/models` |
| POST | `/api/embeddings/models/{model_name:path}/download` |
| GET | `/api/embeddings/models/{model_name:path}/status` |
| DELETE | `/api/embeddings/models/{model_name:path}` |
| GET | `/api/embeddings/endpoint` |
| POST | `/api/embeddings/endpoint` |
| DELETE | `/api/embeddings/endpoint` |

### emoji_routes.py — Public (same-origin proxy, cached on disk)
| Method | Path |
|---|---|
| GET | `/api/emoji/{code}.svg` |

### font_routes.py — Open
| Method | Path |
|---|---|
| GET | `/api/fonts/custom` |

### gallery_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| POST | `/api/gallery/upload` |
| POST | `/api/gallery/{image_id}/replace` |
| POST | `/api/gallery/{image_id}/rename` |
| POST | `/api/gallery/{image_id}/rotate` |
| POST | `/api/gallery/ai-upscale` |
| POST | `/api/gallery/style-transfer` |
| GET | `/api/gallery/tags` |
| GET | `/api/gallery/library` |
| GET | `/api/gallery/albums` |
| POST | `/api/gallery/albums` |
| GET | `/api/gallery/stats` |
| POST | `/api/gallery/ai-tag-batch` |
| GET | `/api/gallery/{image_id}` |
| PATCH | `/api/gallery/{image_id}` |
| POST | `/api/gallery/download-zip` |
| POST | `/api/gallery/clear-user-tags` |
| POST | `/api/gallery/clear-ai-tags` |
| POST | `/api/gallery/dedupe-tags` |
| DELETE | `/api/gallery/{image_id}` |
| POST | `/api/image/inpaint` |
| POST | `/api/image/harmonize` |
| POST | `/api/image/sharpen` |
| POST | `/api/image/denoise` |
| POST | `/api/image/upscale-local` |
| POST | `/api/image/remove-bg` |
| POST | `/api/image/enhance-face` |
| PUT | `/api/gallery/albums/{album_id}` |
| DELETE | `/api/gallery/albums/{album_id}` |
| POST | `/api/gallery/albums/{album_id}/add` |
| POST | `/api/gallery/albums/{album_id}/remove` |
| POST | `/api/gallery/{image_id}/favorite` |
| POST | `/api/gallery/{image_id}/ai-tag` |

### history_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| GET | `/api/history/{session_id}` |
| POST | `/api/session/{session_id}/truncate` |
| POST | `/api/session/{session_id}/message` |
| POST | `/api/session/{session_id}/delete-messages` |
| POST | `/api/session/{session_id}/edit-message` |
| POST | `/api/session/{session_id}/mark-stopped` |
| POST | `/api/session/{session_id}/update-last-meta` |
| POST | `/api/session/{session_id}/merge-last-assistant` |
| POST | `/api/session/{session_id}/fork` |
| GET | `/api/conversations/topics` |
| POST | `/api/session/{session_id}/compact` |

### hub_routes.py — see §5.3 (all `require_admin`)
| Method | Path |
|---|---|
| GET | `/api/hub/free-models` |
| POST | `/api/hub/free-endpoint` |
| GET | `/api/hub/gguf-search` |
| GET | `/api/hub/gguf-files` |
| POST | `/api/hub/gguf-download` |
| GET | `/api/hub/gguf-downloads` |
| GET | `/api/hub/codex-router` |
| GET | `/api/hub/security-scan` |
| GET | `/api/hub/catalog` |
| POST | `/api/hub/personas/preview` |
| POST | `/api/hub/personas/install` |
| GET | `/api/hub/reference/sources` |
| POST | `/api/hub/reference/install` |
| POST | `/api/hub/reference/remove` |
| GET | `/api/hub/reference/search` |

### hwfit_routes.py — Open
| Method | Path |
|---|---|
| GET | `/api/hwfit/system` |
| GET | `/api/hwfit/models` |
| GET | `/api/hwfit/profiles` |
| GET | `/api/hwfit/image-models` |

### integration_routes.py — Open
| Method | Path |
|---|---|
| GET | `/api/integrations/agent-workbench/status` |

### lmproxy_routes.py — Self-auth (bearer token, exempt from session middleware)
| Method | Path |
|---|---|
| GET | `/lmproxy/v1/models` |

### localmodels_routes.py — see §5.4 (all `require_admin`)
| Method | Path |
|---|---|
| GET | `/api/local-models` |
| POST | `/api/local-models/scan` |
| GET | `/api/local-models/voices` |
| GET | `/api/local-models/dirs` |
| PUT | `/api/local-models/dirs` |
| GET | `/api/local-models/binary` |
| PUT | `/api/local-models/binary` |
| POST | `/api/local-models/{model_id}/start` |
| POST | `/api/local-models/{model_id}/stop` |

### mcp_routes.py — Admin
| Method | Path |
|---|---|
| GET | `/api/mcp/servers` |
| POST | `/api/mcp/servers` |
| POST | `/api/mcp/servers/{server_id}/reconnect` |
| PATCH | `/api/mcp/servers/{server_id}` |
| DELETE | `/api/mcp/servers/{server_id}` |
| GET | `/api/mcp/tools` |
| GET | `/api/mcp/servers/{server_id}/tools` |
| PATCH | `/api/mcp/servers/{server_id}/tools` |
| GET | `/api/mcp/oauth/authorize/{server_id}` |
| GET | `/api/mcp/oauth/callback` |
| POST | `/api/mcp/oauth/exchange/{server_id}` |

### memory_routes.py — see §5.5 (User, owner-scoped; `can_manage_memory` privilege on mutating routes)
| Method | Path |
|---|---|
| POST | `/api/memory/debug` |
| POST | `/api/memory/add` |
| GET | `/api/memory` |
| GET | `/api/memory/{memory_id}/provenance` |
| GET | `/api/memory/export-pack` |
| POST | `/api/memory/import-pack` |
| GET | `/api/memory/graph` |
| POST | `/api/memory/search` |
| GET | `/api/memory/timeline` |
| GET | `/api/memory/by-session/{session_id}` |
| POST | `/api/memory/extract` |
| POST | `/api/memory/audit` |
| POST | `/api/memory/import` |
| POST | `/api/memory/distill-session` |
| POST | `/api/memory/import-chat-export` |
| POST | `/api/memory/{memory_id}/pin` |
| GET | `/api/memory/{memory_id}` |
| PUT | `/api/memory/{memory_id}` |
| DELETE | `/api/memory/{memory_id}` |

### model_routes.py — see §5.6 (Admin on `/model-endpoints*`; `/models`, `/providers` open to any authenticated user)
| Method | Path |
|---|---|
| GET | `/api/models` |
| GET | `/api/model-endpoints/probe-local` |
| GET | `/api/ping` |
| POST | `/api/probe-selected` |
| GET | `/api/probe` |
| GET | `/api/providers` |
| GET | `/api/discover` |
| GET | `/api/model-endpoints` |
| POST | `/api/model-endpoints` |
| POST | `/api/model-endpoints/test` |
| GET | `/api/model-endpoints/{ep_id}/probe` |
| GET | `/api/model-endpoints/{ep_id}/models` |
| PATCH | `/api/model-endpoints/{ep_id}/models` |
| GET | `/api/default-chat` |
| PATCH | `/api/model-endpoints/{ep_id}` |
| GET | `/api/model-endpoints/{ep_id}/dependents` |
| DELETE | `/api/model-endpoints/{ep_id}` |
| GET | `/api/tools` |
| POST | `/api/tools` |

### note_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| GET | `/api/notes` |
| POST | `/api/notes` |
| GET | `/api/notes/{note_id}` |
| PUT | `/api/notes/{note_id}` |
| DELETE | `/api/notes/{note_id}` |
| POST | `/api/notes/{note_id}/pin` |
| POST | `/api/notes/{note_id}/archive` |
| POST | `/api/notes/{note_id}/items/{index}/toggle` |
| POST | `/api/notes/fire-reminder` |
| POST | `/api/notes/reorder` |

### paperclip_routes.py — Mixed (HTTP gated by global middleware; websocket self-authenticates)
| Method | Path |
|---|---|
| GET | `/api/paperclip/status` |
| POST | `/api/paperclip/events` — Self-auth: `PAPERCLIP_EVENTS_TOKEN` or loopback-only |
| GET | `/api/paperclip/stream` |
| POST | `/api/paperclip/agent-tokens` |
| GET | `/api/paperclip/agent-tokens` |
| WS | `/paperclip/{path:path}` — Self-auth: websocket handler validates the session cookie itself (bypasses `BaseHTTPMiddleware`) |

### personal_routes.py — User AND Admin (most routes require both `require_user` and `require_admin`; `/upload` is user-only)
| Method | Path |
|---|---|
| GET | `/api/personal` |
| POST | `/api/personal/reload` |
| POST | `/api/personal/add_directory` |
| DELETE | `/api/personal/remove_directory` |
| POST | `/api/personal/upload` — User only, not admin-gated |
| DELETE | `/api/personal/file` |

### prefs_routes.py — see §5.7 (User)
| Method | Path |
|---|---|
| GET | `/api/prefs` |
| GET | `/api/prefs/{key}` |
| PUT | `/api/prefs/{key}` |

### preset_routes.py — Admin (`Depends(require_admin)` on mutating routes)
| Method | Path |
|---|---|
| GET | `/api/presets` |
| POST | `/api/presets/custom` |
| GET | `/api/presets/templates` |
| POST | `/api/presets/templates` |
| DELETE | `/api/presets/templates/{template_id}` |
| POST | `/api/presets/expand` |
| GET | `/api/presets/groups` |
| POST | `/api/presets/groups` |

### research_routes.py — User (owner-scoped); `crawl4ai/crawl` and `start` require the `can_use_research` privilege
| Method | Path |
|---|---|
| GET | `/api/research/crawl4ai/status` |
| POST | `/api/research/crawl4ai/crawl` |
| GET | `/api/research/active` |
| GET | `/api/research/status/{session_id}` |
| POST | `/api/research/cancel/{session_id}` |
| POST | `/api/research/result/{session_id}` |
| GET | `/api/research/report/{session_id}` |
| POST | `/api/research/{session_id}/hide-image` |
| POST | `/api/research/{session_id}/unhide-images` |
| GET | `/api/research/library` |
| GET | `/api/research/detail/{session_id}` |
| POST | `/api/research/{session_id}/archive` |
| DELETE | `/api/research/{session_id}` |
| POST | `/api/research/start` |
| GET | `/api/research/stream/{session_id}` — SSE |
| POST | `/api/research/result-peek/{session_id}` |
| POST | `/api/research/spinoff/{session_id}` |

### search_routes.py — Open / Admin (SearXNG install is admin)
| Method | Path |
|---|---|
| GET | `/api/search/config` |
| POST | `/api/search` |
| GET | `/api/search/providers` |
| POST | `/api/search/query` |
| GET | `/api/search/searxng/status` |
| POST | `/api/search/searxng/install` |

### session_routes.py — User (owner-scoped)
| Method | Path |
|---|---|
| GET | `/api/sessions` |
| POST | `/api/session` |
| PATCH | `/api/session/{sid}` |
| POST | `/api/session/{sid}/inject_messages` |
| POST | `/api/session/{sid}/delete` |
| POST | `/api/sessions/bulk-delete` |
| DELETE | `/api/session/{sid}` |
| DELETE | `/api/sessions/all` |
| POST | `/api/session/{sid}/archive` |
| POST | `/api/session/{sid}/unarchive` |
| GET | `/api/sessions/archived` |
| GET | `/api/history/{sid}` |
| GET | `/api/session/{sid}/export` |
| POST | `/api/sessions/save` |
| POST | `/api/session/openai` |
| POST | `/api/session/{session_id}/important` |
| POST | `/api/session/{session_id}/compact` |
| POST | `/api/sessions/auto-sort` |
| GET | `/api/session/{session_id}/context_info` |

### shell_routes.py — User
| Method | Path |
|---|---|
| POST | `/api/shell/exec` |
| POST | `/api/shell/stream` — SSE |
| GET | `/api/cookbook/packages` |
| POST | `/api/cookbook/packages/install` |

### signature_routes.py — User
| Method | Path |
|---|---|
| GET | `/api/signatures` |
| POST | `/api/signatures` |
| DELETE | `/api/signatures/{sig_id}` |

### skill_pack_routes.py — Admin
| Method | Path |
|---|---|
| POST | `/api/skills/packs/preview` |
| POST | `/api/skills/packs/install` |

### skills_routes.py — User (mix; built-in skill edits admin-flavored)
| Method | Path |
|---|---|
| GET | `/api/skills` |
| GET | `/api/skills/index` |
| GET | `/api/skills/builtin` |
| GET | `/api/skills/builtin/{name}` |
| PUT | `/api/skills/builtin/{name}` |
| DELETE | `/api/skills/builtin/{name}` |
| POST | `/api/skills/add` |
| GET | `/api/skills/{skill_id}` |
| GET | `/api/skills/{skill_id}/markdown` |
| POST | `/api/skills/{skill_id}/test` |
| GET | `/api/skills/{skill_id}/test-status` |
| POST | `/api/skills/audit-all` |
| GET | `/api/skills/audit-all/status` |
| POST | `/api/skills/audit-all/cancel` |
| POST | `/api/skills/{skill_id}/markdown` |
| PUT | `/api/skills/{skill_id}` |
| DELETE | `/api/skills/{skill_id}` |
| POST | `/api/skills/search` |

### stt_routes.py — User
| Method | Path |
|---|---|
| GET | `/api/stt/stats` |
| POST | `/api/stt/transcribe` |

### system_status_routes.py — Admin
| Method | Path |
|---|---|
| GET | `/api/system/status` |
| POST | `/api/system/actions/{action_id}` |

### task_routes.py — see §5.8 (User, owner-scoped; shell-executing actions admin-only)
| Method | Path |
|---|---|
| GET | `/api/tasks` |
| GET | `/api/tasks/onboarding` |
| POST | `/api/tasks/onboarding` |
| POST | `/api/tasks` |
| POST | `/api/tasks/assign` |
| GET | `/api/tasks/notifications` |
| POST | `/api/tasks/{task_id}/clear-cache` |
| GET | `/api/tasks/{task_id}` |
| PUT | `/api/tasks/{task_id}` |
| DELETE | `/api/tasks/{task_id}` |
| POST | `/api/tasks/{task_id}/pause` |
| POST | `/api/tasks/{task_id}/resume` |
| POST | `/api/tasks/{task_id}/revert` |
| POST | `/api/tasks/{task_id}/run` |
| POST | `/api/tasks/{task_id}/stop` |
| GET | `/api/tasks/runs/recent` |
| GET | `/api/tasks/{task_id}/runs` |
| GET | `/api/tasks/meta/output-targets` |
| GET | `/api/tasks/meta/actions` |
| GET | `/api/tasks/meta/events` |
| POST | `/api/tasks/{task_id}/webhook/{token}` — Self-auth (path-embedded token; middleware-exempt regex) |
| POST | `/api/tasks/{task_id}/webhook-regenerate` |
| POST | `/api/tasks/parse` |

### tts_routes.py — User
| Method | Path |
|---|---|
| GET | `/api/tts/stats` |
| POST | `/api/tts/synthesize` |
| POST | `/api/tts/clear-cache` |

### upload_routes.py — see §5.9 (User for upload/download; Admin for cleanup/stats)
| Method | Path |
|---|---|
| POST | `/api/upload` |
| POST | `/api/upload/cleanup` |
| GET | `/api/upload/stats` |
| GET | `/api/upload/{file_id}` |
| GET | `/api/upload/{file_id}/vision` |
| PUT | `/api/upload/{file_id}/vision` |

### vault_routes.py — Admin
| Method | Path |
|---|---|
| GET | `/api/vault/config` |
| POST | `/api/vault/config` |
| POST | `/api/vault/login` |
| POST | `/api/vault/unlock` |
| POST | `/api/vault/lock` |
| POST | `/api/vault/logout` |

### webhook_routes.py — Admin (webhook CRUD); `/v1/chat` is API-token/self-auth
| Method | Path |
|---|---|
| GET | `/api/webhooks` |
| POST | `/api/webhooks` |
| POST | `/api/webhooks/{webhook_id}/test` |
| PATCH | `/api/webhooks/{webhook_id}` |
| DELETE | `/api/webhooks/{webhook_id}` |
| POST | `/api/v1/chat` |

---

## 5. Deep detail — the 10 priority routers

### 5.1 `auth_routes.py`

See §1.3 for the `require_admin` vs `_require_admin_user` split. Request/response
shapes, verbatim from the Pydantic models and handlers:

```python
# routes/auth_routes.py:36-72
class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = True
    totp_code: Optional[str] = None

class SetupRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False
```

**`POST /api/auth/login`** — request `{"username","password","remember"?,"totp_code"?}`.
If the account has TOTP enabled and no code was sent, responds
`{"ok": false, "requires_totp": true, "username": ...}` (200, not an error) so
the client can prompt for the code and resubmit. On success, sets the
`apollo_session` cookie (`httponly`, `samesite=lax`, `secure` from
`SECURE_COOKIES` env, `max_age=7 days` if `remember`) and returns
`{"ok": true, "username": ...}`. Rate-limited 15 req/60s per client IP.

**`GET /api/auth/status`** — public. Returns
`{...auth_manager.status(token), "signup_enabled": bool, "privileges"?: {...}}`
— the caller's effective privileges (admins get the full `ADMIN_PRIVILEGES`
set; regular users get their stored privileges merged with
`DEFAULT_PRIVILEGES`) so the frontend can grey out UI the user can't use.

**`GET /api/auth/users`** (strict admin) — `{"users": auth_manager.list_users()}`.

**`GET /api/auth/settings`** — a rare split-behavior endpoint:
```python
# routes/auth_routes.py:412-427
@router.get("/settings")
async def get_settings(request: Request):
    settings = _load_settings()
    try:
        _require_admin_user(request)
        return settings
    except HTTPException:
        return scrub_settings(settings)
```
Reachable without a cookie (middleware-exempt), always returns *something*,
but the *content* differs: full settings for a genuine admin (or desktop
`AUTH_ENABLED=false` mode), `scrub_settings()`-redacted (secret keys blanked)
for anyone else.

### 5.2 Chat / session / streaming — `chat_routes.py` + `session_routes.py`

See §2 for the full SSE event catalogue and the detached-run architecture.

**`POST /api/session`** (`session_routes.py:280`, `response_model=SessionResponse`)
creates a session — request typically `{"name","endpoint_url","model","rag"?,"folder"?}`
(form-encoded); response mirrors `core.database.Session.to_dict()` (§03 doc:
`id, name, model, endpoint_url, rag, archived, created_at, updated_at,
last_accessed, last_message_at, message_count, is_important, folder,
total_input_tokens, total_output_tokens, crew_member_id`).

**`GET /api/sessions`** — list, owner-scoped, supports archived/folder
filters via query params; each row is the same `to_dict()` shape.

**`DELETE /api/sessions/all`** — bulk-delete every session for the caller
(dangerous, used by the Danger Zone / `admin_wipe_routes.py`'s `chats` kind).

**`POST /api/session/{sid}/archive` / `/unarchive`** — soft-delete toggle
(`Session.archived`); archived sessions drop out of the default list but
stay in `GET /api/sessions/archived`.

### 5.3 `hub_routes.py`

All routes call `require_admin(request)` first — see the module docstring:
"these routes create model endpoints (with API keys), write files into the
local-models directories, and report on local services." Full Pydantic
bodies:

```python
# routes/hub_routes.py:40-63
class FreeEndpointBody(BaseModel):
    provider: str
    api_key: str

class GgufDownloadBody(BaseModel):
    repo_id: str
    file: str
    hf_token: Optional[str] = None

class ReferenceSourceBody(BaseModel):
    source: str

class PersonaPreviewBody(BaseModel):
    source: str
    ref: Optional[str] = ""

class PersonaInstallBody(BaseModel):
    source: str
    names: list[str]
    ref: Optional[str] = ""
```

**`POST /api/hub/free-endpoint`** — creates/refreshes a `ModelEndpoint` row
scoped to a provider's free-tier models only. Response:
`{"ok": true, "endpoint_id": str, "name": str, "free_models": int}`.
`cached_models` is pinned to the free-list snapshot so the model picker
never surfaces the provider's paid catalog through this endpoint.

**`GET /api/hub/security-scan`** — reads every `McpServer` row, decodes its
`args`/`env` JSON, and runs `scan_mcp_servers()` + `scan_skills()` (if a
skills manager is wired in) for risky patterns (secret-shaped env var
*names* only — "Never returns secret values"). Response:
`{"findings": [...], "summary": {...}}`.

**`/api/hub/reference/*`** (Reference Library — "a fourth store alongside
memory/skills/documents," per the `reference_entries` table in doc 03):
- `GET /sources` → `{"sources": [...]}` (installed catalog sources + status)
- `POST /install` (body `{"source": str}`) → installs/reinstalls one catalog
- `POST /remove` (body `{"source": str}`) → uninstalls
- `GET /search?q=&source=&kind=&limit=20` →
  `{"query": str, "count": int, "results": [...]}` — same search surface the
  agent's `reference_search` tool uses.

### 5.4 `localmodels_routes.py`

Module docstring: "All routes require admin: they enumerate the filesystem,
change a global scan-directory setting, and launch/kill OS processes —
strictly more privileged than the already admin-gated model-endpoint
routes." Every handler calls `require_admin(request)` as its first line.

```python
# routes/localmodels_routes.py:31-37
class DirsBody(BaseModel):
    dirs: list[str]

class BinaryBody(BaseModel):
    path: str
```

**`GET /api/local-models`** → `{"dirs": [...], "models": [{...asdict(m), "running": bool}, ...]}`
— filesystem scan of the configured GGUF directories, cross-referenced
against `get_server().status()` for which are currently loaded.

**`POST /api/local-models/{model_id}/start`** → `{"ok": true, "base_url": str}`
on success (launches a `llama-server` child process bound to a free port),
or `400 {"ok": false, "error": "Model could not be started"}`.

**`GET /api/local-models/binary`** → `{"path": <configured>, "resolved": <what actually resolves right now>}`.

### 5.5 `memory_routes.py`

Owner resolution uses `effective_user(request)` throughout, not
`get_current_user` — an explicit fix noted in-code because an API-token
request's literal principal is `"api"`, which would otherwise bucket every
token's memories together (cross-tenant leak, `routes/memory_routes.py:43-48`).
Ownership checks are **strict**: `memory.get("owner") != user` → `404` (not
`403`, to avoid confirming the memory's existence), a documented fix for a
previous bug where empty/null owners were treated as visible to everyone.

**IMPORTANT — storage layer:** despite the `Memory` SQLAlchemy table
existing (doc 03 §2), this router's `memory_manager` (`services/memory/memory.py`,
`MemoryManager`) reads/writes `<data_root>/memory.json` directly
(`self.memory_file = os.path.join(data_dir, "memory.json")`) — **not** the
SQL table. See doc 03's uncertainty note; the `memories` table appears to be
legacy/unused by this router.

```python
# request_models.py — MemoryAddRequest (imported by memory_routes.py)
```

**`POST /api/memory/add`** — requires `can_manage_memory` privilege
(`require_privilege`). Accepts either a `MemoryAddRequest` JSON body or a
form fallback: `{text, category="fact", source="user", session_id?}`.
Deduplicates via `memory_manager.find_duplicates` before inserting; on
success also calls `memory_vector.add(id, text)` (ChromaDB `apollo_memories`
collection, doc 03 §5) and fires the `memory_added` event. Response:
`{"ok": true, "count": <owner's total memory count>}`.

**`GET /api/memory/export-pack`** / **`POST /api/memory/import-pack`** — the
cross-install sync mechanism ("the sync path between two brains"). Export
shape:
```json
{
  "apollo_memory_pack": 1,
  "exported_at": 1712345678,
  "count": 42,
  "memories": [
    {"text": "...", "category": "fact", "pinned": false, "timestamp": 1712340000, "source": "user"}
  ]
}
```
Import skips exact-duplicate texts, tags new rows `source="import"`,
`provenance={"kind": "import-pack"}`.

**`GET /api/memory/graph`** — owner-scoped knowledge graph;
`build_graph(mems, neighbor_fn, threshold=0.6, max_neighbors=4, max_nodes=300)`
where `neighbor_fn` queries the ChromaDB vector store (degrades to
session-only edges if the vector store is unhealthy).

**`POST /api/memory/{memory_id}/pin`**, **`GET/PUT/DELETE /api/memory/{memory_id}`**
— note in the source: *"Wildcard routes MUST come last — otherwise they
swallow /import, /search, etc."* (FastAPI matches path routes in declaration
order, and `/{memory_id}` is a catch-all relative to the router prefix).

### 5.6 `model_routes.py`

**`GET /api/models`** — per-user cached (30s TTL) model list. Owner resolved
via `get_current_user`; unauthenticated callers get `401` only when auth is
configured and enabled (desktop/unconfigured mode = "see everything," per
comment at `model_routes.py:854-856`). Admins see every `ModelEndpoint`
regardless of `owner`; regular users see their own + null-owner
(legacy/shared) rows. Response is a list of per-endpoint items:
```json
[
  {
    "host": "custom", "port": 0,
    "url": "http://localhost:8002/v1/chat/completions",
    "models": ["llama-3-8b-instruct"],
    "models_display": ["llama-3-8b-instruct"],
    "models_extra": [], "models_extra_display": [],
    "endpoint_id": "a1b2c3d4", "endpoint_name": "Local vLLM",
    "category": "local", "model_type": "llm",
    "model_meta": {"llama-3-8b-instruct": {"kind": "chat", "arch": "llama"}}
  }
]
```
An unreachable endpoint is still listed with `"offline": true` and empty
model arrays rather than omitted, so the picker can show it greyed out.

**`POST /api/model-endpoints`** (admin) — form-encoded:
`name, base_url (required), api_key, skip_probe, require_models, model_type="llm",
supports_tools, container_local, shared="true"`. Dedupes by `base_url` (scoped
to the caller's own + shared rows) before creating — returns the existing row
with `"existing": true` rather than a duplicate. `shared="false"` stamps the
creating admin as `owner` so the endpoint is private to them; default is
shared (`owner=NULL`, visible to everyone, matching the app's historical
behavior). Response on create: the new `ModelEndpoint` fields plus probed
`models`.

**`GET /api/model-endpoints`** (admin) — per-row status derived from
`cached_models`/`hidden_models` plus a live ping when the cache is empty:
`{"id","name","base_url","has_key": bool,"is_enabled","models": [...],
"hidden_count": int, "online": bool, "status": "online"|"offline"|"empty",
"ping_error"?, "model_type","supports_tools"}`.

### 5.7 `prefs_routes.py`

The entire router is 76 lines — a per-user JSON key/value store, no
Pydantic models, request bodies are raw `dict`. Storage:
`<data_root>/user_prefs.json`, shape `{"_users": {"<username>": {...prefs}}}`
(new format) or a flat `{...prefs}` dict (legacy single-user format, used
directly when `AUTH_ENABLED=false` / `user is None`).

```python
# routes/prefs_routes.py:56-73
@router.get("")
async def get_all_prefs(request): return _load_for_user(get_current_user(request))

@router.get("/{key}")
async def get_pref(request, key): return {"key": key, "value": prefs.get(key)}

@router.put("/{key}")
async def set_pref(request, key, body: dict):
    prefs[key] = body.get("value")
    _save_for_user(user, prefs)
    return {"key": key, "value": prefs[key]}
```

`PUT /api/prefs/{key}` request: `{"value": <anything JSON-serializable>}`.
This module also exports `_load_for_user`/`_save_for_user` as an internal
API — `task_routes.py` and `auth_routes.py`'s rename flow both import and
call them directly rather than going through HTTP.

### 5.8 `task_routes.py`

Full request models:

```python
# routes/task_routes.py:23-67
class TaskCreate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    task_type: str = "llm"                        # "llm" | "action" | "research"
    action: Optional[str] = None
    schedule: Optional[str] = None                # "once" | "daily" | "weekly" | "monthly" | "cron"
    scheduled_time: str = "09:00"
    scheduled_day: Optional[int] = None
    scheduled_date: Optional[str] = None
    cron_expression: Optional[str] = None
    trigger_type: str = "schedule"                # "schedule" | "event" | "webhook"
    trigger_event: Optional[str] = None
    trigger_count: Optional[int] = None
    output_target: str = "session"
    model: Optional[str] = None
    endpoint_url: Optional[str] = None
    then_task_id: Optional[str] = None
    notifications_enabled: Optional[bool] = None

class AssignBody(BaseModel):
    prompt: str
    name: Optional[str] = None
    model: Optional[str] = None
    endpoint_url: Optional[str] = None
```
`TaskUpdate` mirrors `TaskCreate` with every field `Optional` (PATCH-style
partial update via `PUT`).

Admin gate for a **subset** of action types:
```python
# routes/task_routes.py:311-333
_ADMIN_ONLY_ACTIONS = {"run_local", "run_script", "ssh_command"}
```
`POST /api/tasks` and `PUT /api/tasks/{task_id}` both reject
(`403 "Action '{action}' requires admin privileges"`) a non-admin creating or
retargeting a task onto one of these shell-executing built-in actions.

**`POST /api/tasks/{task_id}/webhook/{token}`** — the one genuinely
unauthenticated endpoint in this router (also globally exempted from the
session middleware, §1.2): *"Unauthenticated endpoint — the token IS the
auth."* Validates `ScheduledTask.webhook_token == token` and
`status == "active"`, then triggers `task_scheduler.run_task_now(task_id)`.

**`POST /api/tasks/parse`** — natural-language → task draft via LLM. Sends a
strict-JSON-schema system prompt (task_type/name/prompt/schedule/scheduled_time/
scheduled_day/scheduled_date/cron_expression/output_target) to the resolved
`"utility"` (falling back to `"default"`) model endpoint, whitelists/validates
the parsed fields, and returns `{"success": true, "draft": {...}}` — a draft
only, never auto-saved, so a misparsed schedule needs explicit user review.

**`GET /api/tasks/runs/recent`** — cross-task run history driving the
Activity view; de-dupes near-identical `check_email_urgency` scanner rows
(same minute + status + result text) that would otherwise flood the feed
when auth is bypassed and legacy multi-owner rows are all visible together.

### 5.9 `upload_routes.py`

**`POST /api/upload`** (`files: List[UploadFile]`) — per-IP concurrent-upload
rate limiting via `count_recent_uploads` (counts genuine upload *events*, not
files-in-this-batch — a documented fix for issue #1346 where a single
multi-file attach falsely tripped the limit). Response:
`{"files": [{"id","name","mime","size","hash","uploaded_at","width"?,"height"?,"is_duplicate": bool}, ...]}`.
Per-file failures are swallowed and skipped (`continue`); only if *every*
file fails does the route 500.

**`GET /api/upload/{file_id}?thumb=1`** — serves the raw file, or (with
`thumb=1` on an image) a cached 320×320 JPEG thumbnail generated once via
PIL (EXIF-rotation baked into pixels before caching, since PIL strips EXIF
on save) and reused on subsequent requests unless the source file is newer.
Owner-checked against `uploads.json` when auth is configured — a mismatched
owner returns `404` (not `403`) to avoid confirming the file exists.

**`GET /api/upload/{file_id}/vision`** — vision-model OCR/description for an
image attachment, cached at `<UPLOAD_DIR>/.vision/{file_id}.txt`; `force=1`
recomputes. **`PUT`** on the same path lets the user hand-edit the cached
OCR text (used as an override on the next chat send).

---

## 6. UNCERTAIN — flagged gaps in this pass

Every router file was independently read handler-by-handler (not just
router-level imports) to confirm the per-endpoint auth calls in §1.4 and §4.
A handful of endpoints could not be fully confirmed within that pass and are
flagged here rather than guessed — re-check these specifically before
treating their auth requirement as load-bearing:

- `calendar_routes.py::test_connection` (`POST /api/calendar/test`) and
  `::quick_parse` (`POST /api/calendar/quick-parse`).
- `memory_routes.py::api_add_memory` (uses `require_privilege(..., "can_manage_memory")`
  per the handler body in §5.5 — confirmed there, but not cross-checked
  against every other memory-mutating route in the file).
- `note_routes.py::fire_reminder` (`POST /api/notes/fire-reminder`).
- `session_routes.py::bulk_delete_sessions` (`POST /api/sessions/bulk-delete`).
- `upload_routes.py::put_vision_text` — the handler checks
  `if auth_configured:` further down (visible in the full read, §5.9) rather
  than calling a `require_*` helper at the top, so it's owner-scoped only
  when auth is configured — effectively open in desktop/unconfigured mode,
  same caveat as its GET sibling (§1.4).
- `gallery_routes.py::remove_background` (`POST /api/image/remove-bg`) — every
  sibling `/api/image/*` route requires `can_generate_images` except the
  confirmed-open `sharpen` (§1.4); `remove_background`'s gate was not
  independently re-confirmed in this pass.
