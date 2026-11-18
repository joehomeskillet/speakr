"""User start: remembered selection and user switch.

Pure session logic. No Flask routes, no database access, no clock.
State lives in an in-memory store; persistence and routes are a later wiring package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class LastSelectionError(ValueError):
    """The selection operation was rejected before it changed state."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Selection:
    """A user's remembered selection for a dictation session."""

    role: str  # "author" | "typist"
    author_id: int | None
    pool_id: str | None

    def __post_init__(self) -> None:
        _validate_selection(self)


@dataclass(frozen=True)
class Allowed:
    """What a user is currently allowed to use."""

    roles: frozenset[str]
    author_ids: frozenset[int]
    pool_ids: frozenset[str]
    own_author_id: int | None

    def __post_init__(self) -> None:
        _validate_allowed(self)


@dataclass(frozen=True)
class Restored:
    """Result of restoring a selection against current allowances."""

    selection: Selection
    fallback: tuple[str, ...]  # reason codes, empty when nothing changed


def _validate_role(role: str) -> None:
    if role not in ("author", "typist"):
        raise LastSelectionError("invalid_role", f"role must be 'author' or 'typist', got {role!r}")


def _validate_user_id(user_id: int) -> None:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise LastSelectionError("invalid_user_id", "user_id must be a positive int")


def _validate_selection(selection: Selection) -> None:
    if not isinstance(selection, Selection):
        raise LastSelectionError("invalid_selection", "selection must be a Selection")
    _validate_role(selection.role)
    if selection.author_id is not None:
        if isinstance(selection.author_id, bool) or not isinstance(selection.author_id, int):
            raise LastSelectionError("invalid_author_id", "author_id must be an int or None")
    if selection.pool_id is not None:
        if not isinstance(selection.pool_id, str) or selection.pool_id == "":
            raise LastSelectionError("invalid_pool_id", "pool_id must be a non-empty string or None")


def _validate_allowed(allowed: Allowed) -> None:
    if not isinstance(allowed, Allowed):
        raise LastSelectionError("invalid_allowed", "allowed must be an Allowed")
    if not isinstance(allowed.roles, frozenset):
        raise LastSelectionError("invalid_roles", "roles must be a frozenset")
    if not isinstance(allowed.author_ids, frozenset):
        raise LastSelectionError("invalid_author_ids", "author_ids must be a frozenset")
    if not isinstance(allowed.pool_ids, frozenset):
        raise LastSelectionError("invalid_pool_ids", "pool_ids must be a frozenset")
    for role in allowed.roles:
        _validate_role(role)
    for aid in allowed.author_ids:
        if isinstance(aid, bool) or not isinstance(aid, int):
            raise LastSelectionError("invalid_author_id_in_allowed", "author_ids must contain ints")
    for pid in allowed.pool_ids:
        if not isinstance(pid, str) or pid == "":
            raise LastSelectionError("invalid_pool_id_in_allowed", "pool_ids must contain non-empty strings")
    if allowed.own_author_id is not None:
        if isinstance(allowed.own_author_id, bool) or not isinstance(allowed.own_author_id, int):
            raise LastSelectionError("invalid_own_author_id", "own_author_id must be an int or None")


def restore(saved: Selection | None, allowed: Allowed) -> Restored:
    """Restore a saved selection against current allowances.

    The result is always a subset of ``allowed``. ``fallback`` contains reason
    codes for each component that had to change; empty when nothing changed.
    """
    _validate_allowed(allowed)

    if saved is not None:
        _validate_selection(saved)

    if not allowed.roles:
        raise ValueError("user has no roles")

    fallback_reasons: list[str] = []

    # Role
    if saved is not None and saved.role in allowed.roles:
        role = saved.role
    else:
        role = min(allowed.roles)  # lexicographically first
        if saved is not None:
            fallback_reasons.append("role_not_allowed")

    # Author
    if saved is not None and saved.author_id is not None and saved.author_id in allowed.author_ids:
        author_id = saved.author_id
    else:
        if saved is not None and saved.author_id is not None:
            # Fallback from saved selection: own_author_id must be allowed
            author_id = allowed.own_author_id if allowed.own_author_id in allowed.author_ids else None
            fallback_reasons.append("author_not_allowed")
        else:
            # Default (no saved selection): use own_author_id directly
            author_id = allowed.own_author_id

    # Pool
    if saved is not None and saved.pool_id is not None and saved.pool_id in allowed.pool_ids:
        pool_id = saved.pool_id
    else:
        pool_id = None
        if saved is not None and saved.pool_id is not None:
            fallback_reasons.append("pool_not_allowed")

    restored = Selection(role=role, author_id=author_id, pool_id=pool_id)
    return Restored(selection=restored, fallback=tuple(fallback_reasons))


def serialize(selection: Selection) -> str:
    """Serialize a Selection to a JSON string."""
    _validate_selection(selection)
    payload: dict[str, Any] = {
        "role": selection.role,
    }
    if selection.author_id is not None:
        payload["author_id"] = selection.author_id
    if selection.pool_id is not None:
        payload["pool_id"] = selection.pool_id
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def deserialize(text: str | None) -> Selection | None:
    """Deserialize a Selection from a JSON string.

    Returns None for None, empty string, invalid JSON, a list, unknown role,
    or wrong types.
    """
    if text is None or text == "":
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    role = data.get("role")
    if not isinstance(role, str):
        return None
    if role not in ("author", "typist"):
        return None

    author_id = data.get("author_id")
    if author_id is not None:
        if isinstance(author_id, bool) or not isinstance(author_id, int):
            return None

    pool_id = data.get("pool_id")
    if pool_id is not None:
        if not isinstance(pool_id, str) or pool_id == "":
            return None

    # Reject extra keys
    allowed_keys = {"role", "author_id", "pool_id"}
    if set(data.keys()) - allowed_keys:
        return None

    return Selection(role=role, author_id=author_id, pool_id=pool_id)


class SelectionStore:
    """In-memory store for remembered selections per user."""

    def __init__(self) -> None:
        self._selections: dict[int, Selection] = {}

    def remember(self, user_id: int, selection: Selection) -> None:
        """Store the selection for a user."""
        _validate_user_id(user_id)
        _validate_selection(selection)
        self._selections[user_id] = selection

    def start_context(self, user_id: int, allowed: Allowed) -> Restored:
        """Restore the stored selection for a user against current allowances.

        If no selection is stored, uses the default (role 'author' if held
        else 'typist', own author, no pool).
        """
        _validate_user_id(user_id)
        _validate_allowed(allowed)

        saved = self._selections.get(user_id)
        return restore(saved, allowed)

    def switch_user(
        self, previous_user_id: int, new_user_id: int, allowed: Allowed
    ) -> Restored:
        """Switch from one user to another.

        The previous user's stored selection is preserved. The new user starts
        from their own stored selection or the default, never from the
        previous user's selection.
        """
        _validate_user_id(previous_user_id)
        _validate_user_id(new_user_id)
        _validate_allowed(allowed)

        # previous_user_id's entry stays untouched in self._selections
        saved = self._selections.get(new_user_id)
        return restore(saved, allowed)