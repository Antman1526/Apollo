"""Browser-level smoke coverage against an already-running isolated app."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from threading import Thread

import pytest


def _setup_and_login(page):
    result = page.evaluate("""async () => {
        const status = await fetch('/api/auth/status', { credentials: 'same-origin' });
        const state = await status.json();
        let setupStatus = null;
        if (!state.configured) {
          const setup = await fetch('/api/auth/setup', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: 'e2e-admin', password: 'e2e-password-123' }),
          });
          setupStatus = setup.status;
        }
        const login = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: 'e2e-admin', password: 'e2e-password-123' }),
        });
        return { setup: setupStatus, login: login.status, loginBody: await login.json() };
    }""")
    assert result["setup"] in (None, 200), result
    assert result["login"] == 200, result
    assert result["loginBody"].get("ok") is True, result
    # Workspace background polling may begin immediately after login, so a
    # global network-idle wait is not a valid readiness signal here.
    page.reload(wait_until="commit")


@contextmanager
def _browser_fixture_server():
    """Serve deterministic local HTML for the agent-browser journey."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
            body = b"""<!doctype html><html><head><title>Browser fixture</title></head>
            <body><h1 id=fixture-heading>Agent browser fixture</h1>
            <p>Visible fixture content</p><script>console.log('fixture console ready')</script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _browser_api(page, path, *, method="GET", body=None):
    return page.evaluate(
        """async ({ path, method, body }) => {
            const response = await fetch(path, {
              method,
              headers: body ? { 'Content-Type': 'application/json' } : undefined,
              credentials: 'same-origin',
              body: body ? JSON.stringify(body) : undefined,
            });
            const text = await response.text();
            let payload;
            try { payload = JSON.parse(text); } catch (_) { payload = { text }; }
            return { status: response.status, payload };
        }""",
        {"path": path, "method": method, "body": body},
    )


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
        page.goto(os.environ["APOLLO_E2E_BASE_URL"], wait_until="commit")
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


@pytest.mark.e2e
def test_browser_panel_and_agent_browser_local_fixture():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ["APOLLO_E2E_CHROMIUM"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(os.environ["APOLLO_E2E_BASE_URL"], wait_until="commit")
        _setup_and_login(page)

        # The panel's HTTP fallback is a supported path. Keep its live viewer
        # disconnected here so this UI-only validation does not take over the
        # shared agent browser that the API assertions below need to control.
        page.evaluate("""() => {
            window.WebSocket = class { constructor() { throw new Error('disabled for E2E'); } };
        }""")
        page.locator("#rail-browser").click()
        page.locator("#browser-modal").wait_for(state="visible")
        page.locator("#browser-address").fill("file:///tmp/e2e-blocked.html")
        page.locator("#browser-go").click()
        assert "Blocked URL scheme: file" in page.locator("#browser-status").inner_text()
        page.locator("#close-browser-modal").click()

        blocked = _browser_api(
            page,
            "/api/browser/navigate",
            method="POST",
            body={"url": "javascript:alert('blocked')"},
        )
        assert blocked["status"] == 400, blocked
        assert "blocked URL scheme" in blocked["payload"]["detail"], blocked

        with _browser_fixture_server() as fixture_url:
            navigated = _browser_api(
                page,
                "/api/browser/navigate",
                method="POST",
                body={"url": fixture_url},
            )
            assert navigated["status"] == 200, navigated
            assert navigated["payload"]["title"] == "Browser fixture", navigated

            visible = _browser_api(page, "/api/browser/text")
            assert visible["status"] == 200, visible
            assert "Visible fixture content" in visible["payload"]["text"], visible

            executed = _browser_api(
                page,
                "/api/browser/execute",
                method="POST",
                body={"script": "document.querySelector('#fixture-heading').textContent"},
            )
            assert executed["status"] == 200, executed
            assert executed["payload"]["result"] == "Agent browser fixture", executed

            for _ in range(20):
                events = _browser_api(page, "/api/browser/events")
                if any("fixture console ready" in event["message"] for event in events["payload"]["events"]):
                    break
                page.wait_for_timeout(100)
            else:
                pytest.fail(f"fixture console log was not piped: {events}")
        browser.close()


@pytest.mark.e2e
def test_paperclip_floor_renders_preview_and_live_agent_activity():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ["APOLLO_E2E_CHROMIUM"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(os.environ["APOLLO_E2E_BASE_URL"], wait_until="commit")
        _setup_and_login(page)

        rail = page.locator("#rail-paperclip")
        rail.wait_for(state="visible")
        rail.click()
        modal = page.locator("#paperclip-modal")
        modal.wait_for(state="visible")
        page.locator("g.paperclip-roaming-agent").first.wait_for(state="visible")
        assert page.locator("g.paperclip-roaming-agent").count() >= 4
        assert "agents" in page.locator("#paperclip-live-count").inner_text()

        ingested = page.evaluate("""async () => {
            const response = await fetch('/api/paperclip/events', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              credentials: 'same-origin',
              body: JSON.stringify({ events: [
                { type: 'agent.status', payload: {
                  agentId: 'e2e-architect', name: 'E2E Architect', role: 'coding',
                  status: 'running', task: 'Verify live workspace flow'
                } },
                { type: 'activity.logged', payload: {
                  fromAgentId: 'e2e-architect', toAgentId: 'coder',
                  message: 'Live workspace handoff verified'
                } }
              ] }),
            });
            return { status: response.status, body: await response.json() };
        }""")
        assert ingested["status"] == 200, ingested
        assert ingested["body"]["accepted"] == 2, ingested
        page.locator('g[data-agent-id="e2e-architect"]').wait_for(state="visible")
        page.get_by_text("Live workspace handoff verified").wait_for(state="visible")
        assert page.locator("g.paperclip-roaming-agent.talking").count() >= 1

        page.set_viewport_size({"width": 390, "height": 844})
        content_box = page.locator("#paperclip-modal .modal-content").bounding_box()
        assert content_box is not None
        assert content_box["width"] <= 390
        page.keyboard.press("Escape")
        modal.wait_for(state="hidden")
        browser.close()
