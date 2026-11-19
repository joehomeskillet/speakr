"""Clinic mode: only users with active own voice consent are named in transcripts."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.app import app
from src.clinic import voice_consent as vc
from src.models import User
from src.services.speaker_embedding_matcher import apply_speaker_names_to_transcription
from src.services.speaker_identification import apply_contextual_auto_labels

TEXT = "Ich willige in die Erstellung eines Stimmprofils zur Autorenerkennung ein."


def _recording():
    segments = [
        {"speaker": "SPEAKER_00", "sentence": "Guten Tag."},
        {"speaker": "SPEAKER_01", "sentence": "Hallo."},
    ]
    return SimpleNamespace(id=1, transcription=json.dumps(segments), participants=None)


def _speakers(rec):
    return [s["speaker"] for s in json.loads(rec.transcription)]


@pytest.fixture
def names(world):
    with app.app_context():
        vc.create_tables()
        yield (User.query.get(world["user_a"]), User.query.get(world["user_b"]))


def test_matcher_names_only_consented_user(names, clinic_on):
    consented, other = names
    vc.grant_consent(actor_user_id=consented.id, user_id=consented.id, statement_text=TEXT)
    rec = _recording()
    mapping = {"SPEAKER_00": consented.username, "SPEAKER_01": other.username}
    assert apply_speaker_names_to_transcription(rec, mapping) is True
    assert _speakers(rec) == [consented.username, "SPEAKER_01"]
    assert [s["sentence"] for s in json.loads(rec.transcription)] == ["Guten Tag.", "Hallo."]


def test_matcher_withdrawn_consent_stays_neutral(names, clinic_on):
    consented, _ = names
    vc.grant_consent(actor_user_id=consented.id, user_id=consented.id, statement_text=TEXT)
    vc.revoke_consent(actor_user_id=consented.id, user_id=consented.id)
    rec = _recording()
    before = rec.transcription
    assert apply_speaker_names_to_transcription(rec, {"SPEAKER_00": consented.username}) is False
    assert rec.transcription == before


def test_matcher_nobody_consented_changes_nothing(names, clinic_on):
    _, other = names
    rec = _recording()
    before = rec.transcription
    assert apply_speaker_names_to_transcription(rec, {"SPEAKER_01": other.username}) is False
    assert rec.transcription == before


def test_matcher_clinic_off_names_everyone(names, clinic_off):
    consented, other = names
    rec = _recording()
    mapping = {"SPEAKER_00": consented.username, "SPEAKER_01": other.username}
    assert apply_speaker_names_to_transcription(rec, mapping) is True
    assert _speakers(rec) == [consented.username, other.username]


def test_contextual_labelling_names_nobody_in_clinic_mode(names, clinic_on):
    user = SimpleNamespace(id=1, auto_speaker_labelling=True)
    rec = _recording()
    before = rec.transcription
    with patch("src.services.speaker_identification.identify_speakers_from_transcript") as llm, \
            patch("src.services.speaker_embedding_matcher.apply_speaker_names_to_transcription") as apply:
        assert apply_contextual_auto_labels(rec, user) == {}
    llm.assert_not_called()
    apply.assert_not_called()
    assert rec.transcription == before


def test_contextual_labelling_clinic_off_still_runs(names, clinic_off):
    user = SimpleNamespace(id=1, auto_speaker_labelling=True)
    rec = _recording()
    speaker = SimpleNamespace(name="Dr Synthetic")
    with patch("src.models.Speaker") as model, \
            patch("src.services.speaker_identification.identify_speakers_from_transcript",
                  return_value={"SPEAKER_00": "Dr Synthetic"}) as llm, \
            patch("src.services.speaker_embedding_matcher.apply_speaker_names_to_transcription",
                  return_value=True), \
            patch("src.services.speaker_snippets.create_speaker_snippets"):
        model.query.filter_by.return_value.order_by.return_value.all.return_value = [speaker]
        assert apply_contextual_auto_labels(rec, user) == {"SPEAKER_00": "Dr Synthetic"}
    llm.assert_called_once()
