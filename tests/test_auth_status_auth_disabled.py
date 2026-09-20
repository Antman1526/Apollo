"""With AUTH_ENABLED=false the status route must call the local operator an
admin, or the frontend hides every admin-only settings section (Local
Models among them) from the desktop app's only user."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import ADMIN_PRIVILEGES


class _Auth:
    is_configured = True
    signup_enabled = False

    def status(self, token):
        return {"configured": True, "authenticated": False, "username": None, "is_admin": False}

    def get_privileges(self, u):
        return {}


def _client(monkeypatch):
    import routes.auth_routes as ar
    app = FastAPI()
    app.include_router(ar.setup_auth_routes(_Auth()))
    return TestClient(app)


def test_auth_disabled_reports_admin(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    d = _client(monkeypatch).get("/api/auth/status").json()
    assert d["is_admin"] is True
    assert d["privileges"] == ADMIN_PRIVILEGES
    assert d["authenticated"] is False  # no account session exists


def test_auth_enabled_unchanged(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    d = _client(monkeypatch).get("/api/auth/status").json()
    assert d["is_admin"] is False and "privileges" not in d
