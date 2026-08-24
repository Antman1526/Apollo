"""Regression: feature-route _owner uses the canonical owner, not the 'api'
compatibility principal. An API-token request has principal 'api' but a real
api_token_owner; using get_current_user collapsed every token owner into one
shared 'api' bucket (cross-tenant data leak)."""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTES = ["memory_routes", "note_routes", "task_routes", "skills_routes"]


@pytest.mark.parametrize("mod", ROUTES)
def test_owner_helper_uses_effective_user(mod):
    src = (ROOT / "routes" / f"{mod}.py").read_text()
    # The _owner helper body must call effective_user, never get_current_user.
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_owner":
            calls = [n.func.id for n in ast.walk(node)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
            assert "effective_user" in calls, f"{mod}._owner must call effective_user"
            assert "get_current_user" not in calls, \
                f"{mod}._owner must NOT call get_current_user (api-principal bug)"
            found = True
    assert found, f"no _owner function found in {mod}"


def test_effective_user_resolves_api_token_owner():
    """effective_user returns the real token owner, get_current_user the 'api' literal."""
    from types import SimpleNamespace
    from src import auth_helpers

    req = SimpleNamespace(state=SimpleNamespace(
        current_user="api", api_token=True, api_token_owner="alice",
        internal_tool=False))
    # get_current_user (principal) = "api"; effective_user (owner) = "alice"
    assert auth_helpers.get_current_user(req) == "api"
    assert auth_helpers.effective_user(req) == "alice"
