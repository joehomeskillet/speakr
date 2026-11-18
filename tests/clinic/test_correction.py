"""Correction service: markers, text/audio links, correction state.

Real database through the app fixtures, SQLite foreign keys on. No mocks.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic.correction import (
    ORIGIN_AUTO,
    ORIGIN_MANUAL,
    ClinicMarker,
    ClinicMarkerEvent,
    CorrectionConflict,
    CorrectionError,
    add_marker,
    complete_section,
    delete_marker,
    effective_links,
    get_state,
    hand_over,
    link_asr_segments,
    link_at_text,
    list_markers,
    marker_events,
    move_marker,
    open_sections,
    save_state,
    set_manual_link,
)
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision, add_human_revision
from src.models import Recording, User

BODY = "Anamnese Husten seit drei Tagen. Befund Lunge frei."
SEGMENTS = [
    {"text": "Anamnese Husten seit drei Tagen.", "start": 0.0, "end": 4.2},
    {"text": " Befund Lunge frei.", "start": 4.2, "end": 7.5},
]


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


def _user():
    tag = uuid.uuid4().hex[:10]
    user = User(username="co" + tag, email=tag + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _scene():
    """Two users, a dictation, an original and one ASR revision with BODY."""
    author, helper = _user(), _user()
    recording = Recording(user_id=author.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    asr = add_asr_revision(dictation, original, BODY, "model:test")
    return SimpleNamespace(
        author=author, helper=helper, dictation=dictation, original=original, asr=asr
    )


# ------------------------------------------------------------ markers


def test_markers_come_back_in_audio_order_with_stable_ties(clinic_db):
    s = _scene()
    late = add_marker(s.dictation, 9000, s.author.id, label="Befund")
    first = add_marker(s.dictation, 1000, s.author.id, label="Anamnese")
    tie_a = add_marker(s.dictation, 5000, s.author.id, label="A")
    tie_b = add_marker(s.dictation, 5000, s.author.id, label="B")
    assert [m.id for m in list_markers(s.dictation)] == [first.id, tie_a.id, tie_b.id, late.id]
    move_marker(first, 9500, s.author.id)
    assert [m.label for m in list_markers(s.dictation)] == ["A", "B", "Befund", "Anamnese"]


def test_marker_trace_records_actor_time_and_positions(clinic_db):
    s = _scene()
    marker = add_marker(s.dictation, 2000, s.author.id, label="Anamnese")
    move_marker(marker, 2500, s.helper.id)
    delete_marker(marker, s.author.id)
    events = marker_events(marker)
    assert [e.action for e in events] == ["CREATE", "MOVE", "DELETE"]
    assert [e.actor_user_id for e in events] == [s.author.id, s.helper.id, s.author.id]
    assert [(e.old_position_ms, e.new_position_ms) for e in events] == [
        (None, 2000), (2000, 2500), (2500, None),
    ]
    assert all(e.created_at is not None for e in events)
    assert list_markers(s.dictation) == []
    # soft delete: the row and its events remain
    assert db.session.get(ClinicMarker, marker.id).deleted_at is not None
    assert db.session.query(ClinicMarkerEvent).filter_by(marker_id=marker.id).count() == 3


def test_deleted_marker_cannot_move_again(clinic_db):
    s = _scene()
    marker = add_marker(s.dictation, 100, s.author.id)
    delete_marker(marker, s.author.id)
    with pytest.raises(CorrectionError):
        move_marker(marker, 200, s.author.id)


def test_marker_rejects_bad_input(clinic_db):
    s = _scene()
    with pytest.raises(CorrectionError):
        add_marker(s.dictation, -1, s.author.id)
    with pytest.raises(CorrectionError):
        add_marker(s.dictation, 10, s.author.id, marker_type="NOPE")
    with pytest.raises(CorrectionError):
        add_marker(s.dictation, 10, 999999999)
    with pytest.raises(CorrectionError):
        add_marker(s.dictation, 10, s.author.id, anchor_offset=3)
    with pytest.raises(CorrectionError):
        add_marker(s.dictation, 10, s.author.id, anchor_revision=s.asr, anchor_offset=len(BODY) + 1)


def test_marker_text_anchor_is_kept_and_checked_by_the_database(clinic_db):
    s = _scene()
    marker = add_marker(
        s.dictation, 4200, s.author.id, label="Befund", anchor_revision=s.asr, anchor_offset=34
    )
    assert (marker.anchor_revision_id, marker.anchor_offset) == (s.asr.id, 34)
    db.session.add(ClinicMarker(
        dictation_id=s.dictation.id, seq=99, marker_type="SECTION", position_ms=1,
        anchor_revision_id=s.asr.id, anchor_offset=None,
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_index_marker_type_is_separate_from_sections(clinic_db):
    s = _scene()
    add_marker(s.dictation, 100, s.author.id, label="Anamnese")
    add_marker(s.dictation, 200, s.author.id, marker_type="INDEX", label="Wiederholen")
    assert len(list_markers(s.dictation)) == 2
    assert [m.label for m in list_markers(s.dictation, "INDEX")] == ["Wiederholen"]


# ------------------------------------------------- text/audio links


def test_auto_links_come_from_asr_segments(clinic_db):
    s = _scene()
    links = link_asr_segments(s.dictation, s.asr, SEGMENTS)
    assert [(lk.text_start, lk.text_end, lk.audio_start_ms, lk.audio_end_ms) for lk in links] == [
        (0, 32, 0, 4200), (33, 51, 4200, 7500),
    ]
    assert {lk.origin for lk in links} == {ORIGIN_AUTO}
    assert all(lk.actor_user_id is None for lk in links)
    assert BODY[33:51] == "Befund Lunge frei."


def test_auto_links_are_stored_once_and_skip_unmatched_segments(clinic_db):
    s = _scene()
    first = link_asr_segments(s.dictation, s.asr, SEGMENTS + [
        {"text": "nicht im Text", "start": 8.0, "end": 9.0},
        {"text": "ohne Zeit"},
    ])
    again = link_asr_segments(s.dictation, s.asr, [])
    assert len(first) == 2
    assert [lk.id for lk in again] == [lk.id for lk in first]


def test_auto_links_accept_transcription_segment_objects(clinic_db):
    s = _scene()
    segment = SimpleNamespace(text="Anamnese Husten seit drei Tagen.", start_time=0.0, end_time=4.2)
    links = link_asr_segments(s.dictation, s.asr, [segment])
    assert len(links) == 1 and links[0].audio_end_ms == 4200


def test_manual_link_overrides_auto_and_both_stay_stored(clinic_db):
    s = _scene()
    link_asr_segments(s.dictation, s.asr, SEGMENTS)
    manual = set_manual_link(s.dictation, s.asr, 33, 51, 5000, 8000, s.helper.id)
    assert manual.origin == ORIGIN_MANUAL and manual.actor_user_id == s.helper.id
    effective = effective_links(s.dictation, s.asr)
    assert [(lk.origin, lk.audio_start_ms) for lk in effective] == [
        (ORIGIN_AUTO, 0), (ORIGIN_MANUAL, 5000),
    ]
    assert link_at_text(s.dictation, s.asr, 40).id == manual.id
    assert link_at_text(s.dictation, s.asr, 5).origin == ORIGIN_AUTO
    from src.clinic.correction import ClinicTextAudioLink
    assert db.session.query(ClinicTextAudioLink).count() == 3


def test_later_manual_link_wins_over_earlier_manual(clinic_db):
    s = _scene()
    first = set_manual_link(s.dictation, s.asr, 0, 10, 100, 200, s.author.id)
    second = set_manual_link(s.dictation, s.asr, 5, 15, 300, 400, s.helper.id)
    assert [lk.id for lk in effective_links(s.dictation, s.asr)] == [second.id]
    assert first.id != second.id


def test_untyped_text_has_no_invented_audio(clinic_db):
    s = _scene()
    human = add_human_revision(s.dictation, s.asr, BODY + " Neu getippt.", s.author.id)
    link_asr_segments(s.dictation, s.asr, SEGMENTS)
    assert effective_links(s.dictation, human) == []
    assert link_at_text(s.dictation, human, len(BODY) + 3) is None


def test_manual_link_rejects_bad_ranges_and_foreign_revision(clinic_db):
    s = _scene()
    other = _scene()
    with pytest.raises(CorrectionError):
        set_manual_link(s.dictation, s.asr, 5, 5, 0, 1, s.author.id)
    with pytest.raises(CorrectionError):
        set_manual_link(s.dictation, s.asr, 0, len(BODY) + 1, 0, 1, s.author.id)
    with pytest.raises(CorrectionError):
        set_manual_link(s.dictation, s.asr, 0, 5, 10, 5, s.author.id)
    with pytest.raises(CorrectionError):
        set_manual_link(s.dictation, other.asr, 0, 5, 0, 5, s.author.id)


# --------------------------------------------------- correction state


def test_state_saves_position_and_guards_stale_writes(clinic_db):
    s = _scene()
    state = save_state(
        s.dictation, s.author.id, 4200, text_revision=s.asr, text_offset=34
    )
    assert (state.audio_position_ms, state.text_offset, state.version) == (4200, 34, 1)
    state = save_state(s.dictation, s.author.id, 5000, expected_version=1)
    assert state.version == 2 and state.text_revision_id is None
    with pytest.raises(CorrectionConflict):
        save_state(s.dictation, s.author.id, 6000, expected_version=1)
    with pytest.raises(CorrectionConflict):
        save_state(s.dictation, s.author.id, 6000)
    assert get_state(s.dictation, s.author.id).audio_position_ms == 5000


def test_open_sections_shrink_as_sections_are_finished(clinic_db):
    s = _scene()
    anamnese = add_marker(s.dictation, 0, s.author.id, label="Anamnese")
    befund = add_marker(s.dictation, 4200, s.author.id, label="Befund")
    add_marker(s.dictation, 100, s.author.id, marker_type="INDEX")
    assert [m.id for m in open_sections(s.dictation, s.author.id)] == [anamnese.id, befund.id]
    complete_section(s.dictation, s.author.id, anamnese)
    complete_section(s.dictation, s.author.id, anamnese)
    assert [m.id for m in open_sections(s.dictation, s.author.id)] == [befund.id]
    delete_marker(befund, s.author.id)
    assert open_sections(s.dictation, s.author.id) == []


def test_complete_section_rejects_index_markers(clinic_db):
    s = _scene()
    index = add_marker(s.dictation, 100, s.author.id, marker_type="INDEX")
    with pytest.raises(CorrectionError):
        complete_section(s.dictation, s.author.id, index)


def test_hand_over_carries_the_state_and_leaves_the_source(clinic_db):
    s = _scene()
    anamnese = add_marker(s.dictation, 0, s.author.id, label="Anamnese")
    befund = add_marker(s.dictation, 4200, s.author.id, label="Befund")
    save_state(s.dictation, s.author.id, 4300, text_revision=s.asr, text_offset=40)
    complete_section(s.dictation, s.author.id, anamnese)
    received = hand_over(s.dictation, s.author.id, s.helper.id)
    assert received.user_id == s.helper.id
    assert received.handed_over_from_user_id == s.author.id
    assert (received.audio_position_ms, received.text_revision_id, received.text_offset) == (
        4300, s.asr.id, 40,
    )
    assert [m.id for m in open_sections(s.dictation, s.helper.id)] == [befund.id]
    source = get_state(s.dictation, s.author.id)
    assert source.handed_over_from_user_id is None and source.audio_position_ms == 4300
    # the receiver continues on their own row without touching the source
    save_state(s.dictation, s.helper.id, 6000, expected_version=received.version)
    assert get_state(s.dictation, s.author.id).audio_position_ms == 4300


def test_hand_over_needs_a_source_and_two_users(clinic_db):
    s = _scene()
    with pytest.raises(CorrectionError):
        hand_over(s.dictation, s.author.id, s.helper.id)
    save_state(s.dictation, s.author.id, 1)
    with pytest.raises(CorrectionError):
        hand_over(s.dictation, s.author.id, s.author.id)


def test_state_has_no_approval_field(clinic_db):
    s = _scene()
    state = save_state(s.dictation, s.author.id, 1)
    names = {c.name for c in state.__table__.columns}
    assert not {n for n in names if "approv" in n or "abnahme" in n}
