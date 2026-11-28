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
                assert page.locator("html").evaluate("(el) => el.classList.contains('dark')") == (theme == "dark")
                page.screenshot(path=str(out / f"authors-{label}-{size}-{theme}.png"), full_page=True)
                if label == "rows":
                    table = page.get_by_role("table", name="Diktierende")
                    table.evaluate("el => el.scrollIntoView({block: 'start'})")
                    page.screenshot(path=str(out / f"authors-{label}-table-{size}-{theme}.png"))
                if size == "1440x900":
                    snapshot = page.evaluate("""staticPath => {
                        const root = document.documentElement.cloneNode(true);
                        root.querySelectorAll('script, meta[name=csrf-token]').forEach(node => node.remove());
                        for (const node of root.querySelectorAll('[href], [src]')) {
                            const attr = node.hasAttribute('href') ? 'href' : 'src';
                            const url = new URL(node.getAttribute(attr), location.href);
                            if (url.pathname.startsWith('/static/')) {
                                node.setAttribute(attr, staticPath + url.pathname.slice(7));
                            }
                        }
                        for (const node of root.querySelectorAll('[srcset]')) {
                            node.setAttribute('srcset', node.getAttribute('srcset').replaceAll('/static/', staticPath + '/'));
                        }
                        return '<!doctype html>\\n' + root.outerHTML;
                    }""", (Path(__file__).resolve().parents[3] / "static").as_uri())
                    (out / f"authors-{label}-{theme}.html").write_text(snapshot, encoding="utf-8")
                page.get_by_test_id("author-pool-cards").scroll_into_view_if_needed()
                page.screenshot(path=str(out / f"authors-{label}-cards-{size}-{theme}.png"))
            finally:
                context.close()


def _check_phone_rows(browser, base, state, width):
    context = browser.new_context(storage_state=state, viewport={"width": width, "height": 844}, has_touch=True)
    try:
        page = context.new_page()
        page.goto(base + "/admin#/diktierende")
        table = page.get_by_role("table", name="Diktierende")
        alpha = table.get_by_role("row").filter(has=page.get_by_role("rowheader", name=ALPHA))
        expect(alpha.get_by_text("Diktierrolle", exact=True)).to_be_visible()
        expect(alpha.get_by_text("Diktierend", exact=True)).to_be_visible()
        expect(alpha.get_by_text("Aktiv", exact=True)).to_be_visible()
        expect(alpha.get_by_text("Kein Pool zugewiesen", exact=True)).to_be_visible()
        assert table.evaluate("el => el.getBoundingClientRect().right <= innerWidth && el.scrollWidth <= el.clientWidth")
        assert alpha.get_by_role("cell").evaluate_all("cells => cells.every(cell => getComputedStyle(cell, '::before').content === 'none')")
        action_a = alpha.get_by_role("button", name=f"Zum Autorenpool: {POOL_A}")
        action_b = alpha.get_by_role("button", name=f"Zum Autorenpool: {POOL_B}")
        for button in (action_a, action_b):
            bounds = button.bounding_box()
            assert bounds["width"] >= 48 and bounds["height"] >= 48
        search = page.get_by_role("searchbox", name="Suche")
        search.focus()
        for next_control in (
            page.get_by_role("combobox", name="Autorenpools"),
            page.get_by_role("button", name="Filter zurücksetzen"),
            table.locator(".."),
            alpha.get_by_role("button", name="Zum Autorenpool: Autoren V860"),
            action_a,
        ):
            page.keyboard.press("Tab")
            expect(next_control).to_be_focused()
        expect(action_a).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name=POOL_A, exact=True)).to_be_focused()
        action_b.tap()
        expect(page.get_by_role("heading", name=POOL_B, exact=True)).to_be_focused()
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

        table = view.get_by_role("table", name="Diktierende")
        expect(table.get_by_role("columnheader")).to_have_text([
            "Anzeigename", "Autorenpools", "Schreibkräfte-Pools", "Diktierrolle", "Kontostatus",
        ])
        expect(table.get_by_text("Diktierrolle", exact=True).last).to_be_hidden()
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

        for width in (375, 390):
            _check_phone_rows(browser, app_base, role_states["admin"], width)
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
