"""Unified read-only system status aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Callable

from src.observability import report_exception


StatusProvider = Callable[[], dict[str, Any]]
logger = logging.getLogger(__name__)


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
    actions: list[dict[str, Any]] | None = None,
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
    if actions:
        out["actions"] = actions
    return out


def _action(
    action_id: str,
    label: str,
    *,
    endpoint: str | None = None,
    method: str = "POST",
    kind: str = "repair",
    confirm: str | None = None,
) -> dict[str, Any]:
    out = {
        "id": action_id,
        "label": label,
        "method": method,
        "endpoint": endpoint or f"/api/system/actions/{action_id}",
        "kind": kind,
    }
    if confirm:
        out["confirm"] = confirm
    return out


def _failed_component(label: str, exc: Exception) -> dict[str, Any]:
    return _component(
        label=label,
        ready=False,
        state="error",
        summary="Status check failed",
        metrics={"error": "Status check failed", "error_type": type(exc).__name__},
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
        report_exception(logger, "system_status_storage_check_failed", exc, outcome="degraded")
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
        indexable_count = len([
            mem for mem in (memories or [])
            if str((mem or {}).get("id") or "").strip()
            and str((mem or {}).get("text") or "").strip()
        ])
    except Exception as exc:
        report_exception(logger, "system_status_memory_load_failed", exc, outcome="degraded")
        return _component(
            label="Memory",
            ready=False,
            state="error",
            summary="Persistent memory could not be read",
            metrics={"error": "Persistent memory could not be read", "error_type": type(exc).__name__},
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
            report_exception(logger, "system_status_memory_vector_count_failed", exc, outcome="degraded")
            vector_error = "Vector index count is unavailable"
            vector_ready = False

    vector_drift = abs(indexable_count - vector_count) if vector_attached and vector_ready else 0
    ready = vector_attached and vector_ready and vector_drift == 0
    actions: list[dict[str, Any]] = []
    if ready:
        state = "ready"
        summary = "Memory store and semantic index are ready"
        next_step = None
    elif vector_attached and vector_ready and vector_drift:
        state = "degraded"
        summary = "Persistent memory is readable, but the semantic index is out of sync"
        next_step = "Rebuild the semantic memory index"
        actions.append(_action(
            "memory.rebuild_semantic_index",
            "Rebuild index",
            confirm="Rebuild the semantic memory index from saved memory entries?",
        ))
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
        "indexable_entries": indexable_count,
        "vector_attached": vector_attached,
        "vector_ready": vector_ready,
        "vector_entries": vector_count,
        "vector_drift": vector_drift,
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
        actions=actions,
    )


def auth_status(auth_manager: Any = None) -> dict[str, Any]:
    """Summarize account/auth configuration without exposing user details."""
    if auth_manager is None:
        return _component(
            label="Auth",
            ready=True,
            state="disabled",
            summary="Authentication manager is not attached",
            metrics={"configured": False, "users": 0, "admins": 0, "active_sessions": 0},
        )

    try:
        users = getattr(auth_manager, "users", {}) or {}
        admins = sum(1 for data in users.values() if isinstance(data, dict) and data.get("is_admin"))
        active_sessions = len(getattr(auth_manager, "_sessions", {}) or {})
        configured = bool(getattr(auth_manager, "is_configured", False))
        ready = (not configured) or admins > 0
        return _component(
            label="Auth",
            ready=ready,
            state="ready" if ready else "blocked",
            summary="Authentication is configured" if configured else "Authentication is not configured",
            metrics={
                "configured": configured,
                "users": len(users),
                "admins": admins,
                "active_sessions": active_sessions,
                "signup_enabled": bool(getattr(auth_manager, "signup_enabled", False)),
            },
            next_step="Create or repair an admin account" if configured and admins == 0 else None,
        )
    except Exception as exc:
        report_exception(logger, "system_status_auth_check_failed", exc, outcome="degraded")
        return _failed_component("Auth", exc)


def email_status() -> dict[str, Any]:
    """Summarize configured mail accounts."""
    try:
        from core.database import EmailAccount, SessionLocal

        db = SessionLocal()
        try:
            total = db.query(EmailAccount).count()
            enabled = db.query(EmailAccount).filter(EmailAccount.enabled == True).count()  # noqa: E712
            defaults = db.query(EmailAccount).filter(EmailAccount.is_default == True).count()  # noqa: E712
        finally:
            db.close()
        ready = total == 0 or enabled > 0
        state = "idle" if total == 0 else "ready" if ready else "degraded"
        summary = "No mail accounts configured" if total == 0 else f"{enabled}/{total} mail accounts enabled"
        return _component(
            label="Email",
            ready=ready,
            state=state,
            summary=summary,
            metrics={"accounts": total, "enabled": enabled, "defaults": defaults},
            next_step="Enable or repair at least one mail account" if total and not enabled else None,
        )
    except Exception as exc:
        report_exception(logger, "system_status_email_check_failed", exc, outcome="degraded")
        return _failed_component("Email", exc)


def documents_status() -> dict[str, Any]:
    """Summarize document-store rows."""
    try:
        from core.database import Document, SessionLocal

        db = SessionLocal()
        try:
            total = db.query(Document).count()
            active = db.query(Document).filter(Document.is_active == True).count()  # noqa: E712
            archived = db.query(Document).filter(Document.archived == True).count()  # noqa: E712
        finally:
            db.close()
        return _component(
            label="Documents",
            ready=True,
            state="idle" if total == 0 else "ready",
            summary="No documents stored" if total == 0 else f"{total} documents stored",
            metrics={"documents": total, "active": active, "archived": archived},
        )
    except Exception as exc:
        report_exception(logger, "system_status_documents_check_failed", exc, outcome="degraded")
        return _failed_component("Documents", exc)


def model_endpoint_status() -> dict[str, Any]:
    """Summarize configured model endpoints and cached model lists."""
    try:
        from core.database import ModelEndpoint, SessionLocal

        db = SessionLocal()
        try:
            endpoints = db.query(ModelEndpoint).all()
        finally:
            db.close()

        enabled = [ep for ep in endpoints if getattr(ep, "is_enabled", False)]
        cached = 0
        tool_capable = 0
        model_count = 0
        for ep in enabled:
            if getattr(ep, "supports_tools", None) is True:
                tool_capable += 1
            raw = getattr(ep, "cached_models", None)
            if not raw:
                continue
            try:
                models = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as exc:
                report_exception(
                    logger,
                    "system_status_cached_models_parse_failed",
                    exc,
                    outcome="best_effort",
                    context={"endpoint_id": getattr(ep, "id", None)},
                )
                models = []
            if isinstance(models, list) and models:
                cached += 1
                model_count += len(models)

        ready = len(enabled) == 0 or cached > 0
        state = "idle" if not enabled else "ready" if ready else "degraded"
        summary = "No model endpoints enabled" if not enabled else f"{cached}/{len(enabled)} endpoints have cached models"
        return _component(
            label="Models",
            ready=ready,
            state=state,
            summary=summary,
            metrics={
                "endpoints": len(endpoints),
                "enabled": len(enabled),
                "with_cached_models": cached,
                "cached_models": model_count,
                "tool_capable": tool_capable,
            },
            next_step="Refresh model endpoint caches or check endpoint connectivity" if enabled and cached == 0 else None,
        )
    except Exception as exc:
        report_exception(logger, "system_status_models_check_failed", exc, outcome="degraded")
        return _failed_component("Models", exc)


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
        report_exception(logger, "system_status_tool_servers_check_failed", exc, outcome="degraded")
        return _failed_component("Tool Servers", exc)

    counts: dict[str, int] = {}
    for info in (statuses or {}).values():
        status = str((info or {}).get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    configured = len(statuses or {})
    connected = counts.get("connected", 0)
    errors = counts.get("error", 0)
    disconnected = configured - connected
    ready = errors == 0 and (configured == 0 or connected == configured)
    state = "idle" if configured == 0 else "ready" if ready else "degraded"
    summary = (
        "No external tool servers configured"
        if configured == 0
        else f"{connected}/{configured} tool servers connected"
    )
    actions = []
    if not ready and configured:
        actions.append(_action(
            "tool_servers.reconnect_failed",
            "Reconnect failed",
            confirm="Reconnect failed or disconnected tool servers?",
        ))
    return _component(
        label="Tool Servers",
        ready=ready,
        state=state,
        summary=summary,
        metrics={
            "configured": configured,
            "connected": connected,
            "disconnected": disconnected,
            "errors": errors,
            "statuses": counts,
            "tools": len(tools or []),
        },
        next_step="Reconnect failed tool servers or review their configuration" if not ready and configured else None,
        actions=actions,
    )


def search_status(rag_manager: Any = None, personal_docs_mgr: Any = None) -> dict[str, Any]:
    """Summarize global and personal search/index availability."""
    metrics: dict[str, Any] = {}
    degraded: list[str] = []

    if rag_manager is None:
        degraded.append("global_index_unavailable")
        metrics["global_index"] = {"available": False}
    else:
        try:
            metrics["global_index"] = {"available": True, "stats": rag_manager.get_stats()}
        except Exception as exc:
            report_exception(logger, "system_status_global_index_check_failed", exc, outcome="degraded")
            degraded.append("global_index_error")
            metrics["global_index"] = {"available": False, "error": "Status check failed", "error_type": type(exc).__name__}

    if personal_docs_mgr is None:
        metrics["personal_docs"] = {"available": False}
    else:
        try:
            metrics["personal_docs"] = {"available": True, "stats": personal_docs_mgr.get_stats()}
        except Exception as exc:
            report_exception(logger, "system_status_personal_docs_check_failed", exc, outcome="degraded")
            degraded.append("personal_docs_error")
            metrics["personal_docs"] = {"available": False, "error": "Status check failed", "error_type": type(exc).__name__}

    ready = not degraded
    return _component(
        label="Search Index",
        ready=ready,
        state="ready" if ready else "degraded",
        summary="Search indexes are reporting stats" if ready else "One or more search indexes need attention",
        metrics=metrics,
        next_step="Rebuild indexes or inspect indexing logs" if degraded else None,
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
        report_exception(logger, "system_status_terminal_check_failed", exc, outcome="degraded")
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
    db_metrics: dict[str, Any] = {}
    db_error = None

    try:
        from core.database import ScheduledTask, SessionLocal, TaskRun

        db = SessionLocal()
        try:
            now = datetime.utcnow()
            stale_cutoff = now - timedelta(hours=2)
            db_metrics = {
                "active_tasks": db.query(ScheduledTask).filter(ScheduledTask.status == "active").count(),
                "paused_tasks": db.query(ScheduledTask).filter(ScheduledTask.status == "paused").count(),
                "due_tasks": db.query(ScheduledTask).filter(
                    ScheduledTask.status == "active",
                    ScheduledTask.next_run.isnot(None),
                    ScheduledTask.next_run <= now,
                ).count(),
                "queued_or_running_runs": db.query(TaskRun).filter(TaskRun.status.in_(("queued", "running"))).count(),
                "recent_failed_runs": db.query(TaskRun).filter(
                    TaskRun.status == "error",
                    TaskRun.started_at >= now - timedelta(hours=24),
                ).count(),
                "stuck_runs": db.query(TaskRun).filter(
                    TaskRun.status.in_(("queued", "running")),
                    TaskRun.started_at < stale_cutoff,
                ).count(),
            }
            latest = db.query(TaskRun).order_by(TaskRun.started_at.desc()).first()
            if latest is not None:
                started_at = getattr(latest, "started_at", None)
                db_metrics["last_run_at"] = started_at.isoformat() if started_at else None
                db_metrics["last_run_status"] = getattr(latest, "status", None)
        finally:
            db.close()
    except Exception as exc:
        report_exception(logger, "system_status_background_database_check_failed", exc, outcome="best_effort")
        db_error = "Background work database metrics are unavailable"

    ready = running and task_done is not True and not db_metrics.get("stuck_runs")
    state = "ready" if ready else "degraded" if running and task_done is not True else "stopped"
    next_step = None
    if task_done is True or not running:
        next_step = "Check application startup logs for scheduler failures"
    elif db_metrics.get("stuck_runs"):
        next_step = "Inspect stuck task runs and cancel or retry them"
    elif db_error:
        next_step = "Inspect database access for task observability"
    return _component(
        label="Background Work",
        ready=ready,
        state=state,
        summary="Background scheduler is running" if running and task_done is not True else "Background scheduler is stopped",
        metrics={
            "running": running,
            "loop_done": task_done,
            "executing": len(executing),
            "concurrency_cap": concurrency_cap,
            **db_metrics,
            **({"observability_error": db_error} if db_error else {}),
        },
        next_step=next_step,
    )


def build_system_status(
    *,
    memory_manager: Any = None,
    memory_vector: Any = None,
    mcp_manager: Any = None,
    task_scheduler: Any = None,
    auth_manager: Any = None,
    rag_manager: Any = None,
    personal_docs_mgr: Any = None,
    readiness_provider: StatusProvider | None = None,
) -> dict[str, Any]:
    """Build a compact health model for user-facing status surfaces."""
    components = {
        "storage": storage_status(readiness_provider),
        "auth": auth_status(auth_manager),
        "memory": memory_status(memory_manager, memory_vector),
        "email": email_status(),
        "documents": documents_status(),
        "models": model_endpoint_status(),
        "search": search_status(rag_manager, personal_docs_mgr),
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
