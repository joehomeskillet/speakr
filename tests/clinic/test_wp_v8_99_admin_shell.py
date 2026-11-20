"""Klinik admin shell and identity panels look like controls, not raw text (v8-99).

Reuses the harness of test_wp_mount_admin (real Flask app, throwaway SQLite database, synthetic users only, headless
Chromium). Checks the structure the owner complaint of 2026-11-20 was about: one grouped navigation instead of two rows,
styled tables and buttons in the four identity panels, and no horizontal page overflow at 390 px.
"""

from __future__ import annotations

from test_wp_mount_admin import ADMIN_EMAIL, _login, browser, live_server  # noqa: F401  (fixtures of the earlier mount package)

_FIXTURES = (browser, live_server)


def _open(browser, base, width, height):
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + "/admin")
    page.locator("[data-testid=admin-tab-clinic]").click()
    page.locator("[data-testid=clinic-admin]").wait_for(state="visible", timeout=20000)
    return page, js_errors


def test_one_grouped_navigation_with_every_panel_and_test_id(live_server, browser):
    base, _ids = live_server
    page, js_errors = _open(browser, base, 1440, 900)
    assert page.locator("[data-testid=clinic-admin] nav").count() == 1
    groups = page.locator(".cadm-nav__group")
    assert groups.count() >= 4
    # every panel of both families is still reachable under its test id
    assert page.locator("[data-testid^=clinic-panel-]").count() == 9
    assert page.locator("[data-testid^=clinic-v8-panel-]").count() == 9
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
    base, _ids = live_server
    page, js_errors = _open(browser, base, 390, 844)
    for panel in ("accounts", "pools", "grants", "directory"):
        page.locator(f"[data-testid=clinic-panel-{panel}]").click()
        page.locator(f"[data-testid=clinic-view-{panel}]").wait_for(state="visible", timeout=20000)
        page.wait_for_timeout(500)
        overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1 || document.body.scrollWidth > innerWidth + 1")
        assert not overflow, panel
        spilled = page.evaluate(
            """() => [...document.querySelectorAll('[data-testid=clinic-admin] *')].filter(el => {
                if (el.getBoundingClientRect().right <= innerWidth + 1) return false;
                for (let p = el.parentElement; p; p = p.parentElement) {
                    const ox = getComputedStyle(p).overflowX;
                    if ((ox === 'auto' || ox === 'scroll') && (p.matches('.cadm-nav, .cadm-tablewrap') || p.dataset.testid === 'clinic-admin')) return false;
                }
                return true;
            }).map(el => el.className || el.tagName)"""
        )
        assert not spilled, (panel, spilled)
    assert not js_errors, js_errors
