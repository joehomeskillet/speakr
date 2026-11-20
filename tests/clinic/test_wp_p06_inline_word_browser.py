"""Word times are painted inside the actual writing surface without moving the caret."""
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_mount_correction import live_server  # noqa: F401
from test_wp_v8_130_release_browser import review_scene  # noqa: F401


def test_inline_timed_words_and_accessible_full_reference(request, monkeypatch):
    # Use the real ingestion path so the screen receives revision-bound word cues.
    seed = """
    from src.clinic.correction import ingest_asr_result
    words = ["Anamnese", "Husten", "seit", "drei", "Tagen."]
    spoken = " ".join(words)
    timings = [dict(word=word, start=i * .74, end=i * .74 + .72) for i, word in enumerate(words)]
    ingest_asr_result(dic, spoken, [{"sentence": spoken, "start_time": 0, "end_time": 4,
                                     "words": timings}], "synthetic")
    """
    at = "    init_audio_revision(dic, original, 4000)"
    monkeypatch.setattr(mount, "SERVER", mount.SERVER.replace(at, at + "\n" + seed))
    base, did, _, _engine = request.getfixturevalue("review_scene")
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            page = mount._login(browser, base, mount.TYPIST)
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator(f'[data-testid=open-link][data-id="{did}"]').click()
            field = page.get_by_test_id("text")
            expect(field).to_have_value("Anamnese Husten seit drei Tagen.")
            reference = page.locator(".document-id summary")
            expect(reference.locator("[aria-hidden=true]")).to_have_text(f"#{did[:8]}…{did[-4:]}")
            expect(reference).to_have_accessible_name("Vollständige Diktatkennung " + did)
            reference.focus()
            reference.press("Enter")
            expect(page.locator(".document-id-full")).to_have_text(did)
            reference.press("Enter")
            field.focus()
            field.press("Home")
            field.press("ArrowRight")
            caret = field.evaluate("(el) => [el.selectionStart, el.selectionEnd]")
            player = page.get_by_test_id("play-toggle")
            player.click()
            mark = page.locator(".writing-surface mark[data-active-word]")
            expect(mark).to_have_text("Husten")
            assert field.evaluate("(el) => [el.selectionStart, el.selectionEnd]") == caret
            assert mark.evaluate("""el => {
                const word = el.getBoundingClientRect(), field = document.querySelector('[data-testid=text]').getBoundingClientRect();
                return word.top >= field.top && word.bottom <= field.bottom && word.left >= field.left
                    && word.right <= field.right && getComputedStyle(el).backgroundColor !== 'rgba(0, 0, 0, 0)';
            }""")
            expect(mark).to_have_text("seit")
            player.click()
            page.get_by_test_id("highlight-toggle").click()
            expect(mark).to_have_count(0)
            expect(field).to_have_value("Anamnese Husten seit drei Tagen.")
        finally:
            browser.close()
