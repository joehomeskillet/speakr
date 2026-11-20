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
  receipt (``origin_ref``: ``time=recording`` or ``time=upload``).
* Failure: the half-made upload recording is removed and a visible error is returned. The
  stored original file stays under its key, so a retry reuses it (no orphan row).

The anchor recording of the dictation is created by ``IngestService`` and is separate from
the upload recording; ``origin_ref`` carries the upload recording id as the link.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone

from flask import current_app, jsonify

from src.clinic import pool_policy, startup
from src.clinic.ingest import IngestError, IngestService
from src.clinic.models import ClinicDictation
from src.clinic.workflow import DRAFT, ClinicWorkflowDictation
from src.database import db

logger = logging.getLogger(__name__)

WEB_SOURCE = 'upload'  # processing_source of the browser upload and recorder finalisation
MSG_NO_POOL = ('Ihr Benutzerkonto ist keinem Autorenpool zugeordnet. '
               'Die Aufnahme wurde nicht als Diktat angelegt.')
MSG_FAILED = ('Die Aufnahme konnte nicht als Diktat angelegt werden. '
              'Bitte den Vorgang wiederholen.')
_CHUNK = 1024 * 1024


def author_pool_for(user_id: int) -> str | None:
    """One author pool per user is the clinic rule; a user in none has no pool."""
    ids = sorted(pool_policy.author_pool_ids(user_id))
    return ids[0] if ids else None


def _utc(naive: datetime) -> datetime:
    return naive.replace(tzinfo=timezone.utc)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(_CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def _refuse(recording, storage, code: str, message: str, status: int):
    """Remove the upload recording so no stray row remains, then answer visibly."""
    from src.services.upload_cancel import discard_unannounced_recording
    db.session.rollback()
    discard_unannounced_recording(recording, storage)
    return jsonify({'error': message, 'code': code}), status


def register_upload(*, owner, recording, storage, form, now: datetime,
                    meeting_date: datetime | None, processing_source: str):
    """Create the clinic dictation for a stored web upload.

    Returns None when done (or when the upload is not a browser upload); otherwise a
    ``(response, status)`` pair the caller returns instead of the normal answer.
    ``meeting_date is now`` marks the upload-time fallback of the pipeline.
    """
    if processing_source != WEB_SOURCE:
        return None
    pool_id = author_pool_for(owner.id)
    if pool_id is None:
        return _refuse(recording, storage, 'clinic_no_author_pool', MSG_NO_POOL, 403)

    from src.services.upload_cancel import client_upload_id_from
    time_source = 'upload' if meeting_date is None or meeting_date is now else 'recording'
    moment = _utc(now if time_source == 'upload' else meeting_date)
    origin = 'web-recorder' if str(form.get('from_recorder', '')).lower() == 'true' else 'web-upload'
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
                    origin_ref=f'upload:recording:{recording.id}:time={time_source}',
                    author_user_id=owner.id, idempotency_key=key, now=moment, commit=False)
        dictation = db.session.get(ClinicDictation, receipt.dictation_id)
        if dictation.pool_id is None:
            dictation.pool_id = pool_id
        if db.session.get(ClinicWorkflowDictation, dictation.id) is None:
            db.session.add(ClinicWorkflowDictation(
                id=dictation.id, author_user_id=owner.id, pool_id=dictation.pool_id,
                status=DRAFT, audio_seconds=int(recording.audio_duration_seconds or 0)))
        db.session.commit()
    except IngestError as error:
        logger.warning('web ingest refused for user %s: %s', owner.id, error.code)
        return _refuse(recording, storage, f'clinic_ingest_{error.code}', MSG_FAILED,
                       409 if error.code == 'conflict' else 500)
    except Exception:
        logger.error('web ingest failed for user %s', owner.id, exc_info=True)
        return _refuse(recording, storage, 'clinic_ingest_failed', MSG_FAILED, 500)
    return None
