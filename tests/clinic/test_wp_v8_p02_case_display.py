"""D1. The synthetic case bound through the authorized endpoint reaches the mounted P02 row."""
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_mount_correction import live_server  # noqa: F401
from test_wp_v8_130_release_browser import _headers, review_scene  # noqa: F401


def test_bound_case_in_worklist_heading(review_scene):
    base, did, _, _ = review_scene
    path = f"{base}/api/clinic/v1/dictations/{did}"
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = mount._login(browser, base, mount.TYPIST)
            acquired = page.request.post(path + "/leases", data={}, headers=_headers(page, base))
            assert acquired.ok, acquired.status
            bound = page.request.post(path + "/context", data={"case_reference": "SYN-0492"},
                                      headers=_headers(page, base))
            assert bound.status == 201
            assert page.request.get(path + "/detail").json()["case_display"] == "SYN-0492"
            page.goto(base + "/#/arbeitsliste")
            heading = page.locator(f'tr[data-row][data-id="{did}"] .titletext strong')
            expect(heading).to_have_text("Fall-ID SYN-0492")
            page.set_viewport_size({"width": 390, "height": 844})
            expect(heading).to_be_visible()
            stranger = mount._login(browser, base, mount.STRANGER)
            outsider_items = stranger.request.get(base + "/api/clinic/v1/worklist/rows").json()["items"]
            assert all(row["dictation_id"] != did for row in outsider_items)
            assert all(row.get("case_display") != "SYN-0492" for row in outsider_items)
        finally:
            browser.close()
