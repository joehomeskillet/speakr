import hashlib
import io
import zipfile
from contextlib import contextmanager
from uuid import UUID

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_wtf.csrf import generate_csrf

from src.clinic import richtext_formats as formats
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.richtext import CanonicalDocument, Node, Segment
from src.clinic.routes_bundle import PREFIX, BundleSession, StaleVersion, create_blueprint, text_to_document
from src.clinic.sdict_bundle import read_bundle

DID = str(UUID(int=5))
WAV = b'RIFF\x24\x00\x00\x00WAVEfmt ' + b'\x00' * 24
KEY = 'idem-key-0001'


def doc(text='Befund unauffällig'):
    return CanonicalDocument((Node(id='n1', kind='paragraph', runs=(Segment(id='s1', text=text, bold=True),)),))


class User(UserMixin):
    def __init__(self, uid):
        self.id = uid


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-bundle-test')
    LoginManager(app).user_loader(lambda uid: User(int(uid)))
    state = {'session': BundleSession(doc(), WAV, 3), 'managed': True, 'allow': True,
             'fail': None, 'saved': [], 'receipts': {}}

    def authorize(actor, dictation_id, action):
        state['asked'] = (actor, dictation_id, action)
        return state['allow'] and actor == 7

    @contextmanager
    def persist(actor, dictation_id, new_doc, audio, expected):
        if state['fail']:
            raise state['fail']
        if expected != state['session'].version:
            raise StaleVersion()
        state['saved'].append((actor, new_doc, audio, expected))
        yield expected + 1

    def once(scope, digest, operation):
        old = state['receipts'].get(scope)
        if old:
            if old[0] != digest:
                raise CommandProblem('IDEMPOTENCY_CONFLICT')
            return old[1]
        result = operation()
        state['receipts'][scope] = (digest, result)
        return result

    app.register_blueprint(create_blueprint(
        lambda actor, did: state['session'] if did == DID else None, authorize, persist, once,
        lambda: state['managed']))
    client = app.test_client()

    return app, client, state


@pytest.fixture
def csrf(ctx):
    app, client, state = ctx
    with client.session_transaction() as sess:
        sess['_user_id'] = '7'
    with app.test_request_context():
        from flask import session as flask_session
        token = generate_csrf()
        secret = flask_session['csrf_token']
    with client.session_transaction() as sess:
        sess['csrf_token'] = secret
    return client, state, token


def headers(token, version=3, key=KEY, **extra):
    return {'X-CSRFToken': token, 'If-Match': f'"{version}"', 'Idempotency-Key': key, **extra}


def sdict_bytes(text='Neuer Text'):
    return formats.export_document(doc(text), expected_version=1, allowed=True, audio=WAV).bundle


def test_sdict_export_round_trips_text_and_audio(csrf):
    client, state, _token = csrf
    response = client.get(f'{PREFIX}/{DID}/export?version=3')
    assert response.status_code == 200
    assert 'attachment' in response.headers['Content-Disposition']
    assert response.headers['ETag'] == '"3"'
    assert response.headers['X-Content-SHA256'] == hashlib.sha256(response.data).hexdigest()
    bundle = read_bundle(response.data)
    assert bundle.files['audio/a.wav'] == WAV
    assert formats.reload_bundle(response.data, expected_version=1, allowed=True) == doc()
    assert state['asked'] == (7, DID, 'export')


def test_txt_and_rtf_export(csrf):
    client, _state, _token = csrf
    txt = client.get(f'{PREFIX}/{DID}/export?format=txt')
    assert txt.data.decode('utf-8') == 'Befund unauffällig'
    assert txt.headers['Content-Type'].startswith('text/plain')
    rtf = client.get(f'{PREFIX}/{DID}/export?format=rtf')
    assert rtf.status_code == 200 and rtf.data.startswith(b'{\\rtf')
    assert client.get(f'{PREFIX}/{DID}/export?format=exe').status_code == 400


def test_export_denials_and_stale(csrf):
    client, state, _token = csrf
    assert client.get(f'{PREFIX}/{DID}/export?version=2').status_code == 409
    assert client.get(f'{PREFIX}/{UUID(int=9)}/export').status_code == 404
    assert client.get(f'{PREFIX}/not-a-uuid/export').status_code == 404
    state['allow'] = False
    assert client.get(f'{PREFIX}/{DID}/export').status_code == 404
    state['allow'] = True
    state['managed'] = False  # BYOD
    assert client.get(f'{PREFIX}/{DID}/export').status_code == 403


def test_unauthenticated_and_mode_off(ctx, monkeypatch):
    _app, client, _state = ctx
    assert client.get(f'{PREFIX}/{DID}/export').status_code == 401
    monkeypatch.setenv('CLINIC_MODE', '0')
    assert client.get(f'{PREFIX}/{DID}/export').status_code == 404


def test_sdict_import_persists_once_and_replays(csrf):
    client, state, token = csrf
    data = sdict_bytes()
    url = f'{PREFIX}/{DID}/import'
    first = client.post(url, data=data, headers=headers(token) | {
        'X-Content-SHA256': hashlib.sha256(data).hexdigest()})
    assert first.status_code == 200 and first.json['revision'] == 4
    assert first.json['audio_synced'] is True
    again = client.post(url, data=data, headers=headers(token) | {
        'X-Content-SHA256': hashlib.sha256(data).hexdigest()})
    assert again.get_json() == first.get_json()
    assert len(state['saved']) == 1
    actor, new_doc, audio, expected = state['saved'][0]
    assert (actor, audio, expected) == (7, WAV, 3) and new_doc == doc('Neuer Text')
    other = client.post(url, data=sdict_bytes('Anderer'), headers=headers(token))
    assert other.status_code == 409 and other.json['code'] == 'IDEMPOTENCY_CONFLICT'


def test_txt_import(csrf):
    client, state, token = csrf
    response = client.post(f'{PREFIX}/{DID}/import?format=txt', data='Zeile eins\r\nZeile zwei'.encode(),
                           headers=headers(token))
    assert response.status_code == 200
    assert response.json['audio_synced'] is False
    assert state['saved'][0][1] == text_to_document(b'Zeile eins\nZeile zwei')
    assert state['saved'][0][2] is None
    assert client.post(f'{PREFIX}/{DID}/import?format=txt', data=b'\xff\xfe', headers=headers(token, key='idem-key-0002')
                       ).status_code == 400


def test_import_rejects_bad_requests(csrf):
    client, state, token = csrf
    url = f'{PREFIX}/{DID}/import'
    data = sdict_bytes()
    assert client.post(url, data=data, headers={'If-Match': '"3"', 'Idempotency-Key': KEY}).status_code == 403
    assert client.post(url, data=data, headers={'X-CSRFToken': token, 'Idempotency-Key': KEY}).status_code == 428
    assert client.post(url, data=data, headers=headers(token, version=2)).status_code == 409
    assert client.post(url, data=data, headers=headers(token) | {'X-Content-SHA256': '0' * 64}).status_code == 400
    assert client.post(url, data=b'not a zip', headers=headers(token)).status_code == 400
    assert client.post(url, data=b'', headers=headers(token)).status_code == 400
    assert client.post(url, data=data, headers=headers(token, key='short')).status_code == 400
    state['allow'] = False
    assert client.post(url, data=data, headers=headers(token)).status_code == 404
    state['allow'] = True
    state['managed'] = False
    assert client.post(url, data=data, headers=headers(token)).status_code == 403
    assert state['saved'] == []


def test_tampered_bundle_refused(csrf):
    client, state, token = csrf
    src = zipfile.ZipFile(io.BytesIO(sdict_bytes()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zf:
        for name in src.namelist():
            zf.writestr(name, b'RIFF' + b'x' * 40 if name == 'audio/a.wav' else src.read(name))
    response = client.post(f'{PREFIX}/{DID}/import', data=out.getvalue(), headers=headers(token))
    assert response.status_code == 400 and state['saved'] == []


def test_persist_failures(csrf):
    client, state, token = csrf
    url = f'{PREFIX}/{DID}/import'
    state['fail'] = StaleVersion()
    assert client.post(url, data=sdict_bytes(), headers=headers(token)).status_code == 409
    state['fail'] = OSError('synthetic')
    assert client.post(url, data=sdict_bytes(), headers=headers(token, key='idem-key-0003')).status_code == 503
    state['fail'] = None
    assert client.post(url, data=sdict_bytes(), headers=headers(token, key='idem-key-0003')).status_code == 200


def test_actor_comes_from_session_not_request(csrf):
    client, state, token = csrf
    client.post(f'{PREFIX}/{DID}/import?actor=99&user_id=99', data=sdict_bytes(),
                headers=headers(token, **{'X-User-Id': '99'}))
    assert state['saved'][0][0] == 7
