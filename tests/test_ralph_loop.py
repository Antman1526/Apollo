import json
import subprocess
import sys

from src import ralph_loop as ralph


def _prd():
    return {
        "project": "Apollo",
        "userStories": [
            {"id": "done", "title": "Done", "priority": "critical", "passes": True},
            {"id": "blocked", "title": "Blocked", "priority": "high", "passes": False, "dependencies": ["missing"]},
            {"id": "low", "title": "Low", "priority": "low", "passes": False},
            {"id": "next", "title": "Next", "priority": "high", "passes": False, "dependencies": ["done"]},
            {"id": "failed", "title": "Failed", "priority": "critical", "passes": False, "failed": True},
        ],
    }


def test_select_next_story_respects_priority_dependencies_and_failed_state():
    story = ralph.select_next_story(_prd())
    assert ralph.story_id(story) == "next"

    story = ralph.select_next_story(_prd(), include_failed=True)
    assert ralph.story_id(story) == "failed"


def test_status_summary_reports_next_and_done_state():
    summary = ralph.status_summary(_prd())
    assert summary["total"] == 5
    assert summary["complete"] == 1
    assert summary["failed"] == 1
    assert summary["next"] == "next"
    assert summary["done"] is False


def test_build_prompt_includes_story_acceptance_and_learnings():
    prd = _prd()
    story = {
        "id": "s1",
        "title": "Ship Ralph",
        "priority": "high",
        "acceptanceCriteria": ["checks pass"],
        "verificationCommand": "scripts/check-paperclip-browser --dry-run",
    }
    prompt = ralph.build_prompt(prd, story, "Use scripts/check.sh")

    assert "Story ID: s1" in prompt
    assert "- checks pass" in prompt
    assert "scripts/check-paperclip-browser --dry-run" in prompt
    assert "Use scripts/check.sh" in prompt
    assert "EXIT_SIGNAL: true" in prompt


def test_has_exit_signal_accepts_text_and_json_contracts():
    assert ralph.has_exit_signal("work complete\nEXIT_SIGNAL: true")
    assert ralph.has_exit_signal('{"exit_signal": true}')
    assert not ralph.has_exit_signal("checks passed but still investigating")


def test_init_record_and_learning_files(tmp_path):
    paths = ralph.init_workspace(tmp_path / "ralph")
    prd = ralph.load_prd(paths.prd)
    story_id = ralph.story_id(prd["userStories"][0])

    story = ralph.mark_story(prd, story_id, passes=True)
    ralph.save_prd(paths.prd, prd)
    ralph.append_learning(paths.progress, story, "Learned the local check command.")
    ralph.append_agent_learning(paths.agent_learnings, "Apollo CLI scripts use argparse.")
    ralph.append_iteration(paths.state, {"story_id": story_id, "ok": True})

    updated = ralph.load_prd(paths.prd)
    assert updated["userStories"][0]["passes"] is True
    assert "Learned the local check command." in paths.progress.read_text(encoding="utf-8")
    assert "Apollo CLI scripts use argparse." in paths.agent_learnings.read_text(encoding="utf-8")
    assert json.loads(paths.state.read_text(encoding="utf-8"))["iterations"][-1]["ok"] is True


def test_run_quality_check_captures_success(tmp_path):
    result = ralph.run_quality_check(f"{sys.executable} -c 'print(\"ok\")'", cwd=tmp_path)

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert "ok" in result["output"]


def test_run_quality_check_reports_timeout(tmp_path):
    result = ralph.run_quality_check(
        f"{sys.executable} -c 'import time; time.sleep(2)'",
        cwd=tmp_path,
        timeout_seconds=0.05,
    )

    assert result["ok"] is False
    assert result["returncode"] == 124
    assert result["timed_out"] is True


def test_apollo_ralph_cli_init_status_and_next_prompt(tmp_path):
    root = tmp_path / "ralph"

    init = subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "init"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    assert '"ok": true' in init.stdout

    status = subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "status"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    assert json.loads(status.stdout)["next"] == "story-1"

    prompt = subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "next", "--prompt"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    assert "Apollo's enhanced Ralph loop" in prompt.stdout


def test_apollo_ralph_run_once_without_agent_only_prints_prompt(tmp_path):
    root = tmp_path / "ralph"
    subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "init"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/apollo-ralph",
            "--root",
            str(root),
            "run-once",
            "--check-command",
            f"{sys.executable} -c 'raise SystemExit(99)'",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    assert "Apollo's enhanced Ralph loop" in result.stdout
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["iterations"] == []


def test_apollo_ralph_auto_mark_requires_exit_signal(tmp_path):
    root = tmp_path / "ralph"
    subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "init"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    no_signal = subprocess.run(
        [
            sys.executable,
            "scripts/apollo-ralph",
            "--root",
            str(root),
            "run-once",
            "--agent-cmd",
            f"{sys.executable} -c 'print(\"checks pass, still working\")'",
            "--check-command",
            f"{sys.executable} -c 'print(\"ok\")'",
            "--auto-mark",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    assert json.loads(no_signal.stdout)["auto_marked"] is False
    assert ralph.load_prd(root / "prd.json")["userStories"][0]["passes"] is False

    with_signal = subprocess.run(
        [
            sys.executable,
            "scripts/apollo-ralph",
            "--root",
            str(root),
            "run-once",
            "--agent-cmd",
            f"{sys.executable} -c 'print(\"EXIT_SIGNAL: true\")'",
            "--check-command",
            f"{sys.executable} -c 'print(\"ok\")'",
            "--auto-mark",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    assert json.loads(with_signal.stdout)["auto_marked"] is True
    assert ralph.load_prd(root / "prd.json")["userStories"][0]["passes"] is True


def test_apollo_ralph_auto_mark_requires_verification_command(tmp_path):
    root = tmp_path / "ralph"
    subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "init"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    failed = subprocess.run(
        [
            sys.executable,
            "scripts/apollo-ralph",
            "--root",
            str(root),
            "run-once",
            "--agent-cmd",
            f"{sys.executable} -c 'print(\"EXIT_SIGNAL: true\")'",
            "--check-command",
            f"{sys.executable} -c 'print(\"ok\")'",
            "--verification-command",
            f"{sys.executable} -c 'raise SystemExit(42)'",
            "--auto-mark",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert failed.returncode == 42
    assert ralph.load_prd(root / "prd.json")["userStories"][0]["passes"] is False
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["iterations"][-1]["verification_ok"] is False


def test_apollo_ralph_records_agent_timeout(tmp_path):
    root = tmp_path / "ralph"
    subprocess.run(
        [sys.executable, "scripts/apollo-ralph", "--root", str(root), "init"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/apollo-ralph",
            "--root",
            str(root),
            "run-once",
            "--agent-cmd",
            f"{sys.executable} -c 'import time; time.sleep(2)'",
            "--timeout-minutes",
            "0.001",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 124
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["iterations"][-1]["timed_out"] is True
