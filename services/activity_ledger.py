"""Append-only activity ledger for agent tool executions.

Records every tool call the agent makes into the `activity_events` table so
the user has a searchable "computer history" (what the agent ran, wrote,
fetched, and when) plus per-write undo. Recording is strictly best-effort:
a ledger failure must never break tool execution, so `record_event` swallows
and logs its own errors.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from core.database import ActivityEvent, SessionLocal
from src.settings import get_setting

logger = logging.getLogger(__name__)

# Previews keep the timeline readable without dragging full outputs along.
INPUT_PREVIEW_CHARS = 4_000
OUTPUT_PREVIEW_CHARS = 4_000
# before_content is the undo payload — bigger cap, but bounded so one huge
# file can't bloat the DB. Writes over the cap are recorded without undo.
BEFORE_CONTENT_CHARS = 512_000
DEFAULT_MAX_EVENTS = 10_000

# Tools that are pure reads of the agent's own state — recording them buries
# the actions the user actually cares about ("what did it DO?").
_SKIP_TOOLS = {"read_file", "manage_memory"}


def enabled() -> bool:
    return bool(get_setting("activity_ledger_enabled", True))


def _clip(text: Any, limit: int) -> str:
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    return s if len(s) <= limit else s[:limit] + f"\n… [clipped at {limit} chars]"


def capture_before(path: str) -> Dict[str, Any]:
    """Snapshot a file's pre-write state for undo. Returns {} on any failure."""
    try:
        if not os.path.exists(path):
            return {"before_existed": False, "before_content": ""}
        if os.path.getsize(path) > BEFORE_CONTENT_CHARS:
            return {"before_existed": True, "before_content": None}  # too big to undo
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {"before_existed": True, "before_content": f.read()}
    except OSError:
        return {}


def record_event(
    *,
    tool: str,
    summary: str = "",
    input_text: str = "",
    result: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    duration_ms: Optional[int] = None,
    path: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert one ledger row. Never raises; returns the event id or None."""
    if not enabled() or tool in _SKIP_TOOLS:
        return None
    try:
        result = result or {}
        before = before or {}
        db = SessionLocal()
        try:
            ev = ActivityEvent(
                id=str(uuid.uuid4()),
                session_id=session_id,
                owner=owner,
                tool=tool,
                summary=_clip(summary, 500),
                input_preview=_clip(input_text, INPUT_PREVIEW_CHARS),
                output_preview=_clip(
                    result.get("output") or result.get("error") or "",
                    OUTPUT_PREVIEW_CHARS,
                ),
                exit_code=result.get("exit_code"),
                duration_ms=duration_ms,
                path=path,
                before_content=before.get("before_content"),
                before_existed=before.get("before_existed"),
            )
            db.add(ev)
            db.commit()
            _prune(db)
            return ev.id
        finally:
            db.close()
    except Exception:
        logger.exception("activity ledger: record failed (ignored)")
        return None


def _prune(db) -> None:
    """Keep the table bounded: drop oldest rows past the configured cap."""
    try:
        cap = int(get_setting("activity_ledger_max_events", DEFAULT_MAX_EVENTS))
        count = db.query(ActivityEvent).count()
        if count > cap:
            old = (
                db.query(ActivityEvent)
                .order_by(ActivityEvent.created_at.asc())
                .limit(count - cap)
                .all()
            )
            for row in old:
                db.delete(row)
            db.commit()
    except Exception:
        logger.exception("activity ledger: prune failed (ignored)")


def list_events(
    *,
    q: Optional[str] = None,
    tool: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        query = db.query(ActivityEvent)
        if tool:
            query = query.filter(ActivityEvent.tool == tool)
        if session_id:
            query = query.filter(ActivityEvent.session_id == session_id)
        if q:
            like = f"%{q}%"
            query = query.filter(
                ActivityEvent.input_preview.like(like)
                | ActivityEvent.output_preview.like(like)
                | ActivityEvent.summary.like(like)
                | ActivityEvent.path.like(like)
            )
        rows = (
            query.order_by(ActivityEvent.created_at.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 500)))
            .all()
        )
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


def _to_dict(r: ActivityEvent) -> Dict[str, Any]:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "session_id": r.session_id,
        "owner": r.owner,
        "tool": r.tool,
        "summary": r.summary,
        "input_preview": r.input_preview,
        "output_preview": r.output_preview,
        "exit_code": r.exit_code,
        "duration_ms": r.duration_ms,
        "path": r.path,
        "undoable": bool(
            r.path and r.before_content is not None and not r.undone
        ),
        "undone": bool(r.undone),
    }


def recent_tool_events(
    days: int = 7, tools: tuple = ("bash",), only_success: bool = True
) -> List[Dict[str, Any]]:
    """Recent events for pattern mining: (session_id, tool, input) triples."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max(1, days))
    db = SessionLocal()
    try:
        q = db.query(ActivityEvent).filter(
            ActivityEvent.tool.in_(list(tools)),
            ActivityEvent.created_at >= cutoff,
        )
        if only_success:
            q = q.filter(ActivityEvent.exit_code == 0)
        return [
            {"session_id": r.session_id, "tool": r.tool, "input": r.input_preview}
            for r in q.order_by(ActivityEvent.created_at.asc()).all()
        ]
    finally:
        db.close()


def undo_session(session_id: str) -> Dict[str, Any]:
    """Roll back every still-undoable file write from one session, as a bundle.

    Undoes newest-first so a file written twice steps back through each
    snapshot and lands on its original content. Per-event failures don't
    abort the bundle — the result reports both counts.
    """
    if not session_id:
        return {"ok": False, "error": "session_id required"}
    db = SessionLocal()
    try:
        rows = (
            db.query(ActivityEvent)
            .filter(
                ActivityEvent.session_id == session_id,
                ActivityEvent.undone.is_(False),
                ActivityEvent.path.isnot(None),
                ActivityEvent.before_content.isnot(None),
            )
            .order_by(ActivityEvent.created_at.desc())
            .all()
        )
        ids = [r.id for r in rows]
    finally:
        db.close()
    if not ids:
        return {"ok": False, "error": "no undoable writes in this session"}
    undone = 0
    failed = []
    for eid in ids:
        res = undo_event(eid)
        if res.get("ok"):
            undone += 1
        else:
            failed.append({"id": eid, "error": res.get("error")})
    return {"ok": undone > 0, "undone": undone, "failed": failed}


def undo_event(event_id: str) -> Dict[str, Any]:
    """Revert a recorded file write to its captured pre-write state.

    Only the path stored on the event is touched — never a caller-supplied
    one. Returns {"ok": True, ...} or {"ok": False, "error": ...}.
    """
    db = SessionLocal()
    try:
        ev = db.query(ActivityEvent).filter(ActivityEvent.id == event_id).first()
        if not ev:
            return {"ok": False, "error": "event not found"}
        if ev.undone:
            return {"ok": False, "error": "already undone"}
        if not ev.path or ev.before_content is None:
            return {"ok": False, "error": "event is not undoable"}
        try:
            if ev.before_existed:
                d = os.path.dirname(ev.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(ev.path, "w", encoding="utf-8") as f:
                    f.write(ev.before_content)
                action = f"restored previous contents of {ev.path}"
            else:
                if os.path.exists(ev.path):
                    os.remove(ev.path)
                action = f"removed {ev.path} (did not exist before the write)"
        except OSError as e:
            return {"ok": False, "error": f"undo failed: {e}"}
        ev.undone = True
        db.commit()
        record_event(
            tool="undo",
            summary=f"undo: {ev.tool} on {ev.path}",
            input_text=action,
            result={"output": action, "exit_code": 0},
            session_id=ev.session_id,
            owner=ev.owner,
            path=ev.path,
        )
        return {"ok": True, "action": action}
    finally:
        db.close()
