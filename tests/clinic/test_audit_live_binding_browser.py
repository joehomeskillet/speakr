"""Real clinic shell, tab fencing and durable capture against a synthetic local ASR."""

import pytest
from playwright.sync_api import expect, sync_playwright

import test_audit_live_draft_picker_browser as drafts
from test_audit_live_draft_picker_browser import draft_app  # noqa: F401
from test_audit_live_stream_api import ManualSpeaches, _MODEL
from test_wp_mount_correction import _launch, _login

LIVE = "/api/clinic/v1/live"
CAPTURE_APP = '''
from gunicorn.app.base import BaseApplication

class BrowserApp(BaseApplication):
    def load_config(self):
        self.cfg.set("bind", f"127.0.0.1:{port}")
        self.cfg.set("threads", 4)
        self.cfg.set("when_ready", lambda _server: print("READY " + json.dumps(originals), flush=True))

    def load(self):
        return app

BrowserApp().run()
'''
MICROPHONE = """() => {
    navigator.mediaDevices.getUserMedia = async () => {
        const context = new AudioContext();
        const gain = context.createGain();
        const tone = context.createOscillator();
        const output = context.createMediaStreamDestination();
        tone.connect(gain).connect(output);
        gain.gain.value = 0;
        tone.start();
        await context.resume();
        window.syntheticMicrophone = { context, gain, track: output.stream.getAudioTracks()[0] };
        return output.stream;
    };
}"""


class BrowserPeer(ManualSpeaches):
    def _run(self):
        self._listen.settimeout(60)
        super()._run()


@pytest.fixture
def binding_app(monkeypatch, request):
    peer = BrowserPeer()
    monkeypatch.setenv("CLINIC_LIVE_ASR_BASE_URL", peer.base_url)
    monkeypatch.setenv("CLINIC_LIVE_ASR_MODEL", _MODEL)
    monkeypatch.setenv("CLINIC_LIVE_ASR_API_KEY", "")
    seed = drafts.SEED.split("server = make_server", 1)[0].replace(
        "import io, os, wave", "import io, os, wave\nfrom src.clinic import audio_revisions",
    ).replace(
        "        originals[dictation.id]",
        """        db.session.flush()
        original = ClinicOriginal.query.filter_by(dictation_id=dictation.id).one()
        audio_revisions.init_audio_revision(dictation, original, 1000)
        originals[dictation.id]""",
    )
    monkeypatch.setattr(drafts, "SEED", seed + CAPTURE_APP)
    try:
        yield request.getfixturevalue("draft_app")
    finally:
        peer.close()


def _switch(page, base, draft_id, action):
    with page.expect_response(f"{base}{LIVE}/drafts/{draft_id}/switch") as selected:
        action()
    return selected.value


def _history(page, base, draft_id, session_id):
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    response = page.request.post(f"{base}{LIVE}/dictations/{draft_id}/resume",
                                 data={"session_id": session_id, "after_sequence": 0},
                                 headers={"X-CSRF-Token": token})
    assert response.ok, response.text()
    return response.json()["events"]


def test_bound_draft_follows_tab_and_survives_focus_change(binding_app):
    base, originals = binding_app
    a, b = originals
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, drafts.AUTHOR)
            frames = []
            page.on("websocket", lambda socket: socket.on("framereceived", lambda data: frames.append(data)))
            drafts._picker(page, base)
            drafts._capture(page, "picker-empty", prefix="p248-live-binding")
            drafts._create(page, a, drafts.A)
            page.get_by_test_id("live-draft-close").click()
            drafts._create(page, b, drafts.B)
            pick = page.get_by_role("button", name=drafts.A, exact=True)
            selected = _switch(page, base, a, pick.click)
            assert selected.ok, selected.text()
            position = selected.json()
            status = page.get_by_test_id("live-binding-status")
            view = page.get_by_test_id("clinic-live-view")
            expect(status).to_contain_text("Gebunden: Entwurf A")
            expect(status).to_contain_text("Niemand spricht")
            expect(view).to_have_attribute("data-phase", "paused")
            drafts._capture(page, "bound-empty", prefix="p248-live-binding")

            other = page.context.new_page()
            switches = []
            other.on("request", lambda req: switches.append(req) if req.url.endswith("/switch") else None)
            other.goto(f"{base}/?dictation={b}#/live")
            expect(other.get_by_test_id("live-binding-status")).to_contain_text("Kein Entwurf gebunden")
            assert switches == []
            bind = other.get_by_role("button", name="Zum Diktieren binden")
            conflict = _switch(other, base, b, bind.click)
            assert conflict.status == 409
            expect(other.get_by_test_id("live-binding-status")).to_contain_text("Kein Entwurf gebunden")

            page.evaluate(MICROPHONE)
            page.get_by_role("button", name="Fortsetzen", exact=True).click()
            expect(status).to_contain_text("Aktiv spricht: Autor P215")
            drafts._capture(page, "recording", prefix="p248-live-binding")
            field = page.get_by_test_id("live-draft-text")
            field.focus()
            expect(field).to_be_focused()
            page.evaluate("syntheticMicrophone.gain.gain.value = 0.4")
            expect(view.get_by_text("Guten", exact=True)).to_be_visible()
            expect(field).to_have_value("")

            release_path = f"{base}{LIVE}/sessions/{position['session_id']}/pause"
            release = page.get_by_role("button", name="Bindung lösen")
            page.route(release_path, lambda route: route.abort()
                       if route.request.post_data_json.get("release") else route.continue_(), times=2)
            release.click()
            try:
                expect(view.get_by_text("Guten Tag", exact=True)).to_be_visible(timeout=15000)
                expect(status).to_contain_text("Bindung nicht bestätigt")
            except AssertionError as error:
                raise AssertionError(f"{frames!r} {page.responses!r}") from error
            with page.expect_response(lambda response: response.url == release_path
                                      and response.request.post_data_json.get("release") is True) as freed:
                release.click()
            assert freed.value.ok, freed.value.text()
            assert freed.value.request.post_data_json == {
                "client_id": selected.request.post_data_json["tab_id"],
                "generation": position["generation"], "release": True,
            }
            expect(status).to_contain_text("Kein Entwurf gebunden")
            expect(view.get_by_text("Guten Tag", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name=drafts.A, exact=True)).to_be_focused()
            assert page.evaluate("syntheticMicrophone.track.readyState") == "ended"
            drafts._capture(page, "released-rows", prefix="p248-live-binding", scroll_to=view.get_by_test_id("live-phrases"))

            taken = _switch(other, base, b, bind.click)
            assert taken.ok, taken.text()
            assert taken.request.post_data_json["tab_id"] != selected.request.post_data_json["tab_id"]
            expect(other.get_by_test_id("live-binding-status")).to_contain_text("Gebunden: Entwurf B")
            expect(other.get_by_test_id("clinic-live-view")).to_have_attribute("data-phase", "paused")
            assert [event["text"] for event in _history(page, base, a, position["session_id"])] == ["Guten Tag"]
            assert _history(other, base, b, taken.json()["session_id"]) == []
            assert _switch(page, base, a, page.get_by_role("button", name="Zum Diktieren binden").click).status == 409
            expect(status).to_contain_text("Kein Entwurf gebunden")
            old_switches = []
            page.on("request", lambda req: old_switches.append(req) if req.url.endswith("/switch") else None)
            page.reload()
            expect(status).to_contain_text("Kein Entwurf gebunden")
            assert old_switches == []

            # An in-window switch hands over the exact B fence only after the user's selection.
            again = _switch(other, base, a, other.get_by_role("button", name=drafts.A, exact=True).click)
            assert again.ok, again.text()
            assert again.request.post_data_json == {
                "tab_id": taken.request.post_data_json["tab_id"],
                "acknowledged_holder": {
                    "session_id": taken.json()["session_id"], "generation": taken.json()["generation"],
                    "client_id": taken.request.post_data_json["tab_id"],
                },
            }
            expect(other.get_by_test_id("live-binding-status")).to_contain_text("Gebunden: Entwurf A")
            expect(other.get_by_test_id("clinic-live-view")).to_have_attribute("data-phase", "paused")

            with other.expect_response(lambda response: response.url.endswith("/pause")
                                       and response.request.post_data_json.get("release") is True) as closed:
                other.get_by_test_id("live-draft-close").click()
            assert closed.value.ok, closed.value.text()
            expect(other.get_by_test_id("clinic-live-view")).to_have_count(0)
            expect(other.get_by_role("status").filter(has_text="Bindung gelöst")).to_be_visible()
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
