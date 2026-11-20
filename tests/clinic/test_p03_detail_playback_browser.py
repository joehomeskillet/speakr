"""Real P03 detail playback of cut audio, with independent view, play, edit and export rights."""
import io
import os
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

import test_wp_mount_correction as mount
from test_wp_rest27_repeat_ui import _repeat_server


@pytest.fixture
def detail_server(tmp_path_factory, request):
    # Extend the established synthetic clinic fixture. The typist inherits view via their
    # assigned pool, and has only a play grant. The author can see the case but is denied play and edit.
    server = mount.SERVER.replace('actions=("play", "edit", "release")', 'actions=("play",)')
    server = server.replace('    rec = Recording(', '''    pools.deny(author.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=("play", "edit"))
    rec = Recording(''', 1)
    # Four distinct square waves make cutting out the second interval byte-verifiable.
    variable_envelope = getattr(request, "param", None) == "variable_envelope"
    server = server.replace("with app.app_context():", f"""with app.app_context():
    from math import sin
    def sample(i, half_period):
        amplitude = int(12000 * (.03 + abs(sin(i / 1850)) ** 2)) if {variable_envelope!r} else 3000
        return (amplitude if i // half_period % 2 else -amplitude).to_bytes(2, "little", signed=True)

    audio_buffer = BytesIO()
    with wave.open(audio_buffer, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        for half_period in (40, 32, 25, 20):
            wav.writeframes(b"".join(sample(i, half_period)
                                    for i in range(16000)))
    audio_bytes = audio_buffer.getvalue()""", 1)
    server = server.replace('    init_audio_revision(dic, original, 4000)', '''    init_audio_revision(dic, original, 4000)
    from src.clinic.audio_revisions import apply_cut
    apply_cut(dic, 1000, 2000)''')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mount, "SERVER", server)
        yield from _repeat_server(tmp_path_factory)


def _detail(browser, base, username, did, size, *, peaks_offline=False):
    page = mount._login(browser, base, username)
    page.set_viewport_size(size)
    page.get_by_test_id("clinic-recordings-view").locator(f'[data-row][data-id="{did}"]').click()
    if peaks_offline:
        # Only the optional waveform transport is unavailable. All permission, WAV and edit routes remain real.
        page.route("**/audio-revisions/*/peaks", lambda route: route.abort("connectionfailed"))
    events = []
    page.on("request", lambda r: events.append((r.method, urlsplit(r.url).path)))
    with page.expect_response(lambda r: urlsplit(r.url).path.endswith("/audio-revisions/current")) as head:
        page.get_by_test_id("clinic-dictation-link-detail").click()
    host = page.get_by_test_id("clinic-detail-view")
    expect(host.get_by_test_id("open-editor")).to_be_visible()
    return page, host.get_by_test_id("detail-playback"), head.value, events


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_read_only_detail_plays_seeks_cut_audio_without_a_lease_or_export(detail_server, width, height):
    from src.models import Recording

    base, did, engine = detail_server
    with Session(engine) as session:
        rid = session.query(Recording).filter_by(title="Synthetisch").one().id
    size = {"width": width, "height": height}
    with sync_playwright() as p:
        browser = mount._launch(p)
        try:
            page, panel, head, events = _detail(browser, base, mount.TYPIST, did, size, peaks_offline=True)
            assert head.status == 200
            rev = head.json()["revision_id"]
            audio = panel.get_by_label("Diktat anhören", exact=True)
            expect(audio).to_have_js_property("duration", 3)
            actual_src = urlsplit(audio.evaluate("e => e.currentSrc"))
            assert actual_src.path == f"/api/clinic/v1/dictations/{did}/audio/derived"
            assert parse_qs(actual_src.query) == {"format": ["wav"], "audio_revision_id": [rev]}
            res = page.request.get(audio.evaluate("e => e.currentSrc"))
            assert res.status == 200
            with wave.open(io.BytesIO(res.body())) as wav:
                assert wav.getparams()[:4] == (1, 2, 16000, 48000)
                for half_period in (40, 25, 20):
                    expected = b"".join((3000 if i // half_period % 2 else -3000).to_bytes(2, "little", signed=True)
                                        for i in range(16000))
                    assert wav.readframes(16000) == expected
            audio.click(position={"x": 26, "y": 27})
            page.wait_for_function("document.querySelector('.clinic-detail-view audio').currentTime > 0.1")
            audio.click(position={"x": 26, "y": 27})
            expect(audio).to_have_js_property("paused", True)
            # Chromium's native timeline responds to a real pointer independently of playback.
            bounds = audio.bounding_box()
            audio.click(position={"x": int(bounds["width"] * .58), "y": 27})
            page.wait_for_function("document.querySelector('.clinic-detail-view audio').currentTime > 0.7")
            assert events and all(m == "GET" and path != f"/audio/{rid}" for m, path in events)
            assert "nodownload" in audio.get_attribute("controlslist")
            expect(panel.locator("a[download]")).to_have_count(0)
            assert page.request.get(f"{base}/audio/{rid}?download=true").status in (403, 404)
            if folder := os.environ.get("P03_ARTIFACTS"):
                Path(folder).mkdir(exist_ok=True, parents=True)
                page.screenshot(path=str(Path(folder) / f"p03-hoeren-{width}.png"))
            before_key = audio.evaluate("e => e.currentTime")
            page.keyboard.press("ArrowLeft")
            page.wait_for_function("t => document.querySelector('.clinic-detail-view audio').currentTime < t",
                                   arg=before_key, timeout=4000)
            assert not page.js_errors
            page.context.close()

            reader, panel, head, events = _detail(browser, base, "author213@local.test", did, size)
            assert head.status == 404
            expect(panel.get_by_role("status")).to_have_text("Für dieses Diktat ist keine Wiedergabe verfügbar.")
            expect(panel.locator("audio")).to_have_count(0)
            assert all("/audio/derived" not in path and m == "GET" for m, path in events)
            assert reader.request.get(base + actual_src.path + "?" + actual_src.query).status == 404
            assert not reader.js_errors
        finally:
            browser.close()



def test_detail_explains_a_missing_media_source(tmp_path_factory):
    for base, did in mount.live_server.__wrapped__(tmp_path_factory):
        with sync_playwright() as p:
            browser = mount._launch(p)
            try:
                page, panel, head, _ = _detail(browser, base, mount.TYPIST, did, {"width": 390, "height": 844})
                assert head.status == 200
                expect(panel.get_by_role("status")).to_have_text("Die Audiodatei lässt sich nicht abspielen.")
                expect(panel.locator("audio")).to_have_count(0)
                assert not page.js_errors
            finally:
                browser.close()
