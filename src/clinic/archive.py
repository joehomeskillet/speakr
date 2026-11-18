"""Transactional archival with mandatory, append-only evidence.

Retention has recording-wide scope. Manual archive is personal, and only
the owner may restore a retention-archived recording for all viewers.
Callers own the transaction and must roll it back on any failure.
"""

from datetime import datetime

from sqlalchemy import text

from src.database import db
from src.clinic.models import ClinicArchiveEvent
from src.services import audit


class ArchiveRestoreDenied(PermissionError):
    """A recipient cannot clear the recording-wide retention archive."""


def lock_recording(recording):
    """Refresh after locking on PostgreSQL, preserving pending caller edits.

    SQLite serializes writes. Its stale readers are additionally fenced by
    the unique archive revision, so a conflict rolls back the whole change.
    """
    db.session.flush()
    db.session.refresh(recording, with_for_update=True)


def record_transition(recording, archived, *, rule, scope, target_user_id=None,
                      retention_days=None, tag_id=None):
    """Add mandatory access-log and durable evidence in the state transaction."""
    details = {'rule': rule, 'scope': scope}
    if retention_days is not None:
        details['retention_days'] = int(retention_days)
    if tag_id is not None:
        details['tag_id'] = int(tag_id)
    event = audit.record_in_tx(
        'recording.archived' if archived else 'recording.unarchived',
        recording=recording, target_user_id=target_user_id, details=details, force=True,
    )
    if event is None:
        raise RuntimeError('Archive transition requires an audit event')
    recording.archive_revision += 1
    db.session.flush()
    db.session.add(ClinicArchiveEvent(
        recording_id=recording.id,
        recording_created_at=recording.created_at or datetime.min,
        revision=recording.archive_revision,
        audit_event_id=event.id,
        event=event.event,
        created_at=event.created_at,
        actor_kind=event.actor_kind,
        actor_user_id=event.actor_user_id,
        actor_token_id=event.actor_token_id,
        recording_owner_id=event.recording_owner_id,
        target_user_id=event.target_user_id,
        details=event.details,
    ))
    db.session.flush()


def archive_for_retention(recording, *, now, retention_days, tag_id=None):
    """Archive an eligible, locked recording. No-op when already archived."""
    if recording.retention_archived_at is not None:
        return False
    recording.retention_archived_at = now
    record_transition(
        recording, True, rule='retention.tag' if tag_id is not None else 'retention.global',
        scope='recording', retention_days=retention_days, tag_id=tag_id,
    )
    return True


def install_archive_guards(engine):
    """Protect the durable ledger from ORM and raw SQL mutation."""
    if engine.name == 'sqlite':
        statements = [
            f"CREATE TRIGGER IF NOT EXISTS clinic_archive_no_{action.lower()} "
            f"BEFORE {action} ON clinic_archive_event FOR EACH ROW BEGIN "
            "SELECT RAISE(ABORT, 'clinic archive evidence is append-only'); END"
            for action in ('UPDATE', 'DELETE')
        ]
    elif engine.name == 'postgresql':
        statements = [
            """CREATE OR REPLACE FUNCTION clinic_archive_immutable()
               RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
               RAISE EXCEPTION 'clinic archive evidence is append-only'
               USING ERRCODE = 'check_violation'; END; $$""",
            'DROP TRIGGER IF EXISTS clinic_archive_immutable ON clinic_archive_event',
            'CREATE TRIGGER clinic_archive_immutable BEFORE UPDATE OR DELETE '
            'ON clinic_archive_event FOR EACH ROW '
            'EXECUTE PROCEDURE clinic_archive_immutable()',
        ]
    else:
        raise RuntimeError('Archive evidence requires SQLite or PostgreSQL')
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
