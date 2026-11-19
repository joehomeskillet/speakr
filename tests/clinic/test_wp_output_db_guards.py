"""Database guards for output receipts, releases and release history (w061)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic.append_only_guards import APPEND_ONLY_TABLES, pg_trigger_name, trigger_names
from src.clinic.kis import LOOKUP_ABSENT, LOOKUP_CONFIRMED
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDelivery,
    ClinicDictation,
    ClinicOriginal,
    ClinicTranscriptRevision,
)
from src.clinic.output import CHANNEL_CLIPBOARD, ClinicOutputReceipt, list_outputs, record_output
from src.clinic.output_attempts import (
    FAILED,
    PENDING,
    SUCCEEDED,
    UNKNOWN,
    OutputAttemptError,
    begin_output,
    bind_output_source,
    freeze_output_attempts,
    record_output_result,
)
from src.clinic.output_db_guards import (
    MUTABLE_TABLES,
    OUTPUT_GUARDED_TABLES,
    install_output_db_guards,
    remove_output_db_guards,
)
from src.clinic.release import (
    ClinicReleaseSupersession,
    ClinicWorkStateTransfer,
    create_release,
    invalidate_on_change,
    transfer_work_state,
)
from src.clinic.revisions import add_human_revision
from src.models import Recording, User

ALLOWED = {"allowed": True, "role": "physician", "rule": "release"}
T0 = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)

# Raw writers try to rewrite one column of each protected table.
_ASSIGNMENT = {
    "clinic_output_receipt": "target = 'stale'",
    "clinic_release": "document_type = 'stale'",
    "clinic_context_revision": "case_ref = 'stale'",
    "clinic_release_supersession": "reason = 'stale'",
    "clinic_work_state_transfer": "to_user_id = from_user_id",
}


class _OtherEngine:
    name = "mysql"


@pytest.fixture
def guards(clinic_isolation):
    """Install after the shared purge, and drop before the next purge."""
    del clinic_isolation
    with app.app_context():
        install_output_db_guards(db.engine)
    yield
    with app.app_context():
        db.session.rollback()
        db.session.remove()
        remove_output_db_guards(db.engine)


def _scene():
    suffix = uuid.uuid4().hex[:10]
    actor = User(username=f"og_a_{suffix}", email=f"a{suffix}@local.test", password="x")
    other = User(username=f"og_b_{suffix}", email=f"b{suffix}@local.test", password="x")
    db.session.add_all([actor, other])
    db.session.flush()
    rec = Recording(user_id=actor.id, title="guard dictation", status="PENDING")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=actor.id)
    db.session.add(dictation)
    db.session.flush()
    audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="b" * 64, size_bytes=1,
        storage_key="originals/" + suffix, receipt="stored", origin_ref="test",
    )
    context = ClinicContextRevision(
        dictation_id=dictation.id, source_system="test", tenant_scope="site",
        case_ref="case", patient_ref="patient",
    )
    db.session.add_all([audio, original, context])
    db.session.flush()
    revision = add_human_revision(dictation, None, "Synthetischer Text", actor.id,
                                  original_version=original)
    return actor, other, dictation, audio, revision, context


def _rows(table):
    found = db.session.execute(text("SELECT * FROM " + table)).mappings().all()
    return {row["id"]: tuple(row.values()) for row in found}


def _trigger_set():
    query = (
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        if db.engine.dialect.name == "sqlite"
        else "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
    )
    return {row[0] for row in db.session.execute(text(query))}


def _expected(table):
    if db.engine.dialect.name == "sqlite":
        return trigger_names(table)
    return (pg_trigger_name(table),)


def test_unsupported_engines_skip_without_touching_the_database():
    install_output_db_guards(_OtherEngine())
    remove_output_db_guards(_OtherEngine())


def test_guards_cover_receipts_and_history_and_leave_delivery_mutable(guards):
    with app.app_context():
        install_output_db_guards(db.engine)  # idempotent
        present = _trigger_set()
        for table in (*OUTPUT_GUARDED_TABLES, ClinicOutputReceipt.__tablename__):
            assert table in APPEND_ONLY_TABLES
            for name in _expected(table):
                assert name in present
        for table in MUTABLE_TABLES:
            assert table not in APPEND_ONLY_TABLES
            for name in _expected(table):
                assert name not in present


def test_stale_raw_writes_roll_back_and_appends_retry(guards):
    with app.app_context():
        actor, other, dictation, audio, revision, context = _scene()
        release = create_release(
            dictation, audio, revision, context, revision.normalised_text_hash, actor.id,
            ALLOWED, T0, document_type="report", release_type="clinical",
        )
        transfer_work_state(dictation, actor.id, other.id, T0)
        record_output(dictation, revision, actor.id, CHANNEL_CLIPBOARD, "browser")
        db.session.add(ClinicContextRevision(
            dictation_id=dictation.id, parent_revision_id=context.id, source_system="test",
            tenant_scope="site", case_ref="case-2", patient_ref="patient",
        ))
        db.session.flush()
        # The context moved on, so the release is stale and gets a supersession mark.
        assert [r.id for r in invalidate_on_change(dictation, T1, "context_changed")] == [release.id]
        dictation_id, actor_id, other_id = dictation.id, actor.id, other.id
        revision_id = revision.id
        db.session.commit()
        before = {table: _rows(table) for table in _ASSIGNMENT}
        assert all(before.values())

        for table, assignment in _ASSIGNMENT.items():
            row_id = next(iter(before[table]))
            for statement in ("UPDATE " + table + " SET " + assignment + " WHERE id = :i",
                              "DELETE FROM " + table + " WHERE id = :i"):
                with pytest.raises(DBAPIError, match="append-only"):
                    db.session.execute(text(statement), {"i": row_id})
                db.session.rollback()
            with pytest.raises(DBAPIError, match="append-only"):
                db.session.execute(text("DELETE FROM " + table))
            db.session.rollback()
            assert _rows(table) == before[table]

        # Appends still work after the refused writes, and old rows stay as they were.
        dictation = db.session.get(ClinicDictation, dictation_id)
        assert invalidate_on_change(dictation, T1) == []
        transfer_work_state(dictation, other_id, actor_id, T1)
        revision = db.session.get(ClinicTranscriptRevision, revision_id)
        record_output(dictation, revision, actor_id, CHANNEL_CLIPBOARD, "browser")
        db.session.commit()
        assert len(list_outputs(dictation)) == 2
        for table in (ClinicWorkStateTransfer.__tablename__, ClinicOutputReceipt.__tablename__):
            after = _rows(table)
            assert before[table].items() <= after.items()
            assert len(after) == 2
        assert before[ClinicReleaseSupersession.__tablename__] == _rows(
            ClinicReleaseSupersession.__tablename__)


def test_delivery_and_attempt_status_advance_through_their_state_machine(guards):
    with app.app_context():
        actor, _other, dictation, audio, revision, context = _scene()
        release = create_release(
            dictation, audio, revision, context, revision.normalised_text_hash, actor.id,
            ALLOWED, T0, document_type="report", release_type="clinical",
        )
        delivery = ClinicDelivery(release_id=release.id, target="kis", idempotency_key="k1",
                                  payload_hash="p" * 64, state="PENDING")
        db.session.add(delivery)
        db.session.commit()
        for state in ("SENT", "UNKNOWN", "ACKNOWLEDGED"):
            db.session.execute(text("UPDATE clinic_delivery SET state = :s WHERE id = :i"),
                               {"s": state, "i": delivery.id})
            db.session.commit()
        db.session.expire_all()
        assert db.session.get(ClinicDelivery, delivery.id).state == "ACKNOWLEDGED"

        source = bind_output_source(dictation, audio, revision, context)
        attempt, = freeze_output_attempts(
            dictation, source, source, actor_user_id=actor.id, request_id="r1", allowed=True,
            mode="working_copy", targets={(CHANNEL_CLIPBOARD, "browser"): {"format": "PLAIN"}})
        key = attempt.idempotency_key
        claim = begin_output({key: attempt}, key, dictation, source, allowed=True)
        assert (attempt.status, claim.status) == (PENDING, UNKNOWN)
        failed = record_output_result(claim, claim, LOOKUP_ABSENT)
        assert failed.status == FAILED
        retry = begin_output({key: failed}, key, dictation, source, allowed=True)
        with pytest.raises(OutputAttemptError, match="unresolved claim"):
            record_output_result(retry, claim, LOOKUP_CONFIRMED)
        assert record_output_result(retry, retry, LOOKUP_CONFIRMED).status == SUCCEEDED
