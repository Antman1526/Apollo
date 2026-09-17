"""Route tests for POST /api/council/ask (The Council)."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.council_routes as council_routes
from routes.council_routes import setup_council_routes


def _make_client():
    router = setup_council_routes(MagicMock())  # session_manager unused
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _members(n):
    return [{"model": f"model-{i}", "endpoint_url": f"http://ep{i}.example"} for i in range(n)]


def test_400_on_too_few_members(monkeypatch):
    client = _make_client()
    resp = client.post("/api/council/ask", json={"question": "q", "members": _members(1)})
    assert resp.status_code == 400


def test_400_on_empty_question(monkeypatch):
    client = _make_client()
    resp = client.post("/api/council/ask", json={"question": "   ", "members": _members(2)})
    assert resp.status_code == 400


def test_400_on_too_many_members(monkeypatch):
    client = _make_client()
    resp = client.post("/api/council/ask", json={"question": "q", "members": _members(5)})
    assert resp.status_code == 400


def test_200_with_expected_shape(monkeypatch):
    monkeypatch.setattr(
        council_routes, "resolve_ad_hoc_endpoint",
        lambda url: (url + "/chat/completions", {"Authorization": "Bearer k"}),
    )
    monkeypatch.setattr(
        council_routes, "resolve_endpoint",
        lambda prefix, owner=None: ("https://rev.example/v1/chat/completions", "rev-model", {}),
    )

    async def _fake_run_council(question, members, reviewer, **kw):
        assert question == "2+2?"
        assert len(members) == 2
        assert reviewer["model"] == "rev-model"
        return {
            "question": question,
            "answers": [
                {"model": m["model"], "text": "4", "error": None} for m in members
            ],
            "synthesis": {"model": "rev-model", "text": "CONSENSUS: 4", "sections": {"consensus": "4", "disagreements": "", "recommended": "4"}},
        }

    monkeypatch.setattr(council_routes, "run_council", _fake_run_council)

    client = _make_client()
    resp = client.post("/api/council/ask", json={"question": "2+2?", "members": _members(2)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "2+2?"
    assert [a["model"] for a in body["answers"]] == ["model-0", "model-1"]
    assert body["synthesis"]["model"] == "rev-model"
    assert body["synthesis"]["sections"]["recommended"] == "4"


def test_reviewer_unconfigured_falls_back_to_none(monkeypatch):
    monkeypatch.setattr(
        council_routes, "resolve_ad_hoc_endpoint",
        lambda url: (url, {}),
    )

    def _raise(prefix, owner=None):
        raise RuntimeError("no reviewer configured")

    monkeypatch.setattr(council_routes, "resolve_endpoint", _raise)

    async def _fake_run_council(question, members, reviewer, **kw):
        assert reviewer is None
        return {"question": question, "answers": [], "synthesis": None}

    monkeypatch.setattr(council_routes, "run_council", _fake_run_council)

    client = _make_client()
    resp = client.post("/api/council/ask", json={"question": "q", "members": _members(2)})
    assert resp.status_code == 200
    assert resp.json()["synthesis"] is None
