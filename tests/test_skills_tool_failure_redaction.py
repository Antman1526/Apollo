"""Agent-facing skill parsing failures must remain generic and observable."""

import asyncio
import json
import sys
import types

from src.tools import skills_tasks


def test_edit_parse_failure_redacts_parser_detail(monkeypatch):
    class FakeSkillsManager:
        def __init__(self, _data_dir):
            pass

    class FailingSkill:
        @staticmethod
        def from_markdown(_content):
            raise ValueError("parser included private content")

    fake_skills = types.ModuleType("services.memory.skills")
    fake_skills.SkillsManager = FakeSkillsManager
    fake_format = types.ModuleType("services.memory.skill_format")
    fake_format.Skill = FailingSkill
    fake_format.slugify = lambda value: value
    monkeypatch.setitem(sys.modules, "services.memory.skills", fake_skills)
    monkeypatch.setitem(sys.modules, "services.memory.skill_format", fake_format)
    events = []
    monkeypatch.setattr(
        skills_tasks,
        "report_exception",
        lambda _logger, event, _error, **kwargs: events.append((event, kwargs)),
    )

    result = asyncio.run(
        skills_tasks.do_manage_skills(
            json.dumps({"action": "edit", "name": "skill", "content": "---\n---"}),
            owner="alice",
        )
    )

    assert result == {"error": "Could not parse content as SKILL.md", "exit_code": 1}
    assert events == [
        (
            "skill_tool_edit_parse_failed",
            {"outcome": "critical", "context": {"owner": "alice"}},
        )
    ]
