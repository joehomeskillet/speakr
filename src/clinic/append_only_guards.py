"""Database triggers for the clinic tables that are append-only.

These models already refuse UPDATE and DELETE through ORM listeners. A listener
does not stop a raw SQL statement, a bulk ``Query.delete()`` or another client,
so the same rule is repeated in the database. ``install_append_only_guards`` is
idempotent and is called from ``init_db`` next to ``install_clinic_guards``.
"""
from __future__ import annotations

from sqlalchemy import text

# Table -> human reason used in the trigger message.
APPEND_ONLY_TABLES: dict[str, str] = {
    "clinic_output_receipt": "clinic output receipt",
    "clinic_marker_event": "clinic marker event",
    "clinic_asr_profile_version": "clinic ASR profile version",
    "clinic_asr_training_run": "clinic ASR training run",
    "clinic_voice_consent": "clinic voice consent",
    "clinic_voice_consent_audit": "clinic voice consent audit",
}

_PG_FUNCTION = "clinic_append_only"


def trigger_names(table: str) -> tuple[str, str]:
    """SQLite trigger names (update, delete) for ``table``."""
    return (table + "_no_update", table + "_no_delete")


def pg_trigger_name(table: str) -> str:
    return table + "_append_only"


def install_append_only_guards(engine) -> None:
    """Reject UPDATE and DELETE on every append-only clinic table.

    Tables that do not exist yet are skipped, so the call is safe before
    ``create_all``. Other engines keep the tables without the guard.
    """
    if engine.name not in ("sqlite", "postgresql"):
        return
    with engine.begin() as conn:
        present = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    if engine.name == "sqlite"
                    else "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
                )
            )
        }
        if engine.name == "postgresql":
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION " + _PG_FUNCTION + "() "
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN "
                    "RAISE EXCEPTION '% is append-only', TG_ARGV[0] "
                    "USING ERRCODE = 'check_violation'; "
                    "END; $$"
                )
            )
        for table, label in APPEND_ONLY_TABLES.items():
            if table not in present:
                continue
            if engine.name == "sqlite":
                for name, event in zip(trigger_names(table), ("UPDATE", "DELETE")):
                    conn.execute(text("DROP TRIGGER IF EXISTS " + name))
                    conn.execute(
                        text(
                            "CREATE TRIGGER " + name + " BEFORE " + event + " ON " + table
                            + " FOR EACH ROW BEGIN SELECT RAISE(ABORT, '" + label
                            + " is append-only'); END"
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
