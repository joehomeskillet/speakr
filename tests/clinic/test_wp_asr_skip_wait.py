"""Exercise the skip command through the clinic app and deliver a late ASR result."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.clinic import asr_profiles, asr_repeat, pools, revisions, routes_workflow, workflow
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db
from src.models import AuditEvent, ProcessingJob, Recording
from src.services import audit, job_control
from test_wp_v8_assignment_route import ROOT, _client, _staff_pool, _user
from test_wp_v8_send_route import _draft


def test_skip_wait_through_real_app(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1', 'PYTHONPATH': str(ROOT),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "skip.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise():
    from src.app import app
    from src.init_db import initialize_database
    from src.tasks.processing import _handle_late_asr

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    queued_at = datetime.now(timezone.utc).replace(microsecond=0)
    with app.app_context():
        initialize_database(app)
        author, editor, reader, admin = (_user('author'), _user('editor'), _user('reader'),
                                         _user('admin', admin=True))
        pool = pools.create_author_pool('Autoren', actor_id=admin).id
        pools.add_author_member(pool, author, actor_id=admin)
        staff = _staff_pool(admin, 'Schreibdienst', [(editor, 'member')])
        pools.link_staff_pool(pool, staff, actor_id=admin)
        pools.allow(editor, 'pool', pool_id=pool, actions=['view', 'edit'], actor_id=admin)
        pools.allow(reader, 'pool', pool_id=pool, actions=['view'], actor_id=admin)
        target = _draft(author, pool)
        row = db.session.get(ClinicDictation, target)
        source = Recording(user_id=author, title='Aufnahme', status='PROCESSING')
        db.session.add(source)
        db.session.flush()
        original = ClinicOriginal(dictation_id=target, content_hash='b' * 64, size_bytes=1,
                                  storage_key='synthetic', receipt='stored',
                                  origin_ref=f'upload:recording:{source.id}:time=upload')
        job = ProcessingJob(user_id=author, recording_id=source.id, job_type='transcribe',
                            status='queued', created_at=queued_at.replace(tzinfo=None))
        db.session.add_all([original, job])
        db.session.flush()
        source_id, job_id, original_id, anchor_id = source.id, job.id, original.id, row.recording_id
        db.session.commit()
    author_browser, editor_browser, reader_browser = (_client(app, uid) for uid in (author, editor, reader))

    def post(action='asr/skip', *, browser=editor_browser, version=1, key=None, body=None):
        client, csrf = browser
        return client.post(f'/api/clinic/v1/dictations/{target}/{action}',
                           json={} if body is None else body,
                           headers={**csrf, 'If-Match': f'"{target}:{version}"',
                                    'Idempotency-Key': key or str(uuid4())})

    assert post(version=0).json['code'] == 'NOT_PREPARING'
    sent = post('send', browser=author_browser, version=0)
    assert (sent.status_code, sent.json['status']) == (200, workflow.PREP_ONLINE)
    assert post(browser=(app.test_client(), {})).status_code == 401
    assert post(browser=(editor_browser[0], {})).status_code == 400
    assert post(browser=reader_browser).status_code == 404
    assert post(body={'actor_id': editor, 'elapsed': 900}).status_code == 400
    assert post(version=0).json['code'] == 'STALE_VERSION'

    with patch.object(routes_workflow, 'datetime', wraps=datetime) as clock:
        clock.now.return_value = queued_at + timedelta(seconds=119)
        assert post().json['code'] == 'ASR_WAIT_NOT_ELAPSED'
        clock.now.return_value = queued_at + timedelta(seconds=120)
        with patch.object(audit, 'record_in_tx', return_value=None):
            assert post().status_code == 503
        with app.app_context():
            db.session.get(ProcessingJob, job_id).status = 'processing'
            db.session.commit()
            asr_profiles.set_profile(author, actor_id=author,
                                     options={'wait_disabled': True, 'skip_wait_minutes': 3})
        clock.now.return_value = queued_at + timedelta(seconds=180)
        assert post().json['code'] == 'ASR_WAIT_DISABLED'
        with app.app_context():
            asr_profiles.set_profile(author, actor_id=author,
                                     options={'wait_disabled': False, 'skip_wait_minutes': 3})
        clock.now.return_value = queued_at + timedelta(seconds=179)
        assert post().json['code'] == 'ASR_WAIT_NOT_ELAPSED'
        clock.now.return_value = queued_at + timedelta(seconds=180)
        key = str(uuid4())
        done = post(key=key)
        assert done.status_code == 200
        assert done.json == {'dictation_id': target, 'status': workflow.TO_WRITE,
                             'version': 2, 'etag': f'"{target}:2"'}
        assert post(key=key).json == done.json
        assert post(browser=reader_browser, key=key).status_code == 404
        assert post(version=2).json['code'] == 'NOT_PREPARING'

    worklist = author_browser[0].get('/api/clinic/v1/worklist/rows')
    assert worklist.status_code == 200
    assert next(item for item in worklist.json['items'] if item['dictation_id'] == target)['status'] == workflow.TO_WRITE
    with app.app_context():
        row = db.session.get(ClinicDictation, target)
        job = db.session.get(ProcessingJob, job_id)
        source = db.session.get(Recording, source_id)
        original = db.session.get(ClinicOriginal, original_id)
        assert row.version_counter == 2 and job.status == 'processing' and job.cancel_requested_at is None
        event = AuditEvent.query.filter_by(recording_id=anchor_id, actor_user_id=editor,
                                           event='recording.edited').one()
        assert event.details['fields'] == ['workflow_status', 'asr_skip']
        human = revisions.add_human_revision(row, None, 'Manuell geschrieben.', editor, original_version=original)
        db.session.commit()
        attempt = asr_repeat.attempt_identity(job_id)
        with job_control.running(job_control.JobControl(job_id)):
            prior = asr_repeat.lock_projection(source)
            source.transcription = 'Verspätete automatische Abschrift.'
            assert _handle_late_asr(source, prior, 'synthetic', attempt=attempt).id == target
        job.status = 'completed'
        db.session.commit()
        assert db.session.get(workflow.ClinicWorkflowDictation, target).status == workflow.TO_WRITE
        edited, late = revisions.history(row)
        assert revisions.current_revision(row).id == edited.id == human.id
        assert late.kind == revisions.KIND_ASR and late.parent_revision_id == human.id
        assert late.body == 'Verspätete automatische Abschrift.'
        assert source.transcription == 'Manuell geschrieben.'


if __name__ == '__main__':
    _exercise()
