"""Tests for the PyInstaller boot shim's release-only environment setup."""

from __future__ import annotations

import importlib.util
import ast
import io
import json
import os
import subprocess
from pathlib import Path

import pytest


def _mac_launcher_prefix():
    source = (Path(__file__).parents[1] / "build-macos-bundle.sh").read_text(
        encoding="utf-8"
    )
    heredoc = source.split(
        'cat > "$APP/Contents/MacOS/$APP_NAME.tmpl" <<\'LAUNCHER\'\n', 1
    )[1].split("\nLAUNCHER\n", 1)[0]
    return heredoc.split("\nnotify() {", 1)[0]


def _run_mac_launcher_prefix(tmp_path, env_overrides):
    home = tmp_path / "User Home"
    cwd = tmp_path / "Working Directory"
    launcher_dir = tmp_path / "Apollo Preview.app" / "Contents" / "MacOS"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    (launcher_dir.parent / "Resources" / "apollo").mkdir(parents=True, exist_ok=True)
    cwd.mkdir(exist_ok=True)

    launcher = launcher_dir / "Apollo"
    probe = """
printf 'state=%s\\ndata=%s\\ndatabase=%s\\n' \\
  "${APOLLO_STATE_DIR}" "${APOLLO_DATA_DIR_VALUE}" "${DATABASE_URL}"
"""
    launcher.write_text(
        _mac_launcher_prefix().replace("__PORT__", "7860")
        + probe,
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    env = os.environ.copy()
    for key in ("APOLLO_HOME", "APOLLO_DATA_DIR", "DATA_DIR", "DATABASE_URL"):
        env.pop(key, None)
    env.update({"HOME": str(home), **env_overrides})
    result = subprocess.run(
        ["/bin/bash", str(launcher)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


@pytest.mark.skipif(os.name == "nt", reason="macOS shell launcher regression")
def test_macos_launcher_prefix_resolves_isolated_profile_paths(tmp_path):
    home = tmp_path / "User Home"
    cwd = tmp_path / "Working Directory"
    default_state = home / "Library" / "Application Support" / "Apollo"
    cases = (
        ({}, default_state, default_state / "data"),
        (
            {"APOLLO_HOME": "~/Apollo Preview"},
            home / "Apollo Preview",
            home / "Apollo Preview" / "data",
        ),
        (
            {"APOLLO_HOME": "Apollo Preview Profile"},
            cwd / "Apollo Preview Profile",
            cwd / "Apollo Preview Profile" / "data",
        ),
        ({"APOLLO_DATA_DIR": "relative data"}, default_state, cwd / "relative data"),
        ({"DATA_DIR": "legacy data"}, default_state, cwd / "legacy data"),
        (
            {"APOLLO_DATA_DIR": "apollo data", "DATA_DIR": "legacy data"},
            default_state,
            cwd / "apollo data",
        ),
    )

    for env_overrides, expected_state, expected_data in cases:
        values = _run_mac_launcher_prefix(tmp_path, env_overrides)
        assert values == {
            "state": str(expected_state),
            "data": str(expected_data),
            "database": f"sqlite:///{expected_data}/app.db",
        }


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
    monkeypatch.delenv("DATA_DIR", raising=False)

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


def test_relative_apollo_home_resolves_before_boot_chdir(tmp_path, monkeypatch):
    boot = _load_boot_module()
    cwd = tmp_path / "Original CWD"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("APOLLO_HOME", "Apollo Preview Profile")
    monkeypatch.delenv("APOLLO_DATA_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    home = boot._apollo_home()
    data_root = boot._data_root(home)
    home.mkdir()
    monkeypatch.chdir(home)

    assert home == (cwd / "Apollo Preview Profile").resolve()
    assert data_root == home / "data"
    assert data_root / "app.db" == home / "data" / "app.db"


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


def test_child_invocation_accepts_isolated_code_and_shipped_worker(tmp_path, monkeypatch):
    boot = _load_boot_module()
    worker = tmp_path / "scripts" / "apollo_kernel_worker.py"
    worker.parent.mkdir()
    worker.write_text("", encoding="utf-8")
    monkeypatch.setattr(boot, "_bundle_root", lambda: tmp_path)
    monkeypatch.setattr(boot.sys, "_MEIPASS", str(tmp_path), raising=False)

    monkeypatch.setattr(boot.sys, "argv", ["apollo", "-I", "-c", "print('ok')"])
    assert boot._child_invocation() == ("code", "print('ok')", None)

    monkeypatch.setattr(
        boot.sys,
        "argv",
        ["apollo", "-I", "scripts/apollo_kernel_worker.py"],
    )
    assert boot._child_invocation() == ("worker", worker.resolve(), None)

    mcp = tmp_path / "mcp_servers" / "memory_server.py"
    mcp.parent.mkdir()
    mcp.write_text("", encoding="utf-8")
    monkeypatch.setattr(boot.sys, "argv", ["apollo", "mcp_servers/memory_server.py", "--stdio"])
    assert boot._child_invocation() == ("script", mcp.resolve(), ("--stdio",))


def test_child_code_preserves_error_exit_status(monkeypatch, capsys):
    boot = _load_boot_module()
    monkeypatch.setattr(boot.sys, "frozen", True, raising=False)
    monkeypatch.setattr(boot.sys, "_MEIPASS", "/tmp/apollo-bundle", raising=False)
    monkeypatch.setattr(boot.sys, "path", list(boot.sys.path))

    with pytest.raises(SystemExit) as error:
        boot._run_child(("code", "print('child-ok'); raise SystemExit(7)", None))

    assert error.value.code == 7
    assert capsys.readouterr().out.strip() == "child-ok"


def test_isolated_child_keeps_only_bundle_import_paths(tmp_path, monkeypatch):
    boot = _load_boot_module()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    base_library = bundle / "base_library.zip"
    base_library.write_bytes(b"zip")
    native = bundle / "lib-dynload"
    native.mkdir()
    bundled_egg = bundle / "packages" / "apollo.egg"
    bundled_egg.parent.mkdir()
    bundled_egg.write_bytes(b"egg")
    relative_native = bundle / "relative-native"
    relative_native.mkdir()
    pythonpath_bundle = bundle / "pythonpath-injected"
    pythonpath_bundle.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.setattr(boot.sys, "frozen", True, raising=False)
    monkeypatch.setattr(boot.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(
        boot.sys,
        "path",
        [
            str(outside),
            "",
            str(base_library),
            str(native),
            str(bundled_egg),
            "../bundle/relative-native",
            str(bundle),
            str(native),
            str(cwd),
            ".",
            str(pythonpath_bundle),
        ],
    )
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PYTHONPATH", str(pythonpath_bundle))
    monkeypatch.setenv("PYTHONUSERBASE", str(outside / "user-site"))

    boot._configure_isolated_child()

    assert boot.sys.path == [
        str(base_library.resolve()),
        str(native.resolve()),
        str(bundled_egg.resolve()),
        str(relative_native.resolve()),
        str(bundle.resolve()),
    ]
    assert "PYTHONPATH" not in boot.os.environ
    assert "PYTHONUSERBASE" not in boot.os.environ
    assert boot.os.environ["PYTHONNOUSERSITE"] == "1"


def test_worker_child_keeps_json_state_between_requests(tmp_path, monkeypatch, capsys):
    boot = _load_boot_module()
    worker = Path(__file__).parents[1] / "scripts" / "apollo_kernel_worker.py"
    monkeypatch.setattr(boot.sys, "argv", ["apollo", "-I", str(worker)])
    monkeypatch.setattr(boot.sys, "stdin", io.StringIO(
        json.dumps({"code": "value = 41"}) + "\n"
        + json.dumps({"code": "print(value + 1)"}) + "\n"
        + json.dumps({"cmd": "shutdown"}) + "\n"
    ))

    invocation = ("worker", worker, None)
    boot._run_child(invocation)

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["error"] is None
    assert lines[1]["stdout"] == "42\n"


def test_unknown_child_args_are_rejected_without_server_start(monkeypatch):
    boot = _load_boot_module()
    monkeypatch.setattr(boot.sys, "argv", ["apollo", "--not-a-child"])

    with pytest.raises(SystemExit, match="unsupported child arguments"):
        boot._child_invocation()


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
