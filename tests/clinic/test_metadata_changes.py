"""Tests for src.clinic.metadata_changes."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.clinic.metadata_changes import (
    ChangeError,
    ChangePlan,
    FieldChange,
    FIELD_ACTION,
    plan_changes,
    to_audit_rows,
)


class TestPlanChanges:
    def test_empty_request_returns_empty_plan(self):
        current = {"title": "Test", "priority": 1}
        requested = {}
        plan = plan_changes(current, requested, allowed_actions={"rename", "priority"})
        assert isinstance(plan, ChangePlan)
        assert plan.changes == ()

    def test_unknown_field_raises(self):
        current = {"title": "Test"}
        requested = {"status": "done"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "unknown_field"
        assert exc.value.field == "status"

    def test_status_field_raises_unknown_field(self):
        current = {"title": "Test"}
        requested = {"status": "completed"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename", "priority"})
        assert exc.value.code == "unknown_field"
        assert exc.value.field == "status"

    def test_noop_when_value_equals_current(self):
        current = {"title": "Test", "priority": 1}
        requested = {"title": "Test", "priority": 1}
        plan = plan_changes(current, requested, allowed_actions={"rename", "priority"})
        assert plan.changes == ()

    def test_forbidden_action_raises_with_action_attribute(self):
        current = {"title": "Old"}
        requested = {"title": "New"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "forbidden"
        assert exc.value.action == "rename"

    def test_multiple_changes_forbidden_if_any_missing(self):
        current = {"title": "Old", "priority": 0}
        requested = {"title": "New", "priority": 1}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "forbidden"
        assert exc.value.action == "priority"

    def test_allowed_actions_succeeds_for_title_only(self):
        current = {"title": "Old"}
        requested = {"title": "New"}
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert len(plan.changes) == 1
        assert plan.changes[0].field == "title"
        assert plan.changes[0].old == "Old"
        assert plan.changes[0].new == "New"

    # Title validation
    def test_title_nfc_normalised(self):
        current = {"title": "Test"}
        requested = {"title": "caf\u00e9"}  # NFC
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert plan.changes[0].new == "café"

    def test_title_stripped(self):
        current = {"title": "Test"}
        requested = {"title": "  New Title  "}
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert plan.changes[0].new == "New Title"

    def test_title_empty_after_strip_raises(self):
        current = {"title": "Test"}
        requested = {"title": "   "}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    def test_title_too_long_raises(self):
        current = {"title": "Test"}
        requested = {"title": "x" * 201}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    def test_title_control_char_raises(self):
        current = {"title": "Test"}
        requested = {"title": "New\x00Title"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    def test_title_del_char_raises(self):
        current = {"title": "Test"}
        requested = {"title": "New\x7fTitle"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    def test_title_non_string_raises(self):
        current = {"title": "Test"}
        requested = {"title": 123}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    # Priority validation
    @pytest.mark.parametrize("valid_priority", [0, 1, 128])
    def test_valid_priority_accepted(self, valid_priority):
        current = {"priority": 1 if valid_priority != 1 else 0}
        requested = {"priority": valid_priority}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == valid_priority

    @pytest.mark.parametrize("invalid_priority", [-1, 2, 129, 255, "1", True, False, None, 1.5])
    def test_invalid_priority_raises(self, invalid_priority):
        current = {"priority": 1}
        requested = {"priority": invalid_priority}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    def test_priority_not_silently_mapped(self):
        current = {"priority": 1}
        requested = {"priority": 2}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    # Deadline validation
    def test_deadline_date_accepted(self):
        current = {"deadline_date": None}
        requested = {"deadline_date": date(2025, 12, 31)}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == date(2025, 12, 31)

    def test_deadline_date_none_clears(self):
        current = {"deadline_date": date(2025, 12, 31)}
        requested = {"deadline_date": None}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new is None

    def test_deadline_datetime_raises(self):
        current = {"deadline_date": None}
        requested = {"deadline_date": datetime(2025, 12, 31, 23, 59, 59)}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_deadline"

    def test_deadline_time_accepted_as_string(self):
        current = {"deadline_time": None, "deadline_date": date(2025, 12, 31)}
        requested = {"deadline_time": "14:30"}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == "14:30"

    def test_deadline_time_none_clears(self):
        current = {"deadline_time": "14:30", "deadline_date": date(2025, 12, 31)}
        requested = {"deadline_time": None}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new is None

    def test_deadline_time_non_string_raises(self):
        current = {"deadline_time": None, "deadline_date": date(2025, 12, 31)}
        requested = {"deadline_time": 1430}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_deadline"

    def test_deadline_time_without_date_raises(self):
        current = {"deadline_date": None, "deadline_time": None}
        requested = {"deadline_time": "14:30"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "deadline_date_required"

    def test_deadline_time_with_current_date_ok(self):
        current = {"deadline_date": date(2025, 12, 31), "deadline_time": None}
        requested = {"deadline_time": "14:30"}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == "14:30"

    def test_deadline_time_with_requested_date_ok(self):
        current = {"deadline_date": None, "deadline_time": None}
        requested = {"deadline_date": date(2025, 12, 31), "deadline_time": "14:30"}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert len(plan.changes) == 2

    # Reassign validation
    def test_author_user_id_positive_int_accepted(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": 42}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new == 42

    def test_author_user_id_none_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": None}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_author_user_id_zero_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": 0}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_author_user_id_negative_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": -5}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_author_user_id_bool_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": True}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_assignee_user_id_none_allowed(self):
        current = {"assignee_user_id": 5}
        requested = {"assignee_user_id": None}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new is None

    def test_assignee_user_id_positive_int_accepted(self):
        current = {"assignee_user_id": None}
        requested = {"assignee_user_id": 10}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new == 10

    def test_pool_id_non_empty_string_accepted(self):
        current = {"pool_id": "old-pool"}
        requested = {"pool_id": "new-pool-123"}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new == "new-pool-123"

    def test_pool_id_empty_raises(self):
        current = {"pool_id": "old"}
        requested = {"pool_id": ""}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_pool_id_too_long_raises(self):
        current = {"pool_id": "old"}
        requested = {"pool_id": "x" * 37}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_pool_id_non_string_raises(self):
        current = {"pool_id": "old"}
        requested = {"pool_id": 123}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    # FIELD_ACTION mapping coverage
    def test_field_action_mapping_complete(self):
        expected = {
            "title": "rename",
            "priority": "priority",
            "deadline_date": "priority",
            "deadline_time": "priority",
            "author_user_id": "reassign",
            "assignee_user_id": "reassign",
            "pool_id": "reassign",
        }
        assert FIELD_ACTION == expected


class TestToAuditRows:
    def test_returns_one_row_per_change(self):
        plan = ChangePlan(
            changes=(
                FieldChange(field="title", old="Old", new="New"),
                FieldChange(field="priority", old=0, new=1),
            )
        )
        now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=42, dictation_id="dict-123", now=now)
        assert len(rows) == 2
        assert rows[0]["field"] == "title"
        assert rows[1]["field"] == "priority"

    def test_row_keys_exact(self):
        plan = ChangePlan(changes=(FieldChange(field="title", old="A", new="B"),))
        now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=1, dictation_id="d1", now=now)
        row = rows[0]
        assert set(row.keys()) == {"actor_id", "dictation_id", "field", "old", "new", "at"}

    def test_row_values_correct(self):
        plan = ChangePlan(changes=(FieldChange(field="title", old="Old", new="New"),))
        now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=99, dictation_id="dict-abc", now=now)
        row = rows[0]
        assert row["actor_id"] == 99
        assert row["dictation_id"] == "dict-abc"
        assert row["field"] == "title"
        assert row["old"] == "Old"
        assert row["new"] == "New"
        assert row["at"] == now

    def test_naive_now_raises(self):
        plan = ChangePlan(changes=(FieldChange(field="title", old="A", new="B"),))
        naive = datetime(2025, 1, 15, 10, 30, 0)
        with pytest.raises(ValueError) as exc:
            to_audit_rows(plan, actor_id=1, dictation_id="d1", now=naive)
        assert "timezone-aware" in str(exc.value)

    def test_aware_non_utc_converted_to_utc(self):
        plan = ChangePlan(changes=(FieldChange(field="title", old="A", new="B"),))
        from datetime import timedelta

        zurich_offset = timezone(timedelta(hours=1))
        now_zurich = datetime(2025, 1, 15, 11, 30, 0, tzinfo=zurich_offset)
        rows = to_audit_rows(plan, actor_id=1, dictation_id="d1", now=now_zurich)
        assert rows[0]["at"] == datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_empty_plan_returns_empty_list(self):
        plan = ChangePlan(changes=())
        now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=1, dictation_id="d1", now=now)
        assert rows == []

    def test_plan_does_not_touch_status(self):
        plan = ChangePlan(
            changes=(
                FieldChange(field="title", old="A", new="B"),
                FieldChange(field="priority", old=0, new=1),
            )
        )
        now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=1, dictation_id="d1", now=now)
        fields = {row["field"] for row in rows}
        assert "status" not in fields