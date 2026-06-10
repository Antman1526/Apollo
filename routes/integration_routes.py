"""Cross-integration status routes."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request

from services.integrations import agent_workbench


PaperclipStatusProvider = Callable[[], Awaitable[dict[str, Any]]]


def setup_integration_routes(paperclip_status_provider: PaperclipStatusProvider | None = None) -> APIRouter:
    router = APIRouter(tags=["integrations"])

    @router.get("/api/integrations/agent-workbench/status")
    async def agent_workbench_status(request: Request):
        paperclip_status = None
        if paperclip_status_provider is not None:
            paperclip_status = await paperclip_status_provider()
        base_url = str(request.base_url).rstrip("/")
        return agent_workbench.status(app_base_url=base_url, paperclip_status=paperclip_status)

    return router
