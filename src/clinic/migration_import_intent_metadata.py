"""Versioned metadata carried on a clinic import intent (PAR-IMP-003).

A row with no ``metadata_version`` is a pre-contract intent. Its interpretation
is keep for title, priority, recipient and pool, and keep for ASR. The existing
author, pool and language columns are not rewritten.

Version 1 stores an explicit document: each of those fields is ``keep`` or
``set``, and ASR is the tri-state ``keep`` / ``enable`` / ``disable``. A set
recipient must be in the caller-supplied approved set. ``upgrade`` adds the
two nullable columns to a staging database and leaves old rows untouched.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Collection, Mapping

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

SCHEMA_VERSION = 1
TABLE = "clinic_import_intent"
COLUMNS = ("metadata_version", "metadata_json")
_ADD_SQL = {
    "metadata_version": "ALTER TABLE clinic_import_intent ADD COLUMN metadata_version INTEGER",
    "metadata_json": "ALTER TABLE clinic_import_intent ADD COLUMN metadata_json TEXT",
}
_DROP_SQL = {
    "metadata_version": "ALTER TABLE clinic_import_intent DROP COLUMN metadata_version",
    "metadata_json": "ALTER TABLE clinic_import_intent DROP COLUMN metadata_json",
}
_INPUT_KEYS = frozenset({"title", "priority", "recipient_user_id", "pool_id", "asr"})
_ASR = frozenset({"keep", "enable", "disable"})
_PRIORITIES = frozenset({0, 1, 128})
_POOL = re.compile(r"^[A-Za-z0-9._:@-]{1,36}$")


class IntentMetadataError(ValueError):
    """The metadata document was rejected. ``code`` is stable."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def legacy_metadata() -> dict:
    """Interpretation of an intent stored before this contract existed."""
    return {
        "schema_version": 0,
        "title": {"op": "keep"},
        "priority": {"op": "keep"},
        "recipient_user_id": {"op": "keep"},
        "pool_id": {"op": "keep"},
        "asr": "keep",
    }


def encode_metadata(overrides: Mapping, *, approved_recipients: Collection[int]) -> tuple[int, str]:
    """Validate an explicit document and return ``(schema_version, json)``."""
    document = _compile(overrides, _approved(approved_recipients), complete=False)
    return SCHEMA_VERSION, json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def interpret(row) -> dict:
    """Read one stored intent. A missing version keeps the legacy meaning."""
    version = _cell(row, "metadata_version")
    raw = _cell(row, "metadata_json")
    if version is None:
        if raw not in (None, ""):
            raise IntentMetadataError("invalid_metadata")
        return legacy_metadata()
    if isinstance(version, bool) or type(version) is not int or version != SCHEMA_VERSION:
        raise IntentMetadataError("unsupported_version")
    if not isinstance(raw, str):
        raise IntentMetadataError("invalid_metadata")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntentMetadataError("invalid_metadata") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise IntentMetadataError("invalid_metadata")
    if set(payload) != {"schema_version", *_INPUT_KEYS}:
        raise IntentMetadataError("invalid_metadata")
    body = {key: payload[key] for key in _INPUT_KEYS}
    return _compile(body, None, complete=True)


def upgrade(bind) -> list[str]:
    """Add the metadata columns when they are missing. Idempotent."""
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return []
    have = {column["name"] for column in inspector.get_columns(TABLE)}
    statements = [_ADD_SQL[name] for name in COLUMNS if name not in have]
    _run(bind, statements)
    return statements


def downgrade(bind) -> list[str]:
    """Drop the metadata columns again. Idempotent."""
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return []
    have = {column["name"] for column in inspector.get_columns(TABLE)}
    statements = [_DROP_SQL[name] for name in COLUMNS if name in have]
    _run(bind, statements)
    return statements


def _cell(row, name: str):
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _approved(raw: Collection[int]) -> set[int]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Collection):
        raise IntentMetadataError("invalid_recipients")
    approved: set[int] = set()
    for item in raw:
        if isinstance(item, bool) or type(item) is not int or item <= 0:
            raise IntentMetadataError("invalid_recipients")
        approved.add(item)
    return approved


def _compile(overrides: Mapping, approved: set[int] | None, *, complete: bool) -> dict:
    if not isinstance(overrides, Mapping):
        raise IntentMetadataError("invalid_metadata")
    present = set(overrides)
    if complete:
        if present != _INPUT_KEYS:
            raise IntentMetadataError("invalid_metadata")
    elif present - _INPUT_KEYS:
        raise IntentMetadataError("invalid_metadata")
    return {
        "schema_version": SCHEMA_VERSION,
        "title": _override("title", overrides.get("title"), approved),
        "priority": _override("priority", overrides.get("priority"), approved),
        "recipient_user_id": _override("recipient_user_id", overrides.get("recipient_user_id"), approved),
        "pool_id": _override("pool_id", overrides.get("pool_id"), approved),
        "asr": _asr(overrides["asr"] if complete else overrides.get("asr", "keep")),
    }


def _override(field: str, raw, approved: set[int] | None) -> dict:
    if raw is None:
        return {"op": "keep"}
    if not isinstance(raw, Mapping):
        raise IntentMetadataError("invalid_metadata")
    op = raw.get("op")
    if op == "keep":
        if set(raw) != {"op"}:
            raise IntentMetadataError("invalid_metadata")
        return {"op": "keep"}
    if op != "set" or set(raw) != {"op", "value"}:
        raise IntentMetadataError("invalid_metadata")
    return {"op": "set", "value": _value(field, raw["value"], approved)}


def _value(field: str, value, approved: set[int] | None):
    if field == "title":
        return _title(value)
    if field == "priority":
        return _priority(value)
    if field == "pool_id":
        return _pool(value)
    if isinstance(value, bool) or type(value) is not int or value <= 0:
        raise IntentMetadataError("invalid_recipient")
    if approved is not None and value not in approved:
        raise IntentMetadataError("unapproved_recipient")
    return value


def _title(value) -> str:
    if not isinstance(value, str):
        raise IntentMetadataError("invalid_title")
    text = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(text) <= 200 or any(unicodedata.category(ch).startswith("C") for ch in text):
        raise IntentMetadataError("invalid_title")
    return text


def _priority(value) -> int:
    if isinstance(value, bool) or type(value) is not int or value not in _PRIORITIES:
        raise IntentMetadataError("invalid_priority")
    return value


def _pool(value) -> str:
    if not isinstance(value, str) or _POOL.fullmatch(value) is None:
        raise IntentMetadataError("invalid_pool")
    return value


def _asr(value) -> str:
    if value not in _ASR:
        raise IntentMetadataError("invalid_asr")
    return value


def _run(bind, statements: list[str]) -> None:
    if not statements:
        return
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        return
    for statement in statements:
        bind.execute(text(statement))
