"""PAR-LIVE-008 in the real P05 shell; the loopback WAV fixture is not TTS acceptance."""

import io
import json
import os
import re
import sqlite3
import wave
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import cast
from uuid import UUID

import pytest
from playwright.sync_api import Locator, Page, Request, StorageState, expect, sync_playwright

ART = Path(__file__).resolve().parents[2] / ".wp" / "artifacts"
ROWS = "25900000-0000-4000-8000-000000000001"
BLANK = "25900000-0000-4000-8000-000000000002"
PARAGRAPHS = ("Anamnese 📝 ohne Patientendaten.", "Befund 🧪: q\u0301 und Café.", "S🩺eit Montag.")
SECOND_RUNS = ("Befund 🧪: q\u0301 und ", "Cafe\u0301.")
TEXT = "\n".join(PARAGRAPHS)
TTS = "/api/clinic/v1/tts"
MODEL = "speaches-ai/Kokoro-82M-v1.0-ONNX"
VOICE = "de_DE"
SEED = f'''
from datetime import datetime, timezone
from src.clinic import lease
from src.clinic.revisions import add_human_revision, current_revision
from src.clinic.richtext import CanonicalDocument, Node, Segment, save_document

with app.app_context():
    dic = ClinicDictation.query.one()
    author = db.session.get(User, dic.author_user_id)
    head = add_human_revision(dic, current_revision(dic), {TEXT!r}, author.id)
    now = datetime.now(timezone.utc)
    held = lease.acquire(dic.id, author.id, now)
    document = CanonicalDocument((
        Node("absatz-0", "paragraph", (Segment(id="text-0", text={PARAGRAPHS[0]!r}),)),
        Node("absatz-1", "paragraph", (Segment(id="text-1", text={SECOND_RUNS[0]!r}),
                                      Segment(id="text-1-bold", text={SECOND_RUNS[1]!r}, bold=True))),
        Node("absatz-2", "paragraph", (Segment(id="text-2", text={PARAGRAPHS[2]!r}),)),
    ))
    save_document(dic, head, document, expected_version=0, actor_user_id=author.id,
                  lease_generation=held.generation, now=now)
    lease.release(held, held.generation, now)
    rec = Recording(user_id=author.id, title="Leeres Diktat", status="COMPLETED")
    db.session.add(rec)
    db.session.flush()
    db.session.add(ClinicDictation(id="{BLANK}", recording_id=rec.id, author_user_id=author.id,
                                 pool_id=dic.pool_id, version_counter=0))
    db.session.commit()
'''


@contextmanager
def _speaches_fixture():
    speech = []
    missing_voice = Event()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        # Long enough to verify the rate change and stop without racing natural completion.
        writer.writeframes(b"\x00\x00" * 16000 * 15)
    wav = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, mime: str, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/v1/models":
                data = [{"id": MODEL}]
            elif self.path == "/v1/audio/voices":
                data = [] if missing_voice.is_set() else [{"id": VOICE, "language": "de"}]
            else:
                self.send_error(404)
                return
            self._respond("application/json", json.dumps({"data": data}).encode())

        def do_POST(self) -> None:
            if self.path != "/v1/audio/speech":
                self.send_error(404)
                return
            speech.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self._respond("audio/wav", wav)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1/audio/speech", speech, missing_voice, wav
        finally:
            server.shutdown()
            thread.join()


def _open_live(page: Page, url: str) -> tuple[Locator, dict]:
    with page.expect_response(lambda response: response.url.endswith(TTS + "/capability?details=1")) as info:
        if page.url == url:
            page.reload()
        else:
            page.goto(url)
    assert info.value.ok, info.value.status
    group = page.get_by_role("group", name="Text vorlesen", exact=True)
    expect(group).to_be_visible()
    expect(group).to_have_attribute("data-testid", "live-read-aloud")
    return group, info.value.json()


def _capture_matrix(page: Page, support, base: str) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    editor = page.get_by_role("region", name="Textformatierung")
    for state, dictation, text in (("blank", BLANK, ""), ("rows", ROWS, PARAGRAPHS[0])):
        for size, (width, height) in support.VIEWPORTS.items():
            page.set_viewport_size(dict(width=width, height=height))
            for theme in support.THEMES:
                page.evaluate(support.theme_init_script(theme))
                group, capability = _open_live(page, f"{base}/?dictation={dictation}#/live")
                assert capability["available"] is True
                expect(editor.get_by_role("textbox").first).to_have_value(text)
                if state == "rows":
                    group.get_by_text("Text zum Vorlesen markieren", exact=True).click()
                    expect(group.get_by_role("textbox", name="Gespeicherter Text")).to_be_visible()
                expect(group.get_by_role("button", name="Vorlesen", exact=True)).to_be_visible()
                expect(group.get_by_label("Stimme", exact=True)).to_be_visible()
                expect(group.get_by_label("Geschwindigkeit", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark")
                overflow = support.horizontal_overflow(page)
                assert not (overflow["document"] or overflow["body"]), (state, size, theme, overflow)
                sizes = group.locator("button, select, summary, textarea").evaluate_all("""controls => controls.map(control => {
                    const {width, height} = control.getBoundingClientRect();
                    return [width, height];
                })""")
                assert sizes and all(w >= 47.5 and h >= 47.5 for w, h in sizes), (state, size, theme, sizes)
                stem = ART / f"p259-live-{state}-{size}-{theme}"
                group.scroll_into_view_if_needed()
                if state == "rows" and height == 720:
                    # Leave the preceding edit button visible below the sticky navigation.
                    group.evaluate("""node => {
                        while (node && !node.scrollTop) node = node.parentElement;
                        if (node) node.scrollBy({top: -13, behavior: 'instant'});
                    }""")
                page.screenshot(path=stem.with_suffix(".png"), animations="disabled", caret="hide")
                dom = page.evaluate(r"""() => {
                    const root = document.documentElement.cloneNode(true);
                    const selector = '.clinic-richtext textarea, [data-testid="live-read-aloud"] :is(select, textarea)';
                    const controls = document.querySelectorAll(selector);
                    root.querySelectorAll(selector).forEach((el, i) => {
                        if (el.tagName === 'TEXTAREA') el.textContent = controls[i].value;
                        else [...el.options].forEach((opt, j) => opt.toggleAttribute('selected',
                            j === controls[i].selectedIndex));
                    });
                    root.querySelectorAll('script, meta[name="csrf-token"]').forEach(el => el.remove());
                    return '<!doctype html>\n' + root.outerHTML;
                }""")
                dom = dom.replace('href="/static/', 'href="../../static/').replace('src="/static/', 'src="../../static/')
                stem.with_suffix(".html").write_text(dom, encoding="utf-8")


def _revisions(db_path: Path) -> list[tuple[str, str, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT id, kind, body FROM clinic_transcript_revision WHERE dictation_id = ? ORDER BY id",
            (ROWS,),
        ).fetchall()


def test_live_read_aloud_uses_confirmed_unicode_offsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent / "browser"))
    monkeypatch.setenv("SPEAKR_SKIP_BACKGROUND_TASKS", "true")
    monkeypatch.setenv("TRANSCRIPTION_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("TRANSCRIPTION_API_KEY", "unused-synthetic-test-key")
    import v8_browser_support as support

    source = support.SERVER.replace("dic = ClinicDictation(", f'dic = ClinicDictation(id="{ROWS}", ', 1)
    source = source.replace("server = make_server", SEED + "\nserver = make_server", 1)
    with _speaches_fixture() as (engine, speech, missing_voice, wav):
        monkeypatch.setenv("CLINIC_TTS_ENDPOINT", engine)
        with support.serve_app(tmp_path, source) as base, sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
            author = cast(StorageState, support.login(browser, base, support.ROLES["author"]))
            page = browser.new_context(storage_state=author, viewport=dict(width=1280, height=720),
                                       **support.theme_context_options("light")).new_page()
            errors, tts_posts, saves = [], [], []
            speak_url = base + TTS + "/speak"
            correction = f"{base}/api/clinic/v1/dictations/{ROWS}/correction"
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            def observe(request: Request) -> None:
                if request.url.endswith(TTS + "/speak"):
                    tts_posts.append(request.url)
                if request.url == correction + "/save":
                    saves.append(request.url)

            page.on("request", observe)
            target = f"{base}/?dictation={ROWS}#/live"
            _open_live(page, target)
            _capture_matrix(page, support, base)
            page.set_viewport_size(dict(width=1280, height=720))
            group, capability = _open_live(page, target)
            assert capability["available"] is True
            initial_response = page.request.get(correction)
            assert initial_response.ok
            initial = initial_response.json()
            revision = initial["text_revision_id"]
            assert str(UUID(revision)) == revision
            assert initial["text"] == TEXT
            db_path = tmp_path / "v860.db"
            revisions = _revisions(db_path)
            assert (revision, "HUMAN", TEXT) in revisions

            speak = group.get_by_role("button", name="Vorlesen", exact=True)
            stop = group.get_by_role("button", name="Vorlesen stoppen", exact=True)
            voice = group.get_by_label("Stimme", exact=True)
            speed = group.get_by_label("Geschwindigkeit", exact=True)
            audio = group.locator("audio")
            voice.select_option(label="Deutsch")
            speed.select_option(label="1,3×")
            editor = page.get_by_role("region", name="Textformatierung")
            second = editor.locator('[data-run-id="text-1"]')
            third = editor.locator('[data-run-id="text-2"]')
            expect(second).not_to_be_editable()
            expect(second).to_have_value(SECOND_RUNS[0])
            expect(editor.locator('[data-run-id="text-1-bold"]')).to_have_value(SECOND_RUNS[1])
            expect(third).to_have_value(PARAGRAPHS[2])
            group.get_by_text("Text zum Vorlesen markieren", exact=True).click(timeout=3000)
            read_area = group.get_by_role("textbox", name="Gespeicherter Text")
            expect(read_area).to_have_value(TEXT)
            expect(read_area).to_have_attribute("aria-readonly", "true")
            read_area.press("x")
            expect(read_area).to_have_value(TEXT)
            end_of_second = len("\n".join(PARAGRAPHS[:2]).encode("utf-16-le")) // 2
            read_area.focus()
            read_area.evaluate("(el, end) => el.setSelectionRange(end, end)", end_of_second)
            second_start = len(PARAGRAPHS[0]) + 1
            cases = (
                (read_area, ("Shift+Home",), {"start": second_start, "end": second_start + len(PARAGRAPHS[1])},
                 PARAGRAPHS[1]),
                (third, ("Home", "ArrowRight", "ArrowRight"),
                 {"cursor": second_start + len(PARAGRAPHS[1]) + 3}, PARAGRAPHS[2][2:]),
            )
            for run, keys, offsets, material in cases:
                if "cursor" in offsets:
                    page.get_by_role("button", name="Bearbeiten", exact=True).press("Enter")
                    expect(second).to_be_editable()
                run.focus()
                for key in keys:
                    run.press(key)
                if "start" in offsets:
                    expect(run).to_have_js_property("selectionDirection", "backward")
                with page.expect_response(speak_url) as result:
                    speak.press("Enter")
                response = result.value
                assert response.request.method == "POST"
                assert response.request.post_data_json == {
                    "dictation_id": ROWS, "text_revision_id": revision, "voice": VOICE, **offsets,
                }
                assert response.ok, (response.status, response.text())
                assert response.headers["content-type"] == "audio/wav"
                assert int(response.headers['content-length']) == len(wav)
                assert speech[-1] == {"model": MODEL, "voice": VOICE, "input": material, "response_format": "wav"}
                expect(audio).to_have_js_property("paused", False)
                expect(audio).to_have_attribute('src', re.compile(r'^blob:'))
                expect(audio).to_have_js_property("playbackRate", 1.3)
                page.wait_for_function("document.querySelector('[data-testid=live-read-aloud] audio').currentTime > 0")
                speed.select_option(value="1")
                expect(audio).to_have_js_property("playbackRate", 1)
                expect(audio).to_have_js_property("paused", False)
                stop.click()
                expect(audio).to_have_js_property("paused", True)
                speed.select_option(label="1,3×")

            second.fill('Noch nicht gespeicherter Text')
            speak.click()
            expect(group.get_by_role('status')).to_have_text('Bitte Änderungen speichern oder den aktuellen Text neu laden.')
            second.fill(SECOND_RUNS[0])
            page.once('dialog', lambda dialog: dialog.accept())
            voice.select_option(label="Aus")
            expect(speak).to_be_disabled()
            expect(audio).to_have_js_property("paused", True)
            assert tts_posts == [speak_url, speak_url]
            assert len(speech) == 2
            page.get_by_role("button", name="Bearbeitung beenden", exact=True).press("Enter")
            expect(second).not_to_be_editable()

            # Refresh the inventory on the same page without substituting any browser API response.
            missing_voice.set()
            group, capability = _open_live(page, target)
            assert capability["available"] is False
            expect(group.get_by_text("Keine lokale Stimme verfügbar.", exact=True)).to_be_visible()
            expect(speak).to_be_disabled()
            assert tts_posts == [speak_url, speak_url]
            assert len(speech) == 2
            assert not saves, saves
            final_response = page.request.get(correction)
            assert final_response.ok
            final = final_response.json()
            for field in ("text_revision_id", "text", "document_version", "document"):
                assert final[field] == initial[field], field
            assert _revisions(db_path) == revisions
            assert not errors, errors
            browser.close()
