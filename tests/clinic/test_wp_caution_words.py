"""Caution words (PAR-VOC-004): open markers, revision-bound confirmations, pool isolation.

Real database through the app fixtures. No mocks.
"""

import uuid
from types import SimpleNamespace

import pytest

from src.app import app, db
from src.clinic.caution_words import (
    CautionRuleSet,
    confirm,
    confirmed_hits,
    find_hits,
    inspect,
    open_hits,
)
from src.clinic.correction import ClinicMarker, CorrectionError, marker_events
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision, add_human_revision
from src.models import Recording, User

BODY = "Verdacht auf Pneumonie, Allergie gegen Penicillin. Kein Verdacht auf Embolie."
POOL_A = "11111111-1111-1111-1111-111111111111"
POOL_B = "22222222-2222-2222-2222-222222222222"
RULES = CautionRuleSet(POOL_A, "v1", ("Verdacht", "Allergie"), authorized=True)


@pytest.fixture
def clinic_db():
    with app.app_context():
        db.create_all()
        db.session.rollback()
        yield
        db.session.rollback()


def _scene(pool_id=POOL_A):
    tag = uuid.uuid4().hex[:10]
    user = User(username="cw" + tag, email=tag + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    recording = Recording(user_id=user.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(
        recording_id=recording.id, author_user_id=user.id, pool_id=pool_id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    asr = add_asr_revision(dictation, original, BODY, "model:test")
    return SimpleNamespace(user=user, dictation=dictation, asr=asr)


def test_find_hits_whole_words_case_insensitive():
    assert find_hits("verdacht, Verdachts und VERDACHT", ["Verdacht"]) == [
        ("Verdacht", 0), ("Verdacht", 24)]


def test_matching_creates_open_markers_once(clinic_db):
    s = _scene()
    hits = inspect(s.dictation, RULES, s.user.id)
    assert [(h.word, h.offset) for h in hits] == [
        ("Verdacht", 0), ("Allergie", 24), ("Verdacht", 56)]
    assert [h.marker_id for h in inspect(s.dictation, RULES, s.user.id)] == [
        h.marker_id for h in hits]
    assert len(open_hits(s.dictation, RULES)) == 3


def test_two_hits_confirm_one_other_stays_open(clinic_db):
    s = _scene()
    rules = CautionRuleSet(POOL_A, "v1", ("Pneumonie", "Embolie"), authorized=True)
    first, second = inspect(s.dictation, rules, s.user.id)
    confirm(s.dictation, rules, first.marker_id, s.user.id)
    assert [h.marker_id for h in open_hits(s.dictation, rules)] == [second.marker_id]
    assert [h.marker_id for h in confirmed_hits(s.dictation, rules)] == [first.marker_id]
    assert [h.marker_id for h in inspect(s.dictation, rules, s.user.id)] == [second.marker_id]
    marker = db.session.get(ClinicMarker, first.marker_id)
    assert [e.action for e in marker_events(marker)] == ["CREATE", "DELETE"]


def test_edit_invalidates_superseded_confirmation(clinic_db):
    s = _scene()
    rules = CautionRuleSet(POOL_A, "v1", ("Embolie",), authorized=True)
    (hit,) = inspect(s.dictation, rules, s.user.id)
    confirm(s.dictation, rules, hit.marker_id, s.user.id)
    assert open_hits(s.dictation, rules) == []
    human = add_human_revision(s.dictation, s.asr, BODY + " Embolie ausgeschlossen.", s.user.id)
    assert confirmed_hits(s.dictation, rules) == []
    again = inspect(s.dictation, rules, s.user.id)
    assert [h.offset for h in again] == [hit.offset, len(BODY) + 1]
    assert human.id != s.asr.id
    with pytest.raises(CorrectionError):
        confirm(s.dictation, rules, hit.marker_id, s.user.id)


def test_new_rule_set_version_drops_old_confirmation(clinic_db):
    s = _scene()
    v1 = CautionRuleSet(POOL_A, "v1", ("Embolie",), authorized=True)
    v2 = CautionRuleSet(POOL_A, "v2", ("Embolie",), authorized=True)
    (hit,) = inspect(s.dictation, v1, s.user.id)
    confirm(s.dictation, v1, hit.marker_id, s.user.id)
    assert confirmed_hits(s.dictation, v2) == []
    assert len(inspect(s.dictation, v2, s.user.id)) == 1


def test_no_marker_leaks_to_another_pool(clinic_db):
    s = _scene(POOL_A)
    other = CautionRuleSet(POOL_B, "v1", ("Verdacht",), authorized=True)
    assert inspect(s.dictation, other, s.user.id) == []
    assert open_hits(s.dictation, other) == []
    inspect(s.dictation, RULES, s.user.id)
    assert open_hits(s.dictation, other) == []
    with pytest.raises(CorrectionError):
        confirm(s.dictation, other, open_hits(s.dictation, RULES)[0].marker_id, s.user.id)
    assert len(open_hits(s.dictation, RULES)) == 3


def test_unauthorized_rule_set_is_refused(clinic_db):
    s = _scene()
    with pytest.raises(CorrectionError):
        inspect(s.dictation, CautionRuleSet(POOL_A, "v1", ("Verdacht",)), s.user.id)
