"""Mount of the clinic correction view in the normal page (w213).

PAR-COR-001, PAR-COR-002, PAR-COR-005; R-041, V4-013.

The real application is started in a child process (real Flask app, throwaway
SQLite database, synthetic users, pools, one synthetic dictation with a text
revision and an audio revision; no patient data). With CLINIC_MODE on, headless
Chromium logs in as a synthetic typist through the login form, opens ``/``,
opens the dictation from the worklist (the x34 ``open`` event), takes over the
edit lease, types a word, saves, switches the correction mode, reloads and sees
the mode kept, and closes back to the worklist. A second user without rights
gets the denied state. With CLINIC_MODE off the page carries none of it.

The derived audio route has no durable source wired yet (503), so the player
shows its unavailable note; the waveform shows its "Keine Wellenform
verfügbar" fallback because no recordingId is passed.

The browser cases need Playwright and its Chromium (requirements-dev.lock);
without them they error. Rendered-page and import-graph cases need no browser.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Synthetic-pass-213!"
TYPIST = "typist213@local.test"
FILE_AUTHOR = "files213@local.test"
STRANGER = "stranger213@local.test"
WORD = "Synthetikwort"

SERVER = r'''
import hashlib, json, math, struct, sys, uuid, wave
from pathlib import Path
from werkzeug.serving import make_server
from src.app import app, db
from src.clinic import identity, pools, workflow
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember, ClinicPoolRoute, ClinicStaffPool
from src.clinic.startup import originals_root
from src.clinic.revisions import add_asr_revision
from src.init_db import initialize_database
from src.models import Group, GroupMembership, Recording, User

port, password = int(sys.argv[1]), sys.argv[2]


def user(name, email, admin=False):
    row = User(username=name, email=email, password=identity.hash_password(password), is_admin=admin)
    db.session.add(row)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status="active", login_policy="local_only"))
    return row


with app.app_context():
    initialize_database(app)
    db.create_all()
    admin = user("a213", "admin213@local.test", admin=True)
    author, typist, stranger = user("u213", "author213@local.test"), user("t213", sys.argv[3]), user("s213", sys.argv[4])
    pool = ClinicAuthorPool(name="Autoren 213", created_by=admin.id)
    group = Group(name="Schreibdienst 213", created_by=admin.id)
    db.session.add_all([pool, group])
    db.session.flush()
    staff = ClinicStaffPool(group_id=group.id, created_by=admin.id)
    db.session.add(staff)
    db.session.flush()
    db.session.add(ClinicPoolRoute(author_pool_id=pool.id, staff_pool_id=staff.id, created_by=admin.id))
    db.session.add(GroupMembership(group_id=group.id, user_id=typist.id, role="member"))
    db.session.commit()
    # The staff link alone grants "view" (STAFF_LINK_ACTIONS). Correcting needs play, edit and release
    # (the edit lease route requires edit + release): an admin grants them for the pool.
    pools.allow(typist.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=("play", "edit", "release"))
    rec = Recording(user_id=author.id, title="Synthetisch", status="COMPLETED", original_filename="s.wav")
    db.session.add(rec)
    db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool.id, version_counter=0)
    db.session.add(dic)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dic.id, content_hash="o" * 64, size_bytes=1000,
                              storage_key="orig/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:synthetic")
    db.session.add(original)
    db.session.flush()
    add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")
    init_audio_revision(dic, original, 4000)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic.id, author_user_id=author.id, pool_id=pool.id, assignee_user_id=typist.id,
        status=workflow.TO_WRITE, audio_seconds=4))
    db.session.commit()
    dictation_id = dic.id
    if len(sys.argv) > 5:
        owner = user('files213', sys.argv[5])
        authors = ClinicAuthorPool(name='Diktatdateien', created_by=admin.id)
        db.session.add(authors)
        db.session.flush()
        db.session.add(ClinicAuthorPoolMember(pool_id=authors.id, user_id=owner.id, created_by=admin.id))
        recording = Recording(user_id=owner.id, title='Diktatdateien Test', status='COMPLETED', original_filename='ton.wav')
        db.session.add(recording)
        db.session.flush()
        draft = ClinicDictation(recording_id=recording.id, author_user_id=owner.id, pool_id=authors.id, version_counter=0)
        db.session.add(draft)
        db.session.flush()
        key = 'orig/' + uuid.uuid4().hex
        file = Path(originals_root(app), key)
        file.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(file), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b''.join(struct.pack('<h', int(4000 * math.sin(i * 2 * math.pi * 440 / 16000)))
                                     for i in range(16000)))
        data = file.read_bytes()
        original = ClinicOriginal(dictation_id=draft.id, content_hash=hashlib.sha256(data).hexdigest(),
                                  size_bytes=len(data), storage_key=key, receipt='stored', origin_ref='device:synthetic')
        db.session.add(original)
        db.session.flush()
        init_audio_revision(draft, original, 1000)
        db.session.add(workflow.ClinicWorkflowDictation(
            id=draft.id, author_user_id=owner.id, pool_id=authors.id, status=workflow.DRAFT, audio_seconds=1))
        db.session.commit()
        pools.allow(owner.id, 'own', actor_id=admin.id, actions=('export',))
server = make_server("127.0.0.1", port, app, threaded=True)
print("READY " + json.dumps({"dictation": dictation_id}), flush=True)
server.serve_forever()
'''

RENDER = r'''
import sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User
with app.app_context():
    initialize_database(app)
    u = User(username="r213", email="r213@local.test", password="x", is_admin=False)
    db.session.add(u); db.session.commit(); uid = u.id
c = app.test_client()
with c.session_transaction() as s:
    s["_user_id"] = str(uid); s["_fresh"] = True
sys.stdout.write("BODY:" + c.get("/").get_data(as_text=True))
'''


def _env(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w213.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    return env


def _render(tmp_path: Path, clinic: bool) -> str:
    done = subprocess.run([sys.executable, "-c", RENDER], cwd=ROOT, env=_env(tmp_path, clinic),
                          capture_output=True, text=True, timeout=300)
    assert "BODY:" in done.stdout, done.stderr[-3000:]
    return done.stdout.split("BODY:", 1)[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    yield from _serve(tmp_path_factory.mktemp("w213"))


@pytest.fixture(scope="module")
def file_server(tmp_path_factory):
    yield from _serve(tmp_path_factory.mktemp("p124"), FILE_AUTHOR)


def _serve(tmp, *extra):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp / "server.log"  # a file, not a pipe: nobody drains a pipe while the browser runs
    with open(log, "w") as out:
        proc = subprocess.Popen([sys.executable, "-c", SERVER, str(port), PASSWORD, TYPIST, STRANGER, *extra], cwd=ROOT,
                                env=_env(tmp, True), stdout=out, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.time() + 180
            while "READY" not in log.read_text():
                assert proc.poll() is None, "server exited before it was ready: " + log.read_text()[-3000:]
                assert time.time() < deadline, "server start timed out"
                time.sleep(0.2)
            ready = next(line for line in log.read_text().splitlines() if line.startswith("READY "))
            yield f"http://127.0.0.1:{port}", json.loads(ready[len("READY "):])["dictation"]
        finally:
            proc.terminate()
            proc.wait(timeout=20)


def _login(browser, base, email):
    page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
    page.js_errors, page.responses = [], []  # js_errors: uncaught exceptions only; refused upstream calls are not mount failures
    page.on("pageerror", lambda exc: page.js_errors.append(str(exc)))
    page.on("response", lambda r: page.responses.append(f"{r.status} {r.request.method} {r.url}") if "/api/clinic/" in r.url else None)
    page.goto(base + "/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.locator("form:has(input[name=password]) [type=submit]:visible").first.click()
    page.wait_for_load_state("load")
    assert "/login" not in page.url
    return page


def _launch(p):
    # PLAYWRIGHT_CHROMIUM_EXECUTABLE: a Chromium build other than the one Playwright expects.
    return p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)


def _open_from_worklist(page, base):
    page.goto(base + "/")
    page.locator("[data-testid=nav-clinic-worklist]").click()  # Aufnahmen is the landing view (v8-03)
    view = page.locator("[data-testid=clinic-worklist-view]")
    view.wait_for(state="visible", timeout=30000)
    row = page.locator("[data-row]").first
    row.wait_for(state="visible", timeout=30000)
    row.click()
    view.get_by_role("button", name="Öffnen", exact=True).click()
    page.locator("[data-testid=clinic-correction-view]").wait_for(state="visible", timeout=30000)
    return view


def test_typist_opens_corrects_saves_and_returns(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, TYPIST)
            worklist = _open_from_worklist(page, base)

            # The worklist is replaced by the editor; the mount is the normal page, not a test page.
            worklist.wait_for(state="detached", timeout=20000)
            assert dictation in page.locator("[data-testid=clinic-correction-view]").inner_text()
            assert page.locator("[data-testid=clinic-correction-mount]").count() == 1

            # Editing needs the lease: it starts read-only, "Bearbeiten" takes it.
            text = page.locator("[data-testid=text]")
            text.wait_for(state="visible", timeout=30000)
            assert text.input_value() == "Anamnese Husten seit drei Tagen."
            assert text.get_attribute("readonly") is not None
            page.locator("[data-action=acquire]").click()
            try:
                page.wait_for_function("!document.querySelector('[data-testid=text]').readOnly", timeout=20000)
            except Exception as exc:
                raise AssertionError(
                    f"lease not taken; bar: {page.locator('[data-testid=lease-bar]').inner_text()!r}; "
                    f"responses: {page.responses}") from exc

            # No waveform source without a recordingId: the fallback is shown, nothing breaks.
            assert "Keine Wellenform verfügbar" in page.locator("[data-testid=clinic-correction-view]").inner_text()

            # Type a word, save, see the saved state.
            text.click()
            text.press("End")
            text.type(" " + WORD)
            section = page.locator("[data-testid=save-state]")
            page.wait_for_function("document.querySelector('[data-testid=save-state]').dataset.state === 'unsaved'")
            page.locator("[data-testid=save]").click()
            page.wait_for_function("document.querySelector('[data-testid=save-state]').dataset.state === 'saved'",
                                   timeout=30000)
            assert "Gespeichert" in section.inner_text()
            stored = page.evaluate(
                "fetch('/api/clinic/v1/dictations/%s/correction', {credentials: 'same-origin'}).then(r => r.json())" % dictation)
            assert stored["text"].endswith(WORD)

            # Mode switch persists per user across a reload (x36).
            page.get_by_label("Beim Tippen weiterlaufen").check()
            page.wait_for_function(
                "document.querySelector('.clinic-correction-modes').dataset.mode === 'type_while_playing'")
            page.reload()
            page.locator("[data-testid=nav-clinic-worklist]").click()  # Aufnahmen is the landing view (v8-03)
            page.locator("[data-testid=clinic-worklist-view]").wait_for(state="visible", timeout=30000)
            _open_from_worklist(page, base)
            page.wait_for_function(
                "document.querySelector('.clinic-correction-modes')?.dataset.mode === 'type_while_playing'",
                timeout=30000)

            # "Zurück zur Arbeitsliste" is a native button, reachable by keyboard, and closes through the view.
            back = page.locator("[data-testid=clinic-correction-back]")
            assert back.inner_text().strip() == "Zurück zur Arbeitsliste"
            back.focus()
            page.keyboard.press("Enter")
            page.locator("[data-testid=clinic-worklist-view]").wait_for(state="visible", timeout=20000)
            assert page.locator("[data-testid=clinic-correction-view]").count() == 0

            # W16: no patient data in the page.
            assert "patient" not in page.locator("main").inner_text().lower()
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_user_without_rights_gets_denied_state(live_server):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, STRANGER)
            page.goto(base + "/")
            page.locator("[data-testid=nav-clinic-worklist]").click()  # Aufnahmen is the landing view (v8-03)
            page.locator("[data-testid=clinic-worklist-view]").wait_for(state="visible", timeout=30000)
            assert page.locator("[data-row]").count() == 0  # the worklist offers no row to open
            # The stranger cannot pick the row, so the page handler of the worklist's open event is
            # driven directly with the dictation id; the server decides.
            page.evaluate(
                "id => document.getElementById('app').__vue_app__._instance.proxy"
                ".openClinicDictation({ dictationId: id, mode: 'correction' })", dictation)
            alert = page.locator("[data-testid=load-error]")
            alert.wait_for(state="visible", timeout=30000)
            assert alert.inner_text().strip()
            assert page.locator("[data-testid=text]").count() == 0
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def test_clinic_page_declares_correction_mount(tmp_path):
    out = _render(tmp_path, True)
    assert "<clinic-correction-view" in out
    assert "Zurück zur Arbeitsliste" in out
    assert "@open=\"openClinicDictation\"" in out


def test_clinic_mode_off_page_is_unchanged(tmp_path):
    out = _render(tmp_path, False)
    assert 'data-clinic-mode="False"' in out
    assert "clinic-correction" not in out
    assert "Zurück zur Arbeitsliste" not in out


def test_import_graph_is_clinic_gated_and_dynamic():
    src = (ROOT / "static/js/app.modular.js").read_text(encoding="utf-8")
    assert not re.search(r"^import .*clinic/", src, re.M), "clinic code must not be a static import"
    assert "import('./clinic/compose-correction.js')" in src
    assert "registerCorrectionView(app)" in src
    assert src.count("createClinicApiClient()") == 1, "one shared api-client"
    assert src.count("ref(clinicWorklist ? clinicViewFromHash() : null)") == 1


def test_dictation_files_round_trip(file_server, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    base, _ = file_server
    artifacts = ROOT / '.wp' / 'artifacts'
    with sync_playwright() as playwright, _launch(playwright) as browser:
        page = _login(browser, base, FILE_AUTHOR)
        page.get_by_test_id('clinic-recordings-view').get_by_test_id('open-link').first.click()
        expect(page).to_have_url(re.compile(r'#/diktat$'))
        editor = page.get_by_test_id('clinic-correction-view')
        files = editor.get_by_test_id('session-files')
        file_menu = files.get_by_text('Diktatdateien', exact=True)
        file_menu.focus()
        page.keyboard.press('Enter')
        expect(files).to_have_attribute('open', '')
        text = editor.get_by_test_id('text')
        audio = editor.locator('audio')
        editor.locator('[data-action=acquire]').click()
        expect(text).to_be_editable()
        expect(text).to_have_value('')
        status = editor.get_by_test_id('session-file-status')
        picker = editor.get_by_test_id('session-file-input')
        page.on('dialog', lambda dialog: dialog.accept())

        def capture(state):
            if not artifacts.is_dir():
                return
            for theme in ('light', 'dark'):
                page.evaluate("theme => { document.documentElement.classList.toggle('dark', theme === 'dark');"
                              "document.documentElement.dataset.theme = theme; }", theme)
                for width, height in ((1440, 900), (1280, 720), (390, 844)):
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate("() => document.querySelector('[data-testid=clinic-correction-mount]').scrollTop = 0")
                    if width < 1440:
                        files.evaluate("element => element.scrollIntoView({ block: 'start' })")
                    assert files.locator('summary, button').evaluate_all(
                        "controls => controls.every(control => {"
                        " const r = control.getBoundingClientRect();"
                        " return r.height >= 48 && r.left >= 0 && r.right <= innerWidth"
                        " && r.top >= 0 && r.bottom <= innerHeight; })"
                    )
                    name = f'p124-{state}-{theme}-{width}x{height}'
                    page.screenshot(path=artifacts / f'{name}.png')
                    if width == 390:
                        html = page.evaluate("() => { const clone = document.documentElement.cloneNode(true);"
                                             "clone.querySelectorAll('script,meta[name=csrf-token],audio').forEach(n => n.remove());"
                                             "clone.querySelector('[data-testid=text]').textContent = document.querySelector('[data-testid=text]').value;"
                                             "clone.querySelectorAll('link[href]').forEach(link => {"
                                             "if (link.getAttribute('href').startsWith('/static/css/'))"
                                             "link.href = '../../' + link.getAttribute('href').slice(1); else link.remove(); });"
                                             "return '<!doctype html>' + clone.outerHTML; }")
                        (artifacts / f'{name}.html').write_text(html, encoding='utf-8')

        def download(kind):
            path = tmp_path / f'diktat.{kind}'
            with page.expect_download() as downloaded:
                files.locator(f'[data-session-format={kind}]').click()
                expect(status).to_contain_text(f'Download gestartet: diktat.{kind}', timeout=10000)
            downloaded.value.save_as(path)
            return path

        capture('empty')
        original_src = audio.get_attribute('src')
        assert original_src
        text.fill('Synthetischer Befund: Übung.')
        saved = download('sdict')
        capture('with-text')
        with page.expect_file_chooser() as chooser:
            files.get_by_role('button', name='Datei ins Diktat laden …').click()
        assert chooser.value.element.get_attribute('accept') == '.sdict,.rtf,.txt'
        rtf = download('rtf')
        plain = download('txt')
        assert plain.read_text(encoding='utf-8') == 'Synthetischer Befund: Übung.'
        assert rtf.read_bytes().startswith(b'{\\rtf1')
        text.fill('Geänderter Text')
        picker.set_input_files({'name': 'ungueltig.sdict', 'mimeType': 'application/zip', 'buffer': b'invalid'})
        expect(status).to_contain_text('Die Datei wurde nicht geöffnet.')
        expect(text).to_have_value('Geänderter Text')
        expect(audio).to_have_attribute('src', original_src)
        picker.set_input_files(saved)
        expect(status).to_contain_text('Diktatdatei mit Text und Ton geöffnet.')
        expect(text).to_have_value('Synthetischer Befund: Übung.')
        assert audio.get_attribute('src') != original_src
        audio.evaluate('audio => audio.play()')
        page.wait_for_function("document.querySelector('[data-testid=player-bar] audio').currentTime > 0")
        imported_src = audio.get_attribute('src')
        for path in (rtf, plain):
            text.fill('Erneut geändert')
            picker.set_input_files(path)
            expect(status).to_contain_text('Die Datei enthielt keinen Ton.')
            expect(text).to_have_value('Synthetischer Befund: Übung.')
            expect(audio).to_have_attribute('src', imported_src)
        assert not page.js_errors, page.js_errors


def test_context_drawer_returns_focus_to_each_trigger(live_server):
    from playwright.sync_api import expect, sync_playwright

    base, _ = live_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, TYPIST)
            _open_from_worklist(page, base)
            page.set_viewport_size({"width": 390, "height": 844})
            editor = page.get_by_test_id("clinic-correction-view")
            drawer = editor.get_by_test_id("context-drawer")
            close = drawer.get_by_test_id("drawer-close")
            player = editor.get_by_test_id("player-bar")
            expect(editor.get_by_role("group", name="Wiedergabe", exact=True)).to_have_count(1)
            expect(player.get_by_role("group", name="Wiedergabe", exact=True)).to_be_visible()
            audio_node = player.locator("audio").element_handle()
            pedal = editor.get_by_test_id("pedal-status")
            expect(pedal).to_be_visible()
            assert not pedal.get_attribute("aria-label")
            expect(pedal.locator("..")).to_have_role("status")
            for section in ("assignment", "marks", "options"):
                trigger = editor.locator(f'[data-context-toggle="{section}"]')
                for via_escape in (True, False):
                    expect(trigger).to_be_enabled()
                    trigger.focus()
                    page.keyboard.press("Enter")
                    expect(close).to_be_focused()
                    expect(drawer).to_have_accessible_name(trigger.get_attribute("aria-label"))
                    expect(trigger).to_have_attribute("aria-expanded", "true")
                    if via_escape:
                        page.keyboard.press("Escape")
                    else:
                        close.click()
                    expect(trigger).to_be_focused()
                    expect(trigger).to_have_attribute("aria-expanded", "false")
                    expect(drawer).to_have_attribute("inert", "")
                    assert player.locator("audio").evaluate("(element, original) => element === original", audio_node)
        finally:
            browser.close()
