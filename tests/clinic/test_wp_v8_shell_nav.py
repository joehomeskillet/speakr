"""Clinic shell navigation in the normal page (v8-03; UI8-002, V8 P01/P02, UI-V6-D03/D04, UI-V8-D02).

The real application is started in a child process (real Flask app, throwaway SQLite database, the
synthetic typist and dictation of the w213 mount test; no patient data). With CLINIC_MODE on, headless
Chromium logs in through the login form at 1440x900, 1280x720 and 390x844 and checks:

* nothing of components/sidebar.html (sidebar, phone list, queue dock) is in the DOM,
* exactly two primary tabs, "Aufnahmen" and "Arbeitsliste"; Aufnahmen is shown after login,
* tab switch and browser back / forward return to the previous view, the URL carries the view,
* keyboard focus moves to the heading of the view that is shown,
* a restored view keeps its in-memory state (a typed filter),
* Ressourcen is an entry of the header menu,
* a dictation opened from Aufnahmen and from Arbeitsliste opens the correction view and closing
  returns to the view it came from.

With CLINIC_MODE off the rendered page still has the sidebar and none of the clinic shell. The
browser cases need Playwright and its Chromium (requirements-dev.lock), the gitignored static/vendor
and a built static/css/tailwind.css; without them they error. The rendered-page cases need no browser.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The synthetic server, the render helper and the login of the w213 mount test are reused as they are.
_spec = importlib.util.spec_from_file_location("clinic_mount_correction_fixtures", ROOT / "tests/clinic/test_wp_mount_correction.py")
_mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mc)
live_server = _mc.live_server
_render = _mc._render
_launch = _mc._launch

VIEWPORTS = [(1440, 900), (1280, 720), (390, 844)]
SIDEBAR_DOM = "aside, .sidebar, .sidebar-content-wrapper, .sidebar-list, .ph-list"


def _login(browser, base, width, height):
    page = browser.new_context(viewport={"width": width, "height": height}).new_page()
    page.js_errors, page.responses = [], []  # uncaught exceptions only; refused upstream calls are not shell failures
    page.on("pageerror", lambda exc: page.js_errors.append(str(exc)))
    page.on("response", lambda r: page.responses.append(f"{r.status} {r.url}") if "/api/clinic/" in r.url else None)
    page.goto(base + "/login")
    page.fill("input[name=email]", _mc.TYPIST)
    page.fill("input[name=password]", _mc.PASSWORD)
    page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
    page.wait_for_load_state("load")
    assert "/login" not in page.url
    return page


def _stub_recordings_api(page, dictation):
    # GET /api/clinic/v1/recordings (package v8-01) is not registered by the app yet: the shell test serves
    # the one synthetic dictation of the server through the contract shape; everything else is the real server.
    item = {"dictation_id": dictation, "workflow_status": "zu_schreiben", "day": "2026-11-19", "time_source": "recorded",
            "sort_time": "2026-11-19T08:00:00Z", "duration_ms": 4000, "dictated_by": {"display_name": "Synthetisch"},
            "staff_pools": [], "lease_holder": None}
    page.route("**/api/clinic/v1/recordings*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"items": [item], "next_cursor": None})))


def _hash(page):
    return page.evaluate("window.location.hash")


def _heading_focused(page):
    page.wait_for_function(
        "['H1','H2','H3'].includes(document.activeElement && document.activeElement.tagName) "
        "|| (document.activeElement && document.activeElement.hasAttribute('data-clinic-shell-view'))", timeout=20000)


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_shell_without_sidebar_two_tabs_and_history(live_server, width, height):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, width, height)
            _stub_recordings_api(page, dictation)
            page.goto(base + "/")
            recordings = page.locator("[data-testid=clinic-recordings-view]")
            worklist = page.locator("[data-testid=clinic-worklist-view]")
            recordings.wait_for(state="visible", timeout=30000)  # Aufnahmen is the landing view
            assert _hash(page) == "#/aufnahmen"

            # Structurally absent: no sidebar, no phone list, no queue dock, no hamburger.
            assert page.locator(SIDEBAR_DOM).count() == 0
            assert page.locator("main.main-content").count() == 1
            assert not page.locator("header button:has(> i.fa-bars)").is_visible()

            # Exactly two primary tabs.
            tabs = page.locator("[data-testid=clinic-primary-nav] button")
            assert [t.strip() for t in tabs.all_inner_texts()] == ["Aufnahmen", "Arbeitsliste"]
            assert page.locator("[data-testid=nav-recordings]").get_attribute("aria-current") == "page"

            # Keep a typed filter of Aufnahmen, switch, come back by the tab and by history: state is kept.
            page.locator("[data-testid=filter-author]").fill("42")
            page.locator("[data-testid=nav-clinic-worklist]").click()
            worklist.wait_for(state="visible", timeout=30000)
            assert _hash(page) == "#/arbeitsliste"
            assert page.locator("[data-testid=nav-clinic-worklist]").get_attribute("aria-current") == "page"
            _heading_focused(page)
            recordings.wait_for(state="detached", timeout=20000)

            page.go_back()
            recordings.wait_for(state="visible", timeout=20000)
            assert _hash(page) == "#/aufnahmen"
            assert page.locator("[data-testid=filter-author]").input_value() == "42"
            _heading_focused(page)

            page.go_forward()
            worklist.wait_for(state="visible", timeout=20000)
            assert _hash(page) == "#/arbeitsliste"

            # Ressourcen and Verwaltung are in the header menu; the link is a history entry too.
            page.locator("[data-user-menu-toggle]").first.click()
            page.locator("[data-testid=nav-clinic-resources]").click()
            page.locator(".clinic-resources-view").wait_for(state="visible", timeout=30000)
            assert _hash(page) == "#/ressourcen"
            page.go_back()
            worklist.wait_for(state="visible", timeout=20000)

            # A direct link restores the view.
            page.goto(base + "/#/arbeitsliste")
            page.reload()
            worklist.wait_for(state="visible", timeout=30000)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_open_dictation_and_close_returns_to_origin(live_server, width, height):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, width, height)
            _stub_recordings_api(page, dictation)
            page.goto(base + "/")
            recordings = page.locator("[data-testid=clinic-recordings-view]")
            correction = page.locator("[data-testid=clinic-correction-view]")
            recordings.wait_for(state="visible", timeout=30000)

            # From Aufnahmen.
            link = page.locator("[data-testid=open-link]:visible").first
            try:
                link.wait_for(state="visible", timeout=30000)
            except Exception as exc:
                raise AssertionError(f"no row in Aufnahmen: {recordings.inner_text()!r}; responses: {page.responses}") from exc
            link.click()
            correction.wait_for(state="visible", timeout=30000)
            assert dictation in correction.inner_text()
            assert correction.locator("[data-dictator-name]").inner_text() == "Synthetisch"
            assert correction.locator("[data-pool-name]").inner_text() == "Kein Schreibdienst zugeordnet"
            back = page.locator("[data-testid=clinic-correction-back]")
            assert back.inner_text().strip() == "Zurück zu Aufnahmen"
            back.click()
            recordings.wait_for(state="visible", timeout=30000)
            assert page.locator("[data-testid=clinic-correction-view]").count() == 0

            # From Arbeitsliste.
            page.locator("[data-testid=nav-clinic-worklist]").click()
            worklist = page.locator("[data-testid=clinic-worklist-view]")
            worklist.wait_for(state="visible", timeout=30000)
            row = page.locator("[data-row]").first
            row.wait_for(state="visible", timeout=30000)
            author = row.locator('[data-label="Diktiert von"]').inner_text()
            pool = row.locator('[data-label="Schreibdienst"]').inner_text()
            row.click()
            worklist.get_by_role("button", name="Öffnen", exact=True).click()
            correction.wait_for(state="visible", timeout=30000)
            back = page.locator("[data-testid=clinic-correction-back]")
            assert back.inner_text().strip() == "Zurück zur Arbeitsliste"
            assert correction.locator("[data-dictator-name]").inner_text() == author
            assert correction.locator("[data-pool-name]").inner_text() == pool
            back.click()
            worklist.wait_for(state="visible", timeout=30000)
            assert page.locator("[data-testid=nav-clinic-worklist]").get_attribute("aria-current") == "page"

            # Browser back out of a correction returns to the view it came from, saving through the view.
            page.locator("[data-testid=nav-recordings]").click()
            recordings.wait_for(state="visible", timeout=30000)
            page.locator("[data-testid=open-link]:visible").first.click()
            correction.wait_for(state="visible", timeout=30000)
            page.go_back()
            recordings.wait_for(state="visible", timeout=30000)
            page.go_forward()
            correction.wait_for(state="visible", timeout=30000)
            assert correction.locator("[data-dictator-name]").inner_text() == "Synthetisch"
            assert "Synthetisch" not in page.evaluate("JSON.stringify(history.state)")
            # History from an older tab can have an ID with no matching row in this page.
            page.evaluate("""() => {
                const state = {...history.state, dictationId: '00000000-0000-4000-8000-000000000001'};
                history.replaceState(state, '', location.href);
                dispatchEvent(new PopStateEvent('popstate', {state}));
            }""")
            assert correction.locator("[data-dictator-name]").inner_text() == "Nicht angegeben"
            assert correction.locator("[data-pool-name]").inner_text() == "Nicht angegeben"
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_recorder_view_hides_the_empty_shell_container(live_server, width, height):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, width, height)
            _stub_recordings_api(page, dictation)
            page.goto(base + "/")
            page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
            # The upstream recorder ('recording') is not a shell view: no empty reserved pane above it.
            page.evaluate("document.querySelector('#app').__vue_app__._instance.proxy.currentView = 'recording'")
            shell = page.locator("[data-clinic-shell-view]")
            shell.wait_for(state="hidden", timeout=20000)
            recorder = page.locator("main.main-content > div.flex-1.flex-col").last
            recorder.wait_for(state="visible", timeout=20000)
            main_h = page.locator("main.main-content").evaluate("el => el.getBoundingClientRect().height")
            nav_h = page.locator("[data-testid=clinic-primary-nav]").evaluate("el => el.getBoundingClientRect().height")
            rec_h = recorder.evaluate("el => el.getBoundingClientRect().height")
            assert rec_h >= main_h - nav_h - 2, (rec_h, main_h, nav_h)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_no_upstream_placeholder_and_shell_fills_main(live_server, width, height):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, width, height)
            _stub_recordings_api(page, dictation)
            page.goto(base + "/")
            page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
            for tab, view in (("nav-recordings", "clinic-recordings-view"), ("nav-clinic-worklist", "clinic-worklist-view")):
                page.locator(f"[data-testid={tab}]").click()
                page.locator(f"[data-testid={view}]").wait_for(state="visible", timeout=30000)
                # The upstream empty-state placeholder is not in the DOM at all.
                assert page.locator("main.main-content .min-h-\\[60vh\\]").count() == 0  # the empty-state container
                assert "Aufnehmen oder hochladen" not in page.locator("main.main-content").inner_text()
                main_h = page.locator("main.main-content").evaluate("el => el.getBoundingClientRect().height")
                nav_h = page.locator("[data-testid=clinic-primary-nav]").evaluate("el => el.getBoundingClientRect().height")
                shell_h = page.locator("[data-clinic-shell-view]").evaluate("el => el.getBoundingClientRect().height")
                assert shell_h >= main_h - nav_h - 2, (shell_h, main_h, nav_h)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_clinic_page_has_no_sidebar_markup(tmp_path):
    out = _render(tmp_path, True)
    assert 'data-clinic-mode="True"' in out
    assert "<aside" not in out
    assert "emptyState.readyToCapture" not in out  # upstream empty-state placeholder (detail view) is not rendered
    assert 'class="sidebar-list' not in out and "sidebar-content-wrapper" not in out
    assert "isMobile ? 'ph-list'" not in out
    assert "<clinic-recordings-view" in out and "<clinic-worklist-view" in out
    assert "data-testid=\"nav-recordings\"" in out and "data-testid=\"nav-clinic-worklist\"" in out
    assert out.count("data-testid=\"nav-clinic-resources\"") == 1  # the header menu, not a tab
    assert "Ressourcen" in out


def test_upstream_page_still_renders_the_sidebar(tmp_path):
    out = _render(tmp_path, False)
    assert 'data-clinic-mode="False"' in out
    assert "<aside" in out and "emptyState.readyToCapture" in out and "sidebar-content-wrapper" in out and "isMobile ? 'ph-list'" in out
    assert "clinic-primary-nav" not in out and "clinic-recordings-view" not in out
    assert "nav-clinic-resources" not in out
