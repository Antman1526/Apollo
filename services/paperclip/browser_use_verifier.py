"""Browser Use integration for Paperclip/Ralph browser verification.

Browser Use's documented quickstart uses ``Agent`` with ``ChatBrowserUse``.
Source: https://docs.browser-use.com/open-source/quickstart
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from urllib.parse import urlparse
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from src.observability import report_exception

logger = logging.getLogger(__name__)


class BrowserUseUnavailable(RuntimeError):
    pass


@dataclass
class BrowserUseRun:
    ok: bool
    task: str
    returncode: int
    output: str
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_available() -> bool:
    return _python_can_import(_browser_use_python())


def _browser_use_python() -> str:
    configured = os.getenv("APOLLO_BROWSER_USE_PYTHON", "").strip()
    if configured:
        return configured
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    isolated = Path(__file__).resolve().parents[2] / ".apollo" / "browser-use-venv" / scripts_dir / ("python.exe" if os.name == "nt" else "python")
    if isolated.exists():
        return str(isolated)
    return sys.executable


def _python_can_import(python: str) -> bool:
    if python == sys.executable:
        return importlib.util.find_spec("browser_use") is not None
    try:
        proc = subprocess.run(
            [python, "-c", "import browser_use"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        report_exception(
            logger,
            "browser_use_runtime_probe_failed",
            error,
            outcome="best_effort",
        )
        return False


def status(app_base_url: str | None = None) -> dict[str, Any]:
    local = local_model_config(app_base_url)
    return {
        "available": is_available(),
        "package": "browser-use",
        "python": _browser_use_python(),
        "install_hint": "scripts/setup-browser-use-env && export APOLLO_BROWSER_USE_PYTHON=.apollo/browser-use-venv/bin/python",
        "api_key_present": bool(os.getenv("BROWSER_USE_API_KEY")),
        "llm_provider": os.getenv("APOLLO_BROWSER_USE_LLM_PROVIDER", "local").strip().lower(),
        "local_model": {
            "model": local["model"],
            "base_url": local["base_url"],
            "api_key_present": bool(local["api_key"]),
        },
        "purpose": "Browser-agent verification for Paperclip Floor, Ralph tasks, and Apollo UI workflows.",
    }


def _default_lmproxy_base_url(app_base_url: str | None = None) -> str:
    configured = os.getenv("APOLLO_BROWSER_USE_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    if app_base_url:
        parsed = urlparse(app_base_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/lmproxy/v1"
    port = os.getenv("APP_PORT", "7000")
    return f"http://127.0.0.1:{port}/lmproxy/v1"


def _default_lmproxy_token() -> str:
    explicit = os.getenv("APOLLO_BROWSER_USE_API_KEY", "").strip()
    if explicit:
        return explicit
    try:
        from services.paperclip.config import resolve_proxy_token
        return resolve_proxy_token()
    except Exception as error:
        report_exception(
            logger,
            "browser_use_proxy_token_resolve_failed",
            error,
            outcome="best_effort",
        )
        return os.getenv("PAPERCLIP_PROXY_TOKEN", "").strip() or os.getenv("PAPERCLIP_MODEL_API_KEY", "").strip()


def local_model_config(app_base_url: str | None = None) -> dict[str, str]:
    raw_model = (
        os.getenv("APOLLO_BROWSER_USE_MODEL", "").strip()
        or os.getenv("PAPERCLIP_MODEL_NAME", "").strip()
        or os.getenv("APOLLO_LOCAL_MODEL_ID", "").strip()
        or "local"
    )
    model = raw_model if "/" in raw_model else f"openai/{raw_model}"
    return {
        "provider": "local",
        "model": model,
        "base_url": _default_lmproxy_base_url(app_base_url),
        "api_key": _default_lmproxy_token(),
    }


def paperclip_floor_task(base_url: str) -> str:
    username = os.getenv("APOLLO_BROWSER_USE_USERNAME", "")
    password = os.getenv("APOLLO_BROWSER_USE_PASSWORD", "")
    auth_hint = ""
    if username or password:
        auth_hint = (
            f"If a login form appears, sign in with username {username!r} "
            f"and password {password!r}. "
        )
    return (
        f"Open {base_url.rstrip('/')}. {auth_hint}"
        "Open the Paperclip sidebar tab or launcher. Verify the Floor view renders, "
        "that small Lego-like agents are visible, and that at least one agent is "
        "walking, talking, or sitting at a desk. Report concise PASS/FAIL findings."
    )


def run_browser_use_task(
    task: str,
    *,
    app_base_url: str | None = None,
    timeout_seconds: float = 180,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> BrowserUseRun:
    if not is_available():
        raise BrowserUseUnavailable(status()["install_hint"])
    script = """
import asyncio
import json
import os
import sys
from browser_use import Agent
from browser_use.llm.browser_use.chat import ChatBrowserUse
from browser_use.llm.litellm.chat import ChatLiteLLM

async def main():
    task = sys.stdin.read()
    provider = os.environ.get("APOLLO_BROWSER_USE_LLM_PROVIDER", "local").strip().lower()
    if provider == "browser-use":
        llm = ChatBrowserUse(
            model=os.environ.get("APOLLO_BROWSER_USE_MODEL", "bu-2-0"),
            api_key=os.environ.get("BROWSER_USE_API_KEY") or None,
            base_url=os.environ.get("BROWSER_USE_BASE_URL") or None,
        )
    else:
        llm = ChatLiteLLM(
            model=os.environ["APOLLO_BROWSER_USE_MODEL"],
            api_key=os.environ.get("APOLLO_BROWSER_USE_API_KEY") or None,
            api_base=os.environ.get("APOLLO_BROWSER_USE_BASE_URL") or None,
        )
    agent = Agent(task=task, llm=llm)
    history = await agent.run()
    final = history.final_result() if hasattr(history, "final_result") else str(history)
    print(json.dumps({"final_result": final}, ensure_ascii=False))

asyncio.run(main())
"""
    env = dict(os.environ)
    if env.get("APOLLO_BROWSER_USE_LLM_PROVIDER", "local").strip().lower() != "browser-use":
        local = local_model_config(app_base_url)
        env["APOLLO_BROWSER_USE_MODEL"] = local["model"]
        env["APOLLO_BROWSER_USE_BASE_URL"] = local["base_url"]
        if local["api_key"]:
            env["APOLLO_BROWSER_USE_API_KEY"] = local["api_key"]
    try:
        proc = runner(
            [_browser_use_python(), "-c", script],
            input=task,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or f"browser-use task timed out after {timeout_seconds:g}s"
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return BrowserUseRun(False, task, 124, out, timed_out=True)
    return BrowserUseRun(proc.returncode == 0, task, proc.returncode, proc.stdout or "")


def verify_paperclip_floor(
    base_url: str,
    *,
    timeout_seconds: float = 180,
    output_path: str | os.PathLike[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> BrowserUseRun:
    task = paperclip_floor_task(base_url)
    result = run_browser_use_task(task, app_base_url=base_url, timeout_seconds=timeout_seconds, runner=runner)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result
