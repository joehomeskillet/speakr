"""Atomic final ASR phrase commits against the clinic database and audio store."""

from datetime import datetime, timezone
from io import BytesIO
import wave

import pytest

from src.app import app, db
from src.clinic import audio_revisions, ingest, live_phrases, live_session, revisions
from src.clinic.app_wiring import make_source_loader
from src.clinic.audio_render import render_current_revision
from src.clinic.ingest import IngestService
from src.clinic.live_commit import commit_asr_phrase
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.models import Recording, User


def _wav(sample):
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(sample * 1600)
    return buffer.getvalue()


@pytest.fixture
def draft(tmp_path):
    with app.app_context():
        live_session.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        live_phrases.create_tables()
        owner = User(username='live-commit', email='live-commit@example.invalid', password='x')
        db.session.add(owner)
        db.session.flush()
        recording = Recording(user_id=owner.id, title='Synthetic live commit', status='PENDING')
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
        db.session.add(dictation)
        db.session.flush()
        now = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
        session = live_session.start(dictation.id, owner.id, now, client_id='browser')
        db.session.commit()
        yield dict(store=IngestService(tmp_path), dictation_id=dictation.id, session_id=session.id,
                   user_id=owner.id, client_id='browser', now=now, generation=1, sequence=1,
                   event_id='one', audio_start_ms=0, audio_end_ms=100, audio=_wav(b'\x01\x00'),
                   expected_audio_revision_id=None, expected_text_revision_id=None,
                   text='Erste Phrase.', model_ref='local-test')
        db.session.rollback()


def _next(draft, first):
    return dict(draft, sequence=2, event_id='two', audio_start_ms=100, audio_end_ms=200,
                audio=_wav(b'\x02\x00'), expected_audio_revision_id=first.audio_revision_id,
                expected_text_revision_id=first.transcript_revision_id, text='Zweite Phrase.')


def _checkpoint(draft):
    row = db.session.get(live_session.ClinicLiveSession, draft['session_id'])
    return row.ack_seq, row.audio_offset_ms, row.text_ref


def test_interrupted_commit_retries_and_renders_both_original_phrases(draft, monkeypatch):
    first = commit_asr_phrase(**draft)
    second_input = _next(draft, first)

    def interrupted():
        raise OSError('synthetic interruption before SQL commit')

    with monkeypatch.context() as patch:
        patch.setattr(db.session, 'commit', interrupted)
        with pytest.raises(OSError, match='synthetic interruption'):
            commit_asr_phrase(**second_input)
    assert _checkpoint(draft) == (1, 100, first.transcript_revision_id)
    assert ClinicAudioRevision.query.count() == ClinicTranscriptRevision.query.count() == 1
    assert live_phrases.ClinicLivePhrase.query.count() == 1
    assert audio_revisions.ClinicAudioSegment.query.count() == 0

    second = commit_asr_phrase(**second_input)
    assert first.status == second.status == 'server_saved'
    db.session.remove()
    assert commit_asr_phrase(**draft) == first
    assert commit_asr_phrase(**second_input) == second
    for change in ({'text': 'Anders.'}, {'expected_text_revision_id': None}, {'model_ref': 'other'}):
        with pytest.raises(live_phrases.PhraseError, match='sequence_conflict'):
            commit_asr_phrase(**dict(second_input, **change))
    assert _checkpoint(draft) == (2, 200, second.transcript_revision_id)
    assert live_phrases.ClinicLivePhrase.query.count() == 2
    assert ClinicAudioRevision.query.count() == ClinicTranscriptRevision.query.count() == 2
    assert ClinicOriginal.query.count() == audio_revisions.ClinicAudioSegment.query.count() == 1
    assert len(list(draft['store'].root.glob('clinic/originals/v1/*'))) == 2

    dictation = db.session.get(ClinicDictation, draft['dictation_id'])
    for receipt, data, text in ((first, draft, 'Erste Phrase.'),
                               (second, second_input, 'Erste Phrase.\nZweite Phrase.')):
        phrase = db.session.get(live_phrases.ClinicLivePhrase, receipt.phrase_id)
        transcript = db.session.get(ClinicTranscriptRevision, phrase.transcript_revision_id)
        assert phrase.dictation_id == transcript.dictation_id == dictation.id
        assert transcript.body == text and phrase.text_hash == revisions.text_hash(text)
        assert draft['store'].file_path(phrase.audio_storage_key).read_bytes() == data['audio']
    audio = audio_revisions.current_audio_revision(dictation)
    assert audio.id == second.audio_revision_id and audio.dictation_id == dictation.id
    edl = audio_revisions.get_revision_edl(dictation, audio)
    sources = make_source_loader(draft['store'].root)(dictation, edl)
    rendered = render_current_revision(dictation, audio.id, sources)
    with wave.open(BytesIO(rendered.wav), 'rb') as playback:
        assert playback.getnframes() == 3200
        assert playback.readframes(3200) == b'\x01\x00' * 1600 + b'\x02\x00' * 1600


def test_human_correction_survives_retry_and_rejects_pending_phrase(draft):
    first = commit_asr_phrase(**draft)
    dictation = db.session.get(ClinicDictation, draft['dictation_id'])
    human = revisions.add_human_revision(dictation, revisions.current_revision(dictation),
                                        'Menschlich korrigiert.', draft['user_id'])
    db.session.commit()

    assert commit_asr_phrase(**draft) == first
    with pytest.raises(live_phrases.PhraseError, match='stale_version'):
        commit_asr_phrase(**_next(draft, first))
    assert revisions.current_revision(dictation).id == human.id
    assert revisions.current_revision(dictation).body == 'Menschlich korrigiert.'
    assert ClinicAudioRevision.query.count() == live_phrases.ClinicLivePhrase.query.count() == 1
    assert ClinicTranscriptRevision.query.count() == 2
    assert _checkpoint(draft) == (1, 100, first.transcript_revision_id)


def test_retry_syncs_audio_left_by_interrupted_publication(draft, monkeypatch):
    def interrupted(_directory):
        raise OSError('synthetic directory sync failure')

    with monkeypatch.context() as patch:
        patch.setattr(ingest, '_fsync_dir', interrupted)
        for _ in range(2):
            with pytest.raises(OSError, match='synthetic directory sync failure'):
                commit_asr_phrase(**draft)
            assert _checkpoint(draft) == (0, 0, None)
            assert ClinicOriginal.query.count() == ClinicAudioRevision.query.count() == 0
            assert ClinicTranscriptRevision.query.count() == live_phrases.ClinicLivePhrase.query.count() == 0
    receipt = commit_asr_phrase(**draft)
    assert receipt.status == 'server_saved'
    assert draft['store'].file_path(receipt.audio_storage_key).read_bytes() == draft['audio']
    assert len(list(draft['store'].root.glob('clinic/originals/v1/*'))) == 1


@pytest.mark.parametrize('change', [
    {'audio': b'invalid'}, {'audio': _wav(b'\x01\x00')[:-2]}, {'audio_end_ms': 99},
])
def test_unplayable_audio_is_never_acknowledged(draft, change):
    with pytest.raises(live_phrases.PhraseError, match='invalid_audio'):
        commit_asr_phrase(**dict(draft, **change))
    assert _checkpoint(draft) == (0, 0, None)
    assert ClinicOriginal.query.count() == ClinicAudioRevision.query.count() == 0
    assert ClinicTranscriptRevision.query.count() == live_phrases.ClinicLivePhrase.query.count() == 0
