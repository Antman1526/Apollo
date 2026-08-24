"""Self-writing skills: command-shape normalization and the mining action."""
import asyncio

import pytest

from src.builtin_actions import _normalize_command, action_suggest_skills_from_history


def test_normalize_command():
    assert _normalize_command("git commit -m 'x'") == "git commit"
    assert _normalize_command("FOO=1 BAR=2 npm test -- --watch") == "npm test"
    assert _normalize_command("  ffmpeg   -i in.mp4 out.gif") == "ffmpeg -i"
    assert _normalize_command("") == ""
    assert _normalize_command("ls") == "ls"


class _FakeSkills:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.added = []

    def load_all(self):
        return [{"name": n} for n in self.existing]

    def add_skill(self, **kw):
        self.added.append(kw)
        return kw


def _events(shape_sessions):
    """[(command, session_id), ...] -> ledger-event dicts."""
    return [{"session_id": sid, "tool": "bash", "input": cmd}
            for cmd, sid in shape_sessions]


def _run(monkeypatch, events, existing=None):
    fake = _FakeSkills(existing)
    monkeypatch.setattr("services.activity_ledger.recent_tool_events",
                        lambda days=7, tools=("bash",), only_success=True: events)
    monkeypatch.setattr("services.memory.skills.SkillsManager", lambda d: fake)
    return fake, asyncio.run(action_suggest_skills_from_history(owner=None))


def test_drafts_skill_for_repeated_pattern(monkeypatch):
    events = _events([
        ("git commit -m 'a'", "s1"), ("git commit -m 'b'", "s2"),
        ("git commit --amend", "s3"),
        ("ls -la", "s1"),  # only 1 session — ignored
    ])
    fake, (msg, ok) = _run(monkeypatch, events)
    assert ok and "recurring-git-commit" in msg
    assert len(fake.added) == 1
    skill = fake.added[0]
    assert skill["status"] == "draft"
    assert skill["source"] == "learned"
    assert any("git commit -m 'a'" in step for step in skill["procedure"])


def test_noop_when_nothing_repeats(monkeypatch):
    events = _events([("ls -la", "s1"), ("pwd", "s2")])
    with pytest.raises(BaseException) as exc:
        _run(monkeypatch, events)
    assert "no command pattern repeated" in str(exc.value)


def test_skips_patterns_that_already_have_skills(monkeypatch):
    events = _events([
        ("git commit -m 'a'", "s1"), ("git commit -m 'b'", "s2"),
        ("git commit -m 'c'", "s3"),
    ])
    with pytest.raises(BaseException) as exc:
        _run(monkeypatch, events, existing=["recurring-git-commit"])
    assert "already have skills" in str(exc.value)


def test_registered():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS
    assert "suggest_skills_from_history" in BUILTIN_ACTIONS
    assert "suggest_skills_from_history" in BUILTIN_ACTION_INFO
    assert HOUSEKEEPING_DEFAULTS["suggest_skills_from_history"]["schedule"] == "cron"
