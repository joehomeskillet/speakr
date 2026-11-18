"""Database evidence for composing correction writes in a caller transaction."""

import pytest

from src.app import app, db
from src.clinic.correction import CorrectionConflict, CorrectionError, get_state, save_state
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.revisions import RevisionError, add_human_revision, history
from test_correction import BODY, _scene


class InterruptedSave(Exception):
    """Synthetic failure raised by the composite caller."""


@pytest.fixture
def saved_correction():
    with app.app_context():
        scene = _scene()
        ids = scene.dictation.id, scene.author.id, scene.asr.id
        save_state(scene.dictation, scene.author.id, 100, text_revision=scene.asr, text_offset=0)
        db.session.commit()
    return ids


def _load(ids):
    dictation_id, actor_id, revision_id = ids
    return (
        db.session.get(ClinicDictation, dictation_id),
        actor_id,
        db.session.get(ClinicTranscriptRevision, revision_id),
    )


def _assert_saved(ids, revisions, position):
    with app.app_context():
        dictation, actor_id, _ = _load(ids)
        assert [(row.id, row.body) for row in history(dictation)] == revisions
        state = get_state(dictation, actor_id)
        assert (state.text_revision_id, state.audio_position_ms, state.text_offset, state.version) == (
            revisions[-1][0], *position,
        )


@pytest.mark.parametrize("fail_after_position", [False, True], ids=["between-writes", "after-both"])
def test_caller_rolls_back_and_retries_both_writes(saved_correction, fail_after_position):
    baseline = [(saved_correction[2], BODY)]
    corrected = "Korrigierter Text."
    with app.app_context(), pytest.raises(InterruptedSave), db.session.begin():
        dictation, actor_id, parent = _load(saved_correction)
        revision = add_human_revision(dictation, parent, corrected, actor_id)
        assert revision.id and revision.parent_revision_id == parent.id
        if fail_after_position:
            state = save_state(
                dictation, actor_id, 500, text_revision=revision, text_offset=4, expected_version=1,
            )
            assert state.version == 2
        raise InterruptedSave

    _assert_saved(saved_correction, baseline, (100, 0, 1))

    with app.app_context(), db.session.begin():
        dictation, actor_id, parent = _load(saved_correction)
        revision = add_human_revision(dictation, parent, corrected, actor_id)
        revision_id = revision.id
        save_state(
            dictation, actor_id, 500, text_revision=revision, text_offset=4, expected_version=1,
        )

    _assert_saved(saved_correction, [*baseline, (revision_id, corrected)], (500, 4, 2))


def test_stale_position_rejects_a_composite_save(saved_correction):
    with (
        app.app_context(),
        pytest.raises(CorrectionConflict, match="state changed"),
        db.session.begin(),
    ):
        dictation, actor_id, parent = _load(saved_correction)
        revision = add_human_revision(dictation, parent, "Veralteter Entwurf.", actor_id)
        save_state(
            dictation, actor_id, 300, text_revision=revision, text_offset=0, expected_version=0,
        )

    _assert_saved(saved_correction, [(saved_correction[2], BODY)], (100, 0, 1))


def test_unknown_actor_is_rejected_by_both_primitives(saved_correction):
    with app.app_context():
        dictation, _, parent = _load(saved_correction)
        with pytest.raises(RevisionError, match="author user does not exist"):
            add_human_revision(dictation, parent, "Entwurf.", -1)
        with pytest.raises(CorrectionError, match="user does not exist"):
            save_state(dictation, -1, 300, text_revision=parent, text_offset=0)
        assert get_state(dictation, -1) is None
        db.session.commit()

    _assert_saved(saved_correction, [(saved_correction[2], BODY)], (100, 0, 1))
