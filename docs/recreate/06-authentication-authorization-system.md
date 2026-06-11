# Apollo — Authentication & Authorization System

Apollo layers several auth mechanisms: cookie sessions for browsers, bearer `ody_` API tokens for integrations, a per-process internal-tool token for the agent's loopback calls, self-authenticating webhook/event endpoints, and token-guarded proxies for the Paperclip sidecar. The hub is the `AuthMiddleware` in `app.py` (~lines 148–390), backed by `core/auth.py:AuthManager`, with route-level helpers in `src/auth_helpers.py` and `core/middleware.py`.

## 1. AUTH_ENABLED Gate & Single-User Mode

```python
# app.py
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
LOCALHOST_BYPASS = os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"
...
if AUTH_ENABLED:
    ...
    app.add_middleware(AuthMiddleware)
    logger.info("Auth middleware enabled (AUTH_ENABLED=true)")
else:
    logger.info("Auth middleware disabled (set AUTH_ENABLED=true to enable)")
```

The middleware is **only installed when `AUTH_ENABLED` is true** (default). With `AUTH_ENABLED=false`, no auth runs at all — `request.state.current_user` is never set, and every route must tolerate an anonymous caller. Routes mirror this with the `_auth_disabled()` pattern:

```python
# src/auth_helpers.py (same check duplicated in routes/session_routes.py and core/middleware.py)
def _auth_disabled() -> bool:
    return os.getenv("AUTH_ENABLED", "true").lower() == "false"
```

Single-user semantics in `routes/session_routes.py:_verify_session_owner`: if there is no user **and** auth is disabled, ownership gating is skipped entirely ("every session belongs to the user"); if auth is enabled but the caller is anonymous, it raises `403 "Authentication required"`; if the row's `owner` differs, it raises **404** (not 403) so existence isn't leaked. `src/auth_helpers.py:require_user` returns `""` (anonymous-but-allowed) in exactly three cases: (1) `AUTH_ENABLED=false`, (2) unconfigured first run + loopback caller, (3) `LOCALHOST_BYPASS=true` + loopback caller; otherwise it raises 401. Owner scoping in queries:

```python
# src/auth_helpers.py
def owner_filter(query, model_cls, user: str, *, include_shared: bool = True):
    """No-op when `user` is empty (single-user mode)."""
    if not user:
        return query
    if include_shared:
        return query.filter((model_cls.owner == user) | (model_cls.owner == None))  # noqa: E711
    return query.filter(model_cls.owner == user)
```

Note `.env` parsing uses `load_dotenv(encoding="utf-8-sig")` to tolerate a Windows BOM — otherwise `AUTH_ENABLED=false` silently fails to parse (issue #142).

## 2. AuthMiddleware Decision Order (app.py)

For each request, in order:

1. **Exempt paths** pass through untouched (§3).
2. **Internal-tool token** — header `X-Apollo-Internal-Token` matching `INTERNAL_TOOL_TOKEN` *and* a trusted loopback client → `current_user = "internal-tool"` (or an impersonated existing user from `X-Apollo-Owner`, attribution only).
3. **LOCALHOST_BYPASS** — if enabled and the connection is trusted loopback → `current_user = _bypass_user()`.
4. **Unconfigured** (`auth_manager.is_configured == False`, i.e. zero users) → API paths get `401 {"error": "Setup required"}`, page paths redirect 302 to `/login`.
5. **Bearer `ody_` tokens** (§6).
6. **Cookie session** — `apollo_session` cookie validated by `auth_manager.validate_token`; failure → `401 {"error": "Not authenticated"}` for `/api/*`, else 302 `/login`. Success stamps `request.state.current_user` and `request.state.api_token = False`.

Exempt-path matching:

```python
# app.py
def _is_auth_exempt(path: str) -> bool:
    if path in AUTH_EXEMPT_EXACT:
        return True
    if any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        return True
    return any(p.match(path) for p in AUTH_EXEMPT_PATTERNS)
```

### Trusted loopback (anti-tunnel-spoofing)

```python
# app.py
_PROXY_FWD_HEADERS = (
    "cf-connecting-ip", "cf-ray", "cf-visitor",
    "x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded",
)

def _is_trusted_loopback(request: Request) -> bool:
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1"):
        return False
    for _h in _PROXY_FWD_HEADERS:
        if request.headers.get(_h):
            return False
    return True
```

Cloudflared/nginx/Caddy/Tailscale Funnel connect *from* 127.0.0.1, so a bare loopback check would let remote visitors inherit local trust. Any proxy-forwarding header disqualifies the request. Apollo's own in-process loopback calls carry none of those headers.

### LOCALHOST_BYPASS identity

```python
# app.py
def _bypass_user() -> str:
    """Prefer an admin, else the first user, else "" (anonymous)."""
    users = auth_manager.users or {}
    for name, data in users.items():
        if isinstance(data, dict) and data.get("is_admin"):
            return name
    return next(iter(users), "")
```

Bypass requests act as a *real* account so ownership-based routes (sessions, documents) work without a login instead of 403-ing. Startup logs a warning: "LOCALHOST_BYPASS is enabled... Do not expose this instance to a network."

## 3. Auth Exemptions and Why Each Exists

```python
# app.py (inside the AUTH_ENABLED block)
AUTH_EXEMPT_EXACT = {
    "/api/auth/setup", "/api/auth/signup", "/api/auth/login", "/api/auth/logout",
    "/api/auth/status", "/api/auth/features", "/api/auth/settings",
    "/api/auth/integrations/presets",
    "/api/health", "/api/version", "/login",
    "/api/paperclip/events",
}
AUTH_EXEMPT_PREFIXES = ["/static", "/lmproxy"]
AUTH_EXEMPT_PATTERNS = [re.compile(r"^/api/tasks/[^/]+/webhook/[^/]+/?$")]
```

| Exemption | Rationale |
|---|---|
| `/api/auth/*` login surface | Must be reachable pre-auth (login/setup/signup/status). `/api/auth/settings` stays callable for keybinds/TTS prefs, but the handler scrubs secrets for non-admins (`src/settings_scrub.scrub_settings`). |
| `/api/health`, `/api/version`, `/login` | Liveness probes and the login page itself. |
| `/api/paperclip/events` | **Proves identity itself**: when `PAPERCLIP_EVENTS_TOKEN` is set, the `X-Paperclip-Events-Token` header must match (`hmac.compare_digest`); when unset, only direct loopback clients with no `x-forwarded-for` are accepted ("loopback-only trust is void behind a reverse proxy"). Same self-authenticating pattern as task webhooks. |
| `/static` | Public assets (login page CSS/JS must load pre-auth). |
| `/lmproxy` | Local-model OpenAI proxy consumed by Paperclip's opencode agents — same-host child processes with no Apollo session. Guarded by its own bearer token in `routes/lmproxy_routes.py` (§8). |
| `^/api/tasks/{id}/webhook/{token}$` | The route handler validates the per-task `webhook_token` (unique column on `scheduled_tasks`) and 404s on mismatch — **the path is the credential**, so external callers (Zapier, n8n, curl) need no cookie. Without the exemption, AuthMiddleware would 401 every POST before the token was ever checked. |

## 4. Cookie Session Auth (`core/auth.py`, `routes/auth_routes.py`)

- Cookie: `SESSION_COOKIE = "apollo_session"` (`routes/auth_routes.py`). Set on login with `httponly=True, samesite="lax", secure=SECURE_COOKIES env ("false" default), path="/"`; `max_age = 60*60*24*7` only when `remember=true`.
- Tokens: `secrets.token_hex(32)`; server-side TTL `TOKEN_TTL = 60*60*24*7` (7 days). Stored in `data/sessions.json` (`{token: {"username", "expiry"}}`), written atomically under an `RLock`, expired entries pruned on load.
- `validate_token` / `get_username_for_token` also drop **orphan sessions**: if the username no longer exists in `auth.json` (admin deleted the user), the token is revoked on the next request instead of continuing to authenticate.
- Passwords: bcrypt (`bcrypt.hashpw(password, bcrypt.gensalt())`) in `data/auth.json` per user: `{"password_hash", "created", "is_admin", "privileges"}`. Usernames are normalized `.strip().lower()` at every entry point.
- Login flow (`POST /api/auth/login`, body `{username, password, remember=true, totp_code?}`): rate-limited 15 req/60 s per client IP; password verified first; if TOTP is enabled and no code supplied, returns `{"ok": false, "requires_totp": true, "username": ...}` (no cookie); invalid code → 401. Setup (`/setup`, first admin, min 8 chars, only if zero users, guarded by a `_setup_lock` against concurrent double-setup) and signup (`/signup`, only when admin enabled `signup_enabled`) are rate-limited 3 req/300 s.

```http
POST /api/auth/login HTTP/1.1
Content-Type: application/json

{"username": "Antman", "password": "correct horse battery", "remember": true}
```

```http
HTTP/1.1 200 OK
Set-Cookie: apollo_session=<64-hex>; HttpOnly; Path=/; SameSite=lax; Max-Age=604800

{"ok": true, "username": "antman"}
```

`GET /api/auth/status` response (authenticated admin):

```json
{
  "configured": true,
  "authenticated": true,
  "username": "antman",
  "is_admin": true,
  "privileges": {
    "can_use_agent": true, "can_use_browser": true, "can_use_bash": true,
    "can_use_documents": true, "can_use_research": true,
    "can_generate_images": true, "can_manage_memory": true,
    "max_messages_per_day": 0, "allowed_models": []
  },
  "signup_enabled": false
}
```

On-disk shapes:

```json
// data/auth.json
{"users": {"antman": {"password_hash": "$2b$12$...", "created": 1765400000.0,
                       "is_admin": true, "privileges": {...},
                       "totp_enabled": true, "totp_secret": "BASE32...",
                       "totp_backup_codes": ["a1b2c3d4", "..."]}},
 "signup_enabled": false}

// data/sessions.json   (token -> session; pruned of expired entries on load)
{"<64-hex-token>": {"username": "antman", "expiry": 1766004800.0}}
```
- `POST /api/auth/change-password` revokes all of the user's other sessions (keeps the current cookie). `delete_user` revokes every session of the deleted user. `rename_user` rewrites `owner` on every SQLAlchemy model that has an `owner` column (via `Base.registry.mappers`) plus `user_prefs.json`, then renames live session tokens.
- **Reserved usernames** (`RESERVED_USERNAMES = frozenset({"internal-tool", "api", "demo", "system"})`): cannot be created or renamed into. `internal-tool` is security-critical — `require_admin` grants admin to any request whose `current_user == "internal-tool"`, so a real account with that name would pass every admin check. `api` collides with the bearer-token sentinel.

### 4.1 2FA / TOTP (`core/auth.py` + `/api/auth/2fa/*`)

- `POST /api/auth/2fa/setup` → `totp_generate_secret` (pyotp `random_base32`, stored as `totp_secret_pending`) → returns `{"secret", "uri", "qr_code": "data:image/png;base64,..."}` (issuer `"Apollo"`).
- `POST /api/auth/2fa/confirm` body `{code}` → `totp_confirm_enable` verifies with `valid_window=1`, promotes pending→`totp_secret`, sets `totp_enabled: true`, mints 8 single-use backup codes (`secrets.token_hex(4)`), returned once: `{"ok": true, "backup_codes": [...]}`.
- Login verification `totp_verify`: backup codes are checked first and consumed; **fails closed** if `totp_enabled` is set but `totp_secret` is missing (corrupt auth.json) — returning True there would silently bypass the second factor.
- `POST /api/auth/2fa/disable` requires the account password; `GET /api/auth/2fa/status` → `{"enabled": bool}`.

## 5. Admin Gating & the Internal-Tool Token (`core/middleware.py`)

```python
# core/middleware.py
INTERNAL_TOOL_TOKEN = os.environ.get("APOLLO_INTERNAL_TOKEN") or secrets.token_hex(32)
INTERNAL_TOOL_HEADER = "X-Apollo-Internal-Token"

def require_admin(request: Request):
    hdr = request.headers.get(INTERNAL_TOOL_HEADER)
    if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
        return                                            # (a) header-direct loopback
    if getattr(request.state, "current_user", None) == "internal-tool":
        return                                            # (b) middleware-stamped loopback
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return                                            # auth disabled → allow
    if not auth_mgr or not auth_mgr.is_configured:
        raise HTTPException(403, "Admin only")
    user = getattr(request.state, "current_user", None)
    if not user or not auth_mgr.is_admin(user):
        raise HTTPException(403, "Admin only")
```

The token is per-process (never persisted, never sent to clients) and lets the agent's tool layer HTTP-loopback into admin-gated routes where no admin cookie exists. The middleware only honors it from `_is_trusted_loopback` clients. Before any loopback call is issued, tool dispatch verifies the session owner is an admin (`src/tool_security.py:owner_is_admin_or_single_user`), so non-admin users can't reach admin tools via the agent. The `X-Apollo-Owner` header allows owner *attribution* (notes/calendar writes land on the right user) only if that user actually exists — authorization is unchanged.

Privileges (`core/auth.py`): `DEFAULT_PRIVILEGES = {can_use_agent: True, can_use_browser: True, can_use_bash: False, can_use_documents: True, can_use_research: True, can_generate_images: True, can_manage_memory: True, max_messages_per_day: 0, allowed_models: []}`. Admins always receive `ADMIN_PRIVILEGES` wholesale from `get_privileges`. `src/auth_helpers.py:require_privilege(request, key)` 403s when a stored flag is False; unknown keys fail open.

## 6. Bearer `ody_` API Tokens & effective_user()

Minted by `POST /api/tokens` (admin): `raw_token = "ody_" + secrets.token_urlsafe(32)`, bcrypt-hashed, `token_prefix = raw_token[:8]` stored for display and cache lookup, default scope `"chat"`.

Middleware path (`app.py`): `Authorization: Bearer ody_...` → length sanity check (12–100 chars) → prefix lookup in an in-memory cache `{prefix: [(id, hash, owner, scopes)]}` rebuilt off-thread only when `app.state._token_cache_dirty` (set by `app.state.invalidate_token_cache()` on create/revoke) → `bcrypt.checkpw` per candidate:

```python
# app.py — AuthMiddleware bearer branch
auth_header = request.headers.get("authorization", "")
if auth_header.startswith("Bearer ody_"):
    raw_token = auth_header[7:]
    if len(raw_token) < 12 or len(raw_token) > 100:
        return JSONResponse(status_code=401, content={"error": "Invalid API token"})
    prefix = raw_token[:8]
    if app.state._token_cache_dirty:
        async with _token_cache_lock:
            if app.state._token_cache_dirty:
                await _asyncio.to_thread(_refresh_token_cache)
    candidates = list(_token_cache.get(prefix, ()))
    for tid, thash, owner, scopes in candidates:
        if _bcrypt.checkpw(raw_token.encode(), thash.encode()):
            matched_id, matched_owner, matched_scopes = tid, owner, scopes or []
            break
```

On match:

```python
request.state.current_user = "api"        # sandboxed pseudo-user
request.state.api_token = True
request.state.api_token_id = matched_id
request.state.api_token_owner = matched_owner
request.state.api_token_scopes = matched_scopes
```

`last_used_at` is updated fire-and-forget off the hot path. Any invalid bearer is rejected immediately with `401 {"error": "Invalid API token"}` (no fall-through to cookie auth).

```python
# src/auth_helpers.py
def effective_user(request: Request):
    if getattr(request.state, "api_token", False):
        owner = getattr(request.state, "api_token_owner", None)
        if owner:
            return owner
    return get_current_user(request)
```

Bearer callers surface as `"api"` to ordinary routes (kept out of cookie/user flows), but owner-aware routes (sessions, chat history) call `effective_user()` so a paired client sees and creates the **same data** as the owner's desktop UI rather than a separate "api"-owned silo. An ownerless token never escalates (falls back to `"api"`).

## 7. WebSocket Auth Pattern

Starlette's `BaseHTTPMiddleware` (which `AuthMiddleware` subclasses) **does not run for websockets**. Every WS endpoint must authenticate explicitly. The Paperclip proxy does:

```python
# routes/paperclip_routes.py
@router.websocket("/paperclip/{path:path}")
async def proxy_ws(websocket, path: str):
    from routes.auth_routes import SESSION_COOKIE   # local import: avoid cycle
    token = websocket.cookies.get(SESSION_COOKIE)
    ok = ws_validate(token) if ws_validate is not None else bool(token)
    if not cfg.enabled or not ok:
        await websocket.close(code=1008)            # policy violation
        return
```

`ws_validate` is wired in `app.py` as `lambda token: auth_manager.validate_token(token)`. HTTP traffic to `/paperclip/*` is still covered by the global AuthMiddleware.

## 8. lmproxy Tokens: Shared + Per-Agent

`/lmproxy/v1/*` is session-exempt and token-guarded (`routes/lmproxy_routes.py`):

- **Shared token** — `services/paperclip/config.py:resolve_proxy_token()`: env `PAPERCLIP_PROXY_TOKEN`, else file `~/.apollo/paperclip_proxy_token` (path overridable via `PAPERCLIP_PROXY_TOKEN_FILE`); generated as `secrets.token_hex(32)` and chmod 0600 on first use. Apollo passes it to Paperclip's opencode agents as their `OPENAI_API_KEY`. Compared with `hmac.compare_digest`.
- **Per-agent tokens** — `services/paperclip/agent_tokens.py:AgentTokenRegistry`: format `"pa-" + secrets.token_hex(24)`; persisted to `~/.apollo/paperclip_agent_tokens.json` (0600); **one token per agent — minting again rotates the old one out**; `lookup(token)` → `{"agent_id", "name"}`; `list()` exposes only `token_suffix` (last 6 chars). Minted/listed via the admin-gated `/api/paperclip/agent-tokens` routes. Per-agent identity lets the proxy attribute LLM calls and pulse `heartbeat.run.event` activity (rate-limited to one per agent per 10 s) onto the Floor.
- The proxy strips the inbound `Authorization` header before forwarding — the warm llama-server runs unauthenticated on localhost.

Related secret: `resolve_auth_secret()` provisions `BETTER_AUTH_SECRET` for the Paperclip runtime the same way (`PAPERCLIP_AUTH_SECRET` env or `~/.apollo/paperclip_secret`, 0600).

## 9. Threat-Model Notes (THREAT_MODEL.md)

- **Trust boundary**: Apollo targets *trusted users on a private network* — "treat it like an admin console." Admins can run shell commands, read/write files, send email, control model serving by design. The model defends against: unauthenticated access, non-admins reaching admin capabilities, prompt-injection from untrusted content, and internal services (ChromaDB, Ollama, SearXNG) being externally reachable.
- **Role matrix**: non-admins get chat/browser/documents/research/image-gen/memory; shell, file I/O, email, MCP tools, calendar management, token/webhook management, model serving, vault and settings are admin-only. Enforced via `DEFAULT_PRIVILEGES` plus `src/tool_security.py:NON_ADMIN_BLOCKED_TOOLS`; any tool named `mcp__*` is blocked for non-admins.
- **Prompt-injection hardening**: all external content (web results, fetched URLs, read email, memories, skills, notes) must pass through `src/prompt_security.py:untrusted_context_message(label, content)` (user-role data wrapper) under `UNTRUSTED_CONTEXT_POLICY`; injecting untrusted content into the system role is classified as a security bug.
- **Security headers** (`core/middleware.py:SecurityHeadersMiddleware`): per-request CSP nonce (`script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net`), `X-Frame-Options: DENY` + `frame-ancestors 'none'` (except sandboxed tool-render iframes), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. `style-src 'unsafe-inline'` is intentionally retained (inline styles can't execute script).
- **Known gaps** (open): (1) no shell/filesystem sandbox for agent tools; (2) SSRF via the `/api/v1/chat` `base_url` parameter for chat-scoped tokens (fix in PR #1039); (3) partial `src/search/` consolidation drift; (4) coarse token scopes — only `chat`/`admin`, no per-capability granularity.

## 10. Rebuild Checklist

1. Install `AuthMiddleware` only when `AUTH_ENABLED != "false"`; order: exempt → internal-tool → localhost bypass → setup gate → bearer → cookie.
2. Duplicate the `_auth_disabled()` / `require_user("")` semantics in route-level guards so middleware misconfiguration cannot silently open user data, and so disabling auth doesn't 401 the SPA (issue #622).
3. Always use `_is_trusted_loopback` (loopback host AND zero proxy-forwarding headers) for any localhost-trust decision.
4. Keep `RESERVED_USERNAMES` in sync with every synthetic-owner sentinel; never allow them at create/rename.
5. Authenticate websockets explicitly (cookie + `auth_manager.validate_token`); `BaseHTTPMiddleware` will not do it.
6. Store only bcrypt hashes for passwords and API tokens; show full bearer/agent tokens exactly once at mint time; chmod 0600 every secret file under `data/` and `~/.apollo/`.
