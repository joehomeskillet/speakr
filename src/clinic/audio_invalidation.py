"""Audio invalidation service: invalidate incompatible text anchors and releases after audio edits.

When an audio revision is modified (or undid/redid/restored):
1. Incompatible text/audio links (whose audio intervals changed) and out-of-bounds
   or shifted marker positions are invalidated via append-only invalidation events.
2. Demonstrably compatible links and anchors are preserved.
3. Active releases bound to older revisions are superseded via invalidate_on_change.
4. The new audio head, invalidation events, and release supersession commit atomically
   under the current review lease generation.
5. All older audio, transcript, marker, and release revisions remain readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, event

from src.clinic import policy
from src.clinic.audio_revisions import (
    ClinicAudioRevision,
    ClinicAudioRevisionSnapshot,
    EditDecisionList,
    OP_CUT,
    OP_INSERT,
    OP_OVERWRITE,
    OP_TRIM,
    apply_cut,
    apply_insert,
    apply_overwrite,
    apply_trim,
    current_audio_revision,
    get_revision_edl,
    redo,
    undo,
)
from src.clinic.correction import (
    ClinicMarker,
    ClinicTextAudioLink,
    effective_links,
)
from src.clinic.lease import assert_current
from src.clinic.models import (
    ClinicDictation,
    ClinicTranscriptRevision,
    _created_at,
    _in_check,
    _uuid,
)
from src.clinic.release import (
    ClinicRelease,
    ClinicReleaseSupersession,
    invalidate_on_change,
)
from src.database import db
from src.models import Recording, User

TARGET_LINK, TARGET_MARKER, TARGET_RELEASE = "LINK", "MARKER", "RELEASE"
TARGET_TYPES = (TARGET_LINK, TARGET_MARKER, TARGET_RELEASE)

REASON_INTERVAL_CHANGED = "interval_changed"
REASON_POSITION_CHANGED = "position_changed"
REASON_OUT_OF_BOUNDS = "out_of_bounds"
REASON_AUDIO_SUPERSEDED = "audio_superseded"


class AudioInvalidationError(ValueError):
    """Base exception for audio invalidation errors."""


class AudioInvalidationConflict(AudioInvalidationError):
    """Concurrent edit or stale version conflict."""


class AudioInvalidationPermissionDenied(AudioInvalidationError):
    """User is not permitted to edit audio."""


class ClinicAudioInvalidationEvent(db.Model):
    """Append-only audit trace of invalidated anchors and releases."""

    __tablename__ = "clinic_audio_invalidation_event"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    audio_revision_id = db.Column(db.String(36), nullable=False, index=True)
    previous_audio_revision_id = db.Column(db.String(36), nullable=True)
    target_type = db.Column(db.String(32), nullable=False)
    target_id = db.Column(db.String(36), nullable=False, index=True)
    reason = db.Column(db.String(80), nullable=False)
    lease_generation = db.Column(db.Integer, nullable=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_inv_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "audio_revision_id"],
            ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"],
            name="fk_clinic_audio_inv_rev_same",
        ),
        _in_check("target_type", TARGET_TYPES, "ck_clinic_audio_inv_target_type"),
    )


def _reject_audio_invalidation_write(_mapper, _connection, _target):
    raise AudioInvalidationError("audio invalidation events are append-only")


event.listen(ClinicAudioInvalidationEvent, "before_update", _reject_audio_invalidation_write)
event.listen(ClinicAudioInvalidationEvent, "before_delete", _reject_audio_invalidation_write)


@dataclass(frozen=True)
class AudioInvalidationResult:
    new_revision: ClinicAudioRevision
    previous_revision: ClinicAudioRevision | None
    invalidation_events: tuple[ClinicAudioInvalidationEvent, ...]
    superseded_releases: tuple[ClinicRelease, ...]
    preserved_link_ids: tuple[str, ...]
    invalidated_link_ids: tuple[str, ...]


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AudioInvalidationError(f"{name} must be timezone-aware datetime")
    return value


def _require_edit_permission(dictation: ClinicDictation, actor_user_id: int) -> None:
    if isinstance(actor_user_id, bool) or not isinstance(actor_user_id, int):
        raise AudioInvalidationError("actor_user_id must be an int")
    if db.session.get(User, actor_user_id) is None:
        raise AudioInvalidationError("user does not exist")
    owner_id = db.session.query(Recording.user_id).filter_by(id=dictation.recording_id).scalar()
    if owner_id is None:
        raise AudioInvalidationError("recording not found for dictation")
    decision = policy.decide(
        policy.build_subject(actor_user_id),
        "edit",
        policy.build_facts(dictation.recording_id, owner_id),
    )
    if not decision.allowed:
        raise AudioInvalidationPermissionDenied("audio edit not permitted")


def _get_baseline_edl(dictation: ClinicDictation) -> EditDecisionList:
    init_snap = (
        db.session.query(ClinicAudioRevisionSnapshot)
        .filter_by(dictation_id=dictation.id, operation_seq=0)
        .first()
    )
    if init_snap is not None:
        init_rev = db.session.get(ClinicAudioRevision, init_snap.audio_revision_id)
        if init_rev:
            return get_revision_edl(dictation, init_rev)
    cur = current_audio_revision(dictation)
    if cur:
        return get_revision_edl(dictation, cur)
    raise AudioInvalidationError("no audio revision initialized for dictation")


def get_source_slices_for_interval(
    edl: EditDecisionList, start_ms: int, end_ms: int
) -> list[tuple[str, str, int, int]] | None:
    """Return sequence of (source_id, source_type, s_start, s_end) for [start_ms, end_ms]."""
    if start_ms < 0 or end_ms < start_ms or end_ms > edl.total_duration_ms:
        return None
    if start_ms == end_ms:
        for e in edl.entries:
            if (e.output_start_ms <= start_ms < e.output_end_ms) or (
                start_ms == edl.total_duration_ms and start_ms == e.output_end_ms
            ):
                offset = start_ms - e.output_start_ms
                pos = e.source_start_ms + offset
                return [(e.source_id, e.source_type, pos, pos)]
        return None
    slices: list[tuple[str, str, int, int]] = []
    for e in edl.entries:
        if e.output_start_ms >= end_ms:
            break
        if e.output_end_ms <= start_ms:
            continue
        s, t = max(e.output_start_ms, start_ms), min(e.output_end_ms, end_ms)
        if s < t:
            s_start = e.source_start_ms + (s - e.output_start_ms)
            s_end = e.source_start_ms + (t - e.output_start_ms)
            slices.append((e.source_id, e.source_type, s_start, s_end))
    if sum(s[3] - s[2] for s in slices) != (end_ms - start_ms):
        return None
    return slices


def is_interval_compatible(
    old_edl: EditDecisionList, new_edl: EditDecisionList, start_ms: int, end_ms: int
) -> bool:
    """Return True iff [start_ms, end_ms] covers the identical source audio slices in both EDLs."""
    old_slices = get_source_slices_for_interval(old_edl, start_ms, end_ms)
    new_slices = get_source_slices_for_interval(new_edl, start_ms, end_ms)
    return old_slices is not None and old_slices == new_slices


def is_position_compatible(
    old_edl: EditDecisionList, new_edl: EditDecisionList, position_ms: int
) -> bool:
    """Return True iff position_ms points to the identical source audio sample in both EDLs."""
    return is_interval_compatible(old_edl, new_edl, position_ms, position_ms)


def invalidate_audio_change(
    dictation: ClinicDictation,
    new_revision: ClinicAudioRevision,
    actor_user_id: int,
    lease_generation: int,
    now: datetime,
    *,
    previous_revision: ClinicAudioRevision | None = None,
    expected_revision_id: str | None = None,
) -> AudioInvalidationResult:
    """Invalidate incompatible links/anchors and supersede releases for a new audio revision."""
    now = _require_aware(now, "now")
    _require_edit_permission(dictation, actor_user_id)
    assert_current(dictation.id, actor_user_id, lease_generation, now)

    if previous_revision is None and new_revision.parent_revision_id:
        previous_revision = db.session.get(ClinicAudioRevision, new_revision.parent_revision_id)

    if expected_revision_id is not None:
        prev_id = previous_revision.id if previous_revision else None
        if prev_id != expected_revision_id:
            raise AudioInvalidationConflict(f"stale revision: expected {expected_revision_id}, got {prev_id}")

    new_edl = get_revision_edl(dictation, new_revision)
    base_edl = _get_baseline_edl(dictation)

    events: list[ClinicAudioInvalidationEvent] = []
    preserved_links: list[str] = []
    invalidated_links: list[str] = []
    prev_rev_id = previous_revision.id if previous_revision else None

    def _emit(target_type: str, target_id: str, reason: str) -> None:
        ev = ClinicAudioInvalidationEvent(
            dictation_id=dictation.id,
            audio_revision_id=new_revision.id,
            previous_audio_revision_id=prev_rev_id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            lease_generation=lease_generation,
            actor_user_id=actor_user_id,
            created_at=now,
        )
        db.session.add(ev)
        events.append(ev)

    # 1. Evaluate text-audio links against baseline timeline
    links = db.session.query(ClinicTextAudioLink).filter_by(dictation_id=dictation.id).order_by(ClinicTextAudioLink.seq.asc()).all()
    for link in links:
        if is_interval_compatible(base_edl, new_edl, link.audio_start_ms, link.audio_end_ms):
            preserved_links.append(link.id)
        else:
            invalidated_links.append(link.id)
            reason = REASON_OUT_OF_BOUNDS if link.audio_end_ms > new_edl.total_duration_ms else REASON_INTERVAL_CHANGED
            _emit(TARGET_LINK, link.id, reason)

    # 2. Evaluate markers against their bound revision (or baseline)
    markers = db.session.query(ClinicMarker).filter(ClinicMarker.dictation_id == dictation.id, ClinicMarker.deleted_at.is_(None)).all()
    for marker in markers:
        m_base_edl = base_edl
        if marker.audio_revision_id:
            m_rev = db.session.get(ClinicAudioRevision, marker.audio_revision_id)
            if m_rev:
                m_base_edl = get_revision_edl(dictation, m_rev)
        if not is_position_compatible(m_base_edl, new_edl, marker.position_ms):
            reason = REASON_OUT_OF_BOUNDS if marker.position_ms > new_edl.total_duration_ms else REASON_POSITION_CHANGED
            _emit(TARGET_MARKER, marker.id, reason)

    # 3. Release supersession
    superseded = list(invalidate_on_change(dictation, now, reason="audio_edited"))
    superseded_ids = {r.id for r in superseded}
    existing_supersessions = {
        row[0] for row in db.session.query(ClinicReleaseSupersession.release_id)
        .join(ClinicRelease, ClinicRelease.id == ClinicReleaseSupersession.release_id)
        .filter(ClinicRelease.dictation_id == dictation.id).all()
    }
    all_releases = db.session.query(ClinicRelease).filter_by(dictation_id=dictation.id).all()
    for rel in all_releases:
        if rel.id not in superseded_ids and rel.id not in existing_supersessions and rel.audio_revision_id != new_revision.id:
            db.session.add(ClinicReleaseSupersession(release_id=rel.id, reason="audio_edited", superseded_at=now))
            superseded.append(rel)
            superseded_ids.add(rel.id)

    for rel in superseded:
        _emit(TARGET_RELEASE, rel.id, REASON_AUDIO_SUPERSEDED)

    db.session.flush()

    return AudioInvalidationResult(
        new_revision=new_revision,
        previous_revision=previous_revision,
        invalidation_events=tuple(events),
        superseded_releases=tuple(superseded),
        preserved_link_ids=tuple(preserved_links),
        invalidated_link_ids=tuple(invalidated_links),
    )


def apply_audio_edit_and_invalidate(
    dictation: ClinicDictation,
    op_type: str,
    actor_user_id: int,
    lease_generation: int,
    now: datetime,
    *,
    expected_revision_id: str | None = None,
    position_ms: int | None = None,
    source: object = None,
    duration_ms: int | None = None,
    source_start_ms: int = 0,
    start_ms: int | None = None,
    end_ms: int | None = None,
    name: str | None = None,
    description: str | None = None,
) -> AudioInvalidationResult:
    """Atomically perform an audio edit, invalidate incompatible anchors, and supersede releases."""
    now = _require_aware(now, "now")
    _require_edit_permission(dictation, actor_user_id)
    assert_current(dictation.id, actor_user_id, lease_generation, now)

    curr_rev = current_audio_revision(dictation)
    if curr_rev is None:
        raise AudioInvalidationError("audio editing has not been initialized for this dictation")

    if expected_revision_id is not None and curr_rev.id != expected_revision_id:
        raise AudioInvalidationConflict(f"stale audio revision: expected {expected_revision_id}, current {curr_rev.id}")

    op_upper = op_type.upper()
    if op_upper == OP_INSERT:
        if position_ms is None or source is None:
            raise AudioInvalidationError("position_ms and source required for insert")
        new_rev = apply_insert(dictation, position_ms, source, duration_ms, source_start_ms, name, description)
    elif op_upper == OP_OVERWRITE:
        if position_ms is None or source is None:
            raise AudioInvalidationError("position_ms and source required for overwrite")
        new_rev = apply_overwrite(dictation, position_ms, source, duration_ms, source_start_ms, name, description)
    elif op_upper == OP_CUT:
        if start_ms is None or end_ms is None:
            raise AudioInvalidationError("start_ms and end_ms required for cut")
        new_rev = apply_cut(dictation, start_ms, end_ms, name, description)
    elif op_upper == OP_TRIM:
        if position_ms is None:
            raise AudioInvalidationError("position_ms required for trim")
        new_rev = apply_trim(dictation, position_ms, name, description)
    elif op_upper == "UNDO":
        new_rev = undo(dictation)
    elif op_upper == "REDO":
        new_rev = redo(dictation)
    else:
        raise AudioInvalidationError(f"unsupported op_type: {op_type}")

    return invalidate_audio_change(dictation, new_rev, actor_user_id, lease_generation, now, previous_revision=curr_rev)


def get_invalidation_events(
    dictation_id: str, audio_revision_id: str | None = None
) -> list[ClinicAudioInvalidationEvent]:
    query = db.session.query(ClinicAudioInvalidationEvent).filter_by(dictation_id=dictation_id)
    if audio_revision_id is not None:
        query = query.filter_by(audio_revision_id=audio_revision_id)
    return query.order_by(ClinicAudioInvalidationEvent.created_at.asc(), ClinicAudioInvalidationEvent.id.asc()).all()


def is_link_invalidated(link_id: str, audio_revision_id: str | None = None) -> bool:
    query = db.session.query(ClinicAudioInvalidationEvent).filter_by(target_type=TARGET_LINK, target_id=link_id)
    if audio_revision_id is not None:
        query = query.filter_by(audio_revision_id=audio_revision_id)
    return query.first() is not None


def get_invalidated_link_ids(dictation_id: str, audio_revision_id: str | None = None) -> set[str]:
    query = db.session.query(ClinicAudioInvalidationEvent.target_id).filter_by(dictation_id=dictation_id, target_type=TARGET_LINK)
    if audio_revision_id is not None:
        query = query.filter_by(audio_revision_id=audio_revision_id)
    return {row[0] for row in query.all()}


def get_compatible_links(
    dictation: ClinicDictation,
    revision: ClinicTranscriptRevision,
    audio_revision: ClinicAudioRevision | None = None,
) -> list[ClinicTextAudioLink]:
    """Return effective links for transcript revision that remain compatible with audio_revision."""
    all_links = effective_links(dictation, revision)
    target_rev = audio_revision or current_audio_revision(dictation)
    if target_rev is None:
        return all_links
    target_edl = get_revision_edl(dictation, target_rev)
    base_edl = _get_baseline_edl(dictation)
    return [
        link
        for link in all_links
        if is_interval_compatible(base_edl, target_edl, link.audio_start_ms, link.audio_end_ms)
    ]
