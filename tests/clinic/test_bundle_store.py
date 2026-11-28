import io
import wave
from contextlib import ExitStack
from hashlib import sha256

import pytest
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from src.clinic import audio_revisions, bundle_store, parity_receipts, registry, revisions
from src.clinic.app_wiring import make_source_loader
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.routes_bundle import text_to_document
from src.database import db
from src.models import Recording, User


def _wav(milliseconds, sample):
    target = io.BytesIO()
    with wave.open(target, 'wb') as writer:
        writer.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        writer.writeframes(sample * 16 * milliseconds)
    return target.getvalue()


def test_receipt_failure_rolls_back_revisions_and_removes_only_staged_audio(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    db.init_app(app)
    original_audio = _wav(50, b'\x01\x00')
    new_audio = _wav(20, b'\x02\x00')
    original_path = tmp_path / 'old.wav'
    original_path.write_bytes(original_audio)
    with app.app_context():
        db.create_all()
        registry.create_clinic_tables()
        actor = User(username='bundle-author', email='bundle-author@local.test', password='test')
        db.session.add(actor)
        db.session.flush()
        recording = Recording(user_id=actor.id, original_filename='old.wav')
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=actor.id)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, content_hash=sha256(original_audio).hexdigest(),
                                  size_bytes=len(original_audio), storage_key='old.wav', receipt='test', origin_ref='test')
        db.session.add(original)
        db.session.flush()
        previous_id = audio_revisions.init_audio_revision(dictation, original, 50).id
        scope = (actor.id, 'import_bundle', dictation.id, 'same-key')
        db.session.commit()

        def import_bundle(expected=0, audio=new_audio, key=scope):
            with ExitStack() as staged:
                def operation():
                    version = staged.enter_context(bundle_store.persist(
                        dictation, actor.id, text_to_document(b'first saved text'), audio, expected,
                        tmp_path, make_source_loader(tmp_path)))
                    return {'revision': version}

                return bundle_store.run_locked(key, 'digest', operation, parity_receipts.run_once,
                                               lambda *_: True)

        def fail_commit():
            assert len(list(tmp_path.glob('audio-segments/*/*.wav'))) == 1
            raise SQLAlchemyError('synthetic receipt failure')

        with monkeypatch.context() as patch:
            patch.setattr(db.session, 'commit', fail_commit)
            with pytest.raises(CommandProblem, match='DEPENDENCY_UNAVAILABLE'):
                import_bundle()
        assert db.session.query(parity_receipts.ClinicParityReceipt).count() == 0
        assert db.session.query(bundle_store.ClinicBundleDocument).count() == 0
        assert db.session.query(audio_revisions.ClinicAudioSegment).count() == 0
        assert revisions.current_revision(dictation) is None
        assert audio_revisions.current_audio_revision(dictation).id == previous_id
        assert not list(tmp_path.glob('audio-segments/*/*.wav'))
        assert original_path.read_bytes() == original_audio

        assert import_bundle() == import_bundle() == {'revision': 1}
        assert bundle_store.load(dictation, tmp_path, make_source_loader(tmp_path)).audio == new_audio
        current = audio_revisions.current_audio_revision(dictation)
        assert current.parent_revision_id == previous_id
        assert audio_revisions.get_revision_edl(dictation, current).total_duration_ms == 20
        assert audio_revisions.reconstruct_edl_from_operations(dictation).content_hash == current.content_hash
        assert audio_revisions.undo(dictation).id == previous_id
        assert audio_revisions.redo(dictation).id == current.id
        assert import_bundle(1, None, (*scope[:3], 'txt-key')) == {'revision': 2}
        assert bundle_store.text_version(dictation) == 2
        assert bundle_store.load(dictation, tmp_path, make_source_loader(tmp_path)).audio_synced is False
