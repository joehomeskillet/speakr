"""Local synthetic proof through normal Flask ingress and the mounted editor."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from test_wp_mount_correction import PASSWORD, _env, _launch, _login

ROOT = Path(__file__).resolve().parents[2]
OWNER = "manual-first@local.test"
TEXT = "Synthetische manuelle Erstfassung."

SETUP = r'''
import hashlib, io, json, sys, uuid, wave
from pathlib import Path
from src.app import app, db
from src.clinic import identity, pools, workflow
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.correction import ClinicCorrectionState
from src.clinic.parity_receipts import ClinicParityReceipt
from src.clinic.alignment_tokens import ClinicTranscriptTokens
from src.init_db import initialize_database
from src.models import AuditEvent, Recording, User

app.testing = True
with app.app_context():
    initialize_database(app)
    db.create_all()
    owner = User(username="manual-first", email="manual-first@local.test",
                 password=identity.hash_password(sys.argv[1]))
    outsider = User(username="outsider-first", email="outsider-first@local.test", password="unused")
    admin = User(username="admin-first", email="admin-first@local.test", password="unused", is_admin=True)
    db.session.add_all([owner, outsider, admin])
    db.session.flush()
    uid, other = owner.id, outsider.id
    for user in (owner, outsider, admin):
        db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active", login_policy="local_only"))
    db.session.commit()
    pools.allow(uid, "own", actor_id=admin.id, actions=["view", "play", "edit", "release"])
    pool_id = pools.create_author_pool("Manual first authors", actor_id=admin.id).id
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    audio_bytes = stream.getvalue()
    digest = hashlib.sha256(audio_bytes).hexdigest()

def seed(count=1, audio=False):
    with app.app_context():
        rec = Recording(user_id=uid, title="Manuell ohne ASR", status="FAILED", original_filename="synthetic.wav")
        db.session.add(rec)
        db.session.flush()
        dic = ClinicDictation(recording_id=rec.id, author_user_id=uid, pool_id=pool_id)
        db.session.add(dic)
        db.session.flush()
        for n in range(count):
            key = f"synthetic/{uuid.uuid4()}.wav"
            path = Path(app.config['UPLOAD_FOLDER']) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio_bytes)
            original = ClinicOriginal(dictation_id=dic.id, content_hash=digest,
                size_bytes=len(audio_bytes), storage_key=key,
                receipt=json.dumps({'sha256': digest, 'size': len(audio_bytes)}), origin_ref="device:synthetic")
            db.session.add(original)
            db.session.flush()
            if audio and n == 0:
                init_audio_revision(dic, original, 1000)
        db.session.add(workflow.ClinicWorkflowDictation(
            id=dic.id, author_user_id=uid, pool_id=pool_id, status=workflow.DRAFT))
        db.session.commit()
        return dic.id

def client_for(actor):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'], session['_fresh'] = str(actor), True
    token = client.get('/api/csrf-token').json['csrf_token']
    return client, {'X-CSRF-Token': token}

client, csrf = client_for(uid)

def acquire(did):
    response = client.post(f'/api/clinic/v1/dictations/{did}/leases', headers={
        **csrf, 'Idempotency-Key': str(uuid.uuid4())})
    assert response.status_code == 200, response.json
    return response.json['generation']

def read(did):
    response = client.get(f'/api/clinic/v1/dictations/{did}/correction')
    assert response.status_code == 200, response.json
    assert response.headers['ETag'] == response.json['etag']
    return response.json

def save(did, snap, generation, text="Synthetische manuelle Erstfassung.", *, key=None, headers=None, **changes):
    payload = dict(text=text, audio_position_ms=0, lease_generation=generation,
                   expected_text_revision_id=snap['text_revision_id'],
                   expected_state_version=snap['state']['version'] if snap['state'] else None)
    payload.update(changes)
    return client.post(f'/api/clinic/v1/dictations/{did}/correction/save', json=payload,
        headers=headers if headers is not None else {
            **csrf, 'If-Match': snap['etag'], 'Idempotency-Key': key or str(uuid.uuid4())})

def snapshot(did):
    with app.app_context():
        originals = ClinicOriginal.query.filter_by(dictation_id=did).all()
        return {
            'originals': [(o.id, o.content_hash, o.size_bytes, o.storage_key, o.receipt,
                           hashlib.sha256((Path(app.config['UPLOAD_FOLDER']) / o.storage_key).read_bytes()).hexdigest())
                          for o in originals],
            'revisions': [(r.id, r.parent_revision_id, r.body, r.original_version_id,
                           r.kind, r.revision_author_user_id, r.normalised_text_hash)
                          for r in ClinicTranscriptRevision.query.filter_by(dictation_id=did).order_by(
                              ClinicTranscriptRevision.created_at).all()],
            'states': [(s.text_revision_id, s.version) for s in ClinicCorrectionState.query.filter_by(dictation_id=did)],
            'tokens': ClinicTranscriptTokens.query.filter_by(dictation_id=did).count(),
            'receipts': ClinicParityReceipt.query.count(),
            'version': db.session.get(ClinicDictation, did).version_counter,
            'audit': AuditEvent.query.count(),
        }
'''

CHECK = r'''
did = seed(audio=sys.argv[2] == 'audio')
generation = acquire(did)
before = snapshot(did)
empty = read(did)
assert empty['text'] == '' and empty['text_revision_id'] is None and empty['state'] is None
assert empty['etag'] == f'"none.g{generation}"'
assert read(did) == empty and snapshot(did) == before  # Reads never invent text.
key = str(uuid.uuid4())
first = save(did, empty, generation, key=key)
assert first.status_code == 200, (first.status_code, first.json)
stored = snapshot(did)
rid = first.json['text_revision_id']
assert stored['originals'] == before['originals']
assert stored['revisions'] == [(rid, None, 'Synthetische manuelle Erstfassung.', before['originals'][0][0],
                                 'HUMAN', uid, hashlib.sha256('Synthetische manuelle Erstfassung.'.encode()).hexdigest())]
assert stored['states'] == [(rid, 1)] and stored['tokens'] == 1
assert stored['receipts'] == before['receipts'] + 1
assert save(did, empty, generation, key=key).json == first.json
assert snapshot(did) == stored
assert save(did, empty, generation, text='Stale').status_code == 409
assert snapshot(did) == stored
head = read(did)
assert head['text_revision_id'] == rid and head['etag'] == first.json['etag']
for changes, code in [
    ({'lease_generation': generation + 1}, 'LEASE_STALE'),
    ({'expected_text_revision_id': None}, 'REVISION_CONFLICT'),
    ({'expected_state_version': 9}, 'REVISION_CONFLICT'),
]:
    refused = save(did, head, generation, text='Refused', **changes)
    assert (refused.status_code, refused.json['code']) == (409, code), refused.json
    assert snapshot(did) == stored
assert save(did, head, generation, headers={**csrf, 'Idempotency-Key': str(uuid.uuid4())}).status_code == 428
assert save(did, head, generation, headers={'If-Match': head['etag'],
    'Idempotency-Key': str(uuid.uuid4())}).status_code == 400  # App-wide CSRF handler runs first.
stranger, stranger_csrf = client_for(other)
assert stranger.post(f'/api/clinic/v1/dictations/{did}/correction/save',
    headers={**stranger_csrf, 'If-Match': head['etag'], 'Idempotency-Key': str(uuid.uuid4())},
    json=dict(text='Forbidden', audio_position_ms=0, lease_generation=generation,
              expected_text_revision_id=rid)).status_code == 404
assert snapshot(did) == stored
second = save(did, head, generation, text='Zweite manuelle Fassung.')
assert second.status_code == 200, second.json
after = snapshot(did)
assert len(after['revisions']) == 2 and after['revisions'][0] == stored['revisions'][0]
assert after['revisions'][1][1:4] == (rid, 'Zweite manuelle Fassung.', before['originals'][0][0])
assert after['states'] == [(second.json['text_revision_id'], 2)] and after['tokens'] == 2
assert after['originals'] == before['originals']
assert save(did, head, generation, text='Stale second').status_code == 409
assert snapshot(did) == after

for count, code in [(0, 'ORIGINAL_MISSING'), (2, 'ORIGINAL_AMBIGUOUS')]:
    target = seed(count, audio=True)
    gen = acquire(target)
    initial = read(target)
    unchanged = snapshot(target)
    refused = save(target, initial, gen)
    assert (refused.status_code, refused.json['code']) == (409, code), refused.json
    assert snapshot(target) == unchanged

# A failure after revision/state flush must leave no partial first document.
from unittest.mock import patch
target = seed()
gen = acquire(target)
initial = read(target)
unchanged = snapshot(target)
with patch('src.clinic.routes_correction.at.tokens_for', side_effect=RuntimeError('synthetic failure')):
    failed = save(target, initial, gen)
assert (failed.status_code, failed.json['code']) == (503, 'DEPENDENCY_UNAVAILABLE')
assert snapshot(target) == unchanged
assert save(target, initial, gen).status_code == 200

# Acceptance resubmission and its fail-closed audit remain in the save transaction.
target = seed()
gen = acquire(target)
with app.app_context():
    db.session.get(workflow.ClinicWorkflowDictation, target).status = workflow.ACCEPTED
    db.session.commit()
initial = read(target)
unchanged = snapshot(target)
with patch('src.clinic.correction_transaction.record_in_tx', return_value=None):
    failed = save(target, initial, gen)
assert failed.status_code == 503
assert snapshot(target) == unchanged
with app.app_context():
    assert db.session.get(workflow.ClinicWorkflowDictation, target).status == workflow.ACCEPTED
assert save(target, initial, gen).status_code == 200
with app.app_context():
    assert db.session.get(workflow.ClinicWorkflowDictation, target).status == workflow.TO_ACCEPT
    rec = db.session.get(ClinicDictation, target).recording_id
    events = AuditEvent.query.filter_by(recording_id=rec, event='recording.edited').all()
    assert any('workflow_acceptance_resubmitted' in e.details.get('fields', []) for e in events)
    assert 'Synthetische manuelle Erstfassung.' not in repr([e.details for e in events])
print('PASS: first/second save, SHA, read purity, ETag, lease, CSRF, ACL, original refusal, rollback and audit')
'''


@pytest.mark.parametrize("source", ["header", "audio"])
def test_normal_save_ingress(tmp_path, source):
    result = subprocess.run([sys.executable, "-c", SETUP + CHECK, PASSWORD, source],
                            cwd=ROOT, env=_env(tmp_path, True), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-5000:]
    print(result.stdout.split('PASS:')[-1])


@pytest.fixture
def manual_server(tmp_path, request):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = SETUP + '\ndid = seed(audio=True)\n'
    if getattr(request, 'param', None) == 'asr-wait':
        server += r'''
from src.models import ProcessingJob
with app.app_context():
    dic = db.session.get(ClinicDictation, did)
    db.session.get(Recording, dic.recording_id).status = 'PROCESSING'
    db.session.get(workflow.ClinicWorkflowDictation, did).status = workflow.PREP_ONLINE
    db.session.add(ProcessingJob(user_id=uid, recording_id=dic.recording_id,
                                 job_type='transcribe', status='processing'))
    db.session.commit()
'''
    server += r'''
from werkzeug.serving import make_server
server = make_server('127.0.0.1', int(sys.argv[2]), app, threaded=True)
print('READY ' + json.dumps({'dictation': did}), flush=True)
server.serve_forever()
'''
    log = tmp_path / "server.log"
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, "-c", server, PASSWORD, str(port)], cwd=ROOT,
                                env=_env(tmp_path, True), stdout=out, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while not any(line.startswith("READY ") for line in log.read_text().splitlines()):
                assert proc.poll() is None, log.read_text()[-4000:]
                assert time.monotonic() < deadline, "local server start timed out"
                time.sleep(0.2)
            ready = next(line for line in log.read_text().splitlines() if line.startswith("READY "))
            yield f"http://127.0.0.1:{port}", json.loads(ready[6:])["dictation"]
        finally:
            proc.terminate()
            proc.wait(timeout=20)


def _open_from_worklist(page, base):
    page.goto(base + "/")
    page.get_by_test_id("nav-clinic-worklist").click()
    page.get_by_test_id("clinic-worklist-view").locator("[data-row]").first.dblclick()
    details = page.get_by_test_id("clinic-detail-view")
    details.get_by_role("button", name="Im Schreibplatz öffnen", exact=True).click()
    page.get_by_test_id("clinic-correction-view").wait_for(state="visible", timeout=30000)


def test_signed_in_owner_types_saves_and_reopens(manual_server):
    from playwright.sync_api import expect, sync_playwright

    base, did = manual_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, OWNER)
            _open_from_worklist(page, base)
            text = page.locator('[data-testid=text]')
            expect(text).to_have_value('')
            _asr_wait_artifacts(page, 'baseline')
            page.locator('[data-action=acquire]').click()
            expect(text).to_be_editable()
            text.press_sequentially(TEXT)
            with page.expect_response(lambda r: r.url.endswith('/correction/save') and r.request.method == 'POST') as response:
                page.locator('[data-testid=save]').click()
            assert response.value.status == 200, response.value.text()
            saved = response.value.json()
            expect(page.locator('[data-testid=save-state]')).to_have_attribute('data-state', 'saved')
            page.locator('[data-testid=clinic-correction-back]').click()
            _open_from_worklist(page, base)
            expect(page.locator('[data-testid=text]')).to_have_value(TEXT)
            stored = page.request.get(f'{base}/api/clinic/v1/dictations/{did}/correction').json()
            assert stored['text_revision_id'] == saved['text_revision_id']
            assert stored['text'] == TEXT
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


@pytest.mark.parametrize('manual_server', ['asr-wait'], indirect=True)
def test_asr_wait_opens_manual_editor(manual_server, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    base, did = manual_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, OWNER)
            _open_from_worklist(page, base)
            view = page.get_by_test_id('clinic-correction-view')
            waiting = view.locator('.asr-wait-status').get_by_role('status')
            manual = view.get_by_role('button', name='Manuell schreiben', exact=True)
            text = view.get_by_test_id('text')
            expect(waiting).to_have_text('Spracherkennung läuft …')
            expect(manual).to_have_count(0)
            expect(text).to_have_value('')
            expect(text).not_to_be_editable()

            # Make the existing running job older while the real browser stays open.
            with sqlite3.connect(tmp_path / 'w213.db') as connection:
                connection.execute("UPDATE processing_job SET created_at = datetime('now', '-10 minutes')")
            expect(manual).to_be_enabled(timeout=12000)
            manual.focus()
            page.keyboard.press('Tab')
            page.keyboard.press('Shift+Tab')
            expect(manual).to_be_focused()
            assert manual.evaluate("el => el.matches(':focus-visible') && getComputedStyle(el).outlineWidth !== '0px'")
            manual.blur()
            _asr_wait_artifacts(page, 'empty')
            with page.expect_response(lambda r: r.url.endswith('/asr/skip') and r.request.method == 'POST') as response:
                manual.click()
            assert response.value.status == 200, response.value.text()
            assert response.value.json()['status'] == 'zu_schreiben'
            expect(waiting).to_have_text('Zu schreiben')
            expect(manual).to_have_count(0)
            expect(text).to_be_focused()
            expect(text).to_be_editable()
            text.fill(TEXT)
            with page.expect_response(lambda r: r.url.endswith('/correction/save') and r.request.method == 'POST') as saved:
                view.get_by_test_id('save').click()
            assert saved.value.status == 200, saved.value.text()
            _asr_wait_artifacts(page, 'filled')
            with page.expect_response(lambda r: r.url.endswith('/leases/release')) as released:
                page.get_by_test_id('clinic-correction-back').click()
            assert released.value.status == 200, released.value.text()
            detail = page.request.get(f'{base}/api/clinic/v1/dictations/{did}/detail').json()
            assert detail['status_axes']['workflow'] == 'zu_schreiben'
            _open_from_worklist(page, base)
            expect(text).to_have_value(TEXT)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()


def _asr_wait_artifacts(page, state):
    if not (output := os.environ.get('P169_ARTIFACTS')):
        return
    from playwright.sync_api import expect

    out = Path(output)
    initial_size = page.viewport_size
    initial_dark = page.evaluate("document.documentElement.classList.contains('dark')")
    sizes = ((390, 844),) if state == 'baseline' else ((1440, 900), (1280, 720), (390, 844))
    page.mouse.move(0, 0)
    for width, height in sizes:
        page.set_viewport_size({'width': width, 'height': height})
        for theme in ('light', 'dark'):
            page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", theme == 'dark')
            button = page.get_by_role('button', name='Manuell schreiben', exact=True)
            if state == 'empty':
                expect(button).to_be_enabled()
                bounds = button.bounding_box()
                assert min(bounds['width'], bounds['height']) >= 48
                assert 0 <= bounds['x'] <= bounds['x'] + bounds['width'] <= width
            stem = out / f'p169-schreibplatz-{width}x{height}-{theme}-{state}'
            if state != 'baseline':
                page.screenshot(path=stem.with_suffix('.png'), animations='disabled', caret='hide')
            html = page.evaluate('''() => {
                const view = document.querySelector('[data-testid="clinic-correction-view"]');
                const snapshot = view.cloneNode(true);
                snapshot.querySelector('textarea').textContent = view.querySelector('textarea').value;
                const styles = [...document.querySelectorAll('link[rel="stylesheet"][href^="/static/"], style')]
                    .map(el => el.outerHTML.replace('href="/static/', 'href="../../static/')).join('');
                return '<!doctype html><html lang="de" class="' + document.documentElement.className +
                    '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
                    '<title>Schreibplatz</title>' + styles + '</head><body class="v8-ui"><main>' +
                    snapshot.outerHTML + '</main></body></html>';
            }''')
            stem.with_suffix('.html').write_text(html, encoding='utf-8')
    page.set_viewport_size(initial_size)
    page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", initial_dark)
