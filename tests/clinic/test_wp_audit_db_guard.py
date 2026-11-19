"""w065: raw UPDATE/DELETE on audit_event is rejected, appends still work.

Runs on an in-memory SQLite engine with the real ``AuditEvent`` table. This is
trigger evidence only; PostgreSQL role enforcement is proven by
``test_audit_database_role.py`` (CLINIC_AUDIT_DB_PROOF=1) and is not claimed here.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from src.clinic import append_only_guards
from src.clinic.audit_db_guards import (
    AUDIT_TABLE,
    install_audit_db_guards,
    missing_audit_guards,
    remove_audit_db_guards,
)
from src.models.audit import AuditEvent

_INSERT = text(
    "INSERT INTO audit_event (created_at, event, event_group, actor_kind, outcome) "
    "VALUES (CURRENT_TIMESTAMP, 'recording.downloaded', 'access', 'system', 'success')"
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    AuditEvent.__table__.create(eng)
    yield eng
    remove_audit_db_guards(eng)
    eng.dispose()


def _count(eng) -> int:
    with eng.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM audit_event")).scalar()


def test_gap_before_install_then_closed(engine):
    with engine.begin() as conn:
        conn.execute(_INSERT)
        conn.execute(text("UPDATE audit_event SET outcome = 'failed'"))  # gap exposed
    assert len(missing_audit_guards(engine)) == 2
    install_audit_db_guards(engine)
    assert missing_audit_guards(engine) == []


@pytest.mark.parametrize("statement", [
    "UPDATE audit_event SET outcome = 'failed'",
    "DELETE FROM audit_event",
])
def test_update_delete_rejected_and_row_unchanged(engine, statement):
    install_audit_db_guards(engine)
    with engine.begin() as conn:
        conn.execute(_INSERT)
    with pytest.raises(DatabaseError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT outcome FROM audit_event")).scalar() == "success"


def test_append_after_rejected_mutation_and_rollback_retry(engine):
    install_audit_db_guards(engine)
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            conn.execute(_INSERT)
            raise RuntimeError("force rollback")
    assert _count(engine) == 0
    with engine.begin() as conn:
        conn.execute(_INSERT)
    assert _count(engine) == 1


def test_repeated_install_is_safe_and_registry_is_explicit(engine):
    install_audit_db_guards(engine)
    install_audit_db_guards(engine)
    assert missing_audit_guards(engine) == []
    assert AUDIT_TABLE in append_only_guards.APPEND_ONLY_TABLES
    with engine.begin() as conn:
        conn.execute(_INSERT)
    assert _count(engine) == 1


def test_install_before_table_exists_is_noop():
    eng = create_engine("sqlite://")
    install_audit_db_guards(eng)
    AuditEvent.__table__.create(eng)
    assert len(missing_audit_guards(eng)) == 2
    remove_audit_db_guards(eng)
    assert AUDIT_TABLE not in append_only_guards.APPEND_ONLY_TABLES
