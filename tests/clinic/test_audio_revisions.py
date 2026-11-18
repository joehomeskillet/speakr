"""Tests for non-destructive audio editing, revisions, and undo/redo service.

Covers:
- Append-only immutability of edit operations and audio revisions
- Deterministic Edit Decision List (EDL) calculation and hashing
- Full undo/redo service across >16 steps (tested with 20 steps)
- Branch invalidation of redo stack
- Deterministic reconstruction of any historical revision state
- Dictation boundary integrity (cross-dictation prevention)
- Integration with ClinicRelease
- Real SQLite database in tmp_path with PRAGMA foreign_keys=ON
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from src.app import app, db
from src.clinic.audio_revisions import (
    ACTION_APPLY,
    ACTION_INIT,
    ACTION_REDO,
    ACTION_RESTORE,
    ACTION_UNDO,
    DEFAULT_MAX_UNDO_DEPTH,
    OP_CUT,
    OP_INSERT,
    OP_OVERWRITE,
    OP_TRIM,
    SOURCE_ORIGINAL,
    SOURCE_SEGMENT,
    AudioRevisionError,
    AudioRevisionImmutable,
    AudioRevisionNotFoundError,
    AudioUndoRedoError,
    ClinicAudioActionLog,
    ClinicAudioEditOperation,
    ClinicAudioRevisionSnapshot,
    ClinicAudioSegment,
    ClinicAudioSessionState,
    EDLEntry,
    EditDecisionList,
    apply_cut,
    apply_cut_to_edl,
    apply_insert,
    apply_insert_to_edl,
    apply_overwrite,
    apply_overwrite_to_edl,
    apply_trim,
    apply_trim_to_edl,
    can_redo,
    can_undo,
    compute_edl_hash,
    create_segment,
    current_audio_revision,
    get_operations,
    get_revision_edl,
    history,
    init_audio_revision,
    list_named_revisions,
    make_initial_edl,
    reconstruct_edl_from_operations,
    redo,
    redo_depth,
    render_edl,
    restore_revision,
    undo,
    undo_depth,
)
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicTranscriptRevision,
)
from src.clinic.release import create_release
from src.models import Recording, User


@pytest.fixture
def clinic_db():
    """App context with SQLite foreign keys on and new tables created."""
    with app.app_context():
        db.create_all()
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _make_scene():
    suffix = uuid.uuid4().hex[:10]
    user = User(username=f"u_{suffix}", email=f"{suffix}@local.test", password="x")
    db.session.add(user)
    db.session.flush()
    rec = Recording(user_id=user.id, title="dictation", status="PENDING")
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=user.id)
    db.session.add(dictation)
    db.session.flush()
    orig = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="orig_hash_" + suffix,
        size_bytes=1024,
        storage_key=f"originals/{suffix}.wav",
        receipt="receipt",
        origin_ref="device:mic",
    )
    db.session.add(orig)
    db.session.flush()
    return user, dictation, orig


# --- Pure EDL Calculation Tests -------------------------------------------


def test_pure_edl_initial_and_coalescing():
    edl = make_initial_edl("dict1", "orig1", 10000)
    assert edl.total_duration_ms == 10000
    assert len(edl.entries) == 1
    assert edl.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 10000, 0, 10000)
    assert edl.entries[0].duration_ms == 10000

    with pytest.raises(AudioRevisionError):
        make_initial_edl("dict1", "orig1", 0)


def test_pure_edl_cut_operations():
    edl = make_initial_edl("dict1", "orig1", 10000)

    # Cut middle [3000, 5000)
    cut1 = apply_cut_to_edl(edl, 3000, 5000)
    assert cut1.total_duration_ms == 8000
    assert len(cut1.entries) == 2
    assert cut1.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 3000, 0, 3000)
    assert cut1.entries[1] == EDLEntry("orig1", SOURCE_ORIGINAL, 5000, 10000, 3000, 8000)

    # Cut start [0, 2000)
    cut_start = apply_cut_to_edl(edl, 0, 2000)
    assert cut_start.total_duration_ms == 8000
    assert len(cut_start.entries) == 1
    assert cut_start.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 2000, 10000, 0, 8000)

    # Cut end [8000, 10000)
    cut_end = apply_cut_to_edl(edl, 8000, 10000)
    assert cut_end.total_duration_ms == 8000
    assert len(cut_end.entries) == 1
    assert cut_end.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 8000, 0, 8000)

    # Invalid cut ranges
    with pytest.raises(AudioRevisionError):
        apply_cut_to_edl(edl, -1, 5000)
    with pytest.raises(AudioRevisionError):
        apply_cut_to_edl(edl, 5000, 5000)
    with pytest.raises(AudioRevisionError):
        apply_cut_to_edl(edl, 6000, 5000)
    with pytest.raises(AudioRevisionError):
        apply_cut_to_edl(edl, 5000, 12000)


def test_pure_edl_trim():
    edl = make_initial_edl("dict1", "orig1", 10000)
    trimmed = apply_trim_to_edl(edl, 4000)
    assert trimmed.total_duration_ms == 4000
    assert len(trimmed.entries) == 1
    assert trimmed.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 4000, 0, 4000)

    # Trim at end is no-op
    assert apply_trim_to_edl(edl, 10000) == edl


def test_pure_edl_insert_and_overwrite():
    edl = make_initial_edl("dict1", "orig1", 10000)

    # Insert seg1 (2000ms) at 3000ms
    ins = apply_insert_to_edl(edl, 3000, "seg1", SOURCE_SEGMENT, 0, 2000)
    assert ins.total_duration_ms == 12000
    assert len(ins.entries) == 3
    assert ins.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 3000, 0, 3000)
    assert ins.entries[1] == EDLEntry("seg1", SOURCE_SEGMENT, 0, 2000, 3000, 5000)
    assert ins.entries[2] == EDLEntry("orig1", SOURCE_ORIGINAL, 3000, 10000, 5000, 12000)

    # Overwrite at 4000ms with seg2 (2000ms)
    ow = apply_overwrite_to_edl(edl, 4000, "seg2", SOURCE_SEGMENT, 0, 2000)
    assert ow.total_duration_ms == 10000
    assert len(ow.entries) == 3
    assert ow.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 4000, 0, 4000)
    assert ow.entries[1] == EDLEntry("seg2", SOURCE_SEGMENT, 0, 2000, 4000, 6000)
    assert ow.entries[2] == EDLEntry("orig1", SOURCE_ORIGINAL, 6000, 10000, 6000, 10000)

    # Overwrite extending past end of timeline
    ow_ext = apply_overwrite_to_edl(edl, 8000, "seg3", SOURCE_SEGMENT, 0, 5000)
    assert ow_ext.total_duration_ms == 13000
    assert len(ow_ext.entries) == 2
    assert ow_ext.entries[0] == EDLEntry("orig1", SOURCE_ORIGINAL, 0, 8000, 0, 8000)
    assert ow_ext.entries[1] == EDLEntry("seg3", SOURCE_SEGMENT, 0, 5000, 8000, 13000)


def test_pure_edl_render_determinism():
    ops = [
        {"op_type": OP_CUT, "position_ms": 2000, "duration_ms": 1000},
        {"op_type": OP_INSERT, "position_ms": 4000, "duration_ms": 1500, "source_id": "seg1", "source_type": SOURCE_SEGMENT},
        {"op_type": OP_OVERWRITE, "position_ms": 1000, "duration_ms": 2000, "source_id": "seg2", "source_type": SOURCE_SEGMENT},
        {"op_type": OP_TRIM, "position_ms": 9000, "duration_ms": 0},
    ]
    r1 = render_edl("orig1", ops, 10000, dictation_id="d1")
    r2 = render_edl("orig1", ops, 10000, dictation_id="d1")
    assert r1.content_hash == r2.content_hash
    assert r1.total_duration_ms == r2.total_duration_ms
    assert r1.entries == r2.entries


# --- Database & Service Tests ----------------------------------------------


def test_append_only_immutability(clinic_db):
    user, dictation, orig = _make_scene()
    rev = init_audio_revision(dictation, orig, 60000, name="Baseline")
    seg = create_segment(dictation, "storage/s1.wav", 5000, "hash_s1")
    rev1 = apply_insert(dictation, 10000, seg, name="Insert1")

    op = get_operations(dictation)[0]
    snap = list_named_revisions(dictation)[0]
    log = db.session.query(ClinicAudioActionLog).first()
    db.session.commit()
    op_id, snap_id, seg_id, log_id, rev1_id = op.id, snap.id, seg.id, log.id, rev1.id

    # Attempting to update or delete any append-only model must raise AudioRevisionImmutable
    op = db.session.get(ClinicAudioEditOperation, op_id)
    op.position_ms = 999
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    op = db.session.get(ClinicAudioEditOperation, op_id)
    db.session.delete(op)
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    snap = db.session.get(ClinicAudioRevisionSnapshot, snap_id)
    snap.name = "Mutated"
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    snap = db.session.get(ClinicAudioRevisionSnapshot, snap_id)
    db.session.delete(snap)
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    seg = db.session.get(ClinicAudioSegment, seg_id)
    db.session.delete(seg)
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    log = db.session.get(ClinicAudioActionLog, log_id)
    db.session.delete(log)
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    rev1 = db.session.get(ClinicAudioRevision, rev1_id)
    db.session.delete(rev1)
    with pytest.raises(AudioRevisionImmutable):
        db.session.flush()
    db.session.rollback()

    # Original is untouched
    assert db.session.get(ClinicOriginal, orig.id) is not None


def test_dictation_binding_guards(clinic_db):
    user, d1, orig1 = _make_scene()
    _, d2, orig2 = _make_scene()

    init_audio_revision(d1, orig1, 50000)
    init_audio_revision(d2, orig2, 40000)

    seg_d2 = create_segment(d2, "storage/d2.wav", 3000, "h2")

    # d1 cannot insert segment belonging to d2
    with pytest.raises(AudioRevisionError, match="belongs to another dictation"):
        apply_insert(d1, 5000, seg_d2)

    # d1 cannot insert original belonging to d2
    with pytest.raises(AudioRevisionError, match="belongs to another dictation"):
        apply_insert(d1, 5000, orig2, duration_ms=2000)

    # Querying revision for wrong dictation
    rev2 = current_audio_revision(d2)
    with pytest.raises(AudioRevisionError, match="belongs to another dictation"):
        get_revision_edl(d1, rev2)


def test_undo_redo_over_16_steps(clinic_db):
    """Test undo and redo across 20 steps (>16 steps required)."""
    user, dictation, orig = _make_scene()
    init_rev = init_audio_revision(dictation, orig, 100000, name="Baseline")

    revisions = [init_rev]
    edls = [get_revision_edl(dictation, init_rev)]

    # Perform 20 sequential edits
    for step in range(1, 21):
        if step % 3 == 0:
            rev = apply_cut(dictation, 1000 * step, 1000 * step + 500, name=f"Cut_{step}")
        elif step % 3 == 1:
            seg = create_segment(dictation, f"s/seg_{step}.wav", 1000, f"hash_{step}")
            rev = apply_insert(dictation, 500 * step, seg, name=f"Ins_{step}")
        else:
            seg = create_segment(dictation, f"s/ow_{step}.wav", 1200, f"hash_ow_{step}")
            rev = apply_overwrite(dictation, 800 * step, seg, name=f"OW_{step}")

        revisions.append(rev)
        edls.append(get_revision_edl(dictation, rev))

    assert undo_depth(dictation) == 20
    assert redo_depth(dictation) == 0
    assert can_undo(dictation) is True
    assert can_redo(dictation) is False

    # Undo all 20 steps one by one and verify exact EDL match at every step
    for step in range(20, 0, -1):
        curr = undo(dictation)
        expected_rev = revisions[step - 1]
        expected_edl = edls[step - 1]
        assert curr.id == expected_rev.id
        curr_edl = get_revision_edl(dictation, curr)
        assert curr_edl.content_hash == expected_edl.content_hash
        assert curr_edl.total_duration_ms == expected_edl.total_duration_ms

    assert can_undo(dictation) is False
    assert can_redo(dictation) is True
    assert undo_depth(dictation) == 0
    assert redo_depth(dictation) == 20

    with pytest.raises(AudioUndoRedoError):
        undo(dictation)

    # Redo all 20 steps one by one
    for step in range(1, 21):
        curr = redo(dictation)
        expected_rev = revisions[step]
        expected_edl = edls[step]
        assert curr.id == expected_rev.id
        curr_edl = get_revision_edl(dictation, curr)
        assert curr_edl.content_hash == expected_edl.content_hash

    assert can_undo(dictation) is True
    assert can_redo(dictation) is False

    with pytest.raises(AudioUndoRedoError):
        redo(dictation)


def test_branching_invalidates_redo_stack(clinic_db):
    user, dictation, orig = _make_scene()
    init_audio_revision(dictation, orig, 60000)

    seg1 = create_segment(dictation, "s1", 2000, "h1")
    rev1 = apply_insert(dictation, 5000, seg1, name="Edit1")
    seg2 = create_segment(dictation, "s2", 3000, "h2")
    rev2 = apply_insert(dictation, 10000, seg2, name="Edit2")

    # Undo back to Edit1
    u = undo(dictation)
    assert u.id == rev1.id
    assert can_redo(dictation) is True
    assert redo_depth(dictation) == 1

    # Apply new edit from Edit1: branch!
    rev3 = apply_cut(dictation, 1000, 3000, name="BranchCut")
    assert rev3.parent_revision_id == rev1.id
    assert can_redo(dictation) is False
    assert redo_depth(dictation) == 0


def test_restore_revision_and_reconstruct_edl(clinic_db):
    user, dictation, orig = _make_scene()
    init_rev = init_audio_revision(dictation, orig, 50000, name="Baseline")

    seg = create_segment(dictation, "s", 5000, "h")
    rev1 = apply_insert(dictation, 10000, seg, name="Step1")
    rev2 = apply_cut(dictation, 2000, 6000, name="Step2")
    rev3 = apply_trim(dictation, 40000, name="Step3")

    # Reconstruct from operations reproduces rev3 EDL exactly
    reconstructed = reconstruct_edl_from_operations(dictation)
    rev3_edl = get_revision_edl(dictation, rev3)
    assert reconstructed.content_hash == rev3_edl.content_hash
    assert reconstructed.total_duration_ms == rev3_edl.total_duration_ms

    # Partial reconstruction up to seq 2 matches rev2 EDL
    recon_seq2 = reconstruct_edl_from_operations(dictation, up_to_seq=2)
    rev2_edl = get_revision_edl(dictation, rev2)
    assert recon_seq2.content_hash == rev2_edl.content_hash

    # Restore step 1 directly
    restored = restore_revision(dictation, rev1)
    assert restored.id == rev1.id
    assert current_audio_revision(dictation).id == rev1.id
    assert get_revision_edl(dictation, restored).content_hash == get_revision_edl(dictation, rev1).content_hash


def test_named_snapshots_and_history(clinic_db):
    user, dictation, orig = _make_scene()
    init_audio_revision(dictation, orig, 30000, name="Take1")
    apply_cut(dictation, 1000, 2000, name="CutIntro")
    apply_cut(dictation, 5000, 6000, name=None)  # unnamed

    snaps = list_named_revisions(dictation)
    assert len(snaps) == 2
    assert [s.name for s in snaps] == ["Take1", "CutIntro"]

    hist = history(dictation)
    assert len(hist) == 3


def test_integration_with_clinic_release(clinic_db):
    user, dictation, orig = _make_scene()
    audio_rev = init_audio_revision(dictation, orig, 60000)

    transcript_rev = ClinicTranscriptRevision(
        dictation_id=dictation.id,
        kind="ASR",
        body="Clinical text",
        normalised_text_hash="hash_text",
        original_version_id=orig.id,
        model_ref="speaches-de-v1",
    )
    context_rev = ClinicContextRevision(
        dictation_id=dictation.id,
        source_system="KIS",
        case_ref="CASE123",
    )
    db.session.add_all([transcript_rev, context_rev])
    db.session.flush()

    now = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
    decision = {"allowed": True, "rule": "physician"}
    rel = create_release(
        dictation=dictation,
        audio_revision=audio_rev,
        transcript_revision=transcript_rev,
        context_revision=context_rev,
        text_hash="hash_text",
        actor_user_id=user.id,
        authorization_decision=decision,
        now=now,
        document_type="discharge_summary",
        release_type="final",
    )
    assert rel.id is not None
    assert rel.audio_revision_id == audio_rev.id


def test_real_sqlite_db_in_tmp_path(tmp_path):
    """Real SQLite database file in tmp directory with foreign keys enabled."""
    db_path = tmp_path / "standalone_audio.db"
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    db.metadata.create_all(engine)

    with Session(bind=engine) as s:
        # FK integrity check: cannot insert edit operation for nonexistent dictation
        op_bad = ClinicAudioEditOperation(
            dictation_id="nonexistent_dictation",
            seq=1,
            op_type=OP_CUT,
            position_ms=100,
            duration_ms=200,
        )
        s.add(op_bad)
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()

        # Pure EDL render test against real SQLite path
        ops = [{"op_type": OP_CUT, "position_ms": 1000, "duration_ms": 500}]
        edl = render_edl("orig_file", ops, 5000, dictation_id="d_tmp")
        assert edl.total_duration_ms == 4500
        assert len(edl.entries) == 2
