"""The peaks lock name is gone before a ready file can be observed.

GET /audio/<id>/peaks answers 200 once the peaks file exists. The `.lock`
must already be gone at that moment, including on the `.failed` path.
"""

import os
import shutil
import tempfile
import uuid
from unittest.mock import patch

from src.app import app, db
from src.models import Recording, User
from src.services import waveform_peaks as peaks


def _lock_for(path):
    if path.endswith('.failed'):
        return path[:-len('.failed')] + '.lock'
    return path + '.lock'


def _is_publish(path):
    return path.endswith('.bin') or path.endswith('.failed')


def _record(events, real_remove, real_replace):
    def spy_remove(path):
        events.append(('remove', path))
        return real_remove(path)

    def spy_replace(src, dst):
        present = os.path.exists(_lock_for(dst)) if _is_publish(dst) else None
        events.append(('replace', dst, present))
        return real_replace(src, dst)

    return spy_remove, spy_replace


def _client(user_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
    return client


def _scene():
    folder = tempfile.mkdtemp(prefix='speakr_peaks_lock_')
    audio = os.path.join(folder, 'audio.wav')
    with open(audio, 'wb') as handle:
        handle.write(b'RIFF')
    with app.app_context():
        suffix = uuid.uuid4().hex[:9]
        user = User(username=f'plock_{suffix}', email=f'plock_{suffix}@test.local', password='x')
        db.session.add(user)
        db.session.commit()
        recording = Recording(
            user_id=user.id, title='peaks-lock', status='COMPLETED', audio_path=audio,
            original_filename='audio.wav', mime_type='audio/wav')
        db.session.add(recording)
        db.session.commit()
        recording_id = recording.id
        user_id = user.id
    return folder, user_id, recording_id


def _assert_lock_dropped_before_publish(events):
    published = [item for item in events if item[0] == 'replace' and _is_publish(item[1])]
    assert published, events
    first = events.index(published[0])
    assert published[0][2] is False
    assert any(item[0] == 'remove' and item[1].endswith('.lock') for item in events[:first])


def test_ready_response_has_no_lock_and_lock_goes_before_publish():
    folder, user_id, recording_id = _scene()
    events = []
    calls = []
    spy_remove, spy_replace = _record(events, peaks.os.remove, peaks.os.replace)

    def compute(_path):
        calls.append(_path)
        return b'peaks-ok'

    try:
        with patch.dict(app.config, {'UPLOAD_FOLDER': folder, 'WTF_CSRF_ENABLED': False}), \
             patch.object(peaks, 'compute_peaks', side_effect=compute), \
             patch.object(peaks.os, 'remove', spy_remove), \
             patch.object(peaks.os, 'replace', spy_replace):
            response = _client(user_id).get(f'/audio/{recording_id}/peaks')
            assert response.status_code == 200
            assert response.data == b'peaks-ok'
            names = os.listdir(peaks.cache_dir(folder))
            assert not any(name.endswith('.lock') for name in names)
            _assert_lock_dropped_before_publish(events)
            again = _client(user_id).get(f'/audio/{recording_id}/peaks')
            assert again.status_code == 200 and again.data == b'peaks-ok'
            assert len(calls) == 1
    finally:
        peaks.remove_peaks(folder, recording_id)
        shutil.rmtree(folder, ignore_errors=True)


def test_failed_publish_drops_the_lock_before_the_marker_is_visible():
    folder, user_id, recording_id = _scene()
    events = []
    calls = []
    spy_remove, spy_replace = _record(events, peaks.os.remove, peaks.os.replace)

    def compute(_path):
        calls.append(_path)
        raise peaks.PeaksError('cannot decode')

    try:
        with patch.dict(app.config, {'UPLOAD_FOLDER': folder, 'WTF_CSRF_ENABLED': False}), \
             patch.object(peaks, 'compute_peaks', side_effect=compute), \
             patch.object(peaks.os, 'remove', spy_remove), \
             patch.object(peaks.os, 'replace', spy_replace):
            response = _client(user_id).get(f'/audio/{recording_id}/peaks')
            assert response.status_code == 204 and response.data == b''
            names = os.listdir(peaks.cache_dir(folder))
            assert not any(name.endswith('.lock') for name in names)
            assert any(name.endswith('.failed') for name in names)
            _assert_lock_dropped_before_publish(events)
            assert _client(user_id).get(f'/audio/{recording_id}/peaks').status_code == 204
            assert len(calls) == 1
    finally:
        peaks.remove_peaks(folder, recording_id)
        shutil.rmtree(folder, ignore_errors=True)
