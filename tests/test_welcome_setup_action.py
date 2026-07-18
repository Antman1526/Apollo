from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_run_welcome_uses_the_existing_setup_action_contract():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    models = (ROOT / "static" / "js" / "models.js").read_text(encoding="utf-8")

    assert 'class="welcome-setup-action setup-trigger-link"' in index
    assert 'class="welcome-setup-action setup-trigger-link"' in models
    assert 'type="button"' in index
