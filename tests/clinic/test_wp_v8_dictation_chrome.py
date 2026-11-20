"""Dictation metadata and shared navigation in the mounted clinic shell."""

import pytest
from playwright.sync_api import expect, sync_playwright

from test_wp_mount_correction import TYPIST, _launch, _login, _open_from_worklist, live_server
from test_wp_v8_shell_mount_dictation import VIEWS


@pytest.mark.parametrize("unavailable", [False, True], ids=["seeded-staff", "detail-503"])
def test_worklist_opens_correction_with_staff_and_navigation(live_server, unavailable):
    base, dictation = live_server
    detail = f"{base}/api/clinic/v1/dictations/{dictation}/detail"
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, TYPIST)
            if unavailable:
                page.route(detail, lambda route: route.fulfill(status=503, json={}))
            with page.expect_response(detail) as read:
                _open_from_worklist(page, base)
            assert read.value.status == (503 if unavailable else 200)
            view = page.locator(".clinic-correction-view")
            expect(view.locator("dd[data-dictator-name]")).to_have_text("Nicht verfügbar" if unavailable else "u213")
            expect(view.locator("dd[data-pool-name]")).to_have_text("Nicht verfügbar" if unavailable else "Schreibdienst 213")
            head = view.locator(".pagehead")
            expect(head.get_by_test_id("clinic-correction-back")).to_be_visible()
            expect(head.get_by_test_id("clinic-dictation-nav")).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_navigation_stays_in_place_across_dictation_views(live_server, width, height):
    base, _ = live_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, TYPIST)
            _open_from_worklist(page, base)
            page.set_viewport_size({"width": width, "height": height})
            nav = page.get_by_test_id("clinic-dictation-nav")
            back = page.get_by_role("button", name="Zurück zur Arbeitsliste", exact=True)
            start = [el.bounding_box() for el in (back, nav)]
            for view in ["correction", *(view[0] for view in VIEWS)]:
                if view != "correction":
                    tab = nav.get_by_test_id(f"clinic-dictation-link-{view}")
                    tab.click()
                    expect(tab).to_have_attribute("aria-current", "page")
                expect(back.locator(".fa-arrow-right[aria-hidden=true]")).to_be_visible()
                for el, initial in zip((back, nav), start):
                    rect = el.bounding_box()
                    for axis in ("x", "y"):
                        assert rect[axis] == pytest.approx(initial[axis], abs=1), (view, axis)
                tabs = nav.get_by_role("button")
                expect(tabs).to_have_count(5)
                assert len({tab.bounding_box()["y"] for tab in tabs.all()}) == 1
                if width == 390:
                    assert nav.evaluate("el => el.scrollWidth > el.clientWidth")
                    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
            back.click()
            expect(page.get_by_test_id("clinic-worklist-view")).to_be_visible()
        finally:
            browser.close()
