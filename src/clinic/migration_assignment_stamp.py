"""Add nullable assignment timestamps without inventing historical times."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

TABLE = "clinic_workflow_dictation"


def upgrade(bind) -> list[str]:
    """Add the missing column; preserve all assignments and existing timestamps."""
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return []
    if "assigned_at" in {column["name"] for column in inspector.get_columns(TABLE)}:
        return []
    stamp = "TIMESTAMP WITH TIME ZONE" if bind.dialect.name == "postgresql" else "DATETIME"
    statement = f"ALTER TABLE {TABLE} ADD COLUMN assigned_at {stamp}"
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            connection.execute(text(statement))
    else:
        bind.execute(text(statement))
    return [statement]
