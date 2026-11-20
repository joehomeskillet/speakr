"""DS2/DSS playback: /audio serves a browser-playable MP3 derivative.

Synthetic bytes and a stub decoder (see test_ds2_upload.py); the original stays
byte-identical and is still what ``?download=true`` returns.
"""
import uuid

import pytest

from src.app import app, db
from src.clinic import decoder, dictation_playback, policy
from src.models import Recording, User

from test_ds2_upload import DS2, stub_decoder  # noqa: F401  (fixture)


@pytest.fixture
def owner():
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        user = User(username=f'pb_{suffix}', email=f'pb_{suffix}@local.test', password='x')
        db.session.add(user)
        db.session.commit()
        yield user.id


def _client(uid):
    from flask import g, has_app_context
    if has_app_context():
        g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    return client


def _ds2_recording(uid, tmp_path):
    src = tmp_path / f'orig_{uuid.uuid4().hex[:6]}.ds2'
    src.write_bytes(DS2)
    with app.app_context():
        rec = Recording(user_id=uid, title='Diktat', status='COMPLETED', original_filename='d.ds2',
                        audio_path=str(src), mime_type='audio/x-ds2')
        db.session.add(rec)
        db.session.commit()
        return rec.id, src


def test_lazy_derivative_is_served_with_range_and_original_untouched(
        clinic_on, owner, tmp_path, stub_decoder):
    stub_decoder()
    rec_id, src = _ds2_recording(owner, tmp_path)
    assert not (tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}').exists()
    client = _client(owner)

    full = client.get(f'/audio/{rec_id}')
    assert full.status_code == 200
    assert full.mimetype == 'audio/mpeg'
    assert full.get_data()[:3] == b'ID3' or full.get_data()[0] == 0xFF

    part = client.get(f'/audio/{rec_id}', headers={'Range': 'bytes=0-9'})
    assert part.status_code == 206
    assert part.mimetype == 'audio/mpeg'
    assert len(part.get_data()) == 10

    assert src.read_bytes() == DS2
    assert (tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}').exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith('.playback-')]


@pytest.mark.parametrize('route', [
    '/audio/{rid}?download=true',
    '/api/v1/recordings/{rid}/audio',
    '/api/v1/recordings/{rid}/audio?download=true',
])
def test_download_returns_the_original(clinic_on, owner, tmp_path, stub_decoder, route):
    stub_decoder()
    rec_id, src = _ds2_recording(owner, tmp_path)
    url = route.format(rid=rec_id)
    assert _client(owner).get(url).status_code == 403
    policy.register_grant_provider(lambda uid: [
        policy.Grant('own', frozenset({'audio_export'}))
    ] if uid == owner else [])
    response = _client(owner).get(url)
    assert response.status_code == 200
    assert response.get_data() == DS2
    partial = _client(owner).get(url, headers={'Range': 'bytes=0-9'})
    assert partial.status_code == 206
    assert partial.get_data() == DS2[:10]
    assert src.read_bytes() == DS2
    assert not (tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}').exists()


def test_existing_derivative_is_used_without_decoder(clinic_on, owner, tmp_path, monkeypatch):
    monkeypatch.delenv(decoder.DSS_CODEC_BIN_ENV, raising=False)
    rec_id, src = _ds2_recording(owner, tmp_path)
    derived = tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}'
    derived.write_bytes(b'ID3' + b'\x00' * 64)
    response = _client(owner).get(f'/audio/{rec_id}')
    assert response.status_code == 200
    assert response.mimetype == 'audio/mpeg'
    assert response.get_data() == derived.read_bytes()


def test_decoder_failure_falls_back_to_original(clinic_on, owner, tmp_path, monkeypatch):
    monkeypatch.delenv(decoder.DSS_CODEC_BIN_ENV, raising=False)
    rec_id, src = _ds2_recording(owner, tmp_path)
    response = _client(owner).get(f'/audio/{rec_id}')
    assert response.status_code == 200
    assert response.get_data() == DS2
    assert not (tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}').exists()


def test_clinic_mode_off_keeps_upstream_behaviour(clinic_off, owner, tmp_path, stub_decoder):
    stub_decoder()
    rec_id, src = _ds2_recording(owner, tmp_path)
    response = _client(owner).get(f'/audio/{rec_id}')
    assert response.get_data() == DS2
    assert not (tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}').exists()


def test_peaks_are_computed_from_the_derivative(clinic_on, owner, tmp_path, stub_decoder, monkeypatch):
    from src.services import waveform_peaks as peaks
    stub_decoder()
    rec_id, src = _ds2_recording(owner, tmp_path)
    seen = []

    def fake(path):
        seen.append(path)
        return peaks.encode_peaks(peaks.build_levels(*peaks.reduce_pcm([b'\x00\x10' * 800])), 0.1)

    monkeypatch.setattr(peaks, 'compute_peaks', fake)
    app.config['UPLOAD_FOLDER'], old = str(tmp_path / 'up'), app.config['UPLOAD_FOLDER']
    try:
        response = _client(owner).get(f'/audio/{rec_id}/peaks')
    finally:
        app.config['UPLOAD_FOLDER'] = old
    assert response.status_code == 200
    assert seen == [str(tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}')]
    assert src.read_bytes() == DS2


def test_peaks_clinic_off_read_the_original(clinic_off, owner, tmp_path, stub_decoder, monkeypatch):
    from src.services import waveform_peaks as peaks
    stub_decoder()
    rec_id, src = _ds2_recording(owner, tmp_path)
    seen = []
    monkeypatch.setattr(peaks, 'compute_peaks', lambda path: seen.append(path) or (_ for _ in ()).throw(
        peaks.PeaksError('x')))
    app.config['UPLOAD_FOLDER'], old = str(tmp_path / 'up'), app.config['UPLOAD_FOLDER']
    try:
        _client(owner).get(f'/audio/{rec_id}/peaks')
    finally:
        app.config['UPLOAD_FOLDER'] = old
    assert seen == [str(src)]
    assert not (tmp_path / f'{src.stem}{dictation_playback.PLAYBACK_SUFFIX}').exists()
