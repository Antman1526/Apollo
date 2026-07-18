"""Browser-level smoke coverage against an already-running isolated app."""

import os

import pytest


def _setup_and_login(page):
    result = page.evaluate("""async () => {
        const setup = await fetch('/api/auth/setup', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: 'e2e-admin', password: 'e2e-password-123' }),
        });
        const login = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: 'e2e-admin', password: 'e2e-password-123' }),
        });
        return { setup: setup.status, login: login.status, loginBody: await login.json() };
    }""")
    assert result["setup"] in (200, 400), result
    assert result["login"] == 200, result
    assert result["loginBody"].get("ok") is True, result
    page.reload(wait_until="networkidle")


@pytest.mark.e2e
def test_landing_page_renders_without_console_errors():
    playwright = pytest.importorskip("playwright.sync_api")
    base_url = os.environ["APOLLO_E2E_BASE_URL"]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("APOLLO_E2E_CHROMIUM"))
        for viewport in ({"width": 1440, "height": 900}, {"width": 390, "height": 844}):
            page = browser.new_page(viewport=viewport)
            errors = []
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(base_url, wait_until="networkidle", timeout=30_000)
            _setup_and_login(page)
            errors.clear()
            page.reload(wait_until="networkidle")
            assert page.title() == "Apollo Chat"
            assert not errors
            page.close()
        browser.close()


@pytest.mark.e2e
def test_document_create_edit_and_save():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ["APOLLO_E2E_CHROMIUM"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(os.environ["APOLLO_E2E_BASE_URL"], wait_until="networkidle")
        _setup_and_login(page)
        page.evaluate("window.documentModule.newDocument()")
        editor = page.locator("#doc-editor-textarea")
        editor.wait_for(state="visible")
        editor.fill("# E2E document\nSaved through the editor")
        page.evaluate("window.documentModule.saveDocument()")
        page.wait_for_timeout(300)
        document_id = page.evaluate("window.documentModule.getCurrentDocId()")
        payload = page.evaluate("""async (docId) => {
            const response = await fetch(`/api/document/${encodeURIComponent(docId)}`, { credentials: 'same-origin' });
            return { ok: response.ok, body: await response.json() };
        }""", document_id)
        assert payload["ok"], payload["body"]
        payload = payload["body"]
        assert payload.get("current_content") == "# E2E document\nSaved through the editor", payload
        browser.close()
