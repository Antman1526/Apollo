"""Unified system status routes."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from services.system_status import build_system_status


def setup_system_status_routes(
    *,
    memory_manager: Any = None,
    memory_vector: Any = None,
    mcp_manager: Any = None,
    task_scheduler: Any = None,
    auth_manager: Any = None,
    rag_manager: Any = None,
    personal_docs_mgr: Any = None,
    status_cache_ttl: float = 2.0,
) -> APIRouter:
    router = APIRouter(tags=["system-status"])
    status_cache: dict[str, Any] = {"expires_at": 0.0, "value": None}

    @router.get("/api/system/status")
    async def get_system_status(request: Request) -> dict[str, Any]:
        require_admin(request)
        fresh_raw = str(getattr(request, "query_params", {}).get("fresh", "")).lower()
        fresh = fresh_raw in {"1", "true", "yes", "on"}
        now = time.monotonic()
        if not fresh and status_cache_ttl > 0 and status_cache["value"] is not None and status_cache["expires_at"] > now:
            return status_cache["value"]
        status = build_system_status(
            memory_manager=memory_manager,
            memory_vector=memory_vector,
            mcp_manager=mcp_manager,
            task_scheduler=task_scheduler,
            auth_manager=auth_manager,
            rag_manager=rag_manager,
            personal_docs_mgr=personal_docs_mgr,
        )
        if status_cache_ttl > 0:
            status_cache["value"] = status
            status_cache["expires_at"] = now + status_cache_ttl
        return status

    @router.post("/api/system/actions/{action_id}")
    async def run_system_action(action_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        if action_id == "memory.rebuild_semantic_index":
            result = _rebuild_memory_index(memory_manager, memory_vector, action_id)
            status_cache["expires_at"] = 0.0
            status_cache["value"] = None
            return result
        if action_id == "tool_servers.reconnect_failed":
            result = await _reconnect_failed_tool_servers(mcp_manager, action_id)
            status_cache["expires_at"] = 0.0
            status_cache["value"] = None
            return result
        raise HTTPException(404, f"Unknown system action: {action_id}")

    return router


def _rebuild_memory_index(memory_manager: Any, memory_vector: Any, action_id: str) -> dict[str, Any]:
    if memory_manager is None:
        raise HTTPException(409, "Memory manager is not attached")
    if memory_vector is None:
        raise HTTPException(409, "Semantic memory index is not attached")
    if not bool(getattr(memory_vector, "healthy", False)):
        raise HTTPException(409, "Semantic memory index is unavailable")
    if not hasattr(memory_vector, "rebuild"):
        raise HTTPException(409, "Semantic memory index does not support rebuild")

    memories = memory_manager.load_all()
    indexable = [
        mem for mem in (memories or [])
        if str((mem or {}).get("id") or "").strip()
        and str((mem or {}).get("text") or "").strip()
    ]
    memory_vector.rebuild(indexable)
    return {
        "ok": True,
        "action_id": action_id,
        "rebuilt_entries": len(indexable),
    }


async def _reconnect_failed_tool_servers(mcp_manager: Any, action_id: str) -> dict[str, Any]:
    if mcp_manager is None:
        raise HTTPException(409, "Tool-server manager is not attached")

    try:
        from core.database import McpServer, SessionLocal
    except Exception as exc:
        raise HTTPException(409, f"Tool-server storage is unavailable: {exc}") from exc

    statuses = mcp_manager.get_all_statuses() if hasattr(mcp_manager, "get_all_statuses") else {}

    db = SessionLocal()
    try:
        servers = db.query(McpServer).filter(McpServer.is_enabled == True).all()  # noqa: E712
        targets = [
            srv for srv in servers
            if not statuses or str((statuses.get(srv.id) or {}).get("status") or "disconnected") != "connected"
        ]
        results = []
        for srv in targets:
            try:
                await mcp_manager.disconnect_server(srv.id)
                connected = await mcp_manager.connect_server(
                    server_id=srv.id,
                    name=srv.name,
                    transport=srv.transport,
                    command=srv.command,
                    args=json.loads(srv.args) if srv.args else [],
                    env=json.loads(srv.env) if srv.env else {},
                    url=srv.url,
                )
                status = mcp_manager.get_server_status(srv.id)
                results.append({
                    "id": srv.id,
                    "name": srv.name,
                    "connected": bool(connected),
                    "status": status.get("status", "disconnected"),
                    "tool_count": status.get("tool_count", 0),
                    "error": status.get("error"),
                })
            except Exception as exc:
                results.append({
                    "id": srv.id,
                    "name": srv.name,
                    "connected": False,
                    "status": "error",
                    "error": str(exc),
                })
        return {
            "ok": all(item.get("connected") for item in results),
            "action_id": action_id,
            "reconnected": sum(1 for item in results if item.get("connected")),
            "attempted": len(results),
            "results": results,
        }
    finally:
        db.close()
