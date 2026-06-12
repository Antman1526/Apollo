# 06 — Authentication & Authorization

> Scope: how Apollo decides *who you are* (authentication) and *what you may
> do* (authorization). All references are `path:line` into the real tree at
> `/Users/Antman/Apollo`. Secrets are shown as `<REDACTED>`; this document
> copies no live tokens or password hashes.

Apollo's own framing (see `THREAT_MODEL.md`) is "treat it like an admin
console": it is built for **trusted users on a private network**, not public
exposure. A logged-in admin can run shell commands, read/write files, send
email, and control model serving. The auth layer's job is therefore narrow but
critical: keep unauthenticated callers out, and keep non-admins away from
admin-only capabilities.

---

## 1. The master switch — `AUTH_ENABLED`

Auth is toggled by a single env var, parsed in `app.py`:

```python
# app.py:153
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
LOCALHOST_BYPASS = os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"
```

- Default is **on** (`"true"`). Only the literal string `false` disables it.
- The same parse is duplicated, deliberately, in three places that must agree
  on what "off" means: the HTTP middleware (`app.py:153`),
  `core/middleware.py:require_admin` (`app.py`/middleware), and the route-layer
  helper `_auth_disabled()` in `src/auth_helpers.py:39-43`.
- When `AUTH_ENABLED=false`, the `AuthMiddleware` is **never added** to the app
  at all (`app.py:176` guards the whole block; the `else` at `app.py:391`
  just logs "Auth middleware disabled").

There is a BOM caveat noted in the source: a `.env` saved with a UTF-8 byte
order mark can yield a key like `﻿AUTH_ENABLED` instead of `AUTH_ENABLED`
(`app.py:33` comment), which would silently leave auth in its default-on state.

### LOCALHOST_BYPASS

`LOCALHOST_BYPASS=true` (default `false`) skips the login flow for **direct
loopback** callers. It is dev-only and logs a warning at startup
(`app.py:155-156`). Crucially it is gated by `_is_trusted_loopback()`
(`app.py:262-273`), which only returns true for a *direct* `127.0.0.1`/`::1`
connection carrying **none** of the proxy-forwarding headers
(`cf-connecting-ip`, `x-forwarded-for`, `forwarded`, …; `_PROXY_FWD_HEADERS`,
`app.py:251-254`). A remote visitor arriving through a Cloudflare tunnel
connects *from* loopback but carries those headers, so they cannot inherit
local trust.

When the bypass fires, the request is attributed to a real account via
`_bypass_user()` (`app.py:158-174`) — preferring an admin, then the first user,
then `""` — so ownership-scoped routes (sessions, documents) work instead of
403-ing on an empty identity.

---

## 2. The AuthMiddleware

Defined inside the `if AUTH_ENABLED:` block as
`class AuthMiddleware(BaseHTTPMiddleware)` (`app.py:276`), added at
`app.py:389`. Its `dispatch` runs a fixed precedence ladder for every request:

1. **Exempt paths** (`_is_auth_exempt`, `app.py:213-219`) — short-circuit
   before any auth. Exact set includes `/api/auth/setup`, `/api/auth/login`,
   `/api/auth/logout`, `/api/auth/status`, `/api/health`, `/login`, and the
   self-authenticating `/api/paperclip/events` ingest
   (`AUTH_EXEMPT_EXACT`, `app.py:177-193`). Prefix exemptions are `/static` and
   `/lmproxy` (the bearer-guarded local-model proxy)
   (`AUTH_EXEMPT_PREFIXES`, `app.py:199`). A regex pattern exempts per-task
   webhook URLs `^/api/tasks/[^/]+/webhook/[^/]+/?$` because the path itself is
   the credential (`AUTH_EXEMPT_PATTERNS`, `app.py:208-210`).

2. **Internal-tool loopback** (`app.py:283-304`) — the in-process agent tool
   layer HTTP-loopbacks to admin-gated routes. It presents
   `X-Apollo-Internal-Token` (`INTERNAL_TOOL_HEADER`); the middleware accepts it
   only with `secrets.compare_digest(...)` **and** `_is_trusted_loopback`:

   ```python
   # app.py:288-291
   from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN as _ITT
   _hdr = request.headers.get(INTERNAL_TOOL_HEADER)
   if _hdr and secrets.compare_digest(_hdr, _ITT) and _is_trusted_loopback(request):
   ```

   An optional `X-Apollo-Owner` header lets the loopback impersonate a real
   user *for attribution only* (it must exist in `auth_manager.users`), else
   the identity is the `"internal-tool"` sentinel (`app.py:296-303`).

3. **LOCALHOST_BYPASS** (`app.py:311-316`) — direct loopback only, acts as
   `_bypass_user()`.

4. **First-run / unconfigured** (`app.py:318-322`) — if no users exist,
   non-API paths redirect to `/login`, API paths return `401 Setup required`.

5. **Bearer API tokens** (`app.py:325-377`) — `Authorization: Bearer ody_…`.
   Tokens are `ody_` + 43 base64 chars; the prefix (first 8 chars) indexes an
   in-memory cache (`_token_cache`, `app.py:222`) of
   `prefix → [(id, bcrypt_hash, owner, scopes)]`, and the candidate is matched
   with `bcrypt.checkpw`. The cache is rebuilt lazily on a dirty flag
   (`_refresh_token_cache`, `app.py:238-251`) bumped by token create/revoke, so
   the DB isn't scanned per request. A matched bearer caller is stamped
   `current_user = "api"` with `api_token=True` plus `api_token_owner` /
   `api_token_scopes` on `request.state` (`app.py:368-373`). `last_used_at` is
   updated fire-and-forget off the hot path (`app.py:347-366`).

6. **Cookie session** (`app.py:380-388`) — the fallback. Reads the
   `apollo_session` cookie and calls `auth_manager.validate_token`. On failure,
   API paths get `401 Not authenticated`, everything else redirects to
   `/login`. On success the username is stamped onto `request.state.current_user`.

```python
# app.py:380-388
token = request.cookies.get(SESSION_COOKIE)
if not auth_manager.validate_token(token):
    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})
    return RedirectResponse(url="/login", status_code=302)
request.state.current_user = auth_manager.get_username_for_token(token)
request.state.api_token = False
```

---

## 3. Session cookie

The cookie name is a module constant:

```python
# routes/auth_routes.py:69
SESSION_COOKIE = "apollo_session"
```

It is imported into `app.py` (`app.py:149`) and into both WebSocket handlers.
Cookie attributes are set on successful login (`routes/auth_routes.py:135-147`):

```python
# routes/auth_routes.py:135-147
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
```

- `httponly=True` — not readable by JS, mitigating XSS token theft.
- `samesite="lax"` — CSRF mitigation for cross-site top-level navigations.
- `secure` is driven by `SECURE_COOKIES` (default off; `SECURITY.md` says set
  it to `true` behind HTTPS).
- Tokens are 64 hex chars (`secrets.token_hex(32)`, `core/auth.py:485`) and
  live in `data/sessions.json` with a 7-day TTL (`TOKEN_TTL`, `core/auth.py:43`).
  They are persisted atomically and lock-guarded
  (`_save_sessions`, `core/auth.py:130-138`).

---

## 4. Login / 2FA flow

`POST /api/auth/login` (`routes/auth_routes.py:117-149`):

1. Rate-limited to 15 requests / 60 s per client IP
   (`_login_limiter`, `routes/auth_routes.py:75`).
2. Password verified first via `auth_manager.verify_password`
   (bcrypt `checkpw`, `core/auth.py:64-65` / `465-469`).
3. If the user has 2FA enabled (`totp_enabled`, `core/auth.py:359-362`):
   - With no `totp_code` in the body, the handler returns
     `{"ok": False, "requires_totp": True}` so the client prompts for a code
     (`routes/auth_routes.py:124-126`).
   - With a code, `totp_verify` must pass or it `401`s
     (`routes/auth_routes.py:127-128`).
4. Only then is a session minted (`create_session`) and the cookie set.

### TOTP internals (`core/auth.py`)

- Secrets generated with `pyotp.random_base32()` and stored as
  `totp_secret_pending` until confirmed (`totp_generate_secret`,
  `core/auth.py:369-377`).
- Provisioning URI uses issuer `"Apollo"` (`core/auth.py:379-382`); the
  `/api/auth/2fa/setup` route renders it to a base64 QR PNG
  (`routes/auth_routes.py:193-208`).
- `totp_confirm_enable` verifies against the pending secret with
  `valid_window=1`, then promotes it and generates **8 single-use backup codes**
  (`secrets.token_hex(4)`, `core/auth.py:384-401`).
- `totp_verify` checks backup codes first (consuming them on use), then the
  TOTP. Note the **fail-closed** fix: if 2FA is enabled but the secret is
  missing (corrupt `auth.json`), it returns `False` rather than silently
  passing (`core/auth.py:403-425`).

Login attempts also hit `_signup_limiter` (3/300 s) and `_setup_limiter`
(3/300 s) on their respective routes (`routes/auth_routes.py:76-77`).

---

## 5. Admin account bootstrap (first run)

Handled by `create_default_admin()` in `setup.py:73-125`. If
`data/auth.json` already exists it is skipped. Otherwise the priority is
**env vars > interactive prompt > random password**:

```python
# setup.py:84-98
username = os.getenv("APOLLO_ADMIN_USER", "").strip().lower()
password = os.getenv("APOLLO_ADMIN_PASSWORD", "").strip()

if username and password:
    pass  # use them directly
elif sys.stdin.isatty() and not os.getenv("APOLLO_SKIP_ADMIN_PROMPT"):
    username, password = _prompt_admin_credentials()
else:
    # Non-interactive (Docker, CI) — generated password
    username = username or "admin"
    password = password or __import__("secrets").token_urlsafe(18)
```

In the non-interactive/generated case the temporary password is printed once
to stdout with a change-it warning (`setup.py:112-118`):

```python
# setup.py:116-118
if not os.getenv("APOLLO_ADMIN_PASSWORD"):
    print(f"        Temporary password: <REDACTED>")
    print(f"        ** Change it after first login. Set APOLLO_ADMIN_PASSWORD to choose your own. **")
```

(`start-macos.sh:142` and `launch-windows.ps1:118` document that first-run prints
this initial admin password.) The created record is written directly as
`{"users": {username: {"password_hash": <bcrypt>, "is_admin": True}}}`
(`setup.py:99-111`).

The runtime path has a parallel, lock-guarded first-run setup:
`AuthManager.setup()` (`core/auth.py:177-182`) only succeeds while
`is_configured` is false, guarded by `self._setup_lock` so two concurrent
`/api/auth/setup` calls can't both create an admin. The route enforces a
≥8-char password (`routes/auth_routes.py:90-91`).

---

## 6. Privilege model

### Default privileges (`core/auth.py:23-35`)

```python
DEFAULT_PRIVILEGES = {
    "can_use_agent": True,
    "can_use_browser": True,
    "can_use_bash": False,
    "can_use_documents": True,
    "can_use_research": True,
    "can_generate_images": True,
    "can_manage_memory": True,
    "max_messages_per_day": 0,
    "allowed_models": [],
}
```

Admins receive `ADMIN_PRIVILEGES` — every boolean forced true, ints zeroed,
lists emptied (`core/auth.py:38`). `get_privileges()` returns
`ADMIN_PRIVILEGES` wholesale for admins, else stored values merged over
`DEFAULT_PRIVILEGES` so newly added keys default sanely
(`core/auth.py:295-302`). `set_privileges()` refuses to modify an admin and
only accepts known keys (`core/auth.py:304-318`).

Note `can_use_bash` defaults **False** even for regular users — but the harder
gate for shell/file/email/etc. is at the tool layer, not here.

### Reserved usernames (`core/auth.py:57`)

```python
RESERVED_USERNAMES = frozenset({"internal-tool", "api", "demo", "system"})
```

`create_user` and `rename_user` refuse these (`core/auth.py:191-193`,
`core/auth.py:255-257`). The dangerous one is **`internal-tool`**: because the
cookie path stamps `current_user` to the raw username and
`require_admin` grants admin to any request whose `current_user ==
"internal-tool"`, a real account literally named `internal-tool` would silently
pass every admin gate (see the long comment at `core/auth.py:45-56`).

### `require_admin` (`core/middleware.py:20-46`)

```python
# core/middleware.py:31-41
hdr = request.headers.get(INTERNAL_TOOL_HEADER)
if hdr and secrets.compare_digest(hdr, INTERNAL_TOOL_TOKEN):
    return
if getattr(request.state, "current_user", None) == "internal-tool":
    return
...
if os.getenv("AUTH_ENABLED", "true").lower() == "false":
    return
if not auth_mgr or not auth_mgr.is_configured:
    raise HTTPException(403, "Admin only")
user = getattr(request.state, "current_user", None)
if not user or not auth_mgr.is_admin(user):
    raise HTTPException(403, "Admin only")
```

The `INTERNAL_TOOL_TOKEN` is a per-process random secret
(`secrets.token_hex(32)`, or `APOLLO_INTERNAL_TOKEN` if set) that is never
persisted or sent to clients (`core/middleware.py:15-16`).

### `require_privilege` / `require_user` (`src/auth_helpers.py`)

`require_user(request)` (`src/auth_helpers.py:46-89`) is a defence-in-depth
route dependency that re-rejects unauthenticated callers if the middleware was
somehow bypassed. It returns `""` (no enforcement) in three documented cases:
`AUTH_ENABLED=false`, unconfigured-first-run loopback, and `LOCALHOST_BYPASS`
loopback.

`require_privilege(request, key)` (`src/auth_helpers.py:91-119`):

```python
# src/auth_helpers.py:113-117
privs = auth_mgr.get_privileges(user) or {}
...
if not privs.get(key, True):
    raise HTTPException(403, f"Your account is not allowed to {key.replace('_', ' ')}.")
return user
```

Two important properties: it is a **no-op for admins** (their privileges are all
true) and for single-user/anonymous mode (`user == ""`), and it **fails open**
on unknown keys (`privs.get(key, True)`) — the UI is expected to gate display.

`effective_user(request)` (`src/auth_helpers.py:13-35`) resolves the real owner
for attribution: cookie sessions → logged-in user; bearer tokens → their
`api_token_owner` (so a paired companion client sees the SAME data as the
owner's desktop UI) rather than the sandboxed `"api"` pseudo-user.

### Tool-layer enforcement (the real privilege wall)

`require_privilege` covers UI-level feature gates; the high-risk capabilities
are blocked in `src/tool_security.py`. `NON_ADMIN_BLOCKED_TOOLS`
(`src/tool_security.py:14-46`) denies non-admins: `bash`, `python`,
`read_file`, `write_file`, `manage_memory/skills/tasks/endpoints/mcp/webhooks/
tokens/documents/settings`, `send_email`/`read_email`/`list_emails`,
`manage_calendar`, `vault_*`, and all model-serving tools. Any tool name
starting with `mcp__` is also blocked (`src/tool_security.py:54`). Admin status
is verified via `owner_is_admin_or_single_user` (`src/tool_security.py:57`)
before the agent issues its internal-tool loopback, so a non-admin agent
session cannot reach admin tools.

---

## 7. WebSocket authentication (outside the HTTP middleware)

`BaseHTTPMiddleware` does **not** run for WebSocket upgrades, so each WS handler
must authenticate the `apollo_session` cookie itself. Apollo wires this through
`build_and_include_router` callbacks in `app.py`, honoring `AUTH_ENABLED`.

### Paperclip sidecar proxy WS

Wired with a single validate callback (`app.py:730-740`):

```python
# app.py:734
ws_validate=lambda token: auth_manager.validate_token(token),
```

The handler (`routes/paperclip_routes.py:263-278`):

```python
# routes/paperclip_routes.py:264-272
async def proxy_ws(websocket, path: str):
    from routes.auth_routes import SESSION_COOKIE  # local import: avoid cycle
    token = websocket.cookies.get(SESSION_COOKIE)
    ok = ws_validate(token) if ws_validate is not None else bool(token)
    if not cfg.enabled or not ok:
        await websocket.close(code=1008)  # policy violation
        return
```

A valid session is required, then the connection is reverse-proxied to the
Paperclip upstream (the `http→ws` rewrite at `routes/paperclip_routes.py:275`).

The companion HTTP ingest `/api/paperclip/events` is exempted from session auth
(`AUTH_EXEMPT_EXACT`) but **self-authenticates**: when
`PAPERCLIP_EVENTS_TOKEN` is set the `X-Paperclip-Events-Token` header must match
via `hmac.compare_digest`; otherwise only direct loopback (no `x-forwarded-for`)
is accepted (`routes/paperclip_routes.py:98-108`).

### Embedded browser WS

This one needs **two** gates — a valid session *and* the `can_use_browser`
privilege. Both honor `AUTH_ENABLED` (`app.py:796-806`):

```python
# app.py:803-804
ws_validate=lambda token: (not AUTH_ENABLED) or auth_manager.validate_token(token),
ws_authorize=lambda token: (not AUTH_ENABLED) or _browser_ws_authorize(token),
```

`_browser_ws_authorize` (`app.py:780-793`) mirrors `require_privilege`'s
fail-open semantics — anonymous/single-user and missing-key both return true,
admins pass, otherwise it checks `privs.get("can_use_browser", True)`.

The handler runs both checks in order (`routes/browser_routes.py:287-304`):

```python
# routes/browser_routes.py:288-304
async def browser_ws(websocket: WebSocket):
    from routes.auth_routes import SESSION_COOKIE  # local import: avoid cycle
    token = websocket.cookies.get(SESSION_COOKIE)
    valid = ws_validate(token) if ws_validate is not None else True
    if not valid:
        await websocket.close(code=1008)  # policy violation
        return
    privileged = ws_authorize(token) if ws_authorize is not None else True
    if not privileged:
        await websocket.close(code=1008)
        return
    await websocket.accept()
```

The `(not AUTH_ENABLED) or …` shape is the key to the `AUTH_ENABLED=false`
story: in that mode the HTTP `AuthMiddleware` isn't installed at all, and there
is no session cookie to validate, so the WS callbacks short-circuit to `True`
and the stream is allowed — mirroring the HTTP behaviour exactly. With auth on,
the cookie must validate and (for the browser) the privilege must hold.

---

## 8. Account lifecycle security notes

- **Deleting a user** also revokes all their active sessions immediately
  (`delete_user`, `core/auth.py:209-235`) — otherwise a deleted user's cookie
  would keep working until natural expiry.
- **Orphan-session check:** both `validate_token` (`core/auth.py:493-516`) and
  `get_username_for_token` (`core/auth.py:518-543`) re-verify the user still
  exists on every call and drop the session if not, so a deleted account can't
  ride a still-valid cookie.
- **Renaming a user** rewrites every owner-scoped DB row (iterating
  `Base.registry.mappers` for models with an `owner` column) and per-user JSON
  prefs before changing auth, so the account keeps its data
  (`routes/auth_routes.py:284-330`).
- **Changing a password** revokes all *other* sessions but preserves the
  current one (`change_password` route, `routes/auth_routes.py:171-184`).
- **Open signup** is off by default (`signup_enabled`, `core/auth.py:165`); the
  `/api/auth/signup` route 403s unless an admin enabled it
  (`routes/auth_routes.py:101-102`).

---

## 9. Quick reference — env vars

| Var | Default | Effect | Source |
|---|---|---|---|
| `AUTH_ENABLED` | `true` | Master auth switch; `false` removes middleware | `app.py:153` |
| `LOCALHOST_BYPASS` | `false` | Skip login for direct loopback only | `app.py:154` |
| `SECURE_COOKIES` | `false` | `Secure` flag on session cookie | `routes/auth_routes.py:140` |
| `APOLLO_ADMIN_USER` | `admin` | First-run admin username | `setup.py:85` |
| `APOLLO_ADMIN_PASSWORD` | (generated) | First-run admin password; suppresses temp-password print | `setup.py:86` |
| `APOLLO_SKIP_ADMIN_PROMPT` | unset | Skip interactive admin prompt | `setup.py:90` |
| `APOLLO_INTERNAL_TOKEN` | (random) | Override per-process internal-tool token | `core/middleware.py:15` |
| `PAPERCLIP_EVENTS_TOKEN` | unset | Shared secret for `/api/paperclip/events` ingest | `routes/paperclip_routes.py:53` |

---

## 10. Residual risks (honest)

- `AUTH_ENABLED=false` removes *all* HTTP auth and makes both WS callbacks
  unconditionally allow — appropriate only for a single-user desktop on a
  trusted host. `SECURITY.md` flags keeping it `true` for any network exposure.
- The `internal-tool` sentinel is powerful by design; its safety rests on the
  reserved-username guard plus `_is_trusted_loopback`. Both must stay correct.
- Token scopes are coarse (`chat` vs `admin`) — there is no per-capability
  subsetting of a token below its owner's privileges (acknowledged in
  `THREAT_MODEL.md` "Known Gaps" #4).
- `require_privilege` fails open on unknown keys; new privileges must be added
  to `DEFAULT_PRIVILEGES` to be enforced server-side rather than only in the UI.
