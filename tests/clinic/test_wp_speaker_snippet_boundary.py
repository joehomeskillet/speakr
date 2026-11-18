"""w122: upstream speaker snippet extraction stays inert in clinic mode.

Synthetic users, recordings and files only.
"""

import json

import pytest
from flask import Flask

from src.clinic import upstream_voice_purge as purge
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import Recording, Speaker, SpeakerSnippet, User
from src.services import speaker_identification as sid
from src.services.speaker import update_voice_profiles
from src.services.speaker_snippets import create_speaker_snippets, get_speaker_snippets

NAME = 'Synthetic Person'
TURNS = [
    {'speaker': NAME, 'sentence': 'Synthetischer Satz eins im Diktat.', 'start_time': 0.0, 'end_time': 5.0},
    {'speaker': 'SPEAKER_01', 'sentence': 'Anonymer Beitrag ohne Namen.', 'start_time': 5.0, 'end_time': 9.0},
    {'speaker': NAME, 'sentence': 'Synthetischer Satz zwei im Diktat.', 'start_time': 9.0, 'end_time': 14.0},
]
MAP = {'SPEAKER_00': {'name': NAME}}


@pytest.fixture
def env(tmp_path):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    db.init_app(app)
    audio = tmp_path / 'dictation.wav'
    audio.write_bytes(b'synthetic original')
    with app.app_context():
        db.create_all()
        admin = User(username='admin', email='admin@example.invalid', is_admin=True)
        author = User(username='author', email='author@example.invalid', auto_speaker_labelling=True)
        db.session.add_all([admin, author])
        db.session.flush()
        for user in (admin, author):
            db.session.add(ClinicAccountPolicy(user_id=user.id, status='active'))
        speaker = Speaker(user_id=author.id, name=NAME)
        rec = Recording(user_id=author.id, audio_path=str(audio), transcription=json.dumps(TURNS))
        db.session.add_all([speaker, rec])
        db.session.commit()
        yield admin, author, speaker, rec, tmp_path
        db.session.rollback()


def _files(root):
    return sorted((p.name, p.read_bytes()) for p in root.rglob('*') if p.is_file())


def test_upstream_extraction_unchanged_with_clinic_mode_off(env, clinic_off):
    _, _, speaker, rec, _ = env
    assert create_speaker_snippets(rec.id, MAP) == 2
    assert SpeakerSnippet.query.filter_by(speaker_id=speaker.id).count() == 2
    assert [s['recording_id'] for s in get_speaker_snippets(speaker.id)] == [rec.id]


def test_direct_call_writes_nothing_and_keeps_recording(env, clinic_on):
    _, _, speaker, rec, root = env
    before = _files(root)
    assert create_speaker_snippets(rec.id, MAP) == 0
    assert SpeakerSnippet.query.count() == 0
    assert get_speaker_snippets(speaker.id) == []
    stored = db.session.get(Recording, rec.id)
    assert json.loads(stored.transcription) == TURNS
    assert _files(root) == before


def test_stale_snippet_is_dropped_and_retry_stays_empty(env, clinic_on):
    _, _, speaker, rec, _ = env
    db.session.add(SpeakerSnippet(speaker_id=speaker.id, recording_id=rec.id, segment_index=0,
                                  text_snippet='Stale synthetic quote'))
    db.session.commit()
    for _ in range(2):
        assert create_speaker_snippets(rec.id, MAP) == 0
        assert SpeakerSnippet.query.count() == 0
    # Unknown recording and broken transcript take the same inert path.
    assert create_speaker_snippets(rec.id + 99, MAP) == 0
    rec.transcription = 'not json'
    db.session.commit()
    assert create_speaker_snippets(rec.id, MAP) == 0


def test_save_and_queued_paths_recreate_nothing_after_purge(env, clinic_on, monkeypatch):
    admin, author, speaker, rec, root = env
    admin_id, author_id, rec_id = admin.id, author.id, rec.id
    db.session.add(SpeakerSnippet(speaker_id=speaker.id, recording_id=rec_id, segment_index=0,
                                  text_snippet='Pre-purge synthetic quote'))
    db.session.commit()
    inventory = purge.purge_upstream_voice(actor_user_id=admin_id)
    assert inventory.counts['speaker_snippet'] == 1
    purge.purge_upstream_voice(actor_user_id=admin_id, dry_run=False, expected_version=inventory.version)
    db.session.expunge_all()  # The purge deletes through Core; drop the stale ORM objects.
    assert SpeakerSnippet.query.count() == 0 and Speaker.query.count() == 0
    before = _files(root)

    # The person is named again after the purge; a save must not rebuild snippets.
    rec = db.session.get(Recording, rec_id)
    rec.transcription = json.dumps(TURNS)
    db.session.add(Speaker(user_id=author_id, name=NAME))
    db.session.commit()
    user = db.session.get(User, author_id)
    assert update_voice_profiles(rec, {'SPEAKER_00': NAME}, user) == (0, 0)

    # Queued processing: contextual auto-labelling ends in the same extraction.
    rec.transcription = json.dumps([dict(t, speaker='SPEAKER_00') if t['speaker'] == NAME else t for t in TURNS])
    db.session.commit()
    monkeypatch.setattr(sid, 'identify_speakers_from_transcript', lambda *a, **k: {'SPEAKER_00': NAME})
    assert sid.apply_contextual_auto_labels(rec, user) == {'SPEAKER_00': NAME}

    assert SpeakerSnippet.query.count() == 0
    assert purge.purge_upstream_voice(actor_user_id=admin_id).counts['speaker_snippet'] == 0
    assert _files(root) == before
    assert db.session.get(Recording, rec_id).audio_path == str(root / 'dictation.wav')


def test_purge_refuses_non_admin(env, clinic_on):
    _, author, _, _, _ = env
    with pytest.raises(PermissionError):
        purge.purge_upstream_voice(actor_user_id=author.id)
