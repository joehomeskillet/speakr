"""Database-level immutability guards for clinic audio edit rows (w059)."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic.audio_db_guards import (
    AUDIO_IMMUTABLE_TABLES,
    install_audio_db_guards,
    pg_trigger_name,
    remove_audio_db_guards,
    trigger_names,
)
from src.clinic.audio_revisions import (
    AudioRevisionImmutable,
    ClinicAudioActionLog,
    ClinicAudioEditOperation,
    ClinicAudioSegment,
    ClinicAudioSessionState,
    apply_cut,
    apply_insert,
    create_segment,
    get_operations,
    init_audio_revision,
    list_named_revisions,
    redo,
    undo,
)
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording, User

# Columns a raw writer would try to rewrite. Values stay inside existing checks.
_STALE_ASSIGNMENT = {
    "clinic_audio_edit_operation": "position_ms = 1",
    "clinic_audio_revision_snapshot": "name = 'stale'",
    "clinic_audio_segment": "duration_ms = 2",
    "clinic_audio_action_log": "action_type = 'UNDO'",
    "clinic_audio_revision": "content_hash = 'stale'",
}


class _OtherEngine:
    name = "mysql"


@pytest.fixture
def guards(clinic_isolation):
    """Install after the shared purge, and drop before the next purge."""
    del clinic_isolation
    with app.app_context():
        install_audio_db_guards(db.engine)
    yield
    with app.app_context():
        db.session.rollback()
        db.session.remove()
        remove_audio_db_guards(db.engine)


def _make_scene():
    suffix = uuid.uuid4().hex[:10]
    user = User(username=f"ag_u_{suffix}", email=f"{suffix}@local.test", password="x")
    db.session.add(user)
    db.session.flush()
    rec = Recording(user_id=user.id, title="guard dictation", status="PENDING")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=user.id)
    db.session.add(dictation)
    db.session.flush()
    orig = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="orig_hash_" + suffix,
        size_bytes=1024,
        storage_key=f"originals/{suffix}.wav",
        receipt="receipt",
        origin_ref="device:mic",
    )
    db.session.add(orig)
    db.session.flush()
    return dictation, orig


def _rows(table, dictation_id):
    found = db.session.execute(
        text("SELECT * FROM " + table + " WHERE dictation_id = :d"),
        {"d": dictation_id},
    ).mappings().all()
    return {row["id"]: tuple(row.values()) for row in found}


def _trigger_name_set():
    if db.engine.dialect.name == "sqlite":
        query = "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    else:
        query = "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
    return {row[0] for row in db.session.execute(text(query))}


def _expected_trigger_names(table):
    if db.engine.dialect.name == "sqlite":
        return trigger_names(table)
    return (pg_trigger_name(table),)


def test_unsupported_engines_skip_without_touching_the_database():
    install_audio_db_guards(_OtherEngine())
    remove_audio_db_guards(_OtherEngine())


def test_guards_register_once_and_leave_session_state_mutable(guards):
    with app.app_context():
        install_audio_db_guards(db.engine)
        present = _trigger_name_set()
        session_table = ClinicAudioSessionState.__tablename__
        for table in AUDIO_IMMUTABLE_TABLES:
            for name in _expected_trigger_names(table):
                assert name in present
                count_query = (
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name = :n"
                    if db.engine.dialect.name == "sqlite"
                    else "SELECT COUNT(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = :n"
                )
                assert db.session.execute(text(count_query), {"n": name}).scalar() == 1
        for name in _expected_trigger_names(session_table):
            assert name not in present


def test_stale_unauthorized_writes_roll_back_and_append_retries(guards):
    """Raw and bulk writers cannot rewrite history. A later append still works."""
    with app.app_context():
        dictation, orig = _make_scene()
        init_audio_revision(dictation, orig, 60000, name="Baseline")
        seg = create_segment(dictation, "storage/s1.wav", 5000, "hash_s1")
        rev1 = apply_insert(dictation, 10000, seg, name="Insert1")
        op_id = get_operations(dictation)[0].id
        seg_id, rev1_id, dictation_id = seg.id, rev1.id, dictation.id
        db.session.commit()
        before = {table: _rows(table, dictation_id) for table in AUDIO_IMMUTABLE_TABLES}

        operation = db.session.get(ClinicAudioEditOperation, op_id)
        operation.position_ms = 1
        with pytest.raises(AudioRevisionImmutable):
            db.session.flush()
        db.session.rollback()

        with pytest.raises(DBAPIError, match="immutable"):
            db.session.query(ClinicAudioEditOperation).filter_by(id=op_id).update(
                {"position_ms": 1}, synchronize_session=False
            )
        db.session.rollback()
        with pytest.raises(DBAPIError, match="immutable"):
            db.session.query(ClinicAudioSegment).filter_by(id=seg_id).delete(
                synchronize_session=False
            )
        db.session.rollback()

        for table, assignment in _STALE_ASSIGNMENT.items():
            row_id = next(iter(before[table]))
            with pytest.raises(DBAPIError, match="immutable"):
                db.session.execute(
                    text("UPDATE " + table + " SET " + assignment + " WHERE id = :i"),
                    {"i": row_id},
                )
            db.session.rollback()
            with pytest.raises(DBAPIError, match="immutable"):
                db.session.execute(
                    text("DELETE FROM " + table + " WHERE id = :i"),
                    {"i": row_id},
                )
            db.session.rollback()
            assert _rows(table, dictation_id) == before[table]

        dictation = db.session.get(ClinicDictation, dictation_id)
        rev2 = apply_cut(dictation, 5000, 8000, name="Cut1")
        assert rev2.parent_revision_id == rev1_id
        db.session.commit()
        revised = _rows("clinic_audio_revision", dictation_id)
        assert set(before["clinic_audio_revision"]) < set(revised)
        for row_id, payload in before["clinic_audio_revision"].items():
            assert revised[row_id] == payload
        assert {item.name for item in list_named_revisions(dictation)} == {
            "Baseline", "Insert1", "Cut1",
        }


def test_undo_redo_changes_only_head_and_stack_references(guards):
    with app.app_context():
        dictation, orig = _make_scene()
        init_rev = init_audio_revision(dictation, orig, 60000, name="Baseline")
        seg = create_segment(dictation, "storage/s1.wav", 5000, "hash_s1")
        rev1 = apply_insert(dictation, 10000, seg, name="Insert1")
        init_id, rev1_id, dictation_id = init_rev.id, rev1.id, dictation.id
        db.session.commit()
        frozen_tables = tuple(
            table for table in AUDIO_IMMUTABLE_TABLES if table != ClinicAudioActionLog.__tablename__
        )
        frozen = {table: _rows(table, dictation_id) for table in frozen_tables}
        logs_before = _rows(ClinicAudioActionLog.__tablename__, dictation_id)
        session_id = _session_row(dictation_id)["id"]

        dictation = db.session.get(ClinicDictation, dictation_id)
        undone = undo(dictation)
        db.session.commit()
        assert undone.id == init_id
        _assert_frozen(dictation_id, frozen, logs_before)
        _assert_stacks(dictation_id, session_id, init_id, "[]", json.dumps([rev1_id]))

        dictation = db.session.get(ClinicDictation, dictation_id)
        redone = redo(dictation)
        db.session.commit()
        assert redone.id == rev1_id
        _assert_frozen(dictation_id, frozen, logs_before)
        _assert_stacks(dictation_id, session_id, rev1_id, json.dumps([init_id]), "[]")

        # A direct writer may move the same three references. History stays put.
        db.session.execute(
            text(
                "UPDATE clinic_audio_session_state "
                "SET current_revision_id = :head, undo_stack_json = :undo, "
                "redo_stack_json = :redo WHERE dictation_id = :d"
            ),
            {"head": init_id, "undo": "[]", "redo": json.dumps([rev1_id]), "d": dictation_id},
        )
        db.session.commit()
        _assert_frozen(dictation_id, frozen, logs_before)
        _assert_stacks(dictation_id, session_id, init_id, "[]", json.dumps([rev1_id]))
        dictation = db.session.get(ClinicDictation, dictation_id)
        assert len(get_operations(dictation)) == 1


def _assert_frozen(dictation_id, frozen, logs_before):
    for table, payload in frozen.items():
        assert _rows(table, dictation_id) == payload
    logs_now = _rows(ClinicAudioActionLog.__tablename__, dictation_id)
    assert logs_before.keys() <= logs_now.keys()
    for row_id, payload in logs_before.items():
        assert logs_now[row_id] == payload


def _session_row(dictation_id):
    return db.session.execute(
        text(
            "SELECT id, dictation_id, current_revision_id, undo_stack_json, redo_stack_json "
            "FROM clinic_audio_session_state WHERE dictation_id = :d"
        ),
        {"d": dictation_id},
    ).mappings().one()


def _assert_stacks(dictation_id, session_id, head, undo_stack, redo_stack):
    row = _session_row(dictation_id)
    assert row["id"] == session_id
    assert row["dictation_id"] == dictation_id
    assert row["current_revision_id"] == head
    assert row["undo_stack_json"] == undo_stack
    assert row["redo_stack_json"] == redo_stack
