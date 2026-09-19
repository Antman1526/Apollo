"""Agent tool policy must agree with the admin gate when auth is turned off.

The macOS launcher runs with AUTH_ENABLED=false. Requests then carry no
username (owner ""), and require_admin() lets them through as the single
local user. The tool policy used to consult only whether an admin account
*exists* (auth.json), so an install that had ever created one withheld
python, bash, memory, email, calendar, tasks, ... from its only user's agent.
"""
import importlib

from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, blocked_tools_for_owner


def _patch_auth(monkeypatch):
    # Resolve at test time: other tests swap core.auth in sys.modules, and
    # tool_security imports it lazily, so patch whatever module is current.
    monkeypatch.setattr(importlib.import_module("core.auth"), "AuthManager", lambda: _ConfiguredAuth())


class _ConfiguredAuth:
    is_configured = True

    def is_admin(self, username):
        return username == "admin"


def test_auth_disabled_is_single_user_even_with_an_admin_account(monkeypatch):
    _patch_auth(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert blocked_tools_for_owner("") == set()
    assert blocked_tools_for_owner(None) == set()


def test_auth_enabled_still_blocks_anonymous_and_non_admin(monkeypatch):
    _patch_auth(monkeypatch)
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    assert blocked_tools_for_owner("") == set(NON_ADMIN_BLOCKED_TOOLS)
    assert blocked_tools_for_owner("regular-user") == set(NON_ADMIN_BLOCKED_TOOLS)
    assert blocked_tools_for_owner("admin") == set()
