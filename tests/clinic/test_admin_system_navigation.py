"""Browser regression for the clinic and native administrative sections sharing one navigation."""

import pytest
from test_wp_mount_admin import ADMIN_EMAIL, LEAD_EMAIL, _login, _render, browser, live_server  # noqa: F401

@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_system_navigation_and_user_audit(live_server, browser, width, height):
    base, _ = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + "/admin")
    nav = page.get_by_test_id("clinic-v8-nav")
    user_link = nav.get_by_role("button", name="Benutzer und Rollen", exact=True)
    user_link.wait_for()
    assert nav.get_by_role("button", name="Klinik", exact=True).count() == 0
    assert page.locator("[data-user-sort-compact]").count() == 0
    account = page.get_by_test_id("clinic-users-view")
    account.locator("tbody tr").first.wait_for()
    assert len(nav.locator('[aria-current="page"]').all()) == 1
    picker = account.locator("#usr-login-account")
    picker.select_option(index=1)
    account.get_by_role("button", name="Anmelderichtlinie öffnen").click()
    assert account.locator("#acc-policy").evaluate("el => el === document.activeElement")
    activity = account.get_by_role("link", name="Systemaktivität dieses Kontos")
    user_id = activity.get_attribute("href").split("konto=")[1]
    activity.click()
    audit = page.locator("[data-audit-log]")
    audit.wait_for()
    assert audit.locator('[data-audit-filter="user"]').input_value() == user_id
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["System-Auditprotokoll"]
    assert not account.is_visible()
    nav.get_by_role("link", name="Systemstatistik").click()
    nav.get_by_role("link", name="Systemeinstellungen").click()
    page.get_by_role("heading", name="Systemeinstellungen").wait_for()
    page.go_back()
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["Systemstatistik"]
    page.reload()
    nav.get_by_role("link", name="Systemstatistik").wait_for()
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["Systemstatistik"]
    nav.get_by_role("link", name="Systemeinstellungen").click()
    page.get_by_role("heading", name="Systemeinstellungen").wait_for()
    expect_in_nav = """() => {
        const nav = document.querySelector('[data-testid="clinic-v8-nav"]');
        const active = nav.querySelector('[aria-current="page"]').getBoundingClientRect();
        const bounds = nav.getBoundingClientRect();
        return active.left >= bounds.left - 1 && active.right <= bounds.right + 1;
    }"""
    page.wait_for_function(expect_in_nav)
    menu = page.get_by_test_id("clinic-pages-menu")
    menu.locator("summary").click()
    menu.get_by_role("link", name="Schreibkräfte-Pools").click()
    page.get_by_test_id("clinic-v8-view-admin-pools").wait_for()
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["Schreibkräfte-Pools"]
    page.goto(base + "/admin")
    account.locator("tbody tr").first.wait_for()
    nav.get_by_role("button", name="Schreibkräfte-Pools").click()
    page.go_back()
    account.locator("tbody tr").first.wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors


def test_team_admin_cannot_open_native_system_hash(live_server, browser):
    base, _ = live_server
    page, _errors = _login(browser, base, LEAD_EMAIL)
    response = page.goto(base + "/admin#/system/audit")
    assert response.status == 403
    assert not page.locator("[data-audit-log]").is_visible()
    assert page.request.get(base + "/admin/audit-log").status == 403


def test_standard_admin_retains_its_existing_tab_and_user_inventory(tmp_path):
    html = _render(tmp_path, clinic=False)["/admin"]
    assert "clinic-admin-system-nav" not in html
    assert "data-user-sort-compact" in html
    assert "activeTab = 'users'" in html
