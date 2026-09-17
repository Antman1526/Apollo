# routes/council_routes.py
"""The Council — ask up to four models the same question concurrently, then
have the reviewer-role model synthesize consensus, disagreements, and a
recommended answer.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional

from routes.compare_helpers import resolve_ad_hoc_endpoint
from src.auth_helpers import require_user
from src.endpoint_resolver import resolve_endpoint
from src.url_safety import check_outbound_url
from services.council import run_council

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/council", tags=["council"])

MIN_MEMBERS = 2
MAX_MEMBERS = 4
# Enforced by hand (rather than a Pydantic Field(max_length=...)) so a
# too-long question returns a clear 400 instead of FastAPI's default 422
# for a schema violation.
MAX_QUESTION_LENGTH = 4000
# Overall deadline for one full Council run (members + reviewer), regardless
# of the per-call timeout passed to run_council.
COUNCIL_TIMEOUT = 240


class CouncilMember(BaseModel):
    model: str
    endpoint_url: str


class CouncilAskRequest(BaseModel):
    question: str
    members: List[CouncilMember]


def _resolve_members_sync(members_in: List[CouncilMember], owner: Optional[str]) -> list:
    """Validate and resolve every member's endpoint.

    Runs off the event loop (called via ``asyncio.to_thread``) since it does
    blocking DNS resolution (``check_outbound_url``) and a blocking DB
    lookup (``resolve_ad_hoc_endpoint``). Raising HTTPException here is safe
    — it propagates through ``await asyncio.to_thread(...)`` exactly as if
    raised directly in the route.
    """
    resolved = []
    for m in members_in:
        ok, reason = check_outbound_url(m.endpoint_url)
        if not ok:
            raise HTTPException(400, f"Rejected endpoint URL: {reason}")
        url, headers = resolve_ad_hoc_endpoint(m.endpoint_url, owner=owner)
        resolved.append({"model": m.model, "url": url, "headers": headers})
    return resolved


def _resolve_reviewer_sync(owner: Optional[str]) -> Optional[dict]:
    try:
        r_url, r_model, r_headers = resolve_endpoint("reviewer", owner=owner)
        if r_url and r_model:
            return {"model": r_model, "url": r_url, "headers": r_headers}
    except Exception as error:
        logger.warning("Council reviewer resolution failed: %s", error)
    return None


def setup_council_routes(session_manager) -> APIRouter:
    """Setup Council routes. `session_manager` is accepted (unused) so this
    factory matches the registration convention used by the other ad-hoc
    multi-model routes (e.g. Compare)."""

    @router.post("/ask")
    async def ask_council(request: Request, body: CouncilAskRequest):
        question = (body.question or "").strip()
        if not question:
            raise HTTPException(400, "question required")
        if len(question) > MAX_QUESTION_LENGTH:
            raise HTTPException(
                400, f"question is too long (max {MAX_QUESTION_LENGTH} characters)"
            )
        if not (MIN_MEMBERS <= len(body.members) <= MAX_MEMBERS):
            raise HTTPException(
                400, f"Council needs between {MIN_MEMBERS} and {MAX_MEMBERS} members"
            )

        # SECURITY: resolve owner via require_user (consistent with Compare)
        # so a stored API key can only ever be spent by its own owner.
        owner = require_user(request)

        members = await asyncio.to_thread(_resolve_members_sync, body.members, owner)
        reviewer = await asyncio.to_thread(_resolve_reviewer_sync, owner)

        try:
            return await asyncio.wait_for(
                run_council(question, members, reviewer), timeout=COUNCIL_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "The Council timed out waiting for a response")

    return router
