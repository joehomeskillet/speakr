"""Database guards for the existing ``audit_event`` rows (R-031).

The w064 proof showed that the application login could UPDATE and DELETE
committed ``audit_event`` rows with raw SQL, because the ORM listeners in
``src/models/audit.py`` do not see raw statements. This module closes that gap
through the same explicit registry the other clinic history tables use: it
registers ``audit_event`` and calls the shared installer. On PostgreSQL it adds
a statement trigger against TRUNCATE, which the row triggers do not cover.

Appends are untouched, so ``audit.record_in_tx`` keeps working. Removing aged
rows stays a task of the separate retention process (w119), which runs under
its own authority; owners and superusers keep DDL authority and are an
operational concern, not covered by a trigger.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.clinic.append_only_guards import (
    APPEND_ONLY_TABLES,
    install_append_only_guards,
    pg_trigger_name,
    trigger_names,
)
from src.models.audit import AuditEvent

AUDIT_TABLE = AuditEvent.__tablename__
_LABEL = "audit event"
_PG_FUNCTION = "clinic_append_only"


def _truncate_trigger(table: str = AUDIT_TABLE) -> str:
    return table + "_no_truncate"


def install_audit_db_guards(engine: Engine) -> None:
    """Reject UPDATE, DELETE (and on PostgreSQL TRUNCATE) on ``audit_event``.

    Idempotent. A missing table is skipped, so the call is safe before
    ``create_all``. Other engines keep the table without the guard.
    """
    if engine.name not in ("sqlite", "postgresql"):
        return
    APPEND_ONLY_TABLES.setdefault(AUDIT_TABLE, _LABEL)
    install_append_only_guards(engine)
    if engine.name != "postgresql":
        return
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_tables WHERE schemaname = current_schema() AND tablename = :t"),
            {"t": AUDIT_TABLE},
        ).first()
        if exists is None:
            return
        name = _truncate_trigger()
        conn.execute(text("DROP TRIGGER IF EXISTS " + name + " ON " + AUDIT_TABLE))
        conn.execute(
            text(
                "CREATE TRIGGER " + name + " BEFORE TRUNCATE ON " + AUDIT_TABLE
                + " FOR EACH STATEMENT EXECUTE PROCEDURE " + _PG_FUNCTION
                + "('" + _LABEL + "')"
            )
        )


def missing_audit_guards(engine: Engine) -> list[str]:
    """Names of the expected triggers that are absent. Empty when guarded."""
    if engine.name not in ("sqlite", "postgresql"):
        return []
    expected = (
        list(trigger_names(AUDIT_TABLE))
        if engine.name == "sqlite"
        else [pg_trigger_name(AUDIT_TABLE), _truncate_trigger()]
    )
    query = (
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = :t"
        if engine.name == "sqlite"
        else "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(:t) AND NOT tgisinternal"
    )
    with engine.connect() as conn:
        found = {row[0] for row in conn.execute(text(query), {"t": AUDIT_TABLE})}
    return [name for name in expected if name not in found]


def remove_audit_db_guards(engine: Engine) -> None:
    """Drop the guards. Tests remove them before shared cleanup deletes rows."""
    if engine.name not in ("sqlite", "postgresql"):
        return
    with engine.begin() as conn:
        for name in trigger_names(AUDIT_TABLE) if engine.name == "sqlite" else ():
            conn.execute(text("DROP TRIGGER IF EXISTS " + name))
        if engine.name == "postgresql":
            for name in (pg_trigger_name(AUDIT_TABLE), _truncate_trigger()):
                conn.execute(text("DROP TRIGGER IF EXISTS " + name + " ON " + AUDIT_TABLE))
    APPEND_ONLY_TABLES.pop(AUDIT_TABLE, None)
