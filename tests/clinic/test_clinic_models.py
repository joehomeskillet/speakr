"""DB constraints for the clinic dictation tables.

Acceptance catalog entries stay not_run. These tests cover the schema
guarantees behind T-020 and T-061.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDelivery,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicReviewLease,
    ClinicTranscriptRevision,
)
from src.models import Recording, User


@pytest.fixture
def clinic_db():
    """App context with SQLite foreign keys on for this test only."""
    with app.app_context():
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


def _account():
    suffix = uuid.uuid4().hex[:10]
    user = User(
        username="cl" + suffix,
        email=suffix + "@example.invalid",
        password="x",
    )
    db.session.add(user)
    db.session.flush()
    recording = Recording(user_id=user.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    return user, recording


def _dictation(user, recording):
    row = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
    db.session.add(row)
    db.session.flush()
    return row


def _audio(dictation, content_hash=None):
    row = ClinicAudioRevision(
        dictation_id=dictation.id,
        content_hash=content_hash or ("a" * 64),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _original(dictation):
    row = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="o" * 64,
        size_bytes=128,
        storage_key="originals/" + uuid.uuid4().hex,
        receipt="stored",
        origin_ref="device:test",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _transcript(dictation, digest=None, original=None):
    digest = digest or ("t" * 64)
    original = original or _original(dictation)
    row = ClinicTranscriptRevision(
        dictation_id=dictation.id,
        original_version_id=original.id,
        kind="ASR",
        model_ref="stub-asr:1",
        body="recognised text",
        normalised_text_hash=digest,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _context(dictation):
    row = ClinicContextRevision(
        dictation_id=dictation.id,
        source_system="kis",
        tenant_scope="site",
        case_ref="case-1",
        patient_ref="patient-1",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _release(dictation, audio, transcript, context, actor):
    row = ClinicRelease(
        dictation_id=dictation.id,
        audio_revision_id=audio.id,
        transcript_revision_id=transcript.id,
        context_revision_id=context.id,
        text_hash=transcript.normalised_text_hash,
        actor_user_id=actor.id,
        document_type="report",
        release_type="clinical",
        authorization_decision="{}",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _pair():
    author, recording_a = _account()
    _other, recording_b = _account()
    dict_a = _dictation(author, recording_a)
    dict_b = _dictation(_other, recording_b)
    audio = _audio(dict_a)
    transcript = _transcript(dict_a)
    context = _context(dict_a)
    return author, dict_a, dict_b, audio, transcript, context


def test_release_accepts_revisions_of_the_same_dictation(clinic_db):
    author, dict_a, _dict_b, audio, transcript, context = _pair()
    release = _release(dict_a, audio, transcript, context, author)
    assert release.text_hash == transcript.normalised_text_hash


@pytest.mark.parametrize("field", ["transcript", "audio", "context"])
def test_release_rejects_revision_of_another_dictation(clinic_db, field):
    author, dict_a, dict_b, audio, transcript, context = _pair()
    values = {
        "dictation_id": dict_a.id,
        "audio_revision_id": audio.id,
        "transcript_revision_id": transcript.id,
        "context_revision_id": context.id,
        "text_hash": transcript.normalised_text_hash,
        "actor_user_id": author.id,
        "document_type": "report",
        "release_type": "clinical",
        "authorization_decision": "{}",
    }
    if field == "transcript":
        foreign = _transcript(dict_b, digest="b" * 64)
        values["transcript_revision_id"] = foreign.id
        values["text_hash"] = foreign.normalised_text_hash
    elif field == "audio":
        values["audio_revision_id"] = _audio(dict_b, content_hash="c" * 64).id
    else:
        values["context_revision_id"] = _context(dict_b).id

    db.session.add(ClinicRelease(**values))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_release_rejects_text_hash_that_does_not_match_the_revision(clinic_db):
    author, dict_a, _dict_b, audio, transcript, context = _pair()
    db.session.add(ClinicRelease(
        dictation_id=dict_a.id,
        audio_revision_id=audio.id,
        transcript_revision_id=transcript.id,
        context_revision_id=context.id,
        text_hash="f" * 64,
        actor_user_id=author.id,
        document_type="report",
        release_type="clinical",
        authorization_decision="{}",
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_original_cannot_be_updated_in_place(clinic_db):
    author, recording = _account()
    dictation = _dictation(author, recording)
    original = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="a" * 64,
        size_bytes=128,
        storage_key="originals/one",
        receipt="stored",
        origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    original.content_hash = "b" * 64
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_original_cannot_be_deleted(clinic_db):
    author, recording = _account()
    original = _original(_dictation(author, recording))
    db.session.delete(original)
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_original_table_name_follows_the_contract():
    assert ClinicOriginal.__tablename__ == "clinic_original_version"


def test_transcript_rejects_original_of_another_dictation(clinic_db):
    _author, dict_a, dict_b, _audio_row, _transcript_row, _context_row = _pair()
    foreign = _original(dict_b)
    db.session.add(ClinicTranscriptRevision(
        dictation_id=dict_a.id,
        original_version_id=foreign.id,
        kind="ASR",
        model_ref="stub-asr:1",
        body="x",
        normalised_text_hash="z" * 64,
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_asr_transcript_requires_model_reference(clinic_db):
    author, recording = _account()
    dictation = _dictation(author, recording)
    original = _original(dictation)
    db.session.add(ClinicTranscriptRevision(
        dictation_id=dictation.id,
        original_version_id=original.id,
        kind="ASR",
        body="x",
        normalised_text_hash="z" * 64,
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_created_at_is_timezone_aware(clinic_db):
    author, dict_a, _dict_b, audio, transcript, context = _pair()
    release = _release(dict_a, audio, transcript, context, author)
    delivery = ClinicDelivery(
        release_id=release.id,
        target="kis",
        idempotency_key="k-" + uuid.uuid4().hex,
        payload_hash="p" * 64,
        state="SENT",
        external_document_id="doc-1",
    )
    db.session.add(delivery)
    db.session.flush()
    db.session.expire_all()
    for row in (dict_a, release, delivery):
        assert row.created_at is not None
    if db.engine.dialect.name == "postgresql":
        for row in (dict_a, release, delivery):
            assert row.created_at.tzinfo is not None


def test_lease_generation_must_increase(clinic_db):
    author, recording = _account()
    dictation = _dictation(author, recording)
    lease = ClinicReviewLease(
        dictation_id=dictation.id,
        holder_user_id=author.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        generation=1,
    )
    db.session.add(lease)
    db.session.flush()
    lease.generation = 2
    db.session.flush()
    assert lease.generation == 2
    lease.generation = 1
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_delivery_idempotency_key_is_unique(clinic_db):
    author, dict_a, _dict_b, audio, transcript, context = _pair()
    release = _release(dict_a, audio, transcript, context, author)
    key = "delivery-" + uuid.uuid4().hex
    db.session.add(ClinicDelivery(
        release_id=release.id,
        target="kis",
        idempotency_key=key,
        payload_hash="p" * 64,
        state="UNKNOWN",
    ))
    db.session.flush()
    db.session.add(ClinicDelivery(
        release_id=release.id,
        target="kis",
        idempotency_key=key,
        payload_hash="q" * 64,
        state="PENDING",
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
