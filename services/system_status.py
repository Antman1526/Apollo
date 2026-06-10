"""Unified read-only system status aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


StatusProvider = Callable[[], dict[str, Any]]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _component(
    *,
    label: str,
    ready: bool,
    state: str,
    summary: str,
    metrics: dict[str, Any] | None = None,
    next_step: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "label": label,
        "ready": bool(ready),
        "state": state,
        "summary": summary,
        "metrics": metrics or {},
    }
    if next_step:
        out["next_step"] = next_step
    return out


def _failed_component(label: str, exc: Exception) -> dict[str, Any]:
    return _component(
        label=label,
        ready=False,
        state="error",
        summary="Status check failed",
        metrics={"error": str(exc)},
        next_step="Open diagnostics and inspect server logs",
    )


def storage_status(readiness_provider: StatusProvider | None = None) -> dict[str, Any]:
    """Summarize database and local data-directory readiness."""
    try:
        if readiness_provider is None:
            from src.readiness import check_readiness

            readiness_provider = check_readiness
        report = readiness_provider()
        checks = report.get("checks") or {}
        ready = bool(report.get("ready"))
        failed = [name for name, check in checks.items() if not bool((check or {}).get("ok"))]
        summary = "Database and data directory are ready" if ready else "Storage readiness is blocked"
        return _component(
            label="Storage",
            ready=ready,
            state="ready" if ready else "blocked",
            summary=summary,
            metrics={
                "version": report.get("version"),
                "failed_checks": failed,
                "checks": checks,
            },
            next_step="Run the readiness check and repair failed storage paths" if failed else None,
        )
    except Exception as exc:
        return _failed_component("Storage", exc)


def memory_status(memory_manager: Any = None, memory_vector: Any = None) -> dict[str, Any]:
    """Summarize durable memory plus semantic index availability."""
    if memory_manager is None:
        return _component(
            label="Memory",
            ready=False,
            state="unavailable",
            summary="Memory manager is not attached",
            next_step="Attach the memory manager during application startup",
        )

    try:
        memories = memory_manager.load_all()
        memory_count = len(memories or [])
    except Exception as exc:
        return _component(
            label="Memory",
            ready=False,
            state="error",
            summary="Persistent memory could not be read",
            metrics={"error": str(exc)},
            next_step="Check the memory data file and permissions",
        )

    vector_attached = memory_vector is not None
    vector_ready = bool(getattr(memory_vector, "healthy", False)) if vector_attached else False
    vector_count = 0
    vector_error = None
    if vector_attached:
        try:
            vector_count = int(memory_vector.count())
        except Exception as exc:
            vector_error = str(exc)
            vector_ready = False

    ready = vector_attached and vector_ready
    if ready:
        state = "ready"
        summary = "Memory store and semantic index are ready"
        next_step = None
    elif vector_attached:
        state = "degraded"
        summary = "Persistent memory is readable, but the semantic index is unavailable"
        next_step = "Check embedding/vector-store startup logs"
    else:
        state = "degraded"
        summary = "Persistent memory is readable, but no semantic index is attached"
        next_step = "Attach the semantic memory index during startup"

    metrics: dict[str, Any] = {
        "entries": memory_count,
        "vector_attached": vector_attached,
        "vector_ready": vector_ready,
        "vector_entries": vector_count,
    }
    if vector_error:
        metrics["vector_error"] = vector_error
    return _component(
        label="Memory",
        ready=ready,
        state=state,
        summary=summary,
        metrics=metrics,
        next_step=next_step,
    )


def tool_server_status(mcp_manager: Any = None) -> dict[str, Any]:
    """Summarize configured external tool-server connections."""
    if mcp_manager is None:
        return _component(
            label="Tool Servers",
            ready=True,
            state="idle",
            summary="No tool-server manager is attached",
            next_step="Attach the tool-server manager if external tools are required",
        )

    try:
        statuses = mcp_manager.get_all_statuses()
        tools = mcp_manager.get_all_tools() if hasattr(mcp_manager, "get_all_tools") else []
    except Exception as exc:
        return _failed_component("Tool Servers", exc)

    counts: dict[str, int] = {}
    for info in (statuses or {}).values():
        status = str((info or {}).get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    configured = len(statuses or {})
    connected = counts.get("connected", 0)
    errors = counts.get("error", 0)
    ready = errors == 0 and (configured == 0 or connected > 0)
    state = "idle" if configured == 0 else "ready" if ready else "degraded"
    summary = (
        "No external tool servers configured"
        if configured == 0
        else f"{connected}/{configured} tool servers connected"
    )
    return _component(
        label="Tool Servers",
        ready=ready,
        state=state,
        summary=summary,
        metrics={
            "configured": configured,
            "connected": connected,
            "errors": errors,
            "statuses": counts,
            "tools": len(tools or []),
        },
        next_step="Reconnect failed tool servers or review their configuration" if not ready and configured else None,
    )


def terminal_status() -> dict[str, Any]:
    """Summarize terminal execution capability without executing commands."""
    try:
        from routes.shell_routes import IS_WINDOWS, PTY_SUPPORTED, _running_in_container

        in_container = bool(_running_in_container())
        ready = True
        state = "ready" if PTY_SUPPORTED else "limited"
        summary = "Interactive terminal support is available" if PTY_SUPPORTED else "Terminal runs with limited streaming support"
        return _component(
            label="Terminal",
            ready=ready,
            state=state,
            summary=summary,
            metrics={
                "pty": bool(PTY_SUPPORTED),
                "platform": "windows" if IS_WINDOWS else "posix",
                "in_container": in_container,
            },
        )
    except Exception as exc:
        return _failed_component("Terminal", exc)


def background_status(task_scheduler: Any = None) -> dict[str, Any]:
    """Summarize background task-loop liveness."""
    if task_scheduler is None:
        return _component(
            label="Background Work",
            ready=False,
            state="unavailable",
            summary="Background scheduler is not attached",
            next_step="Attach the scheduler during application startup",
        )

    running = bool(getattr(task_scheduler, "_running", False))
    task = getattr(task_scheduler, "_task", None)
    executing = getattr(task_scheduler, "_executing", set()) or set()
    concurrency_cap = getattr(task_scheduler, "_concurrency_cap", None)
    task_done = bool(task.done()) if task is not None and hasattr(task, "done") else None

    return _component(
        label="Background Work",
        ready=running and task_done is not True,
        state="ready" if running and task_done is not True else "stopped",
        summary="Background scheduler is running" if running and task_done is not True else "Background scheduler is stopped",
        metrics={
            "running": running,
            "loop_done": task_done,
            "executing": len(executing),
            "concurrency_cap": concurrency_cap,
        },
        next_step="Check application startup logs for scheduler failures" if not running or task_done is True else None,
    )


def build_system_status(
    *,
    memory_manager: Any = None,
    memory_vector: Any = None,
    mcp_manager: Any = None,
    task_scheduler: Any = None,
    readiness_provider: StatusProvider | None = None,
) -> dict[str, Any]:
    """Build a compact health model for user-facing status surfaces."""
    components = {
        "storage": storage_status(readiness_provider),
        "memory": memory_status(memory_manager, memory_vector),
        "tool_servers": tool_server_status(mcp_manager),
        "terminal": terminal_status(),
        "background": background_status(task_scheduler),
    }
    total = len(components)
    ready_count = sum(1 for component in components.values() if component.get("ready"))
    return {
        "ok": ready_count == total,
        "ready_count": ready_count,
        "total": total,
        "components": components,
        "timestamp": _utcnow_iso(),
    }
