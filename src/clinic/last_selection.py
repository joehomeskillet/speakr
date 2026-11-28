"""User start: remembered selection and user switch.

SelectionStore is the in-memory cache. Pass a preference store to keep the
same fields per user id across process restarts. PresetUserPreferences writes
one clinic user-preset row (scope ``user``). Restore rechecks author, pool,
source and template access. Switching users drops the active client selection
and loads only the new user's row.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

PREFERENCE_VERSION = 1
LAST_SELECTION_KEY = "last_selection"


class LastSelectionError(ValueError):
    """The selection operation was rejected before it changed state."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class PreferenceUnavailable(Exception):
    """The user-preference store failed. SelectionStore retries this once."""


@dataclass(frozen=True)
class Selection:
    """A user's remembered selection for a dictation session."""

    role: str  # "author" | "typist"
    author_id: int | None
    pool_id: str | None
    source_id: str | None = None
    template_id: str | None = None

    def __post_init__(self) -> None:
        _validate_selection(self)


@dataclass(frozen=True)
class Allowed:
    """What a user is currently allowed to use."""

    roles: frozenset[str]
    author_ids: frozenset[int]
    pool_ids: frozenset[str]
    own_author_id: int | None
    source_ids: frozenset[str] = frozenset()
    template_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _validate_allowed(self)


@dataclass(frozen=True)
class Restored:
    """Result of restoring a selection against current allowances."""

    selection: Selection
    fallback: tuple[str, ...]  # reason codes, empty when nothing changed


def _optional_text(value: str | None, code: str, field: str) -> None:
    if value is not None and (not isinstance(value, str) or value == ""):
        raise LastSelectionError(code, f"{field} must be a non-empty string or None")


def _text_set(values: frozenset[str], set_code: str, item_code: str) -> None:
    if not isinstance(values, frozenset):
        raise LastSelectionError(set_code, f"{set_code} must be a frozenset")
    for item in values:
        if not isinstance(item, str) or item == "":
            raise LastSelectionError(item_code, f"{item_code} must contain non-empty strings")


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
    _optional_text(selection.pool_id, "invalid_pool_id", "pool_id")
    _optional_text(selection.source_id, "invalid_source_id", "source_id")
    _optional_text(selection.template_id, "invalid_template_id", "template_id")


def _validate_allowed(allowed: Allowed) -> None:
    if not isinstance(allowed, Allowed):
        raise LastSelectionError("invalid_allowed", "allowed must be an Allowed")
    if not isinstance(allowed.roles, frozenset):
        raise LastSelectionError("invalid_roles", "roles must be a frozenset")
    if not isinstance(allowed.author_ids, frozenset):
        raise LastSelectionError("invalid_author_ids", "author_ids must be a frozenset")
    for role in allowed.roles:
        _validate_role(role)
    for aid in allowed.author_ids:
        if isinstance(aid, bool) or not isinstance(aid, int):
            raise LastSelectionError("invalid_author_id_in_allowed", "author_ids must contain ints")
    _text_set(allowed.pool_ids, "invalid_pool_ids", "invalid_pool_id_in_allowed")
    _text_set(allowed.source_ids, "invalid_source_ids", "invalid_source_id_in_allowed")
    _text_set(allowed.template_ids, "invalid_template_ids", "invalid_template_id_in_allowed")
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

    source_id, source_reason = _restore_choice(
        None if saved is None else saved.source_id, allowed.source_ids, "source_not_allowed"
    )
    template_id, template_reason = _restore_choice(
        None if saved is None else saved.template_id, allowed.template_ids, "template_not_allowed"
    )
    if source_reason:
        fallback_reasons.append(source_reason)
    if template_reason:
        fallback_reasons.append(template_reason)

    restored = Selection(
        role=role, author_id=author_id, pool_id=pool_id, source_id=source_id, template_id=template_id,
    )
    return Restored(selection=restored, fallback=tuple(fallback_reasons))


def _restore_choice(
    saved_id: str | None, allowed_ids: frozenset[str], reason: str
) -> tuple[str | None, str | None]:
    """Keep an allowed id. An unauthorized id falls back to one allowed id."""
    if saved_id is None:
        return None, None
    if saved_id in allowed_ids:
        return saved_id, None
    fallback = min(allowed_ids) if allowed_ids else None
    return fallback, reason


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
    if selection.source_id is not None:
        payload["source_id"] = selection.source_id
    if selection.template_id is not None:
        payload["template_id"] = selection.template_id
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
    source_id = data.get("source_id")
    template_id = data.get("template_id")
    if not _json_text(pool_id) or not _json_text(source_id) or not _json_text(template_id):
        return None

    # Reject extra keys
    allowed_keys = {"role", "author_id", "pool_id", "source_id", "template_id"}
    if set(data.keys()) - allowed_keys:
        return None

    return Selection(
        role=role, author_id=author_id, pool_id=pool_id, source_id=source_id, template_id=template_id,
    )


def _json_text(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value != "")


def encode_preference(user_id: int, selection: Selection) -> str:
    """JSON document stored as the user's last-selection preference."""
    _validate_user_id(user_id)
    body = json.loads(serialize(selection))
    return json.dumps(
        {"version": PREFERENCE_VERSION, "user_id": user_id, "selection": body},
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_preference(user_id: int, payload: str | None) -> Selection | None:
    """Return the selection for this user. Stale, foreign or corrupt payloads are None."""
    _validate_user_id(user_id)
    if payload is None or payload == "":
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    owner = data.get("user_id")
    if isinstance(version, bool) or version != PREFERENCE_VERSION:
        return None
    if isinstance(owner, bool) or owner != user_id:
        return None
    body = data.get("selection")
    if not isinstance(body, dict):
        return None
    return deserialize(json.dumps(body, separators=(",", ":")))


def _preference_call(fn: Callable[[], Any]) -> Any:
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return fn()
    except LastSelectionError:
        raise
    except (OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:
        raise PreferenceUnavailable(str(exc)) from exc


class UserPreferences(Protocol):
    """User-scoped string store. ``get`` returns None when the user has no row."""

    def get(self, user_id: int) -> str | None: ...

    def put(self, user_id: int, payload: str) -> None: ...


class PresetUserPreferences:
    """One clinic user-preset row per user id. The row survives a process restart."""

    def __init__(self, session: Any = None) -> None:
        self._session = session

    def _session_or_default(self) -> Any:
        if self._session is not None:
            return self._session
        from src.database import db
        return db.session

    def get(self, user_id: int) -> str | None:
        _validate_user_id(user_id)
        from src.clinic.presets import ClinicPreset

        def read() -> str | None:
            row = self._session_or_default().query(ClinicPreset).filter_by(
                scope="user", scope_id=str(user_id), key=LAST_SELECTION_KEY,
            ).first()
            return None if row is None else row.value_json

        return _preference_call(read)

    def put(self, user_id: int, payload: str) -> None:
        _validate_user_id(user_id)
        if not isinstance(payload, str) or payload == "":
            raise LastSelectionError("invalid_preference", "payload must be a non-empty string")
        try:
            document = json.loads(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LastSelectionError("invalid_preference", "payload must be JSON") from exc
        if not isinstance(document, dict):
            raise LastSelectionError("invalid_preference", "payload must be a JSON object")
        from src.clinic.presets import set_preset

        def write() -> None:
            set_preset(
                LAST_SELECTION_KEY, document, scope="user", scope_id=str(user_id), session=self._session,
            )
            if self._session is None:  # request session: nobody else commits this row
                from src.database import db
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    raise

        _preference_call(write)


class SelectionStore:
    """Remembered selections per user, optionally backed by a user preference store."""

    def __init__(self, preferences: UserPreferences | None = None) -> None:
        self._preferences = preferences
        self._selections: dict[int, Selection] = {}
        self._client_user_id: int | None = None
        self._client: Selection | None = None

    def client_user_id(self) -> int | None:
        return self._client_user_id

    def client_selection(self) -> Selection | None:
        """Selection the signed-in client is using. None right after an account switch."""
        return self._client

    def remember(self, user_id: int, selection: Selection) -> None:
        """Store the selection for a user and, when configured, in that user's preferences."""
        _validate_user_id(user_id)
        _validate_selection(selection)
        if self._preferences is not None:
            self._write(user_id, encode_preference(user_id, selection))
        self._selections[user_id] = selection

    def start_context(self, user_id: int, allowed: Allowed) -> Restored:
        """Restore the stored selection for a user against current allowances.

        If no selection is stored, uses the default (role 'author' if held
        else 'typist', own author, no pool). A preference store is read when
        the in-memory cache is empty, which is a fresh process.
        """
        _validate_user_id(user_id)
        _validate_allowed(allowed)
        result = restore(self._saved(user_id), allowed)
        self._client_user_id = user_id
        self._client = result.selection
        return result

    def switch_user(
        self, previous_user_id: int, new_user_id: int, allowed: Allowed
    ) -> Restored:
        """Switch from one user to another.

        The active client selection is cleared first. The previous user's
        stored selection is preserved. The new user starts from their own
        stored selection or the default, never from the previous user's.
        """
        _validate_user_id(previous_user_id)
        _validate_user_id(new_user_id)
        _validate_allowed(allowed)
        self._client_user_id = None
        self._client = None
        result = restore(self._saved(new_user_id), allowed)
        self._client_user_id = new_user_id
        self._client = result.selection
        return result

    def _saved(self, user_id: int) -> Selection | None:
        if self._preferences is None:
            return self._selections.get(user_id)
        # The preference row is the source of truth: another worker may have written it.
        loaded = decode_preference(user_id, self._read(user_id))
        if loaded is None:
            self._selections.pop(user_id, None)
        else:
            self._selections[user_id] = loaded
        return loaded

    def _read(self, user_id: int) -> str | None:
        preferences = self._preferences
        if preferences is None:
            return None
        return self._attempt("preference_read_failed", lambda: preferences.get(user_id))

    def _write(self, user_id: int, payload: str) -> None:
        preferences = self._preferences
        if preferences is None:
            return
        self._attempt("preference_write_failed", lambda: preferences.put(user_id, payload))

    def _attempt(self, code: str, fn: Callable[[], Any]) -> Any:
        last: PreferenceUnavailable | None = None
        for _ in range(2):
            try:
                return fn()
            except PreferenceUnavailable as exc:
                last = exc
        raise LastSelectionError(code, str(last)) from last