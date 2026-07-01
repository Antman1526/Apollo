"""Adversarial review of an assistant answer by a second model.

Pure: builds the critique prompt and parses the model's output. The LLM call
lives in the route.
"""
import re

_SYSTEM = (
    "You are an adversarial reviewer. Critically check an assistant's answer for "
    "factual errors, missing caveats, unsupported claims, and gaps. Be concise and "
    "specific. Respond in this format:\n"
    "Verdict: <accurate|incomplete|incorrect|needs context>\n"
    "Issues:\n- <issue>\n- <issue>\n"
    "Suggestion: <one-line fix, or 'none'>"
)


def build_review_prompt(question: str, answer: str) -> list:
    user = (f"Question:\n{question}\n\nAssistant answer:\n{answer}\n\n"
            "Review it. Give your Verdict, Issues, and Suggestion.")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def parse_review(text: str) -> dict:
    text = text or ""
    verdict, suggestion, issues = "", "", []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"(?i)^verdict\s*:\s*(.+)$", s)
        if m:
            verdict = m.group(1).strip(); continue
        m = re.match(r"(?i)^suggestion\s*:\s*(.+)$", s)
        if m:
            sug = m.group(1).strip()
            suggestion = "" if sug.lower() == "none" else sug
            continue
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", s)
        if m:
            issues.append(m.group(1).strip())
    return {"verdict": verdict, "issues": issues, "suggestion": suggestion, "raw": text}
