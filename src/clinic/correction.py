"""Correction service: section markers, text/audio links, correction state.

All parts are additive and bound to one ``ClinicDictation``.

* Markers sit on an audio revision at a position in ms. ``SECTION`` is a section mark of
  the correction window (label such as "Anamnese"), ``INDEX`` a named audio
  index. A marker may carry a text anchor (transcript revision plus
  character offset). Create, move and delete go to an append-only event log
  with actor and time. Delete is soft, so the log always has its marker.
* Text/audio links tie a character range of one transcript revision to an
  audio range in ms. ``AUTO`` links come from ASR segments, ``MANUAL`` links
  from a person and win over ``AUTO`` on overlapping text. Both stay stored
  and distinguishable. Text no segment covers gets no link; no audio range
  is invented.
* Correction state is the progress of one user on one dictation. ``hand_over``
  copies it to another user. Approval (Abnahme) is a separate workflow and is
  neither stored nor changed here.

Routes, review lease and UI come in a later work package.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from src.clinic import policy
from src.clinic.audio_revisions import AudioRevisionError, get_revision_edl
from src.clinic.config import clinic_mode_enabled
from src.clinic.lease import LeaseConflict, assert_current
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicTranscriptRevision,
    _created_at,
    _in_check,
    _utcnow,
    _uuid,
)
from src.database import db

MARKER_SECTION = "SECTION"
MARKER_INDEX = "INDEX"
MARKER_TYPES = (MARKER_SECTION, MARKER_INDEX)

EVENT_CREATE = "CREATE"
EVENT_MOVE = "MOVE"
EVENT_DELETE = "DELETE"
EVENT_ACTIONS = (EVENT_CREATE, EVENT_MOVE, EVENT_DELETE)
_EVENT_RANK = {EVENT_CREATE: 0, EVENT_MOVE: 1, EVENT_DELETE: 2}

ORIGIN_AUTO = "AUTO"
ORIGIN_MANUAL = "MANUAL"
LINK_ORIGINS = (ORIGIN_AUTO, ORIGIN_MANUAL)

_REV_FK = ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id"]


class CorrectionError(ValueError):
    """The request was rejected before it changed the session."""


class CorrectionConflict(CorrectionError):
    """The stored correction state moved since the caller read it."""


class ClinicMarker(db.Model):
    """Section marker or audio index on one dictation, in audio milliseconds."""

    __tablename__ = "clinic_marker"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    seq = db.Column(db.Integer, nullable=False)
    marker_type = db.Column(db.String(16), nullable=False)
    position_ms = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(200), nullable=True)
    anchor_revision_id = db.Column(db.String(36), nullable=True)
    anchor_offset = db.Column(db.Integer, nullable=True)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_marker_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "anchor_revision_id"], _REV_FK,
            name="fk_clinic_marker_anchor_same_dictation",
        ),
        UniqueConstraint("dictation_id", "seq", name="uq_clinic_marker_seq"),
        UniqueConstraint("dictation_id", "id", name="uq_clinic_marker_dictation"),
        CheckConstraint("position_ms >= 0", name="ck_clinic_marker_position"),
        CheckConstraint(
            "(anchor_revision_id IS NULL) = (anchor_offset IS NULL)",
            name="ck_clinic_marker_anchor_pair",
        ),
        CheckConstraint(
            "anchor_offset IS NULL OR anchor_offset >= 0", name="ck_clinic_marker_anchor_offset"
        ),
        _in_check("marker_type", MARKER_TYPES, "ck_clinic_marker_type"),
    )

    @property
    def audio_revision_id(self) -> str | None:
        """None means unaligned: legacy rows contain no audio revision provenance."""
        return _marker_audio_revision_id(self.id)

    @property
    def version(self) -> int:
        """Number of committed or transaction-local marker events."""
        return db.session.scalar(_marker_version(self.id))


class ClinicMarkerAudioBinding(db.Model):
    """Write-once binding shared by the marker and its entire event history.

    An additive table lets existing installations keep their marker and event
    rows intact. Neither a text anchor nor the current audio head proves which
    audio timeline a legacy marker was placed on.
    """

    __tablename__ = "clinic_marker_audio_binding"

    marker_id = db.Column(db.String(36), primary_key=True)
    dictation_id = db.Column(db.String(36), nullable=False)
    audio_revision_id = db.Column(db.String(36), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id", "marker_id"], ["clinic_marker.dictation_id", "clinic_marker.id"],
            name="fk_clinic_marker_binding_marker",
        ),
        ForeignKeyConstraint(
            ["dictation_id", "audio_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
            name="fk_clinic_marker_binding_audio",
        ),
    )


class ClinicMarkerEvent(db.Model):
    """Append-only trace of marker changes: who, when, from where to where."""

    __tablename__ = "clinic_marker_event"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    marker_id = db.Column(db.String(36), nullable=False, index=True)
    action = db.Column(db.String(16), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    old_position_ms = db.Column(db.Integer, nullable=True)
    new_position_ms = db.Column(db.Integer, nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id", "marker_id"],
            ["clinic_marker.dictation_id", "clinic_marker.id"],
            name="fk_clinic_marker_event_marker_same_dictation",
        ),
        _in_check("action", EVENT_ACTIONS, "ck_clinic_marker_event_action"),
    )

    @property
    def audio_revision_id(self) -> str | None:
        return _marker_audio_revision_id(self.marker_id)


class ClinicTextAudioLink(db.Model):
    """Character range of a transcript revision tied to an audio range in ms."""

    __tablename__ = "clinic_text_audio_link"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    transcript_revision_id = db.Column(db.String(36), nullable=False, index=True)
    seq = db.Column(db.Integer, nullable=False)
    origin = db.Column(db.String(16), nullable=False)
    text_start = db.Column(db.Integer, nullable=False)
    text_end = db.Column(db.Integer, nullable=False)
    audio_start_ms = db.Column(db.Integer, nullable=False)
    audio_end_ms = db.Column(db.Integer, nullable=False)
    # Set for MANUAL rows only.
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id"], _REV_FK,
            name="fk_clinic_link_revision_same_dictation",
        ),
        UniqueConstraint("dictation_id", "seq", name="uq_clinic_link_seq"),
        CheckConstraint("text_start >= 0 AND text_end > text_start", name="ck_clinic_link_text"),
        CheckConstraint(
            "audio_start_ms >= 0 AND audio_end_ms >= audio_start_ms", name="ck_clinic_link_audio"
        ),
        CheckConstraint(
            "origin <> 'MANUAL' OR actor_user_id IS NOT NULL", name="ck_clinic_link_manual_actor"
        ),
        _in_check("origin", LINK_ORIGINS, "ck_clinic_link_origin"),
    )


class ClinicCorrectionState(db.Model):
    """Progress of one user on one dictation. ``version`` guards stale writes."""

    __tablename__ = "clinic_correction_state"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    audio_position_ms = db.Column(db.Integer, nullable=False, default=0)
    text_revision_id = db.Column(db.String(36), nullable=True)
    text_offset = db.Column(db.Integer, nullable=True)
    # JSON list of section marker ids the user has finished.
    done_section_ids = db.Column(db.Text, nullable=False, default="[]")
    version = db.Column(db.Integer, nullable=False, default=1)
    handed_over_from_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_state_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "text_revision_id"], _REV_FK,
            name="fk_clinic_state_revision_same_dictation",
        ),
        UniqueConstraint("dictation_id", "user_id", name="uq_clinic_state_dictation_user"),
        CheckConstraint("audio_position_ms >= 0", name="ck_clinic_state_position"),
        CheckConstraint(
            "(text_revision_id IS NULL) = (text_offset IS NULL)", name="ck_clinic_state_text_pair"
        ),
    )


# ---------------------------------------------------------------- markers


def add_marker(
    dictation: ClinicDictation,
    position_ms: int,
    actor_user_id: int,
    *,
    marker_type: str = MARKER_SECTION,
    label: str | None = None,
    anchor_revision: ClinicTranscriptRevision | None = None,
    anchor_offset: int | None = None,
    audio_revision_id: str | None = None,
    lease_generation: int | None = None,
) -> ClinicMarker:
    """Create a marker and log it. The caller commits."""
    _check_dictation(dictation)
    _require_user(actor_user_id)
    _check_ms(position_ms, "position_ms")
    if clinic_mode_enabled() or audio_revision_id is not None:
        _require_marker_write(dictation, actor_user_id, lease_generation)
        _check_audio_position(dictation, audio_revision_id, position_ms)
    if marker_type not in MARKER_TYPES:
        raise CorrectionError("unknown marker_type")
    if (anchor_revision is None) != (anchor_offset is None):
        raise CorrectionError("anchor_revision and anchor_offset go together")
    if anchor_revision is not None:
        _check_revision(dictation, anchor_revision)
        _check_offset(anchor_offset, "anchor_offset", len(anchor_revision.body))
    marker = ClinicMarker(
        dictation_id=dictation.id,
        seq=_next_seq(ClinicMarker, dictation.id),
        marker_type=marker_type,
        position_ms=position_ms,
        label=label,
        anchor_revision_id=None if anchor_revision is None else anchor_revision.id,
        anchor_offset=anchor_offset,
    )
    db.session.add(marker)
    db.session.flush()
    if audio_revision_id is not None:
        db.session.add(ClinicMarkerAudioBinding(
            marker_id=marker.id, dictation_id=dictation.id, audio_revision_id=audio_revision_id,
        ))
    _log(marker, EVENT_CREATE, actor_user_id, None, position_ms)
    return marker


def move_marker(
    marker: ClinicMarker, new_position_ms: int, actor_user_id: int, *,
    expected_version: int | None = None, lease_generation: int | None = None,
) -> ClinicMarker:
    """Move a live marker to another audio position and log old and new."""
    _check_ms(new_position_ms, "new_position_ms")
    return _change_marker(marker, actor_user_id, expected_version, lease_generation, new_position_ms)


def delete_marker(
    marker: ClinicMarker, actor_user_id: int, *,
    expected_version: int | None = None, lease_generation: int | None = None,
) -> ClinicMarker:
    """Soft delete. The row and its events stay; the marker leaves the list."""
    return _change_marker(marker, actor_user_id, expected_version, lease_generation, None)


def _change_marker(
    marker: ClinicMarker, actor_user_id: int, expected_version: int | None,
    lease_generation: int | None, position_ms: int | None,
) -> ClinicMarker:
    if not isinstance(marker, ClinicMarker) or marker.id is None:
        raise CorrectionError("marker must be a persisted ClinicMarker")
    _require_user(actor_user_id)
    changes = {"deleted_at": _utcnow()} if position_ms is None else {"position_ms": position_ms}
    if clinic_mode_enabled() or marker.audio_revision_id is not None:
        dictation = db.session.get(ClinicDictation, marker.dictation_id)
        if dictation is None:
            raise CorrectionError("marker dictation does not exist")
        _require_marker_write(dictation, actor_user_id, lease_generation)
        db.session.refresh(marker)
        if (
            type(expected_version) is not int
            or marker.deleted_at is not None
            or marker.version != expected_version
        ):
            raise CorrectionConflict("marker changed since it was read")
        if position_ms is not None:
            _check_audio_position(dictation, marker.audio_revision_id, position_ms)
        old = marker.position_ms
        updated = db.session.execute(db.update(ClinicMarker).where(
            ClinicMarker.id == marker.id, ClinicMarker.deleted_at.is_(None),
            _marker_version(marker.id).scalar_subquery() == expected_version,
        ).values(**changes).execution_options(synchronize_session=False))
        if updated.rowcount != 1:
            raise CorrectionConflict("marker changed since it was read")
        db.session.refresh(marker)
    else:
        _check_marker(marker)
        old = marker.position_ms
        for name, value in changes.items():
            setattr(marker, name, value)
    _log(marker, EVENT_DELETE if position_ms is None else EVENT_MOVE, actor_user_id, old, position_ms)
    return marker


def list_markers(dictation: ClinicDictation, marker_type: str | None = None) -> list[ClinicMarker]:
    """Live markers in audio order. Equal positions keep creation order."""
    _check_dictation(dictation)
    query = db.session.query(ClinicMarker).filter(
        ClinicMarker.dictation_id == dictation.id, ClinicMarker.deleted_at.is_(None)
    )
    if marker_type is not None:
        query = query.filter(ClinicMarker.marker_type == marker_type)
    return query.order_by(ClinicMarker.position_ms, ClinicMarker.seq).all()


def marker_events(marker: ClinicMarker) -> list[ClinicMarkerEvent]:
    """Change trace of one marker, oldest first (also for a deleted marker)."""
    if not isinstance(marker, ClinicMarker) or marker.id is None:
        raise CorrectionError("marker must be a persisted ClinicMarker")
    rows = db.session.query(ClinicMarkerEvent).filter_by(marker_id=marker.id).all()
    return sorted(rows, key=lambda row: (row.created_at, _EVENT_RANK[row.action]))


# ----------------------------------------------------- text/audio links


def link_asr_segments(
    dictation: ClinicDictation, revision: ClinicTranscriptRevision, segments
) -> list[ClinicTextAudioLink]:
    """Store AUTO links for the segments of an ASR revision, once.

    Segments are dicts (``start``/``end`` or ``start_time``/``end_time`` in
    seconds) or objects with those attributes. Each segment text is searched
    in the revision body, left to right. A segment that is not found or has
    no times gets no link. A second call returns the stored AUTO links.
    """
    from src.clinic.revisions import normalise_text

    _check_dictation(dictation)
    _check_revision(dictation, revision)
    stored = _links(dictation.id, revision.id, ORIGIN_AUTO)
    if stored:
        return stored
    cursor = 0
    for raw in segments:
        parsed = _segment(raw)
        if parsed is None:
            continue
        text, start, end = parsed
        text = normalise_text(text)
        at = revision.body.find(text, cursor)
        if at < 0:
            continue
        cursor = at + len(text)
        db.session.add(
            ClinicTextAudioLink(
                dictation_id=dictation.id,
                transcript_revision_id=revision.id,
                seq=_next_seq(ClinicTextAudioLink, dictation.id),
                origin=ORIGIN_AUTO,
                text_start=at,
                text_end=cursor,
                audio_start_ms=round(start * 1000),
                audio_end_ms=round(end * 1000),
            )
        )
        db.session.flush()
    return _links(dictation.id, revision.id, ORIGIN_AUTO)


def ingest_asr_result(
    dictation: ClinicDictation, text: str, stored_transcription, model_ref: str,
    *, expected_asr_revision_id: str | None = None,
    recognition_segments=None, recognition_run_id: str | None = None, verified_repeat_audio=False,
) -> list[ClinicTextAudioLink]:
    """CLINIC_MODE ingest step: link the segments of a finished ASR run.

    No revision yet: the ASR text becomes the first ASR revision. An ASR head
    with the same text is refreshed. A human head gets the result as a side
    revision. A changed ASR head requires the explicit repeat's current revision
    binding. Any other head is left alone. Links come only from segments that
    carry times; plain text or untimed segments give no links. Repeats add
    nothing.
    """
    from src.clinic.revisions import KIND_ASR, KIND_HUMAN, add_asr_revision, current_revision, normalise_text
    from src.services.transcript_segments import segments_of

    _check_dictation(dictation)
    if not isinstance(text, str) or text.strip() == "":
        return []
    head = current_revision(dictation)
    if head is None:
        original = (
            db.session.query(ClinicOriginal)
            .filter_by(dictation_id=dictation.id)
            .order_by(ClinicOriginal.created_at, ClinicOriginal.id)
            .first()
        )
    elif head.kind == KIND_HUMAN:
        original = db.session.get(ClinicOriginal, head.original_version_id)
    elif head.kind == KIND_ASR and (
        head.body == normalise_text(text) or head.id == expected_asr_revision_id
    ):
        original = db.session.get(ClinicOriginal, head.original_version_id)
    else:
        return []
    if original is None:
        return []
    revision = add_asr_revision(dictation, original, text, model_ref)
    links = link_asr_segments(dictation, revision, segments_of(stored_transcription) or [])
    if recognition_run_id and revision.kind == KIND_ASR:
        from src.clinic.correction_recognition_marks import store_result

        measurements = None if recognition_segments is None else tuple(
            dict(segment) if isinstance(segment, Mapping) else vars(segment)
            for segment in recognition_segments
        )
        store_result(dictation, original, revision, run_id=recognition_run_id, model_ref=model_ref,
                     segments=measurements, verified_repeat_audio=verified_repeat_audio)
    # Word timings are a side product: a failure here never drops the revision or the links.
    try:
        with db.session.begin_nested():
            from src.clinic.alignment_ingest import store_asr_alignment

            store_asr_alignment(dictation, revision, stored_transcription, model_ref)
    except Exception as err:
        import logging

        from src.utils.error_text import log_failure

        log_failure(logging.getLogger(__name__), logging.WARNING,
                    f"ASR word timings of dictation {dictation.id} not stored", err)
    return links


def set_manual_link(
    dictation: ClinicDictation,
    revision: ClinicTranscriptRevision,
    text_start: int,
    text_end: int,
    audio_start_ms: int,
    audio_end_ms: int,
    actor_user_id: int,
) -> ClinicTextAudioLink:
    """Store a MANUAL link. It overrides AUTO and older MANUAL links on its text."""
    _check_dictation(dictation)
    _check_revision(dictation, revision)
    _require_user(actor_user_id)
    _check_offset(text_start, "text_start", len(revision.body))
    _check_offset(text_end, "text_end", len(revision.body))
    if text_end <= text_start:
        raise CorrectionError("text range is empty")
    _check_ms(audio_start_ms, "audio_start_ms")
    _check_ms(audio_end_ms, "audio_end_ms")
    if audio_end_ms < audio_start_ms:
        raise CorrectionError("audio range is reversed")
    link = ClinicTextAudioLink(
        dictation_id=dictation.id,
        transcript_revision_id=revision.id,
        seq=_next_seq(ClinicTextAudioLink, dictation.id),
        origin=ORIGIN_MANUAL,
        text_start=text_start,
        text_end=text_end,
        audio_start_ms=audio_start_ms,
        audio_end_ms=audio_end_ms,
        actor_user_id=actor_user_id,
    )
    db.session.add(link)
    db.session.flush()
    return link


def effective_links(
    dictation: ClinicDictation, revision: ClinicTranscriptRevision
) -> list[ClinicTextAudioLink]:
    """Links in force, ordered by text start.

    A link is dropped when a later MANUAL link overlaps its text range.
    AUTO links never drop anything.
    """
    _check_dictation(dictation)
    _check_revision(dictation, revision)
    rows = _links(dictation.id, revision.id)
    manual = [row for row in rows if row.origin == ORIGIN_MANUAL]
    kept = [
        row for row in rows
        if not any(o.seq > row.seq and _overlap(row, o) for o in manual)
    ]
    return sorted(kept, key=lambda row: (row.text_start, row.seq))


def link_at_text(
    dictation: ClinicDictation, revision: ClinicTranscriptRevision, offset: int
) -> ClinicTextAudioLink | None:
    """Effective link that covers a character offset, or None."""
    for row in effective_links(dictation, revision):
        if row.text_start <= offset < row.text_end:
            return row
    return None


# --------------------------------------------------- correction state


def get_state(dictation: ClinicDictation, user_id: int) -> ClinicCorrectionState | None:
    _check_dictation(dictation)
    return (
        db.session.query(ClinicCorrectionState)
        .filter_by(dictation_id=dictation.id, user_id=user_id)
        .one_or_none()
    )


def save_state(
    dictation: ClinicDictation,
    user_id: int,
    audio_position_ms: int,
    *,
    text_revision: ClinicTranscriptRevision | None = None,
    text_offset: int | None = None,
    expected_version: int | None = None,
) -> ClinicCorrectionState:
    """Store the last audio and text position of a user.

    ``expected_version`` is the version the caller read (None for a first
    save). A different stored version raises ``CorrectionConflict``; the
    state is never overwritten silently.

    This flushes without committing. A revision flushed in the same session
    can be used as the text anchor; the caller commits or rolls back both writes.
    """
    _check_dictation(dictation)
    _require_user(user_id)
    _check_ms(audio_position_ms, "audio_position_ms")
    if (text_revision is None) != (text_offset is None):
        raise CorrectionError("text_revision and text_offset go together")
    if text_revision is not None:
        _check_revision(dictation, text_revision)
        _check_offset(text_offset, "text_offset", len(text_revision.body))
    state = get_state(dictation, user_id)
    if state is None:
        if expected_version is not None:
            raise CorrectionConflict("no stored state for that version")
        state = ClinicCorrectionState(dictation_id=dictation.id, user_id=user_id, version=0)
        db.session.add(state)
    elif expected_version != state.version:
        raise CorrectionConflict("correction state changed since it was read")
    state.audio_position_ms = audio_position_ms
    state.text_revision_id = None if text_revision is None else text_revision.id
    state.text_offset = text_offset
    state.version += 1
    state.updated_at = _utcnow()
    db.session.flush()
    return state


def complete_section(
    dictation: ClinicDictation, user_id: int, marker: ClinicMarker
) -> ClinicCorrectionState:
    """Mark a live section marker as finished for this user."""
    _check_dictation(dictation)
    _require_user(user_id)
    _check_marker(marker)
    if marker.dictation_id != dictation.id or marker.marker_type != MARKER_SECTION:
        raise CorrectionError("marker is not a section of this dictation")
    state = get_state(dictation, user_id) or save_state(dictation, user_id, 0)
    done = json.loads(state.done_section_ids)
    if marker.id not in done:
        state.done_section_ids = json.dumps(done + [marker.id])
        state.version += 1
        state.updated_at = _utcnow()
        db.session.flush()
    return state


def open_sections(dictation: ClinicDictation, user_id: int) -> list[ClinicMarker]:
    """Live section markers the user has not finished, in audio order."""
    state = get_state(dictation, user_id)
    done = set() if state is None else set(json.loads(state.done_section_ids))
    return [m for m in list_markers(dictation, MARKER_SECTION) if m.id not in done]


def hand_over(
    dictation: ClinicDictation, from_user_id: int, to_user_id: int
) -> ClinicCorrectionState:
    """Give the state of one user to another. The source row stays unchanged.

    The receiver's own earlier state is replaced by the copy. No approval
    field is touched; approval is a separate step.
    """
    _check_dictation(dictation)
    _require_user(to_user_id)
    if from_user_id == to_user_id:
        raise CorrectionError("hand over needs two different users")
    source = get_state(dictation, from_user_id)
    if source is None:
        raise CorrectionError("source user has no correction state")
    target = get_state(dictation, to_user_id)
    if target is None:
        target = ClinicCorrectionState(dictation_id=dictation.id, user_id=to_user_id, version=0)
        db.session.add(target)
    target.audio_position_ms = source.audio_position_ms
    target.text_revision_id = source.text_revision_id
    target.text_offset = source.text_offset
    target.done_section_ids = source.done_section_ids
    target.handed_over_from_user_id = from_user_id
    target.version += 1
    target.updated_at = _utcnow()
    db.session.flush()
    return target


# ---------------------------------------------------------- helpers


def _marker_audio_revision_id(marker_id: str) -> str | None:
    binding = db.session.get(ClinicMarkerAudioBinding, marker_id)
    return None if binding is None else binding.audio_revision_id


def _marker_version(marker_id: str):
    return db.select(db.func.count(ClinicMarkerEvent.id)).where(
        ClinicMarkerEvent.marker_id == marker_id,
    )


def _require_marker_write(
    dictation: ClinicDictation, actor_user_id: int, lease_generation: int | None,
) -> None:
    from src.models import Recording

    owner_id = db.session.query(Recording.user_id).filter_by(id=dictation.recording_id).scalar()
    if owner_id is None or not policy.decide(
        policy.build_subject(actor_user_id), "edit", policy.build_facts(dictation.recording_id, owner_id),
    ).allowed:
        raise CorrectionError("marker edit not permitted")
    if type(lease_generation) is not int:
        raise CorrectionConflict("a current review lease is required")
    try:
        assert_current(dictation.id, actor_user_id, lease_generation, _utcnow())
    except LeaseConflict as exc:
        raise CorrectionConflict(exc.reason) from exc


def _check_audio_position(
    dictation: ClinicDictation, audio_revision_id: str | None, position_ms: int,
) -> None:
    if not isinstance(audio_revision_id, str) or not audio_revision_id:
        raise CorrectionError("an explicit audio revision is required")
    revision = db.session.get(ClinicAudioRevision, audio_revision_id)
    if revision is None:
        raise CorrectionError("audio revision is unavailable for this dictation")
    try:
        edl = get_revision_edl(dictation, revision)
    except AudioRevisionError as exc:
        raise CorrectionError("audio revision is unavailable for this dictation") from exc
    if position_ms > edl.total_duration_ms:
        raise CorrectionError("position is outside the audio revision")


def _log(marker, action, actor_user_id, old, new):
    db.session.add(
        ClinicMarkerEvent(
            dictation_id=marker.dictation_id,
            marker_id=marker.id,
            action=action,
            actor_user_id=actor_user_id,
            old_position_ms=old,
            new_position_ms=new,
            created_at=_utcnow(),
        )
    )
    db.session.flush()


def _links(dictation_id, revision_id, origin=None):
    query = db.session.query(ClinicTextAudioLink).filter(
        ClinicTextAudioLink.dictation_id == dictation_id,
        ClinicTextAudioLink.transcript_revision_id == revision_id,
    )
    if origin is not None:
        query = query.filter(ClinicTextAudioLink.origin == origin)
    return query.order_by(ClinicTextAudioLink.seq).all()


def _overlap(a, b):
    return a.text_start < b.text_end and b.text_start < a.text_end


def _next_seq(model, dictation_id):
    top = (
        db.session.query(db.func.max(model.seq))
        .filter(model.dictation_id == dictation_id)
        .scalar()
    )
    return 1 if top is None else top + 1


def _segment(raw):
    """Return ``(stripped text, start s, end s)`` or None when unusable."""
    if isinstance(raw, dict):
        text = raw.get("text")
        start = raw.get("start", raw.get("start_time"))
        end = raw.get("end", raw.get("end_time"))
    else:
        text = getattr(raw, "text", None)
        start = getattr(raw, "start", getattr(raw, "start_time", None))
        end = getattr(raw, "end", getattr(raw, "end_time", None))
    if not isinstance(text, str) or text.strip() == "" or start is None or end is None:
        return None
    if start < 0 or end < start:
        return None
    return text.strip(), float(start), float(end)


def _check_dictation(dictation):
    if not isinstance(dictation, ClinicDictation) or dictation.id is None:
        raise CorrectionError("dictation must be a persisted ClinicDictation")


def _check_revision(dictation, revision):
    if not isinstance(revision, ClinicTranscriptRevision) or revision.id is None:
        raise CorrectionError("revision must be a persisted ClinicTranscriptRevision")
    if revision.dictation_id != dictation.id:
        raise CorrectionError("revision belongs to another dictation")


def _check_marker(marker):
    if not isinstance(marker, ClinicMarker) or marker.id is None:
        raise CorrectionError("marker must be a persisted ClinicMarker")
    if marker.deleted_at is not None:
        raise CorrectionError("marker is deleted")


def _check_ms(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CorrectionError(name + " must be a non-negative int")


def _check_offset(value, name, limit):
    _check_ms(value, name)
    if value > limit:
        raise CorrectionError(name + " is outside the text")


def _require_user(user_id):
    from src.models import User

    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise CorrectionError("user id must be an int")
    if db.session.get(User, user_id) is None:
        raise CorrectionError("user does not exist")
