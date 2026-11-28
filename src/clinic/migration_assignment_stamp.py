"""Add nullable assignment timestamps without inventing historical times."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

TABLE = "clinic_workflow_dictation"


def upgrade(bind) -> list[str]:
    """Add the missing column; preserve all assignments and existing timestamps.

    ``routing_target_json`` is added in the same call when it is absent, so an
    ORM load of the workflow row matches the table. That statement is not
    returned: this stamp reports only ``assigned_at``.
    """
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return []
    names = {column["name"] for column in inspector.get_columns(TABLE)}
    reported: list[str] = []
    pending: list[str] = []
    if "assigned_at" not in names:
        stamp = "TIMESTAMP WITH TIME ZONE" if bind.dialect.name == "postgresql" else "DATETIME"
        reported.append(f"ALTER TABLE {TABLE} ADD COLUMN assigned_at {stamp}")
        pending.extend(reported)
    if "routing_target_json" not in names:
        pending.append("ALTER TABLE clinic_workflow_dictation ADD COLUMN routing_target_json TEXT")
    if not pending:
        return []
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            for statement in pending:
                connection.execute(text(statement))
    else:
        for statement in pending:
            bind.execute(text(statement))
    return reported
