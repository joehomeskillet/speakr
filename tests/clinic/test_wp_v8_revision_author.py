"""V8 UI8-003/UI8-004: the revision author is stored apart from the dictating person.

Synthetic data only.
"""

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from src.app import app, db
from src.clinic import revisions, routes_history
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.models import Recording, User


@pytest.fixture
def clinic_db():
    with app.app_context():
        db.session.rollback()
        yield
        db.session.rollback()


def _user(name=None):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="ra" + suffix, email=suffix + "@example.invalid", password="x", name=name)
    db.session.add(user)
    db.session.flush()
    return user


def _case(dictator):
    recording = Recording(user_id=dictator.id, title="t", status="COMPLETED")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=dictator.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    return dictation, original


def test_typist_save_stores_typist_as_author_and_keeps_dictator(clinic_db):
    dictator, typist = _user("Diana Diktat"), _user("Tina Typ")
    dictation, original = _case(dictator)
    asr = revisions.add_asr_revision(dictation, original, "roh", "model-x")
    human = revisions.add_human_revision(dictation, asr, "korrigiert", typist.id)
    assert human.revision_author_user_id == typist.id
    assert human.created_at is not None
    assert dictation.author_user_id == dictator.id
    assert revisions.revision_author(human) == {"id": typist.id, "display_name": "Tina Typ"}
    # ASR rows have a timestamp but no human author.
    assert asr.created_at is not None and asr.revision_author_user_id is None
    assert revisions.revision_author(asr) is None


def test_history_api_lists_author_and_created_at_and_legacy_null(clinic_db):
    dictator, typist = _user(), _user("Tina Typ")
    dictation, original = _case(dictator)
    asr = revisions.add_asr_revision(dictation, original, "roh", "model-x")
    human = revisions.add_human_revision(dictation, asr, "korrigiert", typist.id)
    legacy = ClinicTranscriptRevision(
        dictation_id=dictation.id, parent_revision_id=human.id, kind="HUMAN", body="alt",
        normalised_text_hash="l" * 64, original_version_id=original.id,
    )
    db.session.add(legacy)
    db.session.flush()
    events = {e["id"]: e for e in routes_history._history(dictation) if e["kind"] == "revision"}
    assert events[human.id]["author"] == {"id": typist.id, "display_name": "Tina Typ"}
    assert events[human.id]["created_at"].endswith("+00:00")
    assert events[legacy.id]["author"] is None
    assert events[legacy.id]["created_at"] is None
    assert "body" not in events[human.id]


def test_unchanged_text_returns_tip_without_new_author(clinic_db):
    first, second = _user(), _user()
    dictation, original = _case(first)
    one = revisions.add_human_revision(dictation, None, "gleich", first.id, original_version=original)
    again = revisions.add_human_revision(dictation, one, "gleich", second.id)
    assert again.id == one.id and again.revision_author_user_id == first.id


def test_rows_stay_append_only(clinic_db):
    user = _user()
    dictation, original = _case(user)
    row = revisions.add_human_revision(dictation, None, "x", user.id, original_version=original)
    row.revision_author_user_id = None
    with pytest.raises(revisions.RevisionImmutable):
        db.session.flush()
    db.session.rollback()


def _legacy_engine(path):
    engine = create_engine("sqlite:///" + path.as_posix())
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE clinic_transcript_revision (id VARCHAR(36) PRIMARY KEY, body TEXT NOT NULL)"
        ))
        conn.execute(text("INSERT INTO clinic_transcript_revision VALUES ('a', 'eins'), ('b', 'zwei')"))
    return engine


def _columns(engine):
    return {c["name"] for c in inspect(engine).get_columns("clinic_transcript_revision")}


def test_migration_is_additive_idempotent_and_reversible(tmp_path):
    engine = _legacy_engine(tmp_path / "legacy.db")
    revisions.create_tables(bind=engine)
    revisions.create_tables(bind=engine)
    assert {"revision_author_user_id", "created_at"} <= _columns(engine)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, body, revision_author_user_id, created_at "
            "FROM clinic_transcript_revision ORDER BY id"
        )).fetchall()
    assert [tuple(r) for r in rows] == [("a", "eins", None, None), ("b", "zwei", None, None)]
    revisions.downgrade_columns(bind=engine)
    assert _columns(engine) == {"id", "body"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM clinic_transcript_revision")).scalar() == 2


def test_create_tables_noop_without_table(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "empty.db").as_posix())
    revisions.create_tables(bind=engine)
    assert "clinic_transcript_revision" not in inspect(engine).get_table_names()
