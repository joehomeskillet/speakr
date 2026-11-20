"""V8 P20 login page: password show/hide toggle, no remember checkbox (CLINIC_MODE only).

Render cases use the real app and the real template; the behaviour case drives the
shipped script in headless Chromium (Playwright from requirements-dev.lock; set
PLAYWRIGHT_CHROMIUM_EXECUTABLE when the bundled build differs).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.app import app

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "static" / "js" / "clinic" / "login-ui.js"


def login_page(monkeypatch, clinic: bool) -> str:
    if clinic:
        monkeypatch.setenv("CLINIC_MODE", "1")
    else:
        monkeypatch.delenv("CLINIC_MODE", raising=False)
    response = app.test_client().get("/login")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_clinic_login_has_toggle_and_no_remember(monkeypatch):
    html = login_page(monkeypatch, True)
    button = re.search(r"<button[^>]*data-password-toggle[^>]*>", html)
    assert button, "password toggle missing"
    assert 'type="button"' in button[0]
    assert 'aria-pressed="false"' in button[0]
    assert 'aria-label="Passwort anzeigen"' in button[0]
    assert 'name="remember"' not in html
    assert "login-ui.js" in html
    assert re.search(r'<input[^>]*id="password"[^>]*type="password"|<input[^>]*type="password"[^>]*id="password"', html)


def test_non_clinic_login_unchanged(monkeypatch):
    html = login_page(monkeypatch, False)
    assert "data-password-toggle" not in html
    assert "login-ui.js" not in html
    assert 'name="remember"' in html


def test_toggle_switches_type_and_keeps_focus(monkeypatch):
    html = login_page(monkeypatch, True)
    button = re.search(r"<button[^>]*data-password-toggle.*?</button>", html, re.S)[0]
    page_html = '<form><input id="password" type="password" name="password">' + button + "</form>"
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        page = browser.new_page()
        page.set_content(page_html)
        page.add_script_tag(path=str(SCRIPT))
        toggle = page.locator("[data-password-toggle]")
        field = page.locator("#password")
        toggle.focus()
        page.keyboard.press("Enter")
        assert field.get_attribute("type") == "text"
        assert toggle.get_attribute("aria-pressed") == "true"
        assert toggle.get_attribute("aria-label") == "Passwort verbergen"
        assert page.evaluate("document.activeElement.hasAttribute('data-password-toggle')")
        page.keyboard.press("Space")
        assert field.get_attribute("type") == "password"
        assert toggle.get_attribute("aria-pressed") == "false"
        assert toggle.get_attribute("aria-label") == "Passwort anzeigen"
        assert page.evaluate("document.activeElement.hasAttribute('data-password-toggle')")
        toggle.click()
        assert field.get_attribute("type") == "text"
        assert page.evaluate("document.activeElement.hasAttribute('data-password-toggle')")
        browser.close()
