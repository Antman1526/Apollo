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
