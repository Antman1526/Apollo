"""Tests for the readiness / integrity self-check (src/readiness.py)."""

from src import readiness
from src.readiness import check_readiness


def test_readiness_reports_core_subsystems():
    result = check_readiness()

    assert {"ready", "version", "checks", "timestamp"}.issubset(result.keys())
    checks = result["checks"]
    for name in ("database", "data_dir", "local_first"):
        assert name in checks, f"missing check: {name}"

    # In the dev/test environment the local SQLite DB and data dir are present,
    # so the critical checks must pass and overall readiness must be True.
    assert checks["database"]["ok"] is True, checks["database"]
    assert checks["data_dir"]["ok"] is True, checks["data_dir"]
    assert result["ready"] is True, result


def test_local_first_check_is_informational_never_fatal():
    result = check_readiness()
    lf = result["checks"]["local_first"]
    # local_first reports whether storage stays on-host but must never gate
    # readiness — a remote database is a valid deployment.
    assert lf["ok"] is True
    assert "local" in lf


def test_data_directory_failure_is_redacted_and_observable(monkeypatch):
    events = []
    monkeypatch.setattr(
        readiness.os,
        "makedirs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("private path detail")),
    )
    monkeypatch.setattr(
        readiness,
        "report_exception",
        lambda _logger, event, _error, **kwargs: events.append((event, kwargs)),
    )

    result = check_readiness()

    assert result["checks"]["data_dir"] == {
        "ok": False,
        "error": "Data directory check failed",
    }
    assert "private path detail" not in str(result)
    assert events == [("readiness_data_dir_check_failed", {"outcome": "critical"})]
