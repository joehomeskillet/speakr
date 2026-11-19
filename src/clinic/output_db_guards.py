"""Database guards for output receipts, releases and release history.

Output receipts are already in the shared append-only registry. The ORM
listeners of the context revision refuse UPDATE and DELETE but do not stop a
raw statement, and the release rows, supersession marks and work-state
transfers have no database guard at all. This installer registers those four
tables and then calls the shared installer. Delivery rows stay out of the
registry: their state advances through ``kis`` and the output attempt machine,
and new receipts are only ever appended.
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
from src.clinic.models import ClinicContextRevision, ClinicDelivery, ClinicRelease
from src.clinic.output import ClinicOutputReceipt
from src.clinic.release import ClinicReleaseSupersession, ClinicWorkStateTransfer

# Tables this module adds to the shared registry (table -> trigger label).
OUTPUT_GUARDED_TABLES: dict[str, str] = {
    ClinicRelease.__tablename__: "clinic release",
    ClinicContextRevision.__tablename__: "clinic context revision",
    ClinicReleaseSupersession.__tablename__: "clinic release supersession",
    ClinicWorkStateTransfer.__tablename__: "clinic work state transfer",
}

# Tables that must stay writable because their status moves forward.
MUTABLE_TABLES: tuple[str, ...] = (ClinicDelivery.__tablename__,)


def install_output_db_guards(engine: Engine) -> None:
    """Reject UPDATE and DELETE on receipts, releases and their history.

    Idempotent. Tables that do not exist yet are skipped by the shared
    installer. w077 calls this after the tables exist.
    """
    receipt = ClinicOutputReceipt.__tablename__
    if receipt not in APPEND_ONLY_TABLES:
        raise RuntimeError("clinic output receipt is not in the append-only registry")
    frozen = [name for name in MUTABLE_TABLES if name in APPEND_ONLY_TABLES]
    if frozen:
        raise RuntimeError(f"mutable output tables must stay writable: {', '.join(frozen)}")
    # The shared installer only reads this registry and this package cannot
    # edit that module. Registration lasts for the process.
    for table, label in OUTPUT_GUARDED_TABLES.items():
        APPEND_ONLY_TABLES.setdefault(table, label)
    install_append_only_guards(engine)


def remove_output_db_guards(engine: Engine) -> None:
    """Drop the triggers of the tables this module registered (test clean-up)."""
    if engine.name not in ("sqlite", "postgresql"):
        return
    with engine.begin() as conn:
        for table in OUTPUT_GUARDED_TABLES:
            if engine.name == "sqlite":
                for name in trigger_names(table):
                    conn.execute(text("DROP TRIGGER IF EXISTS " + name))
            else:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_tables WHERE schemaname = current_schema() "
                         "AND tablename = :t"),
                    {"t": table},
                ).first()
                if exists:
                    conn.execute(
                        text("DROP TRIGGER IF EXISTS " + pg_trigger_name(table) + " ON " + table)
                    )
