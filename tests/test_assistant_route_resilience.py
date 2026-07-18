"""Regression coverage for assistant settings fallbacks."""

from types import SimpleNamespace

from routes.assistant_routes import _crew_to_dict


def test_corrupt_or_non_list_tool_preferences_are_ignored():
    crew = SimpleNamespace(
        id="assistant-1",
        name="Assistant",
        avatar="",
        personality="",
        model="",
        endpoint_url="",
        greeting="",
        enabled_tools='{"not": "a list"}',
        session_id="session-1",
        is_default_assistant=True,
        timezone="UTC",
    )

    result = _crew_to_dict(crew)

    assert result["enabled_tools"] == []
    assert result["allow_autonomous_email"] is False
