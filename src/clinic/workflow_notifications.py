"""Emit in-app workflow notifications for durable acceptance/completion transitions.

``emit`` must be called after the workflow transition has been committed (the
in-app store commits the session itself). It reads
the durable row, refuses an event that the stored status does not support
(rollback or stale call), rechecks every recipient against the current routing
(author, assignee) and the caller's current visibility check, builds the fixed
German text with ``notification_rules`` (no transcript or patient data) and
stores one row per recipient through ``services.notifications``.

The event key is ``clinic.wf:<event>:<dictation>:<transition_id>``. The caller
passes a durable per-transition id (for example the audit event id of the
accept or reject); ``version_counter`` is only a staleness check because it
changes on finalize alone, so two accept/reject cycles share it. A repeated or
retried call for the same transition finds the stored row per recipient and skips it, so there are no
duplicates, a dismissed notice is not revived, and a recipient whose delivery
failed is the only one retried. The local in-app path needs no SMTP.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from src.clinic import notification_rules, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import Notification
from src.services import notifications

KIND = "clinic.workflow"
KEY_PREFIX = "clinic.wf"

# Event -> workflow statuses in which the durable transition has happened.
_DURABLE_STATUS = {
    "acceptance_requested": (workflow.TO_ACCEPT,),
    "acceptance_rejected": (workflow.TO_CORRECT,),
    "accepted": (workflow.ACCEPTED, workflow.DONE),
    "completed": (workflow.DONE,),
}


@dataclass(frozen=True)
class EmitResult:
    event_key: str
    created: tuple[int, ...]
    existing: tuple[int, ...]
    dropped: tuple[int, ...]  # no longer allowed to see or be routed this dictation
    failed: tuple[int, ...]  # storing failed; a retry delivers only these


MAX_TRANSITION_ID = 64


def event_key(event: str, dictation_id: str, transition_id: str) -> str:
    return f"{KEY_PREFIX}:{event}:{dictation_id}:{transition_id}"


def emit(
    event: str,
    dictation_id: str,
    *,
    actor_user_id: int,
    expected_version: int,
    transition_id: str,
    acceptor_ids: Collection[int],
    can_see: Callable[[int, workflow.ClinicWorkflowDictation], bool],
    context: Mapping | None = None,
) -> EmitResult:
    """Notify the current recipients once. Raises ``WorkflowError`` with a stable code."""
    if not clinic_mode_enabled():
        raise workflow.WorkflowError("clinic_disabled")
    if event not in notification_rules.EVENTS:
        raise workflow.WorkflowError("unknown_event")
    if (not isinstance(dictation_id, str) or not dictation_id
            or type(actor_user_id) is not int or actor_user_id < 1):
        raise workflow.WorkflowError("invalid_action_input")
    if (not isinstance(transition_id, str) or not transition_id
            or len(transition_id) > MAX_TRANSITION_ID):
        raise workflow.WorkflowError("invalid_action_input")
    if type(expected_version) is not int or expected_version < 0:
        raise workflow.WorkflowError("stale_version")

    session = db.session()
    dictation = session.get(ClinicDictation, dictation_id, populate_existing=True)
    item = session.get(workflow.ClinicWorkflowDictation, dictation_id, populate_existing=True)
    if dictation is None or item is None or item.author_user_id != dictation.author_user_id:
        raise workflow.WorkflowError("not_found")
    if dictation.version_counter != expected_version:
        raise workflow.WorkflowError("stale_version")
    if item.status not in _DURABLE_STATUS[event]:
        raise workflow.WorkflowError("transition_not_durable")

    try:
        wanted = notification_rules.recipients_for(
            event, author_id=item.author_user_id, assignee_id=item.assignee_user_id,
            acceptor_ids=acceptor_ids, actor_id=actor_user_id)
        recipients = [uid for uid in wanted if can_see(uid, item) is True]
        dropped = tuple(uid for uid in wanted if uid not in recipients)
        note = notification_rules.build_notification(
            event, dictation_ref=dictation_id, recipient_ids=recipients, context=context)
    except notification_rules.NotificationError as error:
        raise workflow.WorkflowError(error.code) from error

    key = event_key(event, dictation_id, transition_id)
    created: list[int] = []
    existing: list[int] = []
    failed: list[int] = []
    for uid in note.recipient_ids:
        if Notification.query.filter_by(user_id=uid, dedupe_key=key).first() is not None:
            existing.append(uid)
            continue
        notifications.notify(
            KIND, f"{KIND}.{event}", user_ids=[uid], params={"title": note.title, "body": note.body},
            link=note.url_path, dedupe_key=key)
        stored = Notification.query.filter_by(user_id=uid, dedupe_key=key).first() is not None
        (created if stored else failed).append(uid)
    return EmitResult(key, tuple(created), tuple(existing), dropped, tuple(failed))
