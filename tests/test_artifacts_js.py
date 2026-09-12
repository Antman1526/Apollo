"""String-scrape guards for the live artifact pane (static/js/artifacts.js).

The behavioural tests live in tests/test_artifacts.mjs (node --test); these
pin the security-relevant wiring that only shows up in source text:

* codeRunner.runHTML no longer executes model HTML through a popup +
  ``document.write`` (which ran it with the app's own origin).
* The preview iframe is sandboxed WITHOUT ``allow-same-origin`` — the whole
  isolation story rests on the artifact living in an opaque origin.
* markdown.js emits the ``preview-artifact`` button for html/svg fences and
  the chat renderer / chat finaliser hook into the module.
"""
from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent / "static"
_JS = _STATIC / "js"


def _read(name: str) -> str:
    return (_JS / name).read_text(encoding="utf-8")


def test_code_runner_no_longer_uses_document_write():
    src = _read("codeRunner.js")
    assert "document.write(" not in src
    assert "import('./artifacts.js')" in src


def test_artifacts_sandbox_never_grants_same_origin():
    src = _read("artifacts.js")
    assert "allow-same-origin" not in src
    assert "allow-scripts allow-modals allow-popups" in src
    assert 'referrerpolicy="no-referrer"' in src
    # The srcdoc CSP is the first head element and denies everything by default.
    assert "default-src 'none'" in src
    assert 'http-equiv="Content-Security-Policy"' in src


def test_markdown_emits_preview_button_for_artifact_fences():
    src = _read("markdown.js")
    assert "preview-artifact" in src
    # xml fences only count when the block is an <svg> document
    assert "<svg" in src


def test_chat_wiring_points_at_artifacts_module():
    # The ▣ click delegation lives in artifacts.js itself (chatRenderer.js is
    # under the module-size ratchet and must not grow).
    artifacts = _read("artifacts.js")
    assert ".preview-artifact" in artifacts
    assert "openFromCode(code, lang, { autoOpened: false })" in artifacts
    assert ".preview-artifact" not in _read("chatRenderer.js")
    chat = _read("chat.js")
    assert "autoOpenFromMessage" in chat
    index = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-ui-key="artifacts_auto_open"' in index
    css = (_STATIC / "css" / "artifacts.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "#artifact-pane" in css or ".artifact-pane" in css
