"""Database triggers that make clinic audio edit rows immutable.

ORM listeners in ``audio_revisions`` already refuse UPDATE and DELETE. They do
not see raw SQL or a bulk ``Query.update()`` / ``Query.delete()``, so the same
rule is installed in the database.

``install_append_only_guards`` cannot take this table map: it is closed over
its own tables. ``ClinicAudioSessionState`` stays mutable because undo and
redo only move the current revision and the two stack references.
"""
from __future__ import annotations

from sqlalchemy import text

from src.clinic.audio_revisions import (
    ClinicAudioActionLog,
    ClinicAudioEditOperation,
    ClinicAudioRevision,
    ClinicAudioRevisionSnapshot,
    ClinicAudioSegment,
)

# Table -> reason embedded in the trigger error.
AUDIO_IMMUTABLE_TABLES: dict[str, str] = {
    ClinicAudioEditOperation.__tablename__: "clinic audio edit operation",
    ClinicAudioRevisionSnapshot.__tablename__: "clinic audio revision snapshot",
    ClinicAudioSegment.__tablename__: "clinic audio segment",
    ClinicAudioActionLog.__tablename__: "clinic audio action log",
    ClinicAudioRevision.__tablename__: "clinic audio revision",
}

_PG_FUNCTION = "clinic_audio_immutable"


def trigger_names(table: str) -> tuple[str, str]:
    """SQLite trigger names (update, delete) for ``table``."""
    return (table + "_no_update", table + "_no_delete")


def pg_trigger_name(table: str) -> str:
    return table + "_immutable"


def _present_tables(conn, dialect: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            if dialect == "sqlite"
            else "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
    )
    return {row[0] for row in rows}


def install_audio_db_guards(engine) -> None:
    """Reject UPDATE and DELETE on every immutable clinic audio table.

    Tables that do not exist yet are skipped, so the call is safe before
    ``create_all``. Other engines keep the tables without the guard.
    Idempotent: each install drops and recreates the same trigger names.
    """
    if engine.name not in ("sqlite", "postgresql"):
        return
    with engine.begin() as conn:
        present = _present_tables(conn, engine.name)
        if engine.name == "postgresql":
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION " + _PG_FUNCTION + "() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN "
                    "RAISE EXCEPTION '% is immutable', TG_ARGV[0] "
                    "USING ERRCODE = 'check_violation'; "
                    "END; $$"
                )
            )
        for table, label in AUDIO_IMMUTABLE_TABLES.items():
            if table not in present:
                continue
            if engine.name == "sqlite":
                for name, event in zip(trigger_names(table), ("UPDATE", "DELETE")):
                    conn.execute(text("DROP TRIGGER IF EXISTS " + name))
                    conn.execute(
                        text(
                            "CREATE TRIGGER " + name + " BEFORE " + event + " ON " + table
                            + " FOR EACH ROW BEGIN SELECT RAISE(ABORT, '" + label
                            + " is immutable'); END"
                        )
                    )
            else:
                name = pg_trigger_name(table)
                conn.execute(text("DROP TRIGGER IF EXISTS " + name + " ON " + table))
                conn.execute(
                    text(
                        "CREATE TRIGGER " + name + " BEFORE UPDATE OR DELETE ON " + table
                        + " FOR EACH ROW EXECUTE PROCEDURE " + _PG_FUNCTION
                        + "('" + label + "')"
                    )
                )


def remove_audio_db_guards(engine) -> None:
    """Drop the guards installed by ``install_audio_db_guards``.

    Tests remove them before the shared clinic cleanup deletes rows. Missing
    tables are skipped.
    """
    if engine.name not in ("sqlite", "postgresql"):
        return
    with engine.begin() as conn:
        present = _present_tables(conn, engine.name)
        for table in AUDIO_IMMUTABLE_TABLES:
            if table not in present:
                continue
            if engine.name == "sqlite":
                for name in trigger_names(table):
                    conn.execute(text("DROP TRIGGER IF EXISTS " + name))
            else:
                conn.execute(
                    text("DROP TRIGGER IF EXISTS " + pg_trigger_name(table) + " ON " + table)
                )
