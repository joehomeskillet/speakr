"""Focused tests for audio invalidation of text anchors and releases (w021-audio-invalidation).

Acceptance criteria:
1. A new audio revision invalidates links whose intervals changed and preserves only
   demonstrably compatible anchors.
2. The new head, invalidation events and release supersession commit together under the
   current lease/generation.
3. Old audio, transcript, marker and release revisions remain readable. Competing edits
   cannot publish mixed-version heads.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic import policy
from src.clinic.audio_invalidation import (
    OP_CUT,
    OP_INSERT,
    OP_OVERWRITE,
    OP_TRIM,
    TARGET_LINK,
    TARGET_MARKER,
    TARGET_RELEASE,
    AudioInvalidationConflict,
    AudioInvalidationError,
    AudioInvalidationPermissionDenied,
    ClinicAudioInvalidationEvent,
    apply_audio_edit_and_invalidate,
    get_compatible_links,
    get_invalidation_events,
    is_interval_compatible,
    is_link_invalidated,
    is_position_compatible,
)
from src.clinic.audio_revisions import (
    ClinicAudioRevision,
    create_segment,
    current_audio_revision,
    get_revision_edl,
    init_audio_revision,
)
from src.clinic.correction import (
    ClinicMarker,
    ClinicTextAudioLink,
    add_marker,
    link_asr_segments,
    set_manual_link,
)
from src.clinic.lease import LeaseConflict, acquire
from src.clinic.models import (
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
)
from src.clinic.release import active_release, create_release
from src.clinic.revisions import add_asr_revision
from src.models import Recording, User

T0 = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 18, 10, 5, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 18, 10, 10, tzinfo=timezone.utc)
BODY = "Anamnese Husten seit drei Tagen. Befund Lunge frei."


class SyntheticCommitError(Exception):
    """Simulate an interrupted caller transaction."""


@pytest.fixture
def clinic_db():
    with app.app_context():
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.create_all()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _user(prefix="u"):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f"{prefix}_{tag}", email=f"{tag}@test.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


@pytest.fixture
def inv_scene(clinic_db):
    author, helper = _user("author"), _user("helper")
    recording = Recording(user_id=author.id, title="invalidation_test", status="PENDING")
    db.session.add(recording)
    db.session.flush()

    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(dictation)
    db.session.flush()

    original = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash="o" * 64,
        size_bytes=1000,
        storage_key="orig/" + uuid.uuid4().hex,
        receipt="stored",
        origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()

    asr = add_asr_revision(dictation, original, BODY, "model:test")
    audio = init_audio_revision(dictation, original, 1000)
    lease = acquire(dictation.id, author.id, T0, duration=3600)

    # 3 text/audio links
    links = link_asr_segments(
        dictation,
        asr,
        [
            {"text": "Anamnese", "start": 0.1, "end": 0.25},
            {"text": "Husten seit", "start": 0.4, "end": 0.55},
            {"text": "Lunge frei.", "start": 0.7, "end": 0.85},
        ],
    )

    context = ClinicContextRevision(dictation_id=dictation.id, case_ref="CASE-01")
    db.session.add(context)
    db.session.flush()

    rel = create_release(
        dictation,
        audio,
        asr,
        context,
        asr.normalised_text_hash,
        author.id,
        {"allowed": True},
        T0,
        document_type="report",
        release_type="final",
    )
    segment = create_segment(dictation, "seg/" + uuid.uuid4().hex, 200, "s" * 64)
    db.session.commit()

    return SimpleNamespace(
        author=author,
        helper=helper,
        dictation=dictation,
        original=original,
        asr=asr,
        audio=audio,
        lease=lease,
        links=links,
        context=context,
        release=rel,
        segment=segment,
    )


def test_audio_cut_invalidates_changed_links_and_preserves_compatible(inv_scene):
    s = inv_scene
    assert len(s.links) == 3
    assert active_release(s.dictation) is not None

    # Cut [350, 600]:
    # Link 0: [100, 250] - before cut, unchanged -> preserved
    # Link 1: [400, 550] - overlaps cut -> invalidated
    # Link 2: [700, 850] - shifted by cut -> invalidated
    result = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_CUT,
        s.author.id,
        s.lease.generation,
        T1,
        start_ms=350,
        end_ms=600,
        expected_revision_id=s.audio.id,
    )
    db.session.commit()

    assert result.new_revision.id != s.audio.id
    assert result.preserved_link_ids == (s.links[0].id,)
    assert set(result.invalidated_link_ids) == {s.links[1].id, s.links[2].id}

    # Release supersession committed together
    assert len(result.superseded_releases) == 1
    assert result.superseded_releases[0].id == s.release.id
    assert active_release(s.dictation) is None

    # Invalidation events
    events = get_invalidation_events(s.dictation.id, result.new_revision.id)
    targets = {e.target_id for e in events}
    assert s.links[1].id in targets
    assert s.links[2].id in targets
    assert s.release.id in targets

    # Compatible links query preserves Link 0 only
    compatible = get_compatible_links(s.dictation, s.asr)
    assert [c.id for c in compatible] == [s.links[0].id]


def test_audio_trim_invalidates_out_of_bounds_anchors_and_markers(inv_scene):
    s = inv_scene
    m_keep = add_marker(s.dictation, 200, s.author.id, label="Anamnese")
    m_cut = add_marker(s.dictation, 800, s.author.id, label="Epikrise")
    db.session.commit()

    # Trim at 600 ms:
    # Link 0 [100, 250] and Link 1 [400, 550] are within 600 ms -> preserved
    # Link 2 [700, 850] extends beyond 600 ms -> invalidated (out of bounds)
    # Marker m_keep (200 ms) -> preserved
    # Marker m_cut (800 ms) -> invalidated (out of bounds)
    result = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_TRIM,
        s.author.id,
        s.lease.generation,
        T1,
        position_ms=600,
    )
    db.session.commit()

    assert set(result.preserved_link_ids) == {s.links[0].id, s.links[1].id}
    assert result.invalidated_link_ids == (s.links[2].id,)

    events = get_invalidation_events(s.dictation.id, result.new_revision.id)
    types_and_targets = {(e.target_type, e.target_id) for e in events}
    assert (TARGET_LINK, s.links[2].id) in types_and_targets
    assert (TARGET_MARKER, m_cut.id) in types_and_targets
    assert (TARGET_MARKER, m_keep.id) not in types_and_targets
    assert (TARGET_RELEASE, s.release.id) in types_and_targets


def test_audio_overwrite_and_insert_compatibility(inv_scene):
    s = inv_scene
    # Overwrite [400, 550] with 150 ms of segment
    # Link 0 [100, 250] is before 400 -> preserved
    # Link 1 [400, 550] is overwritten -> invalidated
    # Link 2 [700, 850] is untouched (overwrite preserves total duration) -> preserved
    res_ow = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_OVERWRITE,
        s.author.id,
        s.lease.generation,
        T1,
        position_ms=400,
        source=s.segment,
        duration_ms=150,
        source_start_ms=0,
    )
    db.session.commit()
    assert set(res_ow.preserved_link_ids) == {s.links[0].id, s.links[2].id}
    assert res_ow.invalidated_link_ids == (s.links[1].id,)

    # Next, insert 100 ms at position 50
    # Link 0 [100, 250] shifted -> invalidated
    # Link 2 [700, 850] shifted -> invalidated
    res_ins = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_INSERT,
        s.author.id,
        s.lease.generation,
        T2,
        position_ms=50,
        source=s.segment,
        duration_ms=100,
        source_start_ms=0,
    )
    db.session.commit()
    assert set(res_ins.invalidated_link_ids) == {s.links[0].id, s.links[1].id, s.links[2].id}
    assert res_ins.preserved_link_ids == ()


def test_atomic_commit_and_rollback_under_lease(inv_scene):
    s = inv_scene
    # Transaction interrupted: verify all parts roll back together
    with pytest.raises(SyntheticCommitError), db.session.begin():
        apply_audio_edit_and_invalidate(
            s.dictation,
            OP_CUT,
            s.author.id,
            s.lease.generation,
            T1,
            start_ms=300,
            end_ms=600,
        )
        raise SyntheticCommitError

    # Rollback verified: audio head unchanged, release still active, 0 invalidation events
    assert current_audio_revision(s.dictation).id == s.audio.id
    assert active_release(s.dictation).id == s.release.id
    assert get_invalidation_events(s.dictation.id) == []
    assert len(get_compatible_links(s.dictation, s.asr)) == 3

    # Clean commit: all parts commit together under current lease
    res = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_CUT,
        s.author.id,
        s.lease.generation,
        T1,
        start_ms=300,
        end_ms=600,
    )
    db.session.commit()

    assert current_audio_revision(s.dictation).id == res.new_revision.id
    assert active_release(s.dictation) is None
    assert len(get_invalidation_events(s.dictation.id)) > 0


def test_competing_edits_rejected_with_stale_expected_revision(inv_scene):
    s = inv_scene
    # Worker A applies edit against s.audio
    res_a = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_TRIM,
        s.author.id,
        s.lease.generation,
        T1,
        position_ms=900,
        expected_revision_id=s.audio.id,
    )
    db.session.commit()
    assert current_audio_revision(s.dictation).id == res_a.new_revision.id

    # Worker B tries to apply competing edit expecting old head s.audio
    with pytest.raises(AudioInvalidationConflict, match="stale audio revision"):
        apply_audio_edit_and_invalidate(
            s.dictation,
            OP_TRIM,
            s.author.id,
            s.lease.generation,
            T2,
            position_ms=800,
            expected_revision_id=s.audio.id,
        )


def test_unauthorized_actor_rejected(inv_scene):
    s = inv_scene
    # Helper user lacks edit rights on author's recording
    with pytest.raises(AudioInvalidationPermissionDenied, match="not permitted"):
        apply_audio_edit_and_invalidate(
            s.dictation,
            OP_CUT,
            s.helper.id,
            s.lease.generation,
            T1,
            start_ms=100,
            end_ms=200,
        )
    # Head and release unchanged
    assert current_audio_revision(s.dictation).id == s.audio.id
    assert active_release(s.dictation).id == s.release.id


def test_stale_or_expired_lease_rejected(inv_scene):
    s = inv_scene
    # Stale generation
    with pytest.raises(LeaseConflict, match="stale_generation"):
        apply_audio_edit_and_invalidate(
            s.dictation,
            OP_CUT,
            s.author.id,
            s.lease.generation + 99,
            T1,
            start_ms=100,
            end_ms=200,
        )

    # Expired lease
    expired_time = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(LeaseConflict, match="expired"):
        apply_audio_edit_and_invalidate(
            s.dictation,
            OP_CUT,
            s.author.id,
            s.lease.generation,
            expired_time,
            start_ms=100,
            end_ms=200,
        )


def test_old_revisions_and_superseded_releases_remain_readable(inv_scene):
    s = inv_scene
    res = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_CUT,
        s.author.id,
        s.lease.generation,
        T1,
        start_ms=300,
        end_ms=600,
    )
    db.session.commit()

    # Old audio revision 1 is still readable with its original duration and EDL
    old_edl = get_revision_edl(s.dictation, s.audio)
    assert old_edl.total_duration_ms == 1000

    # New audio revision has cut duration
    new_edl = get_revision_edl(s.dictation, res.new_revision)
    assert new_edl.total_duration_ms == 700

    # Old transcript and all 3 original links remain in database
    all_links = db.session.query(ClinicTextAudioLink).filter_by(dictation_id=s.dictation.id).all()
    assert len(all_links) == 3

    # Superseded release remains in database and readable
    old_rel = db.session.get(ClinicRelease, s.release.id)
    assert old_rel is not None
    assert old_rel.audio_revision_id == s.audio.id
    assert old_rel.transcript_revision_id == s.asr.id


def test_manual_links_also_evaluated_and_invalidated(inv_scene):
    s = inv_scene
    # Set a manual link on range [0, 50] to audio [100, 200]
    man_link = set_manual_link(s.dictation, s.asr, 0, 8, 100, 200, s.author.id)
    db.session.commit()

    # Cut [150, 300]: manual link [100, 200] is affected
    res = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_CUT,
        s.author.id,
        s.lease.generation,
        T1,
        start_ms=150,
        end_ms=300,
    )
    db.session.commit()
    assert man_link.id in res.invalidated_link_ids
    assert is_link_invalidated(man_link.id, res.new_revision.id)


def test_invalidation_events_are_append_only(inv_scene):
    s = inv_scene
    res = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_CUT,
        s.author.id,
        s.lease.generation,
        T1,
        start_ms=350,
        end_ms=600,
    )
    db.session.commit()

    events = get_invalidation_events(s.dictation.id, res.new_revision.id)
    assert len(events) > 0
    ev = events[0]

    with pytest.raises(AudioInvalidationError, match="append-only"):
        ev.reason = "modified_reason"
        db.session.flush()

    db.session.rollback()

    with pytest.raises(AudioInvalidationError, match="append-only"):
        db.session.delete(ev)
        db.session.flush()


def test_undo_and_redo_invalidation(inv_scene):
    s = inv_scene
    # Cut [400, 600] -> Rev 2
    res_cut = apply_audio_edit_and_invalidate(
        s.dictation,
        OP_CUT,
        s.author.id,
        s.lease.generation,
        T1,
        start_ms=400,
        end_ms=600,
    )
    db.session.commit()
    assert current_audio_revision(s.dictation).id == res_cut.new_revision.id
    assert s.links[1].id in res_cut.invalidated_link_ids

    # Create new release on Rev 2
    rel2 = create_release(
        s.dictation,
        res_cut.new_revision,
        s.asr,
        s.context,
        s.asr.normalised_text_hash,
        s.author.id,
        {"allowed": True},
        T2,
        document_type="report",
        release_type="final",
    )
    db.session.commit()
    assert active_release(s.dictation).id == rel2.id

    # Undo the cut -> restores Rev 1
    t3 = datetime(2026, 11, 18, 10, 15, tzinfo=timezone.utc)
    res_undo = apply_audio_edit_and_invalidate(
        s.dictation,
        "UNDO",
        s.author.id,
        s.lease.generation,
        t3,
    )
    db.session.commit()

    # Audio head is now restored to Rev 1
    assert current_audio_revision(s.dictation).id == s.audio.id
    # Release 2 is now superseded because head moved from Rev 2
    assert active_release(s.dictation) is None
    assert rel2.id in [r.id for r in res_undo.superseded_releases]
    # In Rev 1 timeline, Link 1 [400, 550] is compatible with Rev 1
    edl_cur = get_revision_edl(s.dictation, s.audio)
    edl_orig = get_revision_edl(s.dictation, s.audio)
    assert is_interval_compatible(edl_orig, edl_cur, s.links[1].audio_start_ms, s.links[1].audio_end_ms)
