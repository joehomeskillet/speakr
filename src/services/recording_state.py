"""Archive state of a recording (#394) and recording-wide retention archival.

The owner's personal flag lives on the recording. Retention archival applies
to every viewer, and only the owner can restore it through the audited
transition service.
"""

from src.database import db


def get_user_archived(recording, user):
    if recording.retention_archived_at is not None:
        return True
    return bool(recording.is_archived)


def set_user_archived(recording, user, value, commit=True):
    from src.clinic.archive import ArchiveRestoreDenied, lock_recording, record_transition

    value = bool(value)
    lock_recording(recording)
    retained = recording.retention_archived_at is not None
    if retained and not value and recording.user_id != user.id:
        raise ArchiveRestoreDenied('Only the owner can restore a retention-archived recording')
    if get_user_archived(recording, user) == value:
        if commit:
            db.session.commit()
        return value
    scope = 'personal'
    if retained and not value:
        recording.retention_archived_at = None
        scope = 'recording'
    recording.is_archived = value
    record_transition(recording, value, rule='user.archive' if value else 'user.restore',
                      scope=scope, target_user_id=user.id)
    if commit:
        db.session.commit()
    return value


def parse_archived_flag(value):
    """Strict boolean for an API is_archived field; None when it is not one.

    bool("false") is True, so a string must be parsed, not cast.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ('true', '1', 'false', '0'):
        return value.strip().lower() in ('true', '1')
    return None


def archived_condition(recording_model, user_id):
    """SQL condition: the recording is archived for this user.

    NULL counts as not archived, so rows written before the column existed
    can never disappear from the list. A retention timestamp archives the
    recording for its owner even when the personal flag is clear.
    """
    return db.and_(
        recording_model.user_id == user_id,
        db.or_(
            recording_model.retention_archived_at.is_not(None),
            db.func.coalesce(recording_model.is_archived, False) == True,  # noqa: E712
        ),
    )


def set_user_notes(recording, user, notes):
    """Write the recording's notes. Callers enforce owner or edit access."""
    recording.notes = notes
