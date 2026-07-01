from services.memory.distiller import build_distill_prompt, parse_facts, distill_transcript


def test_build_prompt_includes_transcript():
    msgs = build_distill_prompt("USER: I use Postgres 16.\nASSISTANT: Noted.")
    assert msgs[0]["role"] == "system"
    assert "Postgres 16" in msgs[-1]["content"]


def test_parse_facts_handles_bullets_numbers_blanks():
    text = "- User uses Postgres 16\n1. Prefers dark mode\n\n  \n* Lives in Berlin\nplain fact"
    facts = parse_facts(text)
    assert "User uses Postgres 16" in facts
    assert "Prefers dark mode" in facts
    assert "Lives in Berlin" in facts
    assert "plain fact" in facts
    assert "" not in facts and all(f == f.strip() for f in facts)


def test_parse_facts_drops_none_marker():
    assert parse_facts("NONE") == []
    assert parse_facts("(no durable facts)") == []


def test_distill_transcript_uses_injected_llm():
    calls = {}

    def fake_llm(messages):
        calls["msgs"] = messages
        return "- Fact A\n- Fact B"

    facts = distill_transcript("USER: hi", fake_llm)
    assert facts == ["Fact A", "Fact B"]
    assert "hi" in calls["msgs"][-1]["content"]
