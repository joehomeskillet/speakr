"""Transactional correction: append transcript revision and save correction position atomically.

Provides ``save_correction_transaction`` — a caller‑controlled transaction that
writes a human transcript revision and updates ``ClinicCorrectionState`` in one
unit of work.  If either write fails, both are rolled back.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError

from src.database import db as _db
from src.clinic.models import _utcnow, ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.correction import CorrectionConflict, CorrectionError, save_state, ClinicCorrectionState
from src.clinic.live_session import (
    LiveSessionError,
    _expiry as _session_expiry,
    _load as _load_session,
)
from src.clinic import workflow
from src.clinic.revisions import add_human_revision, current_revision
from src.services.audit import record_in_tx
from src.clinic.lease import LeaseConflict, assert_current
from src.clinic.notify_enqueue import record_notice
from src.clinic.richtext import CanonicalDocument, save_document


class CorrectionOriginalError(CorrectionError):
    """The first human text cannot bind an unambiguous existing original."""


def save_correction_transaction(
    dictation: ClinicDictation,
    user_id: int,
    *,
    text: str,
    audio_position_ms: int,
    lease_generation: int,
    live_session_id: str | None = None,
    expected_state_version: int | None = None,
    expected_text_revision_id: str | None = None,
    document: CanonicalDocument | None = None,
    expected_document_version: int | None = None,
    force_new_revision: bool = False,
    now: datetime | None = None,
) -> tuple[ClinicTranscriptRevision, ClinicCorrectionState]:
    """Append a human transcript revision and save the correction position atomically.

    When ``document`` is set, the canonical snapshot (text, formatting and audio
    anchors) is written in the same unit of work as the transcript and the
    playback position. ``save_document`` applies the existing edit lease and the
    same pool/object ``edit`` decision as ``correction_edit``. The caller commits.
    A failed check or write rolls the whole unit back. Older revisions and
    snapshots are not updated.

    Lease, live-session and expected-version checks run before any database write.
    If any check or write fails, the entire transaction is rolled back and no
    partial state is left visible.

    Returns ``(revision, state)`` on success.

    :raises LeaseConflict: lease generation mismatch or holder mismatch
    :raises LiveSessionError: live-session holder mismatch or no valid session
    :raises CorrectionConflict: expected version does not match
    :raises CorrectionError: invalid input (text, audio position, etc.)
    :raises SQLAlchemyError: any database error rolls back the transaction
    """
    # ── Validate lease ────────────────────────────────────────────────────
    checked_at = now if now is not None else _utcnow()
    try:
        assert_current(dictation.id, user_id, lease_generation, checked_at)
    except LeaseConflict:
        raise
    except Exception:
        raise LeaseConflict("stale_generation")

    # ── Validate live-session holder ─────────────────────────────────────
    if live_session_id is not None:
        try:
            session = _load_session(live_session_id)
            if session.author_user_id != user_id:
                raise LiveSessionError("not_holder")
            # An expired lock is still usable for a write that is part of this
            # transaction; we only reject when another holder has taken over.
        except LiveSessionError:
            raise
        except Exception:
            raise LiveSessionError("not_found")

    # ── Perform both writes in a single database transaction ───────────────
    try:
        # Append the human transcript revision
        parent_revision = None
        if expected_text_revision_id is not None:
            parent_revision = _db.session.get(ClinicTranscriptRevision, expected_text_revision_id)
            if parent_revision is None:
                raise CorrectionError("expected_text_revision_id not found")

        head_before = current_revision(dictation)
        original = None
        if head_before is None:
            # A first human document has no parent to supply its immutable source.
            # Never guess between originals or manufacture one from typed text.
            originals = _db.session.query(ClinicOriginal).filter_by(dictation_id=dictation.id).limit(2).all()
            if len(originals) != 1:
                raise CorrectionOriginalError('ORIGINAL_MISSING' if not originals else 'ORIGINAL_AMBIGUOUS')
            original = originals[0]
        revision = add_human_revision(
            dictation, parent_revision, text, user_id, original_version=original,
            force_new_revision=force_new_revision)
        if head_before is None or revision.id != head_before.id:
            _resubmit_accepted(dictation, user_id, revision.id)

        # Save the correction state pointing at the new revision
        state = save_state(
            dictation,
            user_id,
            audio_position_ms,
            text_revision=revision,
            text_offset=0,
            expected_version=expected_state_version,
        )
        if document is not None:
            # Same lease instant and the same edit decision as the correction route.
            save_document(
                dictation, revision, document,
                expected_version=expected_document_version,
                actor_user_id=user_id,
                lease_generation=lease_generation,
                now=checked_at,
            )

        _db.session.flush()
        return revision, state

    except SQLAlchemyError:
        _db.session.rollback()
        raise

    except Exception:
        _db.session.rollback()
        raise

def _resubmit_accepted(dictation: ClinicDictation, user_id: int, revision_id: str) -> None:
    """Wiedervorlage (v8-74): a text head that moves after acceptance needs a new acceptance.

    Runs inside the save transaction, so the status change and its audit row commit or roll
    back with the revision. The audit row carries field names only, never text.
    """
    item = _db.session.get(workflow.ClinicWorkflowDictation, dictation.id, with_for_update=True)
    if item is None or workflow.resubmit_for_acceptance(_db.session, dictation.id) is None:
        return
    if record_in_tx(
        "recording.edited", actor_user_id=user_id, recording_id=dictation.recording_id,
        recording_owner_id=dictation.author_user_id, force=True,
        details={"fields": ["workflow_status", "workflow_acceptance_resubmitted"]},
    ) is None:
        raise workflow.WorkflowError("audit_unavailable")
    record_notice("acceptance_requested", dictation, user_id, dictation.version_counter, revision_id)
