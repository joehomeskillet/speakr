"""All-user admin dictation metadata on a real SQLite file.

Rows are inserted through the recording, workflow and import tables.
The query is not mocked. Foreign keys stay off so an unassigned delivery
can sit without an agent session; PostgreSQL is not claimed here.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.app import app
from src.clinic import workflow as wf
from src.clinic.admin_dictations import (
    FORBIDDEN_FIELDS,
    METADATA_ALLOWLIST,
    AdminDictationsQuery,
    AdminQueryError,
    query_dictations,
)
from src.clinic.import_centre import ClinicImportDelivery, ClinicImportOriginal
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import Recording

SECRETS = (
    "SECRET-TRANSCRIPT",
    "SECRET-NOTES",
    "SECRET-SUMMARY",
    "SECRET-EMBED",
    "SECRET-AUDIO-PATH",
    "SECRET-STORAGE-KEY",
    "SECRET-STORAGE-ROOT",
    "SECRET-ORIGIN-REF",
    "SECRET-RELATIVE",
    "SECRET-ROOT",
)
_BANNED_SQL = (
    "transcription",
    "notes",
    "summary",
    "speaker_embeddings",
    "audio_path",
    "storage_key",
    "storage_root",
    "relative_path",
    "content_url",
    "embedding",
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "admin-dictations.sqlite").as_posix())
    with app.app_context():
        db.metadata.create_all(bind=engine)
        store = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            yield store
        finally:
            store.close()
            engine.dispose()


def _listen(store):
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    event.listen(store.get_bind(), "before_cursor_execute", capture)
    return statements


def _recording(store, **extra):
    row = Recording(**extra)
    store.add(row)
    store.flush()
    return row


def _secrets(**extra):
    body = dict(
        transcription="SECRET-TRANSCRIPT",
        notes="SECRET-NOTES",
        summary="SECRET-SUMMARY",
        speaker_embeddings={"v": "SECRET-EMBED"},
        audio_path="SECRET-AUDIO-PATH/file.bin",
    )
    body.update(extra)
    return body


def _seed(store):
    legacy = _recording(
        store,
        user_id=11,
        title="Visite",
        mime_type="audio/wav",
        original_filename="visite.wav",
        audio_duration_seconds=12.5,
        file_size=3333,
        created_at=datetime(2026, 1, 2, 8, 0, 0),
        updated_at=datetime(2026, 1, 2, 9, 0, 0),
        **_secrets(audio_path="SECRET-AUDIO-PATH/visite.wav"),
    )
    document = _recording(
        store,
        user_id=22,
        title="Arztbrief",
        mime_type=None,
        original_filename="arztbrief.odt",
        audio_duration_seconds=None,
        file_size=2222,
        created_at=datetime(2026, 2, 2, 8, 0, 0),
        updated_at=datetime(2026, 2, 2, 8, 0, 0),
        **_secrets(audio_path="SECRET-AUDIO-PATH/arztbrief.odt"),
    )
    mystery = _recording(
        store,
        user_id=11,
        title="Ohne Format",
        mime_type=None,
        original_filename=None,
        audio_duration_seconds=None,
        file_size=None,
        created_at=datetime(2026, 2, 3, 8, 0, 0),
        updated_at=datetime(2026, 2, 3, 8, 0, 0),
        **_secrets(audio_path="SECRET-AUDIO-PATH/mystery.bin"),
    )
    pooled = _recording(
        store,
        user_id=11,
        title="Visite Alpha",
        mime_type="audio/wav",
        original_filename="alpha.wav",
        audio_duration_seconds=4,
        file_size=40,
        created_at=datetime(2026, 6, 1, 10, 0, 0),
        updated_at=datetime(2026, 6, 1, 10, 30, 0),
        **_secrets(),
    )
    store.add(ClinicDictation(
        recording_id=pooled.id,
        author_user_id=11,
        pool_id="pool-a",
        version_counter=4,
    ))
    percent = _recording(
        store,
        user_id=11,
        title="100% Visite",
        mime_type="audio/wav",
        audio_duration_seconds=1,
        file_size=10,
        created_at=datetime(2026, 3, 3, 8, 0, 0),
        updated_at=datetime(2026, 3, 3, 8, 0, 0),
    )
    base = datetime(2026, 7, 1, 12, 0, 0)
    page_rows = []
    for title, stamp in (
        ("Seite B", base),
        ("Seite A", base),
        ("Seite C", base + timedelta(seconds=1)),
        ("Seite D", base + timedelta(seconds=2)),
        ("Seite E", base + timedelta(seconds=3)),
    ):
        page_rows.append(_recording(
            store,
            user_id=40,
            title=title,
            mime_type="audio/wav",
            audio_duration_seconds=1,
            file_size=1,
            created_at=stamp,
            updated_at=stamp,
        ))
    typed = wf.start(
        store,
        "create",
        author_user_id=11,
        pool_id="pool-a",
        assignee_user_id=31,
        audio_seconds=95,
        priority_raw=128,
        deadline_on=date(2026, 5, 2),
    )
    wf.transition(store, typed.id, "send_offline")
    wf.transition(store, typed.id, "skip_recognition")
    typed.created_at = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    other = wf.start(
        store,
        "create",
        author_user_id=22,
        pool_id="pool-b",
        audio_seconds=0,
        priority_raw=0,
    )
    other.created_at = datetime(2026, 5, 3, 8, 0, tzinfo=timezone.utc)
    delivery = ClinicImportDelivery(
        ingest_id="ingest-1",
        agent_id="agent-1",
        source_id="source-1",
        root_id="SECRET-ROOT",
        relative_path="SECRET-RELATIVE/file.wav",
        status="UNASSIGNED",
        created_at=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
    )
    store.add(delivery)
    store.flush()
    store.add(ClinicImportOriginal(
        delivery_id=delivery.id,
        idempotency_key="idem-1",
        storage_root="SECRET-STORAGE-ROOT",
        storage_key="SECRET-STORAGE-KEY",
        content_hash="a" * 64,
        size_bytes=4096,
        origin="agent",
        origin_ref="SECRET-ORIGIN-REF",
        created_at=delivery.created_at,
    ))
    store.flush()
    return {
        "legacy": legacy,
        "document": document,
        "mystery": mystery,
        "pooled": pooled,
        "percent": percent,
        "page": page_rows,
        "typed": typed,
        "other": other,
        "delivery": delivery,
    }


def _by_id(page):
    return {row.id: row for row in page.items}


def _rec_id(row) -> str:
    return f"recording:{row.id:010d}"


def _payload(page) -> str:
    return json.dumps(page.as_dict())


def test_projects_legacy_unassigned_and_document_rows(session, monkeypatch):
    seeded = _seed(session)
    monkeypatch.setattr(
        Recording,
        "get_audio_duration",
        lambda self, allow_probe_fallback=False: (_ for _ in ()).throw(AssertionError("probed")),
    )
    monkeypatch.setattr(
        "src.utils.ffprobe.get_duration",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("probed")),
    )
    statements = _listen(session)
    page = query_dictations(session, AdminDictationsQuery(page_size=50))
    blob = _payload(page)
    sql = "\n".join(statements).lower()
    for token in _BANNED_SQL:
        assert token not in sql
    for secret in SECRETS:
        assert secret not in blob
    assert "arztbrief.odt" not in blob
    assert "visite.wav" not in blob
    assert any("LIMIT" in statement.upper() for statement in statements)
    rows = _by_id(page)
    legacy = rows[_rec_id(seeded["legacy"])]
    assert list(legacy.as_dict()) == list(METADATA_ALLOWLIST)
    assert legacy.kind == "legacy"
    assert legacy.recording_id == seeded["legacy"].id
    assert legacy.name == "Visite"
    assert legacy.author_user_id == 11
    assert legacy.typist_user_id is None
    assert legacy.pool_id is None
    assert legacy.status is None
    assert legacy.priority is None
    assert legacy.deadline_at is None
    assert legacy.duration_seconds == 12.5
    assert legacy.size_bytes == 3333
    assert legacy.original_format == "audio/wav"
    document = rows[_rec_id(seeded["document"])]
    assert document.kind == "document"
    assert document.author_user_id == 22
    assert document.duration_seconds is None
    assert document.original_format == "odt"
    assert document.size_bytes == 2222
    assert document.status is None
    mystery = rows[_rec_id(seeded["mystery"])]
    assert mystery.kind == "legacy"
    assert mystery.duration_seconds is None
    assert mystery.original_format is None
    assert mystery.size_bytes is None
    unassigned = rows["unassigned:" + seeded["delivery"].id]
    assert unassigned.kind == "unassigned"
    assert unassigned.name is None
    assert unassigned.author_user_id is None
    assert unassigned.typist_user_id is None
    assert unassigned.pool_id is None
    assert unassigned.status is None
    assert unassigned.priority is None
    assert unassigned.deadline_at is None
    assert unassigned.updated_at is None
    assert unassigned.duration_seconds is None
    assert unassigned.original_format is None
    assert unassigned.recording_id is None
    assert unassigned.size_bytes == 4096
    typed = rows["workflow:" + seeded["typed"].id]
    assert typed.kind == "workflow"
    assert typed.name is None
    assert typed.author_user_id == 11
    assert typed.typist_user_id == 31
    assert typed.pool_id == "pool-a"
    assert typed.status == wf.TO_WRITE
    assert typed.priority == 128
    assert typed.deadline_at is not None and "2026-05-02" in typed.deadline_at
    assert typed.duration_seconds == 95
    assert typed.size_bytes is None
    assert typed.original_format is None
    assert typed.updated_at is None
    assert typed.recording_id is None
    other = rows["workflow:" + seeded["other"].id]
    assert other.author_user_id == 22
    assert other.pool_id == "pool-b"
    assert other.typist_user_id is None
    assert other.priority == 0
    assert other.duration_seconds == 0
    authors = {row.author_user_id for row in page.items}
    assert {11, 22, 40, None} <= authors
    pools = {row.pool_id for row in page.items}
    assert {"pool-a", "pool-b", None} <= pools


def test_combined_filters_and_literal_percent(session):
    seeded = _seed(session)
    typed = query_dictations(session, AdminDictationsQuery(
        author_user_id=11,
        typist_user_id=31,
        pool_id="pool-a",
        status=wf.TO_WRITE,
        priority=128,
        deadline_on=date(2026, 5, 2),
        created_on=date(2026, 5, 1),
        page_size=20,
    ))
    assert typed.total == 1
    assert typed.items[0].id == "workflow:" + seeded["typed"].id
    pooled = query_dictations(session, AdminDictationsQuery(
        name="Visite",
        author_user_id=11,
        pool_id="pool-a",
        created_on=date(2026, 6, 1),
        updated_on=date(2026, 6, 1),
    ))
    assert pooled.total == 1
    assert pooled.items[0].recording_id == seeded["pooled"].id
    assert pooled.items[0].pool_id == "pool-a"
    only_b = query_dictations(session, AdminDictationsQuery(pool_id="pool-b"))
    assert only_b.total == 1
    assert only_b.items[0].author_user_id == 22
    wildcard = query_dictations(session, AdminDictationsQuery(name="%"))
    assert wildcard.total == 1
    assert wildcard.items[0].name == "100% Visite"
    assert query_dictations(session, AdminDictationsQuery(name="_")).total == 0
    literal = query_dictations(session, AdminDictationsQuery(name="100%"))
    assert literal.total == 1
    assert literal.items[0].name == "100% Visite"
    assert query_dictations(session, AdminDictationsQuery(name="keine-treffer")).total == 0


def test_paging_is_bounded_and_id_breaks_ties(session):
    seeded = _seed(session)
    spec = dict(name="Seite", page_size=2)
    first = query_dictations(session, AdminDictationsQuery(page=1, **spec))
    second = query_dictations(session, AdminDictationsQuery(page=2, **spec))
    third = query_dictations(session, AdminDictationsQuery(page=3, **spec))
    empty = query_dictations(session, AdminDictationsQuery(page=4, **spec))
    assert first.total == second.total == third.total == empty.total == 5
    assert len(first.items) == 2
    assert len(second.items) == 2
    assert len(third.items) == 1
    assert empty.items == ()
    assert [row.recording_id for row in first.items] == [
        seeded["page"][0].id,
        seeded["page"][1].id,
    ]
    assert first.items[0].name == "Seite B"
    seen = [row.id for row in (*first.items, *second.items, *third.items)]
    assert len(seen) == len(set(seen)) == 5
    assert seen == sorted(seen)


@pytest.mark.parametrize(
    "spec",
    (
        AdminDictationsQuery(page=0),
        AdminDictationsQuery(page=-1),
        AdminDictationsQuery(page=10_001),
        AdminDictationsQuery(page_size=0),
        AdminDictationsQuery(page_size=101),
        AdminDictationsQuery(page_size=-1),
        {"page": True},
        {"page_size": False},
    ),
)
def test_invalid_pagination_raises_before_sql(session, spec):
    statements = _listen(session)
    with pytest.raises(AdminQueryError) as caught:
        query_dictations(session, spec)
    assert caught.value.code == "invalid_pagination"
    assert statements == []


@pytest.mark.parametrize(
    "raw",
    (
        {"status": "PENDING"},
        {"priority": 2},
        {"priority": True},
        {"author_user_id": True},
        {"author_user_id": 0},
        {"pool_id": " pool"},
        {"pool_id": "x" * 37},
        {"deadline_on": datetime(2026, 5, 2, tzinfo=timezone.utc)},
        {"name": "n" * 201},
        {"folder": "x"},
    ),
)
def test_invalid_filters_raise(session, raw):
    with pytest.raises(AdminQueryError) as caught:
        query_dictations(session, raw)
    assert caught.value.code == "invalid_filter"


@pytest.mark.parametrize("field", sorted(FORBIDDEN_FIELDS))
def test_content_fields_are_rejected_before_a_query(session, field):
    statements = _listen(session)
    with pytest.raises(AdminQueryError) as caught:
        query_dictations(session, {field: "secret"})
    assert caught.value.code == "forbidden_field"
    assert statements == []


def test_read_does_not_change_version_or_rows(session):
    seeded = _seed(session)
    recordings = session.query(func.count(Recording.id)).scalar()
    updated = session.get(Recording, seeded["pooled"].id).updated_at
    version = session.get(ClinicDictation, session.query(ClinicDictation).one().id).version_counter
    priority = session.get(wf.ClinicWorkflowDictation, seeded["typed"].id).priority
    query_dictations(session, AdminDictationsQuery())
    session.expire_all()
    assert session.query(func.count(Recording.id)).scalar() == recordings
    assert session.get(Recording, seeded["pooled"].id).updated_at == updated
    assert session.get(ClinicDictation, session.query(ClinicDictation).one().id).version_counter == version == 4
    assert session.get(wf.ClinicWorkflowDictation, seeded["typed"].id).priority == priority


def test_database_error_propagates(session, monkeypatch):
    def boom(*_args, **_kwargs):
        raise SQLAlchemyError("temporary")

    monkeypatch.setattr(session, "execute", boom)
    with pytest.raises(SQLAlchemyError, match="temporary"):
        query_dictations(session, AdminDictationsQuery())
