"""Per-user worklist sort, grouping and column layout.

Column checks, stable sorting and schema merge live in ``list_layout``.
This module stores one canonical JSON blob per user. It does not open the
database, read another user's row, or copy a layout across accounts.

shortcut: in-memory store, move to a user-scoped table when a migration is owned.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from src.clinic.list_layout import (
    Group,
    LayoutError,
    group_rows,
    layout_to_json,
    merge_layout,
    sort_rows,
    validate_layout,
)

MAX_STORED_BYTES = 8192


class WorklistLayoutError(ValueError):
    """The layout store rejected the call before it changed a row."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StoredLayout:
    """A user's layout and the version the next save must send back."""

    user_id: int
    version: int
    layout: dict


def _user_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise WorklistLayoutError("bad_user")
    return value


def _own(actor_id: object, user_id: object) -> int:
    actor = _user_id(actor_id)
    owner = _user_id(user_id)
    if actor != owner:
        raise WorklistLayoutError("unauthorized")
    return owner


def _canonical(spec: Mapping) -> str:
    text = layout_to_json(spec)
    if len(text.encode("utf-8")) > MAX_STORED_BYTES:
        raise WorklistLayoutError("oversized")
    return text


def _canonical_text(text: str) -> str:
    """Accept a stored blob, including columns the current screen no longer has."""
    if not isinstance(text, str):
        raise LayoutError("bad_layout")
    try:
        spec = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise LayoutError("bad_layout") from exc
    if not isinstance(spec, Mapping):
        raise LayoutError("bad_layout")
    return _canonical(spec)


class LayoutStore:
    """One layout per user. ``writer`` runs before the row changes and may raise."""

    def __init__(self, writer: Callable[[int, int, str], None] | None = None) -> None:
        self._rows: dict[int, tuple[int, str]] = {}
        self._writer = writer

    def _commit(self, user_id: int, version: int, text: str) -> None:
        if self._writer is not None:
            self._writer(user_id, version, text)
        self._rows[user_id] = (version, text)

    def save(
        self,
        actor_id: int,
        user_id: int,
        spec: Mapping,
        allowed_columns: Sequence[str],
        expected_version: int,
    ) -> StoredLayout:
        """Store a validated layout. ``expected_version`` is 0 when none exists."""
        owner = _own(actor_id, user_id)
        if type(expected_version) is not int or expected_version < 0:
            raise WorklistLayoutError("bad_version")
        text = _canonical(validate_layout(spec, allowed_columns))
        current = self._rows.get(owner)
        version = 0 if current is None else current[0]
        if expected_version != version:
            raise WorklistLayoutError("stale_version")
        new_version = version + 1
        self._commit(owner, new_version, text)
        return StoredLayout(owner, new_version, json.loads(text))

    def load(
        self,
        actor_id: int,
        user_id: int,
        default: Mapping,
        allowed_columns: Sequence[str],
    ) -> StoredLayout:
        """Return the user's layout merged with the columns that exist now."""
        owner = _own(actor_id, user_id)
        current = self._rows.get(owner)
        if current is None:
            layout = validate_layout(default, allowed_columns)
            return StoredLayout(owner, 0, layout)
        version, text = current
        # layout_from_json rejects retired names; merge_layout drops them.
        layout = merge_layout(default, json.loads(text), allowed_columns)
        return StoredLayout(owner, version, layout)

    def refresh(
        self,
        actor_id: int,
        user_id: int,
        rows: Sequence[Mapping],
        default: Mapping,
        allowed_columns: Sequence[str],
    ) -> tuple[StoredLayout, list[Mapping], list[Group]]:
        """Re-apply the stored sort and groups. Ties keep their previous order."""
        stored = self.load(actor_id, user_id, default, allowed_columns)
        ordered = sort_rows(rows, stored.layout["sort"])
        grouped = group_rows(ordered, stored.layout["group_by"])
        return stored, ordered, grouped

    def switch_account(
        self,
        actor_id: int,
        previous_user_id: int,
        new_user_id: int,
        default: Mapping,
        allowed_columns: Sequence[str],
    ) -> StoredLayout:
        """Open ``new_user_id``. The previous user's layout is not read or copied."""
        owner = _own(actor_id, new_user_id)
        if previous_user_id != owner:
            _user_id(previous_user_id)
        return self.load(actor_id, owner, default, allowed_columns)

    def snapshot(self, actor_id: int, user_id: int) -> tuple[int, str] | None:
        """Return the stored version and canonical JSON, or None."""
        owner = _own(actor_id, user_id)
        return self._rows.get(owner)

    def restore_snapshot(self, actor_id: int, user_id: int, version: int, text: str) -> None:
        """Load a blob into an empty slot. The same blob again is a no-op."""
        owner = _own(actor_id, user_id)
        if type(version) is not int or version < 1:
            raise WorklistLayoutError("bad_version")
        canonical = _canonical_text(text)
        current = self._rows.get(owner)
        if current == (version, canonical):
            return
        if current is not None:
            raise WorklistLayoutError("stale_version")
        self._commit(owner, version, canonical)
