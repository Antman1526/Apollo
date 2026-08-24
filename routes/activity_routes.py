"""HTTP API for the agent activity ledger (the "computer history").

Admin-only: the ledger records shell commands, file paths, and tool outputs —
strictly more sensitive than the chat transcript — and undo writes files.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.middleware import require_admin
from services.activity_ledger import list_events, undo_event

logger = logging.getLogger(__name__)


def setup_activity_routes() -> APIRouter:
    router = APIRouter(prefix="/api/activity", tags=["activity"])

    @router.get("")
    def list_activity(
        request: Request,
        q: Optional[str] = None,
        tool: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        require_admin(request)
        events = list_events(
            q=q, tool=tool, session_id=session_id, limit=limit, offset=offset
        )
        return {"events": events, "count": len(events)}

    @router.post("/{event_id}/undo")
    def undo(request: Request, event_id: str):
        require_admin(request)
        result = undo_event(event_id)
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        return result

    return router
