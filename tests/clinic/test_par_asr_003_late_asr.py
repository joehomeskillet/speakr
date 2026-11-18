"""Cover late transcript revisions and exact-version release binding in the DB.

Synthetic text exercises the server services, not ASR execution, browser
timing, or Philips, Entra, KIS, and foot-pedal integrations.
"""

import pytest

from src.app import db
from src.clinic import revisions
from src.clinic.release import active_release, invalidate_on_change
from test_release_binding import T1, _audio, _context, _release
from test_release_binding import clinic_db  # noqa: F401 - registers the pytest fixture
from test_revisions import _scene

MODEL = "stub-asr:1"
pytestmark = pytest.mark.usefixtures("clinic_db")


@pytest.fixture
def corrected_dictation():
    author, dictation, original = _scene()
    asr = revisions.add_asr_revision(dictation, original, "Erster Entwurf", MODEL)
    human = revisions.add_human_revision(dictation, asr, "Erste Korrektur", author.id)
    return author, dictation, original, asr, human


def test_par_asr_003_two_late_results_remain_readable_in_chain_order(corrected_dictation) -> None:
    _author, dictation, original, asr, human = corrected_dictation
    first = revisions.add_asr_revision(dictation, original, "Später Entwurf", MODEL)
    assert revisions.current_revision(dictation) == human
    second = revisions.add_asr_revision(dictation, original, "Weiterer Entwurf", MODEL)
    db.session.expire_all()

    assert revisions.current_revision(dictation) == human
    history = revisions.history(dictation)
    assert [row.id for row in history] == [asr.id, human.id, first.id, second.id]
    assert [row.parent_revision_id for row in history] == [None, asr.id, human.id, first.id]
    assert [row.body for row in history] == [
        "Erster Entwurf", "Erste Korrektur", "Später Entwurf", "Weiterer Entwurf",
    ]
    assert revisions.is_flagged(dictation, asr) is False
    assert revisions.is_flagged(dictation, human) is False
    assert revisions.is_flagged(dictation, first) is True
    assert revisions.is_flagged(dictation, second) is True


def test_par_asr_003_retry_reuses_late_asr_tip(corrected_dictation) -> None:
    _author, dictation, original, asr, human = corrected_dictation
    late = revisions.add_asr_revision(dictation, original, "Später Entwurf", MODEL)
    retry = revisions.add_asr_revision(dictation, original, "Später Entwurf", MODEL)

    assert retry.id == late.id
    assert [row.id for row in revisions.history(dictation)] == [asr.id, human.id, late.id]
    assert revisions.current_revision(dictation) == human


def test_par_asr_003_second_human_revision_extends_human_branch(corrected_dictation) -> None:
    author, dictation, original, asr, human = corrected_dictation
    late = revisions.add_asr_revision(dictation, original, "Später Entwurf", MODEL)
    second = revisions.add_human_revision(dictation, human, "Zweite Korrektur", author.id)

    assert revisions.current_revision(dictation) == second
    assert second.parent_revision_id == human.id
    assert [row.id for row in revisions.history(dictation)] == [asr.id, human.id, second.id, late.id]
    assert revisions.is_flagged(dictation, late) is True


def test_par_asr_003_foreign_original_leaves_both_histories_unchanged(corrected_dictation) -> None:
    _author, dictation, original, asr, human = corrected_dictation
    _other_author, other, other_original = _scene()
    other_asr = revisions.add_asr_revision(other, other_original, "Anderes Diktat", MODEL)
    late = revisions.add_asr_revision(dictation, original, "Später Entwurf", MODEL)

    with pytest.raises(revisions.RevisionError, match="original version belongs to another dictation"):
        revisions.add_asr_revision(dictation, other_original, "Ungültiger Entwurf", MODEL)

    assert [row.id for row in revisions.history(dictation)] == [asr.id, human.id, late.id]
    assert [row.id for row in revisions.history(other)] == [other_asr.id]
    assert revisions.current_revision(dictation) == human
    assert revisions.current_revision(other) == other_asr


def test_par_asr_003_late_side_revision_makes_release_inactive_without_changing_head() -> None:
    author, dictation, original = _scene()
    audio = _audio(dictation)
    context = _context(dictation)
    asr = revisions.add_asr_revision(dictation, original, "Erster Entwurf", MODEL)
    initial_release = _release(author, dictation, audio, asr, context)
    assert active_release(dictation) == initial_release

    human = revisions.add_human_revision(dictation, asr, "Erste Korrektur", author.id)
    assert active_release(dictation) is None
    assert invalidate_on_change(dictation, T1) == [initial_release]

    corrected_release = _release(author, dictation, audio, human, context, now=T1)
    assert active_release(dictation) == corrected_release
    late = revisions.add_asr_revision(dictation, original, "Später Entwurf", MODEL)

    assert revisions.is_flagged(dictation, late) is True
    assert revisions.current_revision(dictation) == human
    assert active_release(dictation) is None
    assert invalidate_on_change(dictation, T1) == [corrected_release]
    db.session.refresh(corrected_release)
    assert corrected_release.transcript_revision_id == human.id
    assert corrected_release.text_hash == human.normalised_text_hash


def test_par_asr_003_late_revision_stores_normalised_body_and_hash(corrected_dictation) -> None:
    _author, dictation, original, _asr, _human = corrected_dictation
    raw = "U\u0308bung\r\nA\u0308nderung\r\nO\u0308ffnung"
    normalised = "Übung\nÄnderung\nÖffnung"
    late = revisions.add_asr_revision(dictation, original, raw, MODEL)
    db.session.refresh(late)

    assert late.body == normalised
    assert late.normalised_text_hash == revisions.text_hash(normalised) == revisions.text_hash(raw)
