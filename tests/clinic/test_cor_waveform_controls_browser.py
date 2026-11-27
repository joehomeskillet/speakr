"""PAR-COR-003 browser verification: zoom and hide/show waveform controls in real Chromium."""
import io
import math
import os
import struct
import wave
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".wp" / "artifacts"


def _waveform_server(tmp_path_factory, *, with_audio=True):
    seed = """
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import math, os, struct, wave
from src.clinic.correction import set_manual_link
from src.clinic import correction_recognition_marks as marks
_buf = BytesIO()
with wave.open(_buf, 'wb') as _wav:
    _wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    _wav.writeframes(b''.join(
        struct.pack('<h', round((10000 if (0.3 <= i / 16000 <= 1.5 or 3.5 <= i / 16000 <= 4.7) else 0)
                                * math.sin(i * 440 * math.tau / 16000)))
        for i in range(6 * 16000)
    ))
audio_bytes = _buf.getvalue()
"""
    server = mount.SERVER.replace("with app.app_context():", seed + "\nwith app.app_context():", 1) if with_audio else mount.SERVER
    if with_audio:
        server = server.replace(
            'content_hash="o" * 64, size_bytes=1000',
            "content_hash=sha256(audio_bytes).hexdigest(), size_bytes=len(audio_bytes)",
        )
        server = server.replace(
            'add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")',
            'transcript = add_asr_revision(dic, original, "Anamnese Pause Husten Ende.", "model:synthetic")',
        )
        server = server.replace(
            "init_audio_revision(dic, original, 4000)",
            "audio_rev = init_audio_revision(dic, original, 6000)",
        )
        server = server.replace(
            "    dictation_id = dic.id",
            """
    source = Path(app.config['UPLOAD_FOLDER']) / 'wave.wav'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(audio_bytes)
    rec.audio_path = str(source)
    rec.transcription = 'Anamnese Pause Husten Ende.'
    media = Path(os.environ['CLINIC_ORIGINALS_ROOT']) / original.storage_key
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(audio_bytes)
    set_manual_link(dic, transcript, 0, 8, 300, 1500, author.id)
    set_manual_link(dic, transcript, 15, 21, 3500, 4700, author.id)
    marks.add_mark(dic, audio_rev, transcript, run_id='run-1', start_ms=400, end_ms=1400,
                   kind='uncertain', source='fixture-asr:1/seg/0', confidence=0.35)
    marks.add_mark(dic, audio_rev, transcript, run_id='run-1', start_ms=3600, end_ms=4400,
                   kind='command', source='fixture-asr:1/seg/1', confidence=None)
    db.session.commit()
    dictation_id = dic.id""",
        )
    else:
        # Empty / no-peaks state: no original file written so peaks route returns 503 DEPENDENCY_UNAVAILABLE -> "none".
        pass

    original_env = mount._env

    def env(path, _clinic):
        result = original_env(path, True)
        return {**result, "CLINIC_ORIGINALS_ROOT": str(path / "originals"), "CLINIC_MODE": "1"}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mount, "SERVER", server)
        patch.setattr(mount, "_env", env)
        yield from mount.live_server.__wrapped__(tmp_path_factory)


@pytest.fixture
def wave_server(tmp_path_factory):
    yield from _waveform_server(tmp_path_factory, with_audio=True)


@pytest.fixture
def empty_wave_server(tmp_path_factory):
    yield from _waveform_server(tmp_path_factory, with_audio=False)


def _open_ready_editor(browser, base):
    page = mount._login(browser, base, mount.TYPIST)
    mount._open_from_worklist(page, base)
    page.get_by_test_id("text").wait_for(state="visible", timeout=30000)
    page.locator("[data-action=acquire]").click()
    page.wait_for_function("() => { const el = document.querySelector('[data-testid=text]'); return el && !el.readOnly; }", timeout=20000)
    return page


def _save_view(page, name):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=True)
    (ARTIFACTS / f"{name}.html").write_text(page.content(), encoding="utf-8")


def test_waveform_zoom_hide_keyboard_focus_and_sync(wave_server):
    base, _ = wave_server
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page = _open_ready_editor(browser, base)
            wave = page.locator(".clinic-correction-waveform")
            expect(wave).to_have_attribute("data-status", "ready")
            expect(wave.locator("svg[role=img]")).to_be_visible()

            audio = page.locator(".clinic-correction-view audio")
            expect(audio).to_have_js_property("duration", 6)

            # Seek via player scrubber to first phrase (0.9 s) and verify playhead + current phrase.
            scrubber = page.get_by_role("slider", name="Abspielposition")
            scrubber.evaluate("""el => {
                el.value = 0.9;
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""")
            expect(wave.locator(".clinic-correction-waveform-current")).to_contain_text("Anamnese")
            x1 = float(wave.locator("line[data-kind=playhead]").get_attribute("x1"))
            assert 100 < x1 < 200

            # Start real playback and verify position advances, then pause.
            page.get_by_role("button", name="Wiedergabe starten", exact=True).click()
            page.wait_for_function("document.querySelector('.clinic-correction-view audio').currentTime > 1.0")
            page.get_by_role("button", name="Wiedergabe anhalten", exact=True).click()
            expect(audio).to_have_js_property("paused", True)

            # Pointer activation while textarea has focus & selection must preserve editor focus and selection.
            editor = page.get_by_test_id("text")
            editor.focus()
            editor.evaluate("el => el.setSelectionRange(22, 26)")
            zoom_in = page.get_by_test_id("waveform-zoom-in")
            zoom_out = page.get_by_test_id("waveform-zoom-out")
            zoom_reset = page.get_by_test_id("waveform-zoom-reset")
            toggle = page.get_by_test_id("waveform-toggle")

            zoom_in.click()
            expect(wave).to_have_attribute("data-zoom", "2")
            assert page.evaluate("document.activeElement === document.querySelector('[data-testid=text]')")
            assert page.evaluate("() => { const e = document.querySelector('[data-testid=text]'); return [e.selectionStart, e.selectionEnd]; }") == [22, 26]

            # Scrubber seek to second phrase (4.0 s) while zoomed keeps playhead and phrase synchronized.
            scrubber.evaluate("""el => {
                el.value = 4.0;
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""")
            expect(wave.locator(".clinic-correction-waveform-current")).to_contain_text("Husten")
            expect(wave.locator("line[data-kind=playhead]")).to_be_attached()
            assert float(wave.locator("line[data-kind=playhead]").get_attribute("x1")) == pytest.approx(500, abs=1)
            assert page.evaluate("document.activeElement === document.querySelector('[data-testid=text]')")

            # Typing immediately goes into the still-focused editor.
            page.keyboard.type("Neu")
            assert "Husten Neu." in editor.input_value()

            # Keyboard operation: Tab to zoom-in and hit Enter until 16x, focus must stay on button!
            peaks_count_before = len([r for r in page.responses if "/peaks" in r])
            corr_count_before = len([r for r in page.responses if r.endswith("/correction")])
            zoom_in.focus()
            for expected_zoom in ("4", "8", "16"):
                page.keyboard.press("Enter")
                expect(wave).to_have_attribute("data-zoom", expected_zoom)
                expect(zoom_in).to_be_focused()
            expect(zoom_in).to_have_attribute("aria-disabled", "true")

            # Keyboard Space on zoom-reset returns to 1x and keeps focus.
            zoom_reset.focus()
            page.keyboard.press("Space")
            expect(wave).to_have_attribute("data-zoom", "1")
            expect(zoom_reset).to_be_focused()

            # Toggle hide/show via keyboard (Enter / Space) keeps focus on toggle, keeps phrase & marks visible,
            # and sends zero extra peaks/correction requests.
            toggle.focus()
            page.keyboard.press("Enter")
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(wave.locator("svg[role=img]")).to_have_count(0)
            expect(wave.locator(".clinic-correction-waveform-current")).to_contain_text("Husten")
            expect(wave.locator(".clinic-correction-waveform-uncertain button")).to_have_count(2)
            expect(toggle).to_be_focused()

            # Seek while hidden updates phrase; showing again syncs playhead with zero extra requests.
            scrubber.evaluate("""el => {
                el.value = 0.8;
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }""")
            expect(wave.locator(".clinic-correction-waveform-current")).to_contain_text("Anamnese")
            toggle.focus()
            page.keyboard.press("Space")
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(wave.locator("svg[role=img]")).to_be_visible()
            expect(wave.locator("line[data-kind=playhead]")).to_be_attached()
            x_after_show = float(wave.locator("line[data-kind=playhead]").get_attribute("x1"))
            assert 100 < x_after_show < 200
            assert len([r for r in page.responses if "/peaks" in r]) == peaks_count_before
            assert len([r for r in page.responses if r.endswith("/correction")]) == corr_count_before

            # Zoom to 2x and capture required viewports in light and dark themes.
            zoom_in.click()
            for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                for theme in ("light", "dark"):
                    page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"overflow at {width} {theme}"
                    for testid in ("waveform-toggle", "waveform-zoom-out", "waveform-zoom-in", "waveform-zoom-reset"):
                        box = page.get_by_test_id(testid).bounding_box()
                        assert box is not None and box["height"] >= 48 and box["width"] >= 48, (testid, box)
                    _save_view(page, f"par-p111-ready-{width}x{height}-{theme}")
                    if width == 390:
                        wave.screenshot(path=str(ARTIFACTS / f"par-p111-wave-{width}x{height}-{theme}.png"))
            # Also check 390px hidden state and editor focus preservation on mobile width.
            page.evaluate("() => document.documentElement.classList.remove('dark')")
            page.set_viewport_size({"width": 390, "height": 844})
            editor.focus()
            toggle.click()
            expect(wave).to_have_attribute("data-wave-visible", "false")
            assert page.evaluate("document.activeElement === document.querySelector('[data-testid=text]')")
            _save_view(page, "par-p111-hidden-390x844-light")

            assert not page.js_errors
        finally:
            browser.close()


def test_waveform_empty_state_renders_cleanly(empty_wave_server):
    base, _ = empty_wave_server
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page = _open_ready_editor(browser, base)
            wave = page.locator(".clinic-correction-waveform")
            expect(wave).to_have_attribute("data-status", "none")
            expect(wave).to_contain_text("Keine Wellenform verfügbar.")
            for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                for theme in ("light", "dark"):
                    page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    _save_view(page, f"par-p111-empty-{width}x{height}-{theme}")
            assert not page.js_errors
        finally:
            browser.close()
