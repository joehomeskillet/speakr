"""Menu-level V8 views mounted in the clinic shell (v8-71; V8 P04, P05, P08, P09, P19, P21, DESIGN_SYSTEM section 1).

The real application runs in a child process (the synthetic server of the w213 mount test). With CLINIC_MODE on,
headless Chromium checks that each of the six views is reachable through its entry and its hash route and that
its module root renders (1280x720), that the entries are reachable at 390x844, that icon-only entries have
accessible names and a tooltip, and that the Archiv hands a dictation over to the existing correction view.
With CLINIC_MODE off the rendered page carries none of the new markup.

The browser cases need Playwright, its Chromium, the gitignored static/vendor and a built static/css/tailwind.css.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("clinic_shell_nav_fixtures", ROOT / "tests/clinic/test_wp_v8_shell_nav.py")
_nav = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nav)
live_server = _nav.live_server
_render = _nav._render
_launch = _nav._launch
_login = _nav._login

# hash route, module root selector, entry (how a user reaches it)
VIEWS = [
    ("#/neu", "[data-testid=clinic-recording-view]", "menu-new-recording"),
    ("#/live", "[data-testid=clinic-live-view]", "menu-live"),
    ("#/archiv", "[data-testid=clinic-archive-view]", "menu-archive"),
    ("#/import", "[data-testid=clinic-import-view]", "menu-file"),
    ("#/einstellungen", ".clinic-settings-view", "user-menu:nav-clinic-settings"),
    ("#/offline", ".clinic-offline", "nav-clinic-offline"),
]
NEW_MARKUP = ("clinic-menu-actions", "clinic-menu-view", "nav-clinic-new", "nav-clinic-offline", "nav-clinic-settings",
              "clinic-recording-view", "clinic-archive-view", "clinic-import-view", "clinic-settings-view",
              "clinic-offline-view", "V8 menu views")


def _stub(page, dictation):
    item = {"dictation_id": dictation, "workflow_status": "zu_archivieren", "day": "2026-11-19", "time_source": "recorded",
            "sort_time": "2026-11-19T08:00:00Z", "duration_ms": 4000, "dictated_by": {"display_name": "Synthetisch"},
            "staff_pools": [], "lease_holder": None}
    page.route("**/api/clinic/v1/recordings*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"items": [item], "next_cursor": None})))


def _mounted(page):
    # Before Vue has mounted, the page still holds the raw template with every v-if branch; wait for the app.
    page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)


def _open_entry(page, entry):
    if entry.startswith("user-menu:"):
        page.locator("[data-user-menu-toggle]").first.click()
        page.locator(f"[data-testid={entry.split(':', 1)[1]}]").click()
    else:
        page.locator(f"[data-testid={entry}]").first.click()


def _hash(page):
    return page.evaluate("window.location.hash")


def _start(browser, base, dictation, width, height):
    page = _login(browser, base, width, height)
    _stub(page, dictation)
    page.goto(base + "/?dictation=" + dictation + "#/aufnahmen")
    _mounted(page)
    page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
    return page


@pytest.mark.parametrize("hash_route,root,entry", VIEWS)
def test_view_reachable_by_entry_and_by_hash_route(live_server, hash_route, root, entry):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _start(browser, base, dictation, 1280, 720)
            # By its entry.
            _open_entry(page, entry)
            page.locator(root).wait_for(state="visible", timeout=30000)
            assert page.locator(root).count() == 1
            assert _hash(page) == hash_route
            # Back to Aufnahmen, then by the direct hash route.
            page.locator("[data-testid=nav-recordings]").click()
            page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
            page.goto(base + "/?dictation=" + dictation + hash_route)
            page.reload()
            _mounted(page)
            page.locator(root).wait_for(state="visible", timeout=30000)
            assert page.locator(root).count() == 1  # the mounted view, not the raw template branches
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_header_new_recording_is_hidden_only_on_recorder(live_server, width: int, height: int) -> None:
    from playwright.sync_api import expect, sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _start(browser, base, dictation, width, height)
            page.route("**/api/config?recording_preflight=1", lambda route: route.fulfill(json={"routable": True}))
            new = page.get_by_role("link", name="Neue Aufnahme", exact=True).and_(page.get_by_test_id("nav-clinic-new"))
            expect(new).to_be_visible()
            new.click()
            expect(page.get_by_role("button", name="Aufnahme starten", exact=True)).to_be_visible()
            expect(new).to_have_count(0)
            page.reload()
            _mounted(page)
            expect(page.get_by_role("button", name="Aufnahme starten", exact=True)).to_be_visible()
            expect(new).to_have_count(0)
            page.get_by_test_id("nav-recordings").click()
            expect(new).to_be_visible()
            new.focus()
            expect(page.get_by_role("tooltip")).to_have_text("Neue Aufnahme")
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_live_view_without_dictation_asks_for_the_id(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, 1280, 720)
            _stub(page, dictation)
            page.goto(base + "/#/live")
            _mounted(page)
            picker = page.locator("[data-testid=clinic-live-picker]")
            picker.wait_for(state="visible", timeout=30000)
            assert page.locator("[data-testid=clinic-live-open]").is_disabled()
            page.locator("[data-testid=clinic-live-id]").fill(dictation)
            page.locator("[data-testid=clinic-live-open]").click()
            page.locator("[data-testid=clinic-live-view]").wait_for(state="visible", timeout=30000)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_toolbar_is_not_a_primary_tab_and_archive_opens_the_correction_view(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _start(browser, base, dictation, 1280, 720)
            tabs = page.locator("[data-testid=clinic-primary-nav] button")
            assert [t.strip() for t in tabs.all_inner_texts()] == ["Aufnahmen", "Arbeitsliste"]
            assert [t.strip() for t in page.locator("[data-testid=clinic-menu-actions] button").all_inner_texts()] == [
                "Neue Aufnahme", "Datei", "Live-Diktat", "Archiv"]
            # The upstream new-recording entry is replaced, not duplicated.
            assert page.locator("header button[aria-label='Neue Aufnahme'], header [data-testid=nav-clinic-new]").count() == 1
            page.locator("[data-testid=menu-archive]").click()
            page.locator("[data-testid=clinic-archive-view]").wait_for(state="visible", timeout=30000)
            assert page.locator("[data-testid=menu-archive]").get_attribute("aria-current") == "page"
            page.locator("[data-testid=archive-open]").first.click()
            page.locator("[data-testid=clinic-correction-view]").wait_for(state="visible", timeout=30000)
            assert dictation in page.locator("[data-testid=clinic-correction-view]").inner_text()
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_archive_back_returns_to_aufnahmen(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _start(browser, base, dictation, 1280, 720)
            page.locator("[data-testid=menu-archive]").click()
            page.locator("[data-testid=archive-back]").wait_for(state="visible", timeout=30000)
            page.locator("[data-testid=archive-back]").click()
            page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
            assert _hash(page) == "#/aufnahmen"
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_phone_entries_reachable_with_accessible_names(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _start(browser, base, dictation, 390, 844)
            # Icon-only at 390 px: the visible label is hidden, the accessible name stays.
            new = page.locator("[data-testid=nav-clinic-new]")
            offline = page.locator("[data-testid=nav-clinic-offline]")
            assert new.is_visible() and offline.is_visible()
            assert new.get_attribute("aria-label") == "Neue Aufnahme"
            assert offline.get_attribute("aria-label") == "Verbindungsstatus / Entwürfe"
            for view_root, entry in (("[data-testid=clinic-recording-view]", "nav-clinic-new"),
                                     (".clinic-offline", "nav-clinic-offline"),
                                     ("[data-testid=clinic-live-picker]", "menu-live"),  # no ?dictation after a header-link reload
                                     ("[data-testid=clinic-archive-view]", "menu-archive"),
                                     ("[data-testid=clinic-import-view]", "menu-file"),
                                     (".clinic-settings-view", "user-menu:nav-clinic-settings")):
                page.locator("[data-testid=nav-recordings]").click()
                page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
                _open_entry(page, entry)
                page.locator(view_root).wait_for(state="visible", timeout=30000)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_icon_only_entries_have_a_tooltip(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _start(browser, base, dictation, 1280, 720)
            offline = page.locator("[data-testid=nav-clinic-offline]")
            assert offline.get_attribute("aria-label") == "Verbindungsstatus / Entwürfe"
            offline.focus()
            tip = page.locator("[role=tooltip]")
            tip.wait_for(state="visible", timeout=10000)
            assert tip.inner_text() == "Verbindungsstatus / Entwürfe"
            assert offline.get_attribute("aria-describedby") == tip.get_attribute("id")
            page.keyboard.press("Escape")
            tip.wait_for(state="detached", timeout=10000)
        finally:
            browser.close()


def test_clinic_page_renders_the_menu_entries(tmp_path):
    out = _render(tmp_path, True)
    for needle in ("clinic-menu-actions", "clinic-menu-view", "nav-clinic-new", "nav-clinic-offline", "nav-clinic-settings",
                   "<clinic-recording-view", "<clinic-live-view", "<clinic-archive-view", "<clinic-import-view",
                   "<clinic-settings-view", "<clinic-offline-view"):
        assert needle in out, needle
    assert 'aria-label="Neue Aufnahme"' in out and 'aria-label="Verbindungsstatus / Entwürfe"' in out
    assert "switchToUploadView" not in out.split('data-testid="nav-clinic-new"', 1)[0].rsplit("New Recording Button", 1)[-1]


def test_upstream_page_has_no_menu_markup(tmp_path):
    out = _render(tmp_path, False)
    assert 'data-clinic-mode="False"' in out
    for needle in NEW_MARKUP:
        assert needle not in out, needle
    assert "switchToUploadView" in out
