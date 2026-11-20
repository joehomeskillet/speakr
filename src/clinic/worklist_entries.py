"""Worklist entry loading and the contract Dictation snapshot.

``load_entries`` narrows which dictations an actor may see before the worklist
applies its live view policy:

Each entry also carries the additive role fields `dictated_by`, `staff_pools` and
`lease_holder` from `dictation_roles.project_roles` (staff data only, no patient data).

* ordinary users: dictations they authored or that are assigned to them;
* typist-pool leads (W13): additionally the unassigned dictations (Klärliste)
  of the author pools routed to a staff pool they lead, i.e. the group
  membership role ``admin`` of that pool's group;
* active site administrators (W12): every dictation. Foreign recordings read
  this way are audit-logged like ``admin_dictation_access`` content opens.
  A revoked administrator gets no extra access.

``dictation_snapshot`` serialises the ``Dictation`` schema of
``clinic-spec/contracts/clinic-api.openapi.json``. It carries state and
identifiers only, never patient identifiers, audio or transcript text.
"""

from __future__ import annotations

from datetime import timezone

from sqlalchemy import and_, or_

from src.clinic import release, revisions, routes_history, routes_worklist, workflow
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.models import (
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
)
from src.clinic.pools import ClinicPoolRoute, ClinicStaffPool
from src.database import db
from src.models import GroupMembership, Recording, User
from src.services import audit

LEAD_ROLE = "admin"  # GroupMembership.role of a typist-pool lead


def stamp(value):
    """ISO 8601 UTC with a ``Z`` suffix, or None."""
    if value is None:
        return None
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _led_author_pools(actor: int) -> list[str]:
    """Author pool ids routed to a staff pool whose group the actor leads."""
    rows = (
        db.session.query(ClinicPoolRoute.author_pool_id)
        .join(ClinicStaffPool, ClinicStaffPool.id == ClinicPoolRoute.staff_pool_id)
        .join(GroupMembership, GroupMembership.group_id == ClinicStaffPool.group_id)
        .filter(GroupMembership.user_id == actor, GroupMembership.role == LEAD_ROLE)
    )
    return [pool_id for (pool_id,) in rows]


def _open_mode(actor: int, dictation: ClinicDictation, status: str) -> str:
    """Per-actor open mode: author opens the case, others the player (to-write) or correction."""
    if actor == dictation.author_user_id:
        return "author_case"
    return "player" if status == workflow.TO_WRITE else "correction"


def load_entries(actor: int) -> list[routes_worklist.WorklistEntry]:
    """Entries the actor may see (W12/W13); the live view policy still decides every row."""
    item = workflow.ClinicWorkflowDictation
    query = (
        db.session.query(ClinicDictation, item, Recording)
        .join(item, item.id == ClinicDictation.id)
        .join(Recording, Recording.id == ClinicDictation.recording_id)
    )
    admin = _is_active_site_admin(db.session, db.session.get(User, actor))
    if not admin:
        conditions = [ClinicDictation.author_user_id == actor, item.assignee_user_id == actor]
        led = _led_author_pools(actor)
        if led:
            conditions.append(and_(item.assignee_user_id.is_(None), ClinicDictation.pool_id.in_(led)))
        query = query.filter(or_(*conditions))
    from src.clinic.dictation_roles import project_roles  # lazy: dictation_roles imports stamp from here

    found = list(query)
    roles = project_roles(dictation for dictation, _row, _rec in found)
    entries = []
    for dictation, row, rec in found:
        if admin and actor not in (dictation.author_user_id, rec.user_id):
            audit.record("recording.content.read", recording=rec, details={"part": "worklist"},
                         actor_kind="session", actor_user_id=actor)
        entries.append(routes_worklist.WorklistEntry(rec, dict(
            dictation_id=dictation.id, title=rec.title or "", status=row.status,
            author_user_id=dictation.author_user_id, priority=row.priority,
            deadline_at=stamp(row.deadline_at), assignee_user_id=row.assignee_user_id,
            assigned_at=None, notes=rec.notes or "", created_at=stamp(row.created_at),
            duration_seconds=row.audio_seconds, recipient=None, source_kind="audio",
            open_mode=_open_mode(actor, dictation, row.status), revision=dictation.version_counter,
            **roles[dictation.id]), dictation.pool_id))
    return entries


def _single_head(model, dictation_id: str):
    heads = release._heads(model, dictation_id)
    return db.session.get(model, next(iter(heads))) if len(heads) == 1 else None


def _asr_state(status: str, transcripts: list) -> str:
    if any(row.kind == revisions.KIND_ASR for row in transcripts):
        return "SUCCEEDED"
    if status == workflow.TO_WRITE:
        return "SKIPPED"
    return "PENDING"


def _review_state(status: str, head) -> str:
    if status == workflow.TO_ACCEPT:
        return "APPROVAL_REQUIRED"
    if status in (workflow.ACCEPTED, workflow.DONE):
        return "APPROVED"
    if status == workflow.IN_PROGRESS:
        return "IN_REVIEW"
    if head is not None and head.kind == revisions.KIND_HUMAN:
        return "CORRECTED"
    return "QUEUED"


def dictation_snapshot(dictation: ClinicDictation) -> dict:
    """The contract ``Dictation`` object (all 14 required fields) for one dictation.

    ``ingest_state`` is always STORED because a dictation row exists only after
    its original was stored. States the data model cannot distinguish yet
    (RUNNING/FAILED ASR, WITHDRAWN, COMPLETION_UNKNOWN) are never reported.
    """
    flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id)
    status = flow.status if flow is not None else workflow.DRAFT
    head = revisions.current_revision(dictation)
    context = _single_head(ClinicContextRevision, dictation.id)
    original = (
        db.session.query(ClinicOriginal.id)
        .filter(ClinicOriginal.dictation_id == dictation.id)
        .order_by(ClinicOriginal.created_at.desc(), ClinicOriginal.id)
        .first()
    )
    active = release.active_release(dictation)
    verified = context is not None and bool(context.case_ref)
    return {
        "dictation_id": dictation.id,
        "author_user_id": str(dictation.author_user_id),
        "pool_id": dictation.pool_id,
        "ingest_state": "STORED",
        "submission_state": "DRAFT" if status == workflow.DRAFT else "SUBMITTED",
        "context_state": "VERIFIED" if verified else "CASE_UNRESOLVED",
        "case_display": context.case_ref if verified else None,
        "asr_state": _asr_state(status, revisions.history(dictation)),
        "review_state": _review_state(status, head),
        "current_original_version_id": None if original is None else original[0],
        "current_transcript_revision_id": None if head is None else head.id,
        "current_context_revision_id": None if context is None else context.id,
        "active_release_id": None if active is None else active.id,
        "created_at": stamp(dictation.created_at),
        "etag": routes_history.dictation_etag(dictation),
    }
