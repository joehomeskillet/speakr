"""Open real recordings from the body of the desktop row and the phone card."""

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_recording_surface_opens_its_dictation(browser, app_base, role_states, width, height):
    mobile = width < 768
    context = browser.new_context(
        storage_state=role_states["author"], viewport={"width": width, "height": height}, has_touch=mobile,
    )
    try:
        page = context.new_page()
        page.goto(app_base + "/#/aufnahmen")
        listing = page.get_by_test_id("clinic-recordings-view")
        row = listing.locator("[data-card]:visible" if mobile else "[data-row]:visible").first
        expect(row).to_be_visible()
        dictation = row.get_attribute("data-id")
        assert dictation
        source = page.request.get(app_base + "/api/clinic/v1/recordings")
        assert source.ok
        assert dictation in {str(item["dictation_id"]) for item in source.json()["items"]}
        cell = row.get_by_text("author-v860", exact=True)
        if mobile:
            cell.tap()
        else:
            cell.click()
        editor = page.get_by_test_id("clinic-correction-view")
        expect(editor).to_be_visible(timeout=5000)
        assert page.evaluate("history.state.dictationId") == dictation
        page.get_by_test_id("clinic-correction-back").click()
        expect(row).to_be_visible()
        title = row.locator(".record-title")
        title.click()
        expect(editor).to_be_visible()
        page.get_by_test_id("clinic-correction-back").click()
        expect(row).to_have_role("button")
        row.focus()
        row.press("Enter")
        expect(editor).to_be_visible()
        page.go_back()
        expect(row).to_be_visible()
        row.focus()
        row.press("Space")
        expect(editor).to_be_visible()
    finally:
        context.close()
