"""The vocabulary list selector respects real author membership on both entry points."""

import pytest
from playwright.sync_api import expect

from v8_browser_support import _show_resources


@pytest.mark.parametrize("role", ["author", "admin"])
def test_vocabulary_lists_are_named_and_membership_scoped(browser, app_base, role_states, role):
    context = browser.new_context(storage_state=role_states[role])
    try:
        page = context.new_page()
        with page.expect_response(lambda response: response.url.endswith("/api/clinic/v1/pools")) as pools:
            if role == "admin":
                page.goto(app_base + "/admin#/wortschatz")
            else:
                _show_resources(page, app_base)
        assert pools.value.ok
        catalog = pools.value.json()["author_pools"]
        assert any(pool["name"] == "Autoren V860" for pool in catalog)
        picker = page.get_by_label("Wortliste", exact=True)
        for field_id in ("voc-pool", "voc-q", "voc-mode", "voc-filter"):
            label_box = page.locator(f'label[for="{field_id}"]').bounding_box()
            input_box = page.locator(f"#{field_id}").bounding_box()
            assert label_box["y"] + label_box["height"] <= input_box["y"] + 1
        if role == "admin":
            expect(page.get_by_text("Keine zugeordneten Autorenpools.")).to_be_visible()
            expect(picker.locator("option")).to_have_text(["Persönlich"])
        else:
            expect(picker.locator("option")).to_have_text(["Persönlich", "Autoren V860"])
            pool = next(pool for pool in catalog if pool["name"] == "Autoren V860")
            with page.expect_response(lambda res: "/vocabulary/search?" in res.url and "scope=pool" in res.url) as search:
                picker.select_option(label=pool["name"])
            assert search.value.ok and pool["id"] in search.value.url
            expect(page.locator(".vocabulary-ui strong").first).to_have_text(pool["name"])
            expect(page.locator(".vocabulary-ui")).not_to_contain_text(pool["id"])
            with page.expect_response(lambda res: "/vocabulary/search?" in res.url and "scope=user" in res.url) as own:
                picker.select_option(label="Persönlich")
            assert own.value.ok
    finally:
        context.close()
