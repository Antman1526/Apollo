"""SearxngConfig: paths, url, installed detection."""
import os

from services.searxng.config import SearxngConfig, load_config


def test_paths_derive_from_home(tmp_path):
    cfg = SearxngConfig(enabled=True, port=9001, home=str(tmp_path))
    assert cfg.url == "http://127.0.0.1:9001"
    assert cfg.settings_path == os.path.join(str(tmp_path), "settings.yml")
    assert cfg.venv_python.startswith(os.path.join(str(tmp_path), "venv"))


def test_installed_requires_python_and_settings(tmp_path):
    cfg = SearxngConfig(enabled=True, port=9001, home=str(tmp_path))
    assert cfg.installed is False
    os.makedirs(os.path.dirname(cfg.venv_python), exist_ok=True)
    open(cfg.venv_python, "w").close()
    assert cfg.installed is False  # settings.yml still missing
    open(cfg.settings_path, "w").close()
    assert cfg.installed is True


def test_load_config_reads_settings(monkeypatch):
    monkeypatch.setattr(
        "src.settings.load_settings",
        lambda: {"searxng_managed": False, "searxng_port": "9100"},
    )
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.port == 9100


def test_load_config_bad_port_falls_back(monkeypatch):
    monkeypatch.setattr(
        "src.settings.load_settings",
        lambda: {"searxng_managed": True, "searxng_port": "not-a-number"},
    )
    assert load_config().port == 8893
