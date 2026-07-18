"""Browser-level smoke coverage against an already-running isolated app."""

import os
from pathlib import Path

import pytest


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
            assert page.title() == "Apollo Chat"
            assert not errors
            page.close()
        browser.close()
