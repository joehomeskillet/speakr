"""PAR-COR-005 through the mounted Schreibplatz and Chromium's real media clock."""

import io
import os
import wave
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Locator, Page, Route, expect, sync_playwright

from test_wp_mount_correction import ROOT, TYPIST, _launch, _login, _open_from_worklist
from test_wp_mount_correction import live_server  # noqa: F401 (fixture)

STOP = "Auf zu hörendes Wort zeigen"
CONTINUE = "Beim Tippen weiterlaufen"
BACKSTEP = "Rücksprung in Sekunden"
EDITOR_TEXT = "Anamnese Husten seit drei Tagen."


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = _launch(playwright)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="module")
def synthetic_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(30 * 8000 * 2))
    return buffer.getvalue()


@pytest.fixture
def mounted(browser: Browser, live_server: tuple[str, str], synthetic_wav: bytes) -> Iterator[tuple[Page, str]]:
    base, dictation_id = live_server
    page = _login(browser, base, TYPIST)

    def serve_audio(route: Route) -> None:
        # Only the media bytes are substituted; metadata, leases, mode and UI are live.
        size = len(synthetic_wav)
        headers = {"Accept-Ranges": "bytes"}
        if requested := route.request.headers.get("range"):
            start, end = requested.removeprefix("bytes=").split("-", 1)
            first = int(start) if start else max(0, size - int(end))
            last = min(int(end), size - 1) if start and end else size - 1
            if first > last:
                route.fulfill(status=416, headers={"Content-Range": f"bytes */{size}"})
                return
            headers["Content-Range"] = f"bytes {first}-{last}/{size}"
            route.fulfill(status=206, headers=headers, content_type="audio/wav", body=synthetic_wav[first:last + 1])
        else:
            route.fulfill(status=200, headers=headers, content_type="audio/wav", body=synthetic_wav)

    page.route(f"**/dictations/{dictation_id}/audio/derived?*", serve_audio)
    try:
        yield page, base
    finally:
        page.context.close()


def _open_player(page: Page, base: str) -> tuple[Locator, Locator, Locator]:
    with page.expect_response(lambda response: response.url.endswith("/preferences/correction-mode")) as preference:
        _open_from_worklist(page, base)
    assert preference.value.ok
    view = page.get_by_test_id("clinic-correction-view")
    if stored_mode := preference.value.json().get("mode"):
        expect(view.locator(".clinic-correction-modes")).to_have_attribute("data-mode", stored_mode)
    audio = view.get_by_test_id("player-bar").locator("audio")
    expect(audio).to_have_js_property("duration", 30)
    # Observe requested seeks before the browser can clamp them. Delegate every read/write to native media.
    audio.evaluate("""audio => {
        const native = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
        audio.backstepSeeks = [];
        audio.backstepPauses = 0;
        Object.defineProperty(audio, 'currentTime', {
            get() { return native.get.call(this); },
            set(time) { this.backstepSeeks.push(time); native.set.call(this, time); },
        });
        audio.addEventListener('pause', () => audio.backstepPauses++);
    }""")
    return view, audio, view.get_by_test_id("play-toggle")


def _set_position(audio: Locator, position: int) -> None:
    audio.evaluate("""(audio, position) => {
        audio.currentTime = position;
        audio.backstepSeeks.length = 0;
    }""", position)
    expect(audio).to_have_js_property("seeking", False, timeout=5000)


def _assert_backstep(audio: Locator, paused_at: float, seconds: int) -> None:
    seeks = audio.evaluate("audio => audio.backstepSeeks")
    assert all(time >= 0 for time in seeks), seeks
    if seconds or seeks:
        assert seeks == pytest.approx([max(0, paused_at - seconds)], abs=0.002)
    else:
        assert audio.evaluate("audio => audio.currentTime") >= paused_at - 0.002


@pytest.mark.parametrize("mode", [STOP, CONTINUE], ids=["typing-pause", "keep-playing"])
@pytest.mark.parametrize("seconds", [0, 2], ids=["zero-backstep", "two-second-backstep"])
@pytest.mark.parametrize("position", [1, 10], ids=["at-1s", "at-10s"])
def test_typing_and_manual_restart(mounted: tuple[Page, str], mode: str, seconds: int, position: int) -> None:
    page, base = mounted
    view, audio, play = _open_player(page, base)
    setting = view.get_by_label(BACKSTEP, exact=True)
    expect(setting).to_have_attribute("type", "number")
    expect(setting).to_have_value("2")
    radio = view.get_by_label(mode)
    radio.check()
    expect(radio).to_be_checked()
    # In the 2-second cases, change away and back so they cover commit rather than just the default.
    for selection in ((0, 2) if seconds == 2 else (0,)):
        setting.fill(str(selection))
        setting.press("Tab")  # blur commits change
        expect(setting).to_have_value(str(selection))

    editor = view.get_by_test_id("text")
    view.locator("[data-action=acquire]").click()
    expect(editor).to_be_editable()
    original = editor.input_value()
    editor.focus()
    editor.evaluate("editor => editor.setSelectionRange(editor.value.length, editor.value.length)")
    _set_position(audio, position)
    play.click()
    expect(audio).to_have_js_property("paused", False)
    assert audio.evaluate("audio => audio.backstepSeeks") == []  # Initial play does not rewind.

    editor.press("x")
    expect(editor).to_have_value(original + "x")
    if mode == STOP:
        expect(audio).to_have_js_property("paused", True)
        paused_at = audio.evaluate("audio => audio.currentTime")
        assert paused_at == pytest.approx(position, abs=0.75)
        expect(audio).to_have_js_property("paused", False, timeout=4000)
        _assert_backstep(audio, paused_at, seconds)
    else:
        # Let real playback advance beyond the typing-idle interval; neither a pause nor a seek is allowed.
        after_key = audio.evaluate("audio => audio.currentTime")
        page.wait_for_function(
            "time => document.querySelector('[data-testid=player-bar] audio').currentTime > time",
            arg=after_key + 2,
            timeout=4000,
        )
        expect(audio).to_have_js_property("paused", False)
        assert audio.evaluate("audio => [audio.backstepPauses, audio.backstepSeeks]") == [0, []]

    play.focus()
    _set_position(audio, position)
    play.press("Space")
    expect(audio).to_have_js_property("paused", True)
    paused_at = audio.evaluate("audio => audio.currentTime")
    assert paused_at == pytest.approx(position, abs=0.75)
    assert audio.evaluate("audio => audio.backstepSeeks") == []  # Manual pause itself does not rewind.
    play.press("Space")
    expect(audio).to_have_js_property("paused", False)
    _assert_backstep(audio, paused_at, seconds)

    editor.fill(original)
    view.locator("[data-action=release]").click()
    expect(view.locator("[data-action=acquire]")).to_be_enabled()
    assert not page.js_errors, page.js_errors


def test_software_pedal_hold_does_not_add_correction_backstep(mounted: tuple[Page, str]) -> None:
    """Keyboard emulation exercises the software path, not physical pedal hardware."""
    page, base = mounted
    page.evaluate("""() => localStorage.setItem('clinic.pedal.keyboard.v1', JSON.stringify({
        mapping: { F8: 'hold' }, autoRewindSeconds: 3,
    }))""")
    view, audio, play = _open_player(page, base)
    expect(view.get_by_test_id("pedal-status")).to_have_text("Pedal: Tastaturmodus aktiv")
    expect(view.get_by_label(BACKSTEP, exact=True)).to_have_value("2")
    _set_position(audio, 10)
    page.keyboard.down("F8")
    expect(audio).to_have_js_property("paused", False)
    page.keyboard.up("F8")
    expect(audio).to_have_js_property("paused", True)
    seeks = audio.evaluate("audio => audio.backstepSeeks")
    assert seeks == pytest.approx([7], abs=0.75)
    page.keyboard.down("F8")
    expect(audio).to_have_js_property("paused", False)
    assert audio.evaluate("audio => audio.backstepSeeks") == seeks
    page.keyboard.up("F8")
    expect(audio).to_have_js_property("paused", True)
    pedal_seeks = audio.evaluate("audio => audio.backstepSeeks")
    assert len(pedal_seeks) == 2  # One seek for each release.
    play.click()  # A manual play after a pedal pause is not a manual pause/restart pair.
    expect(audio).to_have_js_property("paused", False)
    assert audio.evaluate("audio => audio.backstepSeeks") == pedal_seeks
    assert not page.js_errors, page.js_errors


@pytest.mark.skipif(os.environ.get("P145_ARTIFACTS") != "1", reason="Optional screenshot capture")
def test_capture_backstep_screenshots(mounted: tuple[Page, str]) -> None:
    page, base = mounted
    output = ROOT / ".wp" / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        page.set_viewport_size({"width": 1440, "height": 900})
        page.evaluate("dark => localStorage.setItem('darkMode', dark)", str(theme == "dark").lower())
        page.reload()
        _open_from_worklist(page, base)
        view = page.get_by_test_id("clinic-correction-view")
        editor = view.get_by_test_id("text")
        # Clearing the actual editor via UI would autosave across captures; shape only this
        # visual fixture's GET so the mounted editor can render the true manual/empty state.
        expect(editor).to_have_value(EDITOR_TEXT)
        assert page.locator("html").evaluate("el => el.classList.contains('dark')") == (theme == "dark")
        for state in ("populated", "empty"):
            if state == "empty":
                page.route("**/dictations/*/correction", _serve_empty_editor)
                try:
                    view.get_by_test_id("reload").click()
                    expect(editor).to_have_value("")
                finally:
                    page.unroute("**/dictations/*/correction", _serve_empty_editor)
            setting = view.get_by_label(BACKSTEP, exact=True)
            for width, height in ((1440, 900), (1280, 720), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                setting.evaluate("el => el.scrollIntoView({ block: 'center', behavior: 'instant' })")
                expect(setting).to_be_in_viewport(ratio=1)
                page.screenshot(path=output / f"p145-{width}x{height}-{theme}-{state}.png", animations="disabled")
    assert not page.js_errors, page.js_errors


def _serve_empty_editor(route: Route) -> None:
    response = route.fetch()
    assert response.ok
    body = response.json()
    body.update(text="", text_revision_id=None, links=[], recognition_spans=[])
    route.fulfill(response=response, json=body)
