import logging

import pytest

from src.observability import report_exception, sanitize_context


def test_sanitize_context_rejects_secret_and_payload_fields():
    with pytest.raises(ValueError, match="unsafe observability context key"):
        sanitize_context({"api_token": "never-log"})

    with pytest.raises(ValueError, match="unsafe observability context key"):
        sanitize_context({"request_body": "never-log"})


def test_report_exception_returns_safe_record_and_uses_outcome_level(caplog):
    logger = logging.getLogger("tests.observability")

    try:
        raise RuntimeError("provider response contained untrusted content")
    except RuntimeError as error:
        with caplog.at_level(logging.DEBUG, logger=logger.name):
            record = report_exception(
                logger,
                "provider_probe_failed",
                error,
                outcome="degraded",
                context={"task_id": "task-1", "owner": "alice"},
            )

    assert record == {
        "event": "provider_probe_failed",
        "outcome": "degraded",
        "error_type": "RuntimeError",
        "task_id": "task-1",
        "owner": "alice",
    }
    assert any(item.levelno == logging.WARNING and "provider_probe_failed" in item.message for item in caplog.records)
    assert "untrusted content" not in caplog.text


def test_report_exception_rejects_unknown_outcome():
    with pytest.raises(ValueError, match="unknown observability outcome"):
        report_exception(logging.getLogger("test"), "bad", RuntimeError(), outcome="unknown")  # type: ignore[arg-type]
