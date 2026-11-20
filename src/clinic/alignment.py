"""Persistent alignment revisions (contract 2.0 ``AlignmentRevision``).

One row binds a dictation, an audio revision and a transcript revision (same
dictation, enforced by composite foreign keys) to the word timings of that
exact pair. A row is written once: ``pending`` goes to one terminal state
through ``complete``, a pending or terminal row goes to ``stale`` through
``mark_stale``. Every other update of a terminal row is rejected.

The whole items payload is validated against the bound token document
(``alignment_tokens``). A single violation makes the whole payload invalid;
nothing of a bad payload is reused. Quality is always computed here.

Non-scope: no routes, no job, no engine. W10 ``derive_links`` ranges are
segment data and never become word timings here. Helpers flush, never commit.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timezone

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, event, inspect

from src.clinic import alignment_tokens as _tokens
from src.clinic.audio_revisions import get_revision_edl
from src.clinic.models import (
    ClinicDictation,
    ClinicTranscriptRevision,
    _created_at,
    _in_check,
    _utcnow,
    _uuid,
)
from src.database import db

SCHEMA_VERSION = "2.0"
TIME_UNIT = "ms"

STATE_PENDING = "pending"
STATE_STALE = "stale"
STATES = ("pending", "succeeded", "partial", "failed", "stale")
SOURCE_KINDS = ("asr", "forced_alignment", "manual_verified")  # never demo in product rows
OPERATION_STATES = ("pending", "succeeded", "failed", "superseded")
ITEM_STATES = ("valid", "unaligned", "stale")

CONTRACT_KEYS = frozenset({
    "schema_version", "recording_id", "audio_revision_id", "transcript_revision_id",
    "alignment_revision_id", "document_hash", "time_unit", "audio_duration_ms", "state",
    "source", "created_at", "items", "quality",
})
OPERATION_KEYS = frozenset({
    "operation_id", "operation_type", "recording_id", "state", "job_id",
    "result_transcript_revision_id", "result_alignment_revision_id",
    "request_fingerprint", "created_at",
})
ITEM_KEYS = frozenset({
    "word_id", "text_node_id", "token_index", "displayed_text", "state",
    "start_ms", "end_ms", "reason_code",
})

_CODE = re.compile(r"[A-Z][A-Z0-9_]*")
# Binding columns never change after the row exists.
_BINDING = (
    "dictation_id", "audio_revision_id", "transcript_revision_id", "document_hash",
    "tokenization_version", "source_kind", "engine", "model_revision",
    "audio_duration_ms", "operation_key", "created_at",
)
_RESULT = ("items", "quality", "completed_at")


class AlignmentError(ValueError):
    """The request was rejected before it changed the session."""


class AlignmentStateError(AlignmentError):
    """The requested state change is not allowed from the current state."""


class AlignmentImmutable(AlignmentError):
    """A terminal alignment row cannot be changed other than to stale."""


class AlignmentInvalid(AlignmentError):
    """The whole items payload violates the contract or the bound document."""


class ClinicAlignmentRevision(db.Model):
    """Word timings of one transcript revision on one audio revision."""

    __tablename__ = "clinic_alignment_revision"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    audio_revision_id = db.Column(db.String(36), nullable=False)
    transcript_revision_id = db.Column(db.String(36), nullable=False)
    document_hash = db.Column(db.String(64), nullable=False)
    tokenization_version = db.Column(db.String(32), nullable=False)
    state = db.Column(db.String(16), nullable=False, default=STATE_PENDING)
    source_kind = db.Column(db.String(32), nullable=False)
    engine = db.Column(db.String(255), nullable=False)
    model_revision = db.Column(db.String(255), nullable=False)
    audio_duration_ms = db.Column(db.Integer, nullable=False)
    items = db.Column(db.JSON, nullable=False, default=list)
    quality = db.Column(db.JSON, nullable=False)
    operation_key = db.Column(db.String(64), nullable=False)
    operation_state = db.Column(db.String(16), nullable=False, default="pending")
    job_id = db.Column(db.String(64), nullable=True)
    created_at = _created_at()
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_alignment_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id"],
            ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id"],
            name="fk_clinic_alignment_transcript_same_dictation",
        ),
        ForeignKeyConstraint(
            ["dictation_id", "audio_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
            name="fk_clinic_alignment_audio_same_dictation",
        ),
        UniqueConstraint("dictation_id", "operation_key", name="uq_clinic_alignment_operation"),
        CheckConstraint("audio_duration_ms > 0", name="ck_clinic_alignment_duration"),
        _in_check("state", STATES, "ck_clinic_alignment_state"),
        _in_check("source_kind", SOURCE_KINDS, "ck_clinic_alignment_source"),
        _in_check("operation_state", OPERATION_STATES, "ck_clinic_alignment_operation_state"),
    )


def _history(target, name):
    return inspect(target).attrs[name].history


def _guard_update(_mapper, _connection, target):
    changed = {n for n in _BINDING + _RESULT + ("state", "operation_state")
               if _history(target, n).has_changes()}
    # A JSON value assigned equal content still reports a change; compare content.
    for name in ("items", "quality"):
        hist = _history(target, name)
        if hist.has_changes() and hist.deleted and hist.added and hist.deleted[0] == hist.added[0]:
            changed.discard(name)
    state_hist = _history(target, "state")
    old_state = state_hist.deleted[0] if state_hist.deleted else target.state
    binding = changed & set(_BINDING)
    if binding:
        raise AlignmentImmutable("alignment binding is write-once: " + ", ".join(sorted(binding)))
    if old_state == STATE_STALE and changed - {"operation_state"}:
        raise AlignmentImmutable("a stale alignment row cannot change")
    if old_state == STATE_PENDING:
        return  # complete() or mark_stale(); both are checked in Python
    if changed & set(_RESULT):
        raise AlignmentImmutable("a terminal alignment row keeps its items and quality")
    if "state" in changed and target.state != STATE_STALE:
        raise AlignmentImmutable("a terminal alignment row can only become stale")


event.listen(ClinicAlignmentRevision, "before_update", _guard_update)


def create_tables(bind=None) -> None:
    """Create both tables. Safe to repeat."""
    _tokens.create_tables(bind)
    ClinicAlignmentRevision.__table__.create(bind if bind is not None else db.engine, checkfirst=True)


def _zero_quality(warnings=()) -> dict:
    return {
        "valid_words": 0, "unaligned_words": 0, "stale_words": 0, "coverage": 0,
        "warnings": sorted(set(warnings)),
    }


def _operation_key(dictation_id, audio_revision_id, transcript_revision_id, document_hash,
                   tokenization_version, source_kind, engine, model_revision, duration) -> str:
    payload = json.dumps(
        [dictation_id, audio_revision_id, transcript_revision_id, document_hash,
         tokenization_version, source_kind, engine, model_revision, duration],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_pending(dictation, audio_revision, transcript_revision, *, source_kind, engine,
                   model_revision, audio_duration_ms=None) -> ClinicAlignmentRevision:
    """New pending row, or the existing row with the same operation key."""
    if source_kind not in SOURCE_KINDS:
        raise AlignmentError("source_kind must be one of " + ", ".join(SOURCE_KINDS))
    if not engine or not model_revision:
        raise AlignmentError("engine and model_revision are required")
    if audio_revision.dictation_id != dictation.id or transcript_revision.dictation_id != dictation.id:
        raise AlignmentError("revision belongs to another dictation")
    if audio_duration_ms is None:
        audio_duration_ms = get_revision_edl(dictation, audio_revision).total_duration_ms
    if type(audio_duration_ms) is not int or audio_duration_ms <= 0:
        raise AlignmentError("audio_duration_ms must be a positive integer")
    tokens = _tokens.tokens_for(dictation, transcript_revision)
    key = _operation_key(
        dictation.id, audio_revision.id, transcript_revision.id, tokens.document_hash,
        tokens.tokenization_version, source_kind, engine, model_revision, audio_duration_ms,
    )
    existing = db.session.query(ClinicAlignmentRevision).filter_by(
        dictation_id=dictation.id, operation_key=key).first()
    if existing is not None:
        return existing
    row = ClinicAlignmentRevision(
        dictation_id=dictation.id, audio_revision_id=audio_revision.id,
        transcript_revision_id=transcript_revision.id, document_hash=tokens.document_hash,
        tokenization_version=tokens.tokenization_version, state=STATE_PENDING,
        source_kind=source_kind, engine=engine, model_revision=model_revision,
        audio_duration_ms=audio_duration_ms, items=[], quality=_zero_quality(),
        operation_key=key, operation_state="pending", created_at=_utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _word_tokens(document: dict) -> list:
    words = []
    for node in document["nodes"]:
        for index, token in enumerate(node["tokens"]):
            if token["kind"] == "word":
                words.append((token["token_id"], node["text_node_id"], index, token["text"]))
    return words


def _separator_ids(document: dict) -> set:
    return {t["token_id"] for n in document["nodes"] for t in n["tokens"] if t["kind"] != "word"}


def _is_int(value) -> bool:
    return type(value) is int


def validate_items(document: dict, items, audio_duration_ms: int) -> list:
    """Return the canonical items or raise ``AlignmentInvalid`` for the whole payload."""
    if not isinstance(items, (list, tuple)):
        raise AlignmentInvalid("items must be a list")
    words = _word_tokens(document)
    by_id = {w[0]: w for w in words}
    separators = _separator_ids(document)
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != ITEM_KEYS:
            raise AlignmentInvalid("item keys differ from the contract")
        word_id = item["word_id"]
        if not isinstance(word_id, str):
            raise AlignmentInvalid("word_id must be a string")
        if word_id in seen:
            raise AlignmentInvalid("duplicate word_id")
        seen.add(word_id)
        if word_id in separators:
            raise AlignmentInvalid("separator referenced")
        if word_id not in by_id:
            raise AlignmentInvalid("unknown word_id")
    if len(items) != len(words):
        raise AlignmentInvalid("a word token has no item")
    previous_end = 0
    for item, (word_id, node_id, index, text) in zip(items, words):
        if item["word_id"] != word_id:
            raise AlignmentInvalid("items are not in document order")
        if item["text_node_id"] != node_id:
            raise AlignmentInvalid("text_node_id differs from the token's node")
        if not _is_int(item["token_index"]) or item["token_index"] != index:
            raise AlignmentInvalid("token_index differs from the token's index")
        if item["displayed_text"] != text:
            raise AlignmentInvalid("displayed_text differs from the token text")
        start, end, reason = item["start_ms"], item["end_ms"], item["reason_code"]
        if item["state"] == "valid":
            if not (_is_int(start) and _is_int(end)):
                raise AlignmentInvalid("a valid item needs integer start_ms and end_ms")
            if not 0 <= start < end <= audio_duration_ms:
                raise AlignmentInvalid("times are outside 0 <= start < end <= duration")
            if reason is not None:
                raise AlignmentInvalid("a valid item has no reason_code")
            if start < previous_end:
                raise AlignmentInvalid("valid items overlap")
            previous_end = end
        elif item["state"] in ITEM_STATES:
            if start is not None or end is not None:
                raise AlignmentInvalid("a non-valid item has no times")
            if not isinstance(reason, str) or not _CODE.fullmatch(reason):
                raise AlignmentInvalid("a non-valid item needs a reason_code")
        else:
            raise AlignmentInvalid("unknown item state")
    return [{k: item[k] for k in sorted(ITEM_KEYS)} for item in items]


def compute_quality(document: dict, items, warnings=()) -> dict:
    """Counts, coverage and sorted unique warnings; always derived from items."""
    total = len(_word_tokens(document))
    count = lambda state: sum(1 for i in items if i["state"] == state)  # noqa: E731
    valid = count("valid")
    return {
        "valid_words": valid,
        "unaligned_words": count("unaligned"),
        "stale_words": count("stale"),
        "coverage": (valid / total) if total else 0,
        "warnings": sorted(set(warnings)),
    }


def _document_of(row) -> dict:
    revision = db.session.get(ClinicTranscriptRevision, row.transcript_revision_id)
    dictation = db.session.get(ClinicDictation, row.dictation_id)
    stored = _tokens.tokens_for(dictation, revision)
    if stored.document_hash != row.document_hash:
        raise AlignmentError("token document no longer matches the row")
    return stored.document


def complete(row, items, warnings=()) -> ClinicAlignmentRevision:
    """Pending to succeeded, partial or failed. Only once."""
    if row.state != STATE_PENDING:
        raise AlignmentStateError("only a pending alignment can complete")
    warnings = list(warnings)
    if any(not isinstance(w, str) or not _CODE.fullmatch(w) for w in warnings):
        raise AlignmentError("warnings must match ^[A-Z][A-Z0-9_]*$")
    document = _document_of(row)
    try:
        clean = validate_items(document, items, row.audio_duration_ms)
    except AlignmentInvalid:
        clean = None
        warnings.append("INVALID_PAYLOAD")
    if clean is not None:
        valid = sum(1 for i in clean if i["state"] == "valid")
        if valid == 0:
            clean = None
            warnings.append("NO_VALID_WORDS")
        elif valid < len(clean):
            warnings.append("PARTIAL_ALIGNMENT")
    if clean is None:
        state, clean = "failed", []
    else:
        state = "succeeded" if all(i["state"] == "valid" for i in clean) else "partial"
    row.items = clean
    row.quality = compute_quality(document, clean, warnings)
    row.state = state
    row.operation_state = "failed" if state == "failed" else "succeeded"
    row.completed_at = _utcnow()
    db.session.flush()
    return row


def mark_stale(row, operation_state="superseded") -> ClinicAlignmentRevision:
    """Pending or terminal to stale. Items and quality stay as stored."""
    if row.state == STATE_STALE:
        raise AlignmentStateError("the alignment is already stale")
    if operation_state not in OPERATION_STATES:
        raise AlignmentError("unknown operation_state")
    row.state = STATE_STALE
    row.operation_state = operation_state
    db.session.flush()
    return row


def _iso(value) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recording_id(row) -> str:
    return str(db.session.get(ClinicDictation, row.dictation_id).recording_id)


def to_contract(row, state=None) -> dict:
    """Exactly the contract's ``AlignmentRevision`` keys."""
    reported = row.state if state is None else state
    if reported not in STATES:
        raise AlignmentError("unknown state")
    return {
        "schema_version": SCHEMA_VERSION,
        "recording_id": _recording_id(row),
        "audio_revision_id": row.audio_revision_id,
        "transcript_revision_id": row.transcript_revision_id,
        "alignment_revision_id": row.id,
        "document_hash": row.document_hash,
        "time_unit": TIME_UNIT,
        "audio_duration_ms": row.audio_duration_ms,
        "state": reported,
        "source": {
            "kind": row.source_kind, "engine": row.engine,
            "model_revision": row.model_revision,
            "tokenization_version": row.tokenization_version,
        },
        "created_at": _iso(row.created_at),
        "items": [dict(i) for i in (row.items or [])],
        "quality": dict(row.quality),
    }


def to_operation(row) -> dict:
    """Exactly the contract's ``JobOperation`` keys."""
    return {
        "operation_id": row.id,
        "operation_type": "alignment",
        "recording_id": _recording_id(row),
        "state": row.operation_state,
        "job_id": row.job_id,
        "result_transcript_revision_id": None,
        "result_alignment_revision_id": None if row.operation_state == "pending" else row.id,
        "request_fingerprint": row.operation_key,
        "created_at": _iso(row.created_at),
    }


def get_exact(dictation_id, audio_revision_id, transcript_revision_id):
    """Newest row of this exact binding, or None."""
    return (
        db.session.query(ClinicAlignmentRevision)
        .filter_by(dictation_id=dictation_id, audio_revision_id=audio_revision_id,
                   transcript_revision_id=transcript_revision_id)
        .order_by(ClinicAlignmentRevision.created_at.desc(), ClinicAlignmentRevision.id.desc())
        .first()
    )


def read_for(dictation_id, audio_revision_id, transcript_revision_id):
    """(row, reported_state): the exact row with its own state, else the newest
    result-bearing row of the dictation reported as stale, else None."""
    exact = get_exact(dictation_id, audio_revision_id, transcript_revision_id)
    if exact is not None:
        return exact, exact.state
    older = (
        db.session.query(ClinicAlignmentRevision)
        .filter(ClinicAlignmentRevision.dictation_id == dictation_id,
                ClinicAlignmentRevision.state.in_(("succeeded", "partial", STATE_STALE)))
        .order_by(ClinicAlignmentRevision.created_at.desc(), ClinicAlignmentRevision.id.desc())
        .first()
    )
    return (older, STATE_STALE) if older is not None else None
