"""Exercise P06 from the recordings list against Flask, its real edit lease and rendered WAV."""
import io
import math
import os
import struct
import wave
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_rest27_repeat_ui import _repeat_server


@pytest.fixture
def audio_server(tmp_path_factory):
    yield from _repeat_server(tmp_path_factory)


def _open(page):
    page.get_by_test_id("open-link").first.click()
    page.locator("[data-context-toggle=options]").click()


def _edit(page, operation, *, key=None):
    with page.expect_response(lambda r: r.url.endswith("/audio-edits") and r.request.method == "POST") as ack:
        control = page.get_by_test_id("audio-edit-panel").get_by_role("button", name=operation, exact=True)
        if key:
            control.press(key)
        else:
            control.click()
    response = ack.value
    assert response.status == 200, (response.status, response.request.post_data_json)
    new_head = response.json()["revision_id"]
    page.wait_for_function("rev => document.querySelector('.clinic-correction-view audio').currentSrc.includes(rev)", arg=new_head)
    return new_head


def _duration(page, seconds):
    audio = page.locator(".clinic-correction-view audio")
    expect(audio).to_have_js_property("duration", seconds)


def _select(page, start, end=None):
    panel = page.get_by_test_id("audio-edit-panel")
    panel.get_by_label("Start (s)", exact=True).fill(str(start))
    if end is not None:
        panel.get_by_label("Ende (s)", exact=True).fill(str(end))


def _ready(page):
    page.locator("[data-action=acquire]").click()
    page.get_by_test_id("audio-edit-open").click()
    expect(page.get_by_label("Start (s)", exact=True)).to_be_enabled()


def test_cut_trim_undo_redo_and_reopen_with_real_player(audio_server):
    base, _, _ = audio_server
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page = mount._login(browser, base, "author213@local.test")
            _open(page)
            expect(page.get_by_test_id("audio-edit-open")).to_have_count(0)
            _ready(page)
            _duration(page, 4)
            listener = mount._login(browser, base, "admin213@local.test")
            _open(listener)
            expect(listener.get_by_test_id("audio-edit-open")).to_have_count(0)
            listener.get_by_test_id("drawer-close").click()
            listener.get_by_role("button", name="Wiedergabe starten", exact=True).click()
            listener.wait_for_function("document.querySelector('.clinic-correction-view audio').currentTime > .1")
            listener.context.close()
            initial = page.locator(".clinic-correction-view audio").get_attribute("src")
            _select(page, 1, 2)
            new_head = _edit(page, "Ausschneiden", key="Delete")
            _duration(page, 3)
            _edit(page, "Rückgängig")
            _duration(page, 4)
            expect(page.locator(".clinic-correction-view audio")).to_have_attribute("src", initial)
            assert _edit(page, "Wiederholen") == new_head
            _select(page, 1.5)
            _edit(page, "Kürzen")
            _duration(page, 1.5)
            if folder := os.environ.get("P06_CUT_ARTIFACTS"):
                for width, height in [(390, 844), (1440, 900)]:
                    page.set_viewport_size({"width": width, "height": height})
                    out = Path(folder)
                    out.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out / f"p06-audioschnitt-{width}.png"))
            page.get_by_test_id("drawer-close").click()
            page.get_by_role("button", name="Wiedergabe starten", exact=True).click()
            page.wait_for_function("document.querySelector('.clinic-correction-view audio').currentTime > .1")
            page.get_by_test_id("close").click()
            _open(page)
            _duration(page, 1.5)
            assert not page.js_errors
        finally:
            browser.close()


def test_segment_insert_overwrite_microphone_and_persisted_undo(audio_server):
    base, _, _ = audio_server
    payload = io.BytesIO()
    with wave.open(payload, "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(struct.pack("<h", round(10000 * math.sin(i * 440 * math.tau / 16000))) for i in range(8000)))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
                                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        try:
            page = mount._login(browser, base, "author213@local.test")
            _open(page)
            _ready(page)
            panel = page.get_by_test_id("audio-edit-panel")
            panel.locator("input[type=file]").set_input_files({"name":"Klang.wav", "mimeType":"audio/wav", "buffer":payload.getvalue()})
            _select(page, 1)
            _edit(page, "Einfügen")
            _duration(page, 4.5)
            _select(page, 0)
            _edit(page, "Überschreiben")
            _duration(page, 4.5)
            # Decode the exact media source used by the player. The original is quiet, the new segment is a 440 Hz tone.
            peaks = page.locator(".clinic-correction-view audio").evaluate("""async player => {
                const context = new AudioContext();
                try {
                    const audio = await context.decodeAudioData(await (await fetch(player.currentSrc)).arrayBuffer());
                    const samples = audio.getChannelData(0);
                    return [0.1, 0.7, 1.1].map(t => {
                        const i = Math.round(t * audio.sampleRate);
                        return Math.max(...samples.slice(i, i + 256));
                    });
                } finally { await context.close(); }
            }""")
            assert peaks[0] > .2 and peaks[1] < .01 and peaks[2] > .2
            page.get_by_test_id("drawer-close").click()
            page.get_by_test_id("close").click()
            _open(page)
            _ready(page)
            _edit(page, "Rückgängig")
            _duration(page, 4.5)
            _edit(page, "Rückgängig")
            _duration(page, 4)
            page.context.grant_permissions(["microphone"], origin=base)
            panel.get_by_role("button", name="Nachtrag aufnehmen").click()
            stop = panel.get_by_role("button", name="Aufnahme beenden")
            expect(stop).to_be_visible()
            # Let the browser record real chunks from Chromium's synthetic microphone.
            page.wait_for_timeout(800)
            stop.click()
            expect(panel.get_by_text("Bereit: Nachtrag.webm")).to_be_visible()
            _select(page, 0)
            with page.expect_response(lambda r: r.url.endswith("/audio-segments")) as uploaded:
                _edit(page, "Einfügen")
            assert uploaded.value.status == 201
            assert uploaded.value.json()["duration_ms"] > 0
            page.wait_for_function("document.querySelector('.clinic-correction-view audio').duration > 4")
            assert not page.js_errors
        finally:
            browser.close()
