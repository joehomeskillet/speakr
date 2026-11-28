"""Recipient choice is administered in Fachrechte, not in personal settings."""

from pathlib import Path

from playwright.sync_api import expect
import pytest

from v8_browser_support import ROLES, SERVER, login, serve_app, theme_context_options, theme_init_script

ARTIFACTS = Path(__file__).resolve().parents[3] / ".wp" / "artifacts"
DEFAULT_URL = "/api/clinic/v1/preferences/recipient-default"
SEED = '''
    from src.clinic import recipient_defaults
    from src.clinic.routes_grants import _apply_recipient_choice
    author.username = "Alpha"
    typist.username = "Schreib1"
    group.name = "Schreibdienst Bern"
    pools.allow(author.id, "own", actor_id=admin.id, actions=("play",))
    recipient_defaults.set_default(author.id, "staff_pool", target_ids=[staff.id],
                                   actor_id=author.id, expected_version=0)
    _apply_recipient_choice(author.id, "open", admin.id)
    _apply_recipient_choice(lead.id, "default_only", admin.id)
'''


def seeded_app(tmp_path, browser, roles):
    source = SERVER.replace('server = make_server', SEED + 'server = make_server', 1)
    with serve_app(tmp_path, source) as base:
        yield base, {role: login(browser, base, ROLES[role]) for role in roles}


@pytest.fixture
def recipient_app(tmp_path, browser):
    yield from seeded_app(tmp_path, browser, ("admin", "author"))


@pytest.fixture(scope="module")
def visual_app(tmp_path_factory, browser):
    yield from seeded_app(tmp_path_factory.mktemp("visual-recipient-choices"), browser, ("admin", "author", "lead"))


def open_grants(page, base, user="Alpha"):
    page.goto(base + "/admin")
    page.get_by_role("button", name="Benutzer und Rollen", exact=True).click()
    users = page.get_by_test_id("clinic-users-view")
    users.get_by_role("button", name="Fachrechte", exact=True).click()
    view = users.get_by_role("region", name="Fachrechte", exact=True)
    user_id, = view.get_by_label("Benutzer", exact=True).select_option(label=user)
    return view, f"{base}/api/clinic/v1/identity/users/{user_id}/grants"


def set_rule(page, view, endpoint, value):
    select = view.get_by_label("Empfängerwahl beim Versand")
    expect(select).to_be_enabled()
    before = page.request.get(endpoint).json()
    select.select_option(value)
    assert page.request.get(endpoint).json() == before
    with page.expect_response(lambda res: res.url.endswith(endpoint) and res.request.method == "PUT") as saved:
        view.get_by_role("button", name="Empfängerwahl übernehmen").click()
    assert saved.value.status == 200
    assert saved.value.request.post_data_json == {"expected_revision": before["revision"], "recipient_choice": value}
    expect(view.get_by_role("status").filter(has_text="Empfängerwahl gespeichert.")).to_be_visible()
    assert saved.value.json()["items"] == before["items"]


def open_settings(page, base):
    page.goto(base + "/#/einstellungen")
    return page.get_by_test_id("clinic-preferences")


def test_admin_recipient_rule_survives_reload_and_limits_alpha(browser, recipient_app):
    base, states = recipient_app
    admin = browser.new_context(storage_state=states["admin"])
    author = browser.new_context(storage_state=states["author"])
    try:
        admin_page, author_page = admin.new_page(), author.new_page()
        view, grants = open_grants(admin_page, base)
        set_rule(admin_page, view, grants, "default_only")
        preferences = open_settings(author_page, base)
        limit = preferences.get_by_test_id("clinic-preferences-recipient-limit")
        expect(limit).to_contain_text("Nur der Standardempfänger")
        expect(preferences.get_by_test_id("clinic-preferences-saved-default")).to_have_text("Schreibdienst Bern")
        expect(preferences.get_by_role("radio")).to_have_count(0)
        expect(preferences.get_by_test_id("clinic-preferences-target")).to_have_count(0)

        set_rule(admin_page, view, grants, "staff_pools_only")
        author_page.reload()
        expect(limit).to_contain_text("Nur Schreibteams")
        expect(preferences.get_by_test_id("clinic-preferences-mode-person")).to_have_count(0)
        expect(preferences.locator('input[name="clinic-preferences-choice"]')).to_have_count(0)
        target = preferences.get_by_test_id("clinic-preferences-target")
        expect(target.locator("option")).to_have_text(["Bitte wählen", "Schreibdienst Bern"])
        expect(target.locator("option:checked")).to_have_text("Schreibdienst Bern")
        preferences.get_by_test_id("clinic-preferences-mode-self_correction").check()
        with author_page.expect_response(lambda res: res.url.endswith(DEFAULT_URL) and res.request.method == "PUT") as saved:
            preferences.get_by_test_id("clinic-preferences-save").click()
        assert saved.value.status == 200
        assert "choice" not in saved.value.request.post_data_json
        expect(preferences.get_by_test_id("clinic-preferences-notice")).to_have_text("Gespeichert.")
        author_page.reload()
        expect(limit).to_contain_text("Nur Schreibteams")
        expect(preferences.get_by_test_id("clinic-preferences-mode-self_correction")).to_be_checked()
        assert author.request.get(grants).json()["recipient_choice"] == "staff_pools_only"

        set_rule(admin_page, view, grants, "open")
        author_page.reload()
        expect(limit).to_contain_text("Personen und Schreibteams")
        preferences.get_by_test_id("clinic-preferences-mode-person").check()
        expect(target.locator("option")).to_contain_text(["Bitte wählen", "Schreib1", "lead-v860"])
    finally:
        author.close()
        admin.close()


@pytest.mark.parametrize("theme", ("light", "dark"))
@pytest.mark.parametrize("width,height", ((1440, 900), (1280, 720), (390, 844)))
def test_recipient_choice_visuals(browser, visual_app, theme, width, height):
    base, states = visual_app
    for role in ("admin", "author", "lead"):
        context = browser.new_context(storage_state=states[role], viewport={"width": width, "height": height},
                                      **theme_context_options(theme))
        context.add_init_script(theme_init_script(theme))
        try:
            page = context.new_page()
            if role == "admin":
                panel, _ = open_grants(page, base)
                expect(panel.get_by_label("Empfängerwahl beim Versand")).to_have_value("open")
                expect(panel.get_by_text("Keine Einträge", exact=True)).to_have_count(0)
                capture(page, panel, role, "rows", theme, width, height)
                panel.get_by_label("Benutzer", exact=True).select_option(label="lead-v860")
                expect(panel.get_by_text("Keine Einträge", exact=True)).to_be_visible()
                capture(page, panel, role, "empty", theme, width, height)
            elif role == "author":
                panel = open_settings(page, base)
                expect(panel.get_by_test_id("clinic-preferences-recipient-limit")).to_contain_text("Personen und Schreibteams")
                expect(panel.get_by_test_id("clinic-preferences-target").locator("option:checked")).to_have_text("Schreibdienst Bern")
                capture(page, panel, role, "rows", theme, width, height)
            else:
                panel = open_settings(page, base)
                expect(panel.get_by_test_id("clinic-preferences-saved-default")).to_have_text("Noch kein Standardempfänger gespeichert.")
                capture(page, panel, "author", "empty", theme, width, height)
        finally:
            context.close()


def capture(page, panel, role, state, theme, width, height):
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
    assert page.locator('html').evaluate("root => root.classList.contains('dark')") == (theme == "dark")
    panel.scroll_into_view_if_needed()
    stem = ARTIFACTS / f"p119-{role}-{state}-{width}x{height}-{theme}"
    page.screenshot(path=stem.with_suffix(".png"), animations="disabled")
    if width == 1440 and theme == "light":
        html = panel.evaluate('''panel => {
            const root = document.documentElement.cloneNode(true);
            root.querySelectorAll('script, link:not([rel="stylesheet"])').forEach(node => node.remove());
            root.querySelectorAll('link[href^="/static/"]').forEach(link => {
                link.setAttribute('href', '../..' + link.getAttribute('href'));
            });
            const frame = document.createElement('main');
            frame.className = panel.closest('.cadm') ? 'cadm v8-admin-page' : 'v8-page clinic-settings-view';
            frame.style.padding = 'var(--sp-4)';
            const snapshot = panel.cloneNode(true);
            panel.querySelectorAll('select').forEach((select, index) => {
                const copy = snapshot.querySelectorAll('select')[index];
                Array.from(copy.options).forEach((option, i) => option.toggleAttribute('selected', i === select.selectedIndex));
            });
            frame.append(snapshot);
            root.querySelector('body').replaceChildren(frame);
            return '<!doctype html>' + root.outerHTML;
        }''')
        stem.with_suffix(".html").write_text(html, encoding="utf-8")
