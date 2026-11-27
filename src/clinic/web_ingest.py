"""Bridge from the web upload and the browser recorder to a clinic dictation.

Called once from the upload pipeline (``src/services/recording_ingestion.py``) in clinic
mode, after the upload was stored as a recording. It hands the stored bytes to
``IngestService.store_original`` (the only place a dictation is created), binds the
dictation to the logged-in author and the author's pool, and adds the work-list row.

* Author: the logged-in uploader (``dictated_by``).
* Pool: the one author pool of that user (same rule as the preference routes). A user in no
  pool gets a German refusal instead of a dictation nobody could see.
* Idempotency key: ``web:<user>:<client_upload_id>``, or the content hash when the browser
  sent no id. The same key and bytes return the existing dictation.
* Time: the recording time (form, file name, file date, embedded metadata) when the upload
  pipeline found one; otherwise the upload time. Which one was used is written into the
  receipt (``origin_ref``: ``time=recording``, ``time=file`` (file system date, marked) or ``time=upload``).
* Speech recognition: on unless the form opts out (``use_speech_recognition`` false).
  The opt-out is marked ``:asr=off`` in ``origin_ref``, before the time label. A missing
  or unknown value stays on, so an old upload request still queues a transcript job.
* Handover (not reached by the single hook): multi-file join uploads, server-side recorder
  sessions (``/upload/session/<sid>/finalize``) and share_target create no dictation; a cancel
  after the request finished (``upload_cancel.cancel_upload``) leaves the bound dictation.
* Failure: the half-made upload recording is removed and a visible error is returned. The
  stored original file stays under its key, so a retry reuses it (no orphan row).

The anchor recording of the dictation is created by ``IngestService`` and is separate from
the upload recording; ``origin_ref`` carries the upload recording id as the link.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone

from flask import current_app, jsonify
from sqlalchemy import or_

from src.clinic import pool_policy, pools, startup
from src.clinic.ingest import IngestError, IngestService
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.workflow import DRAFT, ClinicWorkflowDictation
from src.database import db
from src.models import Recording

logger = logging.getLogger(__name__)

WEB_SOURCE = 'upload'  # processing_source of the browser upload and recorder finalisation
MSG_NO_POOL = ('Ihr Benutzerkonto ist keinem Autorenpool zugeordnet. '
               'Die Aufnahme wurde nicht als Diktat angelegt.')
MSG_FAILED = ('Die Aufnahme konnte nicht als Diktat angelegt werden. '
              'Bitte den Vorgang wiederholen.')
_CHUNK = 1024 * 1024
_RECORDER_TITLE = re.compile(r'Diktat [0-9]{2}\.[0-9]{2}\.[0-9]{4} [0-9]{2}:[0-9]{2}')


def generated_recorder_title(value: str | None) -> str | None:
    """Only the fixed microphone timestamp format may be shown in the recordings list."""
    return value if value and _RECORDER_TITLE.fullmatch(value) else None


def dictation_for_recording(recording_id: int, *, session=None) -> ClinicDictation | None:
    """Resolve a worker's recording, preferring the dictation's own anchor."""
    session = db.session if session is None else session
    dictations = session.query(ClinicDictation)
    anchor = dictations.filter_by(recording_id=recording_id).one_or_none()
    if anchor is not None:
        return anchor
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES
    source = session.get(Recording, recording_id)
    if source is None:
        return None
    refs = [ClinicOriginal.origin_ref.startswith(f'{prefix}{recording_id}:')
            for prefix in ORIGIN_REF_PREFIXES]
    return (dictations.join(ClinicOriginal).join(Recording, ClinicDictation.recording_id == Recording.id)
            .filter(Recording.user_id == source.user_id, or_(*refs)).one_or_none())


def dictations_for_recordings(recording_ids, *, session=None):
    """Resolve job recordings in batches, preserving anchor priority and owner checks."""
    from sqlalchemy import String, cast
    from sqlalchemy.orm import aliased
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES

    session = db.session if session is None else session
    ids = list(set(recording_ids))
    result = {}
    source = aliased(Recording)
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        anchors = {d.recording_id: d for d in session.query(ClinicDictation).filter(
            ClinicDictation.recording_id.in_(part))}
        refs = [ClinicOriginal.origin_ref.startswith(prefix + cast(source.id, String) + ':')
                for prefix in ORIGIN_REF_PREFIXES]
        matches = (session.query(source.id, ClinicDictation).select_from(source)
                   .join(ClinicOriginal, or_(*refs))
                   .join(ClinicDictation, ClinicDictation.id == ClinicOriginal.dictation_id)
                   .join(Recording, Recording.id == ClinicDictation.recording_id)
                   .filter(source.id.in_(part), Recording.user_id == source.user_id))
        linked = {}
        for rid, dictation in matches:
            linked.setdefault(rid, {})[dictation.id] = dictation
        result.update({rid: next(iter(rows.values())) for rid, rows in linked.items() if len(rows) == 1})
        result.update(anchors)
    return result


def author_pool_for(user_id: int) -> str | None:
    """One author pool per user is the clinic rule; a user in none has no pool.

    Inactive or out-of-validity pools are skipped (first remaining id wins).
    """
    now = datetime.now(timezone.utc)
    for pool_id in sorted(pool_policy.author_pool_ids(user_id)):
        pool = db.session.get(pools.ClinicAuthorPool, pool_id)
        if pool is not None and pools.is_routable(pool, now):
            return pool_id
    return None


def _utc(naive: datetime) -> datetime:
    return naive.replace(tzinfo=timezone.utc)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(_CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


# Only an explicit opt-out turns recognition off. Unknown text stays on.
_ASR_OFF = frozenset({'0', 'false', 'no', 'nein', 'off'})
_ASR_MARK = ':asr=off:'


def speech_recognition_requested(form) -> bool:
    """Whether this upload wants a transcript job. Default is on."""
    raw = None if form is None else form.get('use_speech_recognition')
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return True
    text = str(raw).strip().lower()
    if not text:
        return True
    return text not in _ASR_OFF


def recognition_disabled(recording_id: int, *, session=None) -> bool:
    """True when the stored upload receipt for this recording opted out of ASR."""
    session = db.session if session is None else session
    dictation = dictation_for_recording(recording_id, session=session)
    if dictation is None:
        return False
    ref = session.query(ClinicOriginal.origin_ref).filter_by(dictation_id=dictation.id).scalar()
    return isinstance(ref, str) and _ASR_MARK in ref


def skip_transcript_job(form, recording_id: int | None = None) -> bool:
    """True when job creation must not queue a transcript for this clinic upload.

    An explicit form value wins. A retry that omits the field keeps a stored opt-out.
    """
    raw = None if form is None else form.get('use_speech_recognition')
    if raw is not None and str(raw).strip() != '':
        return not speech_recognition_requested(form)
    if recording_id is None:
        return False
    return recognition_disabled(recording_id)


def _time_source(form, now: datetime, meeting_date: datetime | None) -> str:
    """Label the time: recording (form, recorder, embedded), file (file mtime) or upload."""
    if meeting_date is None or meeting_date is now:
        return 'upload'
    if form.get('meeting_date') or str(form.get('from_recorder', '')).lower() == 'true':
        return 'recording'
    try:
        mtime = datetime.fromtimestamp(int(form.get('file_last_modified')) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return 'recording'
    return 'file' if meeting_date == mtime.replace(tzinfo=None) else 'recording'


def _refuse(recording, storage, code: str, message: str, status: int):
    """Remove the upload recording so no stray row remains, then answer visibly."""
    from src.services.upload_cancel import discard_unannounced_recording
    db.session.rollback()
    discard_unannounced_recording(recording, storage)
    return jsonify({'error': message, 'code': code}), status


def register_upload(*, owner, recording, storage, form, now: datetime,
                    meeting_date: datetime | None, processing_source: str):
    """Create the clinic dictation for a stored web upload.

    Returns the dictation UUID on success, None for another upload source, or
    a ``(response, status)`` pair when the upload is refused.
    ``meeting_date is now`` marks the upload-time fallback of the pipeline.
    """
    if processing_source != WEB_SOURCE:
        return None
    pool_id = author_pool_for(owner.id)
    if pool_id is None:
        return _refuse(recording, storage, 'clinic_no_author_pool', MSG_NO_POOL, 403)

    from src.services.upload_cancel import client_upload_id_from
    time_source = _time_source(form, now, meeting_date)
    moment = _utc(now if time_source == 'upload' else meeting_date)
    origin = 'web-recorder' if str(form.get('from_recorder', '')).lower() == 'true' else 'web-upload'
    # Keep the default ref unchanged. The mark sits before ``time=`` so existing
    # parsers that split off the recording id still match.
    asr_mark = ':asr=off' if not speech_recognition_requested(form) else ''
    try:
        with storage.materialize(recording.audio_path) as stored:
            path = stored.local_path
            size = os.path.getsize(path)
            digest = _sha256(path)
            key = f'web:{owner.id}:{client_upload_id_from(form) or digest}'
            service = IngestService(startup.originals_root(current_app))
            with open(path, 'rb') as stream:
                receipt = service.store_original(
                    stream, declared_sha256=digest, declared_size=size, origin=origin,
                    origin_ref=f'upload:recording:{recording.id}{asr_mark}:time={time_source}',
                    author_user_id=owner.id, idempotency_key=key, now=moment, commit=False)
        dictation = db.session.get(ClinicDictation, receipt.dictation_id)
        if dictation.pool_id is None:
            dictation.pool_id = pool_id
        if db.session.get(ClinicWorkflowDictation, dictation.id) is None:
            if origin == 'web-recorder' and (title := generated_recorder_title(recording.title)):
                anchor = db.session.get(Recording, dictation.recording_id)
                anchor.title = title
                anchor.title_source = recording.title_source
            db.session.add(ClinicWorkflowDictation(
                id=dictation.id, author_user_id=owner.id, pool_id=dictation.pool_id,
                status=DRAFT, created_at=moment, audio_seconds=int(recording.audio_duration_seconds or 0)))
        db.session.commit()
    except IngestError as error:
        logger.warning('web ingest refused for user %s: %s', owner.id, error.code)
        return _refuse(recording, storage, f'clinic_ingest_{error.code}', MSG_FAILED,
                       409 if error.code == 'conflict' else 500)
    except Exception:
        logger.error('web ingest failed for user %s', owner.id, exc_info=True)
        return _refuse(recording, storage, 'clinic_ingest_failed', MSG_FAILED, 500)
    return receipt.dictation_id
