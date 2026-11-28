"""Exercise the shared clinic menus in the recordings shell and standalone headers."""

import pytest
from playwright.sync_api import expect

import v8_browser_support as support


def test_upstream_header_has_no_v8_markup(clinic_off, world):
    from src.app import app

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(world["user_admin"])
        session["_fresh"] = True
    for path in ("/", "/account", "/admin"):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "clinic-v8.css" not in html
        assert "v8-ui" not in html
        assert 'data-testid="clinic-pages-menu"' not in html


def _contrast(text, background):
    def luminance(color):
        channels = map(int, color.removeprefix("rgb(").removesuffix(")").split(","))
        srgb = [channel / 255 for channel in channels]
        red, green, blue = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb
        ]
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    low, high = sorted((luminance(text.evaluate("(el) => getComputedStyle(el).color")),
                        luminance(background.evaluate("(el) => getComputedStyle(el).backgroundColor"))))
    ratio = (high + 0.05) / (low + 0.05)
    assert ratio >= 4.5, f"{text.inner_text()!r}: {ratio:.2f}:1"


@pytest.mark.parametrize("path,role", [
    ("/", "admin"), ("/account", "admin"), ("/admin", "admin"), ("/", "author"),
])
@pytest.mark.parametrize("size", ["1440x900", "1280x720", "768x1024", "390x844", "320x568"])
@pytest.mark.parametrize("theme", support.THEMES)
def test_header_menus(browser, app_base, role_states, commit, path, role, size, theme):
    width, height = (768, 1024) if size == "768x1024" else support.VIEWPORTS[size]
    context = browser.new_context(viewport={"width": width, "height": height},
                                  storage_state=role_states[role], has_touch=width <= 390, **support.theme_context_options(theme))
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        assert page.goto(app_base + path).ok
        root = page.locator("#app" if path == "/" else "#global-header")
        expect(root).to_have_attribute("data-v-app", "")
        if path == "/":
            support._wait_loading_overlay_gone(page)
        elif width == 320:
            assert root.locator(".page-header__brand h1").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        if path == "/account" and width <= 390:
            tabs = page.locator("#tabs-container > .tab:visible")
            assert tabs.evaluate_all("els => els.every(el => el.scrollWidth <= el.clientWidth + 1)")
        assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")

        stem = f"{commit}__header_{path.strip('/') or 'recordings'}_{role}_{size}_{theme}"
        support.OUT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(support.OUT_DIR / f"{stem}.png"), animations="disabled")
        (support.OUT_DIR / f"{stem}.html").write_text(page.content(), encoding="utf-8")

        details = page.get_by_test_id("clinic-pages-menu")
        trigger = details.locator("summary")
        menu = details.locator(".v6-menu")
        link = menu.get_by_role("link", name="Neue Aufnahme")
        trigger.focus()
        trigger.press("Enter")
        page.keyboard.press("Tab")
        expect(link).to_be_focused()
        _contrast(link, menu)
        _contrast(menu.get_by_role("heading").first, menu)
        link.hover()
        _contrast(link, link)
        page.screenshot(path=str(support.OUT_DIR / f"{stem}_pages.png"), animations="disabled")
        (support.OUT_DIR / f"{stem}_pages.html").write_text(page.content(), encoding="utf-8")
        support.assert_label_floor(page, stem)
        columns = menu.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length")
        assert columns == (1 if width == 320 else 2)
        if width <= 768:
            overflow = support.horizontal_overflow(page)
            assert not (overflow["document"] or overflow["body"]), overflow
            links = menu.get_by_role("link").all()
            for target in [trigger, *links]:
                target.scroll_into_view_if_needed()
                bounds = target.bounding_box()
                assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                if width <= 390:
                    support.assert_touch_target(target)
            if width == 320:
                for target in links:
                    assert target.evaluate("""el => {
                        const text = document.createRange();
                        text.selectNodeContents(el);
                        return text.getClientRects().length === 1;
                    }"""), target.inner_text()
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        expect(menu).to_be_visible()
        page.mouse.click(5, height // 2)
        expect(menu).to_be_hidden()

        avatar = root.locator("[data-user-menu-toggle]")
        avatar.click()
        account = root.locator("[data-user-menu-dropdown]")
        expect(account).to_be_visible()
        if size == "1440x900":
            bounds = avatar.bounding_box()
            menu_bounds = account.bounding_box()
            assert menu_bounds["x"] + menu_bounds["width"] == pytest.approx(bounds["x"] + bounds["width"], abs=1)

        for item in (account.locator("[data-notifications-toggle]"), account.locator('a[href="/account"]')):
            item.hover()
            _contrast(item.locator("span").first, item)
        page.screenshot(path=str(support.OUT_DIR / f"{stem}_account.png"), animations="disabled")
        (support.OUT_DIR / f"{stem}_account.html").write_text(page.content(), encoding="utf-8")

        support.assert_label_floor(page, stem + "_account")
        if width <= 390:
            support.assert_phone_control_floor(page, stem)
            for target in account.locator(":is(a,button):visible").all():
                support.assert_touch_target(target)
        if path == "/":
            trigger.click()
            if width <= 390:
                link.tap()
            else:
                link.click()
            expect(page).to_have_url(f"{app_base}/#/neu")
            expect(menu).to_be_hidden()
        assert not errors, errors
    finally:
        context.close()
