"""Synthetic evidence in a real database; not a live ASR accuracy claim."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic import correction_recognition_marks as marks
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal
from src.clinic.registry import create_clinic_tables
from src.clinic.revisions import add_asr_revision
from src.models import Recording, User


@pytest.fixture
def scene():
    with app.app_context():
        create_clinic_tables()
        tag = uuid.uuid4().hex
        user = User(username=tag[:20], email=tag + "@example.invalid", password="x")
        db.session.add(user)
        db.session.flush()
        recording = Recording(user_id=user.id, title="Synthetic recognition evidence")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash="a" * 64, size_bytes=1,
            storage_key=tag, receipt="synthetic", origin_ref="fixture",
        )
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="b" * 64)
        db.session.add_all([original, audio])
        db.session.flush()
        transcript = add_asr_revision(dictation, original, "Erster Lauf", "fixture-asr:1")
        yield dictation, audio, transcript, original
        db.session.rollback()


def _add(scene, **changes):
    values = dict(run_id="run-1", start_ms=10, end_ms=20,
                  kind="recognition", source="fixture-asr:1/segment/0")
    values.update(changes)
    return marks.add_mark(*scene[:3], **values)


def _read(scene, run_id="run-1"):
    return marks.marks_for(*(row.id for row in scene[:3]), run_id=run_id)


def test_round_trip_kinds_source_and_unknown_confidence(scene):
    for kind in marks.MARK_KINDS:
        _add(scene, kind=kind)
    zero = _add(scene, confidence=0, start_ms=0, end_ms=1)
    db.session.commit()
    db.session.expire_all()
    rows = _read(scene)
    assert rows[0].id == zero.id
    assert rows[0].confidence == 0
    assert {row.kind for row in rows} == set(marks.MARK_KINDS)
    assert all(row.confidence is None for row in rows[1:])
    assert all(row.source == "fixture-asr:1/segment/0" for row in rows)
    assert all(row.audio_revision_id == scene[1].id for row in rows)
    assert all(row.transcript_revision_id == scene[2].id for row in rows)


def test_new_runs_and_revisions_preserve_old_evidence(scene):
    old = _add(scene)
    second = _add(scene, run_id="run-2", kind="uncertain", confidence=0.2)
    dictation, audio, transcript, original = scene
    new_text = add_asr_revision(dictation, original, "Zweiter Lauf", "fixture-asr:2")
    new_scene = (dictation, audio, new_text, original)
    newer = _add(new_scene, run_id="run-3", source="fixture-asr:2/segment/0")
    new_audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="c" * 64)
    db.session.add(new_audio)
    db.session.flush()
    db.session.commit()
    db.session.expire_all()
    assert [row.id for row in _read(scene)] == [old.id]
    assert [row.id for row in _read(scene, "run-2")] == [second.id]
    assert [row.id for row in _read(new_scene, "run-3")] == [newer.id]
    assert _read(new_scene) == []
    assert _read((dictation, new_audio, transcript)) == []
    assert marks.marks_for("other", audio.id, transcript.id, run_id="run-1") == []
    assert old.kind == "recognition" and old.confidence is None
    assert old.source == "fixture-asr:1/segment/0"


@pytest.mark.parametrize("changes", [
    {"start_ms": -1}, {"start_ms": True}, {"end_ms": 10}, {"end_ms": 1.5},
    {"kind": "invented"}, {"source": " "}, {"source": None}, {"run_id": ""},
    {"confidence": -0.1}, {"confidence": 1.1}, {"confidence": True},
    {"confidence": float("nan")}, {"confidence": float("inf")},
])
def test_invalid_evidence_is_rejected_without_inserting(scene, changes):
    with pytest.raises(marks.RecognitionMarkError):
        _add(scene, **changes)
    assert _read(scene) == []


def test_foreign_or_unsaved_revision_is_rejected(scene):
    foreign = ClinicAudioRevision(dictation_id="other", content_hash="d" * 64)
    with pytest.raises(marks.RecognitionMarkError):
        _add((scene[0], foreign, scene[2]))
    original_dictation_id = scene[1].dictation_id
    scene[1].dictation_id = "other"
    with pytest.raises(marks.RecognitionMarkError, match="another dictation"):
        _add(scene)
    scene[1].dictation_id = original_dictation_id


@pytest.mark.parametrize("operation", ["orm_update", "orm_delete", "sql_update", "sql_delete"])
def test_evidence_is_append_only(scene, operation):
    row = _add(scene)
    db.session.commit()
    row_id = row.id
    if operation.startswith("orm"):
        if operation == "orm_update":
            row.source = "tampered"
        else:
            db.session.delete(row)
        with pytest.raises(marks.RecognitionMarkError):
            db.session.flush()
    else:
        statement = (
            "UPDATE clinic_recognition_mark SET source = 'tampered' WHERE id = :id"
            if operation == "sql_update" else "DELETE FROM clinic_recognition_mark WHERE id = :id"
        )
        with pytest.raises(DBAPIError):
            db.session.execute(text(statement), {"id": row_id})
    db.session.rollback()
    assert db.session.get(marks.ClinicRecognitionMark, row_id).source == "fixture-asr:1/segment/0"
