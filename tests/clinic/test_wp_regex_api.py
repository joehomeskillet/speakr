from datetime import date, datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import register_clinic_resources, register_clinic_worklist
from src.clinic import lease, pools, presets, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.routes_regex import RegexApi
from src.database import db
from src.models import Recording, User

PREVIEW = '/api/clinic/v1/regex/preview'
APPLY = '/api/clinic/v1/regex/apply'
SAVED_RULES = '/api/clinic/v1/regex/rules'
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


@pytest.fixture
def mounted_api(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-regex-store', SQLALCHEMY_DATABASE_URI='sqlite://')
    db.init_app(app)
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    CSRFProtect(app)
    register_clinic_resources(app)
    register_clinic_worklist(app)

    @app.get('/test-session/<int:uid>')
    def browser_session(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    with app.app_context():
        db.create_all()
        admin = User(username='regex-admin', email='admin@example.invalid', password='synthetic', is_admin=True)
        editor = User(username='regex-editor', email='editor@example.invalid', password='synthetic')
        db.session.add_all([admin, editor])
        db.session.flush()
        admin_id, editor_id = admin.id, editor.id
        for uid in (admin_id, editor_id):
            db.session.add(ClinicAccountPolicy(user_id=uid, status='active', login_policy='local_only'))
        recording = Recording(user_id=editor_id, title='Original')
        db.session.add(recording)
        db.session.flush()
        pool = pools.create_author_pool('Regex Authors', actor_id=admin_id)
        pools.add_author_member(pool.id, editor_id, actor_id=admin_id)
        item = workflow.start(db.session, 'create', author_user_id=editor_id, pool_id=pool.id)
        dictation_id = item.id
        db.session.add(ClinicDictation(id=dictation_id, recording_id=recording.id,
                                       author_user_id=editor_id, pool_id=pool.id))
        pools.allow(editor_id, 'own', actor_id=admin_id, actions=('rename', 'priority'))
        db.session.commit()

    def browser(uid):
        client = app.test_client()
        return client, {'X-CSRFToken': client.get(f'/test-session/{uid}').json['csrf']}

    return app, browser(admin_id), browser(editor_id), editor_id, dictation_id


def test_saved_rule_crud_and_policy(mounted_api):
    app, (admin, csrf), (editor, editor_csrf), editor_id, _ = mounted_api
    rule = {**RULES[0], 'max_input_length': 20}
    with app.app_context():
        private = presets.save_regex_rule('Private', 'x', 'notes', scope='user', scope_id=editor_id)
        private_id = private.id
        db.session.commit()

    assert app.test_client().get(SAVED_RULES).status_code == 401
    assert editor.get(SAVED_RULES).json == {'rules': []}
    assert editor.post(SAVED_RULES, headers=editor_csrf, json=rule).status_code == 403
    assert admin.post(SAVED_RULES, json=rule).status_code == 403
    assert admin.post(SAVED_RULES, headers=csrf, json={**rule, 'id': private_id}).status_code == 404
    for target in presets.FORBIDDEN_REGEX_TARGETS:
        refused = admin.post(SAVED_RULES, headers=csrf, json={**rule, 'target_field': target})
        assert (refused.status_code, refused.json['code']) == (403, 'POLICY_DENIED')
    for invalid in ({'max_input_length': True}, {'max_input_length': 0}, {'max_input_length': 10001},
                    {'pattern': '('}, {'replacement': r'\99'}):
        refused = admin.post(SAVED_RULES, headers=csrf, json={**rule, **invalid})
        assert (refused.status_code, refused.json['code']) == (400, 'INVALID_PAYLOAD')

    saved = admin.post(SAVED_RULES, headers=csrf, json=rule)
    assert saved.status_code == 201
    assert saved.json['max_input_length'] == 20
    assert editor.get(SAVED_RULES).json == {'rules': [saved.json]}
    rule_id = saved.json['id']
    updated = admin.post(SAVED_RULES, headers=csrf, json={'id': rule_id, 'max_input_length': 8})
    assert updated.status_code == 200
    assert editor.get(SAVED_RULES).json == {'rules': [{**saved.json, 'max_input_length': 8}]}
    assert editor.delete(SAVED_RULES, headers=editor_csrf, json={'id': rule_id}).status_code == 403
    assert admin.delete(SAVED_RULES, headers=csrf, json={'id': rule_id}).status_code == 204
    assert editor.get(SAVED_RULES).json == {'rules': []}
    with app.app_context():
        assert not db.session.get(presets.ClinicRegexRule, rule_id).is_active


@pytest.mark.parametrize('targets', [
    ('title', 'notes', 'priority', 'deadline'),
    ('dictation_name', 'remarks', 'urgency', 'due_date'),
])
def test_apply_saved_rules_to_leased_dictation(mounted_api, targets):
    app, (admin, csrf), (editor, editor_csrf), editor_id, dictation_id = mounted_api
    values = ('4711', 'Projekt-ID', 128, '2030-01-02')
    rule_ids = []
    for target, pattern in zip(targets, (r'Projekt-ID (\d+)', 'Projekt-ID', 'dringend', r'Termin (\S+)')):
        saved = admin.post(SAVED_RULES, headers=csrf,
                           json={'target_field': target, 'pattern': pattern, 'max_input_length': 5})
        assert saved.status_code == 201
        rule_ids.append(saved.json['id'])
    payload = {'dictation_id': dictation_id, 'rule_ids': rule_ids, 'text': TEXT}
    short = editor.post(PREVIEW, headers=editor_csrf, json=payload)
    assert short.status_code == 200
    assert short.json['truncated'] and short.json['resulting_fields'] == {}
    confirm = {key: short.json[key] for key in ('rule_version', 'input_digest')}

    for rule_id in rule_ids[1:]:
        assert admin.post(SAVED_RULES, headers=csrf, json={'id': rule_id, 'max_input_length': 100}).status_code == 200
    partial = editor.post(PREVIEW, headers=editor_csrf, json=payload)
    assert partial.status_code == 200
    assert partial.json['truncated'] and partial.json['resulting_fields'] == dict(zip(targets[1:], values[1:]))
    assert admin.post(SAVED_RULES, headers=csrf, json={'id': rule_ids[0], 'max_input_length': 100}).status_code == 200
    stale = editor.post(APPLY, headers=editor_csrf, json={**payload, **confirm})
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    capped = editor.post(PREVIEW, headers=editor_csrf, json={**payload, 'max_input_length': 5})
    assert capped.status_code == 200
    assert capped.json['truncated'] and capped.json['resulting_fields'] == {}

    preview = editor.post(PREVIEW, headers=editor_csrf, json=payload)
    assert preview.status_code == 200
    fields = dict(zip(targets, values))
    assert preview.json['resulting_fields'] == fields
    confirm = {key: preview.json[key] for key in ('rule_version', 'input_digest')}
    shorter = editor.post(APPLY, headers=editor_csrf, json={**payload, **confirm, 'max_input_length': 5})
    assert (shorter.status_code, shorter.json['code']) == (409, 'REVISION_CONFLICT')
    assert editor.post(APPLY, headers=editor_csrf, json={**payload, **confirm}).status_code == 409
    with app.app_context():
        lease.acquire(dictation_id, editor_id, datetime.now(timezone.utc))
        db.session.commit()
    applied = editor.post(APPLY, headers=editor_csrf, json={**payload, **confirm})
    assert applied.status_code == 200, applied.json
    assert applied.json['applied_fields'] == fields
    with app.app_context():
        item = db.session.get(workflow.ClinicWorkflowDictation, dictation_id)
        dictation = db.session.get(ClinicDictation, dictation_id)
        recording = db.session.get(Recording, dictation.recording_id)
        assert (recording.title, recording.notes) == values[:2]
        assert (item.priority, item.deadline_date) == (128, date(2030, 1, 2))
    assert admin.delete(f'{SAVED_RULES}/{rule_ids[0]}', headers=csrf).status_code == 204
    deleted = editor.post(APPLY, headers=editor_csrf, json={**payload, **confirm})
    assert (deleted.status_code, deleted.json['code']) == (409, 'REVISION_CONFLICT')
