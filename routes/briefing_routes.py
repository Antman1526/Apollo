"""routes/briefing_routes.py — GET /api/briefing/today

Composes the welcome screen's "Today" card from unread/starred email,
today's calendar events, pinned/due notes, and scheduled tasks due today.

Calendar, notes and scheduled tasks have no injectable "manager" object in
this codebase (routes/calendar_routes.py and routes/note_routes.py query
SQLAlchemy directly; routes/task_routes.py does too). The four `*_manager`
/ `task_scheduler` parameters below exist so tests can inject a fake
`callable(owner, ...) -> list[dict]` per source; when omitted (the real
app.py registration), each source falls back to a default fetch function
that performs the exact same owner-scoped query its own route does. Every
source is wrapped in try/except so one failing dependency (IMAP down, DB
hiccup) degrades to an empty list plus a warning instead of a 500.
"""

import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, Query, Request

from src.auth_helpers import effective_user, require_user
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async
from routes.calendar_routes import FALLBACK_OWNER, set_user_tz_offset
from services.briefing import compose_briefing, summarize_briefing

logger = logging.getLogger(__name__)

# Sources are read-heavy and don't need to be fresh to the second — cache the
# four raw fetch lists per (owner, tz offset) for a minute so re-opening the
# welcome screen, or asking for the spoken summary right after the card
# already fetched, doesn't re-hit IMAP/DB. `?refresh=1` bypasses this.
_BRIEFING_CACHE: dict = {}
_BRIEFING_CACHE_LOCK = threading.Lock()
_BRIEFING_CACHE_TTL = 60.0


def _cache_get(key):
    with _BRIEFING_CACHE_LOCK:
        entry = _BRIEFING_CACHE.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if expires_at < time.monotonic():
        with _BRIEFING_CACHE_LOCK:
            _BRIEFING_CACHE.pop(key, None)
        return None
    return value


def _cache_put(key, value):
    with _BRIEFING_CACHE_LOCK:
        _BRIEFING_CACHE[key] = (time.monotonic() + _BRIEFING_CACHE_TTL, value)


def _resolve_tz(request: Request):
    """Parse the `X-Tz-Offset` header (minutes east of UTC, same convention
    as static/js/chat.js) into `(tz, offset_minutes)`. `(None, None)` when
    the header is missing or not an integer."""
    raw = request.headers.get("x-tz-offset")
    if raw is None:
        return None, None
    try:
        offset = int(raw)
    except (TypeError, ValueError):
        return None, None
    return timezone(timedelta(minutes=offset)), offset


# ── Owner resolution — mirrors each source's own route exactly ──

def _email_owner(request: Request) -> str:
    return require_user(request)


def _calendar_owner(request: Request) -> str:
    # Mirrors routes.calendar_routes._require_user: calendar rows are always
    # written under FALLBACK_OWNER in single-user/no-auth mode, so querying
    # with "" would silently return nothing.
    return require_user(request) or FALLBACK_OWNER


def _note_owner(request: Request) -> Optional[str]:
    return effective_user(request)


def _task_owner(request: Request) -> Optional[str]:
    return effective_user(request)


# ── Default fetchers — same query each source's own route makes ──

def _default_fetch_emails(owner: str) -> list:
    from routes.email_routes import _email_imap_configured, _uid_from_fetch_meta
    from routes.email_helpers import _imap, _decode_header
    import email as email_mod
    import email.utils

    if not _email_imap_configured(None, owner=owner):
        return []
    with _imap(None, owner=owner) as conn:
        conn.select('"INBOX"', readonly=True)
        status, data = conn.uid("SEARCH", None, "(OR UNSEEN FLAGGED)")
        if status != "OK" or not data or not data[0]:
            return []
        uids = list(reversed(data[0].split()))[:40]
        if not uids:
            return []
        # One batched round-trip for every candidate UID instead of one
        # FETCH per message — same trick _list_emails_sync uses.
        status, msg_data = conn.uid("FETCH", b",".join(uids), "(UID FLAGS RFC822.HEADER)")
        if status != "OK" or not msg_data:
            return []
        seq_re = re.compile(rb'^(\d+)\s+\(')
        grouped = []  # list of (meta_bytes, header_bytes)
        for part in msg_data:
            if isinstance(part, tuple):
                meta_b = part[0] if isinstance(part[0], (bytes, bytearray)) else str(part[0]).encode()
                if seq_re.match(meta_b):
                    grouped.append((meta_b, part[1]))
                elif grouped:
                    cur_meta, cur_payload = grouped[-1]
                    grouped[-1] = (cur_meta + b" " + meta_b, cur_payload or part[1])
        out = []
        for meta_b, raw_header in grouped:
            if not raw_header:
                continue
            try:
                meta = meta_b.decode(errors="replace")
                uid_num = _uid_from_fetch_meta(meta_b)
                if not uid_num:
                    continue
                flag_m = re.search(r'FLAGS \(([^)]*)\)', meta)
                flags = flag_m.group(1) if flag_m else ""
                msg = email_mod.message_from_bytes(raw_header)
                subject = _decode_header(msg.get("Subject", "(no subject)"))
                sender = _decode_header(msg.get("From", "unknown"))
                sender_name, sender_addr = email.utils.parseaddr(sender)
                date_str = msg.get("Date", "")
                parsed_date = email.utils.parsedate_to_datetime(date_str) if date_str else None
                if parsed_date and parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                out.append({
                    "uid": uid_num,
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


def _default_fetch_events(owner: str, tz) -> list:
    from core.database import SessionLocal, CalendarCal, CalendarEvent
    from sqlalchemy import and_, or_
    from routes.calendar_routes import _expand_rrule

    now_local = datetime.now(tz) if tz is not None else datetime.now(timezone.utc)
    local_day_start = datetime(now_local.year, now_local.month, now_local.day)
    # CalendarEvent rows mix UTC-stored (is_utc=True) and legacy naive
    # local-wall-clock datetimes (see _event_to_dict's docstring in
    # calendar_routes.py) — widen the SQL window a day on each side so a
    # row landing on the local day under either interpretation is fetched;
    # compose_briefing's tz-aware same-day filter does the exact match.
    start = local_day_start - timedelta(days=1)
    end = local_day_start + timedelta(days=2)
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
    from core.database import SessionLocal
    from routes.note_routes import _note_to_dict, query_active_notes

    db = SessionLocal()
    try:
        return [_note_to_dict(n) for n in query_active_notes(db, owner).all()]
    finally:
        db.close()


def _default_fetch_tasks(owner: Optional[str]) -> list:
    from core.database import SessionLocal, ScheduledTask
    from routes.task_routes import _task_to_dict, query_tasks

    db = SessionLocal()
    try:
        q = query_tasks(db, owner).filter(ScheduledTask.status == "active")
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
    calendar_manager: Optional[Callable[[str, object], list]] = None,
    note_manager: Optional[Callable[[str], list]] = None,
    task_scheduler: Optional[Callable[[str], list]] = None,
) -> APIRouter:
    """Each `*_manager` / `task_scheduler` argument, if given, replaces the
    default fetcher for that source — the seam tests use to simulate one
    source failing or to assert the exact args a source was called with.
    `calendar_manager` takes `(owner, tz)`; the rest take `(owner)`."""
    router = APIRouter(prefix="/api/briefing", tags=["briefing"])

    fetch_emails = email_manager or _default_fetch_emails
    fetch_events = calendar_manager or _default_fetch_events
    fetch_notes = note_manager or _default_fetch_notes
    fetch_tasks = task_scheduler or _default_fetch_tasks

    @router.get("/today")
    async def get_today(request: Request, summary: int = Query(0), refresh: int = Query(0)):
        # Auth gate only — each source below resolves its OWN owner the
        # same way its own route does (see _email_owner/_calendar_owner/…).
        owner = require_user(request)
        tz, tz_offset = _resolve_tz(request)
        if tz_offset is not None:
            try:
                # Same contextvar chat_routes/calendar_routes use for
                # natural-language time parsing — keep it in sync here too.
                set_user_tz_offset(tz_offset)
            except Exception as error:
                logger.debug("Briefing tz-offset propagation failed: %s", error)

        warnings: List[str] = []
        cache_key = (owner, tz_offset)
        cached = None if refresh else _cache_get(cache_key)
        if cached is not None:
            emails, events, notes, tasks = cached
        else:
            async def _safe(name: str, fn: Callable[..., list], *args) -> list:
                try:
                    return await asyncio.to_thread(fn, *args)
                except Exception as error:
                    logger.warning("Briefing source %s failed: %s", name, error)
                    warnings.append(f"{name}: unavailable")
                    return []

            emails, events, notes, tasks = await asyncio.gather(
                _safe("email", fetch_emails, _email_owner(request)),
                _safe("calendar", fetch_events, _calendar_owner(request), tz),
                _safe("notes", fetch_notes, _note_owner(request)),
                _safe("tasks", fetch_tasks, _task_owner(request)),
            )
            # Only cache a fully successful fetch so a transient source failure
            # is retried on the next welcome show instead of hiding for 60s.
            if not warnings:
                _cache_put(cache_key, (emails, events, notes, tasks))

        briefing = compose_briefing(
            now=datetime.now(tz or timezone.utc), emails=emails, events=events,
            notes=notes, tasks=tasks, tz=tz,
        )

        summary_text = None
        if summary:
            endpoint = _resolve_reviewer(_note_owner(request))
            summary_text = await summarize_briefing(briefing, call=llm_call_async, endpoint=endpoint, tz=tz)

        return {**briefing, "summary": summary_text, "warnings": warnings}

    return router
