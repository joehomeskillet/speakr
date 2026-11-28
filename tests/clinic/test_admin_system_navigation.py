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
    toggle = page.get_by_test_id("clinic-admin-nav-toggle")

    def open_navigation():
        if width == 390:
            toggle.click()

    open_navigation()
    user_link = nav.get_by_role("button", name="Benutzer und Rollen", exact=True)
    user_link.wait_for()
    assert nav.get_by_role("button", name="Klinik", exact=True).count() == 0
    assert page.locator("[data-user-sort-compact]").count() == 0
    account = page.get_by_test_id("clinic-users-view")
    account.locator("tbody tr").first.wait_for()
    assert len(nav.locator('[aria-current="page"]').all()) == 1
    if width == 390:
        toggle.click()
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
    if width == 390:
        assert toggle.inner_text() == "Bereich: System-Auditprotokoll"
    open_navigation()
    nav.get_by_role("link", name="Systemstatistik").click()
    open_navigation()
    nav.get_by_role("link", name="Systemeinstellungen").click()
    page.get_by_role("heading", name="Systemeinstellungen").wait_for()
    page.go_back()
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["Systemstatistik"]
    page.reload()
    open_navigation()
    nav.get_by_role("link", name="Systemstatistik").wait_for()
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["Systemstatistik"]
    nav.get_by_role("link", name="Systemeinstellungen").click()
    page.get_by_role("heading", name="Systemeinstellungen").wait_for()
    open_navigation()
    expect_in_nav = """() => {
        const nav = document.querySelector('[data-testid="clinic-v8-nav"]');
        const active = nav.querySelector('[aria-current="page"]').getBoundingClientRect();
        const bounds = nav.getBoundingClientRect();
        return active.left >= bounds.left - 1 && active.right <= bounds.right + 1;
    }"""
    page.wait_for_function(expect_in_nav)
    if width == 390:
        toggle.click()
    menu = page.get_by_test_id("clinic-pages-menu")
    menu.locator("summary").click()
    menu.get_by_role("link", name="Schreibkräfte-Pools").click()
    page.get_by_test_id("clinic-v8-view-admin-pools").wait_for()
    assert nav.locator('[aria-current="page"]').all_inner_texts() == ["Schreibkräfte-Pools"]
    page.goto(base + "/admin")
    account.locator("tbody tr").first.wait_for()
    open_navigation()
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

@pytest.mark.parametrize("width,height", [(320, 700), (390, 844), (1440, 900)])
def test_account_actions_and_active_pool_group_have_clear_context(live_server, browser, width, height):
    base, _ = live_server
    page, _ = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + "/admin")
    users = page.get_by_test_id("clinic-users-view")
    user_region = users.get_by_role("region", name="Konten", exact=True)
    user_region.get_by_label("Anmelderichtlinie für").wait_for()
    assert user_region.get_by_role("button", name="Anmelderichtlinie öffnen").is_visible()
    role_note = users.get_by_text("Eine Identitätsrolle gibt keinen Diktatzugriff.", exact=False)
    assert not role_note.is_visible()
    users.get_by_role("button", name="Fachrechte", exact=True).click()
    assert role_note.is_visible()
    assert not users.get_by_label("Anmelderichtlinie für").is_visible()

    nav = page.get_by_test_id("clinic-v8-nav")
    toggle = page.get_by_test_id("clinic-admin-nav-toggle")
    if width < 640:
        toggle.press("Enter")
    pool = nav.get_by_role("button", name="Schreibkräfte-Pools", exact=True)
    pool.focus()
    pool.press("Enter")
    page.get_by_test_id("clinic-v8-view-admin-pools").wait_for()
    if width < 640:
        toggle.wait_for()
        assert toggle.inner_text() == "Bereich: Schreibkräfte-Pools"
        assert toggle.get_attribute("aria-expanded") == "false"
        assert toggle.evaluate("el => el === document.activeElement")
        toggle.press("Enter")
    page.wait_for_function("""() => {
      const nav = document.querySelector('[data-testid="clinic-v8-nav"]');
      const item = nav.querySelector('[aria-current="page"]');
      const label = item.closest('[role="group"]').querySelector('.cadm-nav__label');
      const bounds = nav.getBoundingClientRect();
      return [item, label].every(el => {
        const r = el.getBoundingClientRect();
        return r.left >= bounds.left - 1 && r.right <= bounds.right + 1;
      });
    }""")
    assert pool.get_attribute("aria-current") == "page"
    if width >= 640:
        assert pool.evaluate("el => el === document.activeElement")
