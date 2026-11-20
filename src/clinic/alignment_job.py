"""The ``clinic_alignment`` job: carry-over alignment on the existing queue.

`request_alignment` checks an exact basis against the current heads, creates one
pending alignment row per basis (operation key) and enqueues one
``ProcessingJob`` through ``JobQueue.enqueue``. `handle` (registered later by the
wiring package) serves the newest pending row of a dictation, marks older pending
rows superseded and computes the result without an engine call.

Carry-over rule: a word keeps its time only when the nearest aligned ancestor
revision holds a valid item with the same word id and the same displayed text, and
the interval is still the same source audio in the target audio revision. Nothing
is interpolated; W10 ``derive_links`` ranges (`ClinicTextAudioLink`) are segment
data and are never read here. Transcript, audio and link rows are never written.

Provenance (source kind, engine, model) is write-once on the row, so it is taken
from the source row found when the request is made; with no source it is
``asr`` / ``none`` / ``none`` and the result is ``failed`` (no valid word).
`request_alignment` commits the pending row before `enqueue` (the queue commits on a
session of its own); `handle` commits after every pass.
"""

from __future__ import annotations

import hashlib
import json

from src.clinic import alignment as _al
from src.clinic import alignment_tokens as _tokens
from src.clinic.alignment import ClinicAlignmentRevision, AlignmentError
from src.clinic.audio_invalidation import is_interval_compatible
from src.clinic.audio_revisions import current_audio_revision, get_revision_edl
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicDictation,
    ClinicTranscriptRevision,
    _utcnow,
)
from src.clinic.revisions import current_revision
from src.database import db
from src.services.job_queue import ClinicJobError, ClinicJobRefused

ALIGNER_VERSION = "carry-1"
JOB_TYPE = "clinic_alignment"
MAX_PASSES = 5

CARRY_OVER = "CARRY_OVER"
AUDIO_CHANGED = "AUDIO_CHANGED"
NO_VERIFIED_TIMING = "NO_VERIFIED_TIMING"
_NO_SOURCE = ("asr", "none", "none")


class AlignmentBaseConflict(AlignmentError):
    """The requested basis is not the current transcript head, audio head and hash."""


class AlignmentUnavailable(AlignmentError):
    """The job could not be queued. The pending row stays; a retry enqueues again."""


def _operation_key(recording_id, audio_revision_id, transcript_revision_id, document_hash,
                   tokenization_version) -> str:
    payload = json.dumps(
        [str(recording_id), audio_revision_id, transcript_revision_id, document_hash,
         tokenization_version, ALIGNER_VERSION],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _lineage(transcript_revision_id):
    """Revision ids from the given revision up its parent chain."""
    seen, current = [], db.session.get(ClinicTranscriptRevision, transcript_revision_id)
    while current is not None and current.id not in seen:
        seen.append(current.id)
        current = (db.session.get(ClinicTranscriptRevision, current.parent_revision_id)
                   if current.parent_revision_id else None)
    return seen


def _find_source(dictation_id, transcript_revision_id, audio_revision_id):
    """Nearest succeeded or partial row on the parent chain, or None.

    The target revision counts only with a different audio revision; the newest
    row of the first revision that has one wins.
    """
    for revision_id in _lineage(transcript_revision_id):
        query = db.session.query(ClinicAlignmentRevision).filter(
            ClinicAlignmentRevision.dictation_id == dictation_id,
            ClinicAlignmentRevision.transcript_revision_id == revision_id,
            ClinicAlignmentRevision.state.in_(("succeeded", "partial")),
        )
        if revision_id == transcript_revision_id:
            query = query.filter(ClinicAlignmentRevision.audio_revision_id != audio_revision_id)
        row = query.order_by(ClinicAlignmentRevision.created_at.desc(),
                             ClinicAlignmentRevision.id.desc()).first()
        if row is not None:
            return row
    return None


def request_alignment(dictation, actor_user_id, *, audio_revision_id, transcript_revision_id,
                      document_hash, queue):
    """Pending row for an exact basis (new or existing) and one queued job."""
    db.session.refresh(dictation, with_for_update=True)
    head = current_revision(dictation)
    audio = current_audio_revision(dictation)
    if head is None or audio is None:
        raise AlignmentBaseConflict("dictation has no transcript or audio head")
    stored = _tokens.tokens_for(dictation, head)
    if (head.id != transcript_revision_id or audio.id != audio_revision_id
            or stored.document_hash != document_hash):
        raise AlignmentBaseConflict("request does not match the current heads")
    key = _operation_key(dictation.recording_id, audio.id, head.id, stored.document_hash,
                         stored.tokenization_version)
    row = db.session.query(ClinicAlignmentRevision).filter_by(
        dictation_id=dictation.id, operation_key=key).first()
    if row is not None and (row.state != _al.STATE_PENDING or row.job_id):
        return row
    if row is None:
        source = _find_source(dictation.id, head.id, audio.id)
        kind, engine, model = (_NO_SOURCE if source is None
                               else (source.source_kind, source.engine, source.model_revision))
        row = ClinicAlignmentRevision(
            dictation_id=dictation.id, audio_revision_id=audio.id, transcript_revision_id=head.id,
            document_hash=stored.document_hash, tokenization_version=stored.tokenization_version,
            state=_al.STATE_PENDING, source_kind=kind, engine=engine, model_revision=model,
            audio_duration_ms=get_revision_edl(dictation, audio).total_duration_ms,
            items=[], quality=_al._zero_quality(), operation_key=key,
            operation_state="pending", created_at=_utcnow(),
        )
        db.session.add(row)
        db.session.flush()
    # The queue works on a session of its own and commits: persist the row first.
    db.session.commit()
    try:
        job_id = queue.enqueue(actor_user_id, dictation.recording_id, JOB_TYPE, {
            "dictation_id": dictation.id,
            "alignment_revision_id": row.id,
            "clinic_operation_key": key,
        })
    except ClinicJobError:
        return row  # an active job of another operation serves this row
    except Exception as error:
        raise AlignmentUnavailable("alignment job could not be queued") from error
    row.job_id = str(job_id)
    db.session.flush()
    return row


def _carry_items(dictation, row, document, source):
    """One item per word token of ``document``; times only from valid source items."""
    carried = {}
    edls = None
    if source is not None:
        carried = {i["word_id"]: i for i in source.items
                   if i["state"] == "valid"}
        if source.audio_revision_id != row.audio_revision_id:
            edls = (
                get_revision_edl(dictation, db.session.get(ClinicAudioRevision, source.audio_revision_id)),
                get_revision_edl(dictation, db.session.get(ClinicAudioRevision, row.audio_revision_id)),
            )
    items = []
    for node in document["nodes"]:
        for index, token in enumerate(node["tokens"]):
            if token["kind"] != "word":
                continue
            item = {
                "word_id": token["token_id"], "text_node_id": node["text_node_id"],
                "token_index": index, "displayed_text": token["text"],
                "state": "unaligned", "start_ms": None, "end_ms": None,
                "reason_code": NO_VERIFIED_TIMING,
            }
            old = carried.get(token["token_id"])
            if old is not None and old["displayed_text"] == token["text"]:
                if edls is None or is_interval_compatible(edls[0], edls[1], old["start_ms"], old["end_ms"]):
                    item.update(state="valid", start_ms=old["start_ms"], end_ms=old["end_ms"],
                                reason_code=None)
                else:
                    item.update(state="stale", reason_code=AUDIO_CHANGED)
            items.append(item)
    return items


def _compute(dictation, row):
    revision = db.session.get(ClinicTranscriptRevision, row.transcript_revision_id)
    document = _tokens.tokens_for(dictation, revision).document
    source = _find_source(dictation.id, row.transcript_revision_id, row.audio_revision_id)
    _al.complete(row, _carry_items(dictation, row, document, source), [CARRY_OVER])
    head = current_revision(dictation)
    audio = current_audio_revision(dictation)
    if (head is None or audio is None or head.id != row.transcript_revision_id
            or audio.id != row.audio_revision_id
            or _tokens.tokens_for(dictation, head).document_hash != row.document_hash):
        row.operation_state = "superseded"  # result stays as history, inactive
        db.session.flush()


def _pending(dictation_id):
    return (db.session.query(ClinicAlignmentRevision)
            .filter_by(dictation_id=dictation_id, state=_al.STATE_PENDING)
            .order_by(ClinicAlignmentRevision.created_at.desc(), ClinicAlignmentRevision.id.desc())
            .all())


def handle(params, correlation_id):
    """Queue handler: compute the newest pending row, supersede older ones."""
    params = params if isinstance(params, dict) else {}
    dictation = db.session.get(ClinicDictation, params.get("dictation_id"))
    if dictation is None:
        raise ClinicJobRefused(f"[{correlation_id}] dictation not found")
    named = db.session.get(ClinicAlignmentRevision, params.get("alignment_revision_id"))
    if named is None or named.dictation_id != dictation.id:
        raise ClinicJobRefused(f"[{correlation_id}] alignment row not found")
    for _ in range(MAX_PASSES):
        pending = _pending(dictation.id)
        if not pending:
            break
        for older in pending[1:]:
            _al.mark_stale(older, "superseded")
        _compute(dictation, pending[0])
        db.session.commit()
