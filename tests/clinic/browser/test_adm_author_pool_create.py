"""PAR-ADM-002: an admin creates author pools A and B and assigns members in the authors view.

Runs against the real Flask app with synthetic users (see v8_browser_support.SERVER). Set
P097_ARTIFACTS to a directory to also save screenshots (3 viewports, light and dark, rows and empty state).
"""

import os
from pathlib import Path

from playwright.sync_api import expect

import v8_browser_support as support

POOL_A, POOL_B = "Pool A P097", "Pool B P097"
ALPHA, BETA = "author-v860", "typist-v860"
SHOTS = os.environ.get("P097_ARTIFACTS")


def _create(view, name):
    view.get_by_label("Name des neuen Autorenpools").fill(name)
    with view.page.expect_response(lambda r: r.url.endswith("/pool-admin/pools") and r.request.method == "POST") as sent:
        view.get_by_role("button", name="Anlegen", exact=True).click()
    return sent.value


def _assign(view, pool, user):
    card = view.get_by_test_id("author-pool-cards").get_by_role("region", name=pool, exact=True)
    card.get_by_label("Benutzer zuordnen").select_option(label=user)
    with view.page.expect_response(lambda r: "/pools/author/" in r.url and r.request.method == "PUT") as sent:
        card.get_by_role("button", name="Zuordnen", exact=True).click()
    assert sent.value.ok
    return card


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
                expect(page.get_by_test_id("author-pool-create")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
                page.screenshot(path=str(out / f"authors-{label}-{size}-{theme}.png"), full_page=True)
                page.get_by_test_id("author-pool-cards").scroll_into_view_if_needed()
                page.screenshot(path=str(out / f"authors-{label}-cards-{size}-{theme}.png"))
            finally:
                context.close()


def test_admin_creates_pools_and_assigns_members(browser, tmp_path):
    # Own server: this test puts the shared author into two unlinked pools, which would
    # leave that author without a routable pool for every later test on the shared app_base.
    with support.serve_app(tmp_path, support.SERVER) as app_base:
        _admin_creates_pools_and_assigns_members(browser, app_base, {"admin": support.login(browser, app_base, support.ROLES["admin"])})


def _admin_creates_pools_and_assigns_members(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states["admin"], viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        page.goto(app_base + "/admin#/diktierende")
        view = page.get_by_test_id("clinic-authors-view")
        expect(view.get_by_test_id("author-pool-create")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)

        for name in (POOL_A, POOL_B):
            assert _create(view, name).status == 201
            expect(view.get_by_test_id("author-pool-create-message")).to_contain_text(name)
            expect(view.get_by_test_id("author-pool-cards").get_by_role("region", name=name, exact=True)).to_be_visible()

        # Alpha belongs to both pools, Beta only to A.
        _assign(view, POOL_A, ALPHA)
        _assign(view, POOL_B, ALPHA)
        _assign(view, POOL_A, BETA)

        page.reload()
        view = page.get_by_test_id("clinic-authors-view")
        cards = view.get_by_test_id("author-pool-cards")
        members_a = cards.get_by_role("region", name=POOL_A, exact=True).locator("dd").first
        members_b = cards.get_by_role("region", name=POOL_B, exact=True).locator("dd").first
        expect(members_a).to_have_text(f"{ALPHA}, {BETA}")
        expect(members_b).to_have_text(ALPHA)

        alpha_row = view.get_by_role("table").locator(f"tr[data-user-id]:has(strong:text-is('{ALPHA}'))")
        expect(alpha_row).to_contain_text(POOL_A)
        expect(alpha_row).to_contain_text(POOL_B)
        expect(alpha_row).to_contain_text("Mehrere Autorenpools")
        expect(view.get_by_role("group").filter(has_text="mehreren Autorenpools angehören")).to_be_visible()
        beta_row = view.get_by_role("table").locator(f"tr[data-user-id]:has(strong:text-is('{BETA}'))")
        expect(beta_row).to_contain_text(POOL_A)
        expect(beta_row).not_to_contain_text(POOL_B)
        expect(beta_row).not_to_contain_text("Mehrere Autorenpools")

        # A duplicate name shows the server conflict and creates nothing.
        assert _create(view, POOL_A).status == 409
        message = view.get_by_test_id("author-pool-create-message")
        expect(message).to_contain_text("Konflikt (Server)")
        expect(message).to_have_attribute("role", "alert")
        expect(cards.get_by_role("region", name=POOL_A, exact=True)).to_have_count(1)

        _shoot(browser, role_states["admin"], app_base, "rows")
    finally:
        context.close()


def test_empty_state_has_create_form(browser, tmp_path):
    source = support.SERVER.replace("pools.add_author_member(pool.id, author.id, actor_id=admin.id)", "pass")
    assert source != support.SERVER
    with support.serve_app(tmp_path, source) as base:
        state = support.login(browser, base, support.ROLES["admin"])
        context = browser.new_context(storage_state=state)
        try:
            page = context.new_page()
            page.goto(base + "/admin#/diktierende")
            view = page.get_by_test_id("clinic-authors-view")
            expect(view.get_by_test_id("author-pool-create")).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
            expect(view.get_by_text("Keine Diktierenden vorhanden")).to_be_visible()
            assert _create(view, POOL_A).status == 201
            expect(view.get_by_test_id("author-pool-cards").get_by_role("region", name=POOL_A, exact=True)).to_be_visible()
            _shoot(browser, state, base, "empty")
        finally:
            context.close()
