"""Synthetic metadata planner contract for PAR-WF-005 notes."""

from datetime import datetime, timezone

import pytest

from src.clinic.metadata_changes import ChangeError, FieldChange, plan_changes, to_audit_rows


@pytest.fixture
def metadata():
    return {"title": "Synthetic report", "notes": "Previous note", "priority": 1}


@pytest.mark.parametrize("value", ["", " \n\t\r "])
def test_blank_notes_clear_only_notes(metadata, value):
    plan = plan_changes(metadata, {"notes": value}, allowed_actions={"rename"})
    assert plan.changes == (FieldChange("notes", "Previous note", ""),)
    metadata.update({change.field: change.new for change in plan.changes})
    assert metadata == {"title": "Synthetic report", "notes": "", "priority": 1}


@pytest.mark.parametrize("value,expected", [
    ("  Cafe\u0301\nSecond line\tend  ", "Café\nSecond line\tend"),
    ("x" * 4000, "x" * 4000),
])
def test_allowed_notes_are_bounded_normalised_text(metadata, value, expected):
    before = metadata.copy()
    requested = {"notes": value}
    plan = plan_changes(metadata, requested, allowed_actions={"rename"})
    assert plan.changes == (FieldChange("notes", "Previous note", expected),)
    assert metadata == before
    assert requested == {"notes": value}


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "x" * 4001, " " * 4001,
                                      "bad\x00", "bad\u200b", "bad\ud800", "bad\x1f"])
def test_invalid_notes_reject_whole_change_without_write(metadata, value):
    before = metadata.copy()
    with pytest.raises(ChangeError) as raised:
        plan_changes(metadata, {"title": "New title", "notes": value},
                     allowed_actions={"rename"})
    assert raised.value.code == "invalid_notes"
    assert raised.value.field == "notes"
    assert metadata == before


@pytest.mark.parametrize("value", ["New note", "", "Previous note", None])
def test_notes_require_rename_even_for_clear_or_noop(metadata, value):
    before = metadata.copy()
    with pytest.raises(ChangeError) as raised:
        plan_changes(metadata, {"priority": 128, "notes": value},
                     allowed_actions={"priority", "reassign"})
    assert raised.value.code == "forbidden"
    assert raised.value.action == "rename"
    assert metadata == before


def test_later_denied_field_rejects_notes_atomically(metadata):
    before = metadata.copy()
    with pytest.raises(ChangeError) as raised:
        plan_changes(metadata, {"notes": "New note", "priority": 128},
                     allowed_actions={"rename"})
    assert raised.value.code == "forbidden"
    assert metadata == before


def test_unchanged_notes_emit_no_change_or_audit(metadata):
    plan = plan_changes(metadata, {"notes": " Previous note "}, allowed_actions={"rename"})
    assert plan.changes == ()
    assert to_audit_rows(plan, actor_id=1, dictation_id="synthetic", now=datetime.now(timezone.utc)) == []


@pytest.mark.parametrize("value", ["New sensitive note", ""])
def test_notes_audit_contains_field_name_without_old_or_new_text(metadata, value):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    plan = plan_changes(metadata, {"notes": value}, allowed_actions={"rename"})
    assert to_audit_rows(plan, actor_id=1, dictation_id="synthetic", now=now) == [
        {"actor_id": 1, "dictation_id": "synthetic", "field": "notes", "at": now}
    ]
