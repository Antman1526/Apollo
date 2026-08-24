import os
import importlib
from unittest.mock import patch


def test_env_seed_parses_comma_and_pathsep(monkeypatch):
    from services.localmodels import config
    monkeypatch.setenv("APOLLO_MODELS_DIRS", "/a/models,/b/models")
    with patch.object(config, "load_settings", return_value={"local_model_dirs": []}):
        dirs = config.get_local_model_dirs()
    assert dirs == ["/a/models", "/b/models"]


def test_settings_override_env(monkeypatch):
    from services.localmodels import config
    monkeypatch.setenv("APOLLO_MODELS_DIRS", "/ignored")
    with patch.object(config, "load_settings", return_value={"local_model_dirs": ["/chosen"]}):
        dirs = config.get_local_model_dirs()
    assert dirs == ["/chosen"]


def test_default_when_unset(monkeypatch):
    from services.localmodels import config
    monkeypatch.delenv("APOLLO_MODELS_DIRS", raising=False)
    with patch.object(config, "load_settings", return_value={"local_model_dirs": []}):
        dirs = config.get_local_model_dirs()
    assert dirs == config.DEFAULT_DIRS


def test_set_cleans_and_persists():
    from services.localmodels import config
    saved = {}
    settings = {"local_model_dirs": []}
    with patch.object(config, "load_settings", return_value=settings), \
         patch.object(config, "save_settings", side_effect=lambda s: saved.update(s)):
        result = config.set_local_model_dirs(["  /a  ", "", "  ", "/b"])
    assert result == ["/a", "/b"]
    assert saved["local_model_dirs"] == ["/a", "/b"]


def test_llama_server_settings_beat_env(monkeypatch):
    from services.localmodels import config
    monkeypatch.setenv("APOLLO_LLAMA_SERVER", "/env/llama-server")
    with patch.object(config, "load_settings", return_value={"llama_server_path": "/chosen/llama-server"}):
        assert config.get_llama_server_path() == "/chosen/llama-server"


def test_llama_server_env_fallback(monkeypatch):
    from services.localmodels import config
    monkeypatch.setenv("APOLLO_LLAMA_SERVER", "/env/llama-server")
    with patch.object(config, "load_settings", return_value={}):
        assert config.get_llama_server_path() == "/env/llama-server"


def test_llama_server_default_empty(monkeypatch):
    from services.localmodels import config
    monkeypatch.delenv("APOLLO_LLAMA_SERVER", raising=False)
    with patch.object(config, "load_settings", return_value={}):
        assert config.get_llama_server_path() == ""


def test_set_llama_server_path_drops_relative_and_persists():
    from services.localmodels import config
    saved = {}
    with patch.object(config, "load_settings", return_value={}), \
         patch.object(config, "save_settings", side_effect=lambda s: saved.update(s)):
        assert config.set_llama_server_path("  /abs/llama-server  ") == "/abs/llama-server"
        assert saved["llama_server_path"] == "/abs/llama-server"
        assert config.set_llama_server_path("relative/llama-server") == ""
        assert saved["llama_server_path"] == ""


def test_windows_default_dirs(monkeypatch):
    import os as _os
    from services.localmodels import config
    monkeypatch.setattr(_os, "name", "nt")
    dirs = config._default_dirs()
    assert len(dirs) == 3
    assert not any(d.startswith("/Volumes") for d in dirs)
    assert any("AI_Models" in d for d in dirs)
    assert any(".lmstudio" in d for d in dirs)
