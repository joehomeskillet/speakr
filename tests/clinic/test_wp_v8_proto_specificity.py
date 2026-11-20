"""Verify the V8 element defaults and Tailwind utility overrides in the mounted clinic shell."""

import pytest
from playwright.sync_api import expect
from test_wp_mount_admin import browser
from test_wp_mount_correction import TYPIST, _login, live_server


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width,height,h1_size", [(1440, 900, "30px"), (390, 844, "26px")])
def test_prototype_element_defaults(browser, live_server, width, height, h1_size, theme):
    base, _ = live_server
    page = _login(browser, base, TYPIST)
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("dark => localStorage.setItem('darkMode', dark)", str(theme == "dark").lower())
    page.goto(base + "/#/einstellungen")
    page.reload()
    view = page.locator(".v8-ui .clinic-settings-view")
    expect(view).to_be_visible()
    assert page.locator("html").evaluate("el => el.classList.contains('dark')") == (theme == "dark")

    button = view.locator("[data-testid=clinic-settings-pedal-capture]")
    expect(button).to_have_class("")
    expect(button).to_have_css("padding", "9px 13px")
    expect(button).not_to_have_css("background-color", "rgba(0, 0, 0, 0)")
    button.focus()
    expect(button).to_have_css("outline-style", "solid")
    assert button.evaluate("el => parseFloat(getComputedStyle(el).outlineWidth)") >= 2
    expect(view.get_by_role("heading", level=1)).to_have_css("font-size", h1_size)

    # The settings wrapper supplies heading utilities. Check bare headings against
    # the same loaded stylesheets, independent of those page-specific overrides.
    frame = view.evaluate_handle("""view => {
        const frame = document.createElement('section');
        frame.className = 'v8-page';
        frame.innerHTML = '<h1>Titel</h1><h2>Abschnitt</h2><button class="p-2">Aktion</button>';
        view.after(frame);
        return frame;
    }""")
    assert frame.evaluate("el => getComputedStyle(el.querySelector('h1')).fontSize") == h1_size
    assert frame.evaluate("el => getComputedStyle(el.querySelector('h2')).fontSize") == "16px"
    assert frame.evaluate("el => getComputedStyle(el.querySelector('h2')).fontWeight") == "700"
    assert frame.evaluate("el => getComputedStyle(el.querySelector('button')).padding") == "8px"


@pytest.mark.parametrize("width,height,filter_size", [(1440, 900, "14px"), (390, 844, "16px")])
def test_dark_text_inputs(browser, live_server, width, height, filter_size):
    base, _ = live_server
    page = _login(browser, base, TYPIST)
    try:
        page.set_viewport_size({"width": width, "height": height})
        page.add_init_script("localStorage.setItem('darkMode', 'true')")
        page.goto(base + "/#/arbeitsliste")
        page.reload()
        page.get_by_role("button", name="Erweiterte Filter").click()
        name = page.get_by_label("Name des Filters")
        expect(name).to_be_visible()
        expect(name).to_have_css("font-size", filter_size)
        expect(name).to_have_css("border-top-color", "rgb(204, 216, 211)")
    finally:
        page.context.close()

    with browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark") as context:
        context.add_init_script("localStorage.setItem('darkMode', 'true')")
        page = context.new_page()
        page.goto(base + "/login")
        for field in ("email", "password"):
            control = page.locator(f"input[name={field}]")
            expect(control).to_have_css("background-color", "rgb(28, 61, 66)")
            expect(control).to_have_css("border-top-color", "rgb(204, 216, 211)")
