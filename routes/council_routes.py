# routes/council_routes.py
"""The Council — ask up to three models the same question concurrently, then
have the reviewer-role model synthesize consensus, disagreements, and a
recommended answer.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional

from routes.compare_helpers import resolve_ad_hoc_endpoint
from src.auth_helpers import get_current_user
from src.endpoint_resolver import resolve_endpoint
from services.council import run_council

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/council", tags=["council"])

MIN_MEMBERS = 2
MAX_MEMBERS = 4


class CouncilMember(BaseModel):
    model: str
    endpoint_url: str


class CouncilAskRequest(BaseModel):
    question: str
    members: List[CouncilMember]


def setup_council_routes(session_manager) -> APIRouter:
    """Setup Council routes. `session_manager` is accepted (unused) so this
    factory matches the registration convention used by the other ad-hoc
    multi-model routes (e.g. Compare)."""

    @router.post("/ask")
    async def ask_council(request: Request, body: CouncilAskRequest):
        question = (body.question or "").strip()
        if not question:
            raise HTTPException(400, "question required")
        if not (MIN_MEMBERS <= len(body.members) <= MAX_MEMBERS):
            raise HTTPException(
                400, f"Council needs between {MIN_MEMBERS} and {MAX_MEMBERS} members"
            )

        members = []
        for m in body.members:
            url, headers = resolve_ad_hoc_endpoint(m.endpoint_url)
            members.append({"model": m.model, "url": url, "headers": headers})

        owner = get_current_user(request)
        reviewer: Optional[dict] = None
        try:
            r_url, r_model, r_headers = resolve_endpoint("reviewer", owner=owner)
            if r_url and r_model:
                reviewer = {"model": r_model, "url": r_url, "headers": r_headers}
        except Exception as error:
            logger.warning("Council reviewer resolution failed: %s", error)
            reviewer = None

        return await run_council(question, members, reviewer)

    return router
