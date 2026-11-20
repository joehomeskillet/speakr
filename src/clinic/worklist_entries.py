"""Worklist entry loading and the contract Dictation snapshot.

``load_entries`` narrows which dictations an actor may see before the worklist
applies its live view policy:

Each entry also carries the additive role fields `dictated_by`, `staff_pools` and
`lease_holder` from `dictation_roles.project_roles` (staff data only, no patient data).

* ordinary users: dictations they authored or that are assigned to them;
* typist-pool leads (W13): additionally the sent, unassigned dictations (Klärliste)
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

import json
from datetime import timezone

from flask import after_this_request, has_request_context, request
from sqlalchemy import and_, or_

from src.clinic import recognition_state, release, revisions, routes_history, routes_worklist, workflow
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
_DEFAULT_SOURCE = ("audio", None)


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


def _sources(dictation_ids: list[str]) -> dict[str, tuple[str, str | None]]:
    """Worklist source kind and origin label from each dictation's first original."""
    earliest: dict[str, str] = {}
    for start in range(0, len(dictation_ids), 500):
        part = dictation_ids[start:start + 500]
        originals = (
            db.session.query(ClinicOriginal.dictation_id, ClinicOriginal.receipt)
            .filter(ClinicOriginal.dictation_id.in_(part))
            .order_by(ClinicOriginal.created_at, ClinicOriginal.id)
        )
        for dictation_id, receipt in originals:
            earliest.setdefault(dictation_id, receipt)
    sources = {}
    for dictation_id, receipt in earliest.items():
        try:
            data = json.loads(receipt)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        kind = "document" if data.get("kind") == "document" else "audio"
        origin = data.get("origin")
        label = routes_worklist.ORIGIN_LABELS.get(origin) if isinstance(origin, str) else None
        sources[dictation_id] = kind, label
    return sources


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
            conditions.append(and_(item.assignee_user_id.is_(None), item.status != workflow.DRAFT,
                                   ClinicDictation.pool_id.in_(led)))
        query = query.filter(or_(*conditions))
    from src.clinic.dictation_roles import project_roles  # lazy: dictation_roles imports stamp from here

    found = list(query)
    roles = project_roles(dictation for dictation, _row, _rec in found)
    sources = _sources([dictation.id for dictation, _row, _rec in found])
    outcomes = recognition_state.asr_outcomes(d for d, _, _ in found)
    entries = []
    for dictation, row, rec in found:
        queue_status = row.resume_status if row.status == workflow.IN_PROGRESS else row.status
        source_kind, source_label = sources.get(dictation.id, _DEFAULT_SOURCE)
        entries.append(routes_worklist.WorklistEntry(rec, dict(
            dictation_id=dictation.id, title=rec.title or "", status=row.status,
            author_user_id=dictation.author_user_id, priority=row.priority,
            deadline_at=stamp(row.deadline_at), assignee_user_id=row.assignee_user_id,
            assigned_at=None, notes=rec.notes or "", created_at=stamp(row.created_at),
            duration_seconds=row.audio_seconds, recipient=None, source_kind=source_kind,
            open_mode=_open_mode(actor, dictation, queue_status), revision=dictation.version_counter,
            source_label=source_label, asr_state=_asr_state(dictation, queue_status, outcomes),
            **roles[dictation.id]), dictation.pool_id))
    if admin:
        foreign = {d.id: (rec.id, rec.user_id) for d, _, rec in found
                   if actor not in (d.author_user_id, rec.user_id)}
        if foreign:
            _audit_foreign_reads(actor, foreign)
    return entries


def _audit_foreign_reads(actor: int, foreign: dict[str, tuple[int, int]]) -> None:
    """An HTTP read covers only the rows returned after policy, filters and paging."""
    def record(recording_id, owner_id):
        audit.record("recording.content.read", recording_id=recording_id,
                     recording_owner_id=owner_id, details={"part": "worklist"},
                     actor_kind="session", actor_user_id=actor)

    if not has_request_context():
        # The direct loader returns the active administrator's complete list.
        for recording_id, owner_id in foreign.values():
            record(recording_id, owner_id)
        return

    @after_this_request
    def audit_page(response):
        if request.method == "GET" and response.status_code == 200 and response.is_json:
            for row in response.get_json()["items"]:
                rec = foreign.get(row["dictation_id"])
                if rec is not None:
                    record(*rec)
        return response


def _single_head(model, dictation_id: str):
    heads = release._heads(model, dictation_id)
    return db.session.get(model, next(iter(heads))) if len(heads) == 1 else None


def _asr_state(dictation, status: str, outcomes=None) -> str:
    outcome = recognition_state.asr_outcome(dictation, outcomes=outcomes, detailed=True)
    return {'completed': 'SUCCEEDED', 'processing': 'RUNNING', 'queued': 'PENDING',
            'failed': 'FAILED'}.get(outcome, 'SKIPPED' if status == workflow.TO_WRITE else 'PENDING')


def case_displays(dictation_ids):
    """Unique context heads without loading full snapshots for each list row."""
    result = {}
    for start in range(0, len(dictation_ids), 500):
        rows = db.session.query(ClinicContextRevision).filter(
            ClinicContextRevision.dictation_id.in_(dictation_ids[start:start + 500])).all()
        parents = {r.parent_revision_id for r in rows if r.parent_revision_id}
        heads = {}
        for row in rows:
            if row.id not in parents:
                heads.setdefault(row.dictation_id, []).append(row)
        result.update({did: values[0].case_ref if len(values) == 1 else None
                       for did, values in heads.items()})
    return result


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
    (WITHDRAWN, COMPLETION_UNKNOWN) are never reported.
    """
    flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id)
    status = flow.status if flow is not None else workflow.DRAFT
    queue_status = flow.resume_status if status == workflow.IN_PROGRESS else status
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
        "asr_state": _asr_state(dictation, queue_status),
        "review_state": _review_state(status, head),
        "current_original_version_id": None if original is None else original[0],
        "current_transcript_revision_id": None if head is None else head.id,
        "current_context_revision_id": None if context is None else context.id,
        "active_release_id": None if active is None else active.id,
        "created_at": stamp(dictation.created_at),
        "etag": routes_history.dictation_etag(dictation),
    }
