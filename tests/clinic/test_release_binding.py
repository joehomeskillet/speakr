"""Version-bound release: binding, invalidation, idempotency, transfer separation.

Acceptance catalog entries (T-019, T-020, T-061, T-062) stay not_run. These
tests cover the service and schema parts behind T-020 and T-061.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.release import (
    ClinicReleaseSupersession,
    ClinicWorkStateTransfer,
    ReleaseError,
    active_release,
    create_release,
    invalidate_on_change,
    transfer_work_state,
)
from src.models import Recording, User

ALLOWED = {"allowed": True, "role": "physician", "rule": "release"}
T0 = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def clinic_db():
    with app.app_context():
        db.create_all()  # new release tables, checkfirst
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="rl" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _dictation(user):
    recording = Recording(user_id=user.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
    db.session.add(row)
    db.session.flush()
    return row


def _add(row):
    db.session.add(row)
    db.session.flush()
    return row


def _audio(dictation, parent=None):
    return _add(ClinicAudioRevision(
        dictation_id=dictation.id, content_hash="a" * 64,
        parent_revision_id=parent.id if parent else None))


def _transcript(dictation, digest="t" * 64, parent=None):
    original = _add(ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex, receipt="stored",
        origin_ref="device:test"))
    return _add(ClinicTranscriptRevision(
        dictation_id=dictation.id, original_version_id=original.id, kind="ASR",
        model_ref="stub-asr:1", body="text", normalised_text_hash=digest,
        parent_revision_id=parent.id if parent else None))


def _context(dictation, parent=None):
    return _add(ClinicContextRevision(
        dictation_id=dictation.id, source_system="kis", tenant_scope="site",
        case_ref="case-1", patient_ref="patient-1",
        parent_revision_id=parent.id if parent else None))


def _setup():
    user = _user()
    dictation = _dictation(user)
    return user, dictation, _audio(dictation), _transcript(dictation), _context(dictation)


def _release(user, dictation, audio, transcript, context, **overrides):
    args = dict(
        dictation=dictation, audio_revision=audio, transcript_revision=transcript,
        context_revision=context, text_hash=transcript.normalised_text_hash,
        actor_user_id=user.id, authorization_decision=ALLOWED, now=T0,
        document_type="report", release_type="clinical",
    )
    args.update(overrides)
    return create_release(**args)


def test_release_binds_exact_versions_and_stores_decision(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    release = _release(user, dictation, audio, transcript, context)
    assert release.audio_revision_id == audio.id
    assert release.transcript_revision_id == transcript.id
    assert release.context_revision_id == context.id
    assert release.text_hash == transcript.normalised_text_hash
    assert json.loads(release.authorization_decision)["allowed"] is True
    assert active_release(dictation).id == release.id


def test_release_binds_the_selected_audio_after_undo(clinic_db):
    from src.clinic import audio_revisions

    user = _user()
    dictation = _dictation(user)
    transcript, context = _transcript(dictation), _context(dictation)
    original = db.session.get(ClinicOriginal, transcript.original_version_id)
    initial = audio_revisions.init_audio_revision(dictation, original, 2000)
    edited = audio_revisions.apply_cut(dictation, 500, 1000)
    assert audio_revisions.undo(dictation).id == initial.id
    with pytest.raises(ReleaseError):
        _release(user, dictation, edited, transcript, context)
    stored = _release(user, dictation, initial, transcript, context)
    assert active_release(dictation).id == stored.id
    assert audio_revisions.redo(dictation).id == edited.id
    assert active_release(dictation) is None


def test_foreign_transcript_fails_at_database_constraint(clinic_db):
    """T-061: the service check is bypassed, the composite FK still rejects."""
    user, dictation_a, audio, _own, context = _setup()
    dictation_b = _dictation(user)
    foreign = _transcript(dictation_b)
    with pytest.raises(IntegrityError):
        with db.session.begin_nested():
            db.session.add(ClinicRelease(
                dictation_id=dictation_a.id, audio_revision_id=audio.id,
                transcript_revision_id=foreign.id, context_revision_id=context.id,
                text_hash=foreign.normalised_text_hash, actor_user_id=user.id,
                document_type="report", release_type="clinical",
                authorization_decision="{}"))
    count = db.session.query(ClinicRelease).filter_by(dictation_id=dictation_a.id).count()
    assert count == 0


def test_service_refuses_foreign_revision(clinic_db):
    user, dictation_a, audio, _own, context = _setup()
    foreign = _transcript(_dictation(user))
    with pytest.raises(ReleaseError, match="another dictation"):
        _release(user, dictation_a, audio, foreign, context)


def test_hash_mismatch_refused(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    with pytest.raises(ReleaseError, match="hash"):
        _release(user, dictation, audio, transcript, context, text_hash="x" * 64)
    assert db.session.query(ClinicRelease).filter_by(dictation_id=dictation.id).count() == 0


@pytest.mark.parametrize("decision", [None, {}, {"allowed": False}, {"allowed": "yes"}, "ok"])
def test_missing_or_denied_authorisation_refused(clinic_db, decision):
    user, dictation, audio, transcript, context = _setup()
    with pytest.raises(ReleaseError, match="authorisation"):
        _release(user, dictation, audio, transcript, context,
                 authorization_decision=decision)
    assert db.session.query(ClinicRelease).filter_by(dictation_id=dictation.id).count() == 0


def test_naive_datetime_refused(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    with pytest.raises(ReleaseError, match="timezone"):
        _release(user, dictation, audio, transcript, context, now=datetime(2026, 1, 1))


def test_stale_lease_refused(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    with pytest.raises(ReleaseError, match="lease"):
        _release(user, dictation, audio, transcript, context, lease_check=lambda: False)


@pytest.mark.parametrize("kind", ["audio", "transcript", "context"])
def test_new_revision_makes_release_inactive(clinic_db, kind):
    user, dictation, audio, transcript, context = _setup()
    release = _release(user, dictation, audio, transcript, context)
    if kind == "audio":
        _audio(dictation, parent=audio)
    elif kind == "transcript":
        _transcript(dictation, digest="n" * 64, parent=transcript)
    else:
        _context(dictation, parent=context)
    assert active_release(dictation) is None

    changed = invalidate_on_change(dictation, T1)
    assert [row.id for row in changed] == [release.id]
    assert invalidate_on_change(dictation, T1) == []  # idempotent
    assert (
        db.session.query(ClinicReleaseSupersession)
        .filter_by(release_id=release.id)
        .count()
        == 1
    )

    kept = db.session.get(ClinicRelease, release.id)
    assert kept.audio_revision_id == audio.id
    assert kept.transcript_revision_id == transcript.id
    assert kept.context_revision_id == context.id
    assert kept.text_hash == transcript.normalised_text_hash


def test_release_of_stale_revision_refused(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    _transcript(dictation, digest="n" * 64, parent=transcript)
    with pytest.raises(ReleaseError, match="not the current head"):
        _release(user, dictation, audio, transcript, context)


def test_new_release_after_change_is_active_old_stays_readable(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    old = _release(user, dictation, audio, transcript, context)
    new_transcript = _transcript(dictation, digest="n" * 64, parent=transcript)
    invalidate_on_change(dictation, T1)
    new = _release(user, dictation, audio, new_transcript, context, now=T1)
    assert active_release(dictation).id == new.id
    assert db.session.get(ClinicRelease, old.id).transcript_revision_id == transcript.id


def test_idempotent_retry_returns_existing(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    first = _release(user, dictation, audio, transcript, context)
    again = _release(user, dictation, audio, transcript, context, now=T1)
    assert again.id == first.id
    assert db.session.query(ClinicRelease).filter_by(dictation_id=dictation.id).count() == 1


def test_other_actor_gets_own_release(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    other = _user()
    first = _release(user, dictation, audio, transcript, context)
    second = _release(other, dictation, audio, transcript, context)
    assert second.id != first.id


def test_transfer_does_not_create_release(clinic_db):
    user, dictation, audio, transcript, context = _setup()
    other = _user()
    transfer = transfer_work_state(dictation, user.id, other.id, T0)
    assert isinstance(transfer, ClinicWorkStateTransfer)
    assert db.session.query(ClinicRelease).filter_by(dictation_id=dictation.id).count() == 0
    assert active_release(dictation) is None
    with pytest.raises(ReleaseError, match="lease"):
        transfer_work_state(dictation, user.id, other.id, T0, lease_check=lambda: False)
