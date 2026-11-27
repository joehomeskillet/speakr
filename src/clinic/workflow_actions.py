"""Guarded, transaction-local submission, rejection and reopening."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from sqlalchemy.engine import CursorResult

from src.clinic import acceptance_rules, release, revisions, workflow
from src.clinic.acceptance_policy import AcceptancePolicyVersion
from src.clinic.config import clinic_mode_enabled
from src.clinic.lease import assert_current
from src.clinic.live_session import assert_edit_allowed
from src.clinic.models import ClinicDictation
from src.clinic.pool_policy import author_pool_ids
from src.clinic.richtext import ClinicRichTextSnapshot
from src.database import db
from src.services.audit import record_in_tx

_COMMANDS = {
    "submit": ("clinic.review", "complete"),
    "accept": ("clinic.accept", "accept"),
    "reject": ("clinic.accept", "reject"),
    "reopen": ("clinic.reopen", "reopen"),
}


@dataclass(frozen=True)
class ActionResult:
    status: str
    version: int
    text_revision_id: str | None
    rejection: acceptance_rules.AcceptanceDecision | None
    acceptance: acceptance_rules.AcceptanceDecision | None = None


def apply_action(
    dictation_id: str, action: str, *, actor_user_id: int, expected_version: int,
    lease_generation: int, now: datetime,
    authorize: Callable[[int, str, ClinicDictation], bool],
    text_revision_id: str | None = None, reason: str | None = None,
    acceptance_policy: Callable[[ClinicDictation], AcceptancePolicyVersion] | None = None,
) -> ActionResult:
    """Apply one action; the caller owns the outer commit and replay receipt.

    The server supplies current authorization for the exact capability and
    target, and resolves the effective acceptance policy for rejection. The
    workflow row and the clinical dictation share the same ID. Callers can
    persist the returned rejection and its private reason in the same outer
    transaction; free text never goes into the access audit.
    """
    if not clinic_mode_enabled():
        raise workflow.WorkflowError("clinic_disabled")
    if (not isinstance(dictation_id, str) or not dictation_id
            or not isinstance(action, str) or action not in _COMMANDS
            or type(actor_user_id) is not int or actor_user_id < 1
            or type(lease_generation) is not int or lease_generation < 1
            or not isinstance(now, datetime) or now.utcoffset() is None):
        raise workflow.WorkflowError("invalid_action_input")
    if type(expected_version) is not int or expected_version < 0:
        raise workflow.WorkflowError("stale_version")
    if action in ("reject", "reopen") and (
        not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
    ):
        raise workflow.WorkflowError("reason_required")

    permission, trigger = _COMMANDS[action]
    session = db.session()
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        # Start the outer write transaction before the savepoint in SQLite legacy mode.
        driver = connection.connection.driver_connection
        if driver is not None and not driver.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    with session.begin_nested():
        dictation = session.get(ClinicDictation, dictation_id, with_for_update=True, populate_existing=True)
        if dictation is None or authorize(actor_user_id, permission, dictation) is not True:
            raise workflow.WorkflowError("forbidden")
        if dictation.version_counter != expected_version:
            raise workflow.WorkflowError("stale_version")
        assert_current(dictation_id, actor_user_id, lease_generation, now)
        assert_edit_allowed(dictation_id, actor_user_id, now)
        item = session.get(workflow.ClinicWorkflowDictation, dictation_id,
                           with_for_update=True, populate_existing=True)
        if item is None or item.author_user_id != dictation.author_user_id:
            raise workflow.WorkflowError("not_found")

        head = revisions.current_revision(dictation)
        rejection = acceptance = None
        if action != "reopen" and (head is None or head.id != text_revision_id):
            raise workflow.WorkflowError("stale_revision")
        if action == "submit" and (head is None or head.kind != revisions.KIND_HUMAN):
            raise workflow.WorkflowError("human_review_required")
        if action == "reject":
            if acceptance_policy is None or head is None:
                raise workflow.WorkflowError("forbidden")
            rejection = acceptance_rules.decide(
                acceptance_policy(dictation).as_rule(), kind="reject", actor_id=actor_user_id,
                actor_pool_ids=author_pool_ids(actor_user_id), revision_id=head.id,
                reason=reason, previous_assignee_id=item.assignee_user_id, now=now,
            )

        if action == "accept":
            # Acceptance only moves the row to ACCEPTED; completion stays with
            # workflow_finalize, which also refuses while review flags are open.
            if acceptance_policy is None or head is None:
                raise workflow.WorkflowError("forbidden")
            try:
                acceptance = acceptance_rules.decide(
                    acceptance_policy(dictation).as_rule(), kind="accept", actor_id=actor_user_id,
                    actor_pool_ids=author_pool_ids(actor_user_id), revision_id=head.id,
                    reason=None, previous_assignee_id=item.assignee_user_id, now=now,
                )
            except acceptance_rules.AcceptanceError as error:
                raise workflow.WorkflowError("forbidden") from error

        advance = db.update(ClinicDictation).where(
            ClinicDictation.id == dictation_id, ClinicDictation.version_counter == expected_version,
        ).values(version_counter=ClinicDictation.version_counter + 1)
        if cast(CursorResult, session.execute(advance)).rowcount != 1:
            raise workflow.WorkflowError("stale_version")
        item = workflow.transition(session, dictation_id, trigger)
        if action == "submit" and item.status != workflow.TO_ACCEPT:
            raise workflow.WorkflowError("submission_requires_acceptance")
        if action == "reopen":
            release.invalidate_on_change(dictation, now)
            # Reopening also invalidates releases that still match the heads.
            while (former := release.active_release(dictation)) is not None:
                session.add(release.ClinicReleaseSupersession(
                    release_id=former.id, reason="reopened", superseded_at=now,
                ))
                session.flush()
            if head is not None:
                snapshot = session.scalar(db.select(ClinicRichTextSnapshot)
                                          .filter_by(revision_id=head.id)
                                          .order_by(ClinicRichTextSnapshot.version.desc()).limit(1))
                head = revisions.append_working_revision(dictation, head, actor_user_id=actor_user_id)
                if snapshot is not None:
                    session.add(ClinicRichTextSnapshot(
                        revision_id=head.id, version=1, payload=snapshot.payload,
                    ))

        if record_in_tx(
            "recording.edited", actor_user_id=actor_user_id, recording_id=dictation.recording_id,
            recording_owner_id=dictation.author_user_id, force=True,
            target_user_id=rejection.handler_id if rejection else None,
            created_at=now.astimezone(timezone.utc).replace(tzinfo=None),
            details={"fields": ["workflow_status", f"workflow_{action}"]},
        ) is None:
            raise workflow.WorkflowError("audit_unavailable")
        return ActionResult(item.status, expected_version + 1, head.id if head else None, rejection, acceptance)
