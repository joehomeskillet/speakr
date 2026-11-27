"""The active admin page leads; unrelated refusals remain available at the menu."""

import pytest
from playwright.sync_api import expect

from test_wp_mount_admin import ADMIN_EMAIL, _login, browser, live_server  # noqa: F401


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
@pytest.mark.parametrize("dark", [False, True])
def test_admin_heading_and_keyboard_permission_notice(live_server, browser, width, height, dark):
    base, _ = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.set_viewport_size({"width": width, "height": height})
    response = page.request.post(base + "/api/user/preferences", data={"dark_mode": dark},
                                headers={"X-CSRFToken": page.locator('meta[name="csrf-token"]').get_attribute("content")})
    assert response.status == 200
    for slug, title in [("benutzer", "Benutzer und Rollen"), ("schreibkraefte-pools", "Schreibkräfte-Pools")]:
        page.goto(base + "/admin#/" + slug)
        heading = page.get_by_role("heading", name=title, exact=True)
        expect(heading).to_be_visible()
        assert page.locator("html").evaluate("el => el.classList.contains('dark')") == dark
        assert heading.bounding_box()["y"] < 260
        expect(page.locator("#clinic-admin-title")).to_have_text("Verwaltung")
        assert page.locator("#clinic-admin-title").evaluate(
            "el => el.parentElement.getBoundingClientRect().height"
        ) <= 1
        expect(page.locator(".cadm > .banner")).to_have_count(0)
        nav = page.get_by_test_id("clinic-v8-nav")
        if width == 390:
            page.get_by_test_id("clinic-admin-nav-toggle").press("Enter")
        notice = nav.get_by_test_id("clinic-admin-withheld")
        expect(notice).to_be_visible()
        assert "Aktivität und Audit" in notice.inner_text()
        button = nav.get_by_role("button", name="Benutzer und Rollen", exact=True)
        button.focus()
        button.press("Tab")
        pools = nav.get_by_test_id("clinic-v8-panel-admin-pools")
        expect(pools).to_be_focused()
        pools.press("Enter")
        expect(page.get_by_role("heading", name="Schreibkräfte-Pools", exact=True)).to_be_visible()
        expect(pools).to_have_attribute("aria-current", "page")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.goto(base + "/admin#/aktivitaet")
    denied = page.get_by_test_id("clinic-admin-denied")
    expect(denied).to_be_visible()
    expect(denied).to_have_attribute("role", "status")
    expect(denied).to_contain_text("Aktivität und Audit")
    expect(denied).to_contain_text("nicht freigegeben")
    assert denied.bounding_box()["y"] < height
    assert not errors, errors
