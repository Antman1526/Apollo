"""Tests for the PyInstaller boot shim's release-only environment setup."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_boot_module():
    source = Path(__file__).parents[1] / "packaging" / "apollo_boot.py"
    spec = importlib.util.spec_from_file_location("apollo_boot_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_bundled_playwright_uses_packaged_browser(tmp_path, monkeypatch):
    boot = _load_boot_module()
    bundled_browser = tmp_path / "playwright-browsers" / "chromium_headless_shell-1223"
    bundled_browser.mkdir(parents=True)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    boot._configure_bundled_playwright(tmp_path)

    assert boot.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "playwright-browsers")


def test_configure_bundled_playwright_preserves_operator_override(tmp_path, monkeypatch):
    boot = _load_boot_module()
    override = str(tmp_path / "operator-browsers")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", override)
    (tmp_path / "playwright-browsers").mkdir()

    boot._configure_bundled_playwright(tmp_path)

    assert boot.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == override


def test_configure_runtime_paths_uses_writable_home(tmp_path, monkeypatch):
    boot = _load_boot_module()
    monkeypatch.delenv("APOLLO_DATA_DIR", raising=False)

    boot._configure_runtime_paths(tmp_path)

    assert boot.os.environ["APOLLO_DATA_DIR"] == str(tmp_path / "data")


def test_configure_runtime_paths_preserves_operator_override(tmp_path, monkeypatch):
    boot = _load_boot_module()
    monkeypatch.setenv("APOLLO_DATA_DIR", "/tmp/operator-data")

    boot._configure_runtime_paths(tmp_path)

    assert boot.os.environ["APOLLO_DATA_DIR"] == "/tmp/operator-data"


def test_seed_home_never_copies_checkout_auth_state(tmp_path):
    boot = _load_boot_module()
    bundle = tmp_path / "bundle"
    (bundle / "static").mkdir(parents=True)
    (bundle / "data").mkdir()
    (bundle / "data" / "auth.json").write_text('{"users":{"developer":{}}}')
    (bundle / "data" / "settings.json").write_text('{}')
    home = tmp_path / "home"

    boot._seed_home(bundle, home)

    assert not (home / "data" / "auth.json").exists()
    assert (home / "data" / "settings.json").exists()
