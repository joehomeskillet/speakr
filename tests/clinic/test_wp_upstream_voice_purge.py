"""Synthetic evidence for the clinic's offline voice identity purge."""

import json
import struct

import pytest
from flask import Flask

from src.clinic import upstream_voice_purge as purge
from src.clinic import voice_consent as consent
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import AuditEvent, Recording, Speaker, SpeakerSnippet, SpeakerVoiceSample, TranscriptChunk, User

VOICE = struct.pack('<f', 1.0)


@pytest.fixture
def saved(tmp_path, clinic_on):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    db.init_app(app)
    store = tmp_path / 'sample-store'
    store.mkdir()
    sample = store / 'speaker_A_sample.mp3'
    sample.write_bytes(b'synthetic sample')
    audio = store / 'chunk.wav'
    audio.write_bytes(b'synthetic original')
    with app.app_context():
        db.create_all()
        admin = User(username='admin', email='a@example.invalid', is_admin=True)
        author = User(username='author', email='b@example.invalid')
        db.session.add_all([admin, author])
        db.session.flush()
        for user in (admin, author):
            db.session.add(ClinicAccountPolicy(user_id=user.id, status='active'))
        speaker = Speaker(user_id=author.id, name='Synthetic Name', aliases='["Synthetic Alias"]',
                          average_embedding=VOICE, embeddings_history=[{'source': 'legacy'}])
        rec = Recording(user_id=author.id, audio_path=str(audio), participants='Synthetic Name, Manual guest',
                        speaker_embeddings={'SPEAKER_01': [1]},
                        speaker_embeddings_space_id=12, speaker_label_map={'SPEAKER_01': speaker.name})
        db.session.add_all([speaker, rec])
        db.session.flush()
        rec.transcription = json.dumps([
            {'speaker': speaker.name, 'speaker_id': speaker.id, 'text': 'Synthetic Name in dictation.', 'start': 1},
            {'speaker': 'SPEAKER_02', 'sentence': 'Another turn', 'start_time': 2},
        ])
        rec.transcription_translation = json.dumps([{'speaker': speaker.name, 'text': 'Translation'}])
        db.session.add_all([
            SpeakerSnippet(speaker_id=speaker.id, recording_id=rec.id, segment_index=0, text_snippet='Sample quote'),
            SpeakerVoiceSample(user_id=author.id, speaker_id=speaker.id, embedding=VOICE, dimension=1),
            Recording(user_id=author.id, transcription='Plain text: Synthetic Name'),
            TranscriptChunk(user_id=author.id, recording_id=rec.id, chunk_index=0,
                            speaker_name=speaker.name, content='Synthetic Name in dictation.'),
        ])
        db.session.commit()
        consent.grant_consent(actor_user_id=author.id, user_id=author.id, statement_text='Synthetische Einwilligung')
        consent.enrol(author.id, consent.EmbeddingRef(VOICE, 1), actor_user_id=author.id)
        yield admin.id, author.id, rec.id, sample, audio


def test_purge_preserves_dictation_audio_and_opt_in(saved):
    admin, author, rid, sample, audio = saved
    history = consent.consent_history(author)
    consent_audit = consent.audit_entries(author)
    opts = dict(actor_user_id=admin, sample_store=sample.parent)
    inventory = purge.purge_upstream_voice(**opts)
    assert inventory.counts == {'speaker': 1, 'speaker_voice_sample': 1, 'speaker_snippet': 1,
                                'recordings': 1, 'transcript_chunks': 1, 'sample_files': 1}
    assert sample.exists() and Speaker.query.one().name == 'Synthetic Name'
    assert AuditEvent.query.count() == 0

    purge.purge_upstream_voice(**opts, dry_run=False, expected_version=inventory.version)
    assert not any(purge.purge_upstream_voice(**opts).counts.values())
    assert not sample.exists() and audio.read_bytes() == b'synthetic original'
    rec = db.session.get(Recording, rid)
    assert rec.audio_path == str(audio) and rec.participants == 'Manual guest'
    assert (rec.speaker_embeddings, rec.speaker_embeddings_space_id, rec.speaker_label_map) == (None, None, None)
    assert json.loads(rec.transcription) == [
        {'speaker': 'SPEAKER_01', 'text': 'Synthetic Name in dictation.', 'start': 1},
        {'speaker': 'SPEAKER_02', 'sentence': 'Another turn', 'start_time': 2},
    ]
    assert json.loads(rec.transcription_translation) == [{'speaker': 'SPEAKER_01', 'text': 'Translation'}]
    assert Recording.query.count() == 2
    assert Recording.query.filter_by(transcription='Plain text: Synthetic Name').count() == 1
    chunk = TranscriptChunk.query.one()
    assert chunk.speaker_name is None and chunk.content == 'Synthetic Name in dictation.'
    assert consent.consent_history(author) == history and consent.audit_entries(author) == consent_audit
    assert consent.ClinicVoiceEmbedding.query.one().embedding == VOICE
    events = AuditEvent.query.order_by(AuditEvent.id).all()
    assert [e.details['phase'] for e in events] == ['database_committed', 'complete']
    assert events[0].details['operation'] == events[1].details['operation']
    assert all(e.actor_user_id == admin and e.outcome == 'success' for e in events)
    assert 'Synthetic Name' not in str([e.details for e in events])


@pytest.mark.parametrize('reason', ['regular_user', 'disabled_admin', 'upstream_mode'])
def test_non_operator_cannot_purge(saved, monkeypatch, reason):
    admin, author, _, sample, _ = saved
    if reason == 'disabled_admin':
        db.session.get(ClinicAccountPolicy, admin).status = 'disabled'
        db.session.commit()
    if reason == 'upstream_mode':
        monkeypatch.delenv('CLINIC_MODE')
    with pytest.raises(PermissionError):
        purge.purge_upstream_voice(actor_user_id=author if reason == 'regular_user' else admin,
                                   sample_store=sample.parent, dry_run=False)
    assert sample.exists() and Speaker.query.count() == 1 and AuditEvent.query.count() == 0


def test_reject_stale_and_outside_samples(saved):
    admin, _, _, sample, _ = saved
    opts = dict(actor_user_id=admin, sample_store=sample.parent)
    old = purge.purge_upstream_voice(**opts)
    sample.write_bytes(b'replacement sample')
    with pytest.raises(ValueError, match='stale_purge_inventory'):
        purge.purge_upstream_voice(**opts, dry_run=False, expected_version=old.version)
    outside = sample.parent.parent / 'protected.wav'
    outside.write_bytes(b'protected')
    sample.with_name('speaker_B_sample.mp3').symlink_to(outside)
    with pytest.raises(ValueError, match='unsafe_sample_path'):
        purge.purge_upstream_voice(**opts)
    assert outside.read_bytes() == b'protected'
    assert sample.exists() and Speaker.query.count() == 1 and AuditEvent.query.count() == 0


def test_reject_same_count_database_changes_and_protect_original_path(saved):
    admin, _, rid, sample, _ = saved
    opts = dict(actor_user_id=admin, sample_store=sample.parent)
    old = purge.purge_upstream_voice(**opts)
    Speaker.query.one().average_embedding = b'new synthetic vector'
    db.session.commit()
    with pytest.raises(ValueError, match='stale_purge_inventory'):
        purge.purge_upstream_voice(**opts, dry_run=False, expected_version=old.version)
    db.session.get(Recording, rid).audio_path = str(sample)
    db.session.commit()
    with pytest.raises(ValueError, match='sample_path_matches_recording_audio'):
        purge.purge_upstream_voice(**opts)
    assert sample.exists() and Speaker.query.count() == 1 and AuditEvent.query.count() == 0


def test_missing_audit_rolls_back_before_unlink(saved, monkeypatch):
    admin, _, rid, sample, _ = saved
    opts = dict(actor_user_id=admin, sample_store=sample.parent)
    preview = purge.purge_upstream_voice(**opts)
    monkeypatch.setattr(purge.audit, 'record_in_tx', lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match='purge_audit_required'):
        purge.purge_upstream_voice(**opts, dry_run=False, expected_version=preview.version)
    assert sample.exists() and db.session.get(Recording, rid).speaker_embeddings
    assert purge.purge_upstream_voice(**opts) == preview


def test_retry_after_partial_file_failure(saved, monkeypatch):
    admin, _, _, first, _ = saved
    second = first.with_name('speaker_B_sample.mp3')
    second.write_bytes(b'second sample')
    opts = dict(actor_user_id=admin, sample_store=first.parent)
    unlink = purge.os.unlink

    def fail_second(name, *, dir_fd=None):
        if name == second.name:
            raise PermissionError('synthetic failure')
        return unlink(name, dir_fd=dir_fd)

    preview = purge.purge_upstream_voice(**opts)
    with monkeypatch.context() as patch:
        patch.setattr(purge.os, 'unlink', fail_second)
        with pytest.raises(RuntimeError, match='sample_purge_incomplete'):
            purge.purge_upstream_voice(**opts, dry_run=False, expected_version=preview.version)
    assert not first.exists() and second.exists()
    assert Speaker.query.count() == 0
    assert AuditEvent.query.filter_by(outcome='failed').one().details['phase'] == 'files_incomplete'
    remaining = purge.purge_upstream_voice(**opts)
    assert remaining.counts['sample_files'] == 1 and sum(remaining.counts.values()) == 1
    purge.purge_upstream_voice(**opts, dry_run=False, expected_version=remaining.version)
    assert not second.exists()
    empty = purge.purge_upstream_voice(**opts)
    purge.purge_upstream_voice(**opts, dry_run=False, expected_version=empty.version)
    assert not any(empty.counts.values()) and AuditEvent.query.count() == 4
