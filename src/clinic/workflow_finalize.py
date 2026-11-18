"""Final clinical release and downstream scheduling as one retry-safe operation.

``finalize`` binds the exact audio, transcript and context revisions, the text
hash and the document type with ``release.create_release``, completes the
workflow row and commits. Only after that durable commit is the existing
``ProcessingJob`` work enqueued through ``JobQueue``. A repeated call with the
same binding returns the stored release and the one logical job; if the first
call died between commit and enqueue, the retry enqueues the missing job.

Gates, all fail closed: current human correction at the transcript head,
configured acceptance (``acceptance_rules.final_allowed``), a trusted context
revision, no open review marker (Vorbehaltswortliste, W11), current lease and
edit lock, and the caller's current authorisation. The ASR job state is never
consulted, so manual transcription completes after a failed or absent ASR run.
"""

from __future__ import annotations

import json
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
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.database import db
from src.services.audit import record_in_tx

PERMISSION = "clinic.release"
RELEASE_TYPE = "clinical_final"
PARAM_RELEASE = "clinic_release_id"


@dataclass(frozen=True)
class FinalizeResult:
    release_id: str
    job_id: int
    version: int
    replayed: bool


def _bound(row, model, dictation_id: str):
    found = db.session.get(model, row) if isinstance(row, str) and row else None
    if found is None or found.dictation_id != dictation_id:
        raise workflow.WorkflowError("stale_revision")
    return found


def _stored(dictation_id, audio_id, text_id, context_id, text_hash, actor, doc_type):
    return db.session.query(ClinicRelease).filter_by(
        dictation_id=dictation_id, audio_revision_id=audio_id,
        transcript_revision_id=text_id, context_revision_id=context_id,
        text_hash=text_hash, actor_user_id=actor, document_type=doc_type,
        release_type=RELEASE_TYPE,
    ).first()


def _job_for(recording_id: int, job_type: str, release_id: str):
    from src.models import ProcessingJob

    rows = db.session.query(ProcessingJob).filter_by(
        recording_id=recording_id, job_type=job_type).order_by(ProcessingJob.id).all()
    for row in rows:
        try:
            params = json.loads(row.params) if row.params else {}
        except ValueError:
            continue
        if isinstance(params, dict) and params.get(PARAM_RELEASE) == release_id:
            return row.id
    return None


def finalize(
    dictation_id: str, *, actor_user_id: int, expected_version: int, lease_generation: int,
    now: datetime, authorize: Callable[[int, str, ClinicDictation], bool],
    audio_revision_id: str, transcript_revision_id: str, context_revision_id: str,
    document_type: str, job_type: str,
    trusted_context: Callable[[ClinicContextRevision], bool],
    open_review_flags: Callable[[ClinicDictation, ClinicTranscriptRevision], int],
    acceptance_policy: Callable[[ClinicDictation], AcceptancePolicyVersion],
    acceptance_decision: acceptance_rules.AcceptanceDecision | None = None,
) -> FinalizeResult:
    """Release and schedule once. Raises ``WorkflowError`` with a stable code."""
    if not clinic_mode_enabled():
        raise workflow.WorkflowError("clinic_disabled")
    if (not isinstance(dictation_id, str) or not dictation_id
            or type(actor_user_id) is not int or actor_user_id < 1
            or type(lease_generation) is not int or lease_generation < 1
            or not isinstance(now, datetime) or now.utcoffset() is None
            or not isinstance(document_type, str) or not document_type.strip()
            or not isinstance(job_type, str) or not job_type):
        raise workflow.WorkflowError("invalid_action_input")
    if type(expected_version) is not int or expected_version < 0:
        raise workflow.WorkflowError("stale_version")

    session = db.session()
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if driver is not None and not driver.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    with session.begin_nested():
        dictation = session.get(ClinicDictation, dictation_id, with_for_update=True,
                                populate_existing=True)
        if dictation is None or authorize(actor_user_id, PERMISSION, dictation) is not True:
            raise workflow.WorkflowError("forbidden")
        item = session.get(workflow.ClinicWorkflowDictation, dictation_id,
                           with_for_update=True, populate_existing=True)
        if item is None or item.author_user_id != dictation.author_user_id:
            raise workflow.WorkflowError("not_found")
        audio = _bound(audio_revision_id, ClinicAudioRevision, dictation_id)
        text = _bound(transcript_revision_id, ClinicTranscriptRevision, dictation_id)
        context = _bound(context_revision_id, ClinicContextRevision, dictation_id)

        existing = _stored(dictation_id, audio.id, text.id, context.id,
                           text.normalised_text_hash, actor_user_id, document_type)
        replay = existing is not None and item.status == workflow.DONE
        if replay:
            stored_id, version = existing.id, dictation.version_counter
        else:
            if dictation.version_counter != expected_version:
                raise workflow.WorkflowError("stale_version")
            assert_current(dictation_id, actor_user_id, lease_generation, now)
            assert_edit_allowed(dictation_id, actor_user_id, now)
            head = revisions.current_revision(dictation)
            if head is None or head.id != text.id:
                raise workflow.WorkflowError("stale_revision")
            if head.kind != revisions.KIND_HUMAN:
                raise workflow.WorkflowError("human_review_required")
            if trusted_context(context) is not True:
                raise workflow.WorkflowError("untrusted_context")
            if open_review_flags(dictation, head) != 0:
                raise workflow.WorkflowError("open_review_flags")
            rule = acceptance_policy(dictation).as_rule()
            if (item.acceptance_required and item.status != workflow.ACCEPTED) or not (
                acceptance_rules.final_allowed(
                    rule, head_is_human_reviewed=True,
                    latest_decision=acceptance_decision, head_revision_id=head.id)
            ):
                raise workflow.WorkflowError("acceptance_required")
            advance = db.update(ClinicDictation).where(
                ClinicDictation.id == dictation_id,
                ClinicDictation.version_counter == expected_version,
            ).values(version_counter=ClinicDictation.version_counter + 1)
            if cast(CursorResult, session.execute(advance)).rowcount != 1:
                raise workflow.WorkflowError("stale_version")
            item = workflow.transition(session, dictation_id, "complete")
            if item.status != workflow.DONE:
                raise workflow.WorkflowError("acceptance_required")
            try:
                created = release.create_release(
                    dictation, audio, text, context, text.normalised_text_hash, actor_user_id,
                    {"allowed": True, "permission": PERMISSION, "actor_user_id": actor_user_id},
                    now, document_type=document_type.strip(), release_type=RELEASE_TYPE,
                )
            except release.ReleaseError as error:
                raise workflow.WorkflowError("release_refused") from error
            stored_id, version = created.id, expected_version + 1
            if record_in_tx(
                "recording.edited", actor_user_id=actor_user_id,
                recording_id=dictation.recording_id, recording_owner_id=dictation.author_user_id,
                force=True, created_at=now.astimezone(timezone.utc).replace(tzinfo=None),
                details={"fields": ["workflow_status", "workflow_finalize"]},
            ) is None:
                raise workflow.WorkflowError("audit_unavailable")
    session.commit()

    # Durable now; scheduling is idempotent per release id.
    recording_id, author_id = dictation.recording_id, dictation.author_user_id
    job_id = _job_for(recording_id, job_type, stored_id)
    if job_id is None:
        from src.services.job_queue import job_queue

        job_id = job_queue.enqueue(
            user_id=author_id, recording_id=recording_id, job_type=job_type,
            params={PARAM_RELEASE: stored_id, "audio_revision_id": audio.id,
                    "transcript_revision_id": text.id, "context_revision_id": context.id,
                    "document_type": document_type.strip(),
                    "text_hash": text.normalised_text_hash},
        )
    return FinalizeResult(stored_id, job_id, version, replay)
