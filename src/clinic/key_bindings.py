"""In-page keyboard and pedal bindings and German recorder feedback.

Dispatch only identifies a command. The caller executes it subject to the
same lease and permission checks as any other web input. System-wide hotkeys
without browser focus remain the open gap WG-OPS-003.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from flask import current_app, has_app_context
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.database import db

ACTIONS = (
    "record_toggle", "play_pause", "stop", "rewind", "forward", "speed_up",
    "speed_down", "undo", "redo", "insert_marker", "next_marker",
    "previous_marker", "finish",
)

DEFAULTS: Mapping[str, str] = MappingProxyType({
    "record_toggle": "Alt+Shift+R",
    "play_pause": "Alt+Shift+Space",
    "stop": "Alt+Shift+S",
    "rewind": "Alt+Shift+ArrowLeft",
    "forward": "Alt+Shift+ArrowRight",
    "speed_up": "Alt+Shift+ArrowUp",
    "speed_down": "Alt+Shift+ArrowDown",
    "undo": "Alt+Shift+Z",
    "redo": "Alt+Shift+Y",
    "insert_marker": "Alt+Shift+M",
    "next_marker": "Alt+Shift+PageDown",
    "previous_marker": "Alt+Shift+PageUp",
    "finish": "Alt+Shift+Enter",
})

_MODIFIERS = {name.lower(): name for name in ("Ctrl", "Alt", "Shift", "Meta")}
_KEYS = {
    key.lower(): key for key in (
        "Escape", "Space", "Delete", "Enter", "Tab", "Backspace", "Insert",
        "Home", "End", "PageUp", "PageDown", "ArrowLeft", "ArrowRight",
        "ArrowUp", "ArrowDown",
    )
}
_KEYS.update({"esc": "Escape", "spacebar": "Space", "del": "Delete"})
_PEDALS = {name.lower(): name for name in ("Left", "Middle", "Right", *"12345678")}
_RESERVED = {
    "Ctrl+W", "Ctrl+T", "Ctrl+N", "Ctrl+Shift+N", "Ctrl+Tab", "Ctrl+L",
    "Ctrl+Q", "Alt+F4", "F5", "Ctrl+R", "F11", "F12", "Ctrl+Shift+I",
    "Ctrl+Shift+Delete",
}


class BindingError(ValueError):
    """A binding input was rejected with a stable code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BindingConflict(BindingError):
    """The requested chord is already assigned to other actions."""

    def __init__(self, conflicts: tuple[tuple[str, str], ...]):
        self.conflicts = conflicts
        super().__init__("binding_conflict")


def normalize_chord(text: str) -> str:
    """Return the canonical chord, rejecting malformed or reserved input."""
    if not isinstance(text, str) or not text.isascii() or not text.isprintable():
        raise BindingError("bad_chord")

    *modifiers, key = (part.strip() for part in text.lower().split("+"))
    if key.startswith("pedal:"):
        pedal = _PEDALS.get(key.removeprefix("pedal:"))
        if modifiers or pedal is None:
            raise BindingError("bad_chord")
        return f"Pedal:{pedal}"

    modifier_set = set(modifiers)
    if len(modifier_set) != len(modifiers) or modifier_set.difference(_MODIFIERS):
        raise BindingError("bad_chord")

    if key in _KEYS:
        key = _KEYS[key]
    elif re.fullmatch(r"[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])", key):
        key = key.upper()
    else:
        raise BindingError("bad_chord")

    chord = "+".join([*(_MODIFIERS[m] for m in _MODIFIERS if m in modifier_set), key])
    if chord in _RESERVED:
        raise BindingError("reserved_chord")
    return chord


def _require_action(action: str) -> None:
    if not isinstance(action, str) or action not in DEFAULTS:
        raise BindingError("unknown_action")


def _require_mapping(bindings: Mapping[str, str]) -> None:
    if not isinstance(bindings, Mapping):
        raise BindingError("bad_bindings")


def _normalized_items(bindings: Mapping[str, str]) -> dict[str, str]:
    _require_mapping(bindings)
    normalized = {}
    for action, chord in bindings.items():
        _require_action(action)
        normalized[action] = normalize_chord(chord)
    return normalized


def set_binding(bindings: Mapping[str, str], action: str, chord: str) -> dict[str, str]:
    """Return a copy with one assignment changed, unless the chord clashes."""
    _require_action(action)
    chord = normalize_chord(chord)
    current = _normalized_items(bindings)
    conflicts = tuple(
        (chord, other) for other in sorted(current)
        if other != action and current[other] == chord
    )
    if conflicts:
        raise BindingConflict(conflicts)
    return {**bindings, action: chord}


def reset(bindings: Mapping[str, str], action: str | None = None) -> dict[str, str]:
    """Restore all defaults, or restore one without displacing another action."""
    if action is None:
        _require_mapping(bindings)
        return dict(DEFAULTS)
    _require_action(action)
    return set_binding(bindings, action, DEFAULTS[action])


def dispatch(bindings: Mapping[str, str], chord: str) -> str | None:
    """Look up a valid chord without performing the associated command."""
    chord = normalize_chord(chord)
    return next(
        (action for action, key in _normalized_items(bindings).items() if key == chord),
        None,
    )


def to_json(bindings: Mapping[str, str]) -> str:
    """Serialize bindings in canonical, stable order."""
    return json.dumps(_normalized_items(bindings), sort_keys=True)


def from_json(text: str | None) -> dict[str, str]:
    """Recover usable saved bindings, then fill any unclaimed defaults."""
    if not isinstance(text, str):
        return dict(DEFAULTS)
    try:
        saved = json.loads(text)
    except (ValueError, RecursionError):
        return dict(DEFAULTS)
    if not isinstance(saved, dict):
        return dict(DEFAULTS)

    bindings: dict[str, str] = {}
    used: set[str] = set()
    for action in sorted(DEFAULTS.keys() & saved.keys()):
        try:
            chord = normalize_chord(saved[action])
        except BindingError:
            continue
        if chord not in used:
            bindings[action] = chord
            used.add(chord)

    for action, chord in DEFAULTS.items():
        if action not in bindings and chord not in used:
            bindings[action] = chord
    return bindings


@dataclass(frozen=True)
class Feedback:
    label: str
    indicator: str
    aria_live: str


_FEEDBACK = {
    "idle": Feedback("Bereit", "idle", "polite"),
    "recording": Feedback("Aufnahme läuft", "recording", "polite"),
    "paused": Feedback("Pausiert", "paused", "polite"),
    "error": Feedback("Fehler", "error", "assertive"),
    "lease_lost": Feedback("Bearbeitung gesperrt", "locked", "assertive"),
}


def feedback_for(state: str) -> Feedback:
    """Return display and live-region feedback for a known recorder state."""
    if not isinstance(state, str) or state not in _FEEDBACK:
        raise BindingError("unknown_state")
    return _FEEDBACK[state]


class StaleVersion(BindingError):
    """``expected_version`` did not match the stored version."""

    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__("stale_version")


@dataclass(frozen=True)
class StoredBindings:
    """One user's persisted bindings and optimistic-lock version."""

    user_id: int
    version: int
    bindings: dict[str, str]


class ClinicKeyBindings(db.Model):
    """Per-user focused-browser key and pedal bindings. ``version`` guards concurrent writes."""

    __tablename__ = "clinic_key_bindings"
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    version = db.Column(db.Integer, nullable=False)
    bindings_json = db.Column(db.Text, nullable=False)


def create_tables(bind=None) -> None:
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[ClinicKeyBindings.__table__],
        checkfirst=True,
    )


def _user_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise BindingError("bad_user")
    return value


def _version(value: object) -> int:
    if type(value) is not int or value < 0:
        raise BindingError("bad_version")
    return value


def _check_conflicts(normalized: Mapping[str, str]) -> None:
    by_chord: dict[str, list[str]] = {}
    for action in sorted(normalized):
        by_chord.setdefault(normalized[action], []).append(action)
    conflicts = tuple(
        (chord, action)
        for chord, actions in sorted(by_chord.items())
        if len(actions) > 1
        for action in actions
    )
    if conflicts:
        raise BindingConflict(conflicts)


def _db_ready() -> bool:
    return has_app_context() and "sqlalchemy" in current_app.extensions


class KeyBindingStore:
    """Per-user binding store with optimistic concurrency and single-key reset.

    Inside a Flask app context with SQLAlchemy, rows live in ``clinic_key_bindings``.
    Otherwise bindings stay in process memory.
    Callers must supply the authenticated user's ID, never an unchecked client ID.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[int, tuple[int, str]] = {}

    def _durable(self) -> bool:
        return _db_ready()

    def _get(self, user_id: int) -> tuple[int, str] | None:
        if not self._durable():
            return self._rows.get(user_id)
        # Column select bypasses the identity map so concurrent commits are visible.
        row = db.session.execute(
            select(ClinicKeyBindings.version, ClinicKeyBindings.bindings_json).where(
                ClinicKeyBindings.user_id == user_id
            )
        ).first()
        return None if row is None else (row[0], row[1])

    def _commit(self, user_id: int, version: int, text: str, previous: int | None) -> None:
        if not self._durable():
            self._rows[user_id] = (version, text)
            return
        try:
            if previous is None:
                db.session.add(
                    ClinicKeyBindings(user_id=user_id, version=version, bindings_json=text)
                )
                db.session.flush()
            else:
                result = db.session.execute(
                    update(ClinicKeyBindings)
                    .where(
                        ClinicKeyBindings.user_id == user_id,
                        ClinicKeyBindings.version == previous,
                    )
                    .values(version=version, bindings_json=text)
                )
                if result.rowcount != 1:
                    db.session.rollback()
                    latest = self._get(user_id)
                    raise StaleVersion(0 if latest is None else latest[0])
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            latest = self._get(user_id)
            if previous is None and latest is not None:
                raise StaleVersion(latest[0]) from exc
            raise
        except BaseException:
            db.session.rollback()
            raise

    def _update(
        self,
        user_id: int,
        expected_version: object,
        transform: Callable[[dict[str, str]], dict[str, str]],
    ) -> StoredBindings:
        uid = _user_id(user_id)
        expected = _version(expected_version)
        if not self._durable():
            with self._lock:
                return self._apply_update(uid, expected, transform)
        return self._apply_update(uid, expected, transform)

    def _apply_update(
        self,
        user_id: int,
        expected_version: int,
        transform: Callable[[dict[str, str]], dict[str, str]],
    ) -> StoredBindings:
        current = self._get(user_id)
        current_version = 0 if current is None else current[0]
        if expected_version != current_version:
            raise StaleVersion(current_version)
        current_bindings = dict(DEFAULTS) if current is None else from_json(current[1])
        updated = transform(current_bindings)
        text = to_json(updated)
        new_version = current_version + 1
        self._commit(user_id, new_version, text, None if current is None else current_version)
        return StoredBindings(user_id, new_version, updated)

    def load(self, user_id: int) -> StoredBindings:
        """Return the user's bindings, or defaults with version 0 when unset."""
        uid = _user_id(user_id)
        if not self._durable():
            with self._lock:
                current = self._rows.get(uid)
        else:
            current = self._get(uid)
        if current is None:
            return StoredBindings(uid, 0, dict(DEFAULTS))
        return StoredBindings(uid, current[0], from_json(current[1]))

    def save(
        self, user_id: int, bindings: Mapping[str, str], expected_version: int
    ) -> StoredBindings:
        """Validate and persist a full binding map if ``expected_version`` matches."""
        normalized = _normalized_items(bindings)
        _check_conflicts(normalized)
        used = set(normalized.values())
        complete = {
            **normalized,
            **{a: c for a, c in DEFAULTS.items() if a not in normalized and c not in used},
        }
        return self._update(user_id, expected_version, lambda _current: dict(complete))

    def set_binding(
        self, user_id: int, action: str, chord: str, expected_version: int
    ) -> StoredBindings:
        """Change one action's chord unless another action already uses it."""
        _require_action(action)
        canonical = normalize_chord(chord)
        return self._update(
            user_id, expected_version, lambda current: set_binding(current, action, canonical)
        )

    def reset(
        self,
        user_id: int,
        action: str | None = None,
        expected_version: int | None = None,
    ) -> StoredBindings:
        """Restore all defaults, or one action while preserving the other bindings."""
        if action is not None:
            _require_action(action)
        return self._update(user_id, expected_version, lambda current: reset(current, action))
