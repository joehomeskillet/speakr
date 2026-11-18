"""Atomic change plan for editing dictation metadata.

Computes and validates a change plan for renaming, priority changes,
deadline changes, and reassignment. Each action requires a separate right;
the plan is atomic (all-or-nothing) and produces audit rows. Pure planning
only: the plan is applied by later wiring.

Non-scope: persistence, routes, workflow transitions, ASR, approval,
audio export, or any side effects beyond the plan and audit rows.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Collection, Mapping


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
    """A change was rejected before a plan could be produced."""

    def __init__(self, code: str, *, action: str | None = None, field: str | None = None):
        self.code = code
        self.action = action
        self.field = field
        msg_parts = [code]
        if action is not None:
            msg_parts.append(f"action={action}")
        if field is not None:
            msg_parts.append(f"field={field}")
        super().__init__(" ".join(msg_parts))


@dataclass(frozen=True)
class FieldChange:
    """A single field change in the plan."""

    field: str
    old: object
    new: object


@dataclass(frozen=True)
class ChangePlan:
    """Atomic change plan: all changes succeed or none do."""

    changes: tuple[FieldChange, ...]


def _require_aware_utc(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _validate_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ChangeError("invalid_title", field="title")
    normalised = unicodedata.normalize("NFC", value).strip()
    if not normalised:
        raise ChangeError("invalid_title", field="title")
    if len(normalised) > 200:
        raise ChangeError("invalid_title", field="title")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in normalised):
        raise ChangeError("invalid_title", field="title")
    return normalised


def _validate_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChangeError("invalid_priority", field="priority")
    if value not in (0, 1, 128):
        raise ChangeError("invalid_priority", field="priority")
    return value


def _validate_deadline_date(value: Any, current: Mapping) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ChangeError("invalid_deadline", field="deadline_date")
    if not isinstance(value, date):
        raise ChangeError("invalid_deadline", field="deadline_date")
    return value


def _validate_deadline_time(value: Any, current: Mapping) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChangeError("invalid_deadline", field="deadline_time")
    return value


def _validate_reassign_id(value: Any, field: str, *, allow_none: bool = False) -> int | None:
    if value is None:
        if allow_none:
            return None
        raise ChangeError("invalid_reassign", field=field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ChangeError("invalid_reassign", field=field)
    return value


def _validate_pool_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChangeError("invalid_reassign", field="pool_id")
    if not value:
        raise ChangeError("invalid_reassign", field="pool_id")
    if len(value) > 36:
        raise ChangeError("invalid_reassign", field="pool_id")
    return value


def plan_changes(
    current: Mapping[str, Any],
    requested: Mapping[str, Any],
    *,
    allowed_actions: Collection[str],
) -> ChangePlan:
    """Compute an atomic change plan from current state and requested changes.

    Args:
        current: Current field values (dict-like).
        requested: Requested new values (dict-like). Only keys in FIELD_ACTION are considered.
        allowed_actions: Set of action names the caller is allowed to perform.

    Returns:
        ChangePlan with a tuple of FieldChange (may be empty).

    Raises:
        ChangeError: unknown_field, forbidden, invalid_title, invalid_priority,
            invalid_deadline, invalid_reassign, deadline_date_required.
    """
    if not isinstance(current, Mapping) or not isinstance(requested, Mapping):
        raise TypeError("current and requested must be mappings")

    changes: list[FieldChange] = []
    required_actions: set[str] = set()

    for field, new_value in requested.items():
        if field not in FIELD_ACTION:
            raise ChangeError("unknown_field", field=field)

        action = FIELD_ACTION[field]
        required_actions.add(action)

        old_value = current.get(field)

        if field == "title":
            validated = _validate_title(new_value)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

        elif field == "priority":
            validated = _validate_priority(new_value)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

        elif field == "deadline_date":
            validated = _validate_deadline_date(new_value, current)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

        elif field == "deadline_time":
            validated = _validate_deadline_time(new_value, current)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

        elif field == "author_user_id":
            validated = _validate_reassign_id(new_value, field, allow_none=False)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

        elif field == "assignee_user_id":
            validated = _validate_reassign_id(new_value, field, allow_none=True)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

        elif field == "pool_id":
            validated = _validate_pool_id(new_value)
            if validated != old_value:
                changes.append(FieldChange(field=field, old=old_value, new=validated))

    # Check deadline_time requires deadline_date (in request or current)
    if "deadline_time" in requested:
        requested_time = requested["deadline_time"]
        requested_date = requested.get("deadline_date")
        current_date = current.get("deadline_date")
        if requested_time is not None and requested_date is None and current_date is None:
            raise ChangeError("deadline_date_required", field="deadline_time")

    # Atomic right check
    for action in required_actions:
        if action not in allowed_actions:
            raise ChangeError("forbidden", action=action)

    return ChangePlan(changes=tuple(changes))


def to_audit_rows(
    plan: ChangePlan,
    *,
    actor_id: int,
    dictation_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Convert a change plan to audit row dictionaries.

    Args:
        plan: The change plan.
        actor_id: ID of the actor performing the change.
        dictation_id: Dictation identifier.
        now: Timezone-aware UTC datetime for the audit timestamp.

    Returns:
        List of audit row dicts with keys: actor_id, dictation_id, field, old, new, at.

    Raises:
        ValueError: If now is not timezone-aware.
    """
    now_utc = _require_aware_utc(now)

    rows = []
    for change in plan.changes:
        rows.append(
            {
                "actor_id": actor_id,
                "dictation_id": dictation_id,
                "field": change.field,
                "old": change.old,
                "new": change.new,
                "at": now_utc,
            }
        )
    return rows