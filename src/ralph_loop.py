"""Apollo-native Ralph loop helpers.

This is intentionally a small, opt-in project loop rather than a daemon. It
keeps the useful Ralph ideas close to Apollo's workflow: PRD stories, quality
checks, append-only learnings, and explicit completion records.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.subproc_env import build_agent_env


DEFAULT_RALPH_DIR = ".apollo/ralph"
DEFAULT_CHECK_COMMAND = "./scripts/check.sh"
MAX_CAPTURE_CHARS = 12_000
EXIT_SIGNAL_RE = re.compile(r"\bEXIT_SIGNAL\b\s*[:=]\s*true\b", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_stamp() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RalphPaths:
    root: Path

    @property
    def prd(self) -> Path:
        return self.root / "prd.json"

    @property
    def progress(self) -> Path:
        return self.root / "progress.md"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def agent_learnings(self) -> Path:
        return self.root / "AGENTS.learning.md"


def paths_for(root: str | os.PathLike[str] = DEFAULT_RALPH_DIR) -> RalphPaths:
    return RalphPaths(Path(root))


def init_workspace(root: str | os.PathLike[str] = DEFAULT_RALPH_DIR, *, force: bool = False) -> RalphPaths:
    paths = paths_for(root)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)
    if force or not paths.prd.exists():
        _write_json(paths.prd, _default_prd())
    if force or not paths.progress.exists():
        paths.progress.write_text("# Apollo Ralph Progress\n\n", encoding="utf-8")
    if force or not paths.state.exists():
        _write_json(paths.state, {"iterations": [], "created_at": _now_stamp()})
    if force or not paths.agent_learnings.exists():
        paths.agent_learnings.write_text(
            "# Apollo Ralph Learnings\n\n"
            "Append concise project learnings here. Copy durable items into AGENTS.md when they become policy.\n",
            encoding="utf-8",
        )
    return paths


def _default_prd() -> dict[str, Any]:
    return {
        "project": "Apollo",
        "branchName": "codex/apollo-ralph-loop",
        "userStories": [
            {
                "id": "story-1",
                "title": "Replace this with a small Apollo improvement",
                "priority": "high",
                "passes": False,
                "failed": False,
                "dependencies": [],
                "acceptanceCriteria": [
                    "Change is scoped and reversible",
                    "Relevant tests pass",
                    "Learning is recorded in progress.md",
                ],
            }
        ],
    }


def load_prd(path: str | os.PathLike[str]) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("prd.json must contain an object")
    stories = data.get("userStories")
    if not isinstance(stories, list):
        raise ValueError("prd.json must contain a userStories list")
    return data


def save_prd(path: str | os.PathLike[str], prd: dict[str, Any]) -> None:
    _write_json(Path(path), prd)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _truncate_output(output: str) -> str:
    if len(output) > MAX_CAPTURE_CHARS:
        return output[-MAX_CAPTURE_CHARS:]
    return output


def story_id(story: dict[str, Any]) -> str:
    return str(story.get("id") or story.get("storyId") or story.get("title") or "").strip()


def story_dependencies(story: dict[str, Any]) -> list[str]:
    raw = story.get("dependencies", story.get("dependsOn", []))
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, Iterable):
        return [str(x) for x in raw if str(x).strip()]
    return []


def story_verification_command(story: dict[str, Any]) -> str:
    command = story.get("verificationCommand", story.get("verification_command", ""))
    return str(command or "").strip()


def priority_rank(value: Any) -> tuple[int, str]:
    if isinstance(value, (int, float)):
        return (int(value), str(value))
    text = str(value or "medium").strip().lower()
    named = {"critical": 0, "highest": 0, "high": 1, "medium": 2, "normal": 2, "low": 3}
    if text in named:
        return (named[text], text)
    try:
        return (int(text), text)
    except ValueError:
        return (2, text)


def completed_story_ids(prd: dict[str, Any]) -> set[str]:
    return {story_id(s) for s in prd.get("userStories", []) if s.get("passes") is True}


def select_next_story(prd: dict[str, Any], *, include_failed: bool = False) -> dict[str, Any] | None:
    done = completed_story_ids(prd)
    candidates = []
    for index, story in enumerate(prd.get("userStories", [])):
        if story.get("passes") is True:
            continue
        if story.get("failed") is True and not include_failed:
            continue
        deps = story_dependencies(story)
        if any(dep not in done for dep in deps):
            continue
        candidates.append((priority_rank(story.get("priority")), index, story))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def status_summary(prd: dict[str, Any]) -> dict[str, Any]:
    stories = prd.get("userStories", [])
    complete = [s for s in stories if s.get("passes") is True]
    failed = [s for s in stories if s.get("failed") is True and s.get("passes") is not True]
    blocked = [
        s for s in stories
        if s.get("passes") is not True
        and s.get("failed") is not True
        and story_dependencies(s)
        and select_next_story({"userStories": [s] + complete}) is None
    ]
    next_story = select_next_story(prd)
    return {
        "total": len(stories),
        "complete": len(complete),
        "failed": len(failed),
        "blocked": len(blocked),
        "pending": max(0, len(stories) - len(complete) - len(failed)),
        "next": story_id(next_story) if next_story else None,
        "done": bool(stories) and len(complete) == len(stories),
    }


def build_prompt(prd: dict[str, Any], story: dict[str, Any], progress_text: str = "") -> str:
    criteria = story.get("acceptanceCriteria") or story.get("acceptance_criteria") or []
    if isinstance(criteria, str):
        criteria = [criteria]
    criteria_text = "\n".join(f"- {item}" for item in criteria) or "- Relevant tests pass"
    progress = progress_text.strip() or "No prior learnings recorded."
    verification = story_verification_command(story)
    verification_text = f"\nExtra verification command: {verification}\n" if verification else ""
    return (
        "You are running Apollo's enhanced Ralph loop.\n\n"
        "Work on exactly one story. Keep the change scoped. Run the configured quality checks. "
        "Record durable learnings for future iterations.\n\n"
        f"Project: {prd.get('project', 'Apollo')}\n"
        f"Story ID: {story_id(story)}\n"
        f"Title: {story.get('title', '')}\n"
        f"Priority: {story.get('priority', 'medium')}\n\n"
        "Acceptance criteria:\n"
        f"{criteria_text}\n\n"
        f"{verification_text}"
        "Prior learnings:\n"
        f"{progress}\n\n"
        "Completion contract:\n"
        "- Mark the story complete only when checks pass.\n"
        "- Append concise learnings to progress.md.\n"
        "- End with EXIT_SIGNAL: true only when this story is actually complete.\n"
    )


def has_exit_signal(output: str | None) -> bool:
    """Return true when an agent explicitly declares the Ralph story complete."""
    text = output or ""
    if EXIT_SIGNAL_RE.search(text):
        return True
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        value = data.get("EXIT_SIGNAL", data.get("exit_signal"))
        if value is True or str(value).strip().lower() == "true":
            return True
    return False


def read_progress(path: str | os.PathLike[str]) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def append_learning(path: str | os.PathLike[str], story: dict[str, Any], learning: str) -> None:
    text = learning.strip()
    if not text:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"## {_now_stamp()} - {story_id(story)}\n\n{text}\n\n")


def append_agent_learning(path: str | os.PathLike[str], learning: str) -> None:
    text = learning.strip()
    if not text:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n## {_now_stamp()}\n\n{text}\n")


def mark_story(prd: dict[str, Any], story_id_value: str, *, passes: bool, failed: bool = False) -> dict[str, Any]:
    for story in prd.get("userStories", []):
        if story_id(story) == story_id_value:
            story["passes"] = bool(passes)
            story["failed"] = bool(failed and not passes)
            story["updatedAt"] = _now_stamp()
            return story
    raise KeyError(f"Story not found: {story_id_value}")


def run_quality_check(
    command: str = DEFAULT_CHECK_COMMAND,
    *,
    cwd: str | os.PathLike[str] = ".",
    timeout_seconds: float = 60 * 60,
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            # Minimal allowlisted env — the verification command runs with no
            # host secrets (SECURITY-FIXLIST P1 #2). A check that needs an extra
            # var can opt in via build_agent_env(passthrough=...).
            env=build_agent_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": 124,
            "ok": False,
            "timed_out": True,
            "output": _truncate_output(_coerce_output(exc.stdout) or f"Timed out after {timeout_seconds:g}s"),
        }
    output = _truncate_output(proc.stdout or "")
    return {
        "command": command,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "timed_out": False,
        "output": output,
    }


def append_iteration(state_path: str | os.PathLike[str], record: dict[str, Any]) -> None:
    path = Path(state_path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"iterations": []}
    data.setdefault("iterations", []).append({"timestamp": _now_stamp(), **record})
    _write_json(path, data)
