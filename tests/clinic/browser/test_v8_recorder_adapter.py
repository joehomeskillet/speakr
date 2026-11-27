"""Exercise synthetic microphone takes through the mounted clinic recorder."""
import hashlib
import io
import os
import re
import wave
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from v8_browser_support import (
    ROOT,
    ROLES,
    SERVER,
    THEMES,
    VIEWPORTS,
    login,
    serve_app,
    theme_context_options,
    theme_init_script,
)


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
        finally:
            browser.close()


_CAPTURE = os.environ.get("P087_SCREENSHOTS") == "1"
_MATRIX = [(size, theme) for size in VIEWPORTS for theme in THEMES] if _CAPTURE else [("1440x900", "light")]


@pytest.fixture
def recorder_app(tmp_path):
    with serve_app(tmp_path, SERVER) as base:
        yield base


@pytest.mark.parametrize(("size", "theme"), _MATRIX)
def test_recorder_saves_and_reopens_draft_audio(recorder_app, size, theme):
    app_base = recorder_app
    width, height = VIEWPORTS[size]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
        )
        try:
            context = browser.new_context(
                storage_state=login(browser, app_base, ROLES["author"]),
                viewport={"width": width, "height": height},
                **theme_context_options(theme),
            )
            context.add_init_script(theme_init_script(theme))
            page = context.new_page()

            def capture(state):
                if not _CAPTURE:
                    return
                page.wait_for_function(
                    "dark => document.documentElement.classList.contains('dark') === dark", arg=theme == "dark"
                )
                path = ROOT / ".wp" / "artifacts" / f"p087-{size}-{theme}-{state}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=path, animations="disabled", caret="hide")

            recorder_url = app_base + "/#/neu"
            page.goto(recorder_url)
            view = page.get_by_test_id("clinic-recording-view")
            start = view.get_by_test_id("rec-start")
            edit = view.get_by_test_id("edit-created-audio")
            expect(start).to_be_enabled()
            expect(edit).to_be_hidden()
            capture("empty")

            posts = []

            def track_post(request):
                if request.method == "POST":
                    posts.append(request.url)

            page.on("request", track_post)
            start.click()
            page.wait_for_function("document.querySelector('#app').__vue_app__._instance.proxy.recordingTime >= 5")
            view.get_by_test_id("rec-stop").click()
            expect(view.get_by_test_id("rec-send")).to_be_enabled()
            save = view.get_by_test_id("rec-save-draft")
            expect(save).to_be_enabled()
            capture("review")
            with page.expect_response(app_base + "/upload") as upload:
                save.click()
            assert upload.value.status == 202, upload.value.text()
            dictation_id = upload.value.json()["dictation_id"]
            dictation_url = f"{app_base}/api/clinic/v1/dictations/{dictation_id}"
            created_id = view.get_by_test_id("created-id")
            expect(created_id).to_have_text(dictation_id)
            expect(edit).to_be_enabled()
            expect(page).to_have_url(recorder_url)
            detail = page.request.get(dictation_url + "/detail")
            assert detail.ok, detail.text()
            assert detail.json()["status_axes"]["workflow"] == "neu_angelegt"
            capture("saved-draft")

            for attempt in range(2):
                edit.click()
                editor = page.get_by_test_id("clinic-correction-view")
                expect(editor).to_be_visible()
                expect(page).to_have_url(recorder_url)
                with page.expect_response(
                    lambda response: response.url == dictation_url + "/leases" and response.request.method == "POST"
                ) as lease:
                    editor.locator('[data-action="acquire"]').click()
                assert lease.value.ok, lease.value.text()
                assert lease.value.json()["dictation_id"] == dictation_id
                drawer = editor.get_by_test_id("context-drawer")
                expect(drawer).to_have_attribute("data-open", "true")
                panel = drawer.get_by_test_id("audio-edit-panel")
                expect(panel).to_be_visible()
                start_field = panel.get_by_label("Start (s)", exact=True)
                expect(start_field).to_be_enabled()
                start_field.fill("1")
                panel.get_by_label("Ende (s)", exact=True).fill("2")
                cut = panel.get_by_role("button", name="Ausschneiden", exact=True)
                expect(cut).to_be_enabled()
                expect(panel.get_by_role("button", name="Kürzen", exact=True)).to_be_enabled()
                for action in ("Einfügen", "Überschreiben", "Rückgängig"):
                    expect(panel.get_by_role("button", name=action, exact=True)).to_be_visible()
                if attempt == 0:
                    segment = io.BytesIO()
                    with wave.open(segment, "wb") as wav:
                        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                        wav.writeframes(b"\x01\x00" * 1600)
                    panel.locator('input[type="file"]').set_input_files({
                        "name": "segment.wav", "mimeType": "audio/wav", "buffer": segment.getvalue(),
                    })
                    if _CAPTURE:
                        cut.scroll_into_view_if_needed()
                        capture("audio-drawer")
                    else:
                        def audio_digest():
                            result = page.request.get(dictation_url + "/audio/derived?format=wav")
                            assert result.ok, result.text()
                            return hashlib.sha256(result.body()).hexdigest()

                        def apply(label):
                            with page.expect_response(lambda response: response.url == dictation_url + "/audio-edits") as mutation:
                                panel.get_by_role("button", name=label, exact=True).click()
                            assert mutation.value.ok, mutation.value.text()

                        original = audio_digest()
                        for label in ("Einfügen", "Überschreiben", "Ausschneiden", "Kürzen") + ("Ausschneiden",) * 12:
                            start_field.fill("1" if label == "Kürzen" else "0.1")
                            panel.get_by_label("Ende (s)", exact=True).fill("0.11" if label == "Ausschneiden" else "")
                            apply(label)
                            expect(start_field).to_have_value("")
                        assert audio_digest() != original
                        for _ in range(16):
                            apply("Rückgängig")
                        expect(panel.get_by_role("button", name="Rückgängig", exact=True)).to_be_disabled()
                        assert audio_digest() == original
                    first_generation = lease.value.json()["generation"]
                elif not _CAPTURE:
                    assert lease.value.json()["generation"] > first_generation
                    current_url = dictation_url + "/audio-revisions/current"
                    before = page.request.get(current_url).json()
                    token = page.request.get(app_base + "/api/csrf-token").json()["csrf_token"]
                    stale = page.request.post(dictation_url + "/audio-edits", headers={
                        "X-CSRFToken": token, "Idempotency-Key": str(uuid4()),
                    }, data={
                        "operation": "trim", "position_ms": 500,
                        "audio_revision_id": before["revision_id"], "lease_generation": first_generation,
                    })
                    assert stale.status in (409, 423), stale.text()
                    assert page.request.get(current_url).json() == before
                drawer.get_by_test_id("drawer-close").click()
                editor.get_by_test_id("close").click()
                expect(editor).to_be_hidden()
                expect(created_id).to_have_text(dictation_id)
                expect(edit).to_be_enabled()
                expect(edit).to_be_focused()
                expect(page).to_have_url(recorder_url)

            assert posts.count(app_base + "/upload") == 1
            assert not any(url.endswith("/send") for url in posts), posts
        finally:
            browser.close()
