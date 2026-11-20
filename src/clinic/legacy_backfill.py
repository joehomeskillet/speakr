"""One-time takeover of legacy recordings into clinic dictations (V8 backfill, v8-95).

Recordings stored before the web-ingest bridge existed have no clinic dictation, so they
are missing under Aufnahmen and in the Arbeitsliste. This module finds them and creates
one dictation per recording through the same store as a web upload
(``IngestService.store_original``), bound the same way as ``web_ingest.register_upload``:

* Author: the recording owner. Pool: ``web_ingest.author_pool_for(owner)``; an owner
  without a routable pool is skipped with ``no_author_pool``.
* Idempotency key ``legacy:<recording_id>``; the receipt ``origin_ref`` is
  ``legacy:recording:<id>:time=recording|upload`` and is the link that marks a recording as taken over.
* Time: ``meeting_date`` when set (time source ``recording``), else ``created_at``
  (time source ``upload``). The new anchor recording of the dictation carries the same
  ``meeting_date``, the source status and the duration, so the list shows them (the list
  reads the anchor row). Its ``audio_path`` stays empty, as for every dictation.
* A completed transcription becomes the first ASR revision through
  ``correction.ingest_asr_result`` (the function the transcription-complete hook calls).
  A failure of that step keeps the dictation and is reported as ``asr_failed``.
* The source recording and its file are only read. Logs and output carry ids, counts and
  reasons, never text.

Why not ``register_upload`` itself: its refusal path deletes the upload recording, which
must never happen to a source row here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import current_app

from src.clinic import startup, web_ingest
from src.clinic.ingest import IngestError, IngestService, storage_key_for
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.workflow import DRAFT, ClinicWorkflowDictation
from src.database import db
from src.models import Recording
from src.services import audit

logger = logging.getLogger(__name__)

ORIGIN = 'legacy-backfill'
ORIGIN_REF_PREFIXES = ('upload:recording:', 'legacy:recording:')
MODEL_REF = 'legacy-backfill'

CREATE = 'create'
SKIP = 'skip'


@dataclass
class Row:
    """One candidate line: what a dry run prints and what apply works on."""

    recording_id: int
    owner_id: int
    action: str
    reason: str
    pool_id: str | None = None
    dictation_id: str | None = None


def key_for(recording_id: int) -> str:
    return f'legacy:{recording_id}'


def _utc(naive: datetime) -> datetime:
    return naive.replace(tzinfo=timezone.utc) if naive.tzinfo is None else naive.astimezone(timezone.utc)


def _linked_recording_ids() -> set[int]:
    """Recordings already taken over: anchors of a dictation and web-ingest or backfill links."""
    linked = {rid for (rid,) in db.session.query(ClinicDictation.recording_id) if rid is not None}
    for (ref,) in db.session.query(ClinicOriginal.origin_ref):
        for prefix in ORIGIN_REF_PREFIXES:
            if ref.startswith(prefix):
                head = ref[len(prefix):].split(':', 1)[0]
                if head.isdigit():
                    linked.add(int(head))
    return linked


def _has_transcript(recording: Recording) -> bool:
    return (recording.status == 'COMPLETED' and isinstance(recording.transcription, str)
            and recording.transcription.strip() != '')


def plan(storage=None) -> list[Row]:
    """List every recording with a stored audio path and what apply would do with it."""
    if storage is None:
        from src.services.storage import get_storage_service
        storage = get_storage_service()
    linked = _linked_recording_ids()
    pools: dict[int, str | None] = {}
    rows = []
    query = Recording.query.filter(Recording.audio_path.isnot(None), Recording.audio_path != '')
    for recording in query.order_by(Recording.id):
        row = Row(recording.id, recording.user_id, SKIP, '')
        rows.append(row)
        if recording.id in linked or _key_taken(recording.id):
            row.reason = 'already_linked'
            continue
        try:
            present = storage.exists(recording.audio_path)
        except Exception:
            present = False
        if not present:
            row.reason = 'no_audio_file'
            continue
        if recording.user_id not in pools:
            pools[recording.user_id] = web_ingest.author_pool_for(recording.user_id)
        row.pool_id = pools[recording.user_id]
        if row.pool_id is None:
            row.reason = 'no_author_pool'
            continue
        row.action = CREATE
        row.reason = 'with_transcription' if _has_transcript(recording) else 'without_transcription'
    return rows


def _key_taken(recording_id: int) -> bool:
    return ClinicOriginal.query.filter_by(storage_key=storage_key_for(key_for(recording_id))).first() is not None


def _asr_text(recording: Recording) -> str:
    from src.tasks.processing import _plain_transcript_text
    return _plain_transcript_text(recording.transcription)


def _create(row: Row, recording: Recording, storage) -> str:
    """Create one dictation in the caller's transaction; returns the text outcome."""
    source_time = 'recording' if recording.meeting_date is not None else 'upload'
    moment = _utc(recording.meeting_date if source_time == 'recording' else recording.created_at)
    with storage.materialize(recording.audio_path) as stored:
        path = stored.local_path
        size = os.path.getsize(path)
        digest = web_ingest._sha256(path)
        service = IngestService(startup.originals_root(current_app))
        with open(path, 'rb') as stream:
            receipt = service.store_original(
                stream, declared_sha256=digest, declared_size=size, origin=ORIGIN,
                origin_ref=f'legacy:recording:{recording.id}:time={source_time}',
                author_user_id=recording.user_id, idempotency_key=key_for(recording.id),
                now=moment, commit=False)
    dictation = db.session.get(ClinicDictation, receipt.dictation_id)
    if dictation.pool_id is None:
        dictation.pool_id = row.pool_id
    anchor = db.session.get(Recording, dictation.recording_id)
    anchor.meeting_date = recording.meeting_date
    anchor.status = recording.status
    anchor.audio_duration_seconds = recording.audio_duration_seconds
    if db.session.get(ClinicWorkflowDictation, dictation.id) is None:
        db.session.add(ClinicWorkflowDictation(
            id=dictation.id, author_user_id=recording.user_id, pool_id=dictation.pool_id,
            status=DRAFT, created_at=moment, audio_seconds=int(recording.audio_duration_seconds or 0)))
    row.dictation_id = dictation.id
    outcome = 'no_text'
    if _has_transcript(recording):
        from src.clinic.correction import ingest_asr_result
        try:
            with db.session.begin_nested():
                ingest_asr_result(dictation, _asr_text(recording), recording.transcription, MODEL_REF)
            outcome = 'text'
        except Exception as error:
            logger.warning('legacy backfill: ASR text of recording %s not stored (%s)',
                           recording.id, type(error).__name__)
            outcome = 'asr_failed'
    return outcome


def apply(storage=None) -> list[Row]:
    """Create the dictations. One transaction per recording; a failure skips only that one."""
    if storage is None:
        from src.services.storage import get_storage_service
        storage = get_storage_service()
    rows = plan(storage)
    for row in rows:
        if row.action != CREATE:
            continue
        recording = db.session.get(Recording, row.recording_id)
        try:
            outcome = _create(row, recording, storage)
            db.session.commit()
        except IngestError as error:
            db.session.rollback()
            row.action, row.reason, row.dictation_id = SKIP, f'ingest_{error.code}', None
            logger.warning('legacy backfill: recording %s refused (%s)', row.recording_id, error.code)
            continue
        except Exception as error:
            db.session.rollback()
            row.action, row.reason, row.dictation_id = SKIP, 'ingest_failed', None
            logger.error('legacy backfill: recording %s failed (%s)', row.recording_id, type(error).__name__)
            continue
        if outcome == 'asr_failed':
            row.reason = 'created_asr_failed'
        audit.record('recording.edited', recording_id=db.session.get(ClinicDictation, row.dictation_id).recording_id,
                     recording_owner_id=row.owner_id, actor_kind='system',
                     details={'fields': ['clinic_backfill']})
    return rows


def format_row(row: Row) -> str:
    """One output line: ids, action and reason only."""
    line = f'recording={row.recording_id} owner={row.owner_id} action={row.action} reason={row.reason}'
    return line + (f' dictation={row.dictation_id}' if row.dictation_id else '')
