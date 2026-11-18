"""
Recording retention archival and existing manual audio removal.
"""

import os
from datetime import datetime, timedelta
from flask import current_app

from src.database import db
from src.models import Recording, RecordingTag, Tag
from src.services.storage import get_storage_service
from src.utils.localization import api_error
from src.utils.log_filters import loggable_locator




def tag_binds_retention(recording, tag):
    """Whether a tag on the recording takes part in its retention.

    A group tag always does. A personal tag is private to the user who made
    it, and anyone who can view a recording may attach their own (#589), so
    only the owner's personal tags count when choosing the archive period.
    """
    if tag is None:
        return False
    if tag.group_id is not None:
        return True
    return recording.user_id is not None and tag.user_id == recording.user_id


def retention_tags(recording):
    """The recording's tags that take part in its retention."""
    return [assoc.tag for assoc in recording.tag_associations if tag_binds_retention(recording, assoc.tag)]


def is_recording_exempt_from_deletion(recording):
    """
    Check the legacy exemption fields for automatic archival.

    Args:
        recording: Recording object to check

    Returns:
        Boolean indicating if the recording should be kept
    """
    # Manual exemption flag
    if recording.deletion_exempt:
        return True

    # The existing protection fields now exempt the recording from archival.
    # Protection can be indicated by either protect_from_deletion flag OR retention_days == -1
    for tag in retention_tags(recording):
        if tag.protect_from_deletion:
            return True
        if tag.retention_days == -1:
            return True

    return False



def get_retention_days_for_recording(recording):
    """
    Get the effective retention period for a recording.
    Multi-tier system: tag retention (shortest) → global retention

    Tags with retention_days set override the global retention policy.
    If multiple tags have retention_days, the SHORTEST period is used (most conservative).
    Note: retention_days == -1 indicates infinite retention (protected), which is handled separately.

    Args:
        recording: Recording object

    Returns:
        Integer days for retention period, or None if no retention applies
    """
    # Collect all tag-level retention periods
    # Skip -1 (infinite retention/protected) as that's handled in is_recording_exempt_from_deletion
    tag_retention_periods = []
    for tag in retention_tags(recording):
        if tag.retention_days and tag.retention_days > 0:
            tag_retention_periods.append(tag.retention_days)

    # If any tags have retention periods, use the shortest one (most conservative)
    if tag_retention_periods:
        return min(tag_retention_periods)

    # Fall back to global retention
    if _self.GLOBAL_RETENTION_DAYS > 0:
        return _self.GLOBAL_RETENTION_DAYS

    return None



def process_auto_deletion():
    """Compatibility entry point for scheduled/admin retention archival.

    Both legacy deletion modes now preserve every file and database row.
    Each recording and its mandatory evidence commit in one transaction.
    """
    from src.clinic.archive import archive_for_retention, lock_recording

    if not _self.ENABLE_AUTO_DELETION:
        return api_error("auto_deletion_disabled")
    if _self.GLOBAL_RETENTION_DAYS <= 0:
        current_app.logger.info("No global retention configured, checking for tag-level retention policies")

    stats = {
        "checked": 0, "archived": 0, "already_archived": 0,
        "deleted_audio_only": 0, "deleted_full": 0,
        "exempted": 0, "skipped_no_retention": 0, "errors": 0,
    }
    try:
        candidates = db.session.query(Recording.id).filter(
            Recording.status.in_(("COMPLETED", "FAILED"))
        ).all()
        stats["checked"] = len(candidates)
        current_time = datetime.utcnow()
        for (recording_id,) in candidates:
            try:
                recording = db.session.get(Recording, recording_id)
                if recording is None:
                    continue
                lock_recording(recording)
                # Recheck under the lock in case a reprocessing job started.
                if recording.status not in ("COMPLETED", "FAILED"):
                    continue
                if recording.retention_archived_at is not None:
                    stats["already_archived"] += 1
                    continue
                if is_recording_exempt_from_deletion(recording):
                    stats["exempted"] += 1
                    continue
                retention_days = get_retention_days_for_recording(recording)
                if not retention_days:
                    stats["skipped_no_retention"] += 1
                    continue
                if recording.created_at is None or recording.created_at >= current_time - timedelta(days=retention_days):
                    continue
                matching_tags = [tag.id for tag in retention_tags(recording)
                                 if tag.retention_days == retention_days and tag.retention_days > 0]
                changed = archive_for_retention(
                    recording, now=current_time, retention_days=retention_days,
                    tag_id=min(matching_tags) if matching_tags else None,
                )
                db.session.commit()
                stats["archived"] += int(changed)
                current_app.logger.info(
                    "Archived recording %s after %s retention days (%s)",
                    recording_id, retention_days, loggable_locator(recording.audio_path),
                )
            except Exception as exc:
                db.session.rollback()
                stats["errors"] += 1
                current_app.logger.error("Error archiving recording %s (%s)", recording_id, type(exc).__name__)
            finally:
                # Release locks even when the recording was skipped.
                db.session.rollback()
        current_app.logger.info("Automatic archival completed: %s", stats)
        return stats
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Automatic archival failed (%s)", type(exc).__name__)
        return {"error": "Automatic archival failed"}


def remove_recording_audio(recording, reason='user'):
    """Delete a recording's media file and keep everything else.

    The transcript, summary, notes, tags and shares stay; the recording shows
    as "Audio removed" and can no longer be played or reprocessed. Used by
    the manual "Delete audio" action.

    Returns True when a stored file was deleted, False when there was none
    (the recording is marked either way). Commits.
    """
    storage = get_storage_service()
    deleted = False
    if recording.audio_path and storage.exists(recording.audio_path):
        storage.delete(recording.audio_path, missing_ok=True)
        current_app.logger.info(f"Removed audio file for recording {recording.id}: {loggable_locator(recording.audio_path)}")
        deleted = True
    from src.services.waveform_peaks import remove_peaks
    remove_peaks(current_app.config.get('UPLOAD_FOLDER') or '', recording.id)
    if not recording.audio_deleted_at:
        recording.audio_deleted_at = datetime.utcnow()
    from src.services import audit
    audit.record_in_tx('recording.audio.deleted', recording=recording, details={'reason': reason})
    db.session.commit()
    return deleted


# Hot-applied System Settings (high priority) with environment fallback.
from src.utils.system_settings import apply_hot_settings
_SYSTEM_SETTING_MAP = {
    "ENABLE_AUTO_DELETION": ('enable_auto_deletion', 'ENABLE_AUTO_DELETION', False),
    "GLOBAL_RETENTION_DAYS": ('global_retention_days', 'GLOBAL_RETENTION_DAYS', 0),
    "DELETION_MODE": ('deletion_mode', 'DELETION_MODE', 'full_recording'),
}
_self, __getattr__ = apply_hot_settings(__name__, _SYSTEM_SETTING_MAP)
