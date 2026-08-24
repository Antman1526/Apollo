"""Regression: admin auth-routes must work in the no-login desktop mode.

The macOS bundle launcher ships AUTH_ENABLED=false as the default desktop
experience. Every require_admin route honors that mode, but auth_routes'
admin endpoints did their own `if not user or not is_admin(user)` check —
which 403s when auth is CONFIGURED (users exist) but DISABLED, breaking
every Settings save, integrations CRUD, and the Users panel in exactly the
mode the app ships in. All sites now delegate to core.middleware's
require_admin, the single policy source.
"""
import importlib.util
import sys
from unittest.mock import MagicMock

import pytest


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "bcrypt", "cryptography"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+bcrypt+cryptography installed"
)


class _ConfiguredAuthManager:
    """Auth manager with users configured, mimicking a real desktop install."""
    is_configured = True
    users = {"antman": {"is_admin": True}}
    signup_enabled = False

    def is_admin(self, user):
        return user == "antman"

    def validate_session(self, token):
        return None

    def get_username_for_token(self, token):
        # No valid session cookie in these tests — the strict gate resolves
        # the user from the cookie itself, so this models "not logged in".
        return None

    def totp_enabled(self, user):
        return False

    def list_users(self):
        return [{"username": "antman", "is_admin": True}]


def _client(monkeypatch, settings_store):
    for mod in list(sys.modules):
        if mod in ("core.database",) or mod.startswith("sqlalchemy"):
            if isinstance(sys.modules[mod], MagicMock):
                sys.modules.pop(mod)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.auth_routes as ar

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(ar, "_load_settings", lambda: dict(settings_store))
    monkeypatch.setattr(ar, "_save_settings", lambda s: settings_store.update(s))

    app = FastAPI()
    mgr = _ConfiguredAuthManager()
    app.state.auth_manager = mgr
    result = ar.setup_auth_routes(mgr)
    router = result[0] if isinstance(result, tuple) else result
    app.include_router(router)
    return TestClient(app)


def test_settings_post_allowed_in_desktop_mode(monkeypatch):
    store = {"default_model": ""}
    c = _client(monkeypatch, store)
    r = c.post("/api/auth/settings", json={"default_model": "Qwen3-365-A3B-Fallback-Q5_K_M"})
    assert r.status_code == 200, r.text
    assert store["default_model"] == "Qwen3-365-A3B-Fallback-Q5_K_M"


def test_settings_get_unscrubbed_in_desktop_mode(monkeypatch):
    store = {"brave_api_key": "sk-secret"}
    c = _client(monkeypatch, store)
    r = c.get("/api/auth/settings")
    assert r.status_code == 200
    assert r.json().get("brave_api_key") == "sk-secret", "desktop mode must see full settings"


def test_users_list_allowed_in_desktop_mode(monkeypatch):
    c = _client(monkeypatch, {})
    r = c.get("/api/auth/users")
    assert r.status_code == 200
    assert r.json()["users"][0]["username"] == "antman"


def test_still_403_when_auth_enabled_and_unauthenticated(monkeypatch):
    """The fix must NOT open the admin surface when auth is actually on."""
    store = {}
    c = _client(monkeypatch, store)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    r = c.post("/api/auth/settings", json={"default_model": "x"})
    assert r.status_code == 403
    assert "default_model" not in store


def test_admin_routes_do_not_trust_middleware_state(monkeypatch):
    """Regression for a leak introduced while fixing the desktop-mode 403.

    The first attempt delegated these routes to core.middleware.require_admin,
    which trusts request.state.current_user — a value the auth middleware
    populates via loopback/bypass paths. On a direct loopback request that
    turned GET /api/auth/users from 403 into 200 for an UNAUTHENTICATED
    caller, leaking usernames and privilege flags.

    Here we STAMP that admin identity onto request.state exactly as the
    middleware would, present NO session cookie, and require that every
    admin surface still rejects — the gate must validate the cookie itself.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.auth_routes as ar

    monkeypatch.setenv("AUTH_ENABLED", "true")
    store = {}
    monkeypatch.setattr(ar, "_load_settings", lambda: dict(store))
    monkeypatch.setattr(ar, "_save_settings", lambda s: store.update(s))

    app = FastAPI()
    mgr = _ConfiguredAuthManager()
    app.state.auth_manager = mgr

    @app.middleware("http")
    async def _stamp_admin_identity(request, call_next):
        # Exactly what the loopback path in app.py does.
        request.state.current_user = "antman"
        request.state.internal_tool = False
        return await call_next(request)

    result = ar.setup_auth_routes(mgr)
    app.include_router(result[0] if isinstance(result, tuple) else result)
    c = TestClient(app)

    for path in ("/api/auth/users", "/api/auth/integrations"):
        assert c.get(path).status_code == 403, (
            f"{path} leaked to an unauthenticated caller carrying a "
            f"middleware-stamped identity"
        )
    assert c.post("/api/auth/settings", json={"default_model": "HACK"}).status_code == 403
    assert "default_model" not in store
