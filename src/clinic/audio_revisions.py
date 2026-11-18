"""Non-destructive audio editing and revision model with >=16-step undo/redo.

Original audio (ClinicOriginal) is strictly immutable. Edits are recorded as
an append-only operations list per dictation. Each revision state represents a
deterministic Edit Decision List (EDL) referencing slices of the original
audio or additional recorded segments.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, event, func

from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal
from src.database import db

OP_INSERT, OP_OVERWRITE, OP_CUT, OP_TRIM = "INSERT", "OVERWRITE", "CUT", "TRIM"
OPERATION_TYPES = (OP_INSERT, OP_OVERWRITE, OP_CUT, OP_TRIM)
SOURCE_ORIGINAL, SOURCE_SEGMENT = "ORIGINAL", "SEGMENT"
SOURCE_TYPES = (SOURCE_ORIGINAL, SOURCE_SEGMENT)
ACTION_INIT, ACTION_APPLY, ACTION_UNDO, ACTION_REDO, ACTION_RESTORE = "INIT", "APPLY_EDIT", "UNDO", "REDO", "RESTORE"
MIN_UNDO_STEPS, DEFAULT_MAX_UNDO_DEPTH = 16, 128


class AudioRevisionError(ValueError):
    """Base exception for audio revision errors."""


class AudioRevisionImmutable(AudioRevisionError):
    """Attempted update or deletion of an immutable append-only record."""


class AudioRevisionNotFoundError(AudioRevisionError):
    """The requested audio revision or snapshot does not exist."""


class AudioUndoRedoError(AudioRevisionError):
    """Cannot perform undo or redo."""


def _uuid():
    return db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


def _created_at():
    return db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=func.now())


# --- Data Structures (EDL) ------------------------------------------------


@dataclass(frozen=True)
class EDLEntry:
    source_id: str
    source_type: str
    source_start_ms: int
    source_end_ms: int
    output_start_ms: int
    output_end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.output_end_ms - self.output_start_ms

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        return d

    @classmethod
    def from_dict(cls, d: dict) -> EDLEntry:
        return cls(d["source_id"], d["source_type"], int(d["source_start_ms"]), int(d["source_end_ms"]), int(d["output_start_ms"]), int(d["output_end_ms"]))


@dataclass(frozen=True)
class EditDecisionList:
    dictation_id: str
    revision_id: str | None
    total_duration_ms: int
    entries: tuple[EDLEntry, ...]
    content_hash: str

    def to_dict(self) -> dict:
        return {"dictation_id": self.dictation_id, "revision_id": self.revision_id, "total_duration_ms": self.total_duration_ms, "content_hash": self.content_hash, "entries": [e.to_dict() for e in self.entries]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict) -> EditDecisionList:
        return cls(d["dictation_id"], d.get("revision_id"), int(d["total_duration_ms"]), tuple(EDLEntry.from_dict(e) for e in d.get("entries", [])), d["content_hash"])


def compute_edl_hash(entries: Sequence[EDLEntry]) -> str:
    payload = [(e.source_id, e.source_type, e.source_start_ms, e.source_end_ms, e.output_start_ms, e.output_end_ms) for e in entries]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _build_edl_entries(slices: Sequence[tuple[str, str, int, int]]) -> tuple[EDLEntry, ...]:
    entries: list[EDLEntry] = []
    t = 0
    for sid, stype, s_start, s_end in slices:
        dur = s_end - s_start
        if dur <= 0:
            continue
        if entries and entries[-1].source_id == sid and entries[-1].source_type == stype and entries[-1].source_end_ms == s_start:
            prev = entries.pop()
            out_end = prev.output_start_ms + (s_end - prev.source_start_ms)
            entries.append(EDLEntry(prev.source_id, prev.source_type, prev.source_start_ms, s_end, prev.output_start_ms, out_end))
            t = out_end
        else:
            entries.append(EDLEntry(sid, stype, s_start, s_end, t, t + dur))
            t += dur
    return tuple(entries)


# --- SQLAlchemy Models -----------------------------------------------------


class ClinicAudioSegment(db.Model):
    __tablename__ = "clinic_audio_segment"
    id, dictation_id = _uuid(), db.Column(db.String(36), nullable=False)
    storage_key, content_hash = db.Column(db.String(500), nullable=False), db.Column(db.String(128), nullable=False)
    duration_ms, created_at = db.Column(db.Integer, nullable=False), _created_at()
    __table_args__ = (
        ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_seg_dictation"),
        UniqueConstraint("dictation_id", "id", name="uq_clinic_audio_seg_dictation"),
        CheckConstraint("duration_ms > 0", name="ck_clinic_audio_seg_duration"),
    )


class ClinicAudioEditOperation(db.Model):
    __tablename__ = "clinic_audio_edit_operation"
    id, dictation_id, seq = _uuid(), db.Column(db.String(36), nullable=False), db.Column(db.Integer, nullable=False)
    op_type, position_ms, duration_ms = db.Column(db.String(20), nullable=False), db.Column(db.Integer, nullable=False), db.Column(db.Integer, nullable=False)
    source_type, original_id, segment_id = db.Column(db.String(20), nullable=True), db.Column(db.String(36), nullable=True), db.Column(db.String(36), nullable=True)
    source_start_ms = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    source_end_ms, audio_revision_id = db.Column(db.Integer, nullable=True), db.Column(db.String(36), nullable=True)
    description, created_at = db.Column(db.String(255), nullable=True), _created_at()
    __table_args__ = (
        ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_op_dictation"),
        UniqueConstraint("dictation_id", "seq", name="uq_clinic_audio_op_dictation_seq"),
        ForeignKeyConstraint(["dictation_id", "audio_revision_id"], ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"], name="fk_clinic_audio_op_rev_same"),
        CheckConstraint("seq >= 1", name="ck_clinic_audio_op_seq"),
        CheckConstraint("position_ms >= 0", name="ck_clinic_audio_op_pos"),
        CheckConstraint("duration_ms >= 0", name="ck_clinic_audio_op_dur"),
    )


class ClinicAudioRevisionSnapshot(db.Model):
    __tablename__ = "clinic_audio_revision_snapshot"
    id, dictation_id = _uuid(), db.Column(db.String(36), nullable=False)
    audio_revision_id, name = db.Column(db.String(36), nullable=False), db.Column(db.String(120), nullable=True)
    original_id, original_duration_ms = db.Column(db.String(36), nullable=False), db.Column(db.Integer, nullable=False)
    total_duration_ms, edl_json = db.Column(db.Integer, nullable=False), db.Column(db.Text, nullable=False)
    content_hash, operation_seq = db.Column(db.String(128), nullable=False), db.Column(db.Integer, nullable=False, default=0, server_default="0")
    created_at = _created_at()
    __table_args__ = (
        ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_snap_dictation"),
        ForeignKeyConstraint(["dictation_id", "audio_revision_id"], ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"], name="fk_clinic_audio_snap_rev_same"),
        UniqueConstraint("dictation_id", "audio_revision_id", name="uq_clinic_audio_snap_revision"),
    )


class ClinicAudioActionLog(db.Model):
    __tablename__ = "clinic_audio_action_log"
    id, dictation_id = _uuid(), db.Column(db.String(36), nullable=False)
    action_type = db.Column(db.String(20), nullable=False)
    from_revision_id, to_revision_id = db.Column(db.String(36), nullable=True), db.Column(db.String(36), nullable=True)
    created_at = _created_at()
    __table_args__ = (ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_act_dictation"),)


class ClinicAudioSessionState(db.Model):
    __tablename__ = "clinic_audio_session_state"
    id, dictation_id = _uuid(), db.Column(db.String(36), nullable=False, unique=True)
    current_revision_id = db.Column(db.String(36), nullable=False)
    undo_stack_json = db.Column(db.Text, nullable=False, default="[]", server_default="[]")
    redo_stack_json = db.Column(db.Text, nullable=False, default="[]", server_default="[]")
    updated_at = _created_at()
    __table_args__ = (
        ForeignKeyConstraint(["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_audio_sess_dictation"),
        ForeignKeyConstraint(["dictation_id", "current_revision_id"], ["clinic_audio_revision.dictation_id", "clinic_audio_revision.id"], name="fk_clinic_audio_sess_rev_same"),
    )


def _reject_audio_immutable_write(_mapper, _connection, _target):
    raise AudioRevisionImmutable("audio edit operations and revisions are append-only")


for _m in (ClinicAudioEditOperation, ClinicAudioRevisionSnapshot, ClinicAudioSegment, ClinicAudioActionLog, ClinicAudioRevision):
    event.listen(_m, "before_update", _reject_audio_immutable_write)
    event.listen(_m, "before_delete", _reject_audio_immutable_write)


# --- Pure EDL Transformation Functions ------------------------------------


def make_initial_edl(dictation_id: str, original_id: str, original_duration_ms: int) -> EditDecisionList:
    if original_duration_ms <= 0:
        raise AudioRevisionError("original duration must be > 0 ms")
    entry = EDLEntry(original_id, SOURCE_ORIGINAL, 0, original_duration_ms, 0, original_duration_ms)
    entries = (entry,)
    return EditDecisionList(dictation_id, None, original_duration_ms, entries, compute_edl_hash(entries))


def apply_cut_to_edl(edl: EditDecisionList, cut_start: int, cut_end: int) -> EditDecisionList:
    if cut_start < 0 or cut_end <= cut_start or cut_end > edl.total_duration_ms:
        raise AudioRevisionError(f"invalid cut range [{cut_start}, {cut_end}) for duration {edl.total_duration_ms}")
    slices: list[tuple[str, str, int, int]] = []
    for e in edl.entries:
        s, t = max(e.output_start_ms, cut_start), min(e.output_end_ms, cut_end)
        if s >= t:
            slices.append((e.source_id, e.source_type, e.source_start_ms, e.source_end_ms))
        else:
            if e.output_start_ms < s:
                slices.append((e.source_id, e.source_type, e.source_start_ms, e.source_start_ms + (s - e.output_start_ms)))
            if e.output_end_ms > t:
                slices.append((e.source_id, e.source_type, e.source_start_ms + (t - e.output_start_ms), e.source_end_ms))
    new_entries = _build_edl_entries(slices)
    new_dur = edl.total_duration_ms - (cut_end - cut_start)
    return EditDecisionList(edl.dictation_id, None, new_dur, new_entries, compute_edl_hash(new_entries))


def apply_trim_to_edl(edl: EditDecisionList, trim_pos: int) -> EditDecisionList:
    if trim_pos < 0 or trim_pos > edl.total_duration_ms:
        raise AudioRevisionError(f"invalid trim position {trim_pos} for duration {edl.total_duration_ms}")
    return edl if trim_pos == edl.total_duration_ms else apply_cut_to_edl(edl, trim_pos, edl.total_duration_ms)


def apply_insert_to_edl(edl: EditDecisionList, pos: int, sid: str, stype: str, s_start: int, s_end: int) -> EditDecisionList:
    dur = s_end - s_start
    if dur <= 0 or s_start < 0:
        raise AudioRevisionError(f"invalid source interval [{s_start}, {s_end})")
    if pos < 0 or pos > edl.total_duration_ms:
        raise AudioRevisionError(f"invalid insert position {pos} for duration {edl.total_duration_ms}")
    new_slice = (sid, stype, s_start, s_end)
    slices: list[tuple[str, str, int, int]] = []
    inserted = (pos == 0)
    if inserted:
        slices.append(new_slice)
    for e in edl.entries:
        if not inserted and e.output_start_ms <= pos <= e.output_end_ms:
            dur_before = pos - e.output_start_ms
            if dur_before > 0:
                slices.append((e.source_id, e.source_type, e.source_start_ms, e.source_start_ms + dur_before))
            slices.append(new_slice)
            if pos < e.output_end_ms:
                slices.append((e.source_id, e.source_type, e.source_start_ms + dur_before, e.source_end_ms))
            inserted = True
        else:
            slices.append((e.source_id, e.source_type, e.source_start_ms, e.source_end_ms))
    if not inserted:
        slices.append(new_slice)
    new_entries = _build_edl_entries(slices)
    return EditDecisionList(edl.dictation_id, None, edl.total_duration_ms + dur, new_entries, compute_edl_hash(new_entries))


def apply_overwrite_to_edl(edl: EditDecisionList, pos: int, sid: str, stype: str, s_start: int, s_end: int) -> EditDecisionList:
    dur = s_end - s_start
    if dur <= 0 or s_start < 0:
        raise AudioRevisionError(f"invalid source interval [{s_start}, {s_end})")
    if pos < 0 or pos > edl.total_duration_ms:
        raise AudioRevisionError(f"invalid overwrite position {pos} for duration {edl.total_duration_ms}")
    work_edl = apply_cut_to_edl(edl, pos, min(pos + dur, edl.total_duration_ms)) if pos < edl.total_duration_ms else edl
    return apply_insert_to_edl(work_edl, pos, sid, stype, s_start, s_end)


def apply_operation_to_edl(edl: EditDecisionList, op: ClinicAudioEditOperation | dict) -> EditDecisionList:
    op_type = op["op_type"] if isinstance(op, dict) else op.op_type
    pos = int(op["position_ms"] if isinstance(op, dict) else op.position_ms)
    dur = int(op["duration_ms"] if isinstance(op, dict) else op.duration_ms)
    if op_type == OP_CUT:
        return apply_cut_to_edl(edl, pos, pos + dur)
    if op_type == OP_TRIM:
        return apply_trim_to_edl(edl, pos)
    s_id = (op.get("source_id") or op.get("segment_id") or op.get("original_id")) if isinstance(op, dict) else (op.segment_id or op.original_id)
    s_type = op.get("source_type") if isinstance(op, dict) else op.source_type
    s_start = int(op.get("source_start_ms", 0) if isinstance(op, dict) else op.source_start_ms)
    s_end = int(op.get("source_end_ms", s_start + dur) if isinstance(op, dict) else (op.source_end_ms or (s_start + dur)))
    if op_type == OP_INSERT:
        return apply_insert_to_edl(edl, pos, str(s_id), str(s_type), s_start, s_end)
    if op_type == OP_OVERWRITE:
        return apply_overwrite_to_edl(edl, pos, str(s_id), str(s_type), s_start, s_end)
    raise AudioRevisionError(f"unknown operation type {op_type}")


def render_edl(original: ClinicOriginal | str, operations: Sequence[ClinicAudioEditOperation | dict], original_duration_ms: int, dictation_id: str | None = None) -> EditDecisionList:
    orig_id = original.id if isinstance(original, ClinicOriginal) else str(original)
    d_id = dictation_id or (original.dictation_id if isinstance(original, ClinicOriginal) else "standalone")
    edl = make_initial_edl(d_id, orig_id, original_duration_ms)
    for op in operations:
        edl = apply_operation_to_edl(edl, op)
    return edl


# --- Service API -----------------------------------------------------------


def _check_entity(inst: object, cls: type, name: str, dictation_id: str | None = None) -> None:
    if not isinstance(inst, cls) or getattr(inst, "id", None) is None:
        raise AudioRevisionError(f"{name} must be a persisted {cls.__name__}")
    if dictation_id and getattr(inst, "dictation_id", None) != dictation_id:
        raise AudioRevisionError(f"{name} belongs to another dictation")


def init_audio_revision(dictation: ClinicDictation, original: ClinicOriginal, original_duration_ms: int, name: str = "Original") -> ClinicAudioRevision:
    _check_entity(dictation, ClinicDictation, "dictation")
    _check_entity(original, ClinicOriginal, "original", dictation.id)
    session = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    if session is not None:
        return db.session.get(ClinicAudioRevision, session.current_revision_id)
    edl = make_initial_edl(dictation.id, original.id, original_duration_ms)
    rev = ClinicAudioRevision(dictation_id=dictation.id, parent_revision_id=None, content_hash=edl.content_hash)
    db.session.add(rev)
    db.session.flush()
    db.session.add_all([
        ClinicAudioRevisionSnapshot(dictation_id=dictation.id, audio_revision_id=rev.id, name=name, original_id=original.id, original_duration_ms=original_duration_ms, total_duration_ms=edl.total_duration_ms, edl_json=edl.to_json(), content_hash=edl.content_hash, operation_seq=0),
        ClinicAudioSessionState(dictation_id=dictation.id, current_revision_id=rev.id),
        ClinicAudioActionLog(dictation_id=dictation.id, action_type=ACTION_INIT, to_revision_id=rev.id),
    ])
    db.session.flush()
    return rev


def create_segment(dictation: ClinicDictation, storage_key: str, duration_ms: int, content_hash: str) -> ClinicAudioSegment:
    _check_entity(dictation, ClinicDictation, "dictation")
    if duration_ms <= 0 or not storage_key or not content_hash:
        raise AudioRevisionError("valid storage_key, content_hash, and duration_ms > 0 are required")
    seg = ClinicAudioSegment(dictation_id=dictation.id, storage_key=storage_key, duration_ms=duration_ms, content_hash=content_hash)
    db.session.add(seg)
    db.session.flush()
    return seg


def _apply_edit_step(dictation: ClinicDictation, op_type: str, pos: int, dur: int, stype: str | None, orig_id: str | None, seg_id: str | None, s_start: int, s_end: int | None, name: str | None, desc: str | None, max_undo: int = DEFAULT_MAX_UNDO_DEPTH) -> ClinicAudioRevision:
    _check_entity(dictation, ClinicDictation, "dictation")
    state = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    if state is None:
        raise AudioRevisionError("dictation audio editing has not been initialized")
    curr_rev = db.session.get(ClinicAudioRevision, state.current_revision_id)
    curr_snap = db.session.query(ClinicAudioRevisionSnapshot).filter_by(dictation_id=dictation.id, audio_revision_id=curr_rev.id).first()
    curr_edl = EditDecisionList.from_dict(json.loads(curr_snap.edl_json))
    op_d = {"op_type": op_type, "position_ms": pos, "duration_ms": dur, "source_id": seg_id or orig_id, "source_type": stype, "source_start_ms": s_start, "source_end_ms": s_end}
    new_edl = apply_operation_to_edl(curr_edl, op_d)
    next_seq = (db.session.query(func.max(ClinicAudioEditOperation.seq)).filter_by(dictation_id=dictation.id).scalar() or 0) + 1
    new_rev = ClinicAudioRevision(dictation_id=dictation.id, parent_revision_id=curr_rev.id, content_hash=new_edl.content_hash)
    db.session.add(new_rev)
    db.session.flush()
    undo_stack: list[str] = json.loads(state.undo_stack_json) + [curr_rev.id]
    state.undo_stack_json, state.redo_stack_json = json.dumps(undo_stack[-max_undo:]), "[]"
    state.current_revision_id, state.updated_at = new_rev.id, datetime.now(timezone.utc)
    db.session.add_all([
        ClinicAudioEditOperation(dictation_id=dictation.id, seq=next_seq, op_type=op_type, position_ms=pos, duration_ms=dur, source_type=stype, original_id=orig_id, segment_id=seg_id, source_start_ms=s_start, source_end_ms=s_end, audio_revision_id=new_rev.id, description=desc),
        ClinicAudioRevisionSnapshot(dictation_id=dictation.id, audio_revision_id=new_rev.id, name=name, original_id=curr_snap.original_id, original_duration_ms=curr_snap.original_duration_ms, total_duration_ms=new_edl.total_duration_ms, edl_json=new_edl.to_json(), content_hash=new_edl.content_hash, operation_seq=next_seq),
        ClinicAudioActionLog(dictation_id=dictation.id, action_type=ACTION_APPLY, from_revision_id=curr_rev.id, to_revision_id=new_rev.id),
    ])
    db.session.flush()
    return new_rev


def _extract_source_args(dictation: ClinicDictation, source: ClinicAudioSegment | ClinicOriginal, duration_ms: int | None, s_start: int) -> tuple[str, str | None, str | None, int, int]:
    if isinstance(source, ClinicAudioSegment):
        _check_entity(source, ClinicAudioSegment, "segment", dictation.id)
        dur = duration_ms or (source.duration_ms - s_start)
        return SOURCE_SEGMENT, None, source.id, dur, s_start + dur
    if isinstance(source, ClinicOriginal):
        _check_entity(source, ClinicOriginal, "original", dictation.id)
        if duration_ms is None:
            raise AudioRevisionError("duration_ms required when using original audio")
        return SOURCE_ORIGINAL, source.id, None, duration_ms, s_start + duration_ms
    raise AudioRevisionError("source must be ClinicAudioSegment or ClinicOriginal")


def apply_insert(dictation: ClinicDictation, position_ms: int, source: ClinicAudioSegment | ClinicOriginal, duration_ms: int | None = None, source_start_ms: int = 0, name: str | None = None, description: str | None = None) -> ClinicAudioRevision:
    stype, orig_id, seg_id, dur, send = _extract_source_args(dictation, source, duration_ms, source_start_ms)
    return _apply_edit_step(dictation, OP_INSERT, position_ms, dur, stype, orig_id, seg_id, source_start_ms, send, name, description)


def apply_overwrite(dictation: ClinicDictation, position_ms: int, source: ClinicAudioSegment | ClinicOriginal, duration_ms: int | None = None, source_start_ms: int = 0, name: str | None = None, description: str | None = None) -> ClinicAudioRevision:
    stype, orig_id, seg_id, dur, send = _extract_source_args(dictation, source, duration_ms, source_start_ms)
    return _apply_edit_step(dictation, OP_OVERWRITE, position_ms, dur, stype, orig_id, seg_id, source_start_ms, send, name, description)


def apply_cut(dictation: ClinicDictation, start_ms: int, end_ms: int, name: str | None = None, description: str | None = None) -> ClinicAudioRevision:
    return _apply_edit_step(dictation, OP_CUT, start_ms, end_ms - start_ms, None, None, None, 0, None, name, description)


def apply_trim(dictation: ClinicDictation, position_ms: int, name: str | None = None, description: str | None = None) -> ClinicAudioRevision:
    _check_entity(dictation, ClinicDictation, "dictation")
    state = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    if state is None:
        raise AudioRevisionError("dictation audio editing has not been initialized")
    snap = db.session.query(ClinicAudioRevisionSnapshot).filter_by(dictation_id=dictation.id, audio_revision_id=state.current_revision_id).first()
    return _apply_edit_step(dictation, OP_TRIM, position_ms, max(0, snap.total_duration_ms - position_ms), None, None, None, 0, None, name, description)


def _step_undo_redo(dictation: ClinicDictation, is_undo: bool) -> ClinicAudioRevision:
    _check_entity(dictation, ClinicDictation, "dictation")
    state = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    if state is None:
        raise AudioUndoRedoError("no audio edit session found")
    pkey, skey = ("undo_stack_json", "redo_stack_json") if is_undo else ("redo_stack_json", "undo_stack_json")
    stack: list[str] = json.loads(getattr(state, pkey))
    if not stack:
        raise AudioUndoRedoError("stack is empty")
    target_id, curr_id = stack.pop(), state.current_revision_id
    other_stack: list[str] = json.loads(getattr(state, skey)) + [curr_id]
    setattr(state, pkey, json.dumps(stack))
    setattr(state, skey, json.dumps(other_stack))
    state.current_revision_id, state.updated_at = target_id, datetime.now(timezone.utc)
    db.session.add(ClinicAudioActionLog(dictation_id=dictation.id, action_type=ACTION_UNDO if is_undo else ACTION_REDO, from_revision_id=curr_id, to_revision_id=target_id))
    db.session.flush()
    return db.session.get(ClinicAudioRevision, target_id)


def undo(dictation: ClinicDictation) -> ClinicAudioRevision:
    return _step_undo_redo(dictation, is_undo=True)


def redo(dictation: ClinicDictation) -> ClinicAudioRevision:
    return _step_undo_redo(dictation, is_undo=False)


def can_undo(dictation: ClinicDictation) -> bool:
    _check_entity(dictation, ClinicDictation, "dictation")
    s = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    return bool(s and json.loads(s.undo_stack_json))


def can_redo(dictation: ClinicDictation) -> bool:
    _check_entity(dictation, ClinicDictation, "dictation")
    s = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    return bool(s and json.loads(s.redo_stack_json))


def undo_depth(dictation: ClinicDictation) -> int:
    _check_entity(dictation, ClinicDictation, "dictation")
    s = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    return len(json.loads(s.undo_stack_json)) if s else 0


def redo_depth(dictation: ClinicDictation) -> int:
    _check_entity(dictation, ClinicDictation, "dictation")
    s = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    return len(json.loads(s.redo_stack_json)) if s else 0


def current_audio_revision(dictation: ClinicDictation) -> ClinicAudioRevision | None:
    _check_entity(dictation, ClinicDictation, "dictation")
    s = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    return db.session.get(ClinicAudioRevision, s.current_revision_id) if s else None


def get_revision_edl(dictation: ClinicDictation, revision: ClinicAudioRevision) -> EditDecisionList:
    _check_entity(dictation, ClinicDictation, "dictation")
    _check_entity(revision, ClinicAudioRevision, "revision", dictation.id)
    snap = db.session.query(ClinicAudioRevisionSnapshot).filter_by(dictation_id=dictation.id, audio_revision_id=revision.id).first()
    if snap is None:
        raise AudioRevisionNotFoundError(f"snapshot for revision {revision.id} not found")
    edl = EditDecisionList.from_dict(json.loads(snap.edl_json))
    return EditDecisionList(edl.dictation_id, revision.id, edl.total_duration_ms, edl.entries, edl.content_hash)


def restore_revision(dictation: ClinicDictation, revision_or_id: ClinicAudioRevision | str) -> ClinicAudioRevision:
    _check_entity(dictation, ClinicDictation, "dictation")
    rev = db.session.get(ClinicAudioRevision, revision_or_id) if isinstance(revision_or_id, str) else revision_or_id
    if rev is None:
        raise AudioRevisionNotFoundError("revision not found")
    _check_entity(rev, ClinicAudioRevision, "revision", dictation.id)
    state = db.session.query(ClinicAudioSessionState).filter_by(dictation_id=dictation.id).first()
    if state is None:
        raise AudioRevisionError("session not initialized")
    if state.current_revision_id == rev.id:
        return rev
    undo_stack: list[str] = json.loads(state.undo_stack_json) + [state.current_revision_id]
    curr_id = state.current_revision_id
    state.undo_stack_json, state.redo_stack_json = json.dumps(undo_stack), "[]"
    state.current_revision_id, state.updated_at = rev.id, datetime.now(timezone.utc)
    db.session.add(ClinicAudioActionLog(dictation_id=dictation.id, action_type=ACTION_RESTORE, from_revision_id=curr_id, to_revision_id=rev.id))
    db.session.flush()
    return rev


def reconstruct_edl_from_operations(dictation: ClinicDictation, up_to_seq: int | None = None) -> EditDecisionList:
    _check_entity(dictation, ClinicDictation, "dictation")
    init_snap = db.session.query(ClinicAudioRevisionSnapshot).filter_by(dictation_id=dictation.id, operation_seq=0).first()
    if init_snap is None:
        raise AudioRevisionError("dictation has no baseline audio snapshot")
    q = db.session.query(ClinicAudioEditOperation).filter(ClinicAudioEditOperation.dictation_id == dictation.id)
    if up_to_seq is not None:
        q = q.filter(ClinicAudioEditOperation.seq <= up_to_seq)
    ops = q.order_by(ClinicAudioEditOperation.seq.asc()).all()
    edl = make_initial_edl(dictation.id, init_snap.original_id, init_snap.original_duration_ms)
    for op in ops:
        edl = apply_operation_to_edl(edl, op)
    return edl


def history(dictation: ClinicDictation) -> list[ClinicAudioRevision]:
    _check_entity(dictation, ClinicDictation, "dictation")
    return db.session.query(ClinicAudioRevision).filter_by(dictation_id=dictation.id).all()


def list_named_revisions(dictation: ClinicDictation) -> list[ClinicAudioRevisionSnapshot]:
    _check_entity(dictation, ClinicDictation, "dictation")
    return db.session.query(ClinicAudioRevisionSnapshot).filter_by(dictation_id=dictation.id).filter(ClinicAudioRevisionSnapshot.name.isnot(None)).order_by(ClinicAudioRevisionSnapshot.created_at.asc()).all()


def get_operations(dictation: ClinicDictation) -> list[ClinicAudioEditOperation]:
    _check_entity(dictation, ClinicDictation, "dictation")
    return db.session.query(ClinicAudioEditOperation).filter_by(dictation_id=dictation.id).order_by(ClinicAudioEditOperation.seq.asc()).all()
