from services.review.reviewer import build_review_prompt, parse_review


def test_prompt_includes_qa_and_asks_for_verdict():
    msgs = build_review_prompt("Is the sky green?", "Yes, always.")
    assert msgs[0]["role"] == "system"
    u = msgs[-1]["content"]
    assert "Is the sky green?" in u and "Yes, always." in u
    assert "verdict" in u.lower()


def test_parse_review_extracts_verdict_and_issues():
    text = ("Verdict: incorrect\n"
            "Issues:\n- The sky is blue, not green\n- Overgeneralizes with 'always'\n"
            "Suggestion: Say the sky appears blue due to Rayleigh scattering")
    r = parse_review(text)
    assert r["verdict"].lower() == "incorrect"
    assert any("blue" in i for i in r["issues"])
    assert "Rayleigh" in r["suggestion"]
    assert r["raw"] == text


def test_parse_review_tolerates_freeform():
    r = parse_review("Looks accurate and complete.")
    assert r["raw"] == "Looks accurate and complete."
    assert isinstance(r["issues"], list)   # empty ok
