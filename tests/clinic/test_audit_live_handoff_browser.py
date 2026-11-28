"""Live dictation handoff in Chromium, with Flask handling all clinic API calls."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Iterator
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Thread
from uuid import uuid4
from wave import open as open_wav

import pytest
from flask import Flask, current_app, jsonify
from flask_login import LoginManager, current_user, login_required, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from playwright.sync_api import Browser, Page, expect, sync_playwright
from werkzeug.serving import make_server

from src.app import register_clinic_audio, register_clinic_resources, register_clinic_worklist
from src.clinic import live_session, pools, revisions, startup, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.ingest import IngestService
from src.clinic.live_commit import commit_asr_phrase
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import Group, GroupMembership, Recording, User
from src.tasks.processing import _handle_late_asr
from test_audit_live_recovery_api import _wav
from test_audit_live_session_browser import ART, HTML, ROOT, _theme
from test_wp_mount_correction import _launch

PREFIX = '/api/clinic/v1'
VIEWPORTS = ((1440, 900), (1280, 720), (390, 844))
PHRASES = ('Erster bestätigter Satz.', 'Zweiter bestätigter Satz.')
CORRECTION = 'Erster Satz, von der Autorin korrigiert.'
LATE = 'Verspätetes synthetisches ASR-Ergebnis.'
MOUNT = HTML.split('window.calls = [];', 1)[0] + '''
import { createClinicApiClient } from '/static/js/clinic/api-client.js';
import { CorrectionEditor } from '/static/js/clinic/correction-editor-ui.js';
const query = new URLSearchParams(location.search);
Vue.createApp(query.get('view') === 'correction' ? CorrectionEditor : LiveView, {
    client: { ...createClinicApiClient(), supportsLiveCapture: false },
    dictationId: query.get('dictation'), currentUser: { id: __ACTOR__ }, vue: Vue,
}).mount('#app');
</script></body></html>'''


@pytest.fixture
def handoff_server(clinic_on, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[
    tuple[Flask, str, tuple[int, int, int], str, str]
]:
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path / 'originals'))
    app = Flask(__name__, static_folder=str(ROOT / 'static'), static_url_path='/static')
    app.config.update(TESTING=True, SECRET_KEY=uuid4().hex, SESSION_PROTECTION=None,
                      SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "handoff.db"}')
    db.init_app(app)
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    CSRFProtect(app)
    register_clinic_resources(app)
    register_clinic_worklist(app)
    register_clinic_audio(app)

    @app.get('/login/<int:uid>')
    def sign_in(uid: int):
        login_user(db.session.get(User, uid))
        return jsonify(csrf_token=generate_csrf())

    @app.get('/api/csrf-token')
    @login_required
    def csrf_token():
        return jsonify(csrf_token=generate_csrf())

    @app.get('/live-handoff-proof')
    @login_required
    def mount() -> str:
        return MOUNT.replace('__ACTOR__', str(current_user.id))

    with app.app_context():
        db.create_all()
        users = [User(username=name, email=f'{name}@example.invalid', password='synthetic', is_admin=name == 'admin')
                 for name in ('author', 'lead', 'typist', 'admin')]
        db.session.add_all(users)
        db.session.flush()
        author, lead, typist, admin = (user.id for user in users)
        pool = pools.ClinicAuthorPool(name='Autorenteam', created_by=author)
        group = Group(name='Schreibdienst', created_by=author)
        db.session.add_all([pool, group, *(ClinicAccountPolicy(user_id=user.id, status='active')
                                           for user in users)])
        db.session.flush()
        staff = pools.ClinicStaffPool(group_id=group.id, created_by=author)
        db.session.add(staff)
        db.session.flush()
        db.session.add_all([
            GroupMembership(group_id=group.id, user_id=lead, role='admin'),
            GroupMembership(group_id=group.id, user_id=typist, role='member'),
            pools.ClinicPoolRoute(author_pool_id=pool.id, staff_pool_id=staff.id, created_by=author),
        ])

        def draft(title: str) -> str:
            recording = Recording(user_id=author, title=title, status='COMPLETED', original_filename='live.wav')
            db.session.add(recording)
            db.session.flush()
            item = workflow.start(db.session(), 'create', author_user_id=author, pool_id=pool.id)
            db.session.add(ClinicDictation(id=item.id, recording_id=recording.id,
                                           author_user_id=author, pool_id=pool.id))
            return item.id

        empty, populated = draft('Leeres Live-Diktat'), draft('Live-Diktat zur Übergabe')
        db.session.commit()
        pools.allow(typist, 'pool', actor_id=admin, pool_id=pool.id, actions=('play',))

    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, f'http://127.0.0.1:{server.server_port}', (author, lead, typist), empty, populated
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        with app.app_context():
            db.engine.dispose()


def _seed_phrases(dictation: str, author: int) -> None:
    # Enter at the ASR commit boundary; this test does not require a microphone or recognizer.
    now = datetime.now(timezone.utc)
    session_id = live_session.start(dictation, author, now).id
    db.session.commit()
    store = IngestService(startup.originals_root(current_app))
    receipt = None
    for index, text in enumerate(PHRASES, 1):
        receipt = commit_asr_phrase(
            store, dictation_id=dictation, session_id=session_id, user_id=author, client_id=None,
            now=now, generation=1, sequence=index, event_id=f'phrase-{index}',
            audio_start_ms=(index - 1) * 10, audio_end_ms=index * 10, audio=_wav(index),
            expected_audio_revision_id=receipt.audio_revision_id if receipt else None,
            expected_text_revision_id=receipt.transcript_revision_id if receipt else None,
            text=text, model_ref='local-test',
        )


def _open(browser: Browser, base: str, actor: int) -> tuple[Page, str, list[str]]:
    page = browser.new_context().new_page()
    errors: list[str] = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    response = page.request.get(f'{base}/login/{actor}')
    assert response.ok, response.text()
    return page, response.json()['csrf_token'], errors


def _capture(page: Page, label: str, clips: tuple[bytes, ...] = ()) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    # Use the shared clinic styles; generated upstream Tailwind is not needed for this mount.
    assert page.locator('link[href$="clinic-v8.css"]').evaluate('link => link.sheet?.cssRules.length > 0')
    sources = ['data:audio/wav;base64,' + b64encode(clip).decode('ascii') for clip in clips]
    for width, height in VIEWPORTS:
        page.set_viewport_size({'width': width, 'height': height})
        for theme in ('light', 'dark'):
            _theme(page, theme)
            page.evaluate('scrollTo(0, 0)')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (label, width, theme)
            stem = ART / f'p231-{label}-{width}x{height}-{theme}'
            page.screenshot(path=stem.with_suffix('.png'), full_page=True, animations='disabled')
            html = page.evaluate(r'''sources => {
                const snapshot = document.documentElement.cloneNode(true);
                snapshot.querySelectorAll('script, meta[name="csrf-token"]').forEach(el => el.remove());
                const links = document.querySelectorAll('link[rel=stylesheet]');
                snapshot.querySelectorAll('link[rel=stylesheet]').forEach((link, i) => {
                    const style = document.createElement('style');
                    style.textContent = Array.from(links[i].sheet?.cssRules || [])
                        .filter(rule => rule.type !== CSSRule.IMPORT_RULE).map(rule => rule.cssText).join('\n');
                    link.replaceWith(style);
                });
                const fields = document.querySelectorAll('textarea');
                snapshot.querySelectorAll('textarea').forEach((field, i) => field.textContent = fields[i].value);
                snapshot.querySelectorAll('audio').forEach((audio, i) => audio.src = sources[i]);
                return '<!doctype html>' + snapshot.outerHTML;
            }''', sources)
            stem.with_suffix('.html').write_text(html, encoding='utf-8')


def test_empty_live_draft_cannot_be_submitted(handoff_server) -> None:
    _, base, (author, _, _), empty, _ = handoff_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page, _, errors = _open(browser, base, author)
        assert page.goto(f'{base}/live-handoff-proof?dictation={empty}').ok
        expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'idle')
        expect(page.get_by_test_id('live-phrases').locator('li')).to_have_count(0)
        expect(page.get_by_role('button', name='Bearbeiten', exact=True)).to_be_visible()
        submit = page.get_by_role('button', name='Diktat einreichen', exact=True)
        if submit.count():
            expect(submit).to_be_disabled()
        expect(page.get_by_role('button', name='Jetzt einreichen', exact=True)).to_have_count(0)
        _capture(page, 'empty')
        assert not errors, errors
        browser.close()


def test_stopped_live_draft_handoff_preserves_human_text_and_audio(handoff_server) -> None:
    app, base, (author, lead, typist), _, dictation = handoff_server
    scope = f'{base}{PREFIX}/dictations/{dictation}'
    corrected_text = '\n'.join((CORRECTION, PHRASES[1]))
    with app.app_context():
        _seed_phrases(dictation, author)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page, csrf, errors = _open(browser, base, author)
        assert page.goto(f'{base}/live-handoff-proof?dictation={dictation}').ok
        rows = page.get_by_test_id('live-phrases').locator('li[data-state="server_saved"]')
        expect(rows).to_have_count(len(PHRASES))
        for index, phrase in enumerate(PHRASES):
            expect(rows.nth(index).get_by_text(phrase, exact=True)).to_be_visible()
        detail = page.request.get(f'{scope}/detail').json()
        send_headers = {'X-CSRF-Token': csrf, 'If-Match': detail['etag'], 'Idempotency-Key': str(uuid4())}
        refused = page.request.post(f'{scope}/send', data={}, headers=send_headers)
        assert refused.status == 409 and refused.json()['code'] == 'LIVE_SESSION_ACTIVE'
        page.get_by_role('button', name='Beenden', exact=True).click()
        expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'stopped')

        page.get_by_role('button', name='Bearbeiten', exact=True).click()
        field = page.locator('.clinic-richtext textarea').first
        expect(field).to_be_editable()
        expect(field).to_have_value(PHRASES[0])
        submit = page.get_by_role('button', name='Diktat einreichen', exact=True)
        expect(submit).to_be_enabled()
        field.press('Control+b')
        expect(submit).to_be_disabled()
        field.fill(CORRECTION)
        with page.expect_response(f'{scope}/correction/save') as saved:
            page.get_by_role('button', name='Speichern', exact=True).click()
        assert saved.value.ok, saved.value.text()
        human_id = saved.value.json()['text_revision_id']
        assert saved.value.json()['document']['nodes'][0]['runs'][0]['bold'] is True
        expect(page.get_by_text('Gespeichert.', exact=True)).to_be_visible()

        with app.app_context():
            draft = db.session.get(ClinicDictation, dictation)
            recording = db.session.get(Recording, draft.recording_id)
            prior = {'transcription': recording.transcription}
            recording.transcription = LATE
            _handle_late_asr(recording, prior, 'local-test')
            db.session.commit()
            head = revisions.current_revision(draft)
            assert (head.id, head.kind, head.body) == (human_id, revisions.KIND_HUMAN, corrected_text)
            assert any(row.kind == revisions.KIND_ASR and row.body == LATE for row in revisions.history(draft))
            assert recording.transcription == corrected_text

        _capture(page, 'rows', tuple(_wav(i) for i in range(1, len(PHRASES) + 1)))
        detail = page.request.get(f'{scope}/detail').json()
        snapshot = saved.value.json()
        stale = page.request.post(f'{scope}/send', data={'live_revision': {
            'text_revision_id': human_id, 'audio_revision_id': detail['binding']['audioRevisionId'],
            'document_version': snapshot['document_version'] - 1, 'lease_generation': None,
        }}, headers={**send_headers, 'If-Match': detail['etag'], 'Idempotency-Key': str(uuid4())})
        assert stale.status == 409 and stale.json()['code'] == 'STALE_REVISION'

        sends: list[str] = []
        page.on('request', lambda req: sends.append(req.method) if req.url == f'{scope}/send' else None)
        submit.click()
        confirm = page.get_by_role('button', name='Jetzt einreichen', exact=True)
        expect(confirm).to_be_visible()
        assert sends == []
        _capture(page, 'confirm', tuple(_wav(i) for i in range(1, len(PHRASES) + 1)))
        first_attempt = []

        def lose_receipt(route):
            request = route.request
            first_attempt.append((request.headers['idempotency-key'], request.headers['if-match'], request.post_data_json))
            assert route.fetch().ok
            route.abort()

        page.route(f'{scope}/send', lose_receipt, times=1)
        confirm.click()
        expect(page.get_by_text('Das Einreichen ist noch nicht bestätigt.', exact=False)).to_be_visible()
        expect(field).to_have_value(CORRECTION)
        expect(rows.locator('audio')).to_have_count(len(PHRASES))
        retry = page.get_by_role('button', name='Erneut versuchen', exact=True).first
        with page.expect_response(f'{scope}/send') as sent:
            retry.click()
        assert sent.value.ok, sent.value.text()
        replay = sent.value.request
        assert first_attempt == [(replay.headers['idempotency-key'], replay.headers['if-match'], replay.post_data_json)]
        expect(page.get_by_text('Diktat eingereicht.', exact=True)).to_be_visible()
        assert sends == ['POST', 'POST']
        sent_data = sent.value.json()
        assert sent_data['status'] == workflow.TO_CORRECT
        assert not errors, errors

        lead_page, token, _ = _open(browser, base, lead)
        candidates = lead_page.request.get(f'{scope}/assignment/candidates')
        assert candidates.ok, candidates.text()
        assert typist in {candidate['id'] for candidate in candidates.json()}
        assigned = lead_page.request.post(f'{scope}/assignment', data={'assignee_user_id': typist}, headers={
            'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4()), 'If-Match': sent_data['etag'],
        })
        assert assigned.ok, assigned.text()
        assert assigned.json()['assignee_user_id'] == typist

        typist_page, _, errors = _open(browser, base, typist)
        assert typist_page.goto(f'{base}/live-handoff-proof?dictation={dictation}&view=correction').ok
        expect(typist_page.locator('.clinic-correction textarea')).to_have_value(corrected_text)
        player = typist_page.locator('.clinic-correction audio[controls]')
        expect(player).to_be_visible()
        audio = typist_page.request.get(player.evaluate('el => el.src'))
        assert audio.ok, audio.text() if not audio.ok else ''
        assert audio.headers['content-type'] == 'audio/wav'
        with open_wav(BytesIO(audio.body()), 'rb') as received:
            assert received.getnframes() / received.getframerate() == 0.02
        player.evaluate('el => el.play()')
        typist_page.wait_for_function('document.querySelector("audio")?.ended === true')
        assert not errors, errors
        browser.close()
