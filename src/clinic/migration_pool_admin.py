"""Migrate the pool activation flag, validity window and grant action list.

Adds nullable ``active``, ``valid_from`` and ``valid_to`` to both pool tables. NULL ``active``
means active, so existing rows keep working untouched. ``downgrade`` drops the three columns again.
Both directions are idempotent. A fresh database already has the columns from ``create_all``.
PostgreSQL also needs room for all grant actions. Downgrade preserves that capacity.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

TABLES = ("clinic_author_pool", "clinic_staff_pool")
COLUMNS = ("active", "valid_from", "valid_to")


def _types(dialect: str) -> dict[str, str]:
    stamp = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    return {"active": "BOOLEAN", "valid_from": stamp, "valid_to": stamp}


def _run(bind, statements: list[str]) -> None:
    if not statements:
        return
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    else:
        for statement in statements:
            bind.execute(text(statement))


def upgrade(bind) -> list[str]:
    """Add the missing columns; return the statements that ran."""
    inspector = inspect(bind)
    types = _types(bind.dialect.name)
    statements = []
    for table in TABLES:
        if not inspector.has_table(table):
            continue
        have = {column["name"] for column in inspector.get_columns(table)}
        statements += [
            f"ALTER TABLE {table} ADD COLUMN {name} {types[name]}"
            for name in COLUMNS if name not in have
        ]
    if bind.dialect.name == "postgresql" and inspector.has_table("clinic_visibility_grant"):
        columns = inspector.get_columns("clinic_visibility_grant")
        if any(col["name"] == "actions" and getattr(col["type"], "length", None) for col in columns):
            statements.append("ALTER TABLE clinic_visibility_grant ALTER COLUMN actions TYPE TEXT")
    _run(bind, statements)
    return statements


def downgrade(bind) -> list[str]:
    """Drop the columns again; return the statements that ran."""
    inspector = inspect(bind)
    statements = []
    for table in TABLES:
        if not inspector.has_table(table):
            continue
        have = {column["name"] for column in inspector.get_columns(table)}
        statements += [
            f"ALTER TABLE {table} DROP COLUMN {name}" for name in COLUMNS if name in have
        ]
    _run(bind, statements)
    return statements
