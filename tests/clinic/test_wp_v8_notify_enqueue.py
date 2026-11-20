"""Committed browser transitions enqueue one real, replayable clinic notice each."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from src.clinic import policy, workflow
from src.clinic.acceptance_policy import AcceptanceLedger, SCOPE_DICTATION
from src.clinic.archive_policy import ArchiveBook, Links
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import Notification, ProcessingJob, Recording
from src.services.job_queue import ClinicJobError, job_queue
from tests.clinic.test_wp_v8_lease_and_acceptance_defaults import Browser, ROOT, TEXT, _seed


def test_mounted_transitions_enqueue_after_commit(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'CLINIC_PROFILE': 'staging', 'PYTHONPATH': str(ROOT),
           'UPLOAD_FOLDER': str(tmp_path / 'uploads'), 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true'}
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-5000:]


def exercise(app):
    users, target = _seed(app)
    did, author, actor = target['id'], users['author'], users['typist']
    policy.register_grant_provider(lambda uid: [policy.Grant('all', frozenset({
        'view', 'edit', 'release', 'archive', 'delete'}))] if uid == actor else [])
    with app.app_context():
        item = db.session.get(workflow.ClinicWorkflowDictation, did)
        item.assignee_user_id = author
        workflow.set_acceptance(db.session(), did, True)
        now = datetime.now(timezone.utc)
        AcceptanceLedger().publish(SCOPE_DICTATION, did, required=True,
                                   approver_user_ids=[actor, users['admin']],
                                   approver_pool_ids=[item.pool_id], deny_user_ids=[users['admin']], now=now)
        db.session.add(ClinicContextGrantUse(
            revision_id=target['context'], issuer='kis', audience='speakr', actor_user_id=author,
            subject_reference='synthetic', purpose='bind_existing', verified_at=now,
            nonce_digest=uuid4().hex.ljust(64, '0')))
        db.session.commit()

    browser = Browser(app, actor, did)
    held = browser.acquire()
    assert browser.write('/leases/release', body={k: held[k] for k in ('lease_id', 'generation')}).status_code == 200
    held = browser.acquire()
    with app.app_context():
        assert ProcessingJob.query.filter_by(job_type='clinic_notify').count() == 0

    def command(name, **payload):
        with app.app_context():
            version = db.session.get(ClinicDictation, did).version_counter
        body = dict(command=name, expected_revision=version + 1, request_id=str(uuid4()),
                    payload=dict(dictation_id=did, **payload))
        headers = {'Idempotency-Key': str(uuid4()), 'X-CSRF-Token': browser.csrf}
        return lambda: browser.client.post('/api/clinic/v1/parity/commands', json=body, headers=headers)

    def deliver(event):
        with app.app_context():
            job = ProcessingJob.query.filter_by(job_type='clinic_notify', status='queued').one()
            params = json.loads(job.params)
            assert params['event'] == event and params['actor_user_id'] == actor
            assert params['transition_id'] == params['clinic_operation_key'] == job.notify_transition_id
            if event == 'acceptance_requested':
                assert set(params['acceptor_ids']) == {author, actor}
            job_queue._process_job(job)
            db.session.refresh(job)
            assert job.status == 'completed', job.error_message
            notice = Notification.query.filter_by(dedupe_key=f'clinic.wf:{event}:{did}:{job.notify_transition_id}').one()
            assert notice.user_id == author

    def submit():
        return command('request_acceptance', text_revision_id=target['head'],
                       context_revision_id=target['context'],
                       acceptance_rule_id=browser.get('/review')['binding']['acceptance_rule_id'])

    request = submit()
    with patch.object(job_queue, 'enqueue', side_effect=ClinicJobError('queue unavailable')):
        assert request().status_code == 503
    with app.app_context():
        assert db.session.get(workflow.ClinicWorkflowDictation, did).status == workflow.TO_ACCEPT
        assert ProcessingJob.query.filter_by(job_type='clinic_notify').count() == 0
    first = request()
    assert first.status_code == 200
    deliver('acceptance_requested')
    assert request().json == first.json

    reject = command('decide_acceptance', text_revision_id=target['head'], decision='return', reason='Bitte prüfen')
    assert reject().status_code == 200
    deliver('acceptance_rejected')
    assert submit()().status_code == 200
    deliver('acceptance_requested')
    assert command('decide_acceptance', text_revision_id=target['head'], decision='accept')().status_code == 200
    deliver('accepted')

    def save(text, head, state_version=None):
        return browser.write('/correction/save', if_match=f'"{head}.g{held["generation"]}"', body={
            'lease_generation': held['generation'], 'text': text, 'audio_position_ms': 0,
            'expected_text_revision_id': head, 'expected_state_version': state_version})

    with patch('src.clinic.routes_correction.derive_links', side_effect=RuntimeError('save failed')):
        assert save('Text B', target['head']).status_code == 503
    with app.app_context():
        assert db.session.get(workflow.ClinicWorkflowDictation, did).status == workflow.ACCEPTED
        assert ProcessingJob.query.filter_by(job_type='clinic_notify').count() == 4
    unchanged = save(TEXT, target['head'])
    assert unchanged.status_code == 200, unchanged.json
    saved = save('Text B', target['head'], unchanged.json['state_version'])
    assert saved.status_code == 200, saved.json
    head = saved.json['text_revision_id']
    deliver('acceptance_requested')
    assert command('decide_acceptance', text_revision_id=head, decision='accept')().status_code == 200
    # A pending acceptance job must not suppress the following completion job.
    released = browser.release(target, held, head, 'Text B')
    assert released.status_code == 201, released.json
    assert browser.release(target, held, head, 'Text B').status_code == 200
    with app.app_context():
        waiting = ProcessingJob.query.filter_by(job_type='clinic_notify', status='queued').order_by(ProcessingJob.id).all()
        assert [json.loads(job.params)['event'] for job in waiting] == ['accepted', 'completed']
        job_queue._process_job(waiting[-1])
        assert Notification.query.filter_by(message_key='clinic.workflow.completed').one().user_id == author
    assert command('reopen_dictation', reason='Erneut prüfen')().status_code == 200
    with app.app_context():
        pool_id = db.session.get(ClinicDictation, did).pool_id
        draft = workflow.start(db.session(), 'create', author_user_id=author, pool_id=pool_id)
        rec = Recording(user_id=author, title='Synthetisch')
        db.session.add(rec)
        db.session.flush()
        draft_id = draft.id
        db.session.add(ClinicDictation(id=draft_id, recording_id=rec.id, author_user_id=author))
        db.session.flush()
        for target_id in (did, draft_id):
            ArchiveBook().register(target_id, user_id=author, links=Links('o', 'a', 't', 'c', None),
                                   data_class='audio', finished_at=None)
        db.session.commit()
    for target_id, action in ((did, 'archive'), (draft_id, 'delete')):
        reviewer = Browser(app, actor, target_id)
        response = reviewer.write('/archive', if_match=f'"{target_id}:1"', body={'action': action, 'confirm': True})
        assert response.status_code == 200, response.json
    with app.app_context():
        assert ProcessingJob.query.filter_by(job_type='clinic_notify').count() == 7

        recording_id = db.session.get(ClinicDictation, did).recording_id
    check_concurrent_reservation(app, author, recording_id)


def check_concurrent_reservation(app, author, recording_id):
    """A second process can win after the lookup and before our first flush."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    transition = str(uuid4())
    params = {'transition_id': transition, 'clinic_operation_key': transition}
    winner_ids = []

    def competing_insert(session, *_):
        if any(isinstance(job, ProcessingJob) and job.notify_transition_id == transition for job in session.new):
            with db.engine.begin() as conn:
                result = conn.execute(ProcessingJob.__table__.insert().values(
                    user_id=author, recording_id=recording_id, job_type='clinic_notify',
                    notify_transition_id=transition, params=json.dumps(params)))
                winner_ids.append(result.inserted_primary_key[0])

    with app.app_context():
        event.listen(Session, 'before_flush', competing_insert)
        try:
            found = job_queue.enqueue(author, recording_id, 'clinic_notify', params)
        finally:
            event.remove(Session, 'before_flush', competing_insert)
        assert winner_ids == [found]
        assert ProcessingJob.query.filter_by(notify_transition_id=transition).one().id == found


def run_in_isolated_database(directory):
    path = Path(directory) / 'notices.db'
    os.environ['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{path}'
    from sqlalchemy import text

    from src.app import app
    from src.init_db import initialize_database

    with app.app_context():
        assert db.engine.url.database == str(path)
        initialize_database(app)
        # Recreate the previous queue layout in this empty, disposable database.
        with db.engine.begin() as connection:
            connection.execute(text('DROP INDEX ux_processing_job_active_unkeyed'))
            connection.execute(text('DROP INDEX ux_processing_job_notify_transition'))
            connection.execute(text('ALTER TABLE processing_job DROP COLUMN notify_transition_id'))
            connection.execute(text("CREATE UNIQUE INDEX ux_processing_job_active_type "
                                    "ON processing_job (recording_id, job_type) "
                                    "WHERE status IN ('queued', 'processing')"))
        initialize_database(app)
    exercise(app)


if __name__ == '__main__':
    with TemporaryDirectory() as directory:
        run_in_isolated_database(directory)
