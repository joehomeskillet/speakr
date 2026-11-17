"""Append-only clinic transcript revisions.

Acceptance catalog entries stay not_run. These tests cover the service
rules behind version control (T-017) and a same-dictation parent link (T-020).
"""

import hashlib
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.revisions import (
    RevisionError,
    RevisionImmutable,
    add_asr_revision,
    add_human_revision,
    current_revision,
    history,
    is_flagged,
    normalise_text,
    text_hash,
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


def _count(dictation):
    return (
        db.session.query(ClinicTranscriptRevision)
        .filter_by(dictation_id=dictation.id)
        .count()
    )


def _scene():
    author, recording = _account()
    dictation = _dictation(author, recording)
    return author, dictation, _original(dictation)


def test_text_hash_is_nfc_and_lf_and_changes_nothing_else():
    nfd = "Cafe\u0301\r\nline\r"
    nfc = "Caf\u00e9\nline\n"
    assert normalise_text(nfd) == nfc
    assert text_hash(nfd) == text_hash(nfc)
    assert text_hash("a\r\nb") == text_hash("a\nb")
    assert text_hash("a\rb") == text_hash("a\nb")
    assert text_hash("a\nb") == hashlib.sha256(b"a\nb").hexdigest()
    assert normalise_text("  Ä  ") == "  Ä  "
    assert text_hash("A") != text_hash("a")
    assert text_hash("a ") != text_hash("a")
    with pytest.raises(TypeError):
        normalise_text(None)


def test_parent_chain_and_history_order(clinic_db):
    author, dictation, original = _scene()
    assert current_revision(dictation) is None
    assert history(dictation) == []

    asr = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    human = add_human_revision(dictation, asr, "beta", author.id)

    assert asr.parent_revision_id is None
    assert human.parent_revision_id == asr.id
    assert human.kind == "HUMAN"
    assert human.model_ref is None
    assert human.original_version_id == original.id
    assert [row.id for row in history(dictation)] == [asr.id, human.id]
    assert current_revision(dictation).id == human.id


def test_late_asr_does_not_replace_a_human_correction(clinic_db):
    author, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "recognised", "stub-asr:1")
    human = add_human_revision(dictation, asr, "corrected", author.id)
    late = add_asr_revision(dictation, original, "late hypothesis", "stub-asr:1")

    db.session.refresh(human)
    db.session.refresh(asr)
    assert current_revision(dictation).id == human.id
    assert human.body == "corrected"
    assert asr.body == "recognised"
    assert late.id != human.id
    assert late.kind == "ASR"
    assert late.body == "late hypothesis"
    assert late.parent_revision_id == human.id
    assert is_flagged(dictation, late) is True
    assert is_flagged(dictation, asr) is False
    assert is_flagged(dictation, human) is False
    assert [row.body for row in history(dictation)] == [
        "recognised",
        "corrected",
        "late hypothesis",
    ]
    assert [row.body for row in history(dictation) if row.kind == "ASR"] == [
        "recognised",
        "late hypothesis",
    ]
    assert _count(dictation) == 3


def test_identical_consecutive_text_is_not_duplicated(clinic_db):
    author, dictation, original = _scene()
    first = add_asr_revision(dictation, original, "Same\r\nline", "stub-asr:1")
    second = add_asr_revision(dictation, original, "Same\nline", "stub-asr:1")
    assert second.id == first.id
    assert first.body == "Same\nline"
    assert first.normalised_text_hash == text_hash("Same\r\nline")

    nfd = "Cafe\u0301"
    third = add_asr_revision(dictation, original, nfd, "stub-asr:1")
    again = add_asr_revision(dictation, original, "Caf\u00e9", "stub-asr:1")
    assert again.id == third.id
    assert third.body == "Caf\u00e9"
    assert _count(dictation) == 2

    confirmed = add_human_revision(dictation, third, "Caf\u00e9", author.id)
    assert confirmed.id == third.id
    changed = add_human_revision(dictation, third, "Caf\u00e9.", author.id)
    assert changed.parent_revision_id == third.id
    repeated = add_human_revision(dictation, changed, "Caf\u00e9.", author.id)
    assert repeated.id == changed.id
    reverted = add_human_revision(dictation, changed, "Same\nline", author.id)
    assert reverted.id != first.id
    assert reverted.parent_revision_id == changed.id
    assert _count(dictation) == 4


def test_append_does_not_change_an_existing_revision(clinic_db):
    author, dictation, original = _scene()
    first = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    snapshot = (
        first.id,
        first.body,
        first.normalised_text_hash,
        first.parent_revision_id,
        first.kind,
        first.model_ref,
        first.original_version_id,
    )
    add_human_revision(dictation, first, "beta", author.id)
    db.session.refresh(first)
    assert snapshot == (
        first.id,
        first.body,
        first.normalised_text_hash,
        first.parent_revision_id,
        first.kind,
        first.model_ref,
        first.original_version_id,
    )


def test_update_of_a_revision_is_rejected(clinic_db):
    _author, dictation, original = _scene()
    revision = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    revision.body = "tampered"
    with pytest.raises(RevisionImmutable):
        db.session.flush()


def test_delete_of_a_revision_is_rejected(clinic_db):
    _author, dictation, original = _scene()
    revision = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    db.session.delete(revision)
    with pytest.raises(RevisionImmutable):
        db.session.flush()


def test_bulk_update_and_raw_sql_of_a_revision_are_rejected_by_the_database(clinic_db):
    _author, dictation, original = _scene()
    revision = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    db.session.commit()
    revision_id = revision.id
    with pytest.raises(DBAPIError):
        db.session.query(ClinicTranscriptRevision).filter_by(id=revision_id).update(
            {"body": "tampered"}, synchronize_session=False
        )
    db.session.rollback()
    with pytest.raises(DBAPIError):
        db.session.execute(
            text("DELETE FROM clinic_transcript_revision WHERE id = :i"),
            {"i": revision_id},
        )
    db.session.rollback()
    assert db.session.get(ClinicTranscriptRevision, revision_id).body == "alpha"


def test_revision_of_another_dictation_cannot_be_parent(clinic_db):
    author_a, recording_a = _account()
    author_b, recording_b = _account()
    dict_a = _dictation(author_a, recording_a)
    dict_b = _dictation(author_b, recording_b)
    original_a = _original(dict_a)
    original_b = _original(dict_b)
    foreign = add_asr_revision(dict_b, original_b, "other", "stub-asr:1")

    with pytest.raises(RevisionError):
        add_human_revision(dict_a, foreign, "nope", author_a.id)
    with pytest.raises(RevisionError):
        add_asr_revision(
            dict_a, original_a, "nope", "stub-asr:1", parent_revision=foreign
        )
    with pytest.raises(RevisionError):
        add_asr_revision(dict_a, original_b, "nope", "stub-asr:1")
    with pytest.raises(RevisionError):
        add_human_revision(dict_a, None, "nope", 0, original_version=original_a)

    assert _count(dict_a) == 0
    assert foreign.body == "other"
    assert _count(dict_b) == 1
