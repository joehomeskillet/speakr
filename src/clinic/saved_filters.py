"""Saved list filters for clinic dictations.

Scope:
- Validate and evaluate filter specifications from a whitelist of fields and operators.
- Enforce a maximum of 20 saved filters per user.
- Provide metadata search over whitelisted keys.
- Pure Python, no Flask, no database access, no datetime.now().

Non-scope:
- Persistence (handled by a later wiring package).
- HTTP routes or authentication.
- Regular expressions in user filters (explicitly forbidden).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any


MAX_FILTERS_PER_USER = 20

FIELDS = (
    "status",
    "priority",
    "author",
    "pool",
    "title",
    "created",
    "deadline",
    "assignee",
    "audio_seconds",
)

OPS = (
    "eq",
    "ne",
    "contains",
    "startswith",
    "lt",
    "le",
    "gt",
    "ge",
    "in",
    "is_null",
)

_META_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_COMPARISON_OPS = {"lt", "le", "gt", "ge"}
_STRING_OPS = {"contains", "startswith"}
_SCALAR_OPS = {"eq", "ne", "lt", "le", "gt", "ge", "contains", "startswith"}
_MAX_CONDITIONS = 20
_MAX_NAME_LENGTH = 60
_MAX_METADATA_QUERY_LENGTH = 200
_MAX_IN_VALUES = 50


class FilterError(ValueError):
    """A filter specification or operation was rejected."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _normalise_text(text: str) -> str:
    """Unicode NFC normalisation."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return unicodedata.normalize("NFC", text)


def _is_aware_utc(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def _require_aware_utc(now: datetime, label: str = "now") -> datetime:
    if not isinstance(now, datetime) or not _is_aware_utc(now):
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _validate_field(field: str) -> None:
    if field in FIELDS:
        return
    if field.startswith("meta."):
        key = field[5:]
        if _META_KEY_RE.fullmatch(key):
            return
    raise FilterError("unknown_field", f"unknown field: {field}")


def _validate_op(op: str) -> None:
    if op not in OPS:
        raise FilterError("unknown_op", f"unknown operator: {op}")
    if op in ("regex", "matches"):
        raise FilterError("unknown_op", f"operator not allowed: {op}")


def _validate_value_for_op(op: str, value: Any) -> None:
    if op == "is_null":
        if not isinstance(value, bool):
            raise FilterError("bad_value", "is_null requires a boolean value")
        return

    if op == "in":
        if not isinstance(value, (list, tuple)):
            raise FilterError("bad_value", "in requires a list or tuple")
        if len(value) > _MAX_IN_VALUES:
            raise FilterError("bad_value", f"in list may have at most {_MAX_IN_VALUES} items")
        for item in value:
            if isinstance(item, (list, tuple, dict, set)):
                raise FilterError("bad_value", "in list items must be scalars")
        return

    if op in _SCALAR_OPS:
        if isinstance(value, (list, tuple, dict, set)):
            raise FilterError("bad_value", f"{op} requires a scalar value")
        return

    raise FilterError("unknown_op", f"unknown operator: {op}")


def _validate_condition(cond: Mapping) -> dict:
    if not isinstance(cond, Mapping):
        raise FilterError("bad_shape", "condition must be a mapping")
    if set(cond.keys()) != {"field", "op", "value"}:
        raise FilterError("bad_shape", "condition must have exactly field, op, value")

    field = cond["field"]
    op = cond["op"]
    value = cond["value"]

    if not isinstance(field, str) or not isinstance(op, str):
        raise FilterError("bad_shape", "field and op must be strings")

    if isinstance(value, Mapping):
        raise FilterError("bad_shape", "nesting deeper than one level is not allowed")

    _validate_field(field)
    _validate_op(op)
    _validate_value_for_op(op, value)

    return {"field": field, "op": op, "value": value}


def _validate_spec_shape(spec: Mapping) -> None:
    if not isinstance(spec, Mapping):
        raise FilterError("bad_shape", "spec must be a mapping")

    allowed_top_keys = {"all", "any"}
    extra = set(spec.keys()) - allowed_top_keys
    if extra:
        raise FilterError("bad_shape", f"unexpected top-level keys: {sorted(extra)}")

    all_conds = spec.get("all", [])
    any_conds = spec.get("any", [])

    if not isinstance(all_conds, (list, tuple)):
        raise FilterError("bad_shape", "all must be a list")
    if not isinstance(any_conds, (list, tuple)):
        raise FilterError("bad_shape", "any must be a list")

    total = len(all_conds) + len(any_conds)
    if total > _MAX_CONDITIONS:
        raise FilterError("too_many_conditions", f"at most {_MAX_CONDITIONS} conditions allowed")

    for cond in all_conds:
        _validate_condition(cond)
    for cond in any_conds:
        _validate_condition(cond)


def validate_filter(spec: Mapping) -> dict:
    """Validate and normalise a filter specification.

    Args:
        spec: Mapping with optional "all" and "any" keys, each a list of
              conditions {"field": str, "op": str, "value": Any}.

    Returns:
        Normalised copy with validated conditions.

    Raises:
        FilterError: With code indicating the validation failure.
    """
    if spec is None:
        return {"all": [], "any": []}

    _validate_spec_shape(spec)

    normalised_all = [_validate_condition(c) for c in spec.get("all", [])]
    normalised_any = [_validate_condition(c) for c in spec.get("any", [])]

    return {"all": normalised_all, "any": normalised_any}


def _coerce_for_comparison(row_value: Any, filter_value: Any) -> tuple[Any, Any]:
    """Coerce values for comparison operations.

    Accepts ISO date strings (YYYY-MM-DD) and date objects interchangeably.
    """
    if isinstance(row_value, str) and isinstance(filter_value, date):
        try:
            return date.fromisoformat(row_value), filter_value
        except ValueError:
            pass
    if isinstance(row_value, date) and isinstance(filter_value, str):
        try:
            return row_value, date.fromisoformat(filter_value)
        except ValueError:
            pass
    if isinstance(row_value, datetime) and isinstance(filter_value, str):
        try:
            return row_value.date(), date.fromisoformat(filter_value)
        except ValueError:
            pass
    if isinstance(row_value, str) and isinstance(filter_value, datetime):
        try:
            return date.fromisoformat(row_value), filter_value.date()
        except ValueError:
            pass
    return row_value, filter_value


def _eval_condition(cond: dict, row: Mapping) -> bool:
    field = cond["field"]
    op = cond["op"]
    value = cond["value"]

    row_value = row.get(field)
    has_key = field in row

    if op == "is_null":
        return (row_value is None or not has_key) == value

    if row_value is None:
        if op in _COMPARISON_OPS:
            return False
        if op in ("eq", "ne"):
            return (row_value is None) == (value is None) if op == "eq" else (row_value is not None)
        return False

    if op in _COMPARISON_OPS:
        row_value, value = _coerce_for_comparison(row_value, value)
        try:
            if op == "lt":
                return row_value < value
            if op == "le":
                return row_value <= value
            if op == "gt":
                return row_value > value
            if op == "ge":
                return row_value >= value
        except TypeError:
            return False

    if op in ("eq", "ne"):
        equal = row_value == value
        return equal if op == "eq" else not equal

    if op in _STRING_OPS:
        row_str = _normalise_text(str(row_value)).casefold()
        val_str = _normalise_text(str(value)).casefold()
        if op == "contains":
            return val_str in row_str
        if op == "startswith":
            return row_str.startswith(val_str)

    if op == "in":
        try:
            return any(row_value == v for v in value)
        except TypeError:
            return False

    return False


def matches(spec: Mapping, row: Mapping) -> bool:
    """Test whether a row matches a validated filter specification.

    Args:
        spec: A validated filter spec (output of validate_filter or equivalent structure).
        row: A mapping of field names to values.

    Returns:
        True if the row matches, False otherwise.
    """
    if not spec or (not spec.get("all") and not spec.get("any")):
        return True

    all_ok = all(_eval_condition(c, row) for c in spec.get("all", []))
    if not all_ok:
        return False

    any_conds = spec.get("any", [])
    if not any_conds:
        return True

    return any(_eval_condition(c, row) for c in any_conds)


def apply_filter(spec: Mapping, rows: Iterable[Mapping]) -> list[Mapping]:
    """Return rows that match the filter specification."""
    validated = validate_filter(spec)
    return [row for row in rows if matches(validated, row)]


def search_metadata(
    rows: Iterable[Mapping], query: str, keys: Sequence[str]
) -> list[Mapping]:
    """Case-insensitive NFC substring search over whitelisted metadata keys.

    Args:
        rows: Iterable of row mappings.
        query: Search string. Multiple whitespace-separated terms are AND-combined.
               Empty query returns all rows. Max 200 characters.
        keys: Sequence of field names to search. Must be from FIELDS or meta.<key>.

    Returns:
        List of rows where all terms match in at least one of the listed keys.

    Raises:
        FilterError: With code 'bad_value' for invalid query or keys.
    """
    if not isinstance(query, str):
        raise FilterError("bad_value", "query must be a string")
    if len(query) > _MAX_METADATA_QUERY_LENGTH:
        raise FilterError("bad_value", f"query exceeds {_MAX_METADATA_QUERY_LENGTH} characters")

    for key in keys:
        if key not in FIELDS and not (key.startswith("meta.") and _META_KEY_RE.fullmatch(key[5:])):
            raise FilterError("bad_value", f"search key not allowed: {key}")

    if not query.strip():
        return list(rows)

    terms = [_normalise_text(t).casefold() for t in query.split() if t.strip()]
    if not terms:
        return list(rows)

    results = []
    for row in rows:
        matched = True
        for term in terms:
            term_matched = False
            for key in keys:
                value = row.get(key)
                if value is None:
                    continue
                haystack = _normalise_text(str(value)).casefold()
                if term in haystack:
                    term_matched = True
                    break
            if not term_matched:
                matched = False
                break
        if matched:
            results.append(row)
    return results


class FilterStore:
    """In-memory filter store (persistence is a later wiring concern)."""

    def __init__(self) -> None:
        self._filters: dict[int, dict[str, tuple[str, dict]]] = {}
        # user_id -> {filter_id: (name, spec)}

    def _next_id(self, user_id: int) -> str:
        existing = self._filters.get(user_id, {})
        n = 1
        while True:
            fid = f"f{n}"
            if fid not in existing:
                return fid
            n += 1

    def save(self, user_id: int, name: str, spec: Mapping) -> str:
        """Save a filter for a user.

        Args:
            user_id: Owner user ID.
            name: Filter name, 1..60 chars, NFC, stripped.
            spec: Validated filter specification.

        Returns:
            Filter ID string.

        Raises:
            FilterError: With code 'limit_reached', 'duplicate_name', or validation errors.
        """
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise FilterError("bad_value", "user_id must be an int")

        normalised_name = _normalise_text(name).strip()
        if not normalised_name:
            raise FilterError("bad_value", "name must not be empty")
        if len(normalised_name) > _MAX_NAME_LENGTH:
            raise FilterError("bad_value", f"name must be at most {_MAX_NAME_LENGTH} characters")

        validated = validate_filter(spec)

        user_filters = self._filters.setdefault(user_id, {})

        if len(user_filters) >= MAX_FILTERS_PER_USER:
            raise FilterError("limit_reached", f"maximum {MAX_FILTERS_PER_USER} filters per user")

        name_casefold = normalised_name.casefold()
        for fid, (existing_name, _) in user_filters.items():
            if existing_name.casefold() == name_casefold:
                raise FilterError("duplicate_name", "a filter with this name already exists")

        filter_id = self._next_id(user_id)
        user_filters[filter_id] = (normalised_name, validated)
        return filter_id

    def rename(self, user_id: int, filter_id: str, name: str) -> None:
        """Rename a saved filter."""
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise FilterError("bad_value", "user_id must be an int")

        user_filters = self._filters.get(user_id)
        if user_filters is None or filter_id not in user_filters:
            raise FilterError("not_found", "filter not found")

        normalised_name = _normalise_text(name).strip()
        if not normalised_name:
            raise FilterError("bad_value", "name must not be empty")
        if len(normalised_name) > _MAX_NAME_LENGTH:
            raise FilterError("bad_value", f"name must be at most {_MAX_NAME_LENGTH} characters")

        name_casefold = normalised_name.casefold()
        for fid, (existing_name, _) in user_filters.items():
            if fid != filter_id and existing_name.casefold() == name_casefold:
                raise FilterError("duplicate_name", "a filter with this name already exists")

        old_name, spec = user_filters[filter_id]
        user_filters[filter_id] = (normalised_name, spec)

    def delete(self, user_id: int, filter_id: str) -> None:
        """Delete a saved filter."""
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise FilterError("bad_value", "user_id must be an int")

        user_filters = self._filters.get(user_id)
        if user_filters is None or filter_id not in user_filters:
            raise FilterError("not_found", "filter not found")

        del user_filters[filter_id]

    def list(self, user_id: int) -> list[tuple[str, str, dict]]:
        """List all filters for a user, sorted by name casefold."""
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise FilterError("bad_value", "user_id must be an int")

        user_filters = self._filters.get(user_id, {})
        result = [(fid, name, spec) for fid, (name, spec) in user_filters.items()]
        result.sort(key=lambda x: x[1].casefold())
        return result


from datetime import timezone