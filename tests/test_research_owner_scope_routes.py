"""Route-level owner-scope tests for persisted research reports."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.real_modules import import_real_module

import_real_module("src.endpoint_resolver")
from routes.research_routes import setup_research_routes


def _request(user: str):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _owned_token_request(owner: str):
    return SimpleNamespace(
        state=SimpleNamespace(current_user="api", api_token=True, api_token_owner=owner),
    )


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def _write_research(data_dir, session_id: str, **data):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{session_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _research_handler():
    handler = MagicMock()
    handler._active_tasks = {}
    return handler


def _research_data_dir(tmp_path, monkeypatch):
    root = tmp_path / "apollo-data"
    monkeypatch.setenv("APOLLO_DATA_DIR", str(root))
    return root / "deep_research"


def test_library_returns_only_caller_owned_unarchived_reports(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    _write_research(data_dir, "alice-live", owner="alice", query="Alice", completed_at=30)
    _write_research(data_dir, "alice-archived", owner="alice", query="Archived", archived=True)
    _write_research(data_dir, "bob-live", owner="bob", query="Bob", completed_at=40)
    _write_research(data_dir, "legacy-null", query="Legacy", completed_at=50)

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/library", "GET")

    out = asyncio.run(target(
        request=_request("alice"),
        search=None,
        sort="recent",
        limit=50,
        archived=False,
    ))

    assert [item["id"] for item in out["research"]] == ["alice-live"]
    assert out["total"] == 1


def test_detail_rejects_cross_owner_and_null_owner_reports(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    _write_research(data_dir, "bob-report", owner="bob", result="bob secret")
    _write_research(data_dir, "legacy-report", result="legacy secret")

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")

    for session_id in ("bob-report", "legacy-report"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(target(session_id=session_id, request=_request("alice")))
        assert exc.value.status_code == 404


def test_report_rejects_null_owner_before_generating_html(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    _write_research(data_dir, "legacy-report", result="legacy secret")

    handler = _research_handler()
    router = setup_research_routes(handler)
    target = _route(router, "/api/research/report/{session_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="legacy-report", request=_request("alice")))

    assert exc.value.status_code == 404
    handler.get_report_html.assert_not_called()


def test_archive_rejects_cross_owner_without_mutating_report(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    path = _write_research(data_dir, "bob-report", owner="bob", archived=False)

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}/archive", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="bob-report", request=_request("alice"), archived=True))

    assert exc.value.status_code == 404
    assert json.loads(path.read_text(encoding="utf-8"))["archived"] is False


def test_delete_rejects_cross_owner_without_unlinking_report(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    path = _write_research(data_dir, "bob-report", owner="bob", result="bob secret")

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}", "DELETE")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="bob-report", request=_request("alice")))

    assert exc.value.status_code == 404
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["result"] == "bob secret"


def test_owned_api_token_sees_the_same_library_as_its_cookie_owner(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    _write_research(data_dir, "alice-report", owner="alice", query="Alice", completed_at=1)
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/library", "GET")

    cookie = asyncio.run(target(request=_request("alice"), search=None, sort="recent", limit=50, archived=False))
    token = asyncio.run(target(request=_owned_token_request("alice"), search=None, sort="recent", limit=50, archived=False))

    assert token == cookie


def test_ownerless_api_token_cannot_read_research_library(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    _write_research(data_dir, "alice-report", owner="alice", query="Alice", completed_at=1)
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/library", "GET")
    request = SimpleNamespace(state=SimpleNamespace(current_user="api", api_token=True, api_token_owner=None))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(request=request, search=None, sort="recent", limit=50, archived=False))
    assert exc.value.status_code == 401


def test_auth_disabled_mode_explicitly_allows_legacy_unowned_reports(tmp_path, monkeypatch):
    data_dir = _research_data_dir(tmp_path, monkeypatch)
    _write_research(data_dir, "legacy-report", query="Legacy", completed_at=1)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/library", "GET")
    request = SimpleNamespace(state=SimpleNamespace(current_user=None))

    out = asyncio.run(target(request=request, search=None, sort="recent", limit=50, archived=False))

    assert [item["id"] for item in out["research"]] == ["legacy-report"]
