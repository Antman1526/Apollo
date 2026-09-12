"""String-scrape guards for the Cookbook failure-feedback UI wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_diagnosis_card_renders_command_and_tail_details():
    source = _read("static/js/cookbook-diagnosis.js")

    assert "cookbook-diag-cmd" in source
    assert "cookbook-diag-tail" in source
    assert "cookbook-diag-details" in source
    assert "_diagCopyButton('Copy command'" in source
    assert "_diagCopyButton('Copy output'" in source
    assert "_lastOutputLines(sourceText, 40)" in source
    assert "export function _redactCrashReportText" in source
    assert "task?.payload?._cmd || task?.cmd" in source


def test_download_launch_stores_cmd_and_persists_failures():
    source = _read("static/js/cookbookDownload.js")

    assert "if (data.cmd) payload._cmd = data.cmd;" in source
    assert "_recordLaunchFailure(" in source
    assert "_recordLaunchFailure = shared._recordLaunchFailure;" in source


def test_running_module_persists_serve_launch_failures():
    source = _read("static/js/cookbookRunning.js")

    assert "export function _recordLaunchFailure(" in source
    assert "status: 'crashed'" in source
    assert "_launchFailed: true" in source
    assert "if (task._launchFailed) continue;" in source
    assert "_recordLaunchFailure(data.session_id, shortName, 'serve'" in source
    assert "function _terminalDownloadDiagnosis(" in source
    assert "_redactCrashReportText } from './cookbook-diagnosis.js'" in source
    assert "_recordLaunchFailure" in _read("static/js/cookbook.js")


def test_feedback_css_is_scoped_to_feedback_file():
    css = _read("static/css/cookbook-feedback.css")
    assert ".cookbook-diag-cmd" in css
    assert ".cookbook-diag-tail" in css
    assert "var(--panel)" in css and "var(--border)" in css and "var(--fg)" in css and "var(--red)" in css
    assert ".cookbook-diag-cmd" not in _read("static/css/cookbook.css")
    assert 'cookbook-feedback.css' in _read("static/index.html")
