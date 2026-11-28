"""Additive upgrade of import-source option columns (PAR-IMP-006).

Adds backup name, retention, cleanup, backup root and the backup toggle. Existing rows keep an
EOL flag that was already set and gain cleanup off. Missing routing defaults
and an empty file pattern are filled once. Both directions are idempotent.
A fresh database already has the columns from ``create_all``.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

TABLE = "clinic_import_source_config"
COLUMNS = ("backup_name", "backup_retention_days", "cleanup_enabled", "backup_root", "backup_enabled")


def _literals(dialect: str) -> tuple[str, str]:
    if dialect == "postgresql":
        return "TRUE", "FALSE"
    return "1", "0"


def _column_types(dialect: str) -> dict[str, str]:
    _on, off = _literals(dialect)
    return {
        "backup_name": "VARCHAR(255) NOT NULL DEFAULT ''",
        "backup_retention_days": "INTEGER",
        "cleanup_enabled": "BOOLEAN NOT NULL DEFAULT " + off,
        "backup_root": "VARCHAR(1024)",
        "backup_enabled": "BOOLEAN",  # NULL preserves the legacy root-based setting.
    }


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


def _rows_match(bind, where: str) -> bool:
    # ``where`` is one of the fixed clauses in ``_backfill``, never caller input.
    statement = text("SELECT 1 FROM " + TABLE + " WHERE " + where + " LIMIT 1")
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return connection.execute(statement).first() is not None
    return bind.execute(statement).first() is not None


def _backfill(bind) -> list[str]:
    """Fill only missing option values. A set EOL flag or cleanup choice stays."""
    on, off = _literals(bind.dialect.name)
    have = {column["name"] for column in inspect(bind).get_columns(TABLE)}
    jobs = []
    if "eol_filter_enabled" in have:
        jobs.append((
            "eol_filter_enabled IS NULL",
            "UPDATE " + TABLE + " SET eol_filter_enabled = " + on + " WHERE eol_filter_enabled IS NULL",
        ))
    if "cleanup_enabled" in have:
        jobs.append((
            "cleanup_enabled IS NULL",
            "UPDATE " + TABLE + " SET cleanup_enabled = " + off + " WHERE cleanup_enabled IS NULL",
        ))
    if "file_pattern" in have:
        jobs.append((
            "file_pattern IS NULL OR file_pattern = ''",
            "UPDATE " + TABLE + " SET file_pattern = '*' WHERE file_pattern IS NULL OR file_pattern = ''",
        ))
    if "priority" in have:
        jobs.append((
            "priority IS NULL",
            "UPDATE " + TABLE + " SET priority = 1 WHERE priority IS NULL",
        ))
    if "language_id" in have:
        jobs.append((
            "language_id IS NULL",
            "UPDATE " + TABLE + " SET language_id = 1031 WHERE language_id IS NULL",
        ))
    if "backup_name" in have:
        jobs.append((
            "backup_name IS NULL",
            "UPDATE " + TABLE + " SET backup_name = '' WHERE backup_name IS NULL",
        ))
    statements = [sql for where, sql in jobs if _rows_match(bind, where)]
    _run(bind, statements)
    return statements


def upgrade(bind) -> list[str]:
    """Add missing option columns and fill defaults. Return the statements that ran."""
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return []
    have = {column["name"] for column in inspector.get_columns(TABLE)}
    types = _column_types(bind.dialect.name)
    statements = [
        "ALTER TABLE " + TABLE + " ADD COLUMN " + name + " " + types[name]
        for name in COLUMNS if name not in have
    ]
    _run(bind, statements)
    inspector.clear_cache()
    return statements + _backfill(bind)


def downgrade(bind) -> list[str]:
    """Drop the option columns added by ``upgrade``. Return the statements that ran."""
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return []
    have = {column["name"] for column in inspector.get_columns(TABLE)}
    statements = [
        "ALTER TABLE " + TABLE + " DROP COLUMN " + name for name in COLUMNS if name in have
    ]
    _run(bind, statements)
    return statements
