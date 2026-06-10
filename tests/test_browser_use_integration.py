import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from services.paperclip import browser_use_verifier


def _load_check_paperclip_browser():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "check-paperclip-browser"
    loader = SourceFileLoader("check_paperclip_browser", str(script_path))
    spec = importlib.util.spec_from_loader("check_paperclip_browser", loader)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_browser_use_paperclip_task_mentions_floor_and_agents(monkeypatch):
    monkeypatch.setenv("APOLLO_BROWSER_USE_USERNAME", "admin")
    task = browser_use_verifier.paperclip_floor_task("http://127.0.0.1:7000/")

    assert "Paperclip" in task
    assert "Floor view" in task
    assert "Lego-like agents" in task
    assert "admin" in task


def test_browser_use_runner_success(monkeypatch):
    monkeypatch.setattr(browser_use_verifier, "is_available", lambda: True)
    monkeypatch.setenv("APOLLO_BROWSER_USE_PYTHON", sys.executable)
    monkeypatch.setenv("APOLLO_BROWSER_USE_MODEL", "qwen-local")
    monkeypatch.setenv("APOLLO_BROWSER_USE_API_KEY", "local-token")

    def fake_runner(cmd, **kwargs):
        assert cmd[0] == sys.executable
        assert "browser_use" in cmd[2]
        assert "ChatLiteLLM" in cmd[2]
        assert "Verify" in kwargs["input"]
        assert kwargs["env"]["APOLLO_BROWSER_USE_MODEL"] == "openai/qwen-local"
        assert kwargs["env"]["APOLLO_BROWSER_USE_API_KEY"] == "local-token"
        assert kwargs["env"]["APOLLO_BROWSER_USE_BASE_URL"].endswith("/lmproxy/v1")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final_result":"PASS"}\n')

    result = browser_use_verifier.run_browser_use_task(
        "Verify Paperclip",
        app_base_url="http://127.0.0.1:7002",
        runner=fake_runner,
    )

    assert result.ok is True
    assert result.returncode == 0
    assert "PASS" in result.output


def test_browser_use_runner_timeout(monkeypatch):
    monkeypatch.setattr(browser_use_verifier, "is_available", lambda: True)

    def fake_runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout=1, output="partial")

    result = browser_use_verifier.run_browser_use_task("Verify Paperclip", timeout_seconds=1, runner=fake_runner)

    assert result.ok is False
    assert result.returncode == 124
    assert result.timed_out is True
    assert result.output == "partial"


def test_local_model_config_uses_apollo_lmproxy(monkeypatch):
    monkeypatch.delenv("APOLLO_BROWSER_USE_BASE_URL", raising=False)
    monkeypatch.setenv("APOLLO_BROWSER_USE_MODEL", "llama3")
    monkeypatch.setattr(browser_use_verifier, "_default_lmproxy_token", lambda: "tok")

    cfg = browser_use_verifier.local_model_config("http://127.0.0.1:7860")

    assert cfg["model"] == "openai/llama3"
    assert cfg["base_url"] == "http://127.0.0.1:7860/lmproxy/v1"
    assert cfg["api_key"] == "tok"


def test_browser_use_python_prefers_isolated_env_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv("APOLLO_BROWSER_USE_PYTHON", raising=False)
    fake_repo = tmp_path / "repo"
    fake_python = fake_repo / ".apollo" / "browser-use-venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")

    class _FakePath:
        @staticmethod
        def resolve():
            return fake_repo / "services" / "paperclip" / "browser_use_verifier.py"

    monkeypatch.setattr(browser_use_verifier, "__file__", str(fake_repo / "services" / "paperclip" / "browser_use_verifier.py"))

    assert browser_use_verifier._browser_use_python() == str(fake_python)


def test_check_paperclip_browser_cli_sets_local_model_env(monkeypatch, capsys):
    check_paperclip_browser = _load_check_paperclip_browser()
    calls = {}

    def fake_status(app_base_url=None):
        calls["app_base_url"] = app_base_url
        calls["model"] = browser_use_verifier.local_model_config(app_base_url)["model"]
        calls["base_url"] = browser_use_verifier.local_model_config(app_base_url)["base_url"]
        return {"ok": True}

    monkeypatch.setattr(check_paperclip_browser.browser_use_verifier, "status", fake_status)

    rc = check_paperclip_browser.main([
        "--status",
        "--base-url",
        "http://127.0.0.1:7002",
        "--model",
        "llama3",
        "--model-api-key",
        "tok",
    ])

    assert rc == 0
    assert calls == {
        "app_base_url": "http://127.0.0.1:7002",
        "model": "openai/llama3",
        "base_url": "http://127.0.0.1:7002/lmproxy/v1",
    }
    assert '"ok": true' in capsys.readouterr().out


def test_browser_use_runner_unavailable(monkeypatch):
    monkeypatch.setattr(browser_use_verifier, "is_available", lambda: False)

    with pytest.raises(browser_use_verifier.BrowserUseUnavailable):
        browser_use_verifier.run_browser_use_task("Verify Paperclip")
