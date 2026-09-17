"""Home routes — the welcome screen's setup checklist + daily brief.

``GET /api/home/brief`` is a non-admin, owner-scoped rollup of the small
set of facts the welcome screen needs:

* ``setup``  — is a model connected, is web search usable, is a mailbox
  configured. Drives the fresh-install checklist.
* ``brief``  — today's calendar, tasks/notes due, inbox counts, and the
  currently warm local model. Drives the "home brief" once set up.

Every section is computed inside its own try/except so a broken subsystem
degrades to an empty/null section and is reported in ``errors`` rather than
failing the whole response. Results are cached per owner for 15 seconds.

Nothing here ever returns credentials — endpoint rows are reduced to a
count, mail accounts to a count, and the search section to a state word.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Request

from core.database import (
    CalendarCal,
    CalendarEvent,
    EmailAccount,
    ModelEndpoint,
    Note,
    ScheduledTask,
    SessionLocal,
)
from src.auth_helpers import owner_filter, require_user
from src.observability import report_exception

logger = logging.getLogger(__name__)

# ── Per-owner response cache ────────────────────────────────────────────
CACHE_TTL_SECONDS = 15.0
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()

MAX_ROWS = 6


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _owner_slug(owner: str) -> str:
    """Same slug rule the email urgency scanner uses for its state file."""
    return "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))


def _is_admin(request: Request, owner: str) -> bool:
    if not owner:
        return False
    try:
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        is_admin = getattr(auth_mgr, "is_admin", None)
        return bool(is_admin(owner)) if callable(is_admin) else False
    except Exception as error:
        report_exception(logger, "home_brief_admin_check_failed", error, outcome="best_effort")
        return False


# ── Setup sections ──────────────────────────────────────────────────────

def _warm_model() -> Optional[str]:
    """Name of the running local chat model, if the llama-server sidecar has one."""
    from services.localmodels.server_manager import get_server

    status = get_server().status() or {}
    for model_id, info in status.items():
        if not isinstance(info, dict):
            continue
        if info.get("running") and (info.get("kind") or "chat") != "embedding":
            return str(info.get("name") or model_id)
    return None


def _models_section(owner: str, is_admin: bool) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
        if owner and not is_admin:
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.count()
    finally:
        db.close()
    warm = None
    try:
        warm = _warm_model()
    except Exception as error:
        report_exception(logger, "home_brief_warm_model_failed", error, outcome="best_effort")
    return {"ready": endpoints > 0, "endpoints": int(endpoints), "warm_model": warm}


def _search_section() -> Dict[str, Any]:
    from services.search.providers import PROVIDER_INFO, _get_provider_key
    from src.settings import load_settings

    settings = load_settings() or {}
    provider = (settings.get("search_provider") or "searxng").strip().lower()
    fallback_chain = [p for p in (settings.get("search_fallback_chain") or []) if p and p != "disabled"]

    if provider == "disabled":
        return {"ready": False, "state": "off", "detail": "Web search is turned off"}

    if provider == "searxng":
        from services.searxng.runtime import get_runtime

        sidecar = get_runtime().status()
        if sidecar == "running":
            return {"ready": True, "state": "sidecar", "detail": "SearXNG sidecar running"}
        if fallback_chain:
            label = PROVIDER_INFO.get(fallback_chain[0], (fallback_chain[0].title(),))[0]
            return {
                "ready": True,
                "state": "fallback",
                "detail": f"SearXNG {sidecar.replace('_', ' ')} — using {label}",
            }
        return {"ready": False, "state": "off", "detail": f"SearXNG {sidecar.replace('_', ' ')}"}

    label, needs_key, _ = PROVIDER_INFO.get(provider, (provider.title(), False, False))
    if needs_key and not _get_provider_key(provider):
        if fallback_chain:
            fb = PROVIDER_INFO.get(fallback_chain[0], (fallback_chain[0].title(),))[0]
            return {"ready": True, "state": "fallback", "detail": f"{label} has no key — using {fb}"}
        return {"ready": False, "state": "off", "detail": f"{label} needs an API key"}
    return {"ready": True, "state": "fallback", "detail": f"Using {label}"}


def _email_accounts_query(db, owner: str):
    """Owner-scoped mail accounts, matching ``GET /api/email/accounts``."""
    from sqlalchemy import and_, or_

    q = db.query(EmailAccount).filter(EmailAccount.enabled == True)  # noqa: E712
    if owner:
        unowned = or_(EmailAccount.owner == None, EmailAccount.owner == "")  # noqa: E711
        same_mailbox = or_(EmailAccount.imap_user == owner, EmailAccount.from_address == owner)
        q = q.filter(or_(EmailAccount.owner == owner, and_(unowned, same_mailbox)))
    return q


def _email_section(owner: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        accounts = _email_accounts_query(db, owner).count()
    finally:
        db.close()
    return {"ready": accounts > 0, "accounts": int(accounts)}


# ── Brief sections ──────────────────────────────────────────────────────

def _local_today_bounds(now: Optional[datetime] = None):
    now = now or datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _to_local(value: str) -> Optional[datetime]:
    """Parse a serialized calendar/note datetime into naive server-local time."""
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.fromisoformat(value)
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _calendar_today(owner: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from sqlalchemy import and_, or_

    from routes.calendar_routes import FALLBACK_OWNER, _expand_rrule

    # Calendar rows are always owned: single-user installs write them under
    # FALLBACK_OWNER, so read them back the same way the calendar routes do.
    cal_owner = owner or FALLBACK_OWNER
    day_start, day_end = _local_today_bounds(now)
    # Widen the SQL window by a day on each side so UTC-stored rows land in
    # the local day regardless of the server's offset; the exact filter
    # happens after expansion in local time.
    win_start, win_end = day_start - timedelta(days=1), day_end + timedelta(days=1)
    db = SessionLocal()
    try:
        q = db.query(CalendarEvent).join(CalendarCal).filter(
            CalendarEvent.status != "cancelled",
            or_(
                and_(
                    or_(CalendarEvent.rrule == "", CalendarEvent.rrule.is_(None)),
                    CalendarEvent.dtstart < win_end,
                    CalendarEvent.dtend > win_start,
                ),
                and_(
                    CalendarEvent.rrule.isnot(None),
                    CalendarEvent.rrule != "",
                    CalendarEvent.dtstart < win_end,
                ),
            ),
        )
        q = q.filter(CalendarCal.owner == cal_owner)
        rows = q.order_by(CalendarEvent.dtstart).all()
        expanded: List[Dict[str, Any]] = []
        for ev in rows:
            expanded.extend(_expand_rrule(ev, win_start, win_end))
    finally:
        db.close()

    out = []
    for d in expanded:
        start = _to_local(d.get("dtstart") or "")
        end = _to_local(d.get("dtend") or "")
        if start is None:
            continue
        if end is None:
            end = start
        if d.get("all_day"):
            # All-day rows are date-only; the end date is exclusive.
            if not (start < day_end and end > day_start):
                continue
        elif not (start < day_end and end > day_start):
            continue
        out.append({
            "id": d.get("uid"),
            "title": d.get("summary") or "(untitled)",
            "start": d.get("dtstart"),
            "end": d.get("dtend"),
            "all_day": bool(d.get("all_day")),
            "calendar": d.get("calendar") or "",
            "_sort": (0 if d.get("all_day") else 1, start),
        })
    out.sort(key=lambda item: item["_sort"])
    for item in out:
        item.pop("_sort", None)
    return out[:MAX_ROWS]


def _tasks_due(owner: str, now_utc: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    horizon = now_utc + timedelta(hours=24)
    db = SessionLocal()
    try:
        q = db.query(ScheduledTask).filter(
            ScheduledTask.status == "active",
            ScheduledTask.next_run.isnot(None),
            ScheduledTask.next_run <= horizon,
        )
        if owner:
            q = q.filter(ScheduledTask.owner == owner)
        rows = q.order_by(ScheduledTask.next_run.asc()).limit(MAX_ROWS).all()
        return [
            {
                "id": t.id,
                "title": t.name or "Untitled Task",
                "due": t.next_run.isoformat() + "Z" if t.next_run else None,
                "status": "overdue" if t.next_run and t.next_run < now_utc else "due",
            }
            for t in rows
        ]
    finally:
        db.close()


def _notes_due(owner: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    _, day_end = _local_today_bounds(now)
    db = SessionLocal()
    try:
        q = db.query(Note).filter(
            Note.archived == False,  # noqa: E712
            Note.due_date.isnot(None),
            Note.due_date != "",
        )
        if owner:
            q = q.filter(Note.owner == owner)
        rows = q.all()
    finally:
        db.close()
    out = []
    for n in rows:
        due = _to_local(n.due_date or "")
        if due is None or due >= day_end:
            continue
        out.append({"id": n.id, "title": n.title or "(untitled)", "due_date": n.due_date, "_sort": due})
    out.sort(key=lambda item: item["_sort"])
    for item in out:
        item.pop("_sort", None)
    return out[:MAX_ROWS]


def _email_brief(owner: str, configured: bool) -> Optional[Dict[str, Any]]:
    if not configured:
        return None
    path = Path(f"data/email_urgency_state_{_owner_slug(owner)}.json")
    if not path.exists():
        return {"unread": 0, "urgent": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "unread": int(data.get("total_unread") or 0),
        "urgent": int(data.get("total_urgent") or 0),
    }


# ── Assembly ────────────────────────────────────────────────────────────

def _run_section(errors: Dict[str, str], name: str, fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception as error:
        report_exception(logger, f"home_brief_{name}_failed", error, outcome="degraded", context={"section": name})
        errors[name] = f"{type(error).__name__}: {error}"[:200]
        return default


def build_home_brief(owner: str, *, is_admin: bool = False) -> Dict[str, Any]:
    errors: Dict[str, str] = {}
    models = _run_section(errors, "models", lambda: _models_section(owner, is_admin),
                          {"ready": False, "endpoints": 0, "warm_model": None})
    search = _run_section(errors, "search", _search_section,
                          {"ready": False, "state": "unknown", "detail": ""})
    email = _run_section(errors, "email", lambda: _email_section(owner),
                         {"ready": False, "accounts": 0})
    calendar_today = _run_section(errors, "calendar", lambda: _calendar_today(owner), [])
    tasks_due = _run_section(errors, "tasks", lambda: _tasks_due(owner), [])
    notes_due = _run_section(errors, "notes", lambda: _notes_due(owner), [])
    email_brief = _run_section(errors, "inbox", lambda: _email_brief(owner, bool(email.get("ready"))), None)
    return {
        "setup": {"models": models, "search": search, "email": email},
        "brief": {
            "calendar_today": calendar_today,
            "tasks_due": tasks_due,
            "notes_due": notes_due,
            "email": email_brief,
            "warm_model": models.get("warm_model"),
        },
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def setup_home_routes() -> APIRouter:
    router = APIRouter(prefix="/api/home", tags=["home"])

    @router.get("/brief")
    async def get_home_brief(request: Request, refresh: bool = False) -> Dict[str, Any]:
        owner = require_user(request)
        is_admin = _is_admin(request, owner)
        cache_key = f"{owner}|{int(is_admin)}"
        now = time.monotonic()
        if not refresh:
            with _CACHE_LOCK:
                entry = _CACHE.get(cache_key)
            if entry and (now - entry["time"]) < CACHE_TTL_SECONDS:
                return entry["data"]
        data = build_home_brief(owner, is_admin=is_admin)
        with _CACHE_LOCK:
            _CACHE[cache_key] = {"data": data, "time": now}
        return data

    return router
