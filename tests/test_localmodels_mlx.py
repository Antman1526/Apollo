"""MLX model folders: discovery, classification, and the mlx_lm.server launch."""
import json
import os
from unittest.mock import patch

import pytest

from services.localmodels import mlx, server_manager
from services.localmodels.scanner import LocalModel, scan_dirs
from services.localmodels.server_manager import LocalModelServer, _Proc


def _mlx_dir(root, name, model_type="qwen3_5", quantized=True, template=None):
    d = root / name
    d.mkdir(parents=True)
    cfg = {"model_type": model_type}
    if quantized:
        cfg["quantization"] = {"bits": 4, "group_size": 64}
    (d / "config.json").write_text(json.dumps(cfg))
    (d / "model.safetensors").write_bytes(b"\0" * 10)
    if template is not None:
        (d / "tokenizer_config.json").write_text(json.dumps({"chat_template": template}))
    return d


@pytest.fixture
def supported():
    with patch.object(mlx, "supported_types", return_value=frozenset({"qwen3_5", "llama"})):
        yield


def test_scan_lists_supported_mlx_folder_as_chat(tmp_path, supported):
    d = _mlx_dir(tmp_path, "Qwen3.6-27B-MLX-6bit")
    (d / "sub").mkdir()
    (d / "sub" / "config.json").write_text("{}")
    (d / "sub" / "x.safetensors").write_bytes(b"")
    with patch.object(mlx, "tool_parsers", return_value={os.path.realpath(str(d)): ("qwen3_coder", False)}):
        [m] = scan_dirs([str(tmp_path)])
    assert (m.backend, m.kind, m.arch) == ("mlx", "chat", "qwen3_5")
    assert m.path == os.path.realpath(str(d))
    assert m.name == "Qwen3.6-27B-MLX-6bit"
    assert m.size_bytes == 10
    assert m.tools is True


def test_scan_marks_unknown_model_type_unsupported(tmp_path, supported):
    _mlx_dir(tmp_path, "Wan2.2-I2V-mlx-q8", model_type="i2v")
    [m] = scan_dirs([str(tmp_path)])
    assert m.kind == "unsupported"


def test_plain_hf_checkpoint_is_not_mlx(tmp_path, supported):
    _mlx_dir(tmp_path, "Llama-3-8B-Instruct", model_type="llama", quantized=False)
    assert scan_dirs([str(tmp_path)]) == []


def test_tools_follow_mlx_lm_parser_not_template_text(tmp_path, supported):
    a = _mlx_dir(tmp_path, "Qwen-MLX")
    b = _mlx_dir(tmp_path, "LFM2.5-MLX")
    with patch.object(mlx, "tool_parsers",
                      return_value={os.path.realpath(str(a)): ("qwen3_coder", False),
                                    os.path.realpath(str(b)): ("pythonic", True)}):
        models = {m.name: m for m in scan_dirs([str(tmp_path)])}
    assert models["Qwen-MLX"].tools is True and models["Qwen-MLX"].mlx_parser is None
    assert models["LFM2.5-MLX"].tools is True and models["LFM2.5-MLX"].mlx_parser == "pythonic"


def test_no_parser_means_prompt_mode_tools(tmp_path, supported):
    a = _mlx_dir(tmp_path, "Devstral-MLX")
    with patch.object(mlx, "tool_parsers", return_value={os.path.realpath(str(a)): (False, False)}):
        [m] = scan_dirs([str(tmp_path)])
    assert m.tools is False and m.mlx_parser is None


def test_undetermined_parser_stays_unknown(tmp_path, supported):
    d = _mlx_dir(tmp_path, "Odd-MLX")
    with patch.object(mlx, "tool_parsers", return_value={os.path.realpath(str(d)): None}):
        [m] = scan_dirs([str(tmp_path)])
    assert m.tools is None


def test_tool_parsers_without_runtime_are_unknown():
    with patch.object(mlx, "find_mlx_runtime", return_value=None):
        assert mlx.tool_parsers(["/m/a"]) == {"/m/a": None}


def test_runtime_prefers_configured_python(tmp_path):
    py = tmp_path / "python"
    py.write_text("")
    with patch.object(mlx, "get_mlx_python_path", return_value=str(py)):
        assert mlx.find_mlx_runtime() == [str(py), "-m", "mlx_lm.server"]
    with patch.object(mlx, "get_mlx_python_path", return_value="/gone/python"):
        assert mlx.find_mlx_runtime() is None


def _mlx_model(path):
    return LocalModel(id="lm_x", name="Qwen-MLX", path=str(path), quant="", kind="chat",
                      size_bytes=1, directory="/m", arch="qwen3_5", backend="mlx", tools=True)


def test_mlx_command_serves_catalog_name_via_symlink(tmp_path):
    model_dir = tmp_path / "real-folder"
    model_dir.mkdir()
    srv = LocalModelServer(dirs_provider=lambda: [])
    with patch.object(server_manager, "find_mlx_runtime", return_value=["/py", "-m", "mlx_lm.server"]), \
         patch("tempfile.gettempdir", return_value=str(tmp_path / "tmp")):
        cmd, cwd = srv._mlx_command(_mlx_model(model_dir), 9123)
        # Idempotent on relaunch.
        srv._mlx_command(_mlx_model(model_dir), 9124)
    assert cmd == ["/py", "-m", "mlx_lm.server", "--model", "Qwen-MLX",
                   "--host", "127.0.0.1", "--port", "9123"]
    assert os.path.realpath(os.path.join(cwd, "Qwen-MLX")) == str(model_dir)


def test_mlx_command_explains_missing_runtime(tmp_path):
    srv = LocalModelServer(dirs_provider=lambda: [])
    with patch.object(server_manager, "find_mlx_runtime", return_value=None), \
         pytest.raises(RuntimeError, match="mlx_lm not found"):
        srv._mlx_command(_mlx_model(tmp_path), 9123)


def test_mlx_tool_caps_come_from_template_not_props(tmp_path):
    class _Alive:
        def poll(self):
            return None

    m = _mlx_model(tmp_path)
    srv = LocalModelServer(dirs_provider=lambda: [])
    srv.set_catalog([m])
    srv._launch = lambda mm: _Proc(mm.id, mm.name, mm.kind, 9001, _Alive(),  # type: ignore[assignment]
                                   "http://127.0.0.1:9001")
    with patch.object(server_manager, "_probe_tool_calls") as probe:
        srv.ensure_running("Qwen-MLX")
        srv.ensure_running("Qwen-MLX")
    probe.assert_not_called()
    assert srv.supports_tool_calls("Qwen-MLX") is True


def test_overlay_injects_parser_without_touching_model(tmp_path):
    model_dir = tmp_path / "lfm"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}")
    (model_dir / "model.safetensors").write_bytes(b"x")
    (model_dir / "tokenizer_config.json").write_text(json.dumps({"chat_template": "t"}))
    m = LocalModel(id="lm_l", name="LFM", path=str(model_dir), quant="", kind="chat",
                   size_bytes=1, directory="/m", arch="lfm2", backend="mlx", tools=True,
                   mlx_parser="pythonic")
    srv = LocalModelServer(dirs_provider=lambda: [])
    with patch.object(server_manager, "find_mlx_runtime", return_value=["/py", "-m", "mlx_lm.server"]), \
         patch("tempfile.gettempdir", return_value=str(tmp_path / "tmp")):
        _, cwd = srv._mlx_command(m, 9001)
        _, cwd = srv._mlx_command(m, 9002)  # rebuild is idempotent
    overlay = os.path.join(cwd, "LFM")
    assert os.path.isdir(overlay) and not os.path.islink(overlay)
    assert os.path.islink(os.path.join(overlay, "model.safetensors"))
    cfg = json.loads(open(os.path.join(overlay, "tokenizer_config.json")).read())
    assert cfg == {"chat_template": "t", "tool_parser_type": "pythonic"}
    assert json.loads((model_dir / "tokenizer_config.json").read_text()) == {"chat_template": "t"}
    # Switching back to a plain model replaces the overlay with a symlink.
    m.mlx_parser = None
    with patch.object(server_manager, "find_mlx_runtime", return_value=["/py", "-m", "mlx_lm.server"]), \
         patch("tempfile.gettempdir", return_value=str(tmp_path / "tmp")):
        srv._mlx_command(m, 9003)
    assert os.path.islink(overlay)



def test_parser_cache_prunes_old_versions_and_missing_folders(tmp_path):
    keep = tmp_path / "still-here"
    keep.mkdir()
    mlx._PARSER_CACHE.clear()
    mlx._PARSER_CACHE.update({
        f"v1|/py|{keep}|1.0": ["qwen3_coder", False],
        f"v1|/py|{tmp_path / 'gone'}|1.0": ["qwen3_coder", False],
        f"v0|/py|{keep}|1.0": ["json_tools", False],
    })
    mlx._prune_parser_cache("v1")
    assert list(mlx._PARSER_CACHE) == [f"v1|/py|{keep}|1.0"]
    mlx._PARSER_CACHE.clear()


def _fake_venv(root, name=".mlx_lm_venv", with_mlx=True):
    bin_dir = root / name / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("")
    if with_mlx:
        pkg = root / name / "lib" / "python3.14" / "site-packages" / "mlx_lm"
        pkg.mkdir(parents=True)
        (pkg / "server.py").write_text("")
    return str(bin_dir / "python")


def _no_configured_or_path_runtime(monkeypatch):
    monkeypatch.setattr(mlx, "get_mlx_python_path", lambda: "")
    monkeypatch.setattr(mlx.shutil, "which", lambda name: None)
    monkeypatch.setattr(mlx, "_SERVER_CANDIDATES", ())


def test_runtime_found_in_a_venv_inside_a_model_dir(tmp_path, monkeypatch):
    _no_configured_or_path_runtime(monkeypatch)
    python = _fake_venv(tmp_path)
    monkeypatch.setattr(mlx, "get_local_model_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(mlx.os.path, "expanduser",
                        lambda p: str(tmp_path / "home") if p == "~" else p)
    assert mlx.find_mlx_runtime() == [python, "-m", "mlx_lm.server"]


def test_venv_without_mlx_lm_is_ignored(tmp_path, monkeypatch):
    _no_configured_or_path_runtime(monkeypatch)
    _fake_venv(tmp_path, name=".venv", with_mlx=False)
    monkeypatch.setattr(mlx, "get_local_model_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(mlx.os.path, "expanduser",
                        lambda p: str(tmp_path / "home") if p == "~" else p)
    assert mlx.find_mlx_runtime() is None


def test_standard_install_location_found_without_path(tmp_path, monkeypatch):
    _no_configured_or_path_runtime(monkeypatch)
    server = tmp_path / "mlx_lm.server"
    server.write_text("#!/bin/sh\n")
    server.chmod(0o755)
    monkeypatch.setattr(mlx, "_SERVER_CANDIDATES", (str(server),))
    assert mlx.find_mlx_runtime() == [str(server)]


def test_configured_path_still_wins_over_detection(tmp_path, monkeypatch):
    configured = tmp_path / "configured-python"
    configured.write_text("")
    monkeypatch.setattr(mlx, "get_mlx_python_path", lambda: str(configured))
    monkeypatch.setattr(mlx, "get_local_model_dirs", lambda: [str(tmp_path)])
    _fake_venv(tmp_path)
    assert mlx.find_mlx_runtime() == [str(configured), "-m", "mlx_lm.server"]
