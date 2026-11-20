"""Klinik admin shell and identity panels look like controls, not raw text (v8-99).

Reuses the harness of test_wp_mount_admin (real Flask app, throwaway SQLite database, synthetic users only, headless
Chromium). Checks the structure the owner complaint of 2026-11-20 was about: one grouped navigation instead of two rows,
styled tables and buttons in the four identity panels, and no horizontal page overflow at 390 px.
"""

from __future__ import annotations

from playwright.sync_api import expect

from test_wp_mount_admin import ADMIN_EMAIL, _login, browser, live_server  # noqa: F401  (fixtures of the earlier mount package)

_FIXTURES = (browser, live_server)


def _open(browser, base, width, height):
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + "/admin")
    expect(page.get_by_test_id("clinic-v8-view-users").locator("#acc-title")).to_be_visible()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)
    return page, js_errors


def test_one_grouped_navigation_with_every_panel_and_test_id(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open(browser, base, 1440, 900)
    nav = page.get_by_role("navigation", name="Verwaltung", exact=True)
    expect(nav).to_have_count(1)
    assert nav.locator(".cadm-nav__group").count() >= 4
    expected = {
        "clinic-panel-" + panel for panel in ("directory", "categories", "regex", "columns", "preferences")
    } | {
        "clinic-v8-panel-" + panel for panel in
        ("users", "admin-pools", "authors", "vocabulary", "commands-templates", "asr-profiles", "import", "jobs")
    } | {"clinic-policy-panel-acceptance"}
    ids = nav.locator("button[data-testid^=clinic-]").evaluate_all("items => items.map(el => el.dataset.testid)")
    assert sorted(ids) == sorted(expected)
    expect(page.get_by_test_id("clinic-admin-withheld")).to_contain_text("Aktivität und Audit")
    assert not js_errors, js_errors


def test_accounts_table_is_styled_and_actions_are_separate_named_buttons(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open(browser, base, 1440, 900)
    page.locator("#acc-title").wait_for(state="visible", timeout=20000)
    table = page.locator(".cadm-table").first
    table.wait_for(state="visible", timeout=20000)
    header_background = table.locator("thead").evaluate("el => getComputedStyle(el).backgroundColor")
    assert header_background not in ("rgba(0, 0, 0, 0)", "transparent")
    row = table.locator("tbody tr").first
    buttons = row.get_by_role("button")
    assert buttons.count() >= 2
    for index in range(buttons.count()):
        button = buttons.nth(index)
        assert button.get_attribute("title") and button.get_attribute("aria-label")
    assert not js_errors, js_errors


def test_no_horizontal_page_overflow_at_390_px_for_the_four_identity_panels(live_server, browser):
    base, ids = live_server
    page, js_errors = _open(browser, base, 390, 844)
    nav = page.get_by_test_id("clinic-v8-nav")
    for panel, entry, root in (
        ("accounts", "Benutzer und Rollen", "#acc-title"),
        ("pools", "Diktierende und Autorenpools", "#pools-title"),
        ("grants", "Benutzer und Rollen", "#grants-title"),
        ("directory", "Verzeichnis", "[data-testid=clinic-view-directory]"),
    ):
        nav.get_by_role("button", name=entry, exact=True).click()
        if panel == "pools":
            page.locator("#authors-pools").click()
        if panel == "grants":
            page.get_by_role("button", name="Fachrechte", exact=True).click()
            page.locator("#usr-pick").select_option(str(ids["typist"]))
        expect(page.locator(root)).to_be_visible()
        overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1 || document.body.scrollWidth > innerWidth + 1")
        assert not overflow, panel
        spilled = page.evaluate(
            """() => [...document.querySelectorAll('[data-testid=clinic-admin] *')].filter(el => {
                if (el.getBoundingClientRect().right <= innerWidth + 1) return false;
                for (let p = el.parentElement; p; p = p.parentElement) {
                    const ox = getComputedStyle(p).overflowX;
                    if ((ox === 'auto' || ox === 'scroll') && (p.matches('.cadm-nav, .cadm-tablewrap, .v8-admin-page .table-wrap') && p.getBoundingClientRect().right <= innerWidth + 1 || p.dataset.testid === 'clinic-admin')) return false;
                }
                return true;
            }).map(el => el.className || el.tagName)"""
        )
        assert not spilled, (panel, spilled)
    assert not js_errors, js_errors

def test_clinic_admin_lands_on_accounts_with_the_active_tab_visible_on_mobile(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + "/admin")
    page.locator("#acc-title").wait_for(state="visible")
    tab = page.get_by_role("button", name="Benutzer und Rollen", exact=True)
    expect(tab).to_have_attribute("aria-current", "page")
    assert tab.evaluate("el => el.getBoundingClientRect().left >= 0 && el.getBoundingClientRect().right <= innerWidth")
    assert not js_errors, js_errors

def test_accounts_search_and_composed_create_action(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open(browser, base, 1440, 900)
    page.get_by_test_id("clinic-v8-panel-users").click()
    view = page.get_by_test_id("clinic-users-view")
    search = view.get_by_role("searchbox", name="Suche")
    with page.expect_response(lambda r: "/admin/users?" in r.url and "search=W226TYPIST" in r.url and r.status == 200):
        search.fill("W226TYPIST")
    rows = view.locator("table tbody tr")
    expect(rows).to_have_count(1)
    expect(rows).to_contain_text("w226typist")
    search.fill("kein-treffer")
    view.get_by_text("Keine passenden Konten.").wait_for()
    view.get_by_role("button", name="Konto anlegen").click()
    expect(page.locator("#acc-username")).to_be_focused()
    assert not js_errors, js_errors
