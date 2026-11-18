"""x11: upstream voice-embedding writers stay inert in clinic mode.

Synthetic users and vectors only.
"""

import json
from unittest.mock import patch

import pytest
from flask import Flask

from src.clinic.voice_embedding_guard import diarized_embeddings_to_store
from src.database import db
from src.models import Recording, Speaker, User
from src.services.speaker import update_voice_profiles
from src.services.speaker_embedding_matcher import update_speaker_embedding

VEC = [0.1] * 256
EMB = {'SPEAKER_00': VEC}


@pytest.fixture
def env():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        user = User(username='author', email='author@example.invalid')
        db.session.add(user)
        db.session.flush()
        speaker = Speaker(user_id=user.id, name='Synthetic Person')
        rec = Recording(user_id=user.id, audio_path='x.wav', transcription=json.dumps([]),
                        speaker_embeddings=EMB)
        db.session.add_all([speaker, rec])
        db.session.commit()
        yield user, speaker, rec
        db.session.rollback()


def test_diarized_embeddings_kept_with_clinic_mode_off(clinic_off):
    assert diarized_embeddings_to_store(EMB) == EMB
    assert diarized_embeddings_to_store({}) is None


def test_diarized_embeddings_dropped_in_clinic_mode(clinic_on):
    assert diarized_embeddings_to_store(EMB) is None


def test_update_voice_profiles_trains_with_clinic_mode_off(env, clinic_off):
    user, _, rec = env
    stats = {'stored': 1, 'rejected': 0, 'skipped_short': 0}
    with patch('src.services.voice_profiles.apply_names_to_profiles', return_value=stats) as apply:
        stored, _ = update_voice_profiles(rec, {}, user)
    apply.assert_called_once()
    assert stored == 1


def test_update_voice_profiles_skips_in_clinic_mode(env, clinic_on):
    user, _, rec = env
    with patch('src.services.voice_profiles.apply_names_to_profiles') as apply:
        stored, _ = update_voice_profiles(rec, {'SPEAKER_00': 'Synthetic Person'}, user)
    apply.assert_not_called()
    assert stored == 0
    assert rec.speaker_embeddings == EMB  # existing data untouched


def test_matcher_writes_with_clinic_mode_off(env, clinic_off):
    _, speaker, rec = env
    update_speaker_embedding(speaker, VEC, rec.id)
    assert speaker.average_embedding is not None
    assert speaker.embedding_count == 1


def test_matcher_writes_nothing_in_clinic_mode(env, clinic_on):
    _, speaker, rec = env
    assert update_speaker_embedding(speaker, VEC, rec.id) is None
    assert speaker.average_embedding is None
    assert not speaker.embedding_count
