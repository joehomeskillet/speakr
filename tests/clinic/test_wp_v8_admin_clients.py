"""Real admin mount regression for the P11 client and directory access."""

from test_wp_mount_admin import ADMIN_EMAIL, _login, browser, live_server

_FIXTURES = (browser, live_server)


def test_site_admin_loads_authors_and_sees_why_directory_review_is_hidden(live_server, browser):
    base, ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    requests = []
    page.on("request", lambda request: requests.append(request.url.removeprefix(base)))
    page.goto(base + "/admin")
    page.get_by_test_id("admin-tab-clinic").click()
    page.get_by_test_id("clinic-v8-panel-authors").click()

    authors = page.get_by_test_id("clinic-v8-view-authors")
    row = authors.locator(f'[data-user-id="{ids["typist"]}"]')
    row.get_by_text("w226typist", exact=True).wait_for()
    assert row.get_by_text("Pool 226", exact=True).is_visible()
    assert "/admin/users" in requests
    assert "/api/clinic/v1/pools" in requests
    assert "/api/clinic/v1/admin/users" not in requests

    page.get_by_test_id("clinic-panel-directory").click()
    directory = page.get_by_test_id("clinic-view-directory")
    directory.get_by_text("Der Verzeichnisabgleich erfordert Rechte zur Identitätsverwaltung.").wait_for()
    assert directory.get_by_role("button", name="Vorschau laden").count() == 0
    assert "/api/clinic/v1/directory/preview" not in requests
    directory.get_by_role("button", name="Lokale Konten verwalten").click()
    page.get_by_test_id("clinic-view-accounts").wait_for()
    assert not js_errors, js_errors
