"""History order and immutability for PAR-WF-006.

These tests prove that the server side of "show the history of a dictation"
returns a complete, ordered, append-only story. They cover:

- revisions.history() returns the exact chain with correct parents and kinds
- Order stability across repeated calls and session expiration
- ORM and raw SQL UPDATE/DELETE attempts fail and history is unchanged
- Isolation: a second dictation's history never contains the first's rows
- pools.history() rows are ordered by id and not modified by later changes
- A superseded release stays readable and active_release points to the newest

What these tests do NOT prove:
- Philips device, Entra, KIS or foot pedal behaviour (no such mocks here)
- UI rendering (the history display is a separate, open piece)
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.app import app, db
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.pools import (
    ClinicPoolHistory,
    create_author_pool,
    add_author_member,
    allow,
    deny,
    history as pools_history,
)
from src.clinic.release import (
    ClinicReleaseSupersession,
    ReleaseError,
    active_release,
    create_release,
    invalidate_on_change,
)
from src.clinic.revisions import (
    RevisionError,
    RevisionImmutable,
    add_asr_revision,
    add_human_revision,
    current_revision,
    history as revisions_history,
    is_flagged,
)
from src.models import Recording, User


ALLOWED = {"allowed": True, "role": "physician", "rule": "release"}
T0 = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


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


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="hw" + suffix, email=suffix + "@example.invalid", password="x")
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


def _audio(dictation, parent=None):
    row = ClinicAudioRevision(
        dictation_id=dictation.id,
        content_hash="a" * 64,
        parent_revision_id=parent.id if parent else None,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _transcript(dictation, digest="t" * 64, parent=None, original=None, kind="ASR", body="text", model_ref="stub-asr:1"):
    if original is None:
        original = _original(dictation)
    row = ClinicTranscriptRevision(
        dictation_id=dictation.id,
        original_version_id=original.id,
        kind=kind,
        model_ref=model_ref if kind == "ASR" else None,
        body=body,
        normalised_text_hash=digest,
        parent_revision_id=parent.id if parent else None,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _context(dictation, parent=None):
    row = ClinicContextRevision(
        dictation_id=dictation.id,
        source_system="kis",
        tenant_scope="site",
        case_ref="case-1",
        patient_ref="patient-1",
        parent_revision_id=parent.id if parent else None,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _release(user, dictation, audio, transcript, context, **overrides):
    args = dict(
        dictation=dictation,
        audio_revision=audio,
        transcript_revision=transcript,
        context_revision=context,
        text_hash=transcript.normalised_text_hash,
        actor_user_id=user.id,
        authorization_decision=ALLOWED,
        now=T0,
        document_type="report",
        release_type="clinical",
    )
    args.update(overrides)
    return create_release(**args)


def test_par_wf_006_history_chain_with_asr_two_humans_and_release(clinic_db):
    """AC1: history() returns exact chain in order with right parents and kinds."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    # ASR -> Human1 -> Human2
    asr = add_asr_revision(dictation, original, "erster asr", "stub-asr:1")
    human1 = add_human_revision(dictation, asr, "erste korrektur", author.id)
    human2 = add_human_revision(dictation, human1, "zweite korrektur", author.id)

    # Create a release bound to the latest transcript
    audio = _audio(dictation)
    context = _context(dictation)
    _release(author, dictation, audio, human2, context)

    chain = revisions_history(dictation)

    assert len(chain) == 3
    assert chain[0].id == asr.id
    assert chain[0].kind == "ASR"
    assert chain[0].parent_revision_id is None
    assert chain[1].id == human1.id
    assert chain[1].kind == "HUMAN"
    assert chain[1].parent_revision_id == asr.id
    assert chain[2].id == human2.id
    assert chain[2].kind == "HUMAN"
    assert chain[2].parent_revision_id == human1.id

    # Verify bodies are in order
    assert [row.body for row in chain] == ["erster asr", "erste korrektur", "zweite korrektur"]


def test_par_wf_006_history_order_stable_across_calls_and_expire(clinic_db):
    """AC2: Order is stable across repeated calls and after session expire."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    asr = add_asr_revision(dictation, original, "asr text", "stub-asr:1")
    human1 = add_human_revision(dictation, asr, "human 1", author.id)
    human2 = add_human_revision(dictation, human1, "human 2", author.id)

    # First call
    first = revisions_history(dictation)
    first_ids = [row.id for row in first]

    # Second call without any changes
    second = revisions_history(dictation)
    second_ids = [row.id for row in second]
    assert first_ids == second_ids

    # After session expire
    db.session.expire_all()
    third = revisions_history(dictation)
    third_ids = [row.id for row in third]
    assert first_ids == third_ids

    # After another session expire
    db.session.expire_all()
    fourth = revisions_history(dictation)
    fourth_ids = [row.id for row in fourth]
    assert first_ids == fourth_ids


def test_par_wf_006_orm_update_fails_and_history_unchanged(clinic_db):
    """AC3: ORM UPDATE on existing revision fails (RevisionImmutable)."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    asr = add_asr_revision(dictation, original, "original text", "stub-asr:1")
    human = add_human_revision(dictation, asr, "human text", author.id)
    db.session.commit()  # Commit so data persists after failed update attempt

    # Snapshot before attempt
    snapshot = [
        (row.id, row.body, row.normalised_text_hash, row.parent_revision_id, row.kind)
        for row in revisions_history(dictation)
    ]

    # ORM UPDATE
    asr_fresh = db.session.get(ClinicTranscriptRevision, asr.id)
    asr_fresh.body = "tampered"
    with pytest.raises(RevisionImmutable):
        db.session.flush()
    db.session.rollback()

    # History unchanged
    after = [
        (row.id, row.body, row.normalised_text_hash, row.parent_revision_id, row.kind)
        for row in revisions_history(dictation)
    ]
    assert after == snapshot


def test_par_wf_006_orm_delete_fails_and_history_unchanged(clinic_db):
    """AC3: ORM DELETE on existing revision fails (RevisionImmutable)."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    asr = add_asr_revision(dictation, original, "original text", "stub-asr:1")
    human = add_human_revision(dictation, asr, "human text", author.id)
    db.session.commit()  # Commit so data persists after failed delete attempt

    # Snapshot before attempt
    snapshot = [
        (row.id, row.body, row.normalised_text_hash, row.parent_revision_id, row.kind)
        for row in revisions_history(dictation)
    ]

    # ORM DELETE
    human_fresh = db.session.get(ClinicTranscriptRevision, human.id)
    db.session.delete(human_fresh)
    with pytest.raises(RevisionImmutable):
        db.session.flush()
    db.session.rollback()

    # History unchanged
    after = [
        (row.id, row.body, row.normalised_text_hash, row.parent_revision_id, row.kind)
        for row in revisions_history(dictation)
    ]
    assert after == snapshot


def test_par_wf_006_bulk_update_and_raw_sql_fail_and_history_unchanged(clinic_db):
    """AC3: Bulk update and raw SQL UPDATE/DELETE fail (DBAPIError) and history is unchanged."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    asr = add_asr_revision(dictation, original, "original text", "stub-asr:1")
    human = add_human_revision(dictation, asr, "human text", author.id)
    db.session.commit()  # Need committed state for raw SQL

    revision_id = asr.id
    snapshot = [
        (row.id, row.body, row.normalised_text_hash, row.parent_revision_id, row.kind)
        for row in revisions_history(dictation)
    ]

    # Bulk UPDATE via query.update()
    with pytest.raises(DBAPIError):
        db.session.query(ClinicTranscriptRevision).filter_by(id=revision_id).update(
            {"body": "tampered"}, synchronize_session=False
        )
    db.session.rollback()

    # Raw SQL UPDATE
    with pytest.raises(DBAPIError):
        db.session.execute(
            text("UPDATE clinic_transcript_revision SET body = :b WHERE id = :i"),
            {"b": "tampered", "i": revision_id},
        )
    db.session.rollback()

    # Raw SQL DELETE
    with pytest.raises(DBAPIError):
        db.session.execute(
            text("DELETE FROM clinic_transcript_revision WHERE id = :i"),
            {"i": revision_id},
        )
    db.session.rollback()

    # History unchanged
    after = [
        (row.id, row.body, row.normalised_text_hash, row.parent_revision_id, row.kind)
        for row in revisions_history(dictation)
    ]
    assert after == snapshot


def test_par_wf_006_second_dictation_history_is_isolated(clinic_db):
    """AC4: A second dictation's history never contains the first one's rows, even with identical texts."""
    author_a = _user()
    author_b = _user()

    dict_a = _dictation(author_a)
    dict_b = _dictation(author_b)
    original_a = _original(dict_a)
    original_b = _original(dict_b)

    # Same text for both dictations
    same_text = "identischer text"

    asr_a = add_asr_revision(dict_a, original_a, same_text, "stub-asr:1")
    human_a = add_human_revision(dict_a, asr_a, "korrektur a", author_a.id)

    asr_b = add_asr_revision(dict_b, original_b, same_text, "stub-asr:1")
    human_b = add_human_revision(dict_b, asr_b, "korrektur b", author_b.id)

    history_a = revisions_history(dict_a)
    history_b = revisions_history(dict_b)

    assert len(history_a) == 2
    assert len(history_b) == 2

    ids_a = {row.id for row in history_a}
    ids_b = {row.id for row in history_b}

    # No overlap between the two histories
    assert ids_a.isdisjoint(ids_b)

    # Each history only has its own rows
    assert history_a[0].id == asr_a.id
    assert history_a[1].id == human_a.id
    assert history_b[0].id == asr_b.id
    assert history_b[1].id == human_b.id

    # Even though texts are identical, bodies are isolated
    assert history_a[0].body == same_text
    assert history_b[0].body == same_text


def test_par_wf_006_pools_history_ordered_by_id_and_immutable(clinic_db):
    """AC5: pools.history() rows for same user are ordered by id and not modified by later changes."""
    actor = _user()
    user = _user()

    pool = create_author_pool("HistPool-" + uuid.uuid4().hex[:8], actor_id=actor.id)
    add_author_member(pool.id, user.id, actor_id=actor.id)

    # First batch of changes
    allow(user.id, "all", actor_id=actor.id, actions=["play"])
    deny(user.id, "own", actor_id=actor.id, actions=["view"])

    first_snapshot = pools_history()
    first_ids = [row["id"] for row in first_snapshot]

    # Verify order by happened_at then id
    for i in range(len(first_snapshot) - 1):
        row1 = first_snapshot[i]
        row2 = first_snapshot[i + 1]
        if row1["happened_at"] == row2["happened_at"]:
            assert row1["id"] < row2["id"], "When timestamps equal, rows must be ordered by id"

    # Second batch of changes (later)
    pool2 = create_author_pool("HistPool2-" + uuid.uuid4().hex[:8], actor_id=actor.id)
    add_author_member(pool2.id, user.id, actor_id=actor.id)

    later_snapshot = pools_history()
    later_ids = [row["id"] for row in later_snapshot]

    # First snapshot rows are preserved in order at the start
    assert later_ids[:len(first_ids)] == first_ids

    # New rows appended after
    assert len(later_ids) > len(first_ids)

    # Verify the first snapshot rows' details are unchanged
    for i, row_id in enumerate(first_ids):
        original = next(r for r in first_snapshot if r["id"] == row_id)
        later = next(r for r in later_snapshot if r["id"] == row_id)
        assert original["change"] == later["change"]
        assert original["entity"] == later["entity"]
        assert original["entity_id"] == later["entity_id"]
        assert original["detail"] == later["detail"]
        assert original["actor_user_id"] == later["actor_user_id"]


def test_par_wf_006_superseded_release_stays_readable_and_active_points_to_newest(clinic_db):
    """AC6: A superseded release stays readable and active_release points to the newest."""
    author = _user()
    dictation = _dictation(author)

    audio = _audio(dictation)
    transcript1 = _transcript(dictation, digest="t1" * 64, body="version 1")
    context = _context(dictation)

    # First release
    release1 = _release(author, dictation, audio, transcript1, context)
    assert active_release(dictation).id == release1.id

    # New transcript revision
    transcript2 = _transcript(dictation, digest="t2" * 64, parent=transcript1, body="version 2")

    # Invalidate old release
    invalidate_on_change(dictation, T1)

    # Second release
    release2 = _release(author, dictation, audio, transcript2, context, now=T1)
    assert active_release(dictation).id == release2.id

    # Old release still readable with original bindings
    old = db.session.get(ClinicRelease, release1.id)
    assert old is not None
    assert old.audio_revision_id == audio.id
    assert old.transcript_revision_id == transcript1.id
    assert old.context_revision_id == context.id
    assert old.text_hash == transcript1.normalised_text_hash

    # Supersession row exists
    supersession_count = db.session.query(ClinicReleaseSupersession).filter_by(release_id=release1.id).count()
    assert supersession_count == 1

    # New release has new transcript binding
    new = db.session.get(ClinicRelease, release2.id)
    assert new.transcript_revision_id == transcript2.id
    assert new.text_hash == transcript2.normalised_text_hash


def test_par_wf_006_history_includes_side_asr_revisions(clinic_db):
    """A late ASR revision is included in history but flagged as side revision."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    asr1 = add_asr_revision(dictation, original, "first asr", "stub-asr:1")
    human = add_human_revision(dictation, asr1, "human correction", author.id)
    asr2 = add_asr_revision(dictation, original, "late asr", "stub-asr:1")

    chain = revisions_history(dictation)

    # All three revisions in history
    assert len(chain) == 3
    assert chain[0].id == asr1.id
    assert chain[1].id == human.id
    assert chain[2].id == asr2.id

    # The late ASR is a side revision (parent is human)
    assert asr2.parent_revision_id == human.id

    # Human is still the head
    assert current_revision(dictation).id == human.id
    assert is_flagged(dictation, asr2) is True
    assert is_flagged(dictation, asr1) is False
    assert is_flagged(dictation, human) is False


def test_par_wf_006_identical_consecutive_text_does_not_create_duplicate(clinic_db):
    """Identical consecutive text does not create a duplicate revision row."""
    author = _user()
    dictation = _dictation(author)
    original = _original(dictation)

    asr1 = add_asr_revision(dictation, original, "Same\ntext", "stub-asr:1")
    asr2 = add_asr_revision(dictation, original, "Same\r\ntext", "stub-asr:1")  # CR+LF normalised to LF

    assert asr2.id == asr1.id

    human1 = add_human_revision(dictation, asr1, "Human text", author.id)
    human2 = add_human_revision(dictation, human1, "Human text", author.id)  # Identical

    assert human2.id == human1.id

    chain = revisions_history(dictation)
    assert len(chain) == 2  # Only one ASR, one HUMAN


def test_par_wf_006_pool_history_immutability_via_orm(clinic_db):
    """Pool history rows cannot be updated or deleted via ORM (event listener).
    
    Raw SQL bypasses the ORM event listener; database triggers for pool history
    are not installed (only clinic_original_version and clinic_transcript_revision
    have triggers via install_clinic_guards).
    """
    actor = _user()
    pool = create_author_pool("ImmutPool-" + uuid.uuid4().hex[:8], actor_id=actor.id)

    rows_before = pools_history()
    history_id = rows_before[-1]["id"]  # The 'created' row for this pool

    # ORM UPDATE
    target = db.session.get(ClinicPoolHistory, history_id)
    target.change = "tampered"
    with pytest.raises(Exception) as exc:
        db.session.flush()
    assert "history_immutable" in str(exc.value).lower() or "immutable" in str(exc.value).lower()
    db.session.rollback()

    # ORM DELETE
    target = db.session.get(ClinicPoolHistory, history_id)
    db.session.delete(target)
    with pytest.raises(Exception) as exc:
        db.session.flush()
    assert "history_immutable" in str(exc.value).lower() or "immutable" in str(exc.value).lower()
    db.session.rollback()

    # History unchanged
    rows_after = pools_history()
    assert len(rows_after) == len(rows_before)
    for before, after in zip(rows_before, rows_after):
        assert before["id"] == after["id"]
        assert before["change"] == after["change"]
        assert before["detail"] == after["detail"]