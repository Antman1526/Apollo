# Apollo — Security Implementation & Best Practices

Apollo is a self-hosted AI workspace with privileged local access — shell, files,
email, model serving. Its security model embraces that: the goal is not to stop a
logged-in admin (the README's framing is "treat it like an admin console"), it is to
stop everyone else. Start with the two policy documents in the repo root, then this doc
walks the implementation.

## 1. THREAT_MODEL.md and SECURITY.md in brief

`THREAT_MODEL.md` defines the trust boundary: **trusted users on a private network,
never public exposure.** What it *does* defend against: unauthenticated access,
non-admins reaching admin capabilities (shell/files/email/MCP/model
serving/vault/settings are admin-only — see the role table; enforcement in
`core/auth.py:DEFAULT_PRIVILEGES` and `src/tool_security.py:NON_ADMIN_BLOCKED_TOOLS`,
plus a blanket block on any `mcp__*` tool for non-admins), prompt injection acting on
the agent (`src/prompt_security.py:untrusted_context_message` wraps web results,
emails, memories, etc. as data, not instructions), and internal services (ChromaDB,
Ollama, SearXNG) being reachable from outside the host. It also documents four **known
gaps**: no shell/filesystem sandbox (#1058), SSRF via `/api/v1/chat` `base_url` (fix in
PR #1039), partial `src/search/` consolidation, and coarse token scopes.

`SECURITY.md` is deployment guidance: keep `AUTH_ENABLED=true` and
`LOCALHOST_BYPASS=false` on anything network-accessible, `SECURE_COOKIES=true` behind
HTTPS, put Apollo behind Tailscale/Cloudflare Access/VPN, keep the internal-only ports
internal (Apollo 7000, SearXNG 8080, ntfy 8091, ChromaDB 8100, Ollama 11434), and a
pre-fork checklist (`git check-ignore` for secrets files plus a `git grep` regex for
leaked keys).

## 2. Authentication model

- **Cookie sessions** — bcrypt-hashed passwords; 7-day session tokens stored
  atomically in `data/sessions.json` (`core/atomic_io.py`). `AuthMiddleware` in
  `app.py` validates the cookie on every non-exempt request and stamps
  `request.state.current_user`. `validate_token` re-checks the user record exists, so
  deleting a user kills their live sessions.
- **2FA (TOTP)** — `core/auth.py` uses `pyotp`: `totp_generate_secret` stores a
  *pending* secret, `totp_confirm_enable(code)` activates it, provisioning URI issuer
  is "Apollo", plus 8 single-use backup codes. Verified after password, before session
  issuance; `tests/test_totp_failclosed.py` pins fail-closed behavior.
- **Single-user mode** — `AUTH_ENABLED=false` disables the middleware entirely
  (`require_admin` also returns early). Intended only for an isolated localhost
  install.
- **Localhost bypass** — `LOCALHOST_BYPASS=true` skips login for *direct* loopback
  connections only. The subtlety is tunnels: cloudflared/nginx connect FROM 127.0.0.1,
  so a naive check would grant every tunneled visitor local trust. `app.py` therefore
  requires loopback **and** the absence of any proxy-forwarding header:

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

  Bypass requests act as a real account (`_bypass_user()`: prefer an admin, else first
  user) so ownership-scoped routes work instead of 403-ing; a warning is logged at
  startup whenever the flag is on.

## 3. Token classes

| Token | Format / storage | Guards |
|---|---|---|
| API tokens | `"ody_" + secrets.token_urlsafe(32)` (`routes/api_token_routes.py`); bcrypt **hash** + 8-char prefix in DB | All `/api/*` via `Authorization: Bearer ody_...` |
| Paperclip proxy token | `~/.apollo/paperclip_proxy_token`, `chmod 0o600` | `/lmproxy/v1/*` |
| Per-agent tokens | `"pa-" + secrets.token_hex(24)`; `~/.apollo/paperclip_agent_tokens.json`, 0600 | `/lmproxy/v1/*` with attribution |
| Events ingest token | `PAPERCLIP_EVENTS_TOKEN` env | `POST /api/paperclip/events` |
| Internal tool token | `secrets.token_hex(32)` per process, never persisted | loopback agent → admin routes |

**`ody_` API tokens** — `AuthMiddleware` length-sanity-checks the raw token, looks up
candidates by prefix from an in-memory cache (rebuilt only when a token is
created/revoked), and `bcrypt.checkpw`s each candidate. Matches run as the synthetic
user `"api"` with `api_token_owner`/`api_token_scopes` on request state — bearer
callers stay out of cookie-user routes.

**Per-agent `pa-` tokens** (`services/paperclip/agent_tokens.py`) — one token per
agent; minting again **rotates** the old one out; the list endpoint exposes metadata
only, never the secret:

```python
# services/paperclip/agent_tokens.py
token = "pa-" + secrets.token_hex(24)
with self._lock:
    # One token per agent: minting again rotates the old one out.
    self._tokens = {tok: meta for tok, meta in self._tokens.items()
                    if meta.get("agent_id") != agent_id}
    self._tokens[token] = {"agent_id": agent_id, "name": str(name or agent_id)}
...
def list(self):
    """Token metadata without the secrets (suffix only, for the UI)."""
    return [{..., "token_suffix": token[-6:]} for token, meta in self._tokens.items()]
```

Mint/list routes in `routes/paperclip_routes.py` are `require_admin`-gated.

**Events ingest guard** — `POST /api/paperclip/events` is session-auth-exempt and
proves identity itself. With a token set, constant-time compare; tokenless mode trusts
loopback only and explicitly rejects proxied requests, because `client.host` becomes
the proxy behind a reverse proxy:

```python
# routes/paperclip_routes.py (ingest_events)
if events_token:
    provided = request.headers.get("x-paperclip-events-token", "")
    if not hmac.compare_digest(provided, events_token):
        return JSONResponse({"detail": "invalid events token"}, status_code=401)
else:
    # Loopback-only trust is void behind a reverse proxy (client.host
    # becomes the proxy), so refuse proxied requests in tokenless mode.
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1") or request.headers.get("x-forwarded-for"):
        return JSONResponse({"detail": "remote ingest requires PAPERCLIP_EVENTS_TOKEN"},
                            status_code=401)
```

**Internal tool loopback token** (`core/middleware.py`) — agent tool calls loopback to
admin-gated HTTP routes without a session cookie:

```python
# core/middleware.py
INTERNAL_TOOL_TOKEN = os.environ.get("APOLLO_INTERNAL_TOKEN") or secrets.token_hex(32)
INTERNAL_TOOL_HEADER = "X-Apollo-Internal-Token"
```

`require_admin` accepts the header (via `secrets.compare_digest`) or
`request.state.current_user == "internal-tool"`. `AuthMiddleware` only honors the
header from a trusted loopback connection. Crucially, `internal-tool` is a **reserved
username** (`core/auth.py:RESERVED_USERNAMES`) — a real account by that name would pass
every `require_admin` check — and tool dispatch verifies the session owner is an admin
(`src/tool_security.py:owner_is_admin_or_single_user`) before issuing any loopback
call, so a non-admin's agent can't ride this path.

### Constant-time comparison

Every secret check uses `hmac.compare_digest` / `secrets.compare_digest`: the lmproxy
bearer check (`routes/lmproxy_routes.py:_resolve_actor` —
`hmac.compare_digest(token, expected)`), the events-token check above, the internal
tool token, and even the Node download checksum (§7). Plain `==` on secrets is treated
as a bug.

## 4. Reverse-proxy header hygiene

`services/paperclip/proxy.py` strips hop-by-hop headers per RFC 7230 §6.1 in both
directions — shared by the Paperclip proxy *and* the lmproxy:

```python
# services/paperclip/proxy.py
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
_DROP_REQUEST = _HOP_BY_HOP | {"host"}
# Let the response layer recompute framing/length from the streamed body.
_DROP_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding"}
```

The lmproxy additionally pops the inbound `Authorization` header before forwarding (the
warm llama-server needs no auth, and the bearer must not leak upstream). The
`/paperclip/{path}` websocket proxy authenticates the session cookie itself, because
websockets bypass `BaseHTTPMiddleware` (`ws_validate`, close code 1008 on failure).

## 5. XSS prevention

`static/js/paperclip.js` renders all live-event text through `escapeHTML`, and anything
that becomes a CSS class goes through an **allowlist**, not escaping:

```javascript
// static/js/paperclip.js
function escapeHTML(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function normalizeRole(role) {
  const token = String(role || 'coding').trim().toLowerCase().match(/[a-z0-9_-]+/)?.[0] || '';
  return ALLOWED_ROLES.has(token) ? token : 'coding';
}
```

`normalizeZone` is the same pattern for zones, so event payloads can never inject
arbitrary class names like `role-"><img onerror=...>`. Markup built from agent data
(`renderWorkspaceHTML`, bubbles, nameplates, board cards, transcripts) escapes every
interpolation; `tests/test_paperclip_floor_ui.mjs` asserts
`doesNotMatch(html, /<script/i)`. Defense in depth comes from
`core/middleware.py:SecurityHeadersMiddleware`: nonce-based CSP
(`script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net`), `X-Frame-Options:
DENY` + `frame-ancestors 'none'` (except sandboxed tool-render iframes), `nosniff`,
`Referrer-Policy: no-referrer`. `style-src 'unsafe-inline'` is a documented, accepted
residual (visual-only risk; see THREAT_MODEL.md).

## 6. SSRF considerations

Raw endpoint URLs make the server dial attacker-chosen hosts. Non-admin session changes
must therefore reference a *registered* endpoint row (already owner-scoped and
validated) instead of supplying a URL:

```python
# routes/session_routes.py
def _reject_raw_endpoint_url_for_non_admin(request, user, endpoint_id, endpoint_url):
    """Require registered endpoints for signed-in non-admin session changes."""
    if endpoint_id and endpoint_id.strip():
        return
    if not endpoint_url:
        return
    # Raw URLs make the server dial whatever host the request supplies. ...
    if user and not _current_user_is_admin(request, user):
        raise HTTPException(403, "Choose a registered model endpoint")
```

Related controls: outbound webhook URL validation (`tests/test_webhook_ssrf_resilience.py`,
`test_check_outbound_url_nonstring.py`, `test_url_safety.py`), and the acknowledged gap
that a chat-scoped API token could still pass `base_url` to `/api/v1/chat` (PR #1039).
Endpoint probing in `routes/model_routes.py` only dials URLs already saved by an
admin/owner, and `local://` URLs are never HTTP-dialed.

## 7. Supply chain: the Node bootstrap

The desktop app auto-downloads a pinned Node from nodejs.org on first native launch
(`services/paperclip/node_bootstrap.py`). Two protections before anything is extracted:

```python
# services/paperclip/node_bootstrap.py
def _verify_download(url: str, path: str) -> None:
    base, _, filename = url.rpartition("/")
    with urllib.request.urlopen(f"{base}/SHASUMS256.txt", timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    expected = _expected_sha256(text, filename)
    if not expected:
        raise RuntimeError(f"no SHASUMS256 entry for {filename}")
    actual = _sha256_of(path)
    if not hmac.compare_digest(actual, expected):
        raise RuntimeError(f"Node download checksum mismatch for {filename}")
...
with tarfile.open(tmp) as t:
    t.extractall(dest_parent, filter="data")   # no symlink/path escapes
```

The SHA-256 of the downloaded archive is checked against nodejs.org's
`SHASUMS256.txt` (a tampered or truncated archive is never extracted), and tar
extraction uses Python's `filter="data"` to neutralize path-traversal/symlink tricks.
All downloads have explicit timeouts — `urlretrieve` (no timeout) is avoided on
purpose.

## 8. SQL safety

All application queries go through the SQLAlchemy ORM
(`db.query(Model).filter(Model.col == value)`) — parametrized by construction; no
request-derived string ever reaches `text()`. The f-strings that *do* appear in
`core/database.py` are migration helpers like
`conn.execute(f"PRAGMA table_info({table_name})")`, where `table_name` comes from a
hard-coded internal list during startup migrations — internal-only by design, never
user input. `PRAGMA foreign_keys=ON` is enforced per connection (see doc 13 §6), so
delete cascades actually fire under SQLite.

## 9. Admin gating for MCP servers

Registering an MCP server is code execution, and the route says so:

```python
# routes/mcp_routes.py (add_server)
"""Add a new MCP server config and attempt connection. Admin-only:
registering a stdio server is equivalent to executing arbitrary
binaries on the host."""
require_admin(request)
```

Every mutating MCP route (`add_server`, update, delete, enable/disable, OAuth, tool
toggles — and even `list_servers`, since configs may embed env secrets) calls
`require_admin`. The same reasoning gates shell, file, email, and model-serving routes;
non-admin agents additionally have all `mcp__*` tools blocked at the tool-dispatch
layer.

## 10. Secrets handling

- **`.env` is untracked** (`.gitignore`: `.env`, `.env.bak.*`, with `!.env.example`
  allowed), as are `data/` and `logs/`. The pre-fork checklist in `SECURITY.md`
  verifies this plus greps for key-shaped strings.
- **Generated secrets live in `~/.apollo` with mode 0600** —
  `services/paperclip/config.py:_read_or_make_secret` creates
  `~/.apollo/paperclip_secret` (BETTER_AUTH_SECRET) and
  `~/.apollo/paperclip_proxy_token` with `secrets.token_hex(32)` + `os.chmod(path,
  0o600)`; `agent_tokens.py` does the same for its JSON registry. Env vars override
  the files (`PAPERCLIP_AUTH_SECRET`, `PAPERCLIP_PROXY_TOKEN`).
- **DB encryption at rest** — sensitive columns use the `EncryptedText` TypeDecorator
  (`core/database.py`), Fernet-encrypted via `src/secret_storage` with an `enc:`
  prefix; legacy plaintext rows migrate on next write.
- The internal tool token is never persisted; per-process only.
- Logs avoid secrets: token auth failures log without the token
  (`logger.warning("API token auth error", exc_info=False)`), and agent-token listings
  expose only the last 6 characters.
