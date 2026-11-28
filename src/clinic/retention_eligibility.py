"""Automatic clinic archival starts at the current clinical release, not upload."""

from datetime import timedelta, timezone

from src.clinic.archive_policy import ClinicArchiveEntry
from src.clinic.models import ClinicDictation
from src.clinic.release import active_release
from src.clinic.workflow import ClinicWorkflowDictation, DONE
from src.database import db


def eligible_for_retention(recording, *, now, retention_days):
    """Check completion and hold under locks held until the caller commits.

    The caller locks the recording and checks the enabled policy and legacy
    exemptions. Missing clinical state fails closed. SQLite returns naive UTC
    release timestamps; PostgreSQL returns aware timestamps.
    """
    dictation = (db.session.query(ClinicDictation)
                 .filter_by(recording_id=recording.id)
                 .populate_existing().with_for_update().one_or_none())
    if dictation is None or not dictation.is_report:
        return False
    workflow = db.session.get(ClinicWorkflowDictation, dictation.id,
                              with_for_update=True, populate_existing=True)
    if workflow is None or workflow.status != DONE:
        return False
    entry = db.session.get(ClinicArchiveEntry, dictation.id,
                           with_for_update=True, populate_existing=True)
    if entry is not None and entry.legal_hold:
        return False
    release = active_release(dictation)
    if release is None:
        return False
    finished_at = release.created_at
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    # Preserve the existing strict cutoff: equality stays unarchived.
    return finished_at < now - timedelta(days=retention_days)
