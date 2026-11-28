"""Both cached clinic lists reflect a draft submitted in the same browser tab."""

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount


@pytest.fixture
def unsubmitted_draft(tmp_path_factory, monkeypatch):
    seed = mount.SERVER.replace("status=workflow.TO_WRITE", "status=workflow.DRAFT")
    seed = seed.replace("Anamnese Husten seit drei Tagen.", "...")
    monkeypatch.setattr(mount, "SERVER", seed)
    yield from mount.live_server.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize("return_link", ["clinic-dictation-back", "nav-recordings"])
def test_p01_refreshes_submitted_row_in_same_tab(unsubmitted_draft, return_link):
    base, did = unsubmitted_draft
    with sync_playwright() as pw:
        browser = mount._launch(pw)
        try:
            page = mount._login(browser, base, "author213@local.test")
            view = page.get_by_test_id("clinic-recordings-view")
            assert page.url == base + "/"
            shortcut = view.get_by_test_id("pending-shortcut")
            expect(shortcut).to_have_accessible_name("Sie haben 1 Diktat, das noch nicht gesendet ist.")
            shortcut.click()
            expect(view.get_by_test_id("pending-heading")).to_be_focused()
            row = view.locator(f'[data-row][data-id="{did}"]')
            expect(view.locator("#crv-pending [data-row]")).to_have_attribute("data-id", did)
            next_step = row.get_by_test_id("recording-next-step")
            expect(next_step).to_have_text("Sie sind dran: Diktat an den Schreibdienst senden")
            expect(next_step).to_have_attribute("data-action-needed", "true")
            page.get_by_test_id("nav-clinic-worklist").click()
            work_row = page.get_by_test_id("clinic-worklist-view").locator(f'[data-row][data-id="{did}"]')
            expect(work_row).to_contain_text("Neu angelegt", ignore_case=True)
            page.get_by_test_id("nav-recordings").click()

            def list_reads():
                return sum("200 GET" in line and "/api/clinic/v1/recordings?" in line for line in page.responses)

            assert list_reads() == 1
            row.click()
            page.get_by_test_id("clinic-dictation-link-detail").click()
            expect(page.get_by_test_id("status-axes")).to_contain_text("Ohne Erkennung")
            workflow = page.locator(".clinic-workflow-actions")
            workflow.get_by_role("button", name="Einreichen", exact=True).click()
            with page.expect_response(lambda res: res.request.method == "POST" and res.url.endswith(f"/{did}/send")) as sent:
                workflow.get_by_role("button", name="Bestätigen").click()
            assert sent.value.status == 200
            assert sent.value.json()["status"] == "zu_schreiben"
            page.get_by_test_id(return_link).click()
            expect(next_step).to_have_text("Bei t213 – Sie müssen nichts tun")
            expect(next_step).to_have_attribute("data-action-needed", "false")
            expect(shortcut).to_have_count(0)
            expect(view.get_by_test_id("pending-heading")).to_have_count(0)
            assert list_reads() == 2
            page.get_by_test_id("nav-clinic-worklist").click()
            expect(work_row).to_contain_text("Zu schreiben", ignore_case=True)
        finally:
            browser.close()


@pytest.mark.parametrize("committed", [False, True], ids=["interrupted-send", "lost-response"])
def test_p01_checks_server_after_interrupted_send(unsubmitted_draft, committed):
    base, did = unsubmitted_draft
    with sync_playwright() as pw:
        browser = mount._launch(pw)
        try:
            page = mount._login(browser, base, "author213@local.test")
            view = page.get_by_test_id("clinic-recordings-view")
            view.locator("#crv-pending [data-row]").click()
            page.get_by_test_id("clinic-dictation-link-detail").click()
            workflow = page.locator(".clinic-workflow-actions")
            workflow.get_by_role("button", name="Einreichen", exact=True).click()

            def interrupt(route):
                if committed:
                    assert route.fetch().status == 200
                route.abort("failed")

            page.route(f"**/{did}/send", interrupt)
            workflow.get_by_role("button", name="Bestätigen").click()
            expect(workflow.get_by_text("Die Verbindung ist fehlgeschlagen.", exact=True)).to_be_visible()
            with page.expect_response(lambda res: "/api/clinic/v1/recordings?" in res.url) as refreshed:
                page.get_by_test_id("clinic-dictation-back").click()
            assert refreshed.value.ok
            row = view.locator(f'[data-row][data-id="{did}"]')
            expect(row).to_be_visible()
            expect(view.locator("#crv-pending [data-row]")).to_have_count(0 if committed else 1)
            expect(view.get_by_test_id("pending-shortcut")).to_have_count(0 if committed else 1)
        finally:
            browser.close()
