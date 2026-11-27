"""Apply dictation metadata edits and reassignment as one atomic step.

Thin layer over ``metadata_changes`` (plan, validation, audit rows),
``workflow`` (priority codes, deadline resolution), ``pool_policy`` and
``recipient_defaults`` (eligibility) and ``lease`` (current edit holder).

Fields: title, notes, category, recipient (the assignee), priority (0/1/128
or the three German labels), deadline date plus optional time (default
23:59:59 Europe/Zurich), author and pool. Title and notes share the
``rename`` right. The caller passes the actions central policy already
granted (``rename``, ``priority``, ``reassign``). Patient context is never
read from or inferred out of these fields.

Persistence owned here: ``ClinicDictation.version_counter`` (the expected
version), the recording title, ``Recording.notes`` and the workflow row.
Notes live only on that column. Their audit row names the field and does
not copy the text. Category has no column yet, so it is validated and
returned in ``Applied.deferred`` for the consumer that owns that storage.
Audit rows are returned, not stored. A notes write does not append a
transcript revision.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Collection, Mapping

from sqlalchemy import update

from src.clinic import lease, pool_policy, recipient_defaults, workflow
from src.clinic.metadata_changes import (
    ChangeError,
    ChangePlan,
    plan_changes,
    to_audit_rows,
)
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import Recording

_ALIASES = {"recipient_user_id": "assignee_user_id"}
_CATEGORY_ACTION = "rename"


@dataclass(frozen=True)
class Applied:
    """Result of one applied edit."""

    version: int
    plan: ChangePlan
    audit_rows: tuple[dict, ...]
    deferred: Mapping[str, Any]


def apply_metadata(
    *,
    dictation_id: str,
    workflow_id: str,
    actor_user_id: int,
    expected_version: int,
    lease_generation: int,
    requested: Mapping[str, Any],
    allowed_actions: Collection[str],
    now: datetime,
) -> Applied:
    """Validate, check version and edit holder, then write everything or nothing.

    Raises ``ChangeError`` with a stable code: ``not_found``,
    ``forbidden``, ``stale_version``, ``lease_<reason>``,
    ``ineligible_author``, ``ineligible_pool``, ``ineligible_recipient`` or
    any validation code of ``metadata_changes``.
    """
    if type(expected_version) is not int or expected_version < 0:
        raise ChangeError("stale_version")
    dictation = db.session.query(ClinicDictation).filter_by(id=dictation_id).with_for_update().first()
    row = db.session.get(workflow.ClinicWorkflowDictation, workflow_id)
    recording = db.session.get(Recording, dictation.recording_id) if dictation else None
    if dictation is None or row is None or recording is None:
        raise ChangeError("not_found")
    if row.author_user_id != dictation.author_user_id:
        raise ChangeError("not_found")

    asked = {_ALIASES.get(key, key): value for key, value in requested.items()}
    deferred = _take_category(asked, allowed_actions)
    if "priority" in asked:
        asked["priority"] = _priority(asked["priority"])
    current = {
        "title": recording.title,
        "notes": recording.notes,
        "priority": row.priority,
        "deadline_date": row.deadline_date,
        "deadline_time": row.deadline_time,
        "author_user_id": dictation.author_user_id,
        "assignee_user_id": row.assignee_user_id,
        "pool_id": dictation.pool_id,
    }
    plan = plan_changes(current, asked, allowed_actions=allowed_actions)
    if dictation.version_counter != expected_version:
        raise ChangeError("stale_version")
    _check_holder(dictation_id, actor_user_id, lease_generation, now)
    after = {**current, **{c.field: c.new for c in plan.changes}}
    _check_eligible(plan, after)
    deadline = _deadline(plan, after)

    with db.session.begin_nested():
        bumped = db.session.execute(
            update(ClinicDictation)
            .where(
                ClinicDictation.id == dictation_id,
                ClinicDictation.version_counter == expected_version,
            )
            .values(version_counter=expected_version + 1)
        )
        if bumped.rowcount != 1:
            raise ChangeError("stale_version")
        _write(plan, after, deadline, dictation, row, recording)
    db.session.expire(dictation, ["version_counter"])
    audit = tuple(
        to_audit_rows(plan, actor_id=actor_user_id, dictation_id=dictation_id, now=now)
    )
    return Applied(expected_version + 1, plan, audit, deferred)


def _take_category(asked: dict, allowed: Collection[str]) -> dict:
    if "category" not in asked:
        return {}
    value = asked.pop("category")
    if _CATEGORY_ACTION not in set(allowed):
        raise ChangeError("forbidden", action=_CATEGORY_ACTION, field="category")
    if not isinstance(value, str):
        raise ChangeError("invalid_category", field="category")
    text = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(text) <= 200 or any(unicodedata.category(c).startswith("C") for c in text):
        raise ChangeError("invalid_category", field="category")
    return {"category": text}


def _priority(raw):
    if isinstance(raw, str):
        code, flagged = workflow.normalize_priority(raw)
        if flagged:
            raise ChangeError("invalid_priority", field="priority")
        return code
    return raw


def _check_holder(dictation_id, user_id, generation, now) -> None:
    try:
        lease.assert_current(dictation_id, user_id, generation, now)
    except lease.LeaseConflict as conflict:
        raise ChangeError("lease_" + str(conflict.args[0])) from conflict


def _check_eligible(plan: ChangePlan, after: Mapping[str, Any]) -> None:
    fields = {change.field for change in plan.changes}
    pool = after["pool_id"]
    if fields & {"author_user_id", "pool_id"} and pool is not None:
        if pool not in pool_policy.author_pool_ids(after["author_user_id"]):
            raise ChangeError("ineligible_author", field="author_user_id")
    if "assignee_user_id" in fields and after["assignee_user_id"] is not None:
        try:
            eligible = recipient_defaults.eligible_user_ids(pool) if pool else ()
        except recipient_defaults.RecipientDefaultError as error:
            raise ChangeError("ineligible_pool", field="pool_id") from error
        if after["assignee_user_id"] not in eligible:
            raise ChangeError("ineligible_recipient", field="assignee_user_id")


def _deadline(plan: ChangePlan, after: Mapping[str, Any]):
    if not {c.field for c in plan.changes} & {"deadline_date", "deadline_time"}:
        return "keep"
    if after["deadline_date"] is None:
        return None
    return workflow.resolve_deadline(after["deadline_date"], after["deadline_time"])


def _write(plan, after, deadline, dictation, row, recording) -> None:
    fields = {change.field for change in plan.changes}
    if "title" in fields:
        recording.title = after["title"]
    if "notes" in fields:
        recording.notes = after["notes"]
    if "priority" in fields:
        row.priority, row.priority_unrecognized = after["priority"], False
    if deadline != "keep":
        if deadline is None:
            row.deadline_date = row.deadline_time = row.deadline_at = None
        else:
            row.deadline_date = after["deadline_date"]
            row.deadline_time = after["deadline_time"]
            row.deadline_at = deadline
    if "author_user_id" in fields:
        dictation.author_user_id = row.author_user_id = after["author_user_id"]
    if "pool_id" in fields:
        dictation.pool_id = row.pool_id = after["pool_id"]
    if "assignee_user_id" in fields:
        row.assignee_user_id = after["assignee_user_id"]
    db.session.flush()
