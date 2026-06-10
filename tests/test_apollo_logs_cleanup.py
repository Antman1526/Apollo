import importlib.machinery
import importlib.util
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "apollo-logs"
    loader = importlib.machinery.SourceFileLoader("apollo_logs_cli_cleanup", str(path))
    spec = importlib.util.spec_from_loader("apollo_logs_cli_cleanup", loader)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_runtime_log_cleanup_candidates_include_old_runtime_logs(tmp_path, monkeypatch):
    cli = _load_cli()
    app_logs = tmp_path / "logs"
    browser_logs = tmp_path / ".playwright-mcp"
    app_logs.mkdir()
    browser_logs.mkdir()
    old_app = app_logs / "old.log"
    fresh_browser = browser_logs / "fresh.log"
    old_app.write_text("old", encoding="utf-8")
    fresh_browser.write_text("fresh", encoding="utf-8")
    old_time = time.time() - 10 * 86400
    os.utime(old_app, (old_time, old_time))

    monkeypatch.setattr(cli, "_APP_LOGS", app_logs)
    monkeypatch.setattr(cli, "_BROWSER_LOGS", browser_logs)
    monkeypatch.setattr(cli, "_LEGACY_SEARCH_LOGS", ())

    old, kept = cli._clean_candidates("runtime", 7)

    assert old == [old_app]
    assert kept == 1


def test_runtime_clean_is_dry_run_without_apply(tmp_path, monkeypatch):
    cli = _load_cli()
    app_logs = tmp_path / "logs"
    app_logs.mkdir()
    old_app = app_logs / "old.log"
    old_app.write_text("old", encoding="utf-8")
    old_time = time.time() - 10 * 86400
    os.utime(old_app, (old_time, old_time))
    emitted = {}

    monkeypatch.setattr(cli, "_APP_LOGS", app_logs)
    monkeypatch.setattr(cli, "_BROWSER_LOGS", tmp_path / "missing")
    monkeypatch.setattr(cli, "_LEGACY_SEARCH_LOGS", ())
    monkeypatch.setattr(cli, "emit", lambda payload, args: emitted.update(payload))

    cli.cmd_clean(type("Args", (), {"scope": "runtime", "days": 7, "apply": False})())

    assert old_app.exists()
    assert emitted["dry_run"] is True
    assert emitted["would_delete"] == [str(old_app)]
