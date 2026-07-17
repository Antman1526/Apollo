"""Shared auth helpers used by all route files."""

import os
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, HTTPException


@dataclass(frozen=True)
class RequestIdentity:
    """The authenticated principal and the canonical owner for one request.

    ``principal`` preserves the credential-facing identity used by legacy code
    and audit trails. ``owner`` is the human account that owns persisted data;
    it is deliberately ``None`` for an ownerless API token so that credential
    can never inherit another account's records.
    """

    principal: Optional[str]
    owner: Optional[str]
    auth_mode: str
    is_authenticated: bool
    is_admin: bool
    is_local_bypass: bool


def _auth_manager(request: Request):
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    return getattr(state, "auth_manager", None)


def _is_loopback(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = (getattr(client, "host", None) or "").lower()
    return host in {"127.0.0.1", "::1", "localhost"}


def _is_admin(auth_manager, username: Optional[str]) -> bool:
    is_admin = getattr(auth_manager, "is_admin", None)
    if not username or not callable(is_admin):
        return False
    try:
        return bool(is_admin(username))
    except Exception:
        return False


def resolve_identity(request: Request) -> RequestIdentity:
    """Resolve all request identity state in one place.

    Feature routes should use :func:`effective_user` or :func:`require_user`
    for ownership decisions instead of reading request-state fields directly.
    """

    state = getattr(request, "state", None)
    principal = getattr(state, "current_user", None)
    auth_manager = _auth_manager(request)

    # This marker is written only after the loopback internal-tool token was
    # validated. Do not infer tool authority from a username string.
    if getattr(state, "internal_tool", False):
        owner = getattr(state, "internal_tool_owner", None) or None
        return RequestIdentity(
            principal=principal or "internal-tool",
            owner=owner,
            auth_mode="internal_tool",
            is_authenticated=True,
            is_admin=True,
            is_local_bypass=False,
        )

    if getattr(state, "api_token", False):
        owner = getattr(state, "api_token_owner", None) or None
        return RequestIdentity(
            principal=principal or "api",
            owner=owner,
            auth_mode="api_token",
            is_authenticated=True,
            is_admin=_is_admin(auth_manager, owner),
            is_local_bypass=False,
        )

    if principal:
        return RequestIdentity(
            principal=principal,
            owner=principal,
            auth_mode="cookie",
            is_authenticated=True,
            is_admin=_is_admin(auth_manager, principal),
            is_local_bypass=False,
        )

    if _auth_disabled():
        return RequestIdentity(None, None, "auth_disabled", False, False, True)

    configured = bool(auth_manager and getattr(auth_manager, "is_configured", False))
    if _is_loopback(request) and not configured:
        return RequestIdentity(None, None, "first_run_loopback", False, False, True)
    if _is_loopback(request) and os.getenv("LOCALHOST_BYPASS", "false").lower() == "true":
        return RequestIdentity(None, None, "localhost_bypass", False, False, True)
    return RequestIdentity(None, None, "anonymous", False, False, False)


def get_current_user(request: Request) -> Optional[str]:
    """Return the compatibility principal set by the auth middleware."""
    return resolve_identity(request).principal


def effective_user(request: Request) -> Optional[str]:
    """Return the canonical human owner for data ownership decisions."""
    return resolve_identity(request).owner


def _auth_disabled() -> bool:
    """True when the operator has explicitly turned off auth via .env.
    Mirrors the AUTH_ENABLED parse in app.py / core/middleware.py so the
    three call sites agree on what "off" means."""
    return os.getenv("AUTH_ENABLED", "true").lower() == "false"


def require_user(request: Request) -> str:
    """FastAPI dependency: reject unauthenticated callers when the upstream
    auth middleware was bypassed unexpectedly (e.g. SSRF from a sibling
    service). Returns the resolved username, or "" in single-user / anonymous
    modes where no username is available.

    The three "" cases are:
      1. AUTH_ENABLED=false — the operator explicitly turned auth off.
         The full /login flow is skipped (issue #622), so route-level
         require_user must let the request through too instead of 401-ing
         and forcing the browser to /login.
      2. Unconfigured first-run + loopback caller — pre-setup access from
         localhost so the operator can hit the SPA before creating the
         first admin.
      3. LOCALHOST_BYPASS=true + loopback caller — documented dev bypass.

    Use this on routes that touch user data so middleware misconfig can't
    open them up.
    """
    identity = resolve_identity(request)
    if identity.owner:
        return identity.owner
    # Operator-disabled auth: honor it at the route layer too. Without this,
    # routes that depend on require_user 401, the front-end fetch wrapper
    # redirects to /login, and the user sees a login page despite
    # AUTH_ENABLED=false (issue #622). Docker / reverse-proxy deployments
    # hit this because requests arrive from a non-loopback client.host, so
    # the loopback fall-through below never fires.
    if identity.auth_mode in {"auth_disabled", "first_run_loopback", "localhost_bypass", "internal_tool"}:
        return ""
    raise HTTPException(401, "Not authenticated")


def require_privilege(request: Request, key: str) -> str:
    """Reject callers whose `auth.json` privilege flag for `key` is False.
    Returns the username so the route handler can keep using it.

    Admins always have every privilege via `auth_manager.get_privileges`
    (which returns ADMIN_PRIVILEGES wholesale), so this is a no-op for
    them. In unauthenticated single-user mode (`require_user` returns ""),
    privileges aren't enforced.
    """
    user = require_user(request)
    if not user:
        return user
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return user
    try:
        privs = auth_mgr.get_privileges(user) or {}
    except Exception:
        return user
    # True = permitted; missing key defaults to permitted (unknown privileges
    # fail open — the UI gates display-side).
    if not privs.get(key, True):
        raise HTTPException(403, f"Your account is not allowed to {key.replace('_', ' ')}.")
    return user


def owner_filter(query, model_cls, user: str, *, include_shared: bool = True):
    """Filter `query` so only rows owned by `user` (and optionally null-owner
    'shared' rows) come through. No-op when `user` is empty (single-user
    mode). Returns the modified query."""
    if not user:
        return query
    if include_shared:
        return query.filter((model_cls.owner == user) | (model_cls.owner == None))  # noqa: E711
    return query.filter(model_cls.owner == user)
