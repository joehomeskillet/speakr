"""Acceptance tests for the in-page binding service."""

import ast
import inspect
from dataclasses import FrozenInstanceError

import pytest

from src.clinic import key_bindings
from src.clinic.key_bindings import (
    ACTIONS,
    DEFAULTS,
    BindingConflict,
    BindingError,
    dispatch,
    feedback_for,
    from_json,
    normalize_chord,
    reset,
    set_binding,
    to_json,
)


@pytest.mark.parametrize(("raw", "canonical"), [
    (" meta + sHiFt + CTRL + aLt + r ", "Ctrl+Alt+Shift+Meta+R"),
    ("alt+ESC", "Alt+Escape"),
    ("escape", "Escape"),
    ("SPACE", "Space"),
    ("spacebar", "Space"),
    ("Del", "Delete"),
    ("delete", "Delete"),
    ("f1", "F1"),
    ("f24", "F24"),
])
def test_normalizes_chords(raw: str, canonical: str) -> None:
    assert normalize_chord(raw) == canonical


@pytest.mark.parametrize("raw", [
    None, True, 123, "", " ", "Hyper+R", "Ctrl+", "+R", "Ctrl++R", "Ctrl+Ctrl+R",
    "Ctrl+Alt", "F0", "F25", "F01", "Ctrl+\tR", "r\n", "Ctrl+\u200bR",
    "Ctrl+Pedal:Left", "Pedal:9", "Pedal:0", "Pedal:Front", "Pedal:",
])
def test_rejects_malformed_chords(raw: object) -> None:
    with pytest.raises(BindingError, match="^bad_chord$"):
        normalize_chord(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize("pedal", ["Left", "Middle", "Right", *"12345678"])
def test_normalizes_standalone_pedals(pedal: str) -> None:
    assert normalize_chord(f"pedal:{pedal.lower()}") == f"Pedal:{pedal}"


@pytest.mark.parametrize("chord", [
    "Ctrl+W", "Ctrl+T", "Ctrl+N", "Ctrl+Shift+N", "Ctrl+Tab", "Ctrl+L", "Ctrl+Q",
    "Alt+F4", "F5", "Ctrl+R", "F11", "F12", "Ctrl+Shift+I", "shift + ctrl + Del",
])
def test_rejects_reserved_browser_chords(chord: str) -> None:
    with pytest.raises(BindingError) as error:
        normalize_chord(chord)
    assert error.value.code == "reserved_chord"


def test_set_binding_copies_and_canonicalizes_without_displacing() -> None:
    original = dict(DEFAULTS)
    updated = set_binding(original, "stop", "pedal:left")
    assert updated == {**DEFAULTS, "stop": "Pedal:Left"}
    assert original == DEFAULTS
    assert updated is not original
    assert set_binding(updated, "stop", "PEDAL:LEFT") == updated
    with pytest.raises(BindingConflict) as error:
        set_binding(updated, "rewind", "pedal:left")
    assert error.value.conflicts == (("Pedal:Left", "stop"),)
    assert updated == {**DEFAULTS, "stop": "Pedal:Left"}
    with pytest.raises(BindingError, match="^unknown_action$"):
        set_binding(updated, "unknown", "F1")


def test_defaults_cover_every_action_with_unique_valid_chords() -> None:
    assert ACTIONS == (
        "record_toggle", "play_pause", "stop", "rewind", "forward", "speed_up",
        "speed_down", "undo", "redo", "insert_marker", "next_marker",
        "previous_marker", "finish",
    )
    assert set(DEFAULTS) == set(ACTIONS)
    assert len(set(DEFAULTS.values())) == len(ACTIONS)
    assert all(normalize_chord(chord) == chord for chord in DEFAULTS.values())


def test_reset_all_or_one_action() -> None:
    custom = {**DEFAULTS, "stop": "Pedal:Left", "rewind": "Pedal:Right"}
    restored = reset(custom)
    assert restored == DEFAULTS
    assert restored is not DEFAULTS
    assert reset(custom, "stop") == {**DEFAULTS, "rewind": "Pedal:Right"}
    assert custom["stop"] == "Pedal:Left"


def test_reset_detects_a_claimed_default() -> None:
    custom = set_binding(DEFAULTS, "stop", "Pedal:Left")
    custom = set_binding(custom, "rewind", DEFAULTS["stop"])
    with pytest.raises(BindingConflict) as error:
        reset(custom, "stop")
    assert error.value.conflicts == ((DEFAULTS["stop"], "rewind"),)


def test_dispatch_returns_only_the_action_id() -> None:
    bindings = set_binding(DEFAULTS, "record_toggle", "Pedal:1")
    action = dispatch(bindings, " pedal:1 ")
    assert type(action) is str
    assert action == "record_toggle"
    assert dispatch(bindings, "shift + alt + s") == "stop"
    assert dispatch(bindings, DEFAULTS["record_toggle"]) is None


def test_dispatch_module_has_no_clinic_imports() -> None:
    def is_clinic(name: str) -> bool:
        return name == "src.clinic" or name.startswith("src.clinic.")

    for node in ast.walk(ast.parse(inspect.getsource(key_bindings))):
        if isinstance(node, ast.Import):
            assert not any(is_clinic(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert not is_clinic(node.module or "")
            if node.module == "src":
                assert all(alias.name != "clinic" for alias in node.names)


@pytest.mark.parametrize("text", [None, "", "{broken", "null", "[]", '"stop"', "123"])
def test_json_garbage_returns_a_fresh_defaults_copy(text: str | None) -> None:
    decoded = from_json(text)
    assert decoded == DEFAULTS
    assert decoded is not DEFAULTS


def test_json_sanitizes_conflicts_and_fills_only_available_defaults() -> None:
    bindings = from_json('''{
        "unknown": "F1",
        "undo": "pedal:left",
        "stop": "PEDAL:LEFT",
        "finish": "ctrl+w",
        "next_marker": ["F1"],
        "record_toggle": "ctrl++r",
        "forward": "alt+shift+space"
    }''')
    expected = {**DEFAULTS, "stop": "Pedal:Left", "forward": DEFAULTS["play_pause"]}
    del expected["play_pause"]
    assert bindings == expected
    assert from_json(to_json(bindings)) == bindings


def test_json_round_trips_a_complete_custom_table() -> None:
    bindings = set_binding(DEFAULTS, "finish", "ctrl+alt+enter")
    assert from_json(to_json(bindings)) == bindings


@pytest.mark.parametrize(("state", "label", "indicator", "aria_live"), [
    ("idle", "Bereit", "idle", "polite"),
    ("recording", "Aufnahme läuft", "recording", "polite"),
    ("paused", "Pausiert", "paused", "polite"),
    ("error", "Fehler", "error", "assertive"),
    ("lease_lost", "Bearbeitung gesperrt", "locked", "assertive"),
])
def test_feedback_states(state: str, label: str, indicator: str, aria_live: str) -> None:
    feedback = feedback_for(state)
    assert (feedback.label, feedback.indicator, feedback.aria_live) == (label, indicator, aria_live)


def test_feedback_is_immutable_and_unknown_states_are_rejected() -> None:
    with pytest.raises(FrozenInstanceError):
        feedback_for("idle").label = "changed"  # type: ignore[misc]
    with pytest.raises(BindingError, match="^unknown_state$"):
        feedback_for("unknown")
