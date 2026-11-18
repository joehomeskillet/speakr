"""Retention archival with real temporary files and transactional DB evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest
from flask import g
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic import archive
from src.clinic.models import (
    ClinicArchiveEvent, ClinicAudioRevision, ClinicContextRevision, ClinicDelivery,
    ClinicDictation, ClinicOriginal, ClinicRelease, ClinicTranscriptRevision,
)
from src.clinic.archive import ArchiveRestoreDenied
from src.models import AuditEvent, ProcessingJob, Recording, RecordingTag, Tag, User
from src.services import audit, retention
from src.services.recording_state import set_user_archived

from clinic_support import login


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def snapshot(row):
    db.session.refresh(row)
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def events(recording):
    return ClinicArchiveEvent.query.filter_by(
        recording_id=recording.id, recording_created_at=recording.created_at,
    ).order_by(ClinicArchiveEvent.revision).all()


def client_for(user_id):
    g.pop('_login_user', None)
    return login(user_id)


def listed(client, **params):
    response = client.get('/api/recordings', query_string={'per_page': 100, **params})
    assert response.status_code == 200, response.get_data(as_text=True)
    return {row['id']: row for row in response.get_json()['recordings']}


@pytest.fixture
def scene(world, clinic_off, monkeypatch, tmp_path):
    # Use the existing settings boundary while restoring dynamic attributes
    # correctly after the test, including names provided through __getattr__.
    monkeypatch.setitem(retention.__dict__, 'ENABLE_AUTO_DELETION', True)
    monkeypatch.setitem(retention.__dict__, 'GLOBAL_RETENTION_DAYS', 30)
    monkeypatch.setitem(retention.__dict__, 'DELETION_MODE', 'archive')
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)

    with app.app_context():
        recording = db.session.get(Recording, world['rec_b'])
        media = tmp_path / 'synthetic-media.wav'
        original_file = tmp_path / 'synthetic-original.ds2'
        peaks = tmp_path / 'synthetic-peaks.json'
        media.write_bytes(b'RIFF synthetic archival bytes\x00\xff')
        original_file.write_bytes(b'synthetic immutable original\x00\xff')
        peaks.write_bytes(b'{"peaks": [0.1, 0.2]}')
        recording.audio_path = str(media)
        recording.audio_duration_seconds = 1
        recording.file_hash = hashlib.sha256(media.read_bytes()).hexdigest()
        recording.created_at = utcnow() - timedelta(days=100)
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=recording.user_id)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id,
            content_hash=hashlib.sha256(original_file.read_bytes()).hexdigest(),
            size_bytes=original_file.stat().st_size, storage_key=str(original_file),
            receipt='synthetic durable receipt', origin_ref='fixture',
        )
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash=recording.file_hash)
        context = ClinicContextRevision(dictation_id=dictation.id, source_system='fixture')
        db.session.add_all([original, audio, context])
        db.session.flush()
        transcript = ClinicTranscriptRevision(
            dictation_id=dictation.id, kind='HUMAN', body='Synthetic correction',
            normalised_text_hash=hashlib.sha256(b'Synthetic correction').hexdigest(),
            original_version_id=original.id,
        )
        db.session.add(transcript)
        db.session.flush()
        release = ClinicRelease(
            dictation_id=dictation.id, audio_revision_id=audio.id,
            transcript_revision_id=transcript.id, context_revision_id=context.id,
            text_hash=transcript.normalised_text_hash, actor_user_id=recording.user_id,
            document_type='fixture', release_type='fixture', authorization_decision='{}',
        )
        db.session.add(release)
        db.session.flush()
        delivery = ClinicDelivery(
            release_id=release.id, target='fixture', idempotency_key=release.id,
            payload_hash=transcript.normalised_text_hash, state='PENDING',
        )
        db.session.add(delivery)
        db.session.commit()
        children = [dictation, original, audio, transcript, context, release, delivery,
                    db.session.get(ProcessingJob, world['job_b'])]
        yield {**world, 'recording': recording, 'files': [media, original_file, peaks],
               'children': children}
        db.session.rollback()


@pytest.mark.parametrize('mode', ['archive', 'audio_only', 'full_recording'])
@pytest.mark.parametrize('status', ['COMPLETED', 'FAILED'])
def test_retention_preserves_bytes_children_revisions_and_exact_release(scene, monkeypatch, mode, status):
    recording = scene['recording']
    recording.status = status
    db.session.commit()
    monkeypatch.setitem(retention.__dict__, 'DELETION_MODE', mode)
    before = snapshot(recording)
    children = [snapshot(row) for row in scene['children']]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in scene['files']]

    stats = retention.process_auto_deletion()
    after = snapshot(recording)
    assert stats['archived'] == 1 and stats['errors'] == 0, stats
    assert stats['deleted_audio_only'] == stats['deleted_full'] == 0
    assert after['retention_archived_at'] is not None
    assert after['archive_revision'] == 1
    for column in before.keys() - {'retention_archived_at', 'archive_revision', 'updated_at'}:
        assert after[column] == before[column], column
    assert [snapshot(row) for row in scene['children']] == children
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in scene['files']] == hashes
    evidence, = events(recording)
    audit_row = db.session.get(AuditEvent, evidence.audit_event_id)
    assert audit_row is not None and audit_row.event == 'recording.archived'
    assert evidence.actor_kind == audit_row.actor_kind == 'system'
    assert evidence.actor_user_id is None and evidence.actor_token_id is None
    assert evidence.recording_owner_id == recording.user_id
    assert evidence.created_at == audit_row.created_at
    assert evidence.details == audit_row.details == {
        'rule': 'retention.global', 'scope': 'recording', 'retention_days': 30,
    }


def test_retention_retry_is_idempotent_and_owner_restore_is_audited(scene):
    recording = scene['recording']
    assert retention.process_auto_deletion()['archived'] == 1
    assert retention.process_auto_deletion()['archived'] == 0
    assert len(events(recording)) == 1
    response = client_for(recording.user_id).post('/save', json={'id': recording.id, 'is_archived': False})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert snapshot(recording)['retention_archived_at'] is None
    evidence = events(recording)
    assert [row.revision for row in evidence] == [1, 2]
    assert evidence[-1].event == 'recording.unarchived'
    assert evidence[-1].actor_kind == 'session'
    assert evidence[-1].actor_user_id == recording.user_id
    assert evidence[-1].details == {'rule': 'user.restore', 'scope': 'recording'}
    # Restoration leaves the age-based policy in force for the next sweep.
    assert retention.process_auto_deletion()['archived'] == 1
    assert [row.revision for row in events(recording)] == [1, 2, 3]


def test_retention_is_hidden_from_the_owner_active_list(scene):
    recording = scene['recording']
    owner_id = recording.user_id
    retention.process_auto_deletion()
    owner = client_for(owner_id)
    assert recording.id not in listed(owner)
    assert listed(owner, archived='true')[recording.id]['is_archived'] is True
    # Shares are gone, so the other author has no route to this recording.
    other = client_for(scene['user_a'])
    assert recording.id not in listed(other)
    assert recording.id not in listed(other, archived='true')
    assert other.post(f'/recording/{recording.id}/toggle_archive').status_code == 404
    with pytest.raises(ArchiveRestoreDenied):
        set_user_archived(recording, db.session.get(User, scene['user_a']), False, commit=False)
    db.session.rollback()
    assert len(events(recording)) == 1
    for flag, included in [('false', False), ('true', True)]:
        response = client_for(owner_id).get(
            '/api/v1/recordings', query_string={'archived': flag, 'per_page': 100})
        assert response.status_code == 200, response.get_data(as_text=True)
        rows = response.get_json()['recordings']
        assert any(row['id'] == recording.id and row['is_archived'] for row in rows) is included


@pytest.mark.parametrize('entry', ['save', 'bulk', 'api', 'api-batch'])
def test_manual_archive_restore_and_noop_are_audited(scene, entry):
    recording = db.session.get(Recording, scene['rec_a'])
    client = client_for(scene['user_a'])
    for value in (True, True, False, False):
        if entry == 'save':
            response = client.post('/save', json={'id': recording.id, 'is_archived': value})
        elif entry == 'bulk':
            response = client.post('/api/recordings/bulk-toggle', json={
                'recording_ids': [recording.id], 'field': 'archive', 'value': value,
            })
        elif entry == 'api':
            response = client.patch(f'/api/v1/recordings/{recording.id}',
                                    json={'is_archived': value})
        else:
            response = client.patch('/api/v1/recordings/batch', json={
                'recording_ids': [recording.id], 'updates': {'is_archived': value},
            })
        assert response.status_code == 200, response.get_data(as_text=True)
    evidence = events(recording)
    assert [row.event for row in evidence] == ['recording.archived', 'recording.unarchived']
    assert [row.revision for row in evidence] == [1, 2]
    assert all(row.actor_user_id == scene['user_a'] and row.target_user_id == scene['user_a']
               and row.actor_kind == 'session' for row in evidence)
    assert [row.details for row in evidence] == [
        {'rule': 'user.archive', 'scope': 'personal'}, {'rule': 'user.restore', 'scope': 'personal'},
    ]


def test_owner_toggle_archive_is_audited(scene):
    recording = db.session.get(Recording, scene['rec_a'])
    client = client_for(scene['user_a'])
    response = client.post(f'/recording/{recording.id}/toggle_archive')
    assert response.status_code == 200
    assert response.get_json()['is_archived'] is True
    assert recording.id not in listed(client)
    assert listed(client, archived='true')[recording.id]['is_archived'] is True
    response = client.post(f'/recording/{recording.id}/toggle_archive')
    assert response.status_code == 200
    assert response.get_json()['is_archived'] is False
    assert recording.id in listed(client)
    assert all(row.target_user_id == scene['user_a'] and row.details['scope'] == 'personal'
               for row in events(recording))
    assert len(events(recording)) == 2


def test_token_actor_identifies_user_and_token(scene):
    g.pop('_login_user', None)
    response = app.test_client().patch(f'/api/v1/recordings/{scene["rec_a"]}',
                                      headers={'Authorization': 'Bearer ' + scene['token']},
                                      json={'is_archived': True})
    assert response.status_code == 200, response.get_data(as_text=True)
    evidence, = events(db.session.get(Recording, scene['rec_a']))
    assert evidence.actor_kind == 'api_token'
    assert evidence.actor_user_id == scene['user_a']
    assert evidence.actor_token_id == scene['token_id']


def fail_after_evidence(monkeypatch, recording_id):
    original = archive.record_transition

    def failing(recording, *args, **kwargs):
        original(recording, *args, **kwargs)
        if recording.id == recording_id:
            raise RuntimeError('synthetic evidence transaction failure')

    monkeypatch.setattr(archive, 'record_transition', failing)


@pytest.mark.parametrize('entry', ['save', 'api'])
def test_archive_failure_rolls_back_metadata_flags_and_both_evidence_rows(scene, monkeypatch, entry):
    recording = db.session.get(Recording, scene['rec_a'])
    before = snapshot(recording)
    audit_before = AuditEvent.query.filter_by(recording_id=recording.id, event='recording.archived').count()
    fail_after_evidence(monkeypatch, recording.id)
    payload = {'title': 'must roll back', 'is_inbox': False, 'is_archived': True}
    client = client_for(scene['user_a'])
    if entry == 'save':
        response = client.post('/save', json={'id': recording.id, **payload})
    else:
        response = client.patch(f'/api/v1/recordings/{recording.id}', json=payload)
    assert response.status_code == 500
    assert snapshot(recording) == before
    assert events(recording) == []
    assert AuditEvent.query.filter_by(recording_id=recording.id, event='recording.archived').count() == audit_before


def test_invalid_folder_rolls_back_staged_api_archive(scene):
    recording = db.session.get(Recording, scene['rec_a'])
    before = snapshot(recording)
    response = client_for(scene['user_a']).patch(f'/api/v1/recordings/{recording.id}',
                                               json={'is_archived': True, 'folder_id': 999999999})
    assert response.status_code == 404
    assert snapshot(recording) == before
    assert events(recording) == []


@pytest.mark.parametrize('entry', ['bulk', 'api-batch'])
def test_batch_failure_keeps_successful_item_and_rolls_back_failed_item(scene, monkeypatch, entry):
    failed = scene['recording']
    before = snapshot(failed)
    fail_after_evidence(monkeypatch, failed.id)
    client = client_for(scene['user_a'])
    if entry == 'bulk':
        response = client.post('/api/recordings/bulk-toggle', json={
            'recording_ids': [scene['rec_a'], failed.id], 'field': 'archive', 'value': True,
        })
        assert response.get_json()['affected_ids'] == [scene['rec_a']]
    else:
        response = client.patch('/api/v1/recordings/batch', json={
            'recording_ids': [scene['rec_a'], failed.id], 'updates': {'is_archived': True},
        })
        assert response.get_json()['updated'] == response.get_json()['failed'] == 1
    assert response.status_code == 200
    assert snapshot(failed) == before
    assert events(failed) == []
    assert len(events(db.session.get(Recording, scene['rec_a']))) == 1


def test_retention_failure_rolls_back_then_retry_succeeds(scene, monkeypatch):
    recording = scene['recording']
    before = snapshot(recording)
    with monkeypatch.context() as fault:
        fail_after_evidence(fault, recording.id)
        stats = retention.process_auto_deletion()
    assert stats['errors'] == 1 and stats['archived'] == 0
    assert snapshot(recording) == before
    assert events(recording) == []
    assert retention.process_auto_deletion()['archived'] == 1
    assert len(events(recording)) == 1


def test_audit_disabled_still_records_archive_but_missing_evidence_fails_closed(scene, monkeypatch):
    monkeypatch.setattr(audit, 'is_enabled', lambda: False)
    with monkeypatch.context() as suppressed:
        suppressed.setattr(audit, 'record_in_tx', lambda *args, **kwargs: None)
        stats = retention.process_auto_deletion()
    assert stats['errors'] == 1 and stats['archived'] == 0
    assert snapshot(scene['recording'])['retention_archived_at'] is None
    assert events(scene['recording']) == []
    assert retention.process_auto_deletion()['archived'] == 1
    evidence, = events(scene['recording'])
    assert db.session.get(AuditEvent, evidence.audit_event_id) is not None


@pytest.mark.parametrize('operation', ['UPDATE clinic_archive_event SET revision = 9',
                                       'DELETE FROM clinic_archive_event'])
def test_database_rejects_ledger_mutation(scene, operation):
    retention.process_auto_deletion()
    evidence, = events(scene['recording'])
    evidence_id = evidence.id
    with pytest.raises(DBAPIError, match='append-only'):
        db.session.execute(text(operation + ' WHERE id = :id'), {'id': evidence_id})
    db.session.rollback()
    assert db.session.get(ClinicArchiveEvent, evidence_id).revision == 1


def test_archive_audit_survives_pruning(scene, monkeypatch):
    retention.process_auto_deletion()
    evidence, = events(scene['recording'])
    audit_id = evidence.audit_event_id
    ordinary = audit.record_in_tx('recording.edited', recording=scene['recording'],
                                  details={'fields': ['title']}, force=True)
    db.session.commit()
    ordinary_id = ordinary.id
    monkeypatch.setattr(audit, 'retention_days', lambda: 1)
    assert audit.prune_audit_events(now=utcnow() + timedelta(days=365)) > 0
    db.session.expire_all()
    assert db.session.get(AuditEvent, audit_id) is not None
    assert db.session.get(AuditEvent, ordinary_id) is None
    assert len(events(scene['recording'])) == 1


def test_admin_run_uses_admin_actor_and_tag_rule_in_clinic_mode(scene, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    tag = Tag(name='archive in seven days', user_id=scene['user_b'], retention_days=7)
    db.session.add(tag)
    db.session.flush()
    db.session.add(RecordingTag(recording_id=scene['recording'].id, tag_id=tag.id))
    db.session.commit()
    tag_id = tag.id
    client = client_for(scene['user_admin'])
    response = client.post('/admin/auto-deletion/run')
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()['archived'] == 1
    evidence, = events(scene['recording'])
    assert evidence.actor_kind == 'session' and evidence.actor_user_id == scene['user_admin']
    assert evidence.details == {'rule': 'retention.tag', 'scope': 'recording',
                                'retention_days': 7, 'tag_id': tag_id}


def test_tag_only_scheduled_archival_runs_and_disabled_switch_prevents_it(scene, monkeypatch):
    from src.config import startup
    from src.services import ownerless_cleanup

    monkeypatch.setitem(retention.__dict__, 'GLOBAL_RETENTION_DAYS', 0)
    monkeypatch.setitem(startup.__dict__, 'GLOBAL_RETENTION_DAYS', 0)
    monkeypatch.setitem(startup.__dict__, 'ENABLE_AUTO_DELETION', False)
    # Other daily maintenance services are independent of this work package.
    monkeypatch.setattr(ownerless_cleanup, 'run_ownerless_cleanup', lambda: {})
    monkeypatch.setattr(audit, 'prune_audit_events', lambda: 0)
    tag = Tag(name='scheduled seven days', user_id=scene['user_b'], retention_days=7)
    db.session.add(tag)
    db.session.flush()
    db.session.add(RecordingTag(recording_id=scene['recording'].id, tag_id=tag.id))
    db.session.commit()
    startup.run_daily_maintenance(app)
    assert events(scene['recording']) == []
    monkeypatch.setitem(startup.__dict__, 'ENABLE_AUTO_DELETION', True)
    startup.run_daily_maintenance(app)
    evidence, = events(scene['recording'])
    assert evidence.actor_kind == 'system'
    assert evidence.details['rule'] == 'retention.tag'


def test_concurrent_archive_requests_commit_one_transition(scene):
    recording_id = scene['rec_a']
    user_id = scene['user_a']
    db.session.rollback()
    ready = Barrier(2)

    def worker():
        with app.app_context():
            recording = db.session.get(Recording, recording_id)
            user = db.session.get(User, user_id)
            ready.wait(timeout=10)
            try:
                set_user_archived(recording, user, True)
                return 'committed'
            except DBAPIError:
                db.session.rollback()
                return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        outcomes = [future.result(timeout=30) for future in futures]
    assert 'committed' in outcomes
    db.session.expire_all()
    recording = db.session.get(Recording, recording_id)
    from src.services.recording_state import get_user_archived
    assert get_user_archived(recording, db.session.get(User, user_id)) is True
    assert recording.archive_revision == 1
    assert len(events(recording)) == 1
    set_user_archived(recording, db.session.get(User, user_id), True)
    assert len(events(recording)) == 1


def test_admin_archive_count_includes_retention_without_double_counting(scene):
    client = client_for(scene['user_admin'])
    before = client.get('/admin/stats')
    assert before.status_code == 200
    count_before = before.get_json()['archived_recordings']
    assert retention.process_auto_deletion()['archived'] == 1
    response = client.get('/admin/stats')
    assert response.status_code == 200
    assert response.get_json()['archived_recordings'] == count_before + 1
    recording = scene['recording']
    # A legacy personal flag and a retention timestamp can coexist.
    recording.is_archived = True
    db.session.commit()
    assert client.get('/admin/stats').get_json()['archived_recordings'] == count_before + 1


def test_archive_refreshes_a_stale_owner_flag(scene):
    recording = db.session.get(Recording, scene['rec_a'])
    user = db.session.get(User, scene['user_a'])
    set_user_archived(recording, user, True)
    db.session.rollback()
    assert recording.is_archived is True
    # A second session changes the owner flag while the first keeps an already
    # loaded ORM object. The next call must refresh it under the lock.
    with app.app_context():
        set_user_archived(db.session.get(Recording, recording.id),
                          db.session.get(User, user.id), False)
    assert recording.is_archived is True
    set_user_archived(recording, user, True)
    assert [row.event for row in events(recording)] == [
        'recording.archived', 'recording.unarchived', 'recording.archived',
    ]


@pytest.mark.parametrize('schema', ['v0.10.3-alpha.sql', 'v0.10.10-alpha.sql'])
def test_legacy_database_migrates_preserving_content_and_archive_evidence(tmp_path, schema):
    from src.init_db import initialize_database
    from tests.test_upgrade_path import _fixture_app

    database = tmp_path / 'legacy.db'
    media = tmp_path / 'original.wav'
    media.write_bytes(b'RIFF synthetic original bytes\x00\xff')
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    fixture = Path(__file__).resolve().parents[1] / 'fixtures' / 'schemas' / schema
    with sqlite3.connect(database) as connection:
        connection.executescript(fixture.read_text())
        columns = {row[1] for row in connection.execute('PRAGMA table_info(recording)')}
        if 'is_archived' not in columns:
            connection.execute('ALTER TABLE recording ADD COLUMN is_archived BOOLEAN DEFAULT 0')
        connection.execute(
            'INSERT INTO "user" (id, username, email, password) VALUES (1, ?, ?, ?)',
            ('archive-owner', 'archive-owner@example.test', 'preserved-hash'),
        )
        for recording_id, archived in ((1, True), (2, None)):
            connection.execute(
                'INSERT INTO recording (id, user_id, title, transcription, summary, status, '
                'audio_path, keep_audio_only, created_at, is_archived) '
                'VALUES (?, 1, ?, ?, ?, ?, ?, 0, ?, ?)',
                (recording_id, 'Synthetic original', 'Exact transcript', 'Exact summary',
                 'COMPLETED', str(media), '2026-01-01 12:00:00.000000', archived),
            )
        preserved_columns = ('id, user_id, title, transcription, summary, status, '
                             'audio_path, keep_audio_only, created_at, is_archived')
        before = connection.execute(
            'SELECT ' + preserved_columns + ' FROM recording ORDER BY id',
        ).fetchall()

    migrated_app = _fixture_app(str(database))
    with migrated_app.app_context():
        initialize_database(migrated_app)
        assert 'MIGRATION_FAILED' not in migrated_app.config
        for recording_id in (1, 2):
            recording = db.session.get(Recording, recording_id)
            assert recording.retention_archived_at is None
            assert recording.archive_revision == 0
        assert ClinicArchiveEvent.query.count() == 0
        recording = db.session.get(Recording, 2)
        set_user_archived(recording, db.session.get(User, 1), True)
        evidence, = events(recording)
        evidence_before = snapshot(evidence)
        set_user_archived(recording, db.session.get(User, 1), False)
        db.session.remove()
        initialize_database(migrated_app)
        initialize_database(migrated_app)
        assert 'MIGRATION_FAILED' not in migrated_app.config
        assert snapshot(db.session.get(ClinicArchiveEvent, evidence_before['id'])) == evidence_before
        assert db.session.get(Recording, 2).archive_revision == 2
        assert ClinicArchiveEvent.query.count() == 2
        with pytest.raises(DBAPIError, match='append-only'):
            db.session.execute(text('DELETE FROM clinic_archive_event'))
        db.session.rollback()

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            'SELECT ' + preserved_columns + ' FROM recording ORDER BY id',
        ).fetchall()
        columns = {row[1]: row for row in connection.execute('PRAGMA table_info(recording)')}
        assert columns['retention_archived_at'][3] == 0
        assert columns['archive_revision'][3] == 1
        assert str(columns['archive_revision'][4]) == '0'
    assert after[0] == before[0]
    assert after[1][:-1] == before[1][:-1]
    assert after[1][-1] == 0
    assert hashlib.sha256(media.read_bytes()).hexdigest() == digest
