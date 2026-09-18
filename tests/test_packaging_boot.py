"""Tests for the PyInstaller boot shim's release-only environment setup."""

from __future__ import annotations

import importlib.util
import ast
import os
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


def test_configure_runtime_paths_preserves_legacy_data_override(tmp_path, monkeypatch):
    boot = _load_boot_module()
    monkeypatch.delenv("APOLLO_DATA_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", "/tmp/legacy-data")

    boot._configure_runtime_paths(tmp_path)

    assert boot.os.environ.get("APOLLO_DATA_DIR") is None
    assert boot.os.environ["DATA_DIR"] == "/tmp/legacy-data"


def test_apollo_home_uses_platform_data_root_and_explicit_home_override(tmp_path, monkeypatch):
    boot = _load_boot_module()
    monkeypatch.delenv("APOLLO_HOME", raising=False)
    monkeypatch.setattr(
        boot,
        "platform_data_root",
        lambda **kwargs: tmp_path / "platform-home",
    )

    assert boot._apollo_home() == tmp_path / "platform-home"

    monkeypatch.setenv("APOLLO_HOME", str(tmp_path / "custom-home"))
    assert boot._apollo_home() == tmp_path / "custom-home"


def test_data_root_preserves_explicit_overrides(tmp_path, monkeypatch):
    boot = _load_boot_module()
    home = tmp_path / "home"
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data-dir"))
    monkeypatch.delenv("APOLLO_DATA_DIR", raising=False)
    assert boot._data_root(home) == (tmp_path / "data-dir").resolve()

    monkeypatch.setenv("APOLLO_DATA_DIR", str(tmp_path / "apollo-data"))
    assert boot._data_root(home) == (tmp_path / "apollo-data").resolve()


def test_seed_home_does_not_copy_or_replace_static_assets(tmp_path):
    boot = _load_boot_module()
    bundle = tmp_path / "bundle"
    (bundle / "static").mkdir(parents=True)
    (bundle / "static" / "app.js").write_text("current", encoding="utf-8")
    home = tmp_path / "home"
    (home / "static").mkdir(parents=True)
    (home / "static" / "app.js").write_text("stale", encoding="utf-8")

    boot._seed_home(bundle, home)

    assert (home / "static" / "app.js").read_text(encoding="utf-8") == "stale"
    assert not (home / "static").is_symlink()
    assert (bundle / "static" / "app.js").read_text(encoding="utf-8") == "current"


def test_bundled_script_argument_resolves_relative_child_from_bundle(tmp_path, monkeypatch):
    boot = _load_boot_module()
    (tmp_path / "mcp_servers").mkdir()
    script = tmp_path / "mcp_servers" / "memory_server.py"
    script.write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(boot.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(boot.sys, "argv", ["apollo", "mcp_servers/memory_server.py"])

    assert boot._bundled_script_argument() == script.resolve()


def test_auth_free_mode_is_limited_to_loopback_hosts():
    boot = _load_boot_module()
    assert boot._is_loopback("127.0.0.1")
    assert boot._is_loopback("localhost")
    assert boot._is_loopback("::1")
    assert not boot._is_loopback("0.0.0.0")
    assert not boot._is_loopback("192.168.1.10")


def test_patch_constants_uses_bundle_static_and_resolved_data(tmp_path, monkeypatch):
    boot = _load_boot_module()
    bundle = tmp_path / "bundle"
    data = tmp_path / "custom-data"
    bundle.mkdir()
    data.mkdir()
    import core.constants as core_constants
    import src.constants as src_constants

    original = {
        name: getattr(src_constants, name)
        for name in (
            "BASE_DIR", "STATIC_DIR", "DATA_DIR", "SESSIONS_FILE",
            "MEMORY_FILE", "MEMORY_DOC", "PERSONAL_DIR", "RUNBOOK_DIR",
            "UPLOAD_DIR", "FEATURES_FILE", "SETTINGS_FILE",
        )
    }
    boot._patch_constants(tmp_path / "home", bundle=bundle, data_root=data)

    assert src_constants.BASE_DIR == str(tmp_path / "home") + os.sep
    assert src_constants.STATIC_DIR == str(bundle / "static")
    assert src_constants.DATA_DIR == str(data)
    assert core_constants.STATIC_DIR == str(bundle / "static")
    for name, value in original.items():
        setattr(src_constants, name, value)
        setattr(core_constants, name, value)


def test_boot_calls_freeze_support_before_runtime_setup():
    source_path = Path(__file__).parents[1] / "packaging" / "apollo_boot.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    first_call = main.body[0].value
    assert isinstance(first_call, ast.Call)
    assert isinstance(first_call.func, ast.Attribute)
    assert first_call.func.attr == "freeze_support"


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
