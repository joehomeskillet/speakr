"""PAR-COR-004: right click and keyboard reset the playback tempo to 1.0 in a real Chromium page.

The audio is a real HTMLAudioElement with a generated WAV. A keyboard event is not evidence for a
foot pedal: BT-COR-004 stays not_run until a supported pedal is tried with audible marks.
"""
import base64
import io
import os
import threading
import wave
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def _wav_url(seconds=20):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 8000 * seconds)
    return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()


PAGE = """<!doctype html><html lang="de"><head>
<link rel="stylesheet" href="/static/css/tokens.css"><link rel="stylesheet" href="/static/css/clinic-v8.css"></head>
<body><div class="v8-ui"><div class="v8-page"><div class="compact-player" id="host"></div></div></div>
<script src="/static/vendor/js/vue.global.js"></script>
<script type="module">
import { PlaybackTempo } from '/static/js/clinic/playback-tempo-ui.js';
const audio = new Audio(AUDIO_URL);
window.testAudio = audio;
Vue.createApp({components: {PlaybackTempo}, setup() { return {audio}; },
  template: '<playback-tempo :audio="audio" :step-seconds="5" />'}).mount('#host');
window.mounted = true;
</script></body></html>""".replace("AUDIO_URL", repr(_wav_url()))


@pytest.fixture
def base():
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/proof":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _open(browser, base, width=1440, height=900):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/proof")
    page.wait_for_function("window.mounted === true")
    page.wait_for_function("window.testAudio.duration > 0")
    return page, errors


def _rate(page):
    return page.evaluate("window.testAudio.playbackRate")


@pytest.mark.parametrize("rate", ["0.5", "2"])
@pytest.mark.parametrize("trigger", ["contextmenu", "Delete", "Backspace"])
def test_reset_returns_audio_and_selection_to_normal_speed(base, rate, trigger):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            page, errors = _open(browser, base)
            select = page.get_by_test_id("tempo-select")
            select.select_option(rate)
            assert _rate(page) == float(rate)
            if trigger == "contextmenu":
                select.click(button="right")
            else:
                select.focus()
                page.keyboard.press(trigger)
            assert _rate(page) == 1.0
            expect(select).to_have_value("1")
            assert select.evaluate("e => e.selectedOptions[0].textContent.trim()") == "1.0×"
            assert not errors
        finally:
            browser.close()


def test_seek_changes_audio_time_not_wall_time_at_any_tempo(base):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            page, errors = _open(browser, base)
            page.get_by_test_id("tempo-select").select_option("2")
            page.evaluate("window.testAudio.currentTime = 10")
            page.get_by_role("button", name="Vorspulen").click()
            assert page.evaluate("window.testAudio.currentTime") == pytest.approx(15, abs=0.01)
            page.get_by_role("button", name="Zurückspulen").click()
            page.get_by_role("button", name="Zurückspulen").click()
            assert page.evaluate("window.testAudio.currentTime") == pytest.approx(5, abs=0.01)
            assert _rate(page) == 2.0
            assert not errors
        finally:
            browser.close()


def test_reset_control_visuals(base, tmp_path):
    out = Path(os.environ.get("COR004_ARTIFACTS", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            for width, height in [(1440, 900), (1280, 720), (390, 844)]:
                page, errors = _open(browser, base, width, height)
                for theme in ("light", "dark"):
                    page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
                    page.get_by_test_id("tempo-select").select_option("0.5")
                    page.screenshot(path=str(out / f"cor004-{width}-{theme}.png"))
                assert not errors
                page.close()
        finally:
            browser.close()
