"""Archive state of a recording (#394).

Archive works like inbox and star: the value lives on the recording and
belongs to its owner.
"""

from src.database import db


def get_user_archived(recording, user):
    return bool(recording.is_archived)


def set_user_archived(recording, user, value, commit=True):
    value = bool(value)
    recording.is_archived = value
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
    can never disappear from the list.
    """
    return db.and_(recording_model.user_id == user_id,
                   db.func.coalesce(recording_model.is_archived, False) == True)  # noqa: E712


def set_user_notes(recording, user, notes):
    """The owner writes the recording's notes."""
    recording.notes = notes
