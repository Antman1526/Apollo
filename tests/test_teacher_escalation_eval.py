import pytest

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
