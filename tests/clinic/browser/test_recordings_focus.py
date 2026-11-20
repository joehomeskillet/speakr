"""Returning to recordings must not steal focus while the user is activating a row."""

from playwright.sync_api import expect


def test_return_to_recordings_keeps_the_users_keyboard_focus(browser, app_base, role_states):
    context = browser.new_context(storage_state=role_states["author"], viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        page.goto(app_base + "/#/aufnahmen")
        row = page.get_by_test_id("clinic-recordings-view").locator("[data-row]").first
        row.click()
        editor = page.get_by_test_id("clinic-correction-view")
        expect(editor).to_be_visible()
        page.get_by_test_id("clinic-correction-back").click()
        row.focus()
        # A physical space press activates a button on keyup, provided focus stays on it.
        page.keyboard.press("Space", delay=150)
        expect(editor).to_be_visible(timeout=2000)
    finally:
        context.close()
