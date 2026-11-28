"""PAR-ADM-011: use the real login, session and recordings list with synthetic accounts."""

import json
import os
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect

import v8_browser_support as support

BETA = "beta-p151"
ALPHA = "author-v860"
PREF = "/api/clinic/v1/preferences/last-selection"
FOREIGN_HINT = "Die Auswahl aus dem Startlink ist nicht verfügbar. Die Gruppe „Diktiergruppe Beta“ bleibt ausgewählt."
SERVER = support.SERVER.replace(
    'server = make_server(',
    '''    beta = user("beta-p151", "beta-p151@local.test")
    beta_pool = ClinicAuthorPool(name="Diktiergruppe Beta", created_by=admin.id)
    db.session.add(beta_pool)
    db.session.commit()
    pools.add_author_member(beta_pool.id, beta.id, actor_id=admin.id)
    from pathlib import Path
    from src.clinic.last_selection import PresetUserPreferences, Selection, SelectionStore
    SelectionStore(preferences=PresetUserPreferences()).remember(beta.id, Selection("author", beta.id, beta_pool.id))
    Path(app.config["UPLOAD_FOLDER"]).parent.joinpath("pools.json").write_text(json.dumps({
        "alpha": pool.id, "beta": beta_pool.id,
    }))
server = make_server(''',
    1,
)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p151")
    with support.serve_app(tmp, SERVER) as base:
        yield base, json.loads((tmp / "pools.json").read_text())


def start_url(base, user, pool, path="/"):
    return f"{base}{path}?{urlencode({'user': user, 'pool': pool})}"


def sign_in(page, user=None, password=support.PASSWORD):
    if user is not None:
        page.locator("#email").fill(user)
    page.locator("#password").fill(password)
    page.locator("form:has(#password) [type=submit]").click()


def sign_out(page, base):
    csrf = page.request.get(base + "/api/csrf-token").json()["csrf_token"]
    assert page.request.post(base + "/logout", form={"csrf_token": csrf}).ok
    page.goto(base + "/login")


def test_remembered_account_and_link_still_require_password(browser, world):
    base, pools = world
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(base + "/login")
        expect(page.locator("#email")).to_have_value("")
        sign_in(page, BETA)
        expect(page.get_by_test_id("clinic-recordings-view")).to_be_visible()
        expect(page.get_by_test_id("filter-pool")).to_have_value("Diktiergruppe Beta")
        sign_out(page, base)
        expect(page.locator("#email")).to_have_value(BETA)
        expect(page.locator("#password")).to_have_value("")
        assert page.request.get(base + PREF).status == 401

        page.goto(start_url(base, ALPHA, pools["alpha"]))
        expect(page.locator("#email")).to_have_value(ALPHA)
        sign_in(page, password="Incorrect-synthetic-password")
        expect(page.get_by_role("alert")).to_contain_text("Anmeldung fehlgeschlagen")
        assert page.request.get(base + PREF).status == 401
        sign_in(page)
        expect(page.get_by_test_id("start-selection-status")).to_have_text("Gruppe „Autoren V860“ aus dem Startlink ausgewählt.")
        expect(page.get_by_test_id("filter-pool")).to_have_value("Autoren V860")
        expect(page.get_by_test_id("open-link")).to_have_count(1)
        assert "?" not in page.url
        assert page.request.get(base + PREF).json()["selection"]["pool_id"] is None


def test_foreign_link_keeps_session_and_saved_selection(browser, world):
    base, pools = world
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(base + "/login")
        sign_in(page, BETA)
        expect(page.get_by_test_id("clinic-recordings-view")).to_be_visible()
        actor = page.locator("#app").get_attribute("data-current-user-id")
        page.goto(start_url(base, ALPHA, pools["alpha"]))
        expect(page.get_by_test_id("start-selection-status")).to_have_text(FOREIGN_HINT)
        expect(page.get_by_test_id("filter-pool")).to_have_value("Diktiergruppe Beta")
        expect(page.get_by_test_id("empty-state")).to_be_visible()
        expect(page.locator("#app")).to_have_attribute("data-current-user-id", actor)
        assert "?" not in page.url
        assert page.request.get(base + PREF).json()["selection"]["pool_id"] == pools["beta"]

        sign_out(page, base)
        page.goto(start_url(base, ALPHA, pools["alpha"], "/login"))
        expect(page.locator("#email")).to_have_value(ALPHA)
        sign_in(page, BETA)
        expect(page.locator("#app")).to_have_attribute("data-current-user-id", actor)
        expect(page.get_by_test_id("start-selection-status")).to_have_text(FOREIGN_HINT)


def capture(page, stem, out_dir):
    page.wait_for_function("!document.querySelector('html.app-loading, body.app-loading, .app-loading-overlay')")
    page.evaluate("document.fonts.ready")
    assert not support.horizontal_overflow(page)["document"]
    assert page.evaluate("document.documentElement.classList.contains('dark')") == stem.endswith("-dark")
    out_dir.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=out_dir / f"{stem}.png", full_page=True, animations="disabled")
    # Retain the live DOM and CSS for offline rendering. Strip scripts and session tokens.
    html = page.evaluate("""assetRoot => {
        const root = document.documentElement.cloneNode(true);
        root.querySelectorAll('script, [name=csrf_token], meta[name=csrf-token]').forEach(node => node.remove());
        for (const node of root.querySelectorAll('link[href^="/static/"], [src^="/static/"]')) {
            const attr = node.hasAttribute('src') ? 'src' : 'href';
            node.setAttribute(attr, assetRoot + node.getAttribute(attr));
        }
        for (const input of document.querySelectorAll('input:not([type=password])[id]')) {
            root.querySelector('#' + CSS.escape(input.id))?.setAttribute('value', input.value);
        }
        return '<!doctype html>\\n' + root.outerHTML;
    }""", os.path.relpath(support.ROOT, out_dir))
    (out_dir / f"{stem}.html").write_text(html, encoding="utf-8")


@pytest.mark.parametrize("size", support.VIEWPORTS)
@pytest.mark.parametrize("theme", support.THEMES)
def test_start_hints_visual_matrix(browser, world, size, theme, tmp_path):
    base, pools = world
    out_dir = support.OUT_DIR if os.environ.get("V8_BROWSER_OUT") else tmp_path
    width, height = support.VIEWPORTS[size]
    with browser.new_context(viewport={"width": width, "height": height},
                             **support.theme_context_options(theme)) as context:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        page.goto(base + "/login")
        expect(page.locator("#email")).to_have_value("")
        for control in ("#email", "#password", "[data-password-toggle]", "form:has(#password) [type=submit]"):
            box = page.locator(control).bounding_box()
            assert box and box["width"] >= 48 and box["height"] >= 48, (control, box)
        capture(page, f"login-empty-{size}-{theme}", out_dir)
        page.goto(start_url(base, ALPHA, pools["alpha"], "/login"))
        expect(page.locator("#clinic-login-hint")).to_be_visible()
        capture(page, f"login-hint-{size}-{theme}", out_dir)
        sign_in(page)
        expect(page.get_by_test_id("start-selection-status")).to_contain_text("aus dem Startlink ausgewählt.")
        expect(page.get_by_test_id("open-link")).to_be_visible()
        capture(page, f"start-rows-{size}-{theme}", out_dir)
        sign_out(page, base)
        sign_in(page, BETA)
        expect(page.get_by_test_id("empty-state")).to_be_visible()
        page.goto(start_url(base, ALPHA, pools["alpha"]))
        expect(page.get_by_test_id("start-selection-status")).to_have_text(FOREIGN_HINT)
        expect(page.get_by_test_id("empty-state")).to_be_visible()
        for control in (page.get_by_test_id("reset"), page.get_by_role("button", name="Filter zurücksetzen")):
            box = control.bounding_box()
            assert box and box["width"] >= 48 and box["height"] >= 48, box
        capture(page, f"start-empty-{size}-{theme}", out_dir)
