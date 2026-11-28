"""Author-only trash and scheduled purge for private clinic recordings."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, has_request_context
from sqlalchemy import event, exists, or_, select
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql.ddl import sort_tables

from src.clinic import startup, web_ingest, workflow
from src.clinic.audio_revisions import ClinicAudioSegment
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.recording_type import PRIVATE_TYPES
from src.database import db
from src.models import ProcessingJob, Recording
from src.services import audit
from src.services.recording_deletion import delete_recording_completely
from src.services.storage.locator import local_path_from_key
from src.services.transcription.provider_tasks import cancel_remote_tasks

RETENTION = timedelta(days=30)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _trash_link(recording_id, outer_table):
    dictation = ClinicDictation.__table__
    original = ClinicOriginal.__table__
    return exists(select(1).select_from(dictation).where(
        dictation.c.private_purge_at.is_not(None),
        or_(dictation.c.recording_id == recording_id,
            exists(select(1).select_from(original).where(
                original.c.dictation_id == dictation.c.id,
                web_ingest.source_recording_link(recording_id),
            ).correlate(dictation, outer_table))),
    ).correlate(outer_table))


@event.listens_for(Session, 'do_orm_execute')
def _hide_trashed_recordings(state):
    """Apply before search, pagination and projections of stock recording APIs."""
    if (not state.is_select or not has_request_context() or not clinic_mode_enabled()
            or state.execution_options.get('clinic_trash_list')):
        return
    state.statement = state.statement.options(
        with_loader_criteria(Recording, ~_trash_link(Recording.id, Recording.__table__), include_aliases=True),
        with_loader_criteria(ProcessingJob, ~_trash_link(ProcessingJob.recording_id, ProcessingJob.__table__),
                             include_aliases=True),
    )


def list_for_author(actor: int) -> list[dict]:
    from src.clinic.recordings_list import day_of

    rows = (db.session.query(ClinicDictation, Recording.title)
            .join(Recording, Recording.id == ClinicDictation.recording_id)
            .filter(ClinicDictation.author_user_id == actor,
                    ClinicDictation.recording_type.in_(PRIVATE_TYPES),
                    ClinicDictation.private_purge_at > utcnow())
            .order_by(ClinicDictation.private_purge_at.desc(), ClinicDictation.id)
            .execution_options(clinic_trash_list=True))
    return [dict(dictation_id=row.id, title=title, recording_type=row.recording_type,
                 recorded_day=day_of(row.created_at), purge_day=day_of(row.private_purge_at))
            for row, title in rows]


def locked_dictation(target: str) -> ClinicDictation | None:
    connection = db.session.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
    return db.session.get(ClinicDictation, target, populate_existing=True, with_for_update=True)


def move(target: str, actor: int, *, restore: bool) -> bool:
    row = locked_dictation(target)
    if row is None or row.author_user_id != actor or row.recording_type not in PRIVATE_TYPES:
        return False
    now = utcnow()
    deadline = row.private_purge_at
    if deadline is not None and deadline <= now:
        return False
    row.private_purge_at = None if restore else deadline or now + RETENTION
    db.session.commit()
    return True


def _recordings(row, originals):
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES

    ids = {row.recording_id}
    for original in originals:
        for prefix in ORIGIN_REF_PREFIXES:
            if original.origin_ref.startswith(prefix):
                source_id = original.origin_ref[len(prefix):].split(':', 1)[0]
                if source_id.isascii() and source_id.isdecimal():
                    ids.add(int(source_id))
                break
    # Match the worker link's ownership rule before deleting any secondary row.
    anchor = db.session.get(Recording, row.recording_id)
    return (Recording.query.filter(
        Recording.id.in_(ids),
        or_(Recording.id == row.recording_id, Recording.user_id == anchor.user_id))
        .order_by(Recording.id).all())


def _delete_content(row):
    originals = ClinicOriginal.query.filter_by(dictation_id=row.id).all()
    recordings = _recordings(row, originals)
    # Serialize with JobQueue finalization before unlinking data.
    jobs = (ProcessingJob.query.filter(
        ProcessingJob.recording_id.in_([recording.id for recording in recordings]),
        ProcessingJob.provider_task.is_not(None)).with_for_update().all())
    cancel_remote_tasks(jobs, require_confirmation=True)

    root = startup.originals_root(current_app)
    segments = ClinicAudioSegment.query.filter_by(dictation_id=row.id).all()
    keys = {item.storage_key for item in (*originals, *segments)}

    # Revision graphs use non-cascading FKs. Delete children before parents,
    # including rows tied to the dictation through a revision or a release.
    tables = db.metadata.tables
    indirect = {
        'clinic_context_grant_use': ('revision_id', 'clinic_context_revision'),
        'clinic_rich_text_snapshot': ('revision_id', 'clinic_transcript_revision'),
        'clinic_delivery': ('release_id', 'clinic_release'),
        'clinic_release_supersession': ('release_id', 'clinic_release'),
    }
    owned = [table for table in tables.values() if table.name.startswith('clinic_')
             and ('dictation_id' in table.c or table.name in indirect)]
    for table in reversed(sort_tables(owned)):
        if table.name in indirect:
            key, parent_name = indirect[table.name]
            parent = tables[parent_name]
            condition = table.c[key].in_(select(parent.c.id).where(parent.c.dictation_id == row.id))
        else:
            condition = table.c.dictation_id == row.id
        db.session.execute(table.delete().where(condition))
    flow = workflow.ClinicWorkflowDictation.__table__
    db.session.execute(flow.delete().where(flow.c.id == row.id))
    db.session.delete(row)
    db.session.flush()

    evidence = audit.record_in_tx('recording.deleted', recording_id=row.recording_id,
                                  actor_kind='session', actor_user_id=row.author_user_id,
                                  created_at=row.private_purge_at - RETENTION, force=True,
                                  details={'reason': 'deleted by author'})
    if evidence is None:
        raise RuntimeError('Private recording purge must be audited')
    for key in keys:
        if (ClinicOriginal.query.filter_by(storage_key=key).first()
                or ClinicAudioSegment.query.filter_by(storage_key=key).first()):
            continue
        Path(local_path_from_key(root, key)).unlink(missing_ok=True)
    db.session.info['tombstone_reason'] = None
    try:
        for recording in recordings:
            delete_recording_completely(recording, strict_media=True, private_purge=True)
        db.session.commit()
    finally:
        db.session.info.pop('tombstone_reason', None)


def purge_expired(*, now: datetime | None = None) -> int:
    """Run from the daily scheduler. Failed items stay hidden and retry next run."""
    now = utcnow() if now is None else now
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    targets = db.session.scalars(select(ClinicDictation.id).where(
        ClinicDictation.private_purge_at <= now,
        ClinicDictation.recording_type.in_(PRIVATE_TYPES),
    )).all()
    db.session.rollback()
    purged = 0
    for target in targets:
        try:
            row = locked_dictation(target)
            if (row is None or row.recording_type not in PRIVATE_TYPES or
                    row.private_purge_at is None or row.private_purge_at > now):
                db.session.rollback()
                continue
            _delete_content(row)
            purged += 1
        except Exception as error:
            db.session.rollback()
            current_app.logger.error('Private recording purge failed: %s', type(error).__name__)
    return purged
