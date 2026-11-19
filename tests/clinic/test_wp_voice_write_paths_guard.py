"""x08: the upstream voice-data write paths write nothing in clinic mode.

Per path one test with clinic mode on (nothing written) and one with it off
(upstream behaviour unchanged). Synthetic data only.
"""

import inspect
import json
from unittest import mock

import numpy as np
import pytest
from flask import Flask

from src import audio_chunking as ac
from src.database import db
from src.models import Recording, Speaker, SpeakerSnippet, User
from src.services import speaker_merge
from src.services.speaker_embedding_matcher import deserialize_embedding, serialize_embedding
from src.services.speaker_snippets import create_speaker_snippets


# --- 1. speaker_merge._combine_embeddings -----------------------------------

def _speakers():
    target = Speaker(name='Sprecher 1', average_embedding=serialize_embedding([1.0, 0.0]), embedding_count=1)
    source = Speaker(name='Sprecher 2', average_embedding=serialize_embedding([0.0, 1.0]), embedding_count=3)
    return target, source


def test_combine_embeddings_writes_nothing_in_clinic_mode(clinic_on):
    target, source = _speakers()
    before = target.average_embedding
    speaker_merge._combine_embeddings(target, [source])
    assert target.average_embedding == before
    assert target.embedding_count == 1


def test_combine_embeddings_upstream_unchanged(clinic_off):
    target, source = _speakers()
    speaker_merge._combine_embeddings(target, [source])
    assert np.allclose(deserialize_embedding(target.average_embedding), [0.25, 0.75])
    assert target.embedding_count == 4


def test_merge_speakers_does_not_call_combine_embeddings():
    # merge_speakers moves samples and calls voice_profiles.refresh_speaker_summary
    # instead; _combine_embeddings has no caller in src/ (see report).
    assert '_combine_embeddings' not in inspect.getsource(speaker_merge.merge_speakers)


# --- 2. speaker_snippets.create_speaker_snippets ------------------------------

@pytest.fixture
def saved_recording():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        user = User(username='author', email='author@example.invalid')
        db.session.add(user)
        db.session.flush()
        speaker = Speaker(user_id=user.id, name='Synthetic Author')
        rec = Recording(user_id=user.id, transcription=json.dumps([
            {'speaker': 'SPEAKER_00', 'sentence': 'Synthetischer Satz mit genug Text.', 'start_time': 1.0},
            {'speaker': 'SPEAKER_00', 'sentence': 'Noch ein synthetischer Satz hier.', 'start_time': 4.0},
        ]))
        db.session.add_all([speaker, rec])
        db.session.flush()
        old = SpeakerSnippet(speaker_id=speaker.id, recording_id=rec.id, segment_index=9, text_snippet='Altdaten')
        db.session.add(old)
        db.session.commit()
        yield rec.id, old.id
        db.session.remove()


SPEAKER_MAP = {'SPEAKER_00': {'name': 'Synthetic Author'}}


def test_create_speaker_snippets_writes_nothing_in_clinic_mode(clinic_on, saved_recording):
    rec_id, old_id = saved_recording
    assert create_speaker_snippets(rec_id, SPEAKER_MAP) == 0
    # No new row; the recording's stale row is dropped (w122 path, owner-approved 2026-11-18).
    assert old_id is not None
    assert [s.id for s in SpeakerSnippet.query.all()] == []
    assert Speaker.query.count() == 1


def test_create_speaker_snippets_upstream_unchanged(clinic_off, saved_recording):
    rec_id, old_id = saved_recording
    assert create_speaker_snippets(rec_id, SPEAKER_MAP) == 2
    texts = sorted(s.text_snippet for s in SpeakerSnippet.query.all())
    assert texts == ['Noch ein synthetischer Satz hier.', 'Synthetischer Satz mit genug Text.']


# --- 3. audio_chunking.extract_speaker_samples --------------------------------

SEGMENTS = [
    {'speaker': 'A', 'start_time': 1.0, 'end_time': 6.0},
    {'speaker': 'B', 'start_time': 7.0, 'end_time': 12.0},
]


def _fake_run(cmd, **kwargs):
    with open(cmd[-1], 'wb') as handle:
        handle.write(b'synthetic sample')
    return mock.Mock(returncode=0, stderr=b'')


def _extract(tmp_path):
    audio = tmp_path / 'chunk.mp3'
    audio.write_bytes(b'synthetic audio')
    with mock.patch('subprocess.run', side_effect=_fake_run) as run, \
            mock.patch.object(ac, 'get_audio_duration_ffprobe', return_value=5.0):
        out = ac.extract_speaker_samples(str(audio), SEGMENTS, str(tmp_path))
    return out, run


def test_extract_speaker_samples_writes_nothing_in_clinic_mode(clinic_on, tmp_path):
    out, run = _extract(tmp_path)
    assert out == {}
    run.assert_not_called()
    assert list(tmp_path.glob('speaker_*_sample.mp3')) == []


def test_extract_speaker_samples_upstream_unchanged(clinic_off, tmp_path):
    out, run = _extract(tmp_path)
    assert set(out) == {'A', 'B'}
    assert sorted(p.name for p in tmp_path.glob('speaker_*_sample.mp3')) == [
        'speaker_A_sample.mp3', 'speaker_B_sample.mp3']
