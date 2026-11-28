"""Additive claimed-author, completion-source and evidence-level columns.

Old inventory rows gain the columns and the unknown sentinels. The fill is a
constant: size, mtime and content hash are not read and are not Philips EOL.
A second ``upgrade`` is a no-op. ``downgrade`` drops the three columns.
"""

from __future__ import annotations

import re
from typing import Mapping

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

TABLE = "clinic_agent_inventory"
UNKNOWN = "unknown"
FILE_STABILITY = "file_stability"
PHILIPS_EOL = "philips_eol"
UNVERIFIED = "unverified"
EVIDENCE = frozenset({"unverified", "fixture", "observed", "validated"})
SOURCES = frozenset({UNKNOWN, FILE_STABILITY, PHILIPS_EOL})
_STABILITY = frozenset({
    "file_stability", "mtime", "mtime_utc", "stable", "stable_snapshot", "sha256", "size", "file",
})
_AUTHOR = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_ADD = {
    "claimed_author_code": "ALTER TABLE clinic_agent_inventory ADD COLUMN claimed_author_code VARCHAR(128)",
    "completion_source": "ALTER TABLE clinic_agent_inventory ADD COLUMN completion_source VARCHAR(32)",
    "evidence_level": "ALTER TABLE clinic_agent_inventory ADD COLUMN evidence_level VARCHAR(32)",
}
_DROP = {
    "claimed_author_code": "ALTER TABLE clinic_agent_inventory DROP COLUMN claimed_author_code",
    "completion_source": "ALTER TABLE clinic_agent_inventory DROP COLUMN completion_source",
    "evidence_level": "ALTER TABLE clinic_agent_inventory DROP COLUMN evidence_level",
}
# Constant sentinels. These statements do not read size, mtime_utc or sha256.
_FILL = {
    "completion_source": "UPDATE clinic_agent_inventory SET completion_source = 'unknown' WHERE completion_source IS NULL",
    "evidence_level": "UPDATE clinic_agent_inventory SET evidence_level = 'unverified' WHERE evidence_level IS NULL",
}
_NULL_COUNT = {
    "completion_source": "SELECT COUNT(*) FROM clinic_agent_inventory WHERE completion_source IS NULL",
    "evidence_level": "SELECT COUNT(*) FROM clinic_agent_inventory WHERE evidence_level IS NULL",
}
Hint = dict[str, str | None]
Bind = Engine | Connection


def _run(bind: Bind, statements: list[str]) -> None:
    if not statements:
        return
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        return
    for statement in statements:
        bind.execute(text(statement))


def _column_names(bind: Bind) -> set[str] | None:
    inspector = inspect(bind)
    inspector.clear_cache()
    if not inspector.has_table(TABLE):
        return None
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _null_count(bind: Bind, column: str) -> int:
    statement = text(_NULL_COUNT[column])
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return int(connection.execute(statement).scalar() or 0)
    return int(bind.execute(statement).scalar() or 0)


def upgrade(bind: Bind) -> list[str]:
    """Add missing hint columns and set unknown sentinels on old rows."""
    names = _column_names(bind)
    if names is None:
        return []
    statements = [_ADD[name] for name in _ADD if name not in names]
    _run(bind, statements)
    fills = [_FILL[column] for column in _FILL if _null_count(bind, column)]
    _run(bind, fills)
    return statements + fills


def upgrade_firmware_claims(bind: Bind) -> list[str]:
    """Add nullable firmware claim columns without backfilling old inventory."""
    names = _column_names(bind)
    if names is None:
        return []
    columns = {
        "device_type": "ALTER TABLE clinic_agent_inventory ADD COLUMN device_type VARCHAR(56)",
        "firmware_version": "ALTER TABLE clinic_agent_inventory ADD COLUMN firmware_version VARCHAR(64)",
    }
    statements = [statement for name, statement in columns.items() if name not in names]
    _run(bind, statements)
    return statements


def downgrade(bind: Bind) -> list[str]:
    """Drop the hint columns. File identity columns stay."""
    names = _column_names(bind)
    if names is None:
        return []
    statements = [_DROP[name] for name in _DROP if name in names]
    _run(bind, statements)
    return statements


def _key(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _explicit_philips(raw: Mapping[str, object]) -> bool:
    """True only for an explicit Philips EOL source that is not file stability."""
    if raw.get("file_stable") is True:
        return False
    source = _key(raw.get("completion_source"))
    basis = _key(raw.get("basis") if raw.get("basis") is not None else raw.get("derived_from"))
    if source in _STABILITY or basis in _STABILITY:
        return False
    return source == PHILIPS_EOL


def normalize_hints(raw: Mapping[str, object] | None) -> Hint:
    """Map an optional hint payload to stored provenance.

    Missing, blank and unrecognised marks stay unknown. File stability is stored
    as ``file_stability`` and is never Philips EOL, even if the payload also says
    the dictation is completed.
    """
    payload: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
    author = payload.get("claimed_author_code", payload.get("claimed_author"))
    if not isinstance(author, str) or not _AUTHOR.match(author):
        author = None
    evidence = _key(payload.get("evidence_level"))
    if evidence not in EVIDENCE:
        evidence = UNVERIFIED
    if _explicit_philips(payload):
        completion = PHILIPS_EOL
    elif _key(payload.get("completion_source")) in _STABILITY or payload.get("file_stable") is True:
        completion = FILE_STABILITY
    else:
        completion = UNKNOWN
    return {
        "claimed_author_code": author,
        "completion_source": completion,
        "evidence_level": evidence,
    }


def replay_hints(stored: Mapping[str, object] | None, incoming: Mapping[str, object] | None) -> Hint:
    """Provenance after the same snapshot is read again.

    File fields on ``stored`` are not a completion mark. A replay cannot turn
    file stability into Philips EOL. Missing or unknown incoming marks stay
    unknown and do not inherit an end-of-letter flag from the previous row.
    """
    hints = normalize_hints(incoming)
    previous = stored.get("completion_source") if isinstance(stored, Mapping) else None
    if previous == PHILIPS_EOL and hints["completion_source"] != PHILIPS_EOL:
        return hints
    return hints


def provenance(claimed_author_code: object, completion_source: object, evidence_level: object) -> Hint:
    """Read stored columns. NULL and unrecognised values stay unknown."""
    author = claimed_author_code if isinstance(claimed_author_code, str) and _AUTHOR.match(claimed_author_code) else None
    source = completion_source if completion_source in SOURCES else UNKNOWN
    evidence = evidence_level if evidence_level in EVIDENCE else UNVERIFIED
    return {
        "claimed_author_code": author,
        "completion_source": source,
        "evidence_level": evidence,
    }
