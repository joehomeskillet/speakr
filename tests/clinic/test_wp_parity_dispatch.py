from uuid import uuid4

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import policy
from src.clinic.parity_dispatch import MAX_BODY_BYTES, CommandProblem, ParityDispatch

PATH = '/api/clinic/v1/parity/commands'
TARGET = str(uuid4())


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-test-key')
    login = LoginManager(app)
    CSRFProtect(app)
    state = {'revision': 1, 'allowed': True, 'calls': 0, 'unavailable': False}
    receipts = {}

    class Actor(UserMixin):
        id = 7

    login.user_loader(lambda uid: Actor() if uid == '7' else None)
    login.request_loader(lambda req: Actor() if req.headers.get('Authorization') else None)

    @app.get('/test-session')
    def browser_session():
        login_user(Actor())
        return jsonify(csrf=generate_csrf())

    def once(scope, digest, operation):
        if scope in receipts:
            saved_digest, result = receipts[scope]
            if saved_digest != digest:
                raise CommandProblem('IDEMPOTENCY_CONFLICT')
            return result
        result = operation()
        receipts[scope] = digest, result
        return result

    def authorize(actor, permission, payload):
        assert permission == 'clinic.export'
        if payload['dictation_id'] != TARGET:
            return None
        grants = (policy.Grant('own', frozenset({'export'})),) if state['allowed'] else ()
        decision = policy.decide(policy.Subject(actor, grants=grants), 'export', policy.RecordingFacts(1, 7))
        return TARGET if decision.allowed else None

    def revision(target):
        assert target == TARGET
        return state['revision']

    def execute(actor, target, envelope):
        assert actor == 7 and target == TARGET
        if state['unavailable']:
            raise CommandProblem('DEPENDENCY_UNAVAILABLE')
        state['calls'] += 1
        state['revision'] += 1
        return {'request_id': envelope['request_id'], 'command_id': str(uuid4()),
                'status': 'queued', 'revision': state['revision']}

    dispatch = ParityDispatch(once)
    adapter = dict(authorize=authorize, revision=revision, execute=execute)
    dispatch.register('render_output', **adapter)
    for command in ('render_output', 'not_in_catalog'):
        with pytest.raises(ValueError, match='Unknown or duplicate'):
            dispatch.register(command, **adapter)
    app.register_blueprint(dispatch.blueprint)
    client = app.test_client()
    token = client.get('/test-session').json['csrf']
    headers = {'X-CSRF-Token': token, 'Idempotency-Key': 'request-123'}
    body = {'command': 'render_output', 'request_id': str(uuid4()), 'expected_revision': 1,
            'payload': {'dictation_id': TARGET, 'text_revision_id': str(uuid4()),
                        'audio_revision_id': str(uuid4()), 'format': 'rtf', 'mode': 'corrected'}}
    return app, client, headers, body, state


def test_dispatch_and_retry(endpoint):
    app, client, headers, body, state = endpoint
    assert [(rule.rule, rule.methods - {'OPTIONS'}) for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith('clinic_parity.')] == [(PATH, {'POST'})]
    first = client.post(PATH, headers=headers, json=body)
    assert first.status_code == 200
    assert first.json['status'] == 'queued' and first.json['revision'] == 2
    assert client.post(PATH, headers=headers, json=body).json == first.json
    conflict = client.post(PATH, headers=headers, json=dict(body, request_id=str(uuid4())))
    assert (conflict.status_code, conflict.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    state['allowed'] = False
    denied = client.post(PATH, headers=headers, json=body)
    assert (denied.status_code, denied.json['code']) == (403, 'POLICY_DENIED')
    assert state['calls'] == 1


def test_session_csrf_and_object_scope(endpoint, monkeypatch):
    app, client, headers, body, state = endpoint
    assert client.post(PATH, json=body).status_code == 403
    # Exercise the standalone endpoint independently of the app's CSRF hook.
    app.config['WTF_CSRF_CHECK_DEFAULT'] = False
    token_only = app.test_client().post(PATH, headers={'Authorization': 'synthetic'}, json=body)
    assert (token_only.status_code, token_only.json['code']) == (401, 'UNAUTHORIZED')
    denied = client.post(PATH, headers={'Idempotency-Key': 'request-123'}, json=body)
    assert (denied.status_code, denied.mimetype) == (403, 'application/problem+json')
    body['payload']['dictation_id'] = str(uuid4())
    denied = client.post(PATH, headers=headers, json=body)
    assert (denied.status_code, denied.json['code']) == (403, 'POLICY_DENIED')
    monkeypatch.delenv('CLINIC_MODE')
    assert client.post(PATH, headers=headers, json=body).status_code == 404
    assert state['calls'] == 0


def test_bad_input_stale_revision_and_dependency_retry(endpoint):
    _, client, headers, body, state = endpoint
    for invalid in (dict(body, command='unknown'), dict(body, expected_revision=True),
                    dict(body, payload=dict(body['payload'], dictation_id='bad-id')),
                    dict(body, payload=dict(body['payload'], target_alias='x' * 129))):
        response = client.post(PATH, headers=headers, json=invalid)
        assert (response.status_code, response.json['code']) == (400, 'INVALID_PAYLOAD')
    oversized = client.post(PATH, headers=headers, content_type='application/json', data=' ' * (MAX_BODY_BYTES + 1))
    assert oversized.status_code == 400
    pool = dict(body, command='save_author_pool', expected_revision=0,
                payload={'name': 'Pool', 'member_user_ids': [1, 1]})
    assert client.post(PATH, headers=headers, json=pool).status_code == 400
    pool['payload']['member_user_ids'] = [1]
    unsupported = client.post(PATH, headers=headers, json=pool)
    assert (unsupported.status_code, unsupported.json['code']) == (422, 'CAPABILITY_UNAVAILABLE')
    stale = client.post(PATH, headers=headers, json=dict(body, expected_revision=0))
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    state['unavailable'] = True
    failed = client.post(PATH, headers=headers, json=body)
    assert (failed.status_code, failed.json['code']) == (503, 'DEPENDENCY_UNAVAILABLE')
    assert state['calls'] == 0
    state['unavailable'] = False
    assert client.post(PATH, headers=headers, json=body).status_code == 200
    assert state['calls'] == 1
