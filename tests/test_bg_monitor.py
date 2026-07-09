"""Regression tests for the background-job follow-up monitor.

The critical property: a PERMANENTLY failing follow-up (deleted session) must
be marked handled, not retried forever. SessionManager.get_session RAISES
KeyError for an unknown id — it never returns None — and treating that raise
as transient once produced a 20 MB log of retry warnings for five dead test
sessions.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.bg_monitor import _run_followup


def _run(coro):
    return asyncio.run(coro)


# _run_followup does `from src.ai_interaction import get_session_manager` at
# call time, so we patch that name. Other tests (e.g. test_auth_regressions)
# plant an empty ModuleType stub for src.ai_interaction into sys.modules with
# no cleanup, so the attribute may be absent when we patch — create=True makes
# the patch set it regardless, scoped to the `with` block (no global leak).
_PATCH_KW = {"create": True}


@pytest.fixture
def rec():
    return {"id": "job1", "session_id": "test-sess", "result": "done"}


def test_deleted_session_keyerror_is_permanent(rec):
    """get_session raising KeyError (session deleted) → handled, no retry."""
    sm = MagicMock()
    sm.get_session.side_effect = KeyError("Session test-sess not found")
    with patch("src.ai_interaction.get_session_manager", return_value=sm, **_PATCH_KW):
        assert _run(_run_followup(rec)) is True


def test_deleted_session_none_is_permanent(rec):
    """get_session returning falsy (belt-and-braces path) → handled too."""
    sm = MagicMock()
    sm.get_session.return_value = None
    with patch("src.ai_interaction.get_session_manager", return_value=sm, **_PATCH_KW):
        assert _run(_run_followup(rec)) is True


def test_no_session_manager_is_transient(rec):
    """Manager not ready yet is genuinely transient → retry next tick."""
    with patch("src.ai_interaction.get_session_manager", return_value=None, **_PATCH_KW):
        assert _run(_run_followup(rec)) is False
