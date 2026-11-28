"""Exercise the type boundary through real clinic startup, with synthetic data."""

import os
import subprocess
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.clinic import kis, pools, release, revisions, workflow
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.retention_eligibility import eligible_for_retention
from src.clinic.recording_type import PRIVATE_WEB_SOURCE
from src.database import db
from src.models import ProcessingJob, Recording, Webhook, WebhookDelivery
from src.services.webhook_dispatch import enqueue_event
from test_wp_v8_assignment_route import ROOT, _client, _dictation, _staff_pool, _user

API = '/api/clinic/v1'
ADMIN = '/api/admin/clinic/dictations'
PRIVATE = ('sitzung', 'patientengespraech', 'notiz')
SECRET = 'Synthetischer privater Inhalt'


def test_private_recording_access_through_real_app(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'CLINIC_PROFILE': 'staging',
           'CLINIC_KIS_DELIVERY_ENABLED': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1', 'PYTHONPATH': str(ROOT),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "access.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _audio_source(app, author):
    from src.services.storage import get_storage_service

    path = Path(app.config['UPLOAD_FOLDER']) / 'synthetic.wav'
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * 1600)
    stored = get_storage_service().upload_local_file(str(path), 'tests/private.wav', delete_source=False)
    source = Recording(user_id=author, title=SECRET, transcription=SECRET, status='COMPLETED',
                       audio_path=stored.locator, mime_type='audio/wav')
    db.session.add(source)
    db.session.flush()
    return source


def _seed(app):
    admin = _user('admin', admin=True)
    author, typist, lead = map(_user, ('author', 'typist', 'lead'))
    pool = pools.create_author_pool('Autoren', actor_id=admin).id
    staff = _staff_pool(admin, 'Schreibdienst', [(lead, 'admin'), (typist, 'member')])
    pools.link_staff_pool(pool, staff, actor_id=admin)
    pools.add_author_member(pool, author, actor_id=admin)
    for actor in (author, typist, lead, admin):
        pools.allow(actor, 'all', actions=['view', 'play', 'edit', 'export', 'audio_export', 'reassign'],
                    actor_id=admin)
    report, _ = _dictation(author, pool, sent=True)
    rows = {}
    for kind in (*PRIVATE, ''):
        target, recording_id = _dictation(author, pool, sent=kind == 'patientengespraech')
        dictation = db.session.get(ClinicDictation, target)
        dictation.configured_recording_type = kind
        recording = db.session.get(Recording, recording_id)
        recording.title = recording.transcription = SECRET
        rows[kind] = (target, recording_id)
    conversation = db.session.get(workflow.ClinicWorkflowDictation, rows['patientengespraech'][0])
    conversation.assignee_user_id = typist
    target, recording_id = rows['notiz']
    dictation = db.session.get(ClinicDictation, target)
    source = _audio_source(app, author)
    original = ClinicOriginal(dictation_id=target, content_hash='b' * 64, size_bytes=1,
                              storage_key='synthetic', receipt='stored',
                              origin_ref=f'upload:recording:{source.id}:time=recording')
    audio = ClinicAudioRevision(dictation_id=target, content_hash='a' * 64)
    context = ClinicContextRevision(dictation_id=target, source_system='test', tenant_scope='test',
                                    case_ref='synthetic-case', patient_ref='synthetic-patient')
    job = ProcessingJob(user_id=author, recording_id=source.id, job_type='transcribe', status='failed')
    db.session.add_all([original, audio, context, job])
    db.session.flush()
    text = revisions.add_asr_revision(dictation, original, SECRET, 'test')
    now = datetime.now(timezone.utc)
    frozen = release.create_release(dictation, audio, text, context, text.normalised_text_hash,
                                    author, {'allowed': True}, now - timedelta(days=30),
                                    document_type='report', release_type='clinical')
    note = db.session.get(workflow.ClinicWorkflowDictation, target)
    note.status = workflow.DONE  # Even stale workflow and release data must not enable output.
    ids = (pool, report, rows, source.id, job.id, frozen.id, text.id)
    db.session.commit()
    return (author, typist, lead, admin), ids


def _exercise():
    from src import file_exporter
    from src.app import app
    from src.clinic import policy
    from src.clinic.models import ClinicRelease
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        (author, typist, lead, admin), ids = _seed(app)
        pending = Recording(user_id=author, title=SECRET, processing_source=PRIVATE_WEB_SOURCE,
                            status='PENDING', transcription=SECRET)
        db.session.add(pending)
        db.session.flush()
        pending_id = pending.id
        db.session.commit()
    pool, report, rows, source, job, frozen_id, revision = ids
    private_ids = {target for target, _ in rows.values()}
    browser, headers = _client(app, author)
    typist_browser, lead_browser, admin_browser = [_client(app, actor)[0] for actor in (typist, lead, admin)]

    def items(client, path):
        response = client.get(path)
        assert response.status_code == 200, response.get_data(as_text=True)
        return {row['dictation_id'] for row in response.json['items']}

    for client in (browser, typist_browser, lead_browser, admin_browser):
        assert not items(client, API + '/worklist/rows') & private_ids
        listed = items(client, API + '/recordings')
        assert listed & private_ids == ({rows[kind][0] for kind in PRIVATE} if client is browser else set())
        assert not items(client, API + '/recordings?pool_id=' + pool) & private_ids
    assert report in items(browser, API + '/worklist/rows')
    assert report in items(admin_browser, API + '/worklist/rows')

    private_recordings = {recording for _, recording in rows.values()} | {source, pending_id}
    response = admin_browser.get(ADMIN)
    assert response.status_code == 200
    assert SECRET not in response.get_data(as_text=True)
    assert all(row['recording_id'] not in private_recordings for row in response.json['items'])
    assert all(row['id'].removeprefix('workflow:') not in private_ids for row in response.json['items'])
    assert admin_browser.get(f'{ADMIN}/{pending_id}/transcript').status_code == 404
    assert admin_browser.get(f'/api/recordings/{pending_id}').status_code == 404
    response = admin_browser.get(API + '/jobs/health')
    assert response.status_code == 200
    assert job not in {row['id'] for row in response.json['jobs']}
    for kind, (target, recording) in rows.items():
        path = API + '/dictations/' + target
        key = {'Idempotency-Key': str(uuid4()), 'If-Match': f'"{target}:0"'}
        for _ in range(2):
            denied = browser.post(path + '/send', headers=headers | key, json={})
            assert (denied.status_code, denied.json['code']) == (409, 'not_sendable_type')
        detail = browser.get(path + '/detail')
        if kind in PRIVATE:
            assert detail.status_code == 200 and 'send' not in detail.json['allowed_actions']
        else:
            assert detail.status_code == 404
        for client in (typist_browser, lead_browser, admin_browser):
            assert client.get(path + '/detail').status_code == 404
            assert client.get(path + '/correction').status_code == 404
            assert client.get(path + '/audio/derived').status_code == 404
            assert client.get(f'/api/recordings/{recording}').status_code == 404
            assert client.get(path + '/assignment/candidates').status_code in (403, 404)
        assert admin_browser.get(f'{ADMIN}/{recording}/transcript').status_code == 404
        assert admin_browser.get(f'{ADMIN}/{recording}/audio').status_code == 404

    target, recording_id = rows['notiz']
    path = API + '/dictations/' + target
    assert SECRET in browser.get(path + '/correction').get_data(as_text=True)
    assert browser.get(f'/audio/{source}').status_code == 200
    assert browser.get(f'/audio/{source}?download=true').status_code == 403
    assert admin_browser.get(f'{ADMIN}/{source}/transcript').status_code == 404
    assert admin_browser.get(f'{ADMIN}/{source}/audio').status_code == 404
    assert admin_browser.get(f'/audio/{source}').status_code == 404
    assert browser.post('/api/recordings/merge', headers=headers,
                        json={'recording_ids': [source, recording_id]}).status_code == 403
    for fmt in ('txt', 'kis'):
        response = browser.post(path + '/exports', headers=headers | {'Idempotency-Key': str(uuid4())},
                                json={'revision_id': revision, 'format': fmt})
        assert response.status_code == 404
    assert all(not row['available'] for row in browser.get(path + '/output-targets').json['targets'])
    assert browser.get(path + '/clipboard/' + revision).status_code == 404
    assert browser.get(path + f'/exports/{uuid4()}/download').status_code == 404
    assert browser.get(API + f'/bundles/{target}/export').status_code == 404

    with app.app_context():
        for target, _recording in rows.values():
            assert db.session.get(ClinicDictation, target).version_counter == 0
        assert db.session.get(workflow.ClinicWorkflowDictation, rows['sitzung'][0]).status == workflow.DRAFT
        note = db.session.get(Recording, recording_id)
        assert not eligible_for_retention(note, now=datetime.now(timezone.utc), retention_days=1)
        assert not policy.decide(policy.build_subject(admin), 'delete', policy.build_facts(note.id, author)).allowed
        frozen = db.session.get(ClinicRelease, frozen_id)
        context = db.session.get(ClinicContextRevision, frozen.context_revision_id)
        adapter = kis.MockKisAdapter()
        denied = kis.deliver(frozen, context, adapter, str(uuid4()), datetime.now(timezone.utc))
        assert denied.outcome == kis.REFUSED and adapter.deliver_calls == 0
        db.session.add(Webhook(user_id=author, name='Synthetic', url='https://example.invalid/hook',
                               events='["recording.updated"]'))
        db.session.commit()
        assert enqueue_event(db.session, author, 'recording.updated', {'recording_id': source}) == 0
        assert enqueue_event(db.session, author, 'recording.updated', {'recording_id': pending_id}) == 0
        assert WebhookDelivery.query.count() == 0
        report_recording = db.session.get(Recording, db.session.get(ClinicDictation, report).recording_id)
        report_recording.transcription = 'Synthetischer Bericht'
        report_id = report_recording.id
        db.session.commit()
        assert enqueue_event(db.session, author, 'recording.updated', {'recording_id': report_id}) == 1
    with patch.object(file_exporter, 'ENABLE_AUTO_EXPORT', True), \
            patch.object(file_exporter, 'AUTO_EXPORT_TRANSCRIPTION', True), \
            patch.object(file_exporter, 'get_export_directory',
                         return_value=Path(app.config['UPLOAD_FOLDER'])) as output:
        assert file_exporter.export_recording(recording_id) is None
        assert file_exporter.export_recording(source) is None
        assert file_exporter.export_recording(pending_id) is None
        output.assert_not_called()
        exported = file_exporter.export_recording(report_id)
        assert exported is not None and 'Synthetischer Bericht' in Path(exported).read_text(encoding='utf-8')


if __name__ == '__main__':
    _exercise()
