"""Route-level checks for ``GET /api/home/brief`` (routes/home_routes.py).

The welcome screen's setup checklist / home brief rollup must:

* require a session when auth is enabled (no admin needed),
* return the documented shape on the happy path,
* degrade one failing section into ``errors`` while the rest still returns,
* stay owner-scoped (bob never sees alice's rows),
* never carry secret-looking keys.

Runs against a throwaway SQLite file with the real models, the same way
tests/test_caldav_writeback_route.py does. Skipped unless FastAPI +
SQLAlchemy are really installed.
"""
import importlib.util
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _has_real(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


_REAL = all(_has_real(m) for m in ("fastapi", "sqlalchemy", "bcrypt", "cryptography"))
pytestmark = pytest.mark.skipif(
    not _REAL, reason="needs real fastapi+sqlalchemy+bcrypt+cryptography installed"
)

_SECRET_KEY_RE = re.compile(r"(api_key|password|passwd|secret|token|credential|imap_|smtp_)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    import core.database as cdb
    import routes.home_routes as hr

    engine = create_engine(
        f"sqlite:///{tmp_path / 'home.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(hr, "SessionLocal", session_factory)
    hr.clear_cache()

    # Auth is enabled; the loopback/first-run bypasses must not fire.
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)

    # Keep the sidecar/model probes deterministic and offline.
    monkeypatch.setattr(hr, "_warm_model", lambda: "llama-3-8b")
    import services.searxng.runtime as searx_rt
    monkeypatch.setattr(searx_rt, "get_runtime", lambda: SimpleNamespace(status=lambda: "running"))
    import src.settings as settings_mod
    monkeypatch.setattr(
        settings_mod, "load_settings",
        lambda: {"search_provider": "searxng", "search_fallback_chain": ["duckduckgo"]},
    )
    # Urgency-state files are read relative to cwd under data/.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    return SimpleNamespace(TS=session_factory, hr=hr, tmp=tmp_path)


def _client(hr, user=None, *, configured=True, admin=False):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=configured, is_admin=lambda u: bool(admin))
    if user:
        @app.middleware("http")
        async def _auth(request, call_next):
            request.state.current_user = user
            return await call_next(request)
    app.include_router(hr.setup_home_routes())
    return TestClient(app)


def _seed(TS, owner, *, tmp=None, task_offset=timedelta(hours=1), task_status="active"):
    from core.database import (
        CalendarCal, CalendarEvent, EmailAccount, ModelEndpoint, Note, ScheduledTask,
    )

    now_local = datetime.now().replace(second=0, microsecond=0)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    suffix = uuid.uuid4().hex[:8]
    db = TS()
    try:
        db.add(ModelEndpoint(id=f"ep-{suffix}", owner=owner, name="Local", base_url="http://127.0.0.1:8002/v1", is_enabled=True))
        cal = CalendarCal(id=f"cal-{suffix}", owner=owner, name="Work", source="local")
        db.add(cal)
        db.flush()
        # A timed event later today (local-naive, legacy is_utc=False) plus an
        # event tomorrow that must NOT appear.
        db.add(CalendarEvent(uid=f"ev-{suffix}", calendar_id=cal.id, summary="Standup",
                             dtstart=now_local.replace(hour=23, minute=0) if now_local.hour < 23 else now_local,
                             dtend=now_local.replace(hour=23, minute=30) if now_local.hour < 23 else now_local + timedelta(minutes=5),
                             all_day=False, is_utc=False, rrule=""))
        db.add(CalendarEvent(uid=f"ev2-{suffix}", calendar_id=cal.id, summary="Tomorrow only",
                             dtstart=now_local + timedelta(days=1, hours=1), dtend=now_local + timedelta(days=1, hours=2),
                             all_day=False, is_utc=False, rrule=""))
        db.add(ScheduledTask(id=f"task-{suffix}", owner=owner, name="Ship release", task_type="llm",
                             status=task_status, next_run=now_utc + task_offset))
        db.add(ScheduledTask(id=f"task-far-{suffix}", owner=owner, name="Far future", task_type="llm",
                             status="active", next_run=now_utc + timedelta(days=3)))
        db.add(Note(id=f"note-{suffix}", owner=owner, title="Renew passport", archived=False,
                    due_date=now_local.strftime("%Y-%m-%dT09:00")))
        db.add(Note(id=f"note-later-{suffix}", owner=owner, title="Not yet", archived=False,
                    due_date=(now_local + timedelta(days=2)).strftime("%Y-%m-%dT09:00")))
        db.add(EmailAccount(id=f"mail-{suffix}", owner=owner, name="Personal", enabled=True,
                            is_default=True, imap_user=owner, imap_password="hunter2"))
        db.commit()
    finally:
        db.close()
    if tmp is not None:
        (tmp / "data" / f"email_urgency_state_{owner}.json").write_text(
            json.dumps({"total_unread": 3, "total_urgent": 1, "max_score": 3, "per_uid": {}, "notified_uids": ["x"]}),
            encoding="utf-8",
        )


def _walk_keys(obj, found):
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.append(str(k))
            _walk_keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_keys(v, found)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_requires_session_when_auth_enabled(env):
    client = _client(env.hr, user=None, configured=True)
    r = client.get("/api/home/brief")
    assert r.status_code == 401


def test_happy_path_shape(env):
    _seed(env.TS, "alice", tmp=env.tmp)
    client = _client(env.hr, user="alice")
    r = client.get("/api/home/brief")
    assert r.status_code == 200
    body = r.json()

    assert set(body) == {"setup", "brief", "errors", "generated_at"}
    assert body["errors"] == {}
    assert isinstance(body["generated_at"], str) and "T" in body["generated_at"]

    setup = body["setup"]
    assert setup["models"] == {"ready": True, "endpoints": 1, "warm_model": "llama-3-8b"}
    assert setup["search"]["ready"] is True
    assert setup["search"]["state"] == "sidecar"
    assert isinstance(setup["search"]["detail"], str)
    assert setup["email"] == {"ready": True, "accounts": 1}

    brief = body["brief"]
    assert [e["title"] for e in brief["calendar_today"]] == ["Standup"]
    ev = brief["calendar_today"][0]
    assert set(ev) == {"id", "title", "start", "end", "all_day", "calendar"}
    assert ev["calendar"] == "Work" and ev["all_day"] is False

    assert [t["title"] for t in brief["tasks_due"]] == ["Ship release"]
    task = brief["tasks_due"][0]
    assert set(task) == {"id", "title", "due", "status"}
    assert task["status"] == "due" and task["due"].endswith("Z")

    assert [n["title"] for n in brief["notes_due"]] == ["Renew passport"]
    assert set(brief["notes_due"][0]) == {"id", "title", "due_date"}

    assert brief["email"] == {"unread": 3, "urgent": 1}
    assert brief["warm_model"] == "llama-3-8b"


def test_overdue_and_paused_tasks(env):
    _seed(env.TS, "alice", tmp=env.tmp, task_offset=timedelta(hours=-2))
    body = _client(env.hr, user="alice").get("/api/home/brief").json()
    assert [t["status"] for t in body["brief"]["tasks_due"]] == ["overdue"]

    env.hr.clear_cache()
    _seed(env.TS, "carol", tmp=env.tmp, task_status="paused")
    body = _client(env.hr, user="carol").get("/api/home/brief").json()
    assert body["brief"]["tasks_due"] == []


def test_failing_section_lands_in_errors_and_rest_is_returned(env, monkeypatch):
    _seed(env.TS, "alice", tmp=env.tmp)

    def _boom():
        raise RuntimeError("searx exploded")

    monkeypatch.setattr(env.hr, "_search_section", _boom)
    r = _client(env.hr, user="alice").get("/api/home/brief")
    assert r.status_code == 200
    body = r.json()
    assert "search" in body["errors"]
    assert "searx exploded" in body["errors"]["search"]
    assert body["setup"]["search"] == {"ready": False, "state": "unknown", "detail": ""}
    # Everything else still came through.
    assert body["setup"]["models"]["ready"] is True
    assert body["setup"]["email"]["ready"] is True
    assert body["brief"]["calendar_today"]
    assert body["brief"]["tasks_due"]
    assert body["brief"]["notes_due"]


def test_owner_scoping(env):
    _seed(env.TS, "alice", tmp=env.tmp)
    body = _client(env.hr, user="bob").get("/api/home/brief").json()
    assert body["errors"] == {}
    assert body["setup"]["models"]["ready"] is False
    assert body["setup"]["models"]["endpoints"] == 0
    assert body["setup"]["email"] == {"ready": False, "accounts": 0}
    assert body["brief"]["calendar_today"] == []
    assert body["brief"]["tasks_due"] == []
    assert body["brief"]["notes_due"] == []
    assert body["brief"]["email"] is None


def test_no_secret_looking_keys_in_output(env):
    _seed(env.TS, "alice", tmp=env.tmp)
    body = _client(env.hr, user="alice").get("/api/home/brief").json()
    keys = []
    _walk_keys(body, keys)
    leaked = [k for k in keys if _SECRET_KEY_RE.search(k)]
    assert leaked == []
    assert "hunter2" not in json.dumps(body)
    assert "127.0.0.1:8002" not in json.dumps(body)


def test_cached_per_owner_for_15_seconds(env, monkeypatch):
    _seed(env.TS, "alice", tmp=env.tmp)
    calls = []
    real_build = env.hr.build_home_brief

    def _counting(owner, **kw):
        calls.append(owner)
        return real_build(owner, **kw)

    monkeypatch.setattr(env.hr, "build_home_brief", _counting)
    alice = _client(env.hr, user="alice")
    bob = _client(env.hr, user="bob")
    first = alice.get("/api/home/brief").json()
    second = alice.get("/api/home/brief").json()
    assert first == second
    assert calls == ["alice"]
    bob.get("/api/home/brief")
    assert calls == ["alice", "bob"]
    alice.get("/api/home/brief?refresh=1")
    assert calls == ["alice", "bob", "alice"]


def test_router_is_registered_in_app():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert "from routes.home_routes import setup_home_routes" in source
    assert 'build_and_include_router(app, "Home", setup_home_routes' in source
