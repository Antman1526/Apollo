"""Route tests for POST /api/review (adversarial reviewer)."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.endpoint_resolver as endpoint_resolver
import src.llm_core as llm_core
from routes.chat_routes import setup_chat_routes


def _make_client():
    # setup_chat_routes needs six manager/handler positional args; the
    # /api/review route uses none of them, so stubs are fine.
    router = setup_chat_routes(
        MagicMock(),  # session_manager
        MagicMock(),  # chat_handler
        MagicMock(),  # chat_processor
        MagicMock(),  # memory_manager
        MagicMock(),  # research_handler
        MagicMock(),  # upload_handler
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_CRITIQUE = (
    "Verdict: incorrect\n"
    "Issues:\n- The sky is blue, not green\n"
    "Suggestion: Say the sky appears blue due to Rayleigh scattering"
)


def test_review_returns_parsed_verdict_and_issues(monkeypatch):
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda prefix, owner=None: ("https://rev.example/v1/chat/completions", "rev-model", {"Authorization": "Bearer k"}),
    )

    async def _fake_llm(url, model, messages, **kwargs):
        assert model == "rev-model"
        return _CRITIQUE

    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm)

    client = _make_client()
    resp = client.post("/api/review", json={"question": "Is the sky green?", "answer": "Yes, always."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"].lower() == "incorrect"
    assert any("blue" in i for i in body["issues"])
    assert "Rayleigh" in body["suggestion"]
    assert body["model"] == "rev-model"
    assert body["raw"] == _CRITIQUE


def test_review_400_on_empty_answer(monkeypatch):
    # resolve_endpoint should not even be reached; guard runs first.
    client = _make_client()
    resp = client.post("/api/review", json={"question": "hi", "answer": "   "})
    assert resp.status_code == 400


def test_review_400_when_no_model_configured(monkeypatch):
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda prefix, owner=None: (None, None, None),
    )
    client = _make_client()
    resp = client.post("/api/review", json={"question": "q", "answer": "a"})
    assert resp.status_code == 400
