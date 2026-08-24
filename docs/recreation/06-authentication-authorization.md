# 06 — Authentication & Authorization System

Apollo layers four identity-resolution paths behind one FastAPI middleware — cookie sessions, a per-process internal-tool token for the agent's own loopback tool calls, an optional `LOCALHOST_BYPASS` for trusted-network deployments, and `ody_`-prefixed bearer API tokens for external integrations — plus a hard `AUTH_ENABLED=false` kill switch for the single-user desktop bundle. The core user/session store is `core/auth.py:AuthManager`, the request-level identity resolver is `AuthMiddleware` in `app.py`, a small shared admin-gate helper lives in `core/middleware.py`, and route-level identity/ownership helpers live in `src/auth_helpers.py`. `routes/auth_routes.py` additionally defines its own **local, stricter** admin gate (`_require_admin_user`) after a real security incident where the shared gate leaked an admin-only endpoint to unauthenticated loopback callers — documented in detail in §6.

---

## 1. `core/auth.py` — `AuthManager`

### 1.1 Storage — flat JSON files, not a database table

Users and sessions are **not** in SurrealDB/SQLite — they're two sibling JSON files written with atomic writes:

```python
# core/auth.py:39-43
DEFAULT_AUTH_PATH = str(data_path("auth.json"))
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days
```

```python
# core/auth.py:73-75
def __init__(self, auth_path: str = DEFAULT_AUTH_PATH):
    self.auth_path = auth_path
    self._sessions_path = os.path.join(os.path.dirname(auth_path), "sessions.json")
```

Both files are persisted through `core.atomic_io.atomic_write_json` (imported as `from core.atomic_io import atomic_write_json as _atomic_write_json`), so a crash mid-write can't leave `auth.json`/`sessions.json` truncated or half-written. `data_path()` comes from `src/runtime_paths.py` — the same resolver `setup.py` uses for its own `auth.json` write (§8), so both code paths agree on where the file lives regardless of how Apollo is launched (native, packaged `.app`, Docker).

Users are a dict keyed by **lowercased username**:

```python
# core/auth.py — create_user, ~207-212
self._config["users"][username] = {
    "password_hash": _hash_password(password),
    "created": time.time(),
    "is_admin": is_admin,
    "privileges": dict(ADMIN_PRIVILEGES if is_admin else DEFAULT_PRIVILEGES),
}
```

`DEFAULT_PRIVILEGES` gates individual capabilities per non-admin user (e.g. `can_use_bash` defaults to `False` even for a signed-up regular user) — this is the same `privileges` object the frontend reads from `/api/auth/status` to cosmetically hide UI controls (see doc 05 §1.4).

Reserved usernames block sentinel-impersonation — a user cannot register as `internal-tool`, `api`, `demo`, or `system` (`RESERVED_USERNAMES = frozenset({"internal-tool", "api", "demo", "system"})`), because those exact strings are the identities the middleware stamps onto `request.state.current_user` for non-cookie auth paths (§3).

### 1.2 Password hashing — bcrypt, default cost factor

```python
# core/auth.py:62-67
def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
```

`bcrypt.gensalt()` is called with no explicit `rounds=` argument, so the cost factor is whatever the installed `bcrypt` package defaults to (12, as of the versions bcrypt has shipped for years). There is no pepper, no per-install salt beyond bcrypt's own per-hash salt, and no configurable work factor — a recreation should preserve `bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt())` exactly, since changing the cost factor would silently invalidate every existing `auth.json`.

### 1.3 Session tokens — opaque random hex, not JWT

Sessions are **not** signed tokens (no JWT, no `itsdangerous`). `create_session` verifies the password, mints a random 256-bit hex token, and stores a server-side mapping from token → `{username, expiry}`:

```python
# core/auth.py:419-431
def create_session(self, username: str, password: str) -> Optional[str]:
    """Verify credentials and return a session token, or None."""
    username = username.strip().lower()
    if not self.verify_password(username, password):
        return None
    token = secrets.token_hex(32)
    with self._sessions_lock:
        self._sessions[token] = {
            "username": username,
            "expiry": time.time() + TOKEN_TTL,
        }
    self._save_sessions()
    return token
```

Because the token carries no embedded claims, **validity is a dictionary lookup, not a signature check** — every request that presents a cookie triggers `sessions.json` (in-memory cache backed by the file) being consulted. `TOKEN_TTL = 60 * 60 * 24 * 7` (7 days) is the server-side session lifetime, independent of the cookie's own `max_age` (§2).

### 1.4 Token validation — verbatim, including the deleted-user fail-safe

```python
# core/auth.py:433-456
def validate_token(self, token: Optional[str]) -> bool:
    if not token:
        return False
    expired = False
    deleted_user = False
    with self._sessions_lock:
        session = self._sessions.get(token)
        if session is None:
            return False
        if time.time() > session["expiry"]:
            self._sessions.pop(token, None)
            expired = True
        else:
            # SECURITY: if the user record has since been removed (admin
            # deleted them while their cookie was still valid), drop the
            # session so the next request kicks them out instead of
            # silently authenticating against a non-existent account.
            if session.get("username") not in self.users:
                self._sessions.pop(token, None)
                deleted_user = True
    if expired or deleted_user:
        self._save_sessions()
        return False
    return True
```

This closes a real class of bug: a session token minted for a now-deleted user would otherwise stay "valid" (lookup succeeds, not expired) until its 7-day TTL elapsed, even though `AuthManager.users` no longer contains that account. The check re-validates against the live user table on every request, not just at login.

### 1.5 Two-factor auth (TOTP) — fail-closed on corrupt state

`core/auth.py` also implements TOTP 2FA (`pyotp`): `totp_generate_secret`, `totp_confirm_enable`, `totp_verify`, `totp_disable`. The verify path fails **closed**, not open, if the enabled flag and the secret disagree:

```python
# core/auth.py:373-384
def totp_verify(self, username: str, code: str) -> bool:
    """Verify a TOTP code for login."""
    username = username.strip().lower()
    user = self.users.get(username, {})
    if not user.get("totp_enabled"):
        return True  # 2FA not enabled, always pass
    secret = user.get("totp_secret")
    if not secret:
        # 2FA is enabled but no secret is stored (corrupt/partially-written
        # auth.json). Fail closed — returning True here bypassed the second
        # factor entirely.
        return False
```

### 1.6 Admin flag and legacy migration

```python
# core/auth.py:283-284
def is_admin(self, username: str) -> bool:
    return self.users.get(username, {}).get("is_admin", False)
```

An older `role: "admin"` marker (from an earlier version of `setup.py`) is migrated forward automatically the first time `AuthManager` loads a legacy file:

```python
# core/auth.py:153-162
def _migrate_legacy_admin_role(self):
    """Normalize setup.py's old role='admin' marker to is_admin=True."""
    changed = False
    for username, user in self.users.items():
        if user.get("role") == "admin" and "is_admin" not in user:
            user["is_admin"] = True
            changed = True
            logger.info(f"Migrated legacy admin role for '{username}'")
    if changed:
        self._save()
```

---

## 2. Login flow, end to end

### 2.1 Frontend form — `static/login.html`

```html
<!-- static/login.html:251-279 (abridged) -->
<form id="authForm" autocomplete="on">
  <input id="username" name="username" type="text" required autofocus autocomplete="username">
  <input type="checkbox" class="remember-check" id="remember" checked aria-label="Remember me">
  <input id="password" name="password" type="password" required autocomplete="current-password">
  <input id="confirmPassword" name="confirmPassword" type="password" autocomplete="new-password">
  <button type="submit" id="submitBtn">Sign In</button>
</form>
```

### 2.2 Submission JS

```js
// static/login.html:465-477
async function doLogin(totpCode) {
  const loginBody = { username, password, remember };
  if (totpCode) loginBody.totp_code = totpCode;
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(loginBody)
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Login failed');
  return data;
}
```

If the response carries `requires_totp: true` (password accepted, 2FA still pending), the JS injects a TOTP code field and resubmits the same request with `totp_code` set (lines ~384-406, 481-495). On success, `finishLogin()` (lines 448-463) prefetches `/api/sessions`, `/api/auth/features`, and `/api/auth/settings` into `sessionStorage` (a warm-cache optimization for the SPA's first paint) and then does a **hard redirect**, not an SPA transition:

```js
window.location.replace('/');
```

The page also guards the reverse case at load — if already authenticated (`GET /api/auth/status` succeeds), it immediately `window.location.replace('/')`s away from `/login` without rendering the form.

### 2.3 Backend handler — `routes/auth_routes.py:144-174`

```python
@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    if not _login_limiter.check(request.client.host):
        raise HTTPException(429, "Too many requests — try again later")
    # Verify password first
    username = body.username.strip().lower()
    if not await asyncio.to_thread(auth_manager.verify_password, username, body.password):
        raise HTTPException(401, "Invalid credentials")
    # Check 2FA if enabled
    if auth_manager.totp_enabled(username):
        if not body.totp_code:
            # Password OK but need TOTP — tell client to show code input
            return {"ok": False, "requires_totp": True, "username": username}
        if not auth_manager.totp_verify(username, body.totp_code):
            raise HTTPException(401, "Invalid 2FA code")
    # All checks passed — create session
    token = await asyncio.to_thread(auth_manager.create_session, username, body.password)
    if not token:
        raise HTTPException(401, "Invalid credentials")
    cookie_kwargs = dict(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true",
        path="/",
    )
    if body.remember:
        cookie_kwargs["max_age"] = 60 * 60 * 24 * 7  # 7 days
    response.set_cookie(**cookie_kwargs)
    return {"ok": True, "username": username}
```

Key details:
- **Rate limiting**: `_login_limiter = RateLimiter(max_requests=15, window_seconds=60)`, keyed by `request.client.host` — 15 attempts/minute per client IP before a `429`.
- Password is verified **before** touching 2FA, so a wrong-password attempt never leaks whether 2FA is enabled for that account.
- Password verification and session creation both run via `asyncio.to_thread` — bcrypt is CPU-bound and synchronous, so it's offloaded off the event loop rather than blocking other requests.
- Cookie name constant: `SESSION_COOKIE = "apollo_session"`.
- Cookie flags: `httponly=True` (no JS access, XSS-resistant), `samesite="lax"`, `secure` gated behind `SECURE_COOKIES` env var (defaults `false` — appropriate for plain-HTTP localhost deployments, must be set `true` behind HTTPS in production).
- **"Remember me" semantics**: if `body.remember` is false, `max_age` is never set, so the browser treats it as a *session cookie* that dies when the browser closes — but the server-side token in `sessions.json` still lives the full 7-day `TOKEN_TTL` regardless. Unchecking "remember me" only changes how long the *browser* keeps the cookie, not how long the *server* would honor it if replayed.

Frontend then does a hard page reload to `/`, which re-runs the full module bootstrap in doc 05 with the new cookie already set.

---

## 3. `AuthMiddleware` — identity resolution and loopback trust

There is a small shared file at `core/middleware.py` with `require_admin()` and `SecurityHeadersMiddleware`, but the middleware that actually populates `request.state.current_user` from the cookie/token on every request is `AuthMiddleware`, defined inline in **`app.py`** (lines ~150-405) and registered conditionally:

```python
# app.py:155, 179
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
...
if AUTH_ENABLED:
    app.add_middleware(AuthMiddleware)
else:
    logger.info("Auth middleware disabled (set AUTH_ENABLED=true to enable)")
```

### 3.1 Exempt paths

Before any identity check, a fixed allowlist of paths passes straight through: `/api/auth/setup`, `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/status`, `/api/auth/features`, `/api/auth/settings`, `/api/auth/integrations/presets`, `/api/health`, `/api/version`, `/login`, `/api/paperclip/events`; prefix-exempt `/static`, `/lmproxy`; and a regex-exempt webhook path `^/api/tasks/[^/]+/webhook/[^/]+/?$` (so external webhook callers don't need a session cookie at all — those routes authenticate themselves per-webhook-secret instead).

### 3.2 Trusted-loopback detection — proxy-aware

A bare `request.client.host in ("127.0.0.1", "::1")` check is unsafe once anything (a Cloudflare Tunnel, an nginx/Caddy reverse proxy, Tailscale Funnel) sits in front of the app: those all connect to the FastAPI process *from* loopback, so a bare host check would let a remote visitor inherit local trust. The middleware instead requires loopback **and** the absence of any proxy-forwarding header:

```python
# app.py:255-278
_PROXY_FWD_HEADERS = (
    "cf-connecting-ip", "cf-ray", "cf-visitor",
    "x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded",
)

def _is_trusted_loopback(request: Request) -> bool:
    """True ONLY for a DIRECT loopback connection with no proxy/tunnel
    forwarding headers. A bare ``client.host in ('127.0.0.1','::1')`` check is
    unsafe behind a Cloudflare tunnel / reverse proxy: those connect from
    loopback, so a remote visitor would otherwise inherit local trust and
    slip past LOCALHOST_BYPASS or spoof the internal-tool path. Apollo's own
    in-process agent loopback calls carry none of these headers, so they still
    qualify."""
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1"):
        return False
    for _h in _PROXY_FWD_HEADERS:
        if request.headers.get(_h):
            return False
    return True
```

This exact predicate gates **both** the internal-tool token path and `LOCALHOST_BYPASS` below — a request carrying `X-Forwarded-For` (proof it was tunneled/proxied) can never take either shortcut, even if its raw TCP peer is `127.0.0.1`.

### 3.3 `dispatch()` — full decision order

```python
# app.py:280-400 (identity-resolution excerpt, verbatim)
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_auth_exempt(path):
            return await call_next(request)

        # In-process internal-tool token bypass.
        try:
            from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN as _ITT
            _hdr = request.headers.get(INTERNAL_TOOL_HEADER)
            if _hdr and secrets.compare_digest(_hdr, _ITT) and _is_trusted_loopback(request):
                _impersonate = (request.headers.get("X-Apollo-Owner") or "").strip()
                _auth_mgr = getattr(request.app.state, "auth_manager", None) or auth_manager
                if _impersonate and _impersonate in getattr(_auth_mgr, "users", {}):
                    request.state.current_user = _impersonate
                    request.state.internal_tool_owner = _impersonate
                else:
                    request.state.current_user = "internal-tool"
                    request.state.internal_tool_owner = None
                request.state.internal_tool = True
                request.state.auth_mode = "internal_tool"
                request.state.api_token = False
                return await call_next(request)
        except Exception as e:
            logger.debug("Internal tool loopback auth check failed: %s", e, exc_info=True)

        # LOCALHOST_BYPASS — direct localhost only, never over a tunnel/proxy.
        if LOCALHOST_BYPASS and _is_trusted_loopback(request):
            request.state.current_user = _bypass_user()
            request.state.internal_tool = False
            request.state.auth_mode = "localhost_bypass"
            request.state.api_token = False
            return await call_next(request)

        if not auth_manager.is_configured:
            if not path.startswith("/api/"):
                return RedirectResponse(url="/login", status_code=302)
            return JSONResponse(status_code=401, content={"error": "Setup required"})

        # --- Bearer token auth (API tokens for external integrations) ---
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ody_"):
            ...   # see §7

        # --- Cookie-based session auth ---
        token = request.cookies.get(SESSION_COOKIE)
        if not auth_manager.validate_token(token):
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"error": "Not authenticated"})
            return RedirectResponse(url="/login", status_code=302)

        request.state.current_user = auth_manager.get_username_for_token(token)
        request.state.internal_tool = False
        request.state.auth_mode = "cookie"
        request.state.api_token = False
        return await call_next(request)
```

Six mutually-exclusive outcomes stamp `request.state`, always including `auth_mode` (useful for downstream logging/telemetry) and `api_token` (a boolean any route can check to require a "real" bearer-token caller — see §7):

| Path | `current_user` | `auth_mode` | Notes |
|---|---|---|---|
| Exempt path | *(unset)* | — | Falls through untouched |
| Internal-tool token + trusted loopback | `X-Apollo-Owner` if valid, else `"internal-tool"` | `internal_tool` | `request.state.internal_tool = True` |
| `LOCALHOST_BYPASS=true` + trusted loopback | `_bypass_user()` | `localhost_bypass` | Only reached if internal-tool path didn't match |
| Auth unconfigured (0 users) | *(unset)* | — | `401`/`302` to force setup |
| Valid `Bearer ody_...` | `"api"` | `api_token` | `request.state.api_token = True` |
| Valid `apollo_session` cookie | actual username | `cookie` | Normal browser session |
| Invalid/missing cookie | *(unset)* | — | `401` for `/api/*`, `302` to `/login` otherwise |

### 3.4 The internal-tool token itself — `core/middleware.py`

```python
# core/middleware.py:17-25 (constants + doc)
# Per-process token that lets the in-app tool layer hit admin-gated
# routes via HTTP loopback (the agent's tool calls don't carry the
# admin user's session cookie). Set once at import; tools read the
# same value from this module. Never persisted or exposed externally.
INTERNAL_TOOL_TOKEN = os.environ.get("APOLLO_INTERNAL_TOKEN") or secrets.token_hex(32)
INTERNAL_TOOL_HEADER = "X-Apollo-Internal-Token"
```

This token exists because the agent's own tool-calling loop makes HTTP calls back into Apollo's own API (loopback) to execute tools — those internal requests have no browser session cookie to present. Comparison uses `secrets.compare_digest` (constant-time) to avoid a timing side-channel on the token check.

### 3.5 `_bypass_user()` — who `LOCALHOST_BYPASS` requests act as

```python
# app.py:161-177
def _bypass_user() -> str:
    """Identity that loopback-bypass requests act as. ..."""
    try:
        users = auth_manager.users or {}
    except Exception as error:
        report_exception(logger, "localhost_bypass_user_lookup_failed", error, outcome="best_effort")
        users = {}
    for name, data in users.items():
        if isinstance(data, dict) and data.get("is_admin"):
            return name
    return next(iter(users), "")
```

Prefers the first admin found; falls back to the first user of any kind; falls back to `""` (empty owner, single-user semantics) if no users exist yet.

### 3.6 `src/auth_helpers.py` — the companion route-level resolver

Most route handlers don't read `request.state.current_user` directly — they call `resolve_identity(request)` / `require_user(request)` from `src/auth_helpers.py`, which layers the same three "who is this" fallbacks (unconfigured+loopback, `LOCALHOST_BYPASS`, `AUTH_ENABLED=false`) on top of whatever the middleware already stamped, returning an empty-string owner (`""`, meaning "single-user mode, no ownership filtering") rather than raising in the desktop-mode/unconfigured/bypass cases. It uses its own, simpler loopback check for this fallback layer:

```python
# src/auth_helpers.py:38-41
def _is_loopback(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = (getattr(client, "host", None) or "").lower()
    return host in {"127.0.0.1", "::1", "localhost"}
```

This simpler check is safe to use here specifically because it only fires *after* `AuthMiddleware`'s stricter `_is_trusted_loopback` has already run earlier in the request pipeline and left `request.state.current_user` unset — i.e. this is a second-layer fallback for the already-filtered "nobody claimed this request" case, not a fresh trust boundary of its own.

Owner-scoped query filtering — the no-op-when-single-user pattern used throughout the DB layer:

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

---

## 4. `AUTH_ENABLED=false` — desktop-mode semantics

Three separate files parse the same env var, and comments in each explicitly note they must agree:

```python
# app.py:155
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
```
When false, `AuthMiddleware` is **never registered** on the app at all (see §3, the `if AUTH_ENABLED: app.add_middleware(...)` gate) — no cookie/bearer/loopback logic runs on any request, and `request.state.current_user` is simply never set by the middleware layer for any route.

```python
# core/middleware.py:49 (inside require_admin)
if os.getenv("AUTH_ENABLED", "true").lower() == "false":
    return
```
Every route using the shared `require_admin` gate (the majority of admin-only routes — see the table in §5) passes through unconditionally when auth is disabled.

```python
# src/auth_helpers.py:121-125
def _auth_disabled() -> bool:
    """True when the operator has explicitly turned off auth via .env.
    Mirrors the AUTH_ENABLED parse in app.py / core/middleware.py so the
    three call sites agree on what "off" means."""
    return os.getenv("AUTH_ENABLED", "true").lower() == "false"
```
Used inside `resolve_identity()`/`require_user()` to let unauthenticated callers through with an empty owner string rather than redirecting to `/login` — this exists specifically to prevent a past regression (referenced in-code as issue #622) where the login page kept appearing even with `AUTH_ENABLED=false` set.

**What is open**: with `AUTH_ENABLED=false`, effectively the entire API surface — chat, sessions, documents, memory, and every admin-only route gated purely through `core.middleware.require_admin` — is reachable with no identity check at all. This is the intended behavior for the packaged macOS desktop bundle, which binds only to `127.0.0.1` and treats "can reach this process's port" as sufficient authorization (single physical user, single machine).

**What still requires a real gate even here**: `routes/auth_routes.py`'s local `_require_admin_user()` (§6) is the one place that does not fully hand-wave desktop mode away — but note carefully what it actually does: when `AUTH_ENABLED=false` it also returns `None` (proceeds, no identity required), matching every other `require_admin`-gated route. The distinction that matters is the *opposite* case — when auth **is** enabled, `_require_admin_user` is strictly cookie-only and does not trust anything the middleware stamped onto `request.state`, whereas the shared `core.middleware.require_admin` does trust `request.state.current_user` (populated via loopback/bypass paths). §6 explains exactly why that distinction was necessary.

In short: `AUTH_ENABLED=false` is a deliberate, total bypass for a single-operator local deployment, not a partial "admin still gated" mode — nothing is admin-gated once it's set.

---

## 5. Admin vs. regular user

Admin status is a single boolean per user (`is_admin`, §1.6), checked via `AuthManager.is_admin(username)`. Three enforcement mechanisms exist:

1. **`core/middleware.require_admin(request)`** — the canonical, shared gate, used across most of `routes/*.py`.
2. **Local per-router gates** that intentionally re-derive identity from the cookie instead of trusting `request.state` — `routes/shell_routes.py::_require_admin` ("Shell exec is admin-only — never expose to regular users; that's RCE-after-signup"), and `routes/auth_routes.py::_require_admin_user` (§6).
3. **Ad hoc `auth_manager.is_admin(user)` checks** inside route bodies for owner-vs-admin authorization on shared resources rather than blanket gating — e.g. `routes/upload_routes.py` (`if file_owner != current_user and not auth_mgr.is_admin(current_user): raise 403`), `routes/session_routes.py::_current_user_is_admin`, `routes/model_routes.py` (deciding whether a user sees other owners' model endpoints), `routes/memory_routes.py` (`_is_admin = resolve_identity(request).is_admin`).

### 5.1 Representative admin-gated surface (via `require_admin`/`_require_admin`)

| Router | Admin-only endpoints |
|---|---|
| `routes/activity_routes.py` | `GET ""`, `POST /undo-session/{id}`, `GET`/`PUT /autonomy`, `POST /{id}/undo` |
| `routes/admin_wipe_routes.py` | `DELETE /wipe/{kind}` |
| `routes/api_token_routes.py` | `GET`/`POST /tokens`, `DELETE /tokens/{id}` |
| `routes/backup_routes.py` | `GET /api/export`, `POST /api/import` |
| `routes/diagnostics_routes.py` | `GET /api/db/stats`, `/api/rag/stats`, `/api/test/youtube`, `POST /api/test-research` |
| `routes/hub_routes.py` | free-model listing/install, GGUF search/download, codex-router, security-scan, catalog, persona install, reference install |
| `routes/localmodels_routes.py` | scan, voices, dirs (get/put), binary (get/put), start/stop |
| `routes/mcp_routes.py` | server CRUD, tool listing/toggling, OAuth flow endpoints |
| `routes/model_routes.py` | 16 admin-only sites — local-model/inference-config management |
| `routes/search_routes.py` | SearXNG status/install |
| `routes/session_routes.py` | `DELETE /sessions/all` |
| `routes/skills_routes.py` | built-in skill update/delete |
| `routes/shell_routes.py` | `POST /api/shell/exec`, `/stream`, cookbook package install |
| `routes/webhook_routes.py` | webhook CRUD/test |
| `routes/upload_routes.py` | `POST /cleanup`, `GET /stats` |
| `routes/vault_routes.py` | vault config/login/unlock/lock/logout |
| `routes/system_status_routes.py` | system status, system actions |
| `routes/skill_pack_routes.py` | preview/install |
| `routes/paperclip_routes.py` | agent-token issuance/listing |
| `routes/cookbook_routes.py` | 11 admin-only sites |
| `routes/auth_routes.py` | user list/create, privilege edits, rename, signup toggle, settings write (§6) |

### 5.2 Open to any authenticated (non-admin) user

Chat/session streaming, owner-scoped document/upload routes, `GET /api/auth/status`, `GET /api/auth/2fa/*`, `POST /api/auth/change-password`, `GET /api/auth/features` (no auth at all — publicly exempt), most of `routes/session_routes.py`, `routes/memory_routes.py`, personal document/contacts routes. These are gated by `require_user`/per-privilege checks (`DEFAULT_PRIVILEGES`), not `require_admin` — a regular user is authenticated but capability-limited by their `privileges` dict rather than blocked outright.

---

## 6. The `_require_admin_user` pattern — `routes/auth_routes.py`

### 6.1 Full verbatim source

```python
# routes/auth_routes.py — inside the router factory, ~lines 84-110
def _get_current_user(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    return auth_manager.get_username_for_token(token)


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
    every require_admin route already honors. Without this, Settings
    saves, integrations CRUD and the Users panel all 403 in the mode the
    app ships in.
    """
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return None
    user = _get_current_user(request)
    if not user or not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")
    return user
```

The critical property: `_get_current_user` reads `request.cookies.get(SESSION_COOKIE)` and resolves the username **directly from the session store**, `auth_manager.get_username_for_token(token)`. It never reads `request.state.current_user`. This matters because `request.state.current_user` can be populated by paths that are *not* "a human proved their password" — the internal-tool token path and `LOCALHOST_BYPASS` both set it, and both are reachable via direct loopback without any credentials.

### 6.2 The endpoint this protects

```python
# routes/auth_routes.py:275-278
# Admin-only routes
@router.get("/users")
async def list_users(request: Request):
    _require_admin_user(request)
    return {"users": auth_manager.list_users()}
```

### 6.3 The security history — verbatim commit message

The pattern was introduced by commit `3d5570a` (`fix(auth): admin routes work in the no-login desktop mode`). Its message documents both the *original* bug it was fixing (admin routes 403'ing in desktop mode) and the *regression it caught in its own first draft* — a wholesale delegation to the shared `require_admin` helper, which turned out to leak the endpoint to unauthenticated loopback callers:

> The macOS bundle launcher ships AUTH_ENABLED=false ("The desktop bundle serves 127.0.0.1 only"), but auth_routes' 14 admin endpoints did their own `if not user or not auth_manager.is_admin(user)` check instead of honoring that mode. With auth CONFIGURED (users exist) but DISABLED — exactly the state the shipped app runs in — every one of them 403'd: Settings saves, integrations CRUD, feature toggles, and the whole Users panel were dead in the mode the app ships in. Found while setting a default model: the UI's save silently failed with 403.
>
> Adds a local `_require_admin_user()` gate: the strict cookie-validating check, unchanged, plus the one missing allowance for AUTH_ENABLED=false. GET /settings uses the same gate so desktop mode sees full settings rather than a scrubbed copy.
>
> **NOT a wholesale delegation to core.middleware.require_admin. That was the first attempt and it introduced a real leak: require_admin trusts request.state.current_user, which the auth middleware populates through loopback/bypass paths, so on a direct loopback request GET /api/auth/users went from 403 to 200 for an UNAUTHENTICATED caller — returning usernames and privilege flags. Caught by A/B-ing the built server against main (baseline 403 vs patched 200) before it ever left the branch. These routes must resolve identity from the session cookie itself.**
>
> Tests: desktop mode allows admin routes; auth-enabled + unauthenticated still rejects; and a regression test that STAMPS an admin identity onto request.state exactly as the loopback middleware does, presents no cookie, and requires 403 anyway. test_auth_regressions' fake request gained the attributes a real Starlette Request always carries (assertions unchanged).
>
> Verified live in both modes against a copy of the real auth config: AUTH_ENABLED=false -> 200 and persisted; AUTH_ENABLED=true unauthenticated -> 403 on users, integrations and settings, with settings untouched. Full suite: 2076 passed.

**Security rationale, stated plainly**: `core.middleware.require_admin` is correct and safe for the routes that use it, because those routes only need "is *some* trusted-enough caller present" (which loopback/internal-tool/bypass all satisfy). But `GET /api/auth/users` (and the router's other admin endpoints — user creation, privilege edits, rename, signup toggle, integrations CRUD, settings write) return **credential-adjacent data** (usernames, privilege flags, integration secrets, full settings) — exactly the kind of response an unauthenticated caller reaching the app over loopback (e.g. a script, another local process, or in a shared/multi-tenant loopback scenario) should never receive. The fix is two independent, additive requirements: (1) identity must come from an actual validated session cookie — not from any `request.state` value a middleware bypass path could have set — and (2) the *only* exception preserved is the explicit, intentional `AUTH_ENABLED=false` desktop-mode bypass, which is a deliberate operator choice (not an incidental loopback artifact) and is honored identically to every other admin route in the app.

### 6.4 The regression test

`tests/test_settings_desktop_mode.py::test_admin_routes_do_not_trust_middleware_state` reproduces exactly the leak scenario: it stamps `request.state.current_user = "antman"` and `request.state.internal_tool = False` onto a fake request — precisely what the loopback middleware path would do — sends **no cookie**, and asserts `GET /api/auth/users`, `GET /api/auth/integrations`, and `POST /api/auth/settings` all still return `403`. This pins the fix as a permanent regression guard: any future refactor that makes `_require_admin_user` (or its replacement) trust `request.state` instead of the cookie will fail this test immediately.

---

## 7. API tokens (bearer auth) — distinct from LLM provider keys

Apollo has its own bearer-token mechanism for external/programmatic integrations (n8n, Make, custom scripts), entirely separate from LLM provider API keys (OpenAI/Anthropic/etc. credentials, which live in `src/integrations.py` / `src/endpoint_resolver.py` / model-endpoint configs and are sent *outbound* as `Authorization: Bearer <provider key>` to upstream LLM APIs — out of scope for this document).

### 7.1 Storage — `core/database.py`, `ApiToken` model

```python
# core/database.py:425-436
class ApiToken(TimestampMixin, Base):
    """API tokens for external integrations (n8n, Make, etc.)."""
    __tablename__ = "api_tokens"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    token_hash = Column(String, nullable=False)
    token_prefix = Column(String, nullable=False)  # first 8 chars for display
    scopes = Column(String, nullable=False, default="chat")
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime, nullable=True)
```

Unlike sessions (flat JSON), API tokens live in the SQL database, and only a bcrypt hash + display prefix are stored — the raw token is shown to the admin exactly once, at creation time.

### 7.2 Issuance — admin-only

```python
# routes/api_token_routes.py:52-83
@router.post("/tokens")
def create_token(request: Request, name: str = Form("")):
    require_admin(request)
    name = name.strip()[:MAX_NAME_LEN]
    if not name:
        raise HTTPException(400, "Token name is required")
    owner = get_current_user(request)

    raw_token = "ody_" + secrets.token_urlsafe(32)
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
    token_id = str(uuid.uuid4())[:8]

    with get_db_session() as db:
        db.add(ApiToken(
            id=token_id,
            owner=owner,
            name=name,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            scopes=DEFAULT_SCOPES,
            is_active=True,
        ))
    _invalidate_cache(request)

    return {
        "id": token_id,
        "name": name,
        "owner": owner,
        "token": raw_token,
        "token_prefix": raw_token[:8],
        "scopes": DEFAULT_SCOPES.split(","),
    }
```

Tokens are prefixed `ody_` — a leftover from the project's former name, "Odysseus" (renamed to Apollo per commit `374f575`); the prefix is load-bearing (it's how `AuthMiddleware` recognizes an Apollo API token vs. any other bearer scheme) and should be preserved verbatim in a recreation for compatibility with any client already issued a token.

### 7.3 Verification — `AuthMiddleware`, bucketed-prefix cache

```python
# app.py:331-386 (abridged)
auth_header = request.headers.get("authorization", "")
if auth_header.startswith("Bearer ody_"):
    raw_token = auth_header[7:]
    if len(raw_token) < 12 or len(raw_token) > 100:
        return JSONResponse(status_code=401, content={"error": "Invalid API token"})
    prefix = raw_token[:8]
    try:
        if app.state._token_cache_dirty:
            async with _token_cache_lock:
                if app.state._token_cache_dirty:
                    await _asyncio.to_thread(_refresh_token_cache)
        candidates = list(_token_cache.get(prefix, ()))
        matched_id = None
        matched_owner = None
        matched_scopes = []
        for tid, thash, owner, scopes in candidates:
            if _bcrypt.checkpw(raw_token.encode(), thash.encode()):
                matched_id = tid
                matched_owner = owner
                matched_scopes = scopes or []
                break
        if matched_id:
            request.state.current_user = "api"
            request.state.internal_tool = False
            request.state.auth_mode = "api_token"
            request.state.api_token = True
            request.state.api_token_id = matched_id
            request.state.api_token_owner = matched_owner
            request.state.api_token_scopes = matched_scopes
            return await call_next(request)
    except Exception:
        logger.warning("API token auth error", exc_info=False)
    # Invalid bearer token — reject immediately
    return JSONResponse(status_code=401, content={"error": "Invalid API token"})
```

An in-memory cache keyed by the 8-char prefix avoids a full-table bcrypt scan per request — the 8-char prefix narrows candidates, and `bcrypt.checkpw` (constant-time-ish by construction) is only run against the small bucket of tokens sharing that prefix. `request.state.current_user` is always the literal string `"api"` for a token-authenticated request (not the token owner's username) — routes that need to know *who issued the token* read `request.state.api_token_owner` separately; `current_user = "api"` is one of the reserved usernames (§1.1) precisely so a real signed-up user could never collide with this sentinel.

### 7.4 Scoped endpoint example

```python
# routes/webhook_routes.py:234-241
@router.post("/v1/chat")
async def sync_chat(request: Request, body: SyncChatRequest):
    if not getattr(request.state, "api_token", False):
        raise HTTPException(403, "This endpoint requires an API token")
    scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    if "chat" not in scopes:
        raise HTTPException(403, "API token is not scoped for chat")
    token_owner = getattr(request.state, "api_token_owner", None)
```

This endpoint explicitly **requires** `request.state.api_token` — a cookie-authenticated browser session cannot call it, even as an admin — and additionally checks the token's `scopes` string (comma-separated, `DEFAULT_SCOPES` defaults to `"chat"`), so a token can be issued that's only good for a subset of the API. Management endpoints (`GET`/`POST /api/tokens`, `DELETE /api/tokens/{id}`) are themselves gated by the shared `core.middleware.require_admin`.

Apollo also has an unrelated inbound-bearer surface, `routes/lmproxy_routes.py`, which maps a bearer token to the local-model OpenAI-compatible proxy used by Paperclip agents — guarded by its own shared token, not `AuthManager`/`ApiToken` at all; don't conflate the two when recreating this system.

---

## 8. First-run admin creation — `setup.py`

`setup.py` (project root) creates the first admin account **before** the app ever serves a request, writing directly to `auth.json` with raw `bcrypt` + `json.dump` — it does not go through `AuthManager` at all, since at first-run time no `AuthManager` instance has anything to manage yet.

### 8.1 Interactive prompt

```python
# setup.py:80-104
def _prompt_admin_credentials():
    """Interactively ask for admin username and password when running in a terminal."""
    import getpass

    print()
    print("  Set up your admin account:")
    print("  (Press Enter to accept defaults)")
    print()

    username = input("  Username [admin]: ").strip().lower()
    if not username:
        username = "admin"

    while True:
        password = getpass.getpass("  Password: ")
        if not password:
            print("  Password cannot be empty.")
            continue
        confirm = getpass.getpass("  Confirm password: ")
        if password != confirm:
            print("  Passwords don't match. Try again.")
            continue
        break

    return username, password
```

### 8.2 Admin creation — env vars > interactive prompt > random password

```python
# setup.py:107-157 (abridged)
def create_default_admin():
    """Create an initial admin user if none exists."""
    auth_path = os.path.join(DATA_DIR, "auth.json")
    if os.path.exists(auth_path):
        print("  [skip] auth.json already exists")
        return "exists"

    try:
        import bcrypt
        import json

        # Priority: env vars > interactive prompt > random password
        username = os.getenv("APOLLO_ADMIN_USER", "").strip().lower()
        password = os.getenv("APOLLO_ADMIN_PASSWORD", "").strip()

        if username and password:
            pass  # Both provided via env — use them directly
        elif sys.stdin.isatty() and not os.getenv("APOLLO_SKIP_ADMIN_PROMPT"):
            username, password = _prompt_admin_credentials()
        else:
            # Non-interactive (Docker, CI) — fall back to generated password
            username = username or "admin"
            password = password or __import__("secrets").token_urlsafe(18)

        username = username or "admin"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        auth_data = {
            "users": {
                username: {
                    "password_hash": hashed,
                    "is_admin": True,
                }
            }
        }
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump(auth_data, f, indent=2)

        if sys.stdin.isatty() and not os.getenv("APOLLO_ADMIN_PASSWORD"):
            print(f"  [ok] Admin account created ({username})")
        else:
            print(f"  [ok] Initial admin user created ({username})")
            if not os.getenv("APOLLO_ADMIN_PASSWORD"):
                print(f"        Temporary password: {password}")
                print(f"        ** Change it after first login. Set APOLLO_ADMIN_PASSWORD to choose your own. **")
        return "created"
    except ImportError:
        print("  [warn] bcrypt not installed — skipping admin user creation")
        print("         Run: pip install bcrypt")
        return "skipped"
```

Precedence, in order: (1) `APOLLO_ADMIN_USER` + `APOLLO_ADMIN_PASSWORD` env vars, used verbatim, no prompt; (2) an interactive TTY with no `APOLLO_SKIP_ADMIN_PROMPT` set — prompts via `getpass` (password never echoed, confirmed twice); (3) non-interactive with no env vars (Docker/CI) — username defaults to `admin`, password is a random `secrets.token_urlsafe(18)` printed once to stdout with an explicit instruction to change it. `DATA_DIR` resolves through `src/runtime_paths.py` — the same resolver behind `core/auth.py`'s `DEFAULT_AUTH_PATH` (§1.1), so `setup.py` and the running server always agree on `auth.json`'s location regardless of packaging (native Python venv, packaged `.app`, or a container). If `auth.json` already exists, `create_default_admin()` is a no-op (`return "exists"`) — it will never overwrite an existing admin account.

### 8.3 The in-app equivalent — `POST /api/auth/setup`

A parallel first-run path exists inside the running app itself, for deployments where `setup.py` isn't (or can't be) run ahead of time — e.g. a Docker image that starts serving before any operator interaction:

```python
# routes/auth_routes.py:112-124 (approximate — first-run setup endpoint)
@router.post("/setup")
async def setup(body: SetupRequest):
    if auth_manager.is_configured:
        raise HTTPException(400, "Already configured")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    auth_manager.setup(body.username, body.password)
    return {"ok": True}
```

This calls `AuthManager.setup()` (`core/auth.py` ~lines 188-193), which — unlike `setup.py`'s direct file write — goes through the normal `create_user(..., is_admin=True)` path and is gated purely by `auth_manager.is_configured` being `False` (i.e. zero users exist). It's the path `static/login.html` drives in its "first-run setup" UI mode, and it enforces a minimum 8-character password where the CLI `setup.py` prompt only enforces non-empty. **UNCERTAIN**: the exact `SetupRequest` field validation and whether `/api/auth/setup` also accepts an admin-privileges override were not independently re-verified against current `routes/auth_routes.py` line numbers beyond what's cited above — confirm against source before treating the line range as authoritative during implementation.
