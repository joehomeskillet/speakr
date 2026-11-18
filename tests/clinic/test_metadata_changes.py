"""Tests for src.clinic.metadata_changes."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from src.clinic.metadata_changes import (
    FIELD_ACTION,
    ChangeError,
    ChangePlan,
    FieldChange,
    plan_changes,
    to_audit_rows,
)


class TestPlanChanges:
    """Tests for plan_changes function."""

    def test_unknown_field_raises(self):
        current = {"title": "Test", "priority": 1}
        requested = {"status": "done"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename", "priority"})
        assert exc.value.code == "unknown_field"
        assert exc.value.field == "status"

    def test_status_field_raises_unknown_field(self):
        current = {"title": "Test", "priority": 1}
        requested = {"status": "completed"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename", "priority", "reassign"})
        assert exc.value.code == "unknown_field"
        assert exc.value.field == "status"

    def test_equal_value_omitted_from_plan(self):
        current = {"title": "Test", "priority": 1}
        requested = {"title": "Test"}
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert plan.changes == ()

    def test_empty_request_gives_empty_plan(self):
        current = {"title": "Test", "priority": 1}
        requested = {}
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert plan.changes == ()

    def test_forbidden_action_raises(self):
        current = {"title": "Old"}
        requested = {"title": "New"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "forbidden"
        assert exc.value.action == "rename"

    def test_allowed_rename_succeeds(self):
        current = {"title": "Old"}
        requested = {"title": "New"}
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert len(plan.changes) == 1
        assert plan.changes[0].field == "title"
        assert plan.changes[0].old == "Old"
        assert plan.changes[0].new == "New"

    def test_title_nfc_normalisation(self):
        current = {"title": "Test"}
        # "é" as e + combining acute
        requested = {"title": "Te\u0301st"}
        plan = plan_changes(current, requested, allowed_actions={"rename"})
        assert plan.changes[0].new == "Tést"

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
        requested = {"title": "Test\x00"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    def test_title_category_cf_raises(self):
        current = {"title": "Test"}
        # Zero-width space is Cf
        requested = {"title": "Test\u200b"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"

    def test_priority_valid_values(self):
        current = {"priority": 1}
        for val in (0, 1, 128):
            requested = {"priority": val}
            plan = plan_changes(current, requested, allowed_actions={"priority"})
            if val == 1:
                assert plan.changes == ()
            else:
                assert plan.changes[0].new == val

    def test_priority_bool_raises(self):
        current = {"priority": 1}
        requested = {"priority": True}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    def test_priority_string_raises(self):
        current = {"priority": 1}
        requested = {"priority": "1"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    def test_priority_float_raises(self):
        current = {"priority": 1}
        requested = {"priority": 1.0}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    def test_priority_invalid_int_raises(self):
        current = {"priority": 1}
        requested = {"priority": 2}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    def test_priority_negative_raises(self):
        current = {"priority": 1}
        requested = {"priority": -1}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_priority"

    def test_deadline_date_valid(self):
        current = {"deadline_date": None}
        requested = {"deadline_date": date(2026, 12, 31)}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == date(2026, 12, 31)

    def test_deadline_date_none_clears(self):
        current = {"deadline_date": date(2026, 12, 31)}
        requested = {"deadline_date": None}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new is None

    def test_deadline_date_datetime_raises(self):
        current = {"deadline_date": None}
        requested = {"deadline_date": datetime(2026, 12, 31, 23, 59)}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_deadline"

    def test_deadline_date_string_raises(self):
        current = {"deadline_date": None}
        requested = {"deadline_date": "2026-12-31"}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_deadline"

    def test_deadline_time_valid(self):
        current = {"deadline_date": date(2026, 12, 31), "deadline_time": None}
        requested = {"deadline_time": time(17, 30)}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == time(17, 30)

    def test_deadline_time_none_clears(self):
        current = {"deadline_date": date(2026, 12, 31), "deadline_time": time(17, 30)}
        requested = {"deadline_time": None}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new is None

    def test_deadline_time_without_date_raises(self):
        current = {"deadline_date": None, "deadline_time": None}
        requested = {"deadline_time": time(17, 30)}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "deadline_date_required"

    def test_deadline_time_with_date_in_current_succeeds(self):
        current = {"deadline_date": date(2026, 12, 31), "deadline_time": None}
        requested = {"deadline_time": time(17, 30)}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert plan.changes[0].new == time(17, 30)

    def test_deadline_time_datetime_raises(self):
        current = {"deadline_date": date(2026, 12, 31), "deadline_time": None}
        requested = {"deadline_time": datetime(2026, 12, 31, 17, 30)}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"priority"})
        assert exc.value.code == "invalid_deadline"

    def test_deadline_both_none_clears(self):
        current = {"deadline_date": date(2026, 12, 31), "deadline_time": time(17, 30)}
        requested = {"deadline_date": None, "deadline_time": None}
        plan = plan_changes(current, requested, allowed_actions={"priority"})
        assert len(plan.changes) == 2
        assert all(c.new is None for c in plan.changes)

    def test_reassign_author_user_id_valid(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": 2}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new == 2

    def test_reassign_author_user_id_none_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": None}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"
        assert exc.value.field == "author_user_id"

    def test_reassign_author_user_id_bool_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": True}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_reassign_author_user_id_zero_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": 0}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_reassign_author_user_id_negative_raises(self):
        current = {"author_user_id": 1}
        requested = {"author_user_id": -1}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_reassign_assignee_user_id_valid(self):
        current = {"assignee_user_id": 1}
        requested = {"assignee_user_id": 2}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new == 2

    def test_reassign_assignee_user_id_none_unassigns(self):
        current = {"assignee_user_id": 1}
        requested = {"assignee_user_id": None}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new is None

    def test_reassign_assignee_user_id_bool_raises(self):
        current = {"assignee_user_id": 1}
        requested = {"assignee_user_id": True}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_reassign_pool_id_valid(self):
        current = {"pool_id": "pool1"}
        requested = {"pool_id": "pool2"}
        plan = plan_changes(current, requested, allowed_actions={"reassign"})
        assert plan.changes[0].new == "pool2"

    def test_reassign_pool_id_empty_raises(self):
        current = {"pool_id": "pool1"}
        requested = {"pool_id": ""}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_reassign_pool_id_too_long_raises(self):
        current = {"pool_id": "pool1"}
        requested = {"pool_id": "x" * 37}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_reassign_pool_id_not_string_raises(self):
        current = {"pool_id": "pool1"}
        requested = {"pool_id": 123}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"reassign"})
        assert exc.value.code == "invalid_reassign"

    def test_multiple_fields_atomic_forbidden(self):
        current = {"title": "Old", "priority": 1}
        requested = {"title": "New", "priority": 2}
        with pytest.raises(ChangeError) as exc:
            plan_changes(current, requested, allowed_actions={"rename"})
        assert exc.value.code == "forbidden"
        assert exc.value.action == "priority"

    def test_multiple_fields_allowed_succeeds(self):
        current = {"title": "Old", "priority": 1}
        requested = {"title": "New", "priority": 128}
        plan = plan_changes(current, requested, allowed_actions={"rename", "priority"})
        assert len(plan.changes) == 2


class TestToAuditRows:
    """Tests for to_audit_rows function."""

    def test_basic_audit_rows(self):
        plan = ChangePlan(
            changes=(
                FieldChange(field="title", old="Old", new="New"),
                FieldChange(field="priority", old=1, new=128),
            )
        )
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=42, dictation_id="dict-123", now=now)
        assert len(rows) == 2
        for row in rows:
            assert row["actor_id"] == 42
            assert row["dictation_id"] == "dict-123"
            assert row["at"] == now
        assert rows[0]["field"] == "title"
        assert rows[0]["old"] == "Old"
        assert rows[0]["new"] == "New"
        assert rows[1]["field"] == "priority"
        assert rows[1]["old"] == 1
        assert rows[1]["new"] == 128

    def test_naive_datetime_raises(self):
        plan = ChangePlan(changes=(FieldChange(field="title", old="Old", new="New"),))
        now = datetime(2026, 11, 18, 12, 0, 0)  # naive
        with pytest.raises(ValueError, match="timezone-aware"):
            to_audit_rows(plan, actor_id=42, dictation_id="dict-123", now=now)

    def test_aware_datetime_converted_to_utc(self):
        plan = ChangePlan(changes=(FieldChange(field="title", old="Old", new="New"),))
        # CET is UTC+1
        from datetime import timedelta

        cet = timezone(timedelta(hours=1))
        now = datetime(2026, 11, 18, 13, 0, 0, tzinfo=cet)
        rows = to_audit_rows(plan, actor_id=42, dictation_id="dict-123", now=now)
        assert rows[0]["at"] == datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

    def test_empty_plan_returns_empty_list(self):
        plan = ChangePlan(changes=())
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        rows = to_audit_rows(plan, actor_id=42, dictation_id="dict-123", now=now)
        assert rows == []


class TestFieldActionMapping:
    """Tests for FIELD_ACTION constant."""

    def test_mapping_complete(self):
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


class TestErrorAttributes:
    """Tests that ChangeError carries expected attributes."""

    def test_forbidden_has_action(self):
        with pytest.raises(ChangeError) as exc:
            plan_changes({"title": "Old"}, {"title": "New"}, allowed_actions={"priority"})
        assert exc.value.code == "forbidden"
        assert exc.value.action == "rename"
        assert exc.value.field is None

    def test_unknown_field_has_field(self):
        with pytest.raises(ChangeError) as exc:
            plan_changes({"title": "Old"}, {"status": "done"}, allowed_actions={"rename"})
        assert exc.value.code == "unknown_field"
        assert exc.value.field == "status"
        assert exc.value.action is None

    def test_invalid_title_has_field(self):
        with pytest.raises(ChangeError) as exc:
            plan_changes({"title": "Old"}, {"title": ""}, allowed_actions={"rename"})
        assert exc.value.code == "invalid_title"
        assert exc.value.field == "title"