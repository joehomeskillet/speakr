"""Synthetic browser evidence for name-based entry points; no device/ASR claims."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("v149_menu", Path(__file__).with_name("test_wp_v8_shell_mount_menu.py"))
_menu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_menu)
live_server = _menu.live_server


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_name_entries_and_visual_proof(live_server, width, height, tmp_path):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _menu._launch(p)
        try:
            page = _menu._login(browser, base, width, height)
            user_id = page.locator("#app").get_attribute("data-current-user-id")
            item = {"dictation_id": dictation, "workflow_status": "neu_angelegt", "day": "2026-11-20",
                    "sort_time": "2026-11-20T08:00:00Z", "time_source": "recorded", "duration_ms": 4000,
                    "dictated_by": {"id": user_id, "display_name": "Dr. Beispiel"},
                    "staff_pools": [{"id": "3", "name": "Stationär"}], "lease_holder": None}
            page.route("**/api/clinic/v1/recordings*", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"items": [item], "next_cursor": None})))
            page.goto(base + "/#/live")
            _menu._mounted(page)
            page.wait_for_function("document.querySelectorAll('#clinic-live-choices option').length === 1")
            label = page.locator("#clinic-live-choices option").get_attribute("value")
            assert "Dr. Beispiel" in label and dictation not in label
            page.get_by_label("Diktat suchen").fill(label)
            page.keyboard.press("Tab")
            page.screenshot(path=str(tmp_path / f"live-picker-{width}.png"), full_page=True)
            page.get_by_role("button", name="Live-Diktat öffnen").click()
            page.locator("[data-testid=clinic-live-view]").wait_for(state="visible")
            page.goto(base + "/#/neu")
            _menu._mounted(page)
            name = page.locator("#app").get_attribute("data-current-user-name")
            assert name
            # X-NEU-1 owner decision: the one-button recorder on #/neu shows no author block;
            # the signed-in user is the author implicitly.
            view = page.locator("[data-testid=clinic-recording-view]")
            view.wait_for(state="visible")
            assert view.get_by_test_id("author").count() == 0
            page.goto(base + "/#/archiv")
            _menu._mounted(page)
            page.locator("#cav-author option[value='" + user_id + "']").wait_for(state="attached")
            assert page.locator("#cav-author").inner_text().find("Dr. Beispiel") >= 0
            page.locator("#cav-author").select_option(user_id)
            page.locator("#cav-pool").select_option("3")
            page.screenshot(path=str(tmp_path / f"archive-names-{width}.png"), full_page=True)
            # Mount the real admin activity component with a synthetic scoped client, not admin credentials.
            page.evaluate("""async ({item}) => {
                const { ActivityView } = await import('/static/js/clinic/compose-activity.js');
                document.querySelector('main').innerHTML = '<div id="activity-proof"></div>';
                const client = {switchContext() {}, async request(path) {
                    return path.startsWith('/recordings') ? {outcome: 'ok', data: {items: [item], next_cursor: null}}
                        : {outcome: 'permission'};
                }};
                Vue.createApp(ActivityView, {client, currentUser: {id: item.dictated_by.id}}).mount('#activity-proof');
            }""", {"item": item})
            page.wait_for_function("document.querySelectorAll('#act-choices option').length === 1")
            activity_label = page.locator("#act-choices option").get_attribute("value")
            page.get_by_label("Diktat suchen").fill(activity_label)
            page.screenshot(path=str(tmp_path / f"activity-names-{width}.png"), full_page=True)
            page.get_by_role("button", name="Verlauf anzeigen", exact=True).click()
            page.get_by_text("Keine Berechtigung für diesen Verlauf.", exact=True).wait_for()
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
