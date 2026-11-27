"""Real SQLite upgrades preserve legacy assignments and unknown timestamps."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from src.clinic import migration_assignment_stamp as migration
from src.clinic import workflow as wf


@pytest.fixture
def engine(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "assignments.sqlite"))
    try:
        yield engine
    finally:
        engine.dispose()


def legacy_rows(engine):
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE clinic_workflow_dictation (
                id VARCHAR(36) PRIMARY KEY, author_user_id INTEGER NOT NULL,
                pool_id VARCHAR(36) NOT NULL, assignee_user_id INTEGER,
                status VARCHAR(32) NOT NULL, resume_status VARCHAR(32),
                acceptance_required BOOLEAN NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 1,
                priority_unrecognized BOOLEAN NOT NULL DEFAULT 0,
                audio_seconds INTEGER NOT NULL DEFAULT 0, deadline_date DATE,
                deadline_time TIME, deadline_at DATETIME, created_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO clinic_workflow_dictation
                (id, author_user_id, pool_id, assignee_user_id, status, created_at)
            VALUES (:id, 1, 'pool-a', :assignee, 'zu_schreiben', '2020-01-01 00:00:00')
        """), [{"id": "assigned", "assignee": 42}, {"id": "unassigned", "assignee": None}])


@pytest.mark.parametrize("connection_bind", [False, True])
def test_two_upgrades_preserve_legacy_assignments(engine, connection_bind):
    legacy_rows(engine)
    if connection_bind:
        with engine.begin() as connection:
            assert len(migration.upgrade(connection)) == 1
            assert migration.upgrade(connection) == []
    else:
        assert len(migration.upgrade(engine)) == 1
        assert migration.upgrade(engine) == []
    with Session(engine) as session:
        rows = session.query(wf.ClinicWorkflowDictation).order_by(wf.ClinicWorkflowDictation.id).all()
        assert [(row.id, row.assignee_user_id, row.assigned_at) for row in rows] == [
            ("assigned", 42, None), ("unassigned", None, None),
        ]
        assert all(row.created_at == datetime(2020, 1, 1, tzinfo=timezone.utc) for row in rows)


def test_create_tables_upgrades_existing_rows(engine):
    legacy_rows(engine)
    wf.create_tables(engine)
    wf.create_tables(engine)
    with Session(engine) as session:
        row = session.get(wf.ClinicWorkflowDictation, "assigned")
        assert row.assignee_user_id == 42
        assert row.assigned_at is None


def test_missing_and_fresh_tables_and_existing_timestamp(engine):
    assert migration.upgrade(engine) == []
    wf.create_tables(engine)
    column = next(c for c in inspect(engine).get_columns(migration.TABLE) if c["name"] == "assigned_at")
    assert column["nullable"] is True
    assert column["default"] is None
    stamp = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
    with Session(engine) as session:
        row = wf.start(session, "create", author_user_id=1, pool_id="pool-a", assignee_user_id=42)
        assert row.assigned_at is None
        row.assigned_at = stamp
        row_id = row.id
        session.commit()
    assert migration.upgrade(engine) == []
    assert migration.upgrade(engine) == []
    with Session(engine) as session:
        row = session.get(wf.ClinicWorkflowDictation, row_id)
        assert row.assignee_user_id == 42
        assert row.assigned_at == stamp
