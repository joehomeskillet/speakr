"""Sort and group projected work-list rows and normalise saved layouts.

Callers supply authorised rows and columns. This module does not project
metadata, calculate deadlines, apply policy, or persist user preferences.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

PRIORITY_RANK = {128: 0, 1: 1, 0: 2}
MAX_COLUMNS = 30


class LayoutError(ValueError):
    """A work-list request was rejected with a stable error code."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Group:
    """A group and all its rows, in input order, with optional subgroups."""

    key_path: tuple
    rows: tuple[Mapping, ...]
    children: tuple[Group, ...]


def sort_rows(rows: Sequence[Mapping], keys: Sequence[tuple[str, str]]) -> list[Mapping]:
    """Sort stably, inferring valid keys from the union of the row mappings.

    With no rows there is no schema to check, so only the shape and directions
    of the sort keys are validated. Missing fields behave like ``None``.
    """
    result = list(_rows(rows))
    order = _sort_keys(keys)
    known = {key for row in result for key in row} if order else set()
    if result and any(key not in known for key, _ in order):
        raise LayoutError("unknown_key")

    # Stable passes from least to most significant preserve all earlier ties.
    for key, direction in reversed(order):
        present = []
        missing = []
        for row in result:
            value = _row_value(row, key, for_sort=True)
            if value is None:
                missing.append(row)
            else:
                present.append((_sort_value(key, value), row))
        try:
            present.sort(key=lambda item: item[0], reverse=direction == "desc")
        except TypeError as exc:
            raise LayoutError("bad_value") from exc
        result = [row for _, row in present] + missing
    return result


def group_rows(rows: Sequence[Mapping], group_by: Sequence[str]) -> list[Group]:
    """Group by raw field values, keeping first appearance at every level."""
    fields = _group_keys(group_by)
    members = tuple(_rows(rows))
    if not fields:
        return [Group((), members, ())]
    return list(_groups(members, fields, ()))


def validate_layout(spec: Mapping, allowed_columns: Sequence[str]) -> dict:
    """Return a fresh, validated version-1 layout with clamped widths."""
    return _validate_layout(spec, _allowed_columns(allowed_columns))


def merge_layout(default: Mapping, saved: Mapping | None, allowed_columns: Sequence[str]) -> dict:
    """Reconcile saved preferences with current columns and append new defaults."""
    allowed = _allowed_columns(allowed_columns)
    base = _validate_layout(default, allowed)
    if saved is None:
        return base

    # Old column names are valid in storage, but not in the current projection.
    result = _validate_layout(saved, None)
    columns = [column for column in result["columns"] if column["key"] in allowed]
    saved_keys = {column["key"] for column in columns}
    missing_defaults = [dict(column, visible=True) for column in base["columns"] if column["key"] not in saved_keys]
    columns.extend(missing_defaults[:MAX_COLUMNS - len(columns)])
    result["columns"] = columns
    result["sort"] = [entry for entry in result["sort"] if entry[0] in allowed]
    result["group_by"] = [key for key in result["group_by"] if key in allowed]
    return _validate_layout(result, allowed)


def layout_to_json(spec: Mapping) -> str:
    """Serialise a layout in canonical key order, independently of its schema."""
    return json.dumps(_validate_layout(spec, None), sort_keys=True, ensure_ascii=False)


def layout_from_json(text: str, allowed_columns: Sequence[str]) -> dict:
    """Decode JSON and validate it against the current column schema."""
    if not isinstance(text, str):
        raise LayoutError("bad_layout")
    try:
        spec = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise LayoutError("bad_layout") from exc
    return validate_layout(spec, allowed_columns)


def _sequence(value: object, code: str) -> Sequence:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, memoryview)):
        raise LayoutError(code)
    return value


def _rows(rows: Sequence[Mapping]) -> Sequence[Mapping]:
    result = _sequence(rows, "bad_rows")
    if any(not isinstance(row, Mapping) for row in result):
        raise LayoutError("bad_rows")
    return result


def _name(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
    ):
        raise LayoutError(code)
    return value


def _sort_keys(keys: Sequence) -> list[tuple[str, str]]:
    result = []
    for item in _sequence(keys, "bad_sort"):
        pair = _sequence(item, "bad_sort")
        if len(pair) != 2:
            raise LayoutError("bad_sort")
        key, direction = pair
        if not isinstance(direction, str) or direction not in ("asc", "desc"):
            raise LayoutError("bad_direction")
        result.append((_name(key, "bad_key"), direction))
    return result


def _group_keys(group_by: Sequence[str]) -> list[str]:
    fields = _sequence(group_by, "bad_group")
    if len(fields) > 3:
        raise LayoutError("too_deep")
    return [_name(key, "bad_key") for key in fields]


def _row_value(row: Mapping, key: str, *, for_sort: bool = False) -> Any:
    value = row.get(key)
    if value is None:
        return None
    # Role DTOs group by stable identity and sort by the displayed staff names.
    if key in ("dictated_by", "lease_holder") and isinstance(value, Mapping):
        identity = _name(str(value.get("id", "")), "bad_value")
        value = (value.get("display_name") or identity) if for_sort else identity
    if key == "staff_pools" and isinstance(value, list):
        if any(not isinstance(pool, Mapping) for pool in value):
            raise LayoutError("bad_value")
        identities = [_name(str(pool.get("id", "")), "bad_value") for pool in value]
        if not for_sort:
            return tuple(sorted(identities))
        if not value:
            return ""
        value = ", ".join(sorted(pool.get("name") or identity for pool, identity in zip(value, identities)))
    if key == "priority" and (type(value) is not int or value not in PRIORITY_RANK):
        raise LayoutError("bad_priority")
    if isinstance(value, str):
        return _name(value, "bad_value")
    if isinstance(value, datetime) and value.utcoffset() is None:
        raise LayoutError("bad_value")
    if not isinstance(value, (int, float, date)) or (isinstance(value, float) and not math.isfinite(value)):
        raise LayoutError("bad_value")
    return value


def _sort_value(key: str, value: Any) -> Any:
    if key == "priority":
        return PRIORITY_RANK[value]
    if isinstance(value, str):
        decomposed = unicodedata.normalize("NFKD", value)
        base = "".join(char for char in decomposed if not unicodedata.category(char).startswith("M"))
        return base.casefold(), value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return value


def _groups(rows: tuple[Mapping, ...], fields: Sequence[str], path: tuple) -> tuple[Group, ...]:
    if not fields:
        return ()
    buckets: dict[Any, list[Mapping]] = {}
    key, *rest = fields
    for row in rows:
        buckets.setdefault(_row_value(row, key), []).append(row)
    result = []
    for value, bucket in buckets.items():
        members = tuple(bucket)
        child_path = (*path, value)
        result.append(Group(child_path, members, _groups(members, rest, child_path)))
    return tuple(result)


def _allowed_columns(allowed_columns: Sequence[str]) -> set[str]:
    return {_name(key, "bad_key") for key in _sequence(allowed_columns, "bad_columns")}


def _validate_layout(spec: object, allowed: set[str] | None) -> dict:
    if not isinstance(spec, Mapping) or set(spec) != {"version", "columns", "sort", "group_by"}:
        raise LayoutError("bad_layout")
    version = spec["version"]
    if type(version) is not int or version != 1:
        raise LayoutError("bad_version")

    columns = _sequence(spec["columns"], "bad_columns")
    if len(columns) > MAX_COLUMNS:
        raise LayoutError("too_many_columns")
    result = []
    seen = set()
    for column in columns:
        if not isinstance(column, Mapping) or set(column) != {"key", "width", "visible"}:
            raise LayoutError("bad_column")
        key = _name(column["key"], "bad_key")
        if key in seen:
            raise LayoutError("duplicate_column")
        seen.add(key)
        width, visible = column["width"], column["visible"]
        if type(width) is not int:
            raise LayoutError("bad_width")
        if type(visible) is not bool:
            raise LayoutError("bad_visible")
        result.append({"key": key, "width": min(800, max(40, width)), "visible": visible})

    order = _sort_keys(spec["sort"])
    fields = _group_keys(spec["group_by"])
    if allowed is not None and (seen | {key for key, _ in order} | set(fields)) - allowed:
        raise LayoutError("unknown_column")
    return {"version": version, "columns": result, "sort": [list(pair) for pair in order], "group_by": fields}
