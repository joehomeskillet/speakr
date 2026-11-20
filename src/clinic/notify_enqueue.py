"""Keep the notice intent in the command receipt and enqueue it after commit."""

import logging
from contextvars import ContextVar

from sqlalchemy import exists, select

from sqlalchemy.exc import SQLAlchemyError

from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.parity_receipts import ClinicNoticeDispatch, ClinicParityReceipt, scope_key
from src.clinic.pools import ClinicAuthorPoolMember
from src.clinic.workflow_notifications import KEY_PREFIX
from src.database import db
from src.models import Notification, ProcessingJob, Recording
from src.services.job_queue import ClinicJobError, job_queue


logger = logging.getLogger(__name__)

_intent = ContextVar("clinic_workflow_notice", default=None)


def acceptors(dictation_id):
    versions = AcceptanceLedger().versions(SCOPE_DICTATION, dictation_id)
    if not versions:
        return []
    policy = versions[-1]
    users = set(policy.approver_user_ids)
    if policy.approver_pool_ids:
        members = db.session.query(ClinicAuthorPoolMember.user_id).filter(
            ClinicAuthorPoolMember.pool_id.in_(policy.approver_pool_ids))
        users.update(uid for (uid,) in members)
    return sorted(users - policy.deny_user_ids)


def record_notice(event, dictation, actor, version, transition_id):
    """Bind the job to the exact transition inside its receipt transaction."""
    intent = _intent.get()
    if intent is None:
        return
    params = dict(event=event, dictation_id=dictation.id, actor_user_id=actor,
                  expected_version=version, transition_id=transition_id,
                  clinic_operation_key=transition_id)
    if event == "acceptance_requested":
        params["acceptor_ids"] = acceptors(dictation.id)
    intent.update(user_id=dictation.author_user_id, recording_id=dictation.recording_id,
                  job_type="clinic_notify", params=params)


def run_once_with_notice(commit_once, scope, digest, operation):
    """An exact retry can recover a crash between receipt commit and job enqueue."""
    def capture():
        intent = {}
        token = _intent.set(intent)
        try:
            result = operation()
        finally:
            _intent.reset(token)
        return {"_workflow_notice": intent, "result": result} if intent else result

    receipt = commit_once(scope, digest, capture)
    if "_workflow_notice" not in receipt:
        return receipt
    try:
        _hand_off(job_queue, scope_key(scope), receipt["_workflow_notice"])
    except (ClinicJobError, SQLAlchemyError) as error:
        db.session.rollback()
        raise CommandProblem("DEPENDENCY_UNAVAILABLE") from error
    return receipt["result"]


def _hand_off(queue, key, intent):
    marker = db.session.get(ClinicNoticeDispatch, key)
    if marker.handed_off:
        return
    # A deleted recording has no delivery target. Its receipt is still historical evidence.
    if db.session.get(Recording, intent["recording_id"]) is not None:
        queue.enqueue(**intent)
    marker.handed_off = True
    db.session.commit()


def recover_pending_notices(queue):
    """Drain the indexed handoff markers using the elected owner's existing recovery loop."""
    if not queue._is_owner:
        return
    with queue._app_context():
        pending = db.session.query(ClinicNoticeDispatch.scope_key, ClinicParityReceipt.result).join(
            ClinicParityReceipt).filter(ClinicNoticeDispatch.handed_off.is_(False))
        last = None
        while True:
            batch = pending if last is None else pending.filter(ClinicNoticeDispatch.scope_key > last)
            rows = batch.order_by(ClinicNoticeDispatch.scope_key).limit(50).all()
            if not rows:
                return
            for last, receipt in rows:
                try:
                    _hand_off(queue, last, receipt["_workflow_notice"])
                except (ClinicJobError, SQLAlchemyError):
                    db.session.rollback()
                    logger.exception("Failed to recover pending clinic notification")


def backfill_notice_markers(engine):
    """One-time upgrade for intents saved before handoff tracking existed."""
    receipt = ClinicParityReceipt.__table__
    marker = ClinicNoticeDispatch.__table__
    job = ProcessingJob.__table__
    intent = receipt.c.result["_workflow_notice"]
    transition = intent["params"]["transition_id"].as_string()
    recording = intent["recording_id"].as_integer()
    existing_job = exists(select(job.c.id).where(
        job.c.recording_id == recording, job.c.notify_transition_id == transition))
    params = intent["params"]
    notice_key = (KEY_PREFIX + ":" + params["event"].as_string() + ":"
                  + params["dictation_id"].as_string() + ":" + transition)
    delivered = exists(select(Notification.id).where(Notification.dedupe_key == notice_key))
    missing_marker = ~exists(select(marker.c.scope_key).where(marker.c.scope_key == receipt.c.scope_key))
    with engine.begin() as conn:
        conn.execute(marker.insert().from_select(
            ["scope_key", "handed_off"], select(receipt.c.scope_key, existing_job | delivered)
            .where(transition.is_not(None), missing_marker)))
