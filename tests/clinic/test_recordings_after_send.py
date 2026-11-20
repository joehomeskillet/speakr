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
            row = page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]')
            expect(row).to_contain_text("Neu angelegt", ignore_case=True)
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
            expect(row).to_contain_text("Zu schreiben", ignore_case=True)
            assert list_reads() == 2
            page.get_by_test_id("nav-clinic-worklist").click()
            expect(work_row).to_contain_text("Zu schreiben", ignore_case=True)
        finally:
            browser.close()
