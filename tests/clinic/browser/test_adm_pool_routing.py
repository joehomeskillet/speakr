"""PAR-ADM-007: an admin connects author pool A to an existing staff pool and sees the saved link after a reload.

Runs against the real Flask app with synthetic users (see v8_browser_support.SERVER). The staff pool
"Schreibdienst V860" already exists there. Set P116_ARTIFACTS to a directory to also save screenshots.
"""

import os
from pathlib import Path

from playwright.sync_api import expect

import v8_browser_support as support

POOL_A = "Pool A P116"
STAFF = "Schreibdienst V860"
SHOTS = os.environ.get("P116_ARTIFACTS")


def _card(view, name):
    return view.get_by_test_id("author-pool-cards").get_by_role("region", name=name, exact=True)


def _shoot(browser, state, base, label):
    if not SHOTS:
        return
    out = Path(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            context = browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                          **support.theme_context_options(theme))
            try:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                page.goto(base + "/admin#/diktierende")
                expect(page.get_by_test_id("author-pool-cards")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
                page.get_by_test_id("author-pool-cards").scroll_into_view_if_needed()
                page.screenshot(path=str(out / f"routing-{label}-{size}-{theme}.png"), full_page=True)
            finally:
                context.close()


def test_admin_links_author_pool_to_staff_pool(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states["admin"], viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        page.goto(app_base + "/admin#/diktierende")
        view = page.get_by_test_id("clinic-authors-view")
        expect(view.get_by_test_id("author-pool-create")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)

        view.get_by_label("Name des neuen Autorenpools").fill(POOL_A)
        with page.expect_response(lambda r: r.url.endswith("/pool-admin/pools") and r.request.method == "POST") as made:
            view.get_by_role("button", name="Anlegen", exact=True).click()
        assert made.value.status == 201
        # No link yet, whatever the memberships are.
        routes = _card(view, POOL_A).get_by_test_id("author-pool-routes")
        expect(routes).to_have_text("Kein Pool zugewiesen")

        card = _card(view, POOL_A)
        card.get_by_label("Schreibpool verbinden").select_option(label=STAFF)
        with page.expect_response(lambda r: r.url.endswith("/links") and r.request.method == "POST") as sent:
            card.get_by_role("button", name="Verbinden", exact=True).click()
        assert sent.value.status == 201
        expect(view.get_by_test_id("author-pool-link-message")).to_contain_text("Verbunden.")
        expect(_card(view, POOL_A).get_by_test_id("author-pool-routes")).to_have_text(STAFF)

        page.reload()
        view = page.get_by_test_id("clinic-authors-view")
        expect(_card(view, POOL_A).get_by_test_id("author-pool-routes")).to_have_text(STAFF, timeout=support.SHOW_TIMEOUT_MS)
        # The linked pool is no longer offered again.
        expect(_card(view, POOL_A).get_by_label("Schreibpool verbinden")).to_be_disabled()

        _shoot(browser, role_states["admin"], app_base, "rows")
    finally:
        context.close()
