# Apollo — Backend API Specifications

Apollo's HTTP API is a FastAPI app assembled in `app.py` ("slim orchestrator"). Routers are built by per-domain `setup_*_routes()` factories in `routes/` and registered through `build_and_include_router` / `register_router_specs` (`services/app_startup.py`). This document inventories every registered router and fully specifies the most important endpoints, including exact SSE wire formats.

## 1. Router Inventory (registration order in app.py)

| # | Name (app.py label) | Factory | Prefix / key paths |
|---|---|---|---|
| 1 | Auth | `routes/auth_routes.py:setup_auth_routes(auth_manager)` | `/api/auth/*` |
| 2 | Uploads | `routes/upload_routes.py:setup_upload_routes(upload_handler)` | `/api/upload*` |
| 3 | Emoji | `routes/emoji_routes.py` | Twemoji SVG proxy |
| 4 | Sessions | `routes/session_routes.py:setup_session_routes(session_manager, session_config, webhook_manager=...)` | `/api/session*`, `/api/sessions*`, `/api/history/{sid}` |
| 5 | Admin wipe | `routes/admin_wipe_routes.py` | admin data wipe |
| 6 | Memory | `routes/memory_routes.py:setup_memory_routes(memory_manager, session_manager, memory_vector=...)` | `/api/memory/*` |
| 7 | Skills | `routes/skills_routes.py:setup_skills_routes(skills_manager)` | skills CRUD + audit |
| 8 | Chat | `routes/chat_routes.py:setup_chat_routes(session_manager, chat_handler, chat_processor, memory_manager, research_handler, upload_handler, memory_vector=, webhook_manager=, skills_manager=)` | `/api/chat`, `/api/chat_stream`, `/api/chat/{resume,stop,stream_status}`, `/api/search`, `/api/rewrite`, `/api/inject_context/{sid}` |
| 9 | Research | `routes/research_routes.py` | `/api/research/*` |
| 10 | History | `routes/history_routes.py` | history utilities |
| 11 | Search | `routes/search_routes.py` | web-search config |
| 12 | Presets | `routes/preset_routes.py` | preset CRUD |
| 13 | Diagnostics | `routes/diagnostics_routes.py(rag_manager, rag_available, research_handler)` | diagnostics |
| 14 | Cleanup | `routes/cleanup_routes.py` | session cleanup |
| 15 | Personal docs | `routes/personal_routes.py(personal_docs_mgr, rag_manager, rag_available)` | `/api/personal/*` (ChromaDB RAG) |
| 16 | Embedding | `routes/embedding_routes.py` | embeddings |
| 17 | Models | `routes/model_routes.py:setup_model_routes(model_discovery)` | `/api/models`, `/api/model-endpoints*`, `/api/probe*`, `/api/ping`, `/api/providers`, `/api/discover`, `/api/default-chat`, `/api/tools` |
| 18 | TTS / 19 STT | `routes/tts_routes.py`, `routes/stt_routes.py` | speech |
| 20 | Documents | `routes/document_routes.py(session_manager, upload_handler)` | artifacts/canvas |
| 21 | Signatures | `routes/signature_routes.py` | signature stamps |
| 22 | Gallery | `routes/gallery_routes.py` | image library |
| 23 | Editor drafts | `routes/editor_draft_routes.py` | editor projects |
| 24 | Tasks | `routes/task_routes.py:setup_task_routes(task_scheduler)` | `/api/tasks/*` |
| 25 | Assistants | `routes/assistant_routes.py(task_scheduler)` | personal assistant |
| 26 | Calendar | `routes/calendar_routes.py` | CalDAV-style calendar |
| 27 | Shell | `routes/shell_routes.py` | `/api/shell/stream` (SSE) |
| 28 | Cookbook | `routes/cookbook_routes.py` | model download/serve |
| 29 | Hardware fit | `routes/hwfit_routes.py` | "What Fits?" |
| 30 | Local models | `routes/localmodels_routes.py` | `/api/local-models/*` |
| 31 | Compare | `routes/compare_routes.py(session_manager)` | A/B comparisons |
| 32 | Preferences | `routes/prefs_routes.py` | per-user prefs JSON |
| 33 | Backup | `routes/backup_routes.py(memory_manager, preset_manager, skills_manager)` | export/import |
| 34 | Fonts | `routes/font_routes.py` | fonts |
| 35 | MCP | `routes/mcp_routes.py(mcp_manager)` | MCP server CRUD |
| 36 | Webhooks | `routes/webhook_routes.py(webhook_manager, auth_manager, session_manager, api_key_manager)` | outgoing webhooks + `/api/v1/chat` token API |
| 37 | API tokens | `routes/api_token_routes.py` | `/api/tokens*` |
| 38 | Notes | `routes/note_routes.py(task_scheduler)` | `/api/notes/*` |
| 39 | Email | `routes/email_routes.py` | IMAP/SMTP |
| 40 | Vault | `routes/vault_routes.py` | secure vault |
| 41 | Contacts | `routes/contacts_routes.py` | contacts |
| 42 | Companion | `companion/:setup_companion_routes` | mobile companion |
| 43 | Sidecar proxy | `routes/paperclip_routes.py:setup_paperclip_routes(cfg, ws_validate=, hub=, collector_status=, agent_tokens=)` | `/api/paperclip/*`, `/paperclip/{path}` HTTP+WS |
| 44 | Integration status | `routes/integration_routes.py(_paperclip_status_for_integrations)` | integration health |
| 45 | System status | `routes/system_status_routes.py(memory_manager=..., mcp_manager=..., task_scheduler=..., auth_manager=..., rag_manager=..., ...)` | status dashboard |
| 46 | Browser | `routes/browser_routes.py` | embedded browser tool |
| 47 | Local model proxy | `routes/lmproxy_routes.py:setup_lmproxy_routes(token_provider=, warm_url_provider=, agent_lookup=, publish_activity=)` | `/lmproxy/v1/*` |

Routes kept directly in `app.py`: SPA pages `/`, `/notes`, `/calendar`, `/cookbook`, `/email`, `/memory`, `/gallery`, `/tasks`, `/library`, `/backgrounds`, `/login` (all serve `static/index.html` with a CSP nonce injected via `{{CSP_NONCE}}`); plus `GET /api/version` → `{"version": APP_VERSION}`, `GET /api/health` → `{"status": "healthy", "timestamp": ...}`, `GET /api/ready` (503 unless `src/readiness.check_readiness()` passes), `GET /api/runtime` → `{"in_docker": bool, "ollama_base_url": str}`, and `GET /api/generated-image/{filename}` (regex-validated content-hash filename, gallery-ownership check, `Cache-Control: public, max-age=31536000, immutable`).

A global `_RequestTimeoutMiddleware` aborts non-exempt requests after `REQUEST_HARD_TIMEOUT` (env, default 45 s) with `504 {"detail": "Request exceeded 45s timeout"}`. Exempt prefixes: `/api/chat`, `/api/shell/stream`, `/api/research`, `/api/model/download`, `/api/model/probe`, `/api/model-endpoints`, `/api/cookbook/setup`, `/api/upload`, `/api/image`.

## 2. Session Routes (`routes/session_routes.py`, prefix `/api`)

All mutating routes call `_verify_session_owner(request, sid)`: 403 if unauthenticated (unless `AUTH_ENABLED=false` → single-user, everything allowed), 404 if the DB row's `owner` differs from `effective_user(request)` (404, not 403, to avoid existence leaks). Non-admins may not pass raw `endpoint_url`s — `_reject_raw_endpoint_url_for_non_admin` 403s with `"Choose a registered model endpoint"` unless `endpoint_id` is supplied.

| Method/Path | Purpose |
|---|---|
| `GET /api/sessions` | List active sessions for caller (purges stale incognito "Nobody"/"Incognito" rows older than 10 min). Items: `{id, name, model, endpoint_url, rag, archived, folder, total_tokens, is_important, created_at, updated_at, last_message_at, has_documents, has_images, mode, message_count}` |
| `POST /api/session` | Create (form fields below) → `SessionResponse {id, name, model, rag, archived}` |
| `PATCH /api/session/{sid}` | Rename / move folder / switch model+endpoint |
| `DELETE /api/session/{sid}` | Delete (403 `SESSION_STARRED` if `is_important`) |
| `POST /api/session/{sid}/delete` | Beacon-friendly delete (same handler) |
| `POST /api/sessions/bulk-delete` | Body `{"ids": [...]}` → `{"deleted": n}` |
| `DELETE /api/sessions/all` | **Admin only** (`require_admin`) wipe all sessions+messages |
| `POST /api/session/{sid}/archive` / `unarchive` | Toggle `archived` |
| `GET /api/sessions/archived?search&offset&limit&sort&model` | Paged archive browser → `{sessions, total}` |
| `GET /api/history/{sid}` | `{"history": [msg.to_dict()...]}` |
| `GET /api/session/{sid}/export?fmt=md\|txt\|json\|html&filename=` | Download conversation |
| `POST /api/session/{sid}/inject_messages` | Bulk insert messages (group-chat sync) |
| `POST /api/session/{sid}/important`, `POST /api/session/{sid}/compact`, `POST /api/sessions/auto-sort`, `GET /api/session/{sid}/context_info`, `POST /api/sessions/save`, `POST /api/session/openai` | star, history compaction, LLM folder auto-sort, context stats, persist, OpenAI-key session |

`POST /api/session` form parameters (not JSON): `name=""`, `endpoint_url=""`, `model=""`, `rag=None`, `skip_validation=None`, `api_key=""`, `endpoint_id=""`.

**endpoint_id resolution** (used identically in create and model switching):

```python
# routes/session_routes.py (create_session / rename_session)
q = _db.query(ModelEndpoint).filter(
    ModelEndpoint.id == endpoint_id.strip(),
    ModelEndpoint.is_enabled == True,
)
if user:
    q = owner_filter(q, ModelEndpoint, user)   # owner == user OR owner IS NULL
endpoint_row = q.first()
if not endpoint_row:
    raise HTTPException(400, "Model endpoint no longer exists")
endpoint_base_url = endpoint_row.base_url or ""
endpoint_api_key  = endpoint_row.api_key or ""        # decrypted by EncryptedText
endpoint_url = build_chat_url(normalize_base(endpoint_base_url))
```

Unless `skip_validation=true`, the model is validated against the endpoint's live `/v1/models`; with no `model` given, the first **chat** model is chosen (skips ids containing `text-embedding`, `embedding`, `tts-`, `whisper`, `text-moderation`, `moderation-`, `dall-e`, `rerank`). On success a `session.created` webhook and a `session_created` event-bus event fire. `PATCH /api/session/{sid}` switches model when `model` is sent together with `endpoint_url` **or** `endpoint_id` (endpoint_id alone is sufficient); the resolved API key is rebuilt into `session.headers` via `build_headers()` and persisted to the DB row.

## 3. Chat Routes (`routes/chat_routes.py`, no prefix)

### 3.1 `POST /api/chat` (non-streaming)

Body: `src/request_models.py:ChatRequest` —

```python
class ChatRequest(BaseModel):
    message: str            # 1..50000 chars, stripped
    session: str            # session ID (required)
    attachments: Optional[List[str]] = []
    use_web: Optional[bool] = False
    use_research: Optional[bool] = False
    time_filter: Optional[str] = None   # day|week|month|year else coerced to None
    preset_id: Optional[str] = None
```

Flow: `_verify_session_owner` → orphaned-endpoint check (400 `"Selected model endpoint was removed..."`) → empty-model recovery (400 `"No model selected for this chat..."`) → `_enforce_chat_privileges` (allowed_models allowlist + `max_messages_per_day` cap → 429) → memory-command shortcut → `build_chat_context` → optional research injection → `llm_call_async`. Response: `{"response": "<assistant reply>"}`.

### 3.2 `POST /api/chat_stream` (SSE)

Accepts **form data** (with optional JSON body for attachments): `message`, `session`, `attachments` (JSON array string), `use_web`, `use_research`, `time_filter`, `preset_id`, `allow_bash`, `allow_web_search`, `use_rag`, `search_context`, `compare_mode`, `incognito`, `mode` (`chat`|`agent`), `no_memory`, `active_doc_id`. Honors the `X-TZ-Offset` header (minutes east of UTC) for natural-language time tools. Chat→agent auto-escalation occurs when a chat message matches a notes/calendar/reminder intent pattern (heavy tools `bash/python/read_file/write_file/builtin_browser` stay disabled in that case). Tool gating merges frontend toggles, incognito denials (`manage_memory`, `search_chats`, `manage_skills`), per-user privileges (`can_use_bash`, `can_use_browser`, `can_use_documents`, `can_generate_images`, `can_manage_memory`, `can_use_research`, `can_use_agent`) and the admin global `disabled_tools` setting.

The stream runs as a **detached background task** (`agent_runs.start(session, _safe_stream())`); the HTTP response merely subscribes. Closing the tab does not stop the run.

**SSE wire format** — every event is a `data: <compact JSON>\n\n` line; comment keepalives are `: heartbeat\n\n` (or `: heartbeat N\n\n` during research polling); upstream errors arrive as a named event; the stream always terminates with `data: [DONE]\n\n`:

```text
data: {"type": "model_info", "model": "qwen2.5-32b", "suffix": "Research"?, "character_name": "..."?}
data: {"type": "attachments", "data": [...]}            # attachment metadata
data: {"type": "doc_update", ...}                       # auto-opened/edited document
data: {"type": "rag_sources", "data": [...]}
data: {"type": "web_sources", "data": [...]}
data: {"type": "memories_used", "data": [...]}
data: {"type": "compacted", "context_length": 32768}    # history auto-compaction notice
data: {"delta": "tok"}                                  # content token
data: {"delta": "tok", "thinking": true}                # reasoning token (NOT saved)
data: {"type": "tool_start", "tool": "bash", "command": "..."}
data: {"type": "tool_output", "tool": "bash", "command": "...", "output": "...", "exit_code": 0}
data: {"type": "agent_step", "round": 2}
data: {"type": "doc_stream_open" | "doc_stream_delta" | "doc_suggestions" | "ui_control", ...}
data: {"type": "fallback", "answered_by": "model-id"}   # primary model failed, fallback answered
data: {"type": "metrics", "data": {"response_time": 4.2, "input_tokens": 812, "output_tokens": 240,
       "tokens_per_second": 57.1, "context_percent": 2.5, "context_length": 32768,
       "model": "qwen2.5-32b", "usage_source": "estimated"?, "tps_source": "backend"?}}
data: {"type": "message_saved", "id": "<chat_messages.id>"}
data: {"type": "research_progress", "data": {...,"started_at":...,"avg_duration":...}}
data: {"type": "research_sources", "data": [...]}
data: {"type": "research_findings", "data": {...}}
data: {"type": "research_done", "data": {"session_id": "<sid>"}}
event: error
data: {"error": "Cannot reach host", "status": 503}     # from src/llm_core.py; also
data: {"status": 401, "text": "friendly msg", "raw": "<=500 chars upstream body"}
data: [DONE]
```

### 3.3 Stop / resume / status

| Method/Path | Behavior |
|---|---|
| `GET /api/chat/resume/{session_id}` | Re-subscribe to a detached run (replay buffer + live). 404 `"No active run for this session"` if none. |
| `POST /api/chat/stop/{session_id}` | Cancel the detached run → `{"stopped": true|false}`. Closing the SSE does NOT stop it. |
| `GET /api/chat/stream_status/{session_id}` | `{"status": "streaming", "partial": "...", "query": "...", "is_research": bool, "mode": "agent"}` or `{"status": "streaming", "detached": true}`; 404 if idle. |
| `POST /api/inject_context/{session_id}` | Form `context=` → appends an untrusted-context message → `{"status": "context_injected"}` |
| `GET /api/search?q=&limit=20` | Owner-scoped ILIKE search over messages → `[{session_id, session_name, role, content_snippet, timestamp}]` |
| `POST /api/rewrite` | JSON `{session_id, original_text, instruction}` → SSE rewrite stream (no tools), replaces last assistant message in memory + DB |

## 4. Model Routes (`routes/model_routes.py`, prefix `/api`)

Admin-gated (`require_admin`) except `GET /api/models` (auth-scoped per user).

| Method/Path | Spec |
|---|---|
| `GET /api/models?refresh=` | Per-user model picker. 401 for anonymous in configured deployments. 30 s per-`(owner, is_admin)` cache. Returns `{"hosts": [], "items": [{host:"custom", port:0, url:<chat_url>, models, models_display, models_extra, models_extra_display, endpoint_id, endpoint_name, category, model_type, offline?}]}` built from `cached_models` minus `hidden_models`. |
| `GET /api/model-endpoints` | Admin list: `[{id, name, base_url, has_key, is_enabled, models, hidden_count, online, status: online|empty|offline, ping_error, model_type, supports_tools}]` |
| `POST /api/model-endpoints` | Form: `name=""`, `base_url` (required), `api_key=""`, `skip_probe="false"`, `require_models="false"`, `model_type="llm"`, `supports_tools=""`, `container_local="false"`, `shared="true"` (`false` = owner-scoped to creator). Dedupes on `base_url` (returns `{"existing": true}` row). Probes `/v1/models` (1 s timeout, 3 s for Ollama), seeds `default_endpoint_id`/`default_model` settings if unset. Returns `{id, name, base_url, models, online, status, ping_error}` — id is `uuid4()[:8]`. |
| `POST /api/model-endpoints/test` | Form `base_url`, `api_key` — connectivity test without saving. |
| `GET /api/model-endpoints/probe-local` | Parallel 1.5–2.5 s probe of *local* endpoints only (8 s cache) → `{ep_id: {alive, latency_ms, status_code, error}}` |
| `GET /api/probe?endpoint_id=` | **SSE**: per-model tiny-completion probes (8 s timeout each). Updates `cached_models`. Wire format below. |
| `POST /api/probe-selected` | JSON `{"models": [{endpoint_id, model, with_tools?}]}` → `{"results": [{model, endpoint_id, status: ok|fail, error?}]}` |
| `GET /api/model-endpoints/{ep_id}/probe`, `GET/PATCH .../models`, `PATCH /api/model-endpoints/{ep_id}`, `GET .../dependents`, `DELETE /api/model-endpoints/{ep_id}` | Per-endpoint probe, model visibility, edit, dependent-session listing, delete |
| `GET /api/ping` | Probe all enabled endpoints → `{"endpoints": [{id, name, base_url, provider, category, status, latency_ms, model_count, error?}]}` (`local://` rows reported as `status:"local"`, never HTTP-probed) |
| `GET /api/providers`, `GET /api/discover`, `GET|POST /api/tools` | provider catalog (30 s cache, runs a host port-scan), LAN scan for model servers, tool listing/config |
| `GET /api/default-chat` | Resolves the caller's default endpoint+model. Admins (and unauthenticated single-user mode) read global `settings.json` `default_endpoint_id`/`default_model`/`default_model_fallbacks`; regular users read **per-user prefs only** with no global fallback (prevents leaking the admin's pick into new accounts). |

`GET /api/probe` SSE wire format (`data:` JSON lines, `text/event-stream`):

```text
data: {"type": "probe_start", "endpoint": "Local vLLM", "model_count": 4, "skipped": 2}
data: {"type": "probe_start", "endpoint": "Dead box", "model_count": 0, "error": "No models found or endpoint offline"}
data: {"type": "probe_result", "endpoint": "Local vLLM", "model": "qwen2.5-7b-instruct", "status": "ok", ...}
data: {"type": "probe_done", "total": 4, "ok": 3}
```

## 5. Local Models (`routes/localmodels_routes.py`, prefix `/api/local-models`)

All endpoints `require_admin` (they enumerate the filesystem and launch/kill OS processes).

| Method/Path | Returns |
|---|---|
| `GET /api/local-models` | `{"dirs": [...], "models": [{...ScannedModel fields..., "running": bool}]}` |
| `POST /api/local-models/scan` | rescan → `{"count": N, "models": [...]}` |
| `GET /api/local-models/voices` | Piper TTS voices (`*.onnx` + sidecar) → `{"voices": [...]}` |
| `GET /api/local-models/dirs` / `PUT` (body `{"dirs": ["..."]}`) | get/set scan directories (PUT triggers rescan) |
| `POST /api/local-models/{model_id}/start` | `get_server().ensure_running(model_id)` → `{"ok": true, "base_url": "http://..."}` or 400 `{"ok": false, "error": ...}` |
| `POST /api/local-models/{model_id}/stop` | `{"ok": true}` or `{"ok": false, "error": "not running"}` |

## 6. Paperclip Routes (`routes/paperclip_routes.py`)

### 6.1 `GET /api/paperclip/status`
Server-side reachability ping of the sidecar (`GET {cfg.url}/api/health`, 2 s). Returns `{enabled, mode, url, browser_url, model_endpoint, reachable, browser_use, collector, agent_workbench}`.

### 6.2 `POST /api/paperclip/events` — activity ingest (self-authenticating)
Exempted from session auth in `app.py` (`AUTH_EXEMPT_EXACT`). Auth inside the handler:

```python
# routes/paperclip_routes.py
if events_token:  # PAPERCLIP_EVENTS_TOKEN env
    provided = request.headers.get("x-paperclip-events-token", "")
    if not hmac.compare_digest(provided, events_token):
        return JSONResponse({"detail": "invalid events token"}, status_code=401)
else:
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for"):
        return JSONResponse({"detail": "remote ingest requires PAPERCLIP_EVENTS_TOKEN"}, status_code=401)
```

Body: `{"events": [{"type": <FLOOR_EVENT_TYPE>, "payload": {...}}, ...]}` (a single `{"type": ..., "payload": ...}` object is also accepted). Max batch 100 (else 413). Valid types (`services/paperclip/events.py:FLOOR_EVENT_TYPES`): `agent.status`, `heartbeat.run.queued`, `heartbeat.run.status`, `heartbeat.run.log`, `heartbeat.run.event`, `activity.logged`. Response: `{"accepted": N, "rejected": M}`.

### 6.3 `GET /api/paperclip/stream` — Floor SSE
`data: <compact JSON>\n\n` frames; `: keepalive\n\n` comment every 25 s of inactivity.

- Sidecar disabled AND empty buffer → emits one event and closes: `data: {"type":"paperclip.stream.unavailable","payload":{"reason":"disabled"}}`
- Enabled but idle → `data: {"type":"paperclip.stream.waiting","payload":{"reason":"no_events_yet"}}`, then holds the connection.
- Otherwise replays the recent hub buffer (subscribe-before-snapshot with a `seq` watermark to dedupe), then streams live `{"type": ..., "payload": ..., "received_at": <epoch>}` events.

### 6.4 Agent tokens (admin)
- `POST /api/paperclip/agent-tokens` (`require_admin`): body `{"agent_id": "...", "name": "..."?}` → `{"agent_id", "name", "token": "pa-<48 hex>"}`. Minting again **rotates** the agent's old token out. 503 if registry not configured.
- `GET /api/paperclip/agent-tokens` (`require_admin`): `{"tokens": [{"agent_id", "name", "token_suffix"}]}` — never returns full secrets.

### 6.5 Reverse proxy
- `/paperclip/{path:path}` for `GET POST PUT PATCH DELETE OPTIONS HEAD`: gated by global AuthMiddleware; 503 `{"detail": "Paperclip is disabled"}` when off; streams upstream via httpx with filtered request/response headers; 502 on connect/request errors.
- `WEBSOCKET /paperclip/{path:path}`: BaseHTTPMiddleware does not see websockets, so the handler reads the `apollo_session` cookie itself and calls `ws_validate(token)` (→ `auth_manager.validate_token`); closes with code 1008 if disabled or invalid; otherwise bridges both directions to `cfg.url` with `http`→`ws` scheme swap.

## 7. Local-Model Proxy (`routes/lmproxy_routes.py`, prefix `/lmproxy`)

Auth-exempt from session middleware (`AUTH_EXEMPT_PREFIXES = ["/static", "/lmproxy"]`), guarded by bearer tokens instead. Endpoints: `GET /lmproxy/v1/models` plus catch-all `GET|POST /lmproxy/v1/{path:path}` (OpenAI-compatible passthrough).

- `_resolve_actor` accepts (a) the shared `PAPERCLIP_PROXY_TOKEN` (constant-time compare) → anonymous actor, or (b) a per-agent `pa-` token via `AgentTokenRegistry.lookup` → `{"agent_id", "name"}`. No/invalid token → `401 {"error": "unauthorized"}`.
- Per-agent actors emit a `heartbeat.run.event` activity pulse onto the Floor hub at most once per `pulse_interval=10.0` s: payload `{"agentId": id, "name": name, "tool": "llm"}`.
- Forwards to the currently warm llama-server (`_warm_chat_base_url()` in app.py: first running slot with `kind=="chat"`), stripping the inbound `Authorization` header. 503 `{"error": "no local model is currently running; serve one in Apollo (Settings → AI / model picker) first"}` when nothing is warm; 502 on upstream failures.

## 8. Highlights: Notes / Tasks / Memory / Auth / API Tokens

- **Notes** (`/api/notes`): `GET ""` list, `POST ""` create, `GET|PUT|DELETE /{note_id}`, `POST /{note_id}/pin`, `/{note_id}/archive`, `/{note_id}/items/{index}/toggle`, `POST /fire-reminder`, `POST /reorder`. Notes carry the AI-classification fields documented in the schema doc.
- **Tasks** (`/api/tasks`): CRUD (`GET ""`, `POST ""`, `GET|PUT|DELETE /{task_id}`), lifecycle (`/pause`, `/resume`, `/revert`, `/run`, `/stop`, `/clear-cache`), runs (`GET /runs/recent`, `GET /{task_id}/runs`), metadata (`/meta/output-targets`, `/meta/actions`, `/meta/events`), onboarding, NL parsing (`POST /parse`), and **`POST /api/tasks/{task_id}/webhook/{token}`** — exempt from auth middleware via regex `^/api/tasks/[^/]+/webhook/[^/]+/?$`; the handler validates the per-task `webhook_token` itself (404 on mismatch → the path IS the credential). `POST /{task_id}/webhook-regenerate` rotates it.
- **Memory** (`/api/memory`): `POST /add` (`MemoryAddRequest {text 1..5000, category∈fact|contact|task|preference|identity|project|goal, source, session_id?}`), `GET ""`, `POST /search`, `GET /timeline`, `GET /by-session/{session_id}`, `POST /extract`, `POST /audit`, `POST /import`, `POST /{memory_id}/pin`, `GET|PUT|DELETE /{memory_id}` (`MemoryUpdateRequest {text, category?}`).
- **Auth** (`/api/auth`): see doc 06. Public: `/setup`, `/signup`, `/login`, `/logout`, `/status`, `/features`, `/settings` (scrubbed for non-admins), `/integrations/presets`.
- **API tokens** (`/api/tokens`, all `require_admin`): `GET /api/tokens` list (prefix only), `POST /api/tokens` (form `name=`) → `{"id", "name", "owner", "token": "ody_<urlsafe43>", "token_prefix", "scopes": ["chat"]}` — full token shown once; bcrypt hash stored; `DELETE /api/tokens/{token_id}`. Create/delete invalidate the AuthMiddleware token cache via `app.state.invalidate_token_cache()`.

## 9. Concrete Request/Response Examples

### 9.1 Create a session against a registered endpoint

```http
POST /api/session HTTP/1.1
Cookie: apollo_session=<token>
Content-Type: application/x-www-form-urlencoded

name=Research+chat&endpoint_id=ab12cd34&model=qwen2.5-7b-instruct
```

```json
{"id": "6f1c2e9a-6f1c-4b2e-9a3d-0c8b1d2e3f40", "name": "Research chat",
 "model": "qwen2.5-7b-instruct", "rag": false, "archived": false}
```

Failure modes: `400 {"detail": "Model endpoint no longer exists"}` (bad/disowned `endpoint_id`), `400 {"detail": "Cannot reach /v1/models"}`, `400 {"detail": "Model not found at server. Available: ..."}`, `403 {"detail": "Choose a registered model endpoint"}` (non-admin sent raw `endpoint_url`).

### 9.2 Switch model mid-session

```http
PATCH /api/session/6f1c2e9a-.../  HTTP/1.1
Content-Type: application/x-www-form-urlencoded

model=llama-3.1-8b-instruct&endpoint_id=ab12cd34
```

```json
{"id": "6f1c2e9a-...", "model": "llama-3.1-8b-instruct",
 "endpoint_url": "http://localhost:8002/v1/chat/completions"}
```

### 9.3 Non-streaming chat

```http
POST /api/chat HTTP/1.1
Content-Type: application/json

{"message": "Summarize my last three notes", "session": "6f1c2e9a-...",
 "attachments": [], "use_web": false, "use_research": false}
```

```json
{"response": "Here is a summary of your three most recent notes: ..."}
```

### 9.4 Paperclip event ingest + agent token mint

```http
POST /api/paperclip/events HTTP/1.1
X-Paperclip-Events-Token: <PAPERCLIP_EVENTS_TOKEN>   (omit on direct loopback)
Content-Type: application/json

{"events": [{"type": "heartbeat.run.log",
             "payload": {"agentId": "agent-7", "line": "Cloning repo..."}}]}
```

```json
{"accepted": 1, "rejected": 0}
```

```http
POST /api/paperclip/agent-tokens HTTP/1.1      (admin cookie or internal-tool token)
Content-Type: application/json

{"agent_id": "agent-7", "name": "DeeperCode"}
```

```json
{"agent_id": "agent-7", "name": "DeeperCode", "token": "pa-<48 hex chars>"}
```

### 9.5 lmproxy (OpenAI-compatible, bearer-guarded)

```http
POST /lmproxy/v1/chat/completions HTTP/1.1
Authorization: Bearer pa-3f2a...            (or the shared PAPERCLIP_PROXY_TOKEN)
Content-Type: application/json

{"model": "local", "messages": [{"role": "user", "content": "hi"}], "stream": true}
```

Proxied verbatim to the warm llama-server (`Authorization` stripped). Errors: `401 {"error": "unauthorized"}`, `503 {"error": "no local model is currently running; serve one in Apollo (Settings → AI / model picker) first"}`, `502 {"error": "local model server unreachable"}`.

### 9.6 Mint an external API token

```http
POST /api/tokens HTTP/1.1                    (admin)
Content-Type: application/x-www-form-urlencoded

name=n8n
```

```json
{"id": "1a2b3c4d", "name": "n8n", "owner": "antman",
 "token": "ody_Vq8Q...43-url-safe-chars", "token_prefix": "ody_Vq8Q", "scopes": ["chat"]}
```

The full `token` value appears only in this response; the DB stores a bcrypt hash plus the 8-char prefix.

## 10. Auth Requirements Summary

| Route group | Requirement |
|---|---|
| `/api/auth/login`, `/setup`, `/signup`, `/status`, `/features`, `/settings`, `/integrations/presets`, `/api/health`, `/api/version`, `/login`, `/static/*` | None (exempt) |
| `/api/paperclip/events` | Self-auth: `X-Paperclip-Events-Token` or direct loopback |
| `/api/tasks/{id}/webhook/{token}` | Self-auth: path-embedded `webhook_token` |
| `/lmproxy/v1/*` | Bearer: shared proxy token or per-agent `pa-` token |
| Sessions / chat / notes / memory / documents / gallery | Cookie session (or `ody_` bearer via `effective_user` attribution); owner-scoped |
| `/api/models` | Authenticated (401 for anonymous when configured); per-user scoping, admins see all |
| `/api/model-endpoints*`, `/api/probe*`, `/api/ping`, `/api/providers`, `/api/discover`, `/api/local-models/*`, `/api/tokens*`, `/api/paperclip/agent-tokens`, `DELETE /api/sessions/all` | `require_admin` (admin cookie, internal-tool token, or `AUTH_ENABLED=false`) |
| `/paperclip/*` HTTP | Global AuthMiddleware (cookie) |
| `/paperclip/*` WS | Explicit cookie validation in the handler (middleware bypassed) |

## 11. Error Conventions

- Custom exceptions map to JSON envelopes in `app.py`: `SessionNotFoundError` → 404 `{"error": "SESSION_NOT_FOUND", "message": ...}`; `InvalidFileUploadError` → 400 `INVALID_FILE_UPLOAD`; `LLMServiceError` → 502 `LLM_SERVICE_ERROR`; `WebSearchError` → 502 `WEB_SEARCH_ERROR`.
- Generic FastAPI errors use `{"detail": ...}`; auth middleware uses `{"error": "Not authenticated"}` / `{"error": "Invalid API token"}` / `{"error": "Setup required"}`.
- `src/request_models.py:ErrorResponse` defines the documented shape `{error, message, details?}`.
