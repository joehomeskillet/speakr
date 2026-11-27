"""Compute and validate a change plan for editing dictation metadata.

Renaming, priority and reassignment are separate actions with separate rights.
The plan is atomic and produces audit rows. Pure planning only: the plan is
applied by later wiring.

Non-scope: persistence, routes, workflow transitions, status changes.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Collection, Any


FIELD_ACTION = {
    "title": "rename",
    "priority": "priority",
    "deadline_date": "priority",
    "deadline_time": "priority",
    "author_user_id": "reassign",
    "assignee_user_id": "reassign",
    "pool_id": "reassign",
}


class ChangeError(ValueError):
    """A metadata change was rejected before a plan could be produced."""

    def __init__(self, code: str, *, action: str | None = None, field: str | None = None):
        self.code = code
        self.action = action
        self.field = field
        message_parts = [code]
        if action is not None:
            message_parts.append(f"action={action}")
        if field is not None:
            message_parts.append(f"field={field}")
        super().__init__(" ".join(message_parts))


@dataclass(frozen=True)
class FieldChange:
    """A single field change in the plan."""

    field: str
    old: object
    new: object


@dataclass(frozen=True)
class ChangePlan:
    """Atomic change plan composed of field changes."""

    changes: tuple[FieldChange, ...]


def plan_changes(
    current: Mapping[str, Any], requested: Mapping[str, Any], *, allowed_actions: Collection[str]
) -> ChangePlan:
    """Compute a change plan from current and requested metadata.

    Args:
        current: Current field values.
        requested: Requested field values (subset of fields).
        allowed_actions: Actions the actor is permitted to perform.

    Returns:
        A ChangePlan with only fields that actually change.

    Raises:
        ChangeError: unknown_field, forbidden, invalid_title, invalid_notes, invalid_priority,
            deadline_date_required, invalid_deadline, or validation errors for reassign.
    """
    if not isinstance(current, Mapping):
        raise ChangeError("current must be a mapping")
    if not isinstance(requested, Mapping):
        raise ChangeError("requested must be a mapping")

    allowed_set = set(allowed_actions)
    changes: list[FieldChange] = []

    # First pass: validate all requested fields and check permissions
    # This also catches deadline_time without deadline_date across the request
    validated: dict[str, Any] = {}
    for field, new_value in requested.items():
        if field != "notes" and field not in FIELD_ACTION:
            raise ChangeError("unknown_field", field=field)

        action = "rename" if field == "notes" else FIELD_ACTION[field]
        if action not in allowed_set:
            raise ChangeError("forbidden", action=action)

        # Validate and normalise per field
        if field == "title":
            validated[field] = _validate_title(new_value)
        elif field == "notes":
            validated[field] = _validate_notes(new_value)
        elif field == "priority":
            validated[field] = _validate_priority(new_value)
        elif field in ("deadline_date", "deadline_time"):
            validated[field] = _validate_deadline_field(field, new_value, current)
        elif field in ("author_user_id", "assignee_user_id", "pool_id"):
            validated[field] = _validate_reassign_field(field, new_value)

    # Check deadline_time requires deadline_date (in request or current)
    if "deadline_time" in validated and validated["deadline_time"] is not None:
        has_date = ("deadline_date" in validated and validated["deadline_date"] is not None) or (
            "deadline_date" in current and current["deadline_date"] is not None
        )
        if not has_date:
            raise ChangeError("deadline_date_required", field="deadline_time")

    # Second pass: build changes, skipping no-ops (type-strict equality)
    for field, new_value in validated.items():
        old_value = current.get(field)
        # Strict no-op check: same type and same value
        if type(new_value) is type(old_value) and new_value == old_value:
            continue
        changes.append(FieldChange(field=field, old=old_value, new=new_value))

    return ChangePlan(changes=tuple(changes))


def to_audit_rows(
    plan: ChangePlan, *, actor_id: int, dictation_id: str, now: datetime
) -> list[dict]:
    """Convert a ChangePlan into audit row dictionaries.

    Args:
        plan: The change plan.
        actor_id: ID of the actor performing the change.
        dictation_id: ID of the dictation being changed.
        now: Timezone-aware UTC datetime for the audit timestamp.

    Returns:
        List of audit row dicts with keys: actor_id, dictation_id, field, at.
        Existing fields also include old and new; notes never include content.

    Raises:
        ValueError: If now is not timezone-aware.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)

    rows = []
    for change in plan.changes:
        rows.append(
            {
                "actor_id": actor_id,
                "dictation_id": dictation_id,
                "field": change.field,
                **({"old": change.old, "new": change.new} if change.field != "notes" else {}),
                "at": now_utc,
            }
        )
    return rows


def _validate_title(value: object) -> str:
    if not isinstance(value, str):
        raise ChangeError("invalid_title", field="title")
    # NFC normalisation
    normalised = unicodedata.normalize("NFC", value)
    # Strip whitespace
    stripped = normalised.strip()
    # Length 1..200
    if len(stripped) < 1 or len(stripped) > 200:
        raise ChangeError("invalid_title", field="title")
    # No control characters (category Cc or Cf)
    for ch in stripped:
        cat = unicodedata.category(ch)
        if cat.startswith("C"):
            raise ChangeError("invalid_title", field="title")
    return stripped


def _validate_notes(value: object) -> str:
    """Accept up to 4000 NFC characters; blank text clears notes only."""
    if not isinstance(value, str):
        raise ChangeError("invalid_notes", field="notes")
    normalised = unicodedata.normalize("NFC", value)
    if len(normalised) > 4000 or any(
        unicodedata.category(ch).startswith("C") and ch not in "\n\r\t"
        for ch in normalised
    ):
        raise ChangeError("invalid_notes", field="notes")
    return normalised.strip()


def _validate_priority(value: object) -> int:
    # Must be int, not bool, exactly 0, 1, or 128
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChangeError("invalid_priority", field="priority")
    if value not in (0, 1, 128):
        raise ChangeError("invalid_priority", field="priority")
    return value


def _validate_deadline_field(
    field: str, value: object, current: Mapping[str, Any]
) -> object:
    """Validate deadline_date or deadline_time.

    Returns the validated value (date, time, or None to clear).
    """
    # Both None clears the deadline
    if value is None:
        return None

    if field == "deadline_date":
        # Must be date, not datetime
        if isinstance(value, datetime):
            raise ChangeError("invalid_deadline", field="deadline_date")
        # Accept date objects (datetime.date)
        from datetime import date

        if not isinstance(value, date):
            raise ChangeError("invalid_deadline", field="deadline_date")
        return value

    # field == "deadline_time"
    from datetime import time

    if not isinstance(value, time):
        raise ChangeError("invalid_deadline", field="deadline_time")

    # deadline_time requires deadline_date (in request or current)
    requested_date = current.get("deadline_date") if "deadline_date" in current else None
    # Check if deadline_date is being set in this request
    # We can't know the full requested dict here, so we check current
    # The caller (plan_changes) processes fields in iteration order, but
    # deadline_date and deadline_time are independent in the request.
    # The requirement: "a deadline_time without a deadline_date (in the request
    # or already current) raises deadline_date_required"
    # Since we can't see the full request here, we'll check in plan_changes
    # after collecting all requested values. For now, just validate the time.
    return value


def _validate_reassign_field(field: str, value: object) -> object:
    if field == "author_user_id":
        if value is None:
            raise ChangeError("invalid_reassign", field="author_user_id")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ChangeError("invalid_reassign", field="author_user_id")
        return value

    if field == "assignee_user_id":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ChangeError("invalid_reassign", field="assignee_user_id")
        return value

    if field == "pool_id":
        if not isinstance(value, str) or value == "":
            raise ChangeError("invalid_reassign", field="pool_id")
        if len(value) > 36:
            raise ChangeError("invalid_reassign", field="pool_id")
        return value

    raise ChangeError("unknown_field", field=field)
