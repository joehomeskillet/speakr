"""w120: no upstream voice profile is written in clinic mode; own-user opt-in only.

Synthetic users, vectors and recordings only.
"""

import json

import numpy as np
import pytest
from flask import Flask

from src.clinic import voice_consent as consent
from src.database import db
from src.models import Recording, Speaker, SpeakerVoiceSample, User, VoiceEmbeddingSpace
from src.services import voice_profiles as vp
from src.services.speaker_embedding_matcher import update_speaker_profiles_from_recording

DIM = 8
VOICE = [float(x) for x in np.linspace(1.0, 2.0, DIM)]
STATEMENT = 'Synthetische Einwilligung zur Stimmerkennung'


@pytest.fixture
def env():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        vp._calibration_cache.clear()
        author = User(username='author', email='author@example.invalid')
        other = User(username='other', email='other@example.invalid')
        db.session.add_all([author, other])
        db.session.flush()
        speaker = Speaker(user_id=author.id, name='Synthetic Person')
        space = VoiceEmbeddingSpace(dimension=DIM, canary_embedding=VOICE)
        db.session.add_all([speaker, space])
        db.session.flush()
        rec = Recording(user_id=author.id, speaker_embeddings={'SPEAKER_00': VOICE},
                        speaker_embeddings_space_id=space.id,
                        transcription=json.dumps([{'speaker': 'Synthetic Person', 'text': 'Synthetisch',
                                                   'start_time': 0, 'end_time': 30}]))
        db.session.add(rec)
        db.session.commit()
        yield author, other, speaker, rec, space
        db.session.rollback()
    vp._calibration_cache.clear()


def _names(rec, user):
    return vp.apply_names_to_profiles(rec, {'SPEAKER_00': 'Synthetic Person'}, None, user)


def test_clinic_mode_blocks_save_training_but_keeps_turn_labels(env, clinic_on):
    author, _, speaker, rec, _ = env
    stats = _names(rec, author)
    db.session.commit()
    assert stats['stored'] == 0
    assert SpeakerVoiceSample.query.count() == 0
    assert db.session.get(Speaker, speaker.id).average_embedding is None
    # The transcript's turns keep their label mapping; only the voice is not kept.
    assert db.session.get(Recording, rec.id).speaker_label_map == {'SPEAKER_00': 'Synthetic Person'}


def test_upstream_training_unchanged_with_clinic_mode_off(env, clinic_off):
    author, _, speaker, rec, _ = env
    assert _names(rec, author)['stored'] == 1
    assert SpeakerVoiceSample.query.filter_by(speaker_id=speaker.id).count() == 1


def test_direct_record_sample_drops_stale_sample(env, clinic_on):
    author, _, speaker, rec, _ = env
    db.session.add(SpeakerVoiceSample(user_id=author.id, speaker_id=speaker.id, recording_id=rec.id,
                                      label='SPEAKER_00', embedding=vp.to_bytes(vp.normalize(VOICE)),
                                      dimension=DIM, space_id=rec.speaker_embeddings_space_id))
    db.session.commit()
    assert vp.record_sample(speaker, rec, 'SPEAKER_00', VOICE, 30.0) == 'blocked'
    assert SpeakerVoiceSample.query.count() == 0
    # A diarization label never gets a sample, in either mode.
    assert vp.record_sample(Speaker(user_id=author.id, name='SPEAKER_01'), rec, 'SPEAKER_00', VOICE) == 'invalid'


def test_delayed_auto_label_task_cannot_recreate_after_purge(env, clinic_on):
    author, _, speaker, rec, _ = env
    # Post-purge state: the person was re-created by a save, no sample exists.
    assert update_speaker_profiles_from_recording(rec, {'SPEAKER_00': 'Synthetic Person'}, author) == 0
    assert SpeakerVoiceSample.query.count() == 0
    assert db.session.get(Speaker, speaker.id).average_embedding is None


def test_sample_refresh_never_materializes_legacy_profile(env, clinic_on):
    _, _, speaker, _, _ = env
    speaker.average_embedding = vp.to_bytes(vp.normalize(VOICE))
    speaker.embedding_count = 3
    db.session.commit()
    vp._materialize_legacy(speaker)
    assert SpeakerVoiceSample.query.count() == 0
    vp.refresh_speaker_summary(speaker)
    db.session.commit()
    refreshed = db.session.get(Speaker, speaker.id)
    assert (refreshed.average_embedding, refreshed.embedding_count) == (None, 0)


def test_own_enrolment_goes_to_consent_store_in_model_space(env, clinic_on):
    author, _, _, rec, space = env
    with pytest.raises(consent.VoiceConsentError) as missing:
        vp.enrol_own_voice(actor_user_id=author.id, user_id=author.id, vector=VOICE, space_id=space.id)
    assert missing.value.code == 'consent_required'

    granted = consent.grant_consent(actor_user_id=author.id, user_id=author.id, statement_text=STATEMENT)
    row = vp.enrol_own_voice(actor_user_id=author.id, user_id=author.id, vector=VOICE,
                             space_id=rec.speaker_embeddings_space_id, expected_consent_id=granted.id)
    assert (row.user_id, row.space_id, row.dimension) == (author.id, space.id, DIM)
    assert np.allclose(np.frombuffer(row.embedding, dtype=np.float32), vp.normalize(VOICE))
    assert consent.embedding_count(author.id) == 1
    assert SpeakerVoiceSample.query.count() == 0


def test_enrolment_refusals_write_nothing(env, clinic_on):
    author, other, _, _, space = env
    granted = consent.grant_consent(actor_user_id=author.id, user_id=author.id, statement_text=STATEMENT)
    cases = [
        (dict(actor_user_id=other.id, user_id=author.id, vector=VOICE, space_id=space.id), 'not_self'),
        (dict(actor_user_id=author.id, user_id=author.id, vector=VOICE, space_id=space.id,
              expected_consent_id=granted.id + 1), 'stale_consent'),
        (dict(actor_user_id=author.id, user_id=author.id, vector=VOICE), 'space_required'),
        (dict(actor_user_id=author.id, user_id=author.id, vector=[0.0] * DIM, space_id=space.id),
         'invalid_embedding'),
        (dict(actor_user_id=author.id, user_id=author.id, vector=VOICE, space_id=space.id + 99), 'invalid_space'),
    ]
    for kwargs, code in cases:
        with pytest.raises(consent.VoiceConsentError) as err:
            vp.enrol_own_voice(**kwargs)
        assert err.value.code == code
    assert consent.embedding_count(author.id) == 0
    assert consent.embedding_count(other.id) == 0
    # Retry after the failures with the current version succeeds.
    vp.enrol_own_voice(actor_user_id=author.id, user_id=author.id, vector=VOICE, space_id=space.id,
                       expected_consent_id=granted.id)
    assert consent.embedding_count(author.id) == 1


def test_withdrawal_deletes_and_delayed_retry_is_refused(env, clinic_on):
    author, _, _, rec, space = env
    granted = consent.grant_consent(actor_user_id=author.id, user_id=author.id, statement_text=STATEMENT)
    vp.enrol_own_voice(actor_user_id=author.id, user_id=author.id, vector=VOICE, space_id=space.id)
    assert consent.revoke_consent(actor_user_id=author.id, user_id=author.id, expected_consent_id=granted.id) == 1
    assert consent.embedding_count(author.id) == 0
    for kwargs in (dict(), dict(expected_consent_id=granted.id)):
        with pytest.raises(consent.VoiceConsentError):
            vp.enrol_own_voice(actor_user_id=author.id, user_id=author.id, vector=VOICE, space_id=space.id, **kwargs)
    # Neither path re-creates a voice: not the consent store, not the upstream samples.
    _names(rec, author)
    db.session.commit()
    assert consent.embedding_count(author.id) == 0
    assert SpeakerVoiceSample.query.count() == 0
