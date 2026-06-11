"""Defaults for the managed SearXNG sidecar and web-access mode."""
from src.settings import DEFAULT_SETTINGS


def test_searxng_sidecar_defaults():
    assert DEFAULT_SETTINGS["searxng_managed"] is True
    assert DEFAULT_SETTINGS["searxng_port"] == 8893


def test_web_access_mode_default_is_manual():
    # "manual" preserves legacy toggle behavior until the user opts in.
    assert DEFAULT_SETTINGS["web_access_mode"] == "manual"
