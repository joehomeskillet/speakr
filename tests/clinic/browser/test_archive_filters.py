"""The archive author selector leaves its dropdown arrow clear on a phone."""

import pytest
from playwright.sync_api import expect

import v8_browser_support as support


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_archive_author_filter_label_fits_beside_arrow(browser, app_base, role_states, theme):
    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  storage_state=role_states["author"], **support.theme_context_options(theme))
    try:
        context.add_init_script(support.theme_init_script(theme))
        page = context.new_page()
        page.v8_notes = []
        archive = next(entry for entry in support.PAGES if entry.page_id == "P08")
        archive.show(page, app_base)
        author = page.get_by_label("Diktiert von", exact=True)
        expect(author).to_be_visible()
        expect(author.locator("option:checked")).to_have_text("Alle Diktierenden")
        page.evaluate("document.fonts.ready")
        available, label, arrow_space = author.evaluate("""select => {
            const style = getComputedStyle(select);
            const canvas = document.createElement("canvas").getContext("2d");
            canvas.font = style.font;
            const usable = select.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
            return [usable, canvas.measureText(select.selectedOptions[0].text).width, parseFloat(style.paddingRight)];
        }""")
        assert arrow_space >= 24
        assert label <= available
    finally:
        context.close()
