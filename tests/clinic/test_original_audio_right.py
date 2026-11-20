"""Synthetic route evidence for the explicit original-audio export right."""

from html.parser import HTMLParser

import pytest
from flask import render_template

from clinic_support import login
from src.app import app, db
from src.clinic import policy, pools
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.models import Recording

AUDIO = b'RIFF synthetic original audio bytes'


@pytest.fixture
def audio_case(clinic_on, world, tmp_path):
    path = tmp_path / 'original.wav'
    path.write_bytes(AUDIO)
    with app.app_context():
        recording = db.session.get(Recording, world['rec_a'])
        recording.audio_path = str(path)
        recording.mime_type = 'audio/wav'
        db.session.add(ClinicDictation(recording_id=recording.id, author_user_id=world['user_a']))
        db.session.add(ClinicAccountPolicy(user_id=world['user_admin'], status='active'))
        db.session.commit()
    return world, path


DOWNLOADS = (
    '/audio/{rid}?download=true',
    '/audio/{rid}?download=TRUE',
    '/api/v1/recordings/{rid}/audio',
    '/api/v1/recordings/{rid}/audio?download=false',
    '/api/v1/recordings/{rid}/audio?download=true',
)


@pytest.mark.parametrize('url', DOWNLOADS)
def test_original_requires_own_audio_export_and_live_revoke(audio_case, url):
    world, path = audio_case
    client = login(world['user_a'])
    url = url.format(rid=world['rec_a'])

    def check(allowed):
        response = client.get(url)
        assert response.status_code == (200 if allowed else 403)
        assert (response.data == AUDIO) is allowed
        partial = client.get(url, headers={'Range': 'bytes=0-9'})
        assert partial.status_code == (206 if allowed else 403)
        if allowed:
            assert partial.data == AUDIO[:10]
            assert partial.headers['Content-Range'] == f'bytes 0-9/{len(AUDIO)}'
        else:
            assert 'Content-Range' not in partial.headers
        detail = client.get(f'/api/recordings/{world["rec_a"]}')
        assert detail.status_code == 200
        assert detail.json['can_download_audio'] is allowed
        playback = client.get(f'/audio/{world["rec_a"]}', headers={'Range': 'bytes=0-9'})
        assert playback.status_code == 206
        assert playback.data == AUDIO[:10]
        assert path.read_bytes() == AUDIO

    check(False)
    with app.app_context():
        # Text export and the old transport action cannot substitute for audio_export.
        pools.allow(world['user_a'], 'own', actor_id=world['user_admin'], actions=['export'])
    policy.register_grant_provider(lambda uid: [policy.Grant('own', frozenset({'download'}))])
    check(False)
    with app.app_context():
        grant = pools.allow(world['user_a'], 'own', actor_id=world['user_admin'], actions=['audio_export'])
        grant_id = grant.id
    check(True)
    with app.app_context():
        denied = pools.deny(world['user_a'], 'own', actor_id=world['user_admin'], actions=['audio_export'])
        deny_id = denied.id
    check(False)
    with app.app_context():
        pools.revoke(deny_id, actor_id=world['user_admin'])
    check(True)
    with app.app_context():
        pools.revoke(grant_id, actor_id=world['user_admin'])
    check(False)


@pytest.mark.parametrize('actor', ['user_b', 'user_admin'])
@pytest.mark.parametrize('url', DOWNLOADS)
def test_foreign_or_admin_has_no_implicit_original_export(audio_case, actor, url):
    world, _ = audio_case
    response = login(world[actor]).get(url.format(rid=world['rec_a']))
    assert response.status_code == 404
    assert AUDIO not in response.data


@pytest.mark.parametrize('query', ['', '?download=false', '?download=1'])
def test_ordinary_playback_needs_no_audio_export(audio_case, query):
    world, _ = audio_case
    response = login(world['user_a']).get(f'/audio/{world["rec_a"]}{query}')
    assert response.status_code == 200
    assert response.data == AUDIO
    assert not response.headers.get('Content-Disposition', '').startswith('attachment')


class _DownloadLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'download=true' in attrs.get(':href', ''):
            self.links.append(attrs)


@pytest.mark.parametrize('clinic', [True, False])
@pytest.mark.parametrize('template', [
    'components/detail/audio-player.html',
    'components/detail/desktop-bottom-player.html',
    'components/phone/recording-sheets.html',
])
def test_download_ui_binds_server_policy_only_in_clinic_mode(template, clinic):
    parser = _DownloadLinks()
    with app.test_request_context('/'):
        parser.feed(render_template(template, clinic_mode=clinic))
    assert len(parser.links) == 1
    binding = parser.links[0].get('v-if', '')
    assert ('selectedRecording.can_download_audio === true' in binding) is clinic


@pytest.mark.parametrize('url', DOWNLOADS)
def test_enterprise_original_routes_keep_owner_access(clinic_off, world, tmp_path, url):
    path = tmp_path / 'original.wav'
    path.write_bytes(AUDIO)
    with app.app_context():
        recording = db.session.get(Recording, world['rec_a'])
        recording.audio_path = str(path)
        db.session.commit()
    client = login(world['user_a'])
    response = client.get(url.format(rid=world['rec_a']))
    assert response.status_code == 200
    assert response.data == AUDIO
    assert 'can_download_audio' not in client.get(f'/api/recordings/{world["rec_a"]}').json
