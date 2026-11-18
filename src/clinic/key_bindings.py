"""In-page keyboard and pedal bindings and German recorder feedback.

Dispatch only identifies a command. The caller executes it subject to the
same lease and permission checks as any other web input. System-wide hotkeys
without browser focus remain the open gap WG-OPS-003.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

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
