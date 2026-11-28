"""Send a synthetic microphone take through the mounted clinic view and the real upload route."""
import os
import re

from playwright.sync_api import expect, sync_playwright

from v8_browser_support import ROLES, login


def test_recorder_can_retry_and_opens_no_upload_modal(app_base):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
        )
        try:
            context = browser.new_context(storage_state=login(browser, app_base, ROLES["author"]))
            page = context.new_page()
            page.goto(app_base + "/#/neu")
            view = page.get_by_test_id("clinic-recording-view")
            expect(view.get_by_test_id("rec-start")).to_be_enabled()
            expect(view.locator("input")).to_have_count(0)
            expect(view.locator('[data-recording-type]')).to_have_count(4)
            expect(view.get_by_role("button", name=re.compile("Patientengespräch"))).to_contain_text("Einwilligung")
            expect(view.get_by_role("button", name=re.compile("Persönliche Notizen"))).to_contain_text(
                "Nur für Sie sichtbar. Bitte keine Patientendaten.")
            posts = []
            page.on("request", lambda req: posts.append(req.url)
                    if req.method == "POST" and req.url.startswith(app_base + "/upload") else None)
            page.evaluate("""() => {
                const app = document.querySelector('#app').__vue_app__._instance.proxy;
                window.stockUploadUsed = false;
                app.$watch(() => app.showUploadModal || app.uploadQueue.length > 0, used => {
                    if (used) window.stockUploadUsed = true;
                }, { flush: 'sync', immediate: true });
            }""")
            view.get_by_test_id("rec-start").click()
            page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 5")
            view.get_by_test_id("rec-stop").click()
            page.route("**/upload", lambda route: route.fulfill(status=503), times=1)
            view.get_by_test_id("rec-send").click()
            expect(view.get_by_test_id("status")).to_contain_text("Senden fehlgeschlagen")
            assert page.url.endswith("/#/neu")

            with page.expect_response(app_base + "/upload") as upload:
                view.get_by_test_id("rec-send").click()
            assert upload.value.status == 202, upload.value.text()
            receipt = upload.value.json()
            assert re.fullmatch(r"Diktat \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", receipt["title"]), receipt
            expect(view.get_by_test_id("created-id")).to_have_text(receipt["dictation_id"])
            expect(view.get_by_test_id("status")).to_have_text("Eingereicht.")
            assert page.url.endswith("/#/neu")
            assert page.evaluate("window.stockUploadUsed") is False
            assert posts == [app_base + "/upload"] * 2
            page.get_by_test_id("nav-recordings").click()
            row = page.locator(f'[data-row][data-id="{receipt["dictation_id"]}"]')
            expect(row).to_be_visible()
            expect(row).to_contain_text(receipt["title"])

            page.goto(app_base + "/#/neu")
            view.get_by_test_id("rec-start-notiz").click()
            page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 5")
            view.get_by_test_id("rec-stop").click()
            with page.expect_response(app_base + "/upload") as upload:
                view.get_by_role("button", name="Aufnahme speichern").click()
            assert upload.value.status == 202, upload.value.text()
            private = upload.value.json()
            assert private["recording_type"] == "notiz"
            expect(view.get_by_test_id("status")).to_have_text("Aufnahme gespeichert.")
            view.get_by_test_id("open-created").click()
            page.get_by_role("button", name="In den Papierkorb").click()
            page.get_by_test_id("nav-recordings").click()
            row = page.locator(f'[data-row][data-id="{private["dictation_id"]}"]')
            expect(row).to_have_count(0)
            page.get_by_role("button", name="Papierkorb", exact=True).click()
            expect(page.get_by_text(re.compile(r"Wird am .+ endgültig gelöscht\."))).to_be_visible()
            page.get_by_role("button", name="Wiederherstellen").click()
            expect(row).to_be_visible()
        finally:
            browser.close()
