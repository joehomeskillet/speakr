"""Guards for marker events and vocabulary review decisions (w063)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic.append_only_guards import APPEND_ONLY_TABLES, pg_trigger_name, trigger_names
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction import (
    ClinicMarker,
    ClinicMarkerEvent,
    CorrectionConflict,
    CorrectionError,
    add_marker,
    delete_marker,
    get_state,
    marker_events,
    move_marker,
    save_state,
)
from src.clinic.correction_db_guards import (
    HISTORY_TABLES,
    MUTABLE_TABLES,
    install_correction_db_guards,
)
from src.clinic.lease import acquire
from src.clinic.vocabulary import (
    REVIEW_STATUS_DEFERRED,
    REVIEW_STATUS_PENDING,
    ClinicVocabularyEntry,
    ClinicVocabularyReviewDecision,
    ClinicVocabularyReviewItem,
    accept_to_user,
    add_vocabulary_entry,
    defer_item,
    get_decision_history,
    submit_review_item,
    suppress_vocabulary_entry,
    undo_decision,
)
from test_correction import _scene


def _triggers(engine):
    if engine.dialect.name == "sqlite":
        rows = db.session.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
        return {row[0] for row in rows}
    rows = db.session.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
    return {row[0] for row in rows}


def _has_guard(names, table):
    if db.engine.dialect.name == "sqlite":
        update_name, delete_name = trigger_names(table)
        return update_name in names and delete_name in names
    return pg_trigger_name(table) in names


def _rejected(statement, params):
    with pytest.raises(DBAPIError) as caught:
        db.session.execute(text(statement), params)
    assert "append-only" in str(caught.value).lower()
    db.session.rollback()


@pytest.fixture
def guarded_scene(clinic_on):
    with app.app_context():
        install_correction_db_guards(db.engine)
        scene = _scene()
        scene.audio = init_audio_revision(scene.dictation, scene.original, 1000)
        scene.lease = acquire(scene.dictation.id, scene.author.id, datetime.now(timezone.utc))
        yield scene


def test_install_is_idempotent_and_leaves_mutable_tables_open():
    engine = create_engine("sqlite://")
    try:
        install_correction_db_guards(engine)
        install_correction_db_guards(engine)
    finally:
        engine.dispose()
    assert set(MUTABLE_TABLES).isdisjoint(HISTORY_TABLES)
    for name in MUTABLE_TABLES:
        assert name not in APPEND_ONLY_TABLES
    assert "clinic_marker_event" in APPEND_ONLY_TABLES
    assert "clinic_vocabulary_review_decision" in APPEND_ONLY_TABLES
    with app.app_context():
        install_correction_db_guards(db.engine)
        install_correction_db_guards(db.engine)
        names = _triggers(db.engine)
        for table in HISTORY_TABLES:
            assert _has_guard(names, table)


def test_history_rejects_direct_writes_and_undo_keeps_the_original(guarded_scene):
    scene = guarded_scene
    generation = scene.lease.generation
    audio_id = scene.audio.id
    marker = add_marker(
        scene.dictation, 200, scene.author.id,
        audio_revision_id=audio_id, lease_generation=generation,
    )
    with pytest.raises(CorrectionError, match="not permitted"):
        add_marker(
            scene.dictation, 100, scene.helper.id,
            audio_revision_id=audio_id, lease_generation=generation,
        )
    with pytest.raises(CorrectionConflict):
        move_marker(
            marker, 300, scene.author.id,
            expected_version=0, lease_generation=generation,
        )
    with pytest.raises(CorrectionConflict):
        move_marker(
            marker, 300, scene.author.id,
            expected_version=1, lease_generation=generation + 99,
        )
    assert (marker.position_ms, marker.version, marker.audio_revision_id) == (200, 1, audio_id)

    state = save_state(scene.dictation, scene.author.id, 10)
    with pytest.raises(CorrectionConflict):
        save_state(scene.dictation, scene.author.id, 80, expected_version=state.version + 1)
    saved = save_state(scene.dictation, scene.author.id, 80, expected_version=state.version)
    item = submit_review_item(
        user_id=scene.author.id, written_form="Probebegriff", spoken_form="Probe",
        session=db.session,
    )
    accept_to_user(item.id, scene.author.id, session=db.session)
    entry = add_vocabulary_entry(
        scope="user", written_form="Zweitform", spoken_form="eins",
        user_id=scene.author.id, session=db.session,
    )
    db.session.commit()

    marker_id = marker.id
    create_id = marker_events(marker)[0].id
    decision_id = get_decision_history(item.id, session=db.session)[0].id
    state_id = saved.id
    item_id = item.id
    entry_id = entry.id

    _rejected(
        "UPDATE clinic_marker_event SET new_position_ms = 1 WHERE id = :id",
        {"id": create_id},
    )
    _rejected("DELETE FROM clinic_marker_event WHERE id = :id", {"id": create_id})
    _rejected(
        "UPDATE clinic_vocabulary_review_decision SET details = 'tampered' WHERE id = :id",
        {"id": decision_id},
    )
    _rejected(
        "DELETE FROM clinic_vocabulary_review_decision WHERE id = :id",
        {"id": decision_id},
    )
    with pytest.raises(DBAPIError) as caught:
        db.session.query(ClinicMarkerEvent).filter_by(id=create_id).update(
            {"new_position_ms": 9}, synchronize_session=False,
        )
    assert "append-only" in str(caught.value).lower()
    db.session.rollback()
    with pytest.raises(DBAPIError) as caught:
        db.session.query(ClinicVocabularyReviewDecision).filter_by(id=decision_id).delete(
            synchronize_session=False,
        )
    assert "append-only" in str(caught.value).lower()
    db.session.rollback()

    db.session.execute(
        text("UPDATE clinic_marker SET label = 'Index' WHERE id = :id"), {"id": marker_id},
    )
    db.session.execute(
        text("UPDATE clinic_correction_state SET audio_position_ms = 15 WHERE id = :id"),
        {"id": state_id},
    )
    db.session.execute(
        text("UPDATE clinic_vocabulary_review_item SET information = 'offen' WHERE id = :id"),
        {"id": item_id},
    )
    db.session.execute(
        text("UPDATE clinic_vocabulary_entry SET notes = 'kept' WHERE id = :id"),
        {"id": entry_id},
    )
    db.session.rollback()

    install_correction_db_guards(db.engine)
    marker = db.session.get(ClinicMarker, marker_id)
    moved = move_marker(
        marker, 400, scene.author.id, expected_version=1, lease_generation=generation,
    )
    assert (moved.position_ms, moved.audio_revision_id) == (400, audio_id)
    delete_marker(
        moved, scene.author.id, expected_version=2, lease_generation=generation,
    )
    events = marker_events(moved)
    assert [row.action for row in events] == ["CREATE", "MOVE", "DELETE"]
    assert events[0].id == create_id
    assert all(row.audio_revision_id == audio_id for row in events)
    assert db.session.get(ClinicMarker, marker_id).audio_revision_id == audio_id

    undone = undo_decision(item.id, scene.author.id, session=db.session)
    assert undone.status == REVIEW_STATUS_PENDING
    history = get_decision_history(item.id, session=db.session)
    assert [row.id for row in history][0] == decision_id
    assert [row.action for row in history] == ["ACCEPT_USER", "UNDO"]
    original = db.session.get(ClinicVocabularyReviewDecision, decision_id)
    assert (original.action, original.new_status) == ("ACCEPT_USER", "ACCEPTED_USER")
    deferred = defer_item(item.id, scene.author.id, notes="später", session=db.session)
    assert deferred.status == REVIEW_STATUS_DEFERRED
    assert db.session.get(ClinicVocabularyReviewItem, item_id).status == REVIEW_STATUS_DEFERRED

    changed = add_vocabulary_entry(
        scope="user", written_form="Zweitform", spoken_form="zwei",
        user_id=scene.author.id, session=db.session,
    )
    assert changed.id == entry_id
    assert suppress_vocabulary_entry(entry_id, session=db.session).status == "suppressed"
    assert get_state(scene.dictation, scene.author.id).audio_position_ms == 80
    retried = save_state(
        scene.dictation, scene.author.id, 90, expected_version=saved.version,
    )
    assert retried.audio_position_ms == 90
    assert db.session.get(ClinicMarkerEvent, create_id).new_position_ms == 200
    assert db.session.get(ClinicVocabularyEntry, entry_id).notes is None
