"""The Council — ask several models the same question, then have a reviewer
model synthesize consensus, disagreements, and a recommended answer.

Pure prompt-building / parsing helpers plus the async orchestration. The
actual LLM call is injected (defaults to ``llm_call_async``) so this module
is trivially testable without network access.
"""

import asyncio
import logging
import re
import string
from typing import Any, Callable, Dict, List, Optional

from src.llm_core import llm_call_async

logger = logging.getLogger(__name__)

_MEMBER_SYSTEM = (
    "You are one of several independent experts being asked the same question. "
    "Answer concisely and directly, on your own — you cannot see the other experts' answers."
)

_SYNTHESIS_SYSTEM = (
    "You are the reviewer for a council of independent models that were each asked the "
    "same question on their own. Read their answers and produce a concise synthesis in "
    "exactly this format, with each section on its own line or paragraph:\n"
    "CONSENSUS: <what most or all of them agree on>\n"
    "DISAGREEMENTS: <where they differ, or 'none'>\n"
    "RECOMMENDED ANSWER: <your recommended final answer>"
)

_SECTION_LABELS = ("consensus", "disagreements", "recommended answer")

_SECTION_PATTERN = re.compile(
    r"(?im)^[ \t]*(consensus|disagreements|recommended answer)[ \t]*:[ \t]*"
    r"(.*?)(?=^[ \t]*(?:consensus|disagreements|recommended answer)[ \t]*:|\Z)",
    re.DOTALL,
)


def build_member_messages(question: str) -> List[Dict[str, str]]:
    """Chat messages sent to each Council member for the same question."""
    return [
        {"role": "system", "content": _MEMBER_SYSTEM},
        {"role": "user", "content": question},
    ]


def build_synthesis_messages(question: str, answers: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Chat messages sent to the reviewer, labeling each answer A/B/C… with its model name."""
    letters = string.ascii_uppercase
    lines = []
    for i, answer in enumerate(answers):
        label = letters[i] if i < len(letters) else str(i)
        model = answer.get("model", "?")
        if answer.get("error"):
            lines.append(f"{label} ({model}): [no answer — {answer['error']}]")
        else:
            lines.append(f"{label} ({model}): {answer.get('text', '')}")
    user = (
        f"Question:\n{question}\n\n"
        "Answers:\n" + "\n\n".join(lines) + "\n\n"
        "Synthesize the consensus, note any disagreements, and give one recommended answer. "
        "Be concise."
    )
    return [{"role": "system", "content": _SYNTHESIS_SYSTEM}, {"role": "user", "content": user}]


def parse_synthesis(text: str) -> Dict[str, str]:
    """Split the reviewer's reply into consensus/disagreements/recommended sections.

    Case-insensitive labels, tolerant of markdown bold (`**CONSENSUS:**`) and
    of a section being missing entirely (returns "" for it).
    """
    cleaned = (text or "").replace("**", "").replace("__", "")
    sections = {"consensus": "", "disagreements": "", "recommended": ""}
    for match in _SECTION_PATTERN.finditer(cleaned):
        label = match.group(1).lower()
        key = "recommended" if label == "recommended answer" else label
        sections[key] = match.group(2).strip()
    return sections


async def run_council(
    question: str,
    members: List[Dict[str, Any]],
    reviewer: Optional[Dict[str, Any]],
    *,
    call: Callable[..., Any] = llm_call_async,
    timeout: int = 90,
) -> Dict[str, Any]:
    """Ask every member the same question concurrently, then (if a reviewer is
    configured and at least one member answered) have the reviewer synthesize
    the results.

    Returns ``{"question", "answers": [{model, text, error}], "synthesis": {model, text, sections} | None}``.
    """

    async def _ask_member(member: Dict[str, Any]) -> Dict[str, Any]:
        try:
            text = await call(
                member["url"], member["model"], build_member_messages(question),
                headers=member.get("headers"), temperature=0.7, timeout=timeout,
            )
            return {"model": member["model"], "text": text, "error": None}
        except Exception as error:
            logger.warning("Council member %s failed: %s", member.get("model"), error)
            return {"model": member["model"], "text": "", "error": str(error)}

    results = await asyncio.gather(
        *(_ask_member(member) for member in members), return_exceptions=True
    )
    answers: List[Dict[str, Any]] = []
    for member, result in zip(members, results):
        if isinstance(result, Exception):
            answers.append({"model": member["model"], "text": "", "error": str(result)})
        else:
            answers.append(result)

    synthesis: Optional[Dict[str, Any]] = None
    if reviewer and any(not a["error"] for a in answers):
        try:
            text = await call(
                reviewer["url"], reviewer["model"], build_synthesis_messages(question, answers),
                headers=reviewer.get("headers"), temperature=0.2, timeout=timeout,
            )
            synthesis = {"model": reviewer["model"], "text": text, "sections": parse_synthesis(text)}
        except Exception as error:
            logger.warning("Council reviewer %s failed: %s", reviewer.get("model"), error)
            synthesis = {
                "model": reviewer["model"], "text": "", "sections": parse_synthesis(""),
                "error": str(error),
            }

    return {"question": question, "answers": answers, "synthesis": synthesis}
