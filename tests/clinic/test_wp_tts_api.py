import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import local_tts
from src.clinic.policy import Deny, RecordingFacts, Subject
from src.clinic.routes_tts import Loaded, TtsApi

SPEAK = '/api/clinic/v1/tts/speak'
STATUS = '/api/clinic/v1/tts/capability'
TEXT = 'Der Patient klagt über Husten.'


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.script = []

    def synthesize(self, text, *, voice_id, model_id, timeout_s):
        self.calls.append((text, voice_id))
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
        return b'RIFFsynthetic'


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-test-key')
    login = LoginManager(app)
    CSRFProtect(app)
    engine = FakeEngine()
    state = {'cap': local_tts.declare_capability(), 'denied': False, 'version': 3, 'loads': []}

    class Actor(UserMixin):
        id = 7

    login.user_loader(lambda uid: Actor() if uid == '7' else None)

    @app.get('/test-session')
    def browser_session():
        login_user(Actor())
        return jsonify(csrf=generate_csrf())

    def load(actor, kind, object_id):
        state['loads'].append((actor, kind, object_id))
        if object_id not in ('d1', 'p1'):
            return None
        text = 'Pneumonie' if kind == 'pronunciation' else TEXT
        denies = (Deny(recording_ids=frozenset({1})),) if state['denied'] else ()
        return Loaded(local_tts.CanonicalText(text, state['version']),
                      Subject(actor, denies=denies), RecordingFacts(1, actor))

    app.register_blueprint(TtsApi(load, lambda: state['cap'], engine).blueprint)
    client = app.test_client()
    headers = {'X-CSRF-Token': client.get('/test-session').json['csrf']}
    return client, headers, state, engine


def code(resp):
    return resp.status_code, resp.json['code']


def test_selection_and_pronunciation_return_audio(api):
    client, headers, _, engine = api
    sel = client.post(SPEAK, headers=headers, json={'dictation_id': 'd1', 'version': 3, 'start': 4, 'end': 11})
    assert sel.status_code == 200 and sel.data == b'RIFFsynthetic'
    assert engine.calls[0] == (TEXT[4:11], 'de_DE')
    pron = client.post(SPEAK, headers=headers, json={'pronunciation_id': 'p1', 'version': 3})
    assert pron.status_code == 200 and engine.calls[1][0] == 'Pneumonie'


def test_capability_status(api):
    client, _, state, _ = api
    assert client.application.test_client().get(STATUS).status_code == 401
    assert client.get(STATUS).json == {'available': True}
    state['cap'] = None
    assert client.get(STATUS).json == {'available': False}


@pytest.mark.parametrize('mutate', [
    lambda s: s.update(cap=None),
    lambda s: s.update(cap=local_tts.declare_capability(verified=False)),
    lambda s: s.update(cap=local_tts.declare_capability(endpoint='https://tts.example.com/v1')),
])
def test_unavailable_capability_never_reaches_engine(api, mutate):
    client, headers, state, engine = api
    mutate(state)
    resp = client.post(SPEAK, headers=headers, json={'dictation_id': 'd1', 'version': 3})
    assert code(resp) == (422, 'CAPABILITY_UNAVAILABLE')
    assert engine.calls == [] and state['loads'] == []


def test_unauthorized_unknown_and_actor_not_trusted(api):
    client, headers, state, engine = api
    state['denied'] = True
    denied = client.post(SPEAK, headers=headers, json={'dictation_id': 'd1', 'version': 3})
    unknown = client.post(SPEAK, headers=headers, json={'dictation_id': 'zz', 'version': 3})
    role = client.post(SPEAK, headers=headers, json={'dictation_id': 'd1', 'version': 3, 'role': 'admin'})
    assert code(denied) == (403, 'POLICY_DENIED') and code(unknown) == (403, 'POLICY_DENIED')
    assert code(role) == (400, 'INVALID_PAYLOAD')
    assert engine.calls == []


def test_stale_version(api):
    client, headers, state, engine = api
    state['version'] = 4
    resp = client.post(SPEAK, headers=headers, json={'dictation_id': 'd1', 'version': 3})
    assert code(resp) == (409, 'REVISION_CONFLICT') and engine.calls == []


@pytest.mark.parametrize('payload', [
    {'version': 3},
    {'dictation_id': 'd1', 'pronunciation_id': 'p1', 'version': 3},
    {'dictation_id': 'd1'},
    {'dictation_id': 'd1', 'version': True},
    {'dictation_id': 'd1', 'version': 3, 'start': 5},
    {'dictation_id': 'd1', 'version': 3, 'start': 9, 'end': 2},
    {'dictation_id': 'd1', 'version': 3, 'start': 0, 'end': 9999},
    {'pronunciation_id': 'p1', 'version': 3, 'start': 0, 'end': 1},
])
def test_invalid_payloads(api, payload):
    client, headers, _, engine = api
    resp = client.post(SPEAK, headers=headers, json=payload)
    assert code(resp) == (400, 'INVALID_PAYLOAD') and engine.calls == []


def test_auth_csrf_and_mode(api, monkeypatch):
    client, headers, _, _ = api
    body = {'dictation_id': 'd1', 'version': 3}
    assert client.post(SPEAK, json=body).status_code == 403
    anonymous = client.application.test_client()
    client.application.config['WTF_CSRF_CHECK_DEFAULT'] = False
    assert code(anonymous.post(SPEAK, json=body)) == (401, 'UNAUTHORIZED')
    monkeypatch.setenv('CLINIC_MODE', '0')
    assert client.post(SPEAK, headers=headers, json=body).status_code == 404


def test_transient_retry_and_failure(api):
    client, headers, _, engine = api
    body = {'dictation_id': 'd1', 'version': 3}
    engine.script = [local_tts.TtsTransient()]
    assert client.post(SPEAK, headers=headers, json=body).status_code == 200
    assert len(engine.calls) == 2
    engine.script = [local_tts.TtsEngineFailed()]
    assert code(client.post(SPEAK, headers=headers, json=body)) == (503, 'DEPENDENCY_UNAVAILABLE')
    engine.script = [TimeoutError()]
    assert code(client.post(SPEAK, headers=headers, json=body)) == (503, 'DEPENDENCY_UNAVAILABLE')
    assert client.post(SPEAK, headers=headers, json=body).status_code == 200
