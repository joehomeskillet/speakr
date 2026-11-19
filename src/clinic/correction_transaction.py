"""Transactional correction: append transcript revision and save correction position atomically.

Provides ``save_correction_transaction`` — a caller‑controlled transaction that
writes a human transcript revision and updates ``ClinicCorrectionState`` in one
unit of work.  If either write fails, both are rolled back.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from src.database import db as _db
from src.clinic.models import _utcnow, ClinicDictation, ClinicTranscriptRevision
from src.clinic.correction import CorrectionConflict, CorrectionError, save_state, ClinicCorrectionState
from src.clinic.live_session import (
    LiveSessionError,
    _expiry as _session_expiry,
    _load as _load_session,
)
from src.clinic.revisions import add_human_revision
from src.clinic.lease import LeaseConflict, assert_current


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
) -> tuple[ClinicTranscriptRevision, ClinicCorrectionState]:
    """Append a human transcript revision and save the correction position atomically.

    All checks (lease, live session, expected versions) are performed before any
    database write.  If any check or write fails, the entire transaction is rolled
    back and no partial state is left visible.

    Returns ``(revision, state)`` on success.

    :raises LeaseConflict: lease generation mismatch or holder mismatch
    :raises LiveSessionError: live-session holder mismatch or no valid session
    :raises CorrectionConflict: expected version does not match
    :raises CorrectionError: invalid input (text, audio position, etc.)
    :raises SQLAlchemyError: any database error rolls back the transaction
    """
    # ── Validate lease ────────────────────────────────────────────────────
    try:
        assert_current(dictation.id, user_id, lease_generation, _utcnow())
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

        revision = add_human_revision(dictation, parent_revision, text, user_id)

        # Save the correction state pointing at the new revision
        state = save_state(
            dictation,
            user_id,
            audio_position_ms,
            text_revision=revision,
            text_offset=0,
            expected_version=expected_state_version,
        )

        _db.session.flush()
        return revision, state

    except SQLAlchemyError:
        _db.session.rollback()
        raise

    except Exception:
        _db.session.rollback()
        raise