"""The projector has to reach llama-server, not just the catalog.

Without ``--mmproj`` on the command line a vision model loads happily and then
rejects every image with HTTP 500 "image input is not supported - hint: if this
is unexpected, you may need to provide the mmproj".
"""
import os

import pytest

from services.localmodels.scanner import LocalModel
from services.localmodels.server_manager import LocalModelServer


def _model(tmp_path, *, mmproj=None, kind="chat"):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"\0" * 16)
    return LocalModel(
        id="lm_test", name="Test-Model", path=str(path), quant="Q4_K_M",
        kind=kind, size_bytes=16, directory=str(tmp_path), arch="qwen3vl",
        mmproj=mmproj,
    )


@pytest.fixture
def captured(monkeypatch):
    """Run ensure_running far enough to capture the argv it would exec."""
    seen = {}

    class _FakeProc:
        def __init__(self, *a, **k):
            pass

        def poll(self):
            return None

    def fake_popen(cmd, *a, **k):
        seen["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr("services.localmodels.server_manager.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        LocalModelServer, "_wait_health", lambda self, *a, **k: None, raising=False)
    monkeypatch.setattr(
        LocalModelServer, "find_binary", lambda self, arch="": "/usr/bin/true", raising=False)
    return seen


def _serve(tmp_path, captured, **kwargs):
    model = _model(tmp_path, **kwargs)
    server = LocalModelServer(lambda: [str(tmp_path)])
    server.set_catalog([model])
    server.ensure_running(model.id)
    return captured["cmd"]


def test_projector_is_passed_to_llama_server(tmp_path, captured):
    projector = tmp_path / "mmproj-F16.gguf"
    projector.write_bytes(b"\0" * 16)

    cmd = _serve(tmp_path, captured, mmproj=str(projector))

    assert "--mmproj" in cmd, f"vision would be broken; argv was {cmd}"
    assert cmd[cmd.index("--mmproj") + 1] == str(projector)


def test_no_projector_flag_when_the_model_has_none(tmp_path, captured):
    cmd = _serve(tmp_path, captured, mmproj=None)
    assert "--mmproj" not in cmd


def test_projector_that_vanished_is_skipped(tmp_path, captured):
    """The catalog can outlive the file; passing a missing path just fails the
    launch, which is worse than serving the model without vision."""
    cmd = _serve(tmp_path, captured, mmproj=str(tmp_path / "gone.gguf"))
    assert "--mmproj" not in cmd


def test_embedding_model_still_gets_its_own_flag(tmp_path, captured):
    cmd = _serve(tmp_path, captured, kind="embedding")
    assert "--embedding" in cmd
