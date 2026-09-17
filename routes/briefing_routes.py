"""routes/briefing_routes.py — GET /api/briefing/today

Composes the welcome screen's "Today" card from unread/starred email,
today's calendar events, pinned/due notes, and scheduled tasks due today.

Calendar, notes and scheduled tasks have no injectable "manager" object in
this codebase (routes/calendar_routes.py and routes/note_routes.py query
SQLAlchemy directly; routes/task_routes.py does too). The four `*_manager`
/ `task_scheduler` parameters below exist so tests can inject a fake
`callable(owner) -> list[dict]` per source; when omitted (the real app.py
registration), each source falls back to a default fetch function that
performs the exact same owner-scoped query its own route does. Every
source is wrapped in try/except so one failing dependency (IMAP down, DB
hiccup) degrades to an empty list plus a warning instead of a 500.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, Query, Request

from src.auth_helpers import effective_user, require_user
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async
from services.briefing import compose_briefing, summarize_briefing

logger = logging.getLogger(__name__)


# ── Owner resolution — mirrors each source's own route exactly ──

def _email_owner(request: Request) -> str:
    return require_user(request)


def _calendar_owner(request: Request) -> str:
    # Mirrors routes.calendar_routes._require_user: calendar rows are always
    # written under FALLBACK_OWNER in single-user/no-auth mode, so querying
    # with "" would silently return nothing.
    from routes.calendar_routes import FALLBACK_OWNER
    return require_user(request) or FALLBACK_OWNER


def _note_owner(request: Request) -> Optional[str]:
    return effective_user(request)


def _task_owner(request: Request) -> Optional[str]:
    return effective_user(request)


# ── Default fetchers — same query each source's own route makes ──

def _default_fetch_emails(owner: str) -> list:
    from routes.email_routes import _email_imap_configured
    from routes.email_helpers import _imap_connect, _decode_header
    import email as email_mod
    import email.utils

    if not _email_imap_configured(None, owner=owner):
        return []
    conn = _imap_connect(None, owner=owner)
    try:
        conn.select('"INBOX"', readonly=True)
        status, data = conn.uid("SEARCH", None, "(OR UNSEEN FLAGGED)")
        if status != "OK" or not data or not data[0]:
            return []
        uids = list(reversed(data[0].split()))[:40]
        out = []
        for uid in uids:
            try:
                status, msg_data = conn.uid("FETCH", uid, "(FLAGS RFC822.HEADER)")
                if status != "OK" or not msg_data:
                    continue
                raw_header = None
                flags = ""
                for part in msg_data:
                    if isinstance(part, tuple):
                        meta = part[0].decode() if isinstance(part[0], bytes) else str(part[0])
                        raw_header = part[1]
                        m = re.search(r"FLAGS \(([^)]*)\)", meta)
                        if m:
                            flags = m.group(1)
                if not raw_header:
                    continue
                msg = email_mod.message_from_bytes(raw_header)
                subject = _decode_header(msg.get("Subject", "(no subject)"))
                sender = _decode_header(msg.get("From", "unknown"))
                sender_name, sender_addr = email.utils.parseaddr(sender)
                date_str = msg.get("Date", "")
                parsed_date = email.utils.parsedate_to_datetime(date_str) if date_str else None
                if parsed_date and parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                out.append({
                    "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "from": sender_name or sender_addr,
                    "subject": subject,
                    "snippet": "",
                    "date": parsed_date.isoformat() if parsed_date else None,
                    "flag_read": "\\Seen" in flags,
                    "flag_star": "\\Flagged" in flags,
                })
            except Exception as error:
                logger.debug("Briefing email fetch skipped one message: %s", error)
                continue
        return out
    finally:
        try:
            conn.logout()
        except Exception as error:
            logger.debug("Briefing email IMAP logout failed: %s", error)


def _default_fetch_events(owner: str) -> list:
    from core.database import SessionLocal, CalendarCal, CalendarEvent
    from sqlalchemy import and_, or_
    from routes.calendar_routes import _expand_rrule

    now = datetime.utcnow()
    start = datetime(now.year, now.month, now.day)
    end = start + timedelta(days=1)
    db = SessionLocal()
    try:
        q = db.query(CalendarEvent).join(CalendarCal).filter(
            CalendarEvent.status != "cancelled",
            CalendarCal.owner == owner,
            or_(
                and_(
                    or_(CalendarEvent.rrule == "", CalendarEvent.rrule.is_(None)),
                    CalendarEvent.dtstart < end,
                    CalendarEvent.dtend > start,
                ),
                and_(
                    CalendarEvent.rrule.isnot(None),
                    CalendarEvent.rrule != "",
                    CalendarEvent.dtstart < end,
                ),
            ),
        )
        expanded = []
        for e in q.order_by(CalendarEvent.dtstart).all():
            expanded.extend(_expand_rrule(e, start, end))
        return expanded
    finally:
        db.close()


def _default_fetch_notes(owner: Optional[str]) -> list:
    from core.database import SessionLocal, Note
    from routes.note_routes import _note_to_dict

    db = SessionLocal()
    try:
        q = db.query(Note).filter(Note.archived == False)  # noqa: E712
        if owner:
            q = q.filter(Note.owner == owner)
        return [_note_to_dict(n) for n in q.all()]
    finally:
        db.close()


def _default_fetch_tasks(owner: Optional[str]) -> list:
    from core.database import SessionLocal, ScheduledTask
    from routes.task_routes import _task_to_dict

    db = SessionLocal()
    try:
        q = db.query(ScheduledTask).filter(ScheduledTask.status == "active")
        if owner:
            q = q.filter(ScheduledTask.owner == owner)
        return [_task_to_dict(t) for t in q.all()]
    finally:
        db.close()


def _resolve_reviewer(owner: Optional[str]) -> Optional[dict]:
    try:
        url, model, headers = resolve_endpoint("reviewer", owner=owner)
        if url and model:
            return {"url": url, "model": model, "headers": headers}
    except Exception as error:
        logger.warning("Briefing reviewer resolution failed: %s", error)
    return None


def setup_briefing_routes(
    email_manager: Optional[Callable[[str], list]] = None,
    calendar_manager: Optional[Callable[[str], list]] = None,
    note_manager: Optional[Callable[[str], list]] = None,
    task_scheduler: Optional[Callable[[str], list]] = None,
) -> APIRouter:
    """Each `*_manager` / `task_scheduler` argument, if given, is a
    ``callable(owner) -> list[dict]`` used in place of the default fetcher
    for that source — the seam tests use to simulate one source failing."""
    router = APIRouter(prefix="/api/briefing", tags=["briefing"])

    fetch_emails = email_manager or _default_fetch_emails
    fetch_events = calendar_manager or _default_fetch_events
    fetch_notes = note_manager or _default_fetch_notes
    fetch_tasks = task_scheduler or _default_fetch_tasks

    @router.get("/today")
    async def get_today(request: Request, summary: int = Query(0)):
        # Auth gate only — each source below resolves its OWN owner the
        # same way its own route does (see _email_owner/_calendar_owner/…).
        require_user(request)
        warnings: List[str] = []

        async def _safe(name: str, owner, fn: Callable[[str], list]) -> list:
            try:
                return await asyncio.to_thread(fn, owner)
            except Exception as error:
                logger.warning("Briefing source %s failed: %s", name, error)
                warnings.append(f"{name}: unavailable")
                return []

        emails, events, notes, tasks = await asyncio.gather(
            _safe("email", _email_owner(request), fetch_emails),
            _safe("calendar", _calendar_owner(request), fetch_events),
            _safe("notes", _note_owner(request), fetch_notes),
            _safe("tasks", _task_owner(request), fetch_tasks),
        )

        briefing = compose_briefing(
            now=datetime.now(timezone.utc), emails=emails, events=events,
            notes=notes, tasks=tasks,
        )

        summary_text = None
        if summary:
            endpoint = _resolve_reviewer(_note_owner(request))
            summary_text = await summarize_briefing(briefing, call=llm_call_async, endpoint=endpoint)

        return {**briefing, "summary": summary_text, "warnings": warnings}

    return router
