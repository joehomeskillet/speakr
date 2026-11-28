"""PAR-WF-013: synthetic DB/file evidence with a controlled retention clock."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic import release, workflow
from src.clinic.archive_policy import ArchiveBook, Links
from src.clinic.models import (
    ClinicArchiveEvent, ClinicAudioRevision, ClinicContextRevision, ClinicDictation,
    ClinicOriginal, ClinicTranscriptRevision,
)
from src.models import Recording, RecordingTag, Tag, User
from src.services import retention

NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def utcnow(cls):
        return NOW.replace(tzinfo=None)


@pytest.fixture
def cases(clinic_on, monkeypatch, tmp_path):
    monkeypatch.setattr(retention, 'datetime', Clock)
    monkeypatch.setitem(retention.__dict__, 'ENABLE_AUTO_DELETION', True)
    monkeypatch.setitem(retention.__dict__, 'GLOBAL_RETENTION_DAYS', 30)

    def add(row):
        db.session.add(row)
        db.session.flush()
        return row

    with app.app_context():
        name = uuid4().hex[:20]
        user = add(User(username=name, email=f'{name}@example.invalid', password='synthetic'))

        def make(status=workflow.DONE, released_at=NOW - timedelta(days=31), *,
                 hold=False, processing='COMPLETED', with_workflow=True, with_dictation=True):
            media = tmp_path / f'{uuid4().hex}.wav'
            media.write_bytes(b'RIFF synthetic retention bytes')
            recording = add(Recording(
                user_id=user.id, title='Synthetic retention', status=processing,
                created_at=(NOW - timedelta(days=100)).replace(tzinfo=None),
                audio_path=str(media), transcription='Synthetic correction',
            ))
            children = []
            if with_dictation:
                dictation = add(ClinicDictation(recording_id=recording.id, author_user_id=user.id))
                children.append(dictation)
                if with_workflow:
                    children.append(add(workflow.ClinicWorkflowDictation(
                        id=dictation.id, author_user_id=user.id, pool_id='synthetic', status=status,
                        resume_status=workflow.TO_CORRECT if status == workflow.IN_PROGRESS else None,
                    )))
                original = add(ClinicOriginal(
                    dictation_id=dictation.id, content_hash='a' * 64, size_bytes=media.stat().st_size,
                    storage_key=str(media), receipt='synthetic receipt', origin_ref='fixture',
                ))
                audio = add(ClinicAudioRevision(dictation_id=dictation.id, content_hash='a' * 64))
                text = add(ClinicTranscriptRevision(
                    dictation_id=dictation.id, kind='HUMAN', body='Synthetic correction',
                    normalised_text_hash='b' * 64, original_version_id=original.id,
                ))
                context = add(ClinicContextRevision(dictation_id=dictation.id, source_system='fixture'))
                children.extend([original, audio, text, context])
                released = None
                if released_at is not None:
                    # Match the web route's UTC clock before SQLite drops tzinfo.
                    released_at = released_at.astimezone(timezone.utc)
                    released = release.create_release(
                        dictation, audio, text, context, text.normalised_text_hash, user.id,
                        {'allowed': True}, released_at, document_type='report', release_type='clinical_final',
                    )
                    children.append(released)
                if hold:
                    ArchiveBook().register(
                        dictation.id, user_id=user.id,
                        links=Links(original.id, audio.id, text.id, context.id, released.id),
                        data_class='audio', finished_at=released_at, retention_days=30, legal_hold=True,
                    )
            db.session.commit()
            return recording, media, children

        yield make
        db.session.rollback()


def snapshot(row):
    db.session.refresh(row)
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@pytest.mark.parametrize('mode', ['archive', 'audio_only', 'full_recording'])
def test_only_due_finished_case_archives_without_deleting(cases, monkeypatch, mode):
    monkeypatch.setitem(retention.__dict__, 'DELETION_MODE', mode)
    rows = [cases(status=workflow.TO_CORRECT), cases(released_at=NOW - timedelta(days=2)),
            cases(), cases(hold=True)]
    before = [snapshot(row) for row, _, _ in rows]
    children = [[snapshot(child) for child in group] for _, _, group in rows]
    files = [path.read_bytes() for _, path, _ in rows]
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        stats = retention.process_auto_deletion()
        assert stats['archived'] == 1 and stats['errors'] == 0, stats
        assert stats['deleted_full'] == stats['deleted_audio_only'] == 0
        assert retention.process_auto_deletion()['archived'] == 0
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)
    assert not any(sql.lstrip().upper().startswith('DELETE') for sql in statements)
    for index, (row, path, group) in enumerate(rows):
        after = snapshot(row)
        assert (after['retention_archived_at'] is not None) == (index == 2)
        for key in before[index].keys() - {'retention_archived_at', 'archive_revision', 'updated_at'}:
            assert after[key] == before[index][key]
        if index == 2:
            children[index][0]['version_counter'] += 1
            children[index][1]['status'] = workflow.TO_ARCHIVE
        assert [snapshot(child) for child in group] == children[index]
        assert path.read_bytes() == files[index]
        assert ClinicArchiveEvent.query.filter_by(recording_id=row.id).count() == int(index == 2)


@pytest.mark.parametrize('status', [state for state in workflow.STATUS_ORDER if state != workflow.DONE])
def test_every_open_workflow_state_is_protected_even_with_old_release(cases, status):
    row, _, _ = cases(status=status)
    stats = retention.process_auto_deletion()
    assert stats['errors'] == 0
    assert snapshot(row)['retention_archived_at'] is None


@pytest.mark.parametrize('kwargs', [
    {'released_at': None}, {'with_workflow': False}, {'with_dictation': False},
    {'released_at': NOW}, {'released_at': NOW + timedelta(days=1)},
    {'released_at': NOW - timedelta(days=30)},
])
def test_missing_completion_or_unelapsed_release_stays(cases, kwargs):
    row, _, _ = cases(**kwargs)
    stats = retention.process_auto_deletion()
    assert stats['errors'] == 0
    assert snapshot(row)['retention_archived_at'] is None


@pytest.mark.parametrize('processing', ['COMPLETED', 'FAILED'])
def test_release_cutoff_utc_and_failed_asr(cases, processing):
    finished = (NOW - timedelta(days=30, microseconds=1)).astimezone(timezone(timedelta(hours=2)))
    row, _, _ = cases(released_at=finished, processing=processing)
    stats = retention.process_auto_deletion()
    assert stats['archived'] == 1 and stats['errors'] == 0, stats
    assert snapshot(row)['retention_archived_at'] is not None


@pytest.mark.parametrize('guard', ['disabled', 'no-policy', 'exempt', 'protected-tag', 'infinite-tag'])
def test_disabled_retention_and_existing_exemptions(cases, monkeypatch, guard):
    row, path, _ = cases()
    if guard == 'disabled':
        monkeypatch.setitem(retention.__dict__, 'ENABLE_AUTO_DELETION', False)
    elif guard == 'no-policy':
        monkeypatch.setitem(retention.__dict__, 'GLOBAL_RETENTION_DAYS', 0)
    elif guard == 'exempt':
        row.deletion_exempt = True
    else:
        tag = Tag(name=guard, user_id=row.user_id, retention_days=-1 if guard == 'infinite-tag' else 1,
                  protect_from_deletion=guard == 'protected-tag')
        db.session.add(tag)
        db.session.flush()
        db.session.add(RecordingTag(recording_id=row.id, tag_id=tag.id, order=0))
    db.session.commit()
    stats = retention.process_auto_deletion()
    assert stats.get('errors', 0) == 0
    if guard == 'disabled':
        assert stats['code'] == 'auto_deletion_disabled'
    assert snapshot(row)['retention_archived_at'] is None
    assert path.read_bytes() == b'RIFF synthetic retention bytes'
    assert ClinicArchiveEvent.query.filter_by(recording_id=row.id).count() == 0


def test_superseded_release_cannot_start_retention(cases):
    row, _, children = cases()
    released = children[-1]
    db.session.add(release.ClinicReleaseSupersession(
        release_id=released.id, reason='synthetic revision changed', superseded_at=NOW,
    ))
    db.session.commit()
    stats = retention.process_auto_deletion()
    assert stats['errors'] == 0
    assert snapshot(row)['retention_archived_at'] is None


def test_changed_revision_without_supersession_row_is_protected(cases):
    row, _, children = cases()
    released = children[-1]
    db.session.add(ClinicAudioRevision(
        dictation_id=released.dictation_id, parent_revision_id=released.audio_revision_id,
        content_hash='c' * 64,
    ))
    db.session.commit()
    stats = retention.process_auto_deletion()
    assert stats['errors'] == 0
    assert snapshot(row)['retention_archived_at'] is None


def test_owner_tag_period_also_starts_at_release(cases, monkeypatch):
    monkeypatch.setitem(retention.__dict__, 'GLOBAL_RETENTION_DAYS', 0)
    rows = [cases(released_at=NOW - timedelta(days=6)), cases(released_at=NOW - timedelta(days=8))]
    for row, _, _ in rows:
        tag = Tag(name=f'week-{row.id}', user_id=row.user_id, retention_days=7)
        db.session.add(tag)
        db.session.flush()
        db.session.add(RecordingTag(recording_id=row.id, tag_id=tag.id, order=0))
    db.session.commit()
    stats = retention.process_auto_deletion()
    assert stats['archived'] == 1 and stats['errors'] == 0, stats
    assert snapshot(rows[0][0])['retention_archived_at'] is None
    assert snapshot(rows[1][0])['retention_archived_at'] is not None


def test_clinic_off_keeps_upload_age_behavior(cases, monkeypatch):
    row, _, _ = cases(status=workflow.TO_CORRECT, released_at=None)
    monkeypatch.delenv('CLINIC_MODE')
    stats = retention.process_auto_deletion()
    assert stats['archived'] == 1 and stats['errors'] == 0, stats
    assert snapshot(row)['retention_archived_at'] is not None
