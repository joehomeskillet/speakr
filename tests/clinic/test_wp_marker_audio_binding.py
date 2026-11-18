"""Synthetic service evidence for revision-bound correction markers."""

from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic import policy
from src.clinic.audio_revisions import apply_trim, get_revision_edl, init_audio_revision
from src.clinic.correction import (
    ClinicMarker, CorrectionConflict, CorrectionError, add_marker, delete_marker,
    list_markers, marker_events, move_marker,
)
from src.clinic.lease import acquire
from test_correction import _scene


@pytest.fixture
def audio_scene():
    with app.app_context():
        scene = _scene()
        scene.audio = init_audio_revision(scene.dictation, scene.original, 1000)
        scene.lease = acquire(scene.dictation.id, scene.author.id, datetime.now(timezone.utc))
        yield scene


@pytest.mark.parametrize("marker_type", ["SECTION", "INDEX"])
def test_marker_uses_its_explicit_revision_and_bounds(clinic_on, audio_scene, marker_type):
    s = audio_scene
    head = apply_trim(s.dictation, 300)
    kwargs = dict(marker_type=marker_type, lease_generation=s.lease.generation)
    for revision_id in (None, "missing", head.id):
        with pytest.raises(CorrectionError):
            add_marker(s.dictation, 1000, s.author.id, audio_revision_id=revision_id, **kwargs)
    foreign = _scene()
    other = init_audio_revision(foreign.dictation, foreign.original, 1000)
    with pytest.raises(CorrectionError, match="unavailable"):
        add_marker(s.dictation, 1000, s.author.id, audio_revision_id=other.id, **kwargs)
    assert list_markers(s.dictation) == []
    marker = add_marker(s.dictation, 1000, s.author.id, audio_revision_id=s.audio.id, **kwargs)
    assert (marker.audio_revision_id, marker.position_ms, marker.version) == (s.audio.id, 1000, 1)


def test_legacy_marker_stays_unaligned_after_audio_edits(clinic_off, audio_scene, monkeypatch):
    s = audio_scene
    marker = add_marker(s.dictation, 100, s.author.id, anchor_revision=s.asr, anchor_offset=0)
    monkeypatch.setenv("CLINIC_MODE", "1")
    assert list_markers(s.dictation)[0].audio_revision_id is None  # even with a single revision
    apply_trim(s.dictation, 300)
    assert list_markers(s.dictation)[0].audio_revision_id is None
    assert marker_events(marker)[0].audio_revision_id is None
    with pytest.raises(CorrectionError, match="explicit audio revision"):
        move_marker(marker, 150, s.author.id, expected_version=1, lease_generation=s.lease.generation)
    assert marker.position_ms == 100 and marker.version == 1


def test_marker_edit_requires_current_policy_and_lease(clinic_on, audio_scene):
    s = audio_scene
    kwargs = dict(audio_revision_id=s.audio.id, lease_generation=s.lease.generation)
    with pytest.raises(CorrectionError, match="not permitted"):
        add_marker(s.dictation, 100, s.helper.id, **kwargs)
    marker = add_marker(s.dictation, 100, s.author.id, **kwargs)
    policy.register_grant_provider(lambda uid: (
        [policy.Grant("all", frozenset({"edit"}))] if uid == s.helper.id else
        [policy.Deny(recording_ids=frozenset({s.dictation.recording_id}))]
    ))
    edit = dict(expected_version=1, lease_generation=s.lease.generation)
    with pytest.raises(CorrectionError, match="not permitted"):
        delete_marker(marker, s.author.id, **edit)
    with pytest.raises(CorrectionConflict, match="foreign_holder"):
        move_marker(marker, 200, s.helper.id, **edit)
    assert (marker.position_ms, marker.deleted_at, marker.version) == (100, None, 1)


def test_move_rollback_retry_and_delete_preserve_original_revision(clinic_on, audio_scene):
    s = audio_scene
    generation = s.lease.generation
    original_id = s.audio.id
    marker = add_marker(s.dictation, 200, s.author.id, audio_revision_id=original_id,
                        lease_generation=generation)
    head = apply_trim(s.dictation, 300)
    marker_id = marker.id
    db.session.commit()

    def move(position, version):
        return move_marker(marker, position, s.author.id, expected_version=version,
                           lease_generation=generation)

    with pytest.raises(CorrectionError, match="outside"):
        move(1001, 1)
    move(900, 1)  # valid on the marker's revision, beyond the head's end
    db.session.rollback()
    assert (marker.position_ms, marker.version) == (200, 1)
    move(900, 1)
    with pytest.raises(CorrectionConflict):
        move(500, 1)
    delete_marker(marker, s.author.id, expected_version=2, lease_generation=generation)
    db.session.commit()
    db.session.expire_all()
    with pytest.raises(CorrectionConflict):
        delete_marker(marker, s.author.id, expected_version=2, lease_generation=generation)
    assert list_markers(s.dictation) == []
    assert db.session.get(ClinicMarker, marker_id).audio_revision_id == original_id
    assert [(e.action, e.audio_revision_id, e.old_position_ms, e.new_position_ms)
            for e in marker_events(marker)] == [
        ("CREATE", original_id, None, 200), ("MOVE", original_id, 200, 900),
        ("DELETE", original_id, 900, None),
    ]
    assert get_revision_edl(s.dictation, head).total_duration_ms == 300
    assert get_revision_edl(s.dictation, s.audio).total_duration_ms == 1000
