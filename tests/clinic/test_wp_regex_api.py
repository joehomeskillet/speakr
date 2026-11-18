import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import presets
from src.clinic.routes_regex import RegexApi

PREVIEW = '/api/clinic/v1/regex/preview'
APPLY = '/api/clinic/v1/regex/apply'
OBJ = 'dictation-1'
TEXT = 'Projekt-ID 4711, dringend, Termin 2030-01-02'
RULES = [
    {'name': 'projekt', 'pattern': r'Projekt-ID (\d+)', 'target_field': 'title', 'replacement': r'P-\1'},
    {'name': 'prio', 'pattern': r'\b(dringend)\b', 'target_field': 'priority'},
]


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-test-key')
    login = LoginManager(app)
    CSRFProtect(app)
    state = {'allowed': True, 'assigned': [], 'fail': False, 'actors': []}

    class Actor(UserMixin):
        id = 7

    login.user_loader(lambda uid: Actor() if uid == '7' else None)

    @app.get('/test-session')
    def browser_session():
        login_user(Actor())
        return jsonify(csrf=generate_csrf())

    def authorize(actor, action, dictation_id):
        state['actors'].append((actor, action))
        return state['allowed'] and dictation_id == OBJ

    def assign(actor, dictation_id, fields):
        if state['fail']:
            raise RuntimeError('store down')
        state['assigned'].append((actor, dictation_id, fields))
        return {'saved': True}

    app.register_blueprint(RegexApi(authorize, assign).blueprint)
    client = app.test_client()
    headers = {'X-CSRF-Token': client.get('/test-session').json['csrf']}
    return client, headers, state


def body(**extra):
    return {'dictation_id': OBJ, 'rules': RULES, 'text': TEXT, **extra}


def test_preview_then_apply_same_engine(api):
    client, headers, state = api
    preview = client.post(PREVIEW, headers=headers, json=body())
    assert preview.status_code == 200
    expected = presets.preview_regex_rules(RULES, TEXT)
    assert preview.json['resulting_fields'] == expected['resulting_fields'] == {'title': 'P-4711', 'priority': 128}
    assert state['assigned'] == []
    confirm = body(rule_version=preview.json['rule_version'], input_digest=preview.json['input_digest'])
    applied = client.post(APPLY, headers=headers, json=confirm)
    assert applied.status_code == 200
    assert applied.json['applied_fields'] == preview.json['resulting_fields']
    assert state['assigned'] == [(7, OBJ, {'title': 'P-4711', 'priority': 128})]
    assert state['actors'] == [(7, 'regex_preview'), (7, 'regex_apply')]


def test_stale_version_and_changed_input(api):
    client, headers, state = api
    pv = client.post(PREVIEW, headers=headers, json=body()).json
    confirm = body(rule_version=pv['rule_version'], input_digest=pv['input_digest'])
    changed_text = client.post(APPLY, headers=headers, json=dict(confirm, text=TEXT + ' x'))
    changed_rules = client.post(APPLY, headers=headers, json=dict(confirm, rules=RULES[:1]))
    stale = client.post(APPLY, headers=headers, json=dict(confirm, rule_version='0' * 64))
    for resp in (changed_text, changed_rules, stale):
        assert (resp.status_code, resp.json['code']) == (409, 'REVISION_CONFLICT')
    assert state['assigned'] == []


@pytest.mark.parametrize('target', ['patient_id', 'author_id', 'user_id'])
def test_identity_targets_rejected(api, target):
    client, headers, state = api
    rules = [{'pattern': r'(\d+)', 'target_field': target}]
    for path, extra in ((PREVIEW, {}), (APPLY, {'rule_version': 'x', 'input_digest': 'y'})):
        resp = client.post(path, headers=headers, json=body(rules=rules, **extra))
        assert (resp.status_code, resp.json['code']) == (403, 'POLICY_DENIED')
    assert state['assigned'] == []


@pytest.mark.parametrize('pattern', ['(a+)+$', '(a*)*b', r'(x{1,})*', r'(a)\1', 'a' * 300, '(unclosed'])
def test_expensive_or_invalid_patterns_rejected(api, pattern):
    client, headers, _ = api
    rules = [{'pattern': pattern, 'target_field': 'notes'}]
    resp = client.post(PREVIEW, headers=headers, json=body(rules=rules))
    assert (resp.status_code, resp.json['code']) == (400, 'INVALID_PAYLOAD')


def test_unknown_target_oversize_and_extra_keys_rejected(api):
    client, headers, _ = api
    for payload in (body(rules=[{'pattern': 'a', 'target_field': 'bogus'}]),
                    body(text='x' * 10001), body(rules=[]), body(role='admin'),
                    {'rules': RULES, 'text': TEXT}):
        resp = client.post(PREVIEW, headers=headers, json=payload)
        assert (resp.status_code, resp.json['code']) == (400, 'INVALID_PAYLOAD')


def test_unauthenticated_csrf_and_object_policy(api, monkeypatch):
    client, headers, state = api
    assert client.post(PREVIEW, json=body()).status_code == 403
    anonymous = client.application.test_client()
    client.application.config['WTF_CSRF_CHECK_DEFAULT'] = False
    resp = anonymous.post(PREVIEW, json=body())
    assert (resp.status_code, resp.json['code']) == (401, 'UNAUTHORIZED')
    foreign = client.post(PREVIEW, headers=headers, json=dict(body(), dictation_id='other'))
    assert (foreign.status_code, foreign.json['code']) == (403, 'POLICY_DENIED')
    state['allowed'] = False
    denied = client.post(APPLY, headers=headers, json=body(rule_version='x', input_digest='y'))
    assert (denied.status_code, denied.json['code']) == (403, 'POLICY_DENIED')
    assert state['assigned'] == []
    monkeypatch.setenv('CLINIC_MODE', '0')
    assert client.post(PREVIEW, headers=headers, json=body()).status_code == 404


def test_apply_failure_then_retry(api):
    client, headers, state = api
    pv = client.post(PREVIEW, headers=headers, json=body()).json
    confirm = body(rule_version=pv['rule_version'], input_digest=pv['input_digest'])
    state['fail'] = True
    failed = client.post(APPLY, headers=headers, json=confirm)
    assert (failed.status_code, failed.json['code']) == (503, 'DEPENDENCY_UNAVAILABLE')
    state['fail'] = False
    retry = client.post(APPLY, headers=headers, json=confirm)
    assert retry.status_code == 200 and len(state['assigned']) == 1
