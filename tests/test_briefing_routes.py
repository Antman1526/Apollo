"""Route tests for GET /api/briefing/today."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.briefing_routes as briefing_routes
from routes.briefing_routes import setup_briefing_routes


@pytest.fixture(autouse=True)
def _reset_briefing_cache():
    briefing_routes._BRIEFING_CACHE.clear()
    yield
    briefing_routes._BRIEFING_CACHE.clear()


def _fake_email(owner):
    return [{"uid": "1", "from": "a@x", "subject": "Hi", "snippet": "", "date": "2026-09-16T07:00:00+00:00", "flag_read": False, "flag_star": False}]


def _fake_events(owner, tz):
    return [{"uid": "e1", "summary": "Standup", "dtstart": "2026-09-16T10:00:00+00:00", "dtend": "2026-09-16T10:15:00+00:00", "all_day": False}]


def _fake_notes(owner):
    return [{"id": "n1", "title": "Ship it", "note_type": "note", "items": [], "due_date": None, "pinned": True, "archived": False}]


def _fake_tasks(owner):
    return [{"id": "t1", "name": "Backup", "next_run": "2026-09-16T23:00:00+00:00", "status": "active"}]


def _make_client(**managers):
    router = setup_briefing_routes(**managers)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _freeze_now(monkeypatch):
    import datetime as real_datetime

    class _FixedDateTime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.datetime(2026, 9, 16, 9, 0, tzinfo=tz)

    monkeypatch.setattr(briefing_routes, "datetime", _FixedDateTime)


def test_200_with_all_sources_healthy(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)
    client = _make_client(
        email_manager=_fake_email, calendar_manager=_fake_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    resp = client.get("/api/briefing/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-16"
    assert body["counts"] == {"emails": 1, "events": 1, "notes": 1, "tasks": 1}
    assert body["warnings"] == []
    assert body["summary"] is None


def test_one_source_failing_returns_200_with_warning(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)

    def _boom(owner):
        raise RuntimeError("IMAP down")

    client = _make_client(
        email_manager=_boom, calendar_manager=_fake_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    resp = client.get("/api/briefing/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["emails"] == []
    assert "email: unavailable" in body["warnings"]
    assert body["counts"]["events"] == 1


def test_summary_1_calls_resolver_and_llm(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)
    monkeypatch.setattr(
        briefing_routes, "resolve_endpoint",
        lambda prefix, owner=None: ("https://rev.example/v1/chat/completions", "rev-model", {}),
    )

    async def _fake_call(url, model, messages, **kw):
        assert url == "https://rev.example/v1/chat/completions"
        assert model == "rev-model"
        return "You have one email, one event, one note, and one task today."

    monkeypatch.setattr(briefing_routes, "llm_call_async", _fake_call)

    client = _make_client(
        email_manager=_fake_email, calendar_manager=_fake_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    resp = client.get("/api/briefing/today?summary=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "You have one email, one event, one note, and one task today."


def test_summary_0_returns_none(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)

    def _should_not_be_called(prefix, owner=None):
        raise AssertionError("resolve_endpoint should not be called when summary=0")

    monkeypatch.setattr(briefing_routes, "resolve_endpoint", _should_not_be_called)

    client = _make_client(
        email_manager=_fake_email, calendar_manager=_fake_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    resp = client.get("/api/briefing/today?summary=0")
    assert resp.status_code == 200
    assert resp.json()["summary"] is None


def test_tz_offset_header_is_parsed_and_reaches_the_calendar_source(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)
    captured = {}

    def _capture_events(owner, tz):
        captured["tz"] = tz
        return []

    client = _make_client(
        email_manager=_fake_email, calendar_manager=_capture_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    resp = client.get("/api/briefing/today", headers={"X-Tz-Offset": "-420"})
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-09-16"
    assert captured["tz"] is not None
    assert captured["tz"].utcoffset(None) == timedelta(minutes=-420)


def test_missing_or_bad_tz_header_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)
    captured = {}

    def _capture_events(owner, tz):
        captured["tz"] = tz
        return []

    client = _make_client(
        email_manager=_fake_email, calendar_manager=_capture_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    resp = client.get("/api/briefing/today", headers={"X-Tz-Offset": "not-a-number"})
    assert resp.status_code == 200
    assert captured["tz"] is None
    # Out-of-range offsets (timezone() rejects >= 24h) are ignored, not a 500.
    resp = client.get("/api/briefing/today", headers={"X-Tz-Offset": "99999"})
    assert resp.status_code == 200
    assert captured["tz"] is None


def test_cache_avoids_refetching_each_source_within_ttl(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)
    calls = {"email": 0, "events": 0, "notes": 0, "tasks": 0}

    def _counted(name, fn):
        def _wrapped(*args):
            calls[name] += 1
            return fn(*args)
        return _wrapped

    client = _make_client(
        email_manager=_counted("email", _fake_email),
        calendar_manager=_counted("events", _fake_events),
        note_manager=_counted("notes", _fake_notes),
        task_scheduler=_counted("tasks", _fake_tasks),
    )
    r1 = client.get("/api/briefing/today")
    r2 = client.get("/api/briefing/today")
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls == {"email": 1, "events": 1, "notes": 1, "tasks": 1}
    assert r1.json()["counts"] == r2.json()["counts"]


def test_refresh_1_bypasses_the_cache(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _freeze_now(monkeypatch)
    calls = {"email": 0}

    def _counted_email(owner):
        calls["email"] += 1
        return _fake_email(owner)

    client = _make_client(
        email_manager=_counted_email, calendar_manager=_fake_events,
        note_manager=_fake_notes, task_scheduler=_fake_tasks,
    )
    client.get("/api/briefing/today")
    client.get("/api/briefing/today?refresh=1")
    assert calls["email"] == 2
