"""Browser test of the translations a page carries (discussion #611).

A page in another language than English embeds that language only; the
English table (about 190 KB) is no longer in it. English is fetched, once
and from the versioned locale URL, only when a key is missing in a language
that the browser fetched itself (a saved language that differs from the one
of the page). Until it arrives the text is empty, never the key, and the
Vue-rendered text is updated when it does. This test covers, in headless
Chromium:

- the sign-in page for a German browser (rendered on the server);
- the main page of an English account: English embedded as before, no
  locale request; and with German saved in the browser: one request for
  the German file, none for English.

The server side is in tests/test_page_assets.py, i18n.js in
static/js/i18n.test.js. It runs only with SPEAKR_BROWSER_SMOKE=1 against a
running Speakr at SPEAKR_BASE_URL, the instance of the browser smoke test.
"""

import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

pytestmark = pytest.mark.skipif(os.environ.get("SPEAKR_BROWSER_SMOKE") != "1",
                                reason="browser test; set SPEAKR_BROWSER_SMOKE=1 with Speakr running")

playwright_api = pytest.importorskip("playwright.sync_api") if os.environ.get("SPEAKR_BROWSER_SMOKE") == "1" else None

from browser_checks import ROOT, PageProblems, login  # noqa: E402
from seed_recordings import TITLES  # noqa: E402
from smoke_env import read_example  # noqa: E402

BASE = os.environ.get("SPEAKR_BASE_URL", "http://127.0.0.1:8899").rstrip("/")
_example = read_example()
ADMIN_EMAIL = os.environ.get("SPEAKR_SMOKE_USER", _example.get("ADMIN_EMAIL", "admin@example.com"))
ADMIN_PASSWORD = os.environ.get("SPEAKR_SMOKE_PASSWORD", _example.get("ADMIN_PASSWORD", "changeme"))

BOOTSTRAP = """() => ({
    locale: window.__I18N_BOOTSTRAP.locale,
    tables: Object.keys(window.__I18N_BOOTSTRAP.translations),
    fallback: window.__I18N_BOOTSTRAP.fallback === undefined ? null : Object.keys(window.__I18N_BOOTSTRAP.fallback).length,
    version: window.__I18N_ASSET_VERSION,
    ready: window.i18n.isReady,
    current: window.i18n.getLocale(),
    loaded: [...window.i18n.loadedLocales].sort(),
})"""


def strings(language):
    with open(os.path.join(ROOT, "static", "locales", f"{language}.json"), encoding="utf-8") as f:
        return json.load(f)


def text(table, key):
    value = table
    for part in key.split("."):
        value = value[part]
    return value


def locale_requests(page):
    """The locale files the page asks for, as path with query."""
    seen = []
    page.on("request", lambda r: seen.append(r.url[len(BASE):]) if "/static/locales/" in r.url else None)
    return seen


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def owner(browser):
    """The signed-in admin: one sign-in for the file."""
    context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
    page = context.new_page()
    login(page, BASE, ADMIN_EMAIL, ADMIN_PASSWORD)
    yield context, page
    context.close()


def test_the_sign_in_page_of_a_german_browser(browser):
    de = strings("de")
    context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="de-DE")
    try:
        page = context.new_page()
        problems = PageProblems(page, BASE)
        requests = locale_requests(page)
        page.goto(f"{BASE}/login")
        page.wait_for_load_state("load")
        assert page.locator("html").get_attribute("lang") == "de"
        assert text(de, "authPages.loginTitle") in page.title()
        assert page.locator("input[name=email]").count() == 1 and page.locator("input[name=password]").count() == 1
        page.wait_for_timeout(500)
        assert requests == [], requests
        assert not problems.failed and not problems.errors, (problems.failed, problems.errors)
    finally:
        context.close()


def test_an_english_main_page_is_unchanged_and_a_saved_language_fetches_only_itself(browser, owner):
    de = strings("de")
    owner_context, _ = owner
    context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
    try:
        context.add_cookies(owner_context.cookies())
        page = context.new_page()
        problems = PageProblems(page, BASE)
        requests = locale_requests(page)
        page.goto(f"{BASE}/")
        page.wait_for_load_state("load")
        page.wait_for_timeout(2000)
        state = page.evaluate(BOOTSTRAP)
        assert state["locale"] == "en" and state["tables"] == ["en"] and state["fallback"] is None, state
        assert state["ready"] is True and state["loaded"] == ["en"], state
        assert requests == [], requests
        assert not problems.failed and not problems.errors, (problems.failed, problems.errors)

        # German saved in this browser (the account is English): German is
        # fetched, English is already in the page.
        page.evaluate("() => localStorage.setItem('preferredLanguage', 'de')")
        problems.reset()
        page.goto(f"{BASE}/")
        page.wait_for_load_state("load")
        page.wait_for_timeout(2000)
        state = page.evaluate(BOOTSTRAP)
        assert state["current"] == "de" and state["loaded"] == ["de", "en"], state
        assert requests == [f"/static/locales/de.json?v={state['version']}"], requests
        page.locator("[data-user-menu-toggle]:visible").first.click()
        menu = page.locator("[data-user-menu-dropdown]:visible").first
        menu.wait_for(state="visible", timeout=5000)
        assert text(de, "nav.signOut") in menu.inner_text()
        assert not problems.failed and not problems.errors, (problems.failed, problems.errors)
    finally:
        context.close()
