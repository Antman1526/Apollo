"""Request identity matrix for user, token, and local-access modes."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth_helpers import effective_user, get_current_user, require_user, resolve_identity


def _request(
    *,
    user=None,
    api_token=False,
    api_token_owner=None,
    internal_tool=False,
    internal_tool_owner=None,
    configured=True,
    host="203.0.113.10",
    is_admin=False,
):
    auth_manager = SimpleNamespace(
        is_configured=configured,
        is_admin=lambda candidate: bool(is_admin and candidate == "alice"),
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user=user,
            api_token=api_token,
            api_token_owner=api_token_owner,
            internal_tool=internal_tool,
            internal_tool_owner=internal_tool_owner,
        ),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager)),
        client=SimpleNamespace(host=host),
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"user": "alice", "is_admin": True}, ("alice", "alice", "cookie", True, True, False)),
        ({"user": "api", "api_token": True, "api_token_owner": "alice", "is_admin": True}, ("api", "alice", "api_token", True, True, False)),
        ({"user": "api", "api_token": True}, ("api", None, "api_token", True, False, False)),
        ({"user": "internal-tool", "internal_tool": True, "internal_tool_owner": "alice", "is_admin": True}, ("internal-tool", "alice", "internal_tool", True, True, False)),
        ({"configured": False, "host": "127.0.0.1"}, (None, None, "first_run_loopback", False, False, True)),
        ({"host": "127.0.0.1"}, (None, None, "localhost_bypass", False, False, True)),
        ({}, (None, None, "anonymous", False, False, False)),
    ],
)
def test_resolve_identity_matrix(monkeypatch, kwargs, expected):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    identity = resolve_identity(_request(**kwargs))

    assert (
        identity.principal,
        identity.owner,
        identity.auth_mode,
        identity.is_authenticated,
        identity.is_admin,
        identity.is_local_bypass,
    ) == expected


def test_auth_disabled_identity_allows_single_user_mode(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    identity = resolve_identity(_request())

    assert identity.auth_mode == "auth_disabled"
    assert identity.owner is None
    assert identity.is_local_bypass is True
    assert require_user(_request()) == ""


def test_ownerless_api_token_has_no_canonical_owner_and_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    request = _request(user="api", api_token=True)

    assert get_current_user(request) == "api"
    assert effective_user(request) is None
    with pytest.raises(HTTPException) as exc:
        require_user(request)
    assert exc.value.status_code == 401


def test_owned_api_token_uses_human_owner_for_owner_scoped_work(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    request = _request(user="api", api_token=True, api_token_owner="alice")

    assert get_current_user(request) == "api"
    assert effective_user(request) == "alice"
    assert require_user(request) == "alice"


def test_non_loopback_anonymous_request_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    with pytest.raises(HTTPException) as exc:
        require_user(_request(host="198.51.100.10"))
    assert exc.value.status_code == 401
