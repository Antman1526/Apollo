from pathlib import Path

from tests.css_source import read_all_css


CSS = read_all_css()
UI_JS = Path("static/js/ui.js").read_text(encoding="utf-8")


def test_prompt_bar_exposes_desktop_resize_handle():
    assert "resize: vertical;" in CSS
    assert "max-height: min(60vh, 600px);" in CSS


def test_auto_resize_preserves_a_manually_chosen_height():
    assert "textarea._manualResizeHeight = height;" in UI_JS
    assert "const manualHeight = textarea._manualResizeHeight || 0;" in UI_JS
    assert "const maxHeight = Math.max(autoMaxHeight, manualHeight);" in UI_JS
