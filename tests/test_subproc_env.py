"""Agent subprocesses must not inherit host secrets — SECURITY-FIXLIST P1 #2.

The bash/python tools, background jobs, the shell service, and MCP stdio servers
used to spawn children with the FULL ``os.environ`` — leaking provider API keys,
``DATABASE_URL``, decrypted SMTP/IMAP passwords, ``SEARXNG_SECRET``, etc. A
prompt-injected agent or malicious skill could ``env | curl`` them out.

These tests pin the allowlist behavior of ``src.subproc_env.build_agent_env`` and
prove the real ``bash`` tool path no longer leaks.
"""
import os

import pytest

# Imported at module load (before the secret-seeding fixture runs) so the core
# import chain initializes its DB engine from the real sqlite default rather than
# the fake postgres DATABASE_URL the fixture injects.
import src.bg_jobs as bg
import src.builtin_actions as builtin_actions
import src.ralph_loop as ralph_loop
from src.subproc_env import build_agent_env


SECRETS = {
    "OPENAI_API_KEY": "sk-secret-openai",
    "ANTHROPIC_API_KEY": "sk-secret-anthropic",
    "DATABASE_URL": "postgres://user:pw@host/db",
    "SMTP_PASSWORD": "hunter2",
    "IMAP_PASSWORD": "hunter2",
    "SEARXNG_SECRET": "deadbeef",
    "MY_ACCESS_TOKEN": "tok",
    "SOME_CLIENT_SECRET": "shh",
}


@pytest.fixture
def _seeded_secrets(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    # Ensure the safe vars exist so the allowlist has something to copy.
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
    monkeypatch.setenv("HOME", os.environ.get("HOME", "/tmp"))


def test_excludes_all_secret_shaped_vars(_seeded_secrets):
    env = build_agent_env()
    for name in SECRETS:
        assert name not in env, f"{name} leaked into agent env"


def test_includes_safe_vars(_seeded_secrets):
    env = build_agent_env()
    assert env.get("PATH")
    assert env.get("HOME")


def test_extra_constants_are_added(_seeded_secrets):
    env = build_agent_env(extra={"TERM": "xterm-256color", "COLUMNS": "120"})
    assert env["TERM"] == "xterm-256color"
    assert env["COLUMNS"] == "120"


def test_passthrough_allows_nonsecret_optin(monkeypatch):
    monkeypatch.setenv("GH_HOST", "github.example.com")
    env = build_agent_env(passthrough=["GH_HOST"])
    assert env.get("GH_HOST") == "github.example.com"


def test_passthrough_cannot_optin_a_secret(monkeypatch):
    monkeypatch.setenv("EVIL_API_KEY", "sk-nope")
    env = build_agent_env(passthrough=["EVIL_API_KEY"])
    assert "EVIL_API_KEY" not in env, "denylist must override passthrough opt-in"


async def test_bash_tool_does_not_leak_secret(_seeded_secrets):
    """End-to-end: the agent's bash tool runs `env`; no host secret appears."""
    from src.tool_execution import _direct_fallback

    result = await _direct_fallback("bash", "env")
    out = result.get("output", "")
    assert "sk-secret-openai" not in out
    assert "OPENAI_API_KEY" not in out
    assert "postgres://user:pw@host/db" not in out


async def test_shell_service_does_not_leak_secret(_seeded_secrets):
    """The ShellService execute path must not inherit host secrets."""
    from services.shell.service import ShellService

    result = await ShellService().execute("env")
    assert "sk-secret-openai" not in result.stdout
    assert "OPENAI_API_KEY" not in result.stdout


def test_mcp_stdio_env_strips_secrets(_seeded_secrets):
    """MCP stdio child env (built from os.environ) must drop host secrets but
    keep the npm-quieting flags and any explicitly-configured server env."""
    from src.mcp_manager import _stdio_env

    env = _stdio_env("npx", {"MY_SERVER_OPT": "1"})
    assert "OPENAI_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert env.get("MY_SERVER_OPT") == "1"           # explicit server env preserved
    assert env.get("NPM_CONFIG_LOGLEVEL") == "silent"  # npx quieting still applied


def test_bg_job_does_not_leak_secret(_seeded_secrets, tmp_path, monkeypatch):
    """A background job runs `env`; the captured log must contain no host secret."""
    import time

    monkeypatch.setattr(bg, "_JOBS_DIR", tmp_path / "bg_jobs")
    bg._JOBS_DIR.mkdir(parents=True, exist_ok=True)

    rec = bg.launch("env", session_id="test-sess")
    job_id = rec["id"]

    # Poll until the detached job writes its exit file (bounded).
    deadline = time.time() + 15
    while time.time() < deadline:
        cur = bg.get(job_id)
        if cur and cur.get("status") in ("done", "failed"):
            break
        time.sleep(0.1)

    out = bg.result_text(bg.get(job_id))
    assert "sk-secret-openai" not in out
    assert "OPENAI_API_KEY" not in out


async def test_builtin_action_run_local_does_not_leak_secret(_seeded_secrets):
    """The agent-reachable run_local action executes `env`; no host secret leaks."""
    out, ok = await builtin_actions.action_run_local("owner", script="env")
    assert "sk-secret-openai" not in out
    assert "OPENAI_API_KEY" not in out


def test_ralph_quality_check_does_not_leak_secret(_seeded_secrets, tmp_path):
    """The Ralph verification command runs with a scrubbed env (no host secrets)."""
    result = ralph_loop.run_quality_check("env", cwd=tmp_path)
    blob = str(result)
    assert "sk-secret-openai" not in blob
    assert "OPENAI_API_KEY" not in blob
