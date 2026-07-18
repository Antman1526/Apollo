"""Vault tool subprocesses must receive only their required session secret."""

import asyncio
import json

from src.tools import vault


def test_vault_tool_uses_scrubbed_subprocess_environment(monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            return b"ok", b""

    async def fake_exec(*_argv, env=None, **_kwargs):
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(vault._run_bw(["status"], session="vault-session"))

    assert captured["env"]["BW_SESSION"] == "vault-session"
    assert "OPENAI_API_KEY" not in captured["env"]


def test_vault_tool_ignores_non_mapping_config(tmp_path, monkeypatch):
    config = tmp_path / "vault.json"
    config.write_text(json.dumps(["invalid", "shape"]), encoding="utf-8")
    monkeypatch.setattr(vault, "data_path", lambda _name: config)

    assert vault._load_vault_config() == {}
