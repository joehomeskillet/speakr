"""Keep the notice intent in the command receipt and enqueue it after commit."""

from contextvars import ContextVar

from sqlalchemy.exc import SQLAlchemyError

from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.pools import ClinicAuthorPoolMember
from src.database import db
from src.services.job_queue import ClinicJobError, job_queue


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
        job_queue.enqueue(**receipt["_workflow_notice"])
    except (ClinicJobError, SQLAlchemyError) as error:
        raise CommandProblem("DEPENDENCY_UNAVAILABLE") from error
    return receipt["result"]
