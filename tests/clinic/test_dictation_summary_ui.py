"""Browser contract of the shared summary card; only its asynchronous backend is controlled."""
import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_mount_correction import live_server


def _login(browser, base):
    page = mount._login(browser, base, mount.TYPIST)
    # The real clinic service worker can claim a tab just after login. Keep all requests
    # for the controlled summary backend observable by Playwright throughout this test.
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setBypassServiceWorker", {"bypass": True})
    return page


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_summary_from_recording_row_and_detail(live_server, width, height):
    base, did = live_server
    saved = "Synthetische Übersicht.\n<em>Prüftext als Klartext</em>"
    state = {"text": None, "status": "empty", "may_generate": True}
    calls = []
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)
            page.set_viewport_size({"width": width, "height": height})

            def respond(route):
                method = route.request.method
                calls.append(method)
                if method == "POST":
                    assert route.request.post_data_json == {}
                    state.update(status="queued")
                    route.fulfill(status=202, json=state)
                else:
                    route.fulfill(json=state)

            page.route(f"**/api/clinic/v1/dictations/{did}/summary", respond)
            page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
            editor = page.get_by_test_id("clinic-correction-view")
            control = editor.get_by_text("KI-Zusammenfassung", exact=True)
            expect(control).to_be_visible()
            assert not calls
            control.focus()
            control.press("Enter")
            expect(editor.get_by_text("Für dieses Diktat liegt noch keine KI-Zusammenfassung vor.")).to_be_visible()
            assert calls == ["GET"]
            original = editor.get_by_test_id("text").input_value()
            editor.get_by_role("button", name="Zusammenfassung erstellen").click()
            expect(editor.get_by_text("Die KI-Zusammenfassung wartet auf die Verarbeitung.")).to_be_visible()
            state.update(text=saved, status="ready")
            expect(editor.locator(".clinic-summary-text")).to_have_text(saved, timeout=8000)
            assert editor.locator(".clinic-summary-text em").count() == 0
            expect(editor.get_by_test_id("text")).to_have_value(original)
            assert calls.count("POST") == 1
            state.update(may_generate=False)
            page.get_by_test_id("clinic-dictation-link-detail").click()
            detail = page.get_by_test_id("clinic-detail-view")
            expect(detail.locator(".clinic-summary-text")).to_have_text(saved)
            expect(detail.locator(".clinic-dictation-summary button")).to_have_count(0)
            expect(detail.locator(".clinic-summary-text em")).to_have_count(0)
            assert calls.count("POST") == 1
            assert detail.locator(".clinic-dictation-summary").evaluate(
                "e=>e.scrollWidth<=e.clientWidth && e.getBoundingClientRect().right<=innerWidth")
            assert not page.js_errors
        finally:
            browser.close()


def test_summary_error_reconciles_without_reposting(live_server):
    base, did = live_server
    calls = []
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = _login(browser, base)

            def respond(route):
                calls.append(route.request.method)
                if route.request.method == "POST":
                    route.fulfill(status=503, json={"code": "DEPENDENCY_UNAVAILABLE"})
                else:
                    route.fulfill(json={"text": "Ältere Übersicht", "status": "stale", "may_generate": True})

            page.route(f"**/api/clinic/v1/dictations/{did}/summary", respond)
            page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
            page.get_by_test_id("clinic-dictation-link-detail").click()
            summary = page.get_by_test_id("clinic-detail-view").locator(".clinic-dictation-summary")
            expect(summary.get_by_role("status")).to_have_text("Die Zusammenfassung bezieht sich auf einen früheren Textstand.")
            summary.get_by_role("button", name="Zusammenfassung neu erstellen").click()
            expect(summary.get_by_role("alert")).to_have_text("Der lokale KI-Dienst ist derzeit nicht verfügbar.")
            expect(summary.locator(".clinic-summary-text")).to_have_text("Ältere Übersicht")
            summary.get_by_role("button", name="Stand erneut laden").click()
            expect(summary.locator(".clinic-summary-text")).to_have_text("Ältere Übersicht")
            assert calls == ["GET", "POST", "GET"]
        finally:
            browser.close()
