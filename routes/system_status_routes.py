"""Unified system status routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from core.middleware import require_admin
from services.system_status import build_system_status


def setup_system_status_routes(
    *,
    memory_manager: Any = None,
    memory_vector: Any = None,
    mcp_manager: Any = None,
    task_scheduler: Any = None,
) -> APIRouter:
    router = APIRouter(tags=["system-status"])

    @router.get("/api/system/status")
    async def get_system_status(request: Request) -> dict[str, Any]:
        require_admin(request)
        return build_system_status(
            memory_manager=memory_manager,
            memory_vector=memory_vector,
            mcp_manager=mcp_manager,
            task_scheduler=task_scheduler,
        )

    return router
