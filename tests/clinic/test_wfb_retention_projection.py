"""PAR-WF-013: scheduled retention updates the clinic views atomically."""

from datetime import timedelta

import pytest
from flask import Flask
from flask_login import LoginManager

from src.app import app, db
from src.clinic import policy, pool_policy, routes_history, routes_recordings, routes_worklist, workflow
from src.clinic import worklist_entries
from src.clinic.archive_policy import ArchiveBook, ClinicArchiveEntry, ClinicArchiveResult, Links
from src.clinic.models import ClinicArchiveEvent
from src.clinic.parity_receipts import run_once
from src.clinic.worklist_filters import WorklistFilterStore
from src.clinic.worklist_layout import LayoutStore
from src.models import AuditEvent, User
from src.services import audit, retention

from test_wfb_finished_retention import NOW, cases, snapshot  # noqa: F401


def _client(user_id):
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY='synthetic-retention-projection',
                        SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    db.init_app(flask)
    policy.install_pool_policy(flask)
    LoginManager(flask).user_loader(lambda uid: db.session.get(User, int(uid)))
    flask.register_blueprint(routes_worklist.create_blueprint(
        worklist_entries.load_entries, WorklistFilterStore(), LayoutStore()))
    flask.register_blueprint(routes_recordings.create_blueprint(worklist_entries.load_entries))
    flask.register_blueprint(routes_history.create_blueprint(
        authorize=lambda user, action, row: pool_policy.decide(
            policy.build_subject(user.id), 'view', row).allowed,
        run_once=run_once, dictation_snapshot=worklist_entries.dictation_snapshot))
    client = flask.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
    return client


def _rows(client, path, **params):
    response = client.get(f'/api/clinic/v1/{path}', query_string=params)
    assert response.status_code == 200, response.get_data(as_text=True)
    return {row['dictation_id']: row for row in response.get_json()['items']}


def _history(client, dictation_id):
    response = client.get(f'/api/clinic/v1/dictations/{dictation_id}/history')
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['events']


def test_due_case_moves_from_worklist_to_archive_with_links_and_history(cases):
    recording, media, (dictation, flow, original, audio, transcript, context, released) = cases()
    client = _client(recording.user_id)
    assert _rows(client, 'worklist/rows')[dictation.id]['status'] == workflow.DONE
    active = _rows(client, 'recordings')[dictation.id]
    assert active['recording_id'] == recording.id and active['archived_at'] is None
    assert dictation.id not in _rows(client, 'recordings', status=workflow.TO_ARCHIVE)
    history = _history(client, dictation.id)
    content_file = media.read_bytes()
    content = [snapshot(row) for row in (original, audio, transcript, context, released)]

    stats = retention.process_auto_deletion()

    assert stats['archived'] == 1 and stats['errors'] == 0, stats
    assert dictation.id not in _rows(client, 'worklist/rows')
    assert dictation.id not in _rows(client, 'recordings')
    archived = _rows(client, 'recordings', status=workflow.TO_ARCHIVE)[dictation.id]
    assert archived['recording_id'] == active['recording_id']
    assert archived['workflow_status'] == workflow.TO_ARCHIVE
    assert archived['archived_at'] == '2026-11-27T12:00:00Z'
    assert flow.status == workflow.TO_ARCHIVE
    entry = db.session.get(ClinicArchiveEntry, dictation.id)
    assert (entry.original_id, entry.audio_revision_id, entry.transcript_revision_id,
            entry.context_revision_id, entry.release_id) == (
                original.id, audio.id, transcript.id, context.id, released.id)
    assert ArchiveBook().archive_index()[f'{recording.user_id}/2026-11'] == (dictation.id,)
    after = _history(client, dictation.id)
    event, = [row for row in after if row.get('action') == 'recording.archived']
    assert [row for row in after if row != event] == history
    assert media.read_bytes() == content_file
    assert [snapshot(row) for row in (original, audio, transcript, context, released)] == content


@pytest.mark.parametrize('registered', [False, True], ids=['new-entry', 'existing-entry'])
def test_audit_failure_rolls_back_projection_and_retry_moves_once(cases, monkeypatch, registered):
    recording, _, (dictation, flow, original, audio, transcript, context, released) = cases()
    entry_before = None
    if registered:
        ArchiveBook().register(
            dictation.id, user_id=recording.user_id,
            links=Links(original.id, audio.id, transcript.id, context.id, released.id),
            data_class='audio', finished_at=NOW - timedelta(days=31),
        )
        db.session.commit()
        entry_before = snapshot(db.session.get(ClinicArchiveEntry, dictation.id))
    before = [snapshot(row) for row in (recording, dictation, flow)]
    record = audit.record_in_tx
    pending = []

    def fail_after_audit(event, *args, **kwargs):
        result = record(event, *args, **kwargs)
        if event == 'recording.archived' and kwargs['recording'].id == recording.id:
            entry = db.session.get(ClinicArchiveEntry, dictation.id)
            pending.append((flow.status, entry is not None and entry.archived_at is not None,
                            ClinicArchiveResult.query.filter_by(dictation_id=dictation.id).count()))
            raise RuntimeError('synthetic archive audit failure after flush')
        return result

    def evidence_counts():
        return (
            ClinicArchiveResult.query.filter_by(dictation_id=dictation.id).count(),
            ClinicArchiveEvent.query.filter_by(recording_id=recording.id).count(),
            AuditEvent.query.filter_by(recording_id=recording.id, event='recording.archived').count(),
        )

    with monkeypatch.context() as fault:
        fault.setattr(audit, 'record_in_tx', fail_after_audit)
        failed = retention.process_auto_deletion()

    assert failed['errors'] == 1 and failed['archived'] == 0, failed
    assert [snapshot(row) for row in (recording, dictation, flow)] == before
    entry_after = db.session.get(ClinicArchiveEntry, dictation.id)
    assert (None if entry_after is None else snapshot(entry_after)) == entry_before
    assert evidence_counts() == (0, 0, 0)

    retried = retention.process_auto_deletion()
    assert retried['errors'] == 0 and retried['archived'] == 1, retried
    repeated = retention.process_auto_deletion()
    assert repeated['errors'] == repeated['archived'] == 0, repeated
    assert flow.status == workflow.TO_ARCHIVE
    assert evidence_counts() == (1, 1, 1)
    assert pending == [(workflow.TO_ARCHIVE, True, 1)]
