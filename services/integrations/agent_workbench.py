"""Health summary for Apollo's agent-workbench integrations."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

from services.paperclip import browser_use_verifier
from services.browser import embedded_browser
from services.research import crawl4ai_adapter
from src import ralph_loop
from src.observability import report_exception

logger = logging.getLogger(__name__)


def _ok(value: Any) -> bool:
    return bool(value)


def _state(ok: bool, configured: bool = True) -> str:
    if ok:
        return "ready"
    return "needs_setup" if not configured else "degraded"


def _ralph_status(root: str | os.PathLike[str] = ralph_loop.DEFAULT_RALPH_DIR) -> dict[str, Any]:
    paths = ralph_loop.paths_for(root)
    if not paths.prd.exists():
        return {
            "state": "needs_setup",
            "ready": False,
            "root": str(paths.root),
            "summary": None,
            "next_step": "Run scripts/apollo-ralph init",
        }
    try:
        summary = ralph_loop.status_summary(ralph_loop.load_prd(paths.prd))
    except Exception as exc:
        report_exception(
            logger,
            "agent_workbench_ralph_status_failed",
            exc,
            outcome="degraded",
            context={"ralph_root": str(paths.root)},
        )
        return {
            "state": "degraded",
            "ready": False,
            "root": str(paths.root),
            "summary": None,
            "error": "Unable to read Ralph status",
            "next_step": "Fix .apollo/ralph/prd.json, then run scripts/apollo-ralph status",
        }
    return {
        "state": "ready",
        "ready": True,
        "root": str(paths.root),
        "summary": summary,
        "next_step": "Run scripts/apollo-ralph next --prompt" if not summary.get("done") else "All Ralph stories are complete",
    }


def status(
    *,
    app_base_url: str | None = None,
    paperclip_status: dict[str, Any] | None = None,
    browser_use_status: dict[str, Any] | None = None,
    embedded_browser_status: dict[str, Any] | None = None,
    crawl4ai_status: dict[str, Any] | None = None,
    ralph_root: str | os.PathLike[str] = ralph_loop.DEFAULT_RALPH_DIR,
) -> dict[str, Any]:
    """Return one no-side-effect readiness summary for agent workflows."""
    browser_use = browser_use_status or browser_use_verifier.status(app_base_url)
    browser = embedded_browser_status or embedded_browser.status()
    crawl4ai = crawl4ai_status or crawl4ai_adapter.status()
    ralph = _ralph_status(ralph_root)
    paperclip = paperclip_status or {}

    components = {
        "paperclip": {
            "state": _state(
                _ok(paperclip.get("enabled")) and paperclip.get("reachable") is not False,
                configured=_ok(paperclip.get("enabled")),
            ),
            "ready": _ok(paperclip.get("enabled")) and paperclip.get("reachable") is not False,
            "enabled": bool(paperclip.get("enabled")),
            "reachable": paperclip.get("reachable"),
            "mode": paperclip.get("mode"),
            "browser_url": paperclip.get("browser_url"),
            "model_endpoint": paperclip.get("model_endpoint"),
            "next_step": "Enable PAPERCLIP_ENABLED=true" if not paperclip.get("enabled") else "Open the Paperclip Floor",
        },
        "browser_use": {
            "state": _state(_ok(browser_use.get("available")), configured=True),
            "ready": bool(browser_use.get("available")),
            "python": browser_use.get("python"),
            "llm_provider": browser_use.get("llm_provider"),
            "local_model": browser_use.get("local_model"),
            "next_step": "Run scripts/setup-browser-use-env" if not browser_use.get("available") else "Run scripts/check-paperclip-browser --dry-run",
        },
        "embedded_browser": {
            "state": _state(_ok(browser.get("available")), configured=True),
            "ready": bool(browser.get("available")),
            "package": browser.get("package"),
            "engine": browser.get("engine"),
            "headless": browser.get("headless"),
            "next_step": browser.get("install_hint") if not browser.get("available") else "Open the Browser panel or call the browser agent tool",
        },
        "crawl4ai": {
            "state": _state(_ok(crawl4ai.get("available")), configured=True),
            "ready": bool(crawl4ai.get("available")),
            "package": crawl4ai.get("package"),
            "next_step": "Install requirements.txt" if not crawl4ai.get("available") else "Run scripts/apollo-research crawl <url>",
        },
        "ralph": ralph,
    }
    ready_count = sum(1 for item in components.values() if item.get("ready"))
    total = len(components)
    return {
        "ok": ready_count == total,
        "ready_count": ready_count,
        "total": total,
        "app_base_url": app_base_url or "",
        "components": components,
    }
