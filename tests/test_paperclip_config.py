import importlib

import pytest


def _fresh(monkeypatch, env):
    for k in list(env):
        monkeypatch.setenv(k, env[k])
    import services.paperclip.config as cfg
    importlib.reload(cfg)
    return cfg


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PAPERCLIP_ENABLED", raising=False)
    cfg = _fresh(monkeypatch, {})
    c = cfg.load_config()
    assert c.enabled is False


def test_docker_defaults(monkeypatch):
    cfg = _fresh(monkeypatch, {"PAPERCLIP_ENABLED": "true"})
    c = cfg.load_config()
    assert c.enabled is True
    assert c.mode == "docker"
    assert c.url == "http://paperclip:3100"
    assert c.port == 3100


def test_browser_url_defaults_to_localhost(monkeypatch):
    cfg = _fresh(monkeypatch, {"PAPERCLIP_ENABLED": "true"})
    c = cfg.load_config()
    # The iframe points the browser directly at Paperclip's own origin.
    assert c.browser_url == "http://localhost:3100"


def test_external_mode_points_url_at_localhost(monkeypatch):
    cfg = _fresh(monkeypatch, {"PAPERCLIP_ENABLED": "true", "PAPERCLIP_MODE": "external"})
    c = cfg.load_config()
    assert c.mode == "external"
    assert c.url == "http://localhost:3100"
    assert c.browser_url == "http://localhost:3100"


def test_browser_url_override(monkeypatch):
    cfg = _fresh(monkeypatch, {
        "PAPERCLIP_ENABLED": "true",
        "PAPERCLIP_BROWSER_URL": "http://box.local:3100",
    })
    c = cfg.load_config()
    assert c.browser_url == "http://box.local:3100"


def test_model_endpoint_ollama_default(monkeypatch):
    cfg = _fresh(monkeypatch, {"PAPERCLIP_ENABLED": "true"})
    c = cfg.load_config()
    assert c.model_endpoint == "ollama"
    # In Docker, Ollama on the host is reached via host.docker.internal.
    assert c.model_base_url == "http://host.docker.internal:11434/v1"


def test_model_endpoint_custom_overrides(monkeypatch):
    cfg = _fresh(monkeypatch, {
        "PAPERCLIP_ENABLED": "true",
        "PAPERCLIP_MODEL_ENDPOINT": "custom",
        "PAPERCLIP_MODEL_BASE_URL": "http://example:9000/v1",
        "PAPERCLIP_MODEL_NAME": "openai/my-model",
    })
    c = cfg.load_config()
    assert c.model_endpoint == "custom"
    assert c.model_base_url == "http://example:9000/v1"
    assert c.model_name == "openai/my-model"


def test_auth_secret_generated_and_persisted(tmp_path, monkeypatch):
    secret_file = tmp_path / "paperclip_secret"
    cfg = _fresh(monkeypatch, {
        "PAPERCLIP_ENABLED": "true",
        "PAPERCLIP_SECRET_FILE": str(secret_file),
    })
    monkeypatch.delenv("PAPERCLIP_AUTH_SECRET", raising=False)
    s1 = cfg.resolve_auth_secret()
    s2 = cfg.resolve_auth_secret()
    assert s1 and len(s1) >= 32
    assert s1 == s2  # persisted, stable across calls
    assert secret_file.read_text().strip() == s1
