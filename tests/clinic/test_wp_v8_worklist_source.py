"""Project durable ingest receipts into the authorized worklist rows."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO

import pytest

from src.app import app, db
from src.clinic import policy, routes_worklist, workflow, worklist_entries
from src.clinic.document_ingest import attach_document
from src.clinic.ingest import IngestService
from src.clinic.models import ClinicDictation
from src.clinic.pools import ClinicAuthorPool
from src.clinic.worklist_layout import LayoutStore
from src.models import Recording, User

RECORDED_AT = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)
SYNTHETIC_AUDIO = b"synthetic dictation audio"


@pytest.fixture
def author(clinic_on):
    with app.app_context():
        user = User(username="v8124_author", email="v8124@example.invalid", password="x")
        db.session.add(user)
        db.session.flush()
        pool = ClinicAuthorPool(name="v8124 authors", created_by=user.id)
        db.session.add(pool)
        db.session.commit()
        yield user.id, pool.id


def _ingest(service, author, origin):
    user_id, _ = author
    # Web, agent, batch and backfill all persist audio through this writer.
    receipt = service.store_original(
        BytesIO(SYNTHETIC_AUDIO), declared_sha256=sha256(SYNTHETIC_AUDIO).hexdigest(),
        declared_size=len(SYNTHETIC_AUDIO), origin=origin, origin_ref=f"{origin}:synthetic",
        author_user_id=user_id, idempotency_key=f"worklist-source:{origin}", now=RECORDED_AT,
    )
    dictation = db.session.get(ClinicDictation, receipt.dictation_id)
    _add_to_worklist(dictation, author)
    return dictation.id


def _add_to_worklist(dictation, author):
    user_id, pool_id = author
    dictation.pool_id = pool_id
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=user_id, pool_id=pool_id, status=workflow.DRAFT,
    ))
    db.session.commit()


def _worklist_row(author, dictation_id):
    user_id, _ = author
    snapshot = routes_worklist.get_worklist(
        worklist_entries.load_entries(user_id), policy.build_subject(user_id), LayoutStore(),
    )
    return next(row for row in snapshot.page.items if row["dictation_id"] == dictation_id)


@pytest.mark.parametrize(("origin", "label"), [
    ("web-upload", "Upload"),
    ("web-recorder", "Mikrofon"),
    ("agent", "Import"),
    ("batch", "Import"),
    ("legacy-backfill", "Import"),
    ("live", "Live"),
    ("unknown-origin", None),
])
def test_audio_kind_and_origin_label(author, tmp_path, origin, label):
    dictation_id = _ingest(IngestService(tmp_path), author, origin)
    row = _worklist_row(author, dictation_id)
    assert (row["source_kind"], row["source_label"]) == ("audio", label)


def test_document_attached_in_the_same_second_keeps_the_audio_source(author, tmp_path):
    user_id, _ = author
    service = IngestService(tmp_path)
    dictation_id = _ingest(service, author, "web-upload")
    attach_document(
        service, dictation_id=dictation_id, actor_user_id=user_id,
        data=b"Synthetic note", filename="note.txt", expected_version=0,
        authorize=lambda dictation, actor: dictation.author_user_id == actor,
        now=RECORDED_AT + timedelta(microseconds=1),
    )

    row = _worklist_row(author, dictation_id)
    assert (row["source_kind"], row["source_label"]) == ("audio", "Upload")


def test_document_original_has_document_kind(author, tmp_path):
    user_id, _ = author
    recording = Recording(user_id=user_id, status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=user_id)
    db.session.add(dictation)
    _add_to_worklist(dictation, author)
    attach_document(
        IngestService(tmp_path), dictation_id=dictation.id, actor_user_id=user_id,
        data=b"Synthetic note", filename="note.txt", expected_version=0,
        authorize=lambda item, actor: item.author_user_id == actor, now=RECORDED_AT,
    )

    row = _worklist_row(author, dictation.id)
    assert (row["source_kind"], row["source_label"]) == ("document", None)
