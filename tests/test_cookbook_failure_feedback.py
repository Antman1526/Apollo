"""Cookbook failure feedback: redacted launch command, dead-session log
fallback, wider output tail, and diagnosis for every task type."""

import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import (
    ModelDownloadRequest,
    read_session_log_tail,
    redact_command,
)


# ── redact_command ──

@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("hf download org/m --token hf_abcdefghijklmnopqrstuvwxyz0123456789", "hf download org/m --token [redacted]"),
        ("HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz0123456789 hf download org/m", "HF_TOKEN=[redacted] hf download org/m"),
        ("echo hf_abcdefghijklmnopqrstuvwxyz0123456789", "echo [redacted-hf-token]"),
        ("vllm serve org/m --api-key supersecret123", "vllm serve org/m --api-key [redacted]"),
        ('curl -H "Authorization: Bearer abcdefgh12345678"', 'curl -H "Authorization: Bearer [redacted]"'),
        ("hf download org/m --include '*.gguf' --local-dir ~/models/m", "hf download org/m --include '*.gguf' --local-dir ~/models/m"),
        ("pip install hf_transfer", "pip install hf_transfer"),
        ("", ""),
    ],
)
def test_redact_command(cmd, expected):
    assert redact_command(cmd) == expected


# ── read_session_log_tail ──

def test_read_session_log_tail_returns_last_n_lines(tmp_path):
    (tmp_path / "cookbook-abc.log").write_text("\n".join(f"line {i}" for i in range(500)) + "\n", encoding="utf-8")
    out = read_session_log_tail("cookbook-abc", max_lines=3, log_dir=tmp_path)
    assert out == "line 497\nline 498\nline 499"
    assert len(read_session_log_tail("cookbook-abc", log_dir=tmp_path).splitlines()) == 400


def test_read_session_log_tail_strips_ansi_and_carriage_returns(tmp_path):
    (tmp_path / "serve-1.log").write_bytes(b"\x1b[32mhello\x1b[0m\nprogress 10%\rprogress 90%\n=== Process exited with code 1 ===\n")
    out = read_session_log_tail("serve-1", log_dir=tmp_path)
    assert out == "hello\nprogress 90%\n=== Process exited with code 1 ==="


def test_read_session_log_tail_rejects_bad_ids_and_traversal(tmp_path):
    (tmp_path / "secret.log").write_text("nope", encoding="utf-8")
    outside = tmp_path.parent / "outside.log"
    outside.write_text("outside", encoding="utf-8")
    try:
        assert read_session_log_tail("../outside", log_dir=tmp_path) == ""
        assert read_session_log_tail("a/../../outside", log_dir=tmp_path) == ""
        assert read_session_log_tail("x; rm -rf ~", log_dir=tmp_path) == ""
        assert read_session_log_tail("", log_dir=tmp_path) == ""
        assert read_session_log_tail("..", log_dir=tmp_path) == ""
    finally:
        outside.unlink()


def test_read_session_log_tail_missing_file_is_empty(tmp_path):
    assert read_session_log_tail("cookbook-missing", log_dir=tmp_path) == ""


def test_read_session_log_tail_reads_only_end_of_large_file(tmp_path):
    big = ("x" * 100 + "\n") * 5000  # ~500 KB
    (tmp_path / "big.log").write_text(big + "last line\n", encoding="utf-8")
    out = read_session_log_tail("big", max_lines=2, log_dir=tmp_path)
    assert out.endswith("last line")


# ── tasks/status ──

def _status_router(monkeypatch, tmp_path, tasks):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cookbook_state.json").write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(cookbook_routes, "IS_WINDOWS", False)
    log_dir = tmp_path / "apollo-tmux"
    log_dir.mkdir()
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", log_dir)
    import routes.shell_routes as shell_routes
    monkeypatch.setattr(shell_routes, "TMUX_LOG_DIR", log_dir)
    router = cookbook_routes.setup_cookbook_routes()
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/cookbook/tasks/status")
    return endpoint, log_dir


def _fake_subprocess_run(monkeypatch, *, alive: bool, pane: str = ""):
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=0 if alive else 1, stdout="", stderr="")
        if cmd[:2] == ["tmux", "capture-pane"]:
            return SimpleNamespace(returncode=0, stdout=pane, stderr="")
        # _download_cache_complete probe: nothing cached
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_status_falls_back_to_session_log_when_tmux_session_is_dead(monkeypatch, tmp_path):
    endpoint, log_dir = _status_router(
        monkeypatch, tmp_path,
        [{"sessionId": "cookbook-dead1", "type": "download", "name": "m", "payload": {"repo_id": "org/m", "_cmd": "hf download org/m"}}],
    )
    (log_dir / "cookbook-dead1.log").write_text(
        "Installing huggingface-hub...\nERROR: could not resolve host huggingface.co\n\nDOWNLOAD_FAILED (exit 1)\n",
        encoding="utf-8",
    )
    calls = _fake_subprocess_run(monkeypatch, alive=False)

    out = asyncio.run(endpoint(SimpleNamespace()))
    task = out["tasks"][0]
    assert task["status"] == "error"
    assert "could not resolve host" in task["output_tail"]
    assert task["cmd"] == "hf download org/m"
    assert not any(c[:2] == ["tmux", "capture-pane"] for c in calls)


def test_status_dead_session_without_log_is_stopped(monkeypatch, tmp_path):
    endpoint, _ = _status_router(
        monkeypatch, tmp_path,
        [{"sessionId": "serve-dead2", "type": "serve", "name": "m", "payload": {"repo_id": "org/m", "_cmd": "vllm serve org/m"}}],
    )
    _fake_subprocess_run(monkeypatch, alive=False)
    task = asyncio.run(endpoint(SimpleNamespace()))["tasks"][0]
    assert task["status"] == "stopped"
    assert task["output_tail"] == ""
    assert task["cmd"] == "vllm serve org/m"


def test_status_output_tail_is_last_80_lines_and_capture_is_400(monkeypatch, tmp_path):
    endpoint, _ = _status_router(
        monkeypatch, tmp_path,
        [{"sessionId": "serve-live", "type": "serve", "name": "m", "payload": {"repo_id": "org/m"}}],
    )
    pane = "\n".join(f"log {i}" for i in range(300))
    calls = _fake_subprocess_run(monkeypatch, alive=True, pane=pane)
    task = asyncio.run(endpoint(SimpleNamespace()))["tasks"][0]
    tail = task["output_tail"].splitlines()
    assert len(tail) == 80
    assert tail[0] == "log 220" and tail[-1] == "log 299"
    capture = next(c for c in calls if c[:2] == ["tmux", "capture-pane"])
    assert capture[-2:] == ["-S", "-400"]


def test_status_diagnoses_download_tasks(monkeypatch, tmp_path):
    endpoint, _ = _status_router(
        monkeypatch, tmp_path,
        [{"sessionId": "cookbook-gated", "type": "download", "name": "m", "payload": {"repo_id": "org/m"}}],
    )
    pane = "Fetching 3 files\n403 Forbidden: gated repo. Access to model org/m is restricted\n"
    _fake_subprocess_run(monkeypatch, alive=True, pane=pane)
    task = asyncio.run(endpoint(SimpleNamespace()))["tasks"][0]
    assert task["diagnosis"] is not None
    assert task["diagnosis"]["message"] == "Model access is gated or unauthorized."
    assert task["status"] == "error"


def test_status_traceback_message_is_generic_for_downloads(monkeypatch, tmp_path):
    endpoint, log_dir = _status_router(
        monkeypatch, tmp_path,
        [{"sessionId": "cookbook-tb", "type": "download", "name": "pip x", "payload": {"repo_id": "x", "_dep": True}}],
    )
    (log_dir / "cookbook-tb.log").write_text(
        "Traceback (most recent call last):\n  File x\nValueError: boom\n=== Process exited with code 1 ===\n",
        encoding="utf-8",
    )
    _fake_subprocess_run(monkeypatch, alive=False)
    task = asyncio.run(endpoint(SimpleNamespace()))["tasks"][0]
    assert task["status"] == "error"
    assert task["diagnosis"]["message"] == "Python traceback detected in the job output."


# ── /api/model/download response ──

def test_download_response_includes_redacted_cmd_and_pipes_pane(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda r: None)
    monkeypatch.setattr(cookbook_routes, "IS_WINDOWS", False)
    log_dir = tmp_path / "apollo-tmux"
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", log_dir)
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cookbook_routes.sys, "executable", str(tmp_path / "venv" / "bin" / "python3"))

    launched = []

    async def fake_shell(cmd, **kwargs):
        launched.append(cmd)

        class _Proc:
            returncode = 0

            async def wait(self):
                return 0

        return _Proc()

    monkeypatch.setattr(cookbook_routes.asyncio, "create_subprocess_shell", fake_shell)

    router = cookbook_routes.setup_cookbook_routes()
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/model/download")
    token = "hf_abcdefghijklmnopqrstuvwxyz0123456789"
    req = ModelDownloadRequest(repo_id="org/model", include="*.gguf", hf_token=token)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace()))

    out = asyncio.run(endpoint(request, req))

    assert out["ok"] is True
    assert out["cmd"] == "hf download org/model --include '*.gguf'"
    assert token not in json.dumps(out)
    assert len(launched) == 1
    assert "tmux new-session -d -s " in launched[0]
    assert "tmux pipe-pane -o -t " in launched[0]
    assert str(log_dir / f"{out['session_id']}.log") in launched[0]
