import logging

import pytest

from src import teacher_escalation
from src.teacher_escalation import evaluate_turn


@pytest.mark.asyncio
async def test_evaluate_turn_short_circuits_regex_failures():
    called = False

    async def self_check(**kwargs):
        nonlocal called
        called = True
        return {"status": "ok"}

    status, reason = await evaluate_turn(
        [{"tool": "x", "error": "bad"}],
        "done",
        self_check=self_check,
    )

    assert status == "failure"
    assert "tool returned error" in reason
    assert called is False


@pytest.mark.asyncio
async def test_evaluate_turn_uses_async_self_check_for_ambiguous_success():
    async def self_check(**kwargs):
        assert kwargs["agent_reply"] == "I completed the request."
        return {"status": "needs_help", "reason": "no verification evidence"}

    status, reason = await evaluate_turn([], "I completed the request.", self_check=self_check)

    assert status == "failure"
    assert reason == "no verification evidence"


def test_maybe_escalate_reports_unavailable_settings(monkeypatch, caplog):
    import src.settings as settings

    caplog.set_level(logging.DEBUG, logger=teacher_escalation.__name__)

    def unavailable(*_args, **_kwargs):
        raise OSError("settings unavailable")

    monkeypatch.setattr(settings, "get_setting", unavailable)

    assert teacher_escalation.maybe_escalate(
        student_endpoint_url="http://127.0.0.1:8080",
        mode="agent",
        user_request="test",
        tool_results=[],
        agent_reply="done",
    ) is None
    assert "teacher_escalation_settings_load_failed" in caplog.text
