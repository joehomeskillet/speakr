"""Grants API (w084): admin-only changes, revision check, audit, live effect.

Synthetic users and dictations in the shared clinic test database.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app, db
from src.clinic import policy, pools, statistics_access
from src.clinic.identity import ClinicAccountPolicy, create_tables as create_identity_tables
from src.clinic.routes_grants import GrantsApi
from src.models import User

from test_pools import _dictation, _suffix, _user, pool_db as pool_db


@pytest.fixture
def grants_app(clinic_on):
    create_identity_tables()
    app = Flask('grants-api-test')
    app.config.update(TESTING=True, SECRET_KEY='synthetic-test-key',
                      SQLALCHEMY_DATABASE_URI=main_app.config['SQLALCHEMY_DATABASE_URI'])
    db.init_app(app)
    CSRFProtect(app)
    LoginManager(app).user_loader(lambda uid: db.session.get(User, int(uid)))

    @app.get('/test-session/<int:uid>')
    def browser_session(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    app.register_blueprint(GrantsApi().blueprint)
    yield app
    statistics_access.clear_grants()


def _account(user, *, status='active', identity_admin=False):
    db.session.add(ClinicAccountPolicy(user_id=user.id, status=status, is_identity_admin=identity_admin))
    db.session.commit()
    return user


def _login(app, user):
    client = app.test_client()
    return client, {'X-CSRF-Token': client.get(f'/test-session/{user.id}').json['csrf']}


def _url(user):
    return f'/api/clinic/v1/identity/users/{user.id}/grants'


def _change(effect='allow', scope='all', actions=('export',), rev=0, **extra):
    return {'expected_revision': rev, 'effect': effect, 'scope': scope, 'actions': list(actions), **extra}


def _writes():
    return pools.ClinicVisibilityGrant.query.count(), pools.ClinicPoolHistory.query.count()


def _allowed(user, action, target):
    subject = policy.build_subject(user.id)
    return policy.decide(subject, action, policy.build_facts(target.recording_id, target.author_user_id)).allowed


def test_admin_change_is_audited_and_live_on_next_request(grants_app):
    admin, typist, author = _account(_user(admin=True)), _user(), _user()
    pool = pools.create_author_pool(f'A-{_suffix()}', actor_id=admin.id)
    target = _dictation(author, pool.id)
    client, headers = _login(grants_app, admin)

    assert client.get(_url(typist)).json == {'user_id': typist.id, 'revision': 0, 'items': []}
    assert not _allowed(typist, 'export', target)
    added = client.post(_url(typist), headers=headers, json=_change(scope='pool', pool_id=pool.id))
    assert added.status_code == 200
    assert added.json['items'] == [{'effect': 'allow', 'scope': 'pool', 'pool_id': pool.id, 'actions': ['export']}]
    assert _allowed(typist, 'export', target) and not _allowed(typist, 'release', target)

    denied = client.post(_url(typist), headers=headers, json=_change('deny', rev=1))
    assert denied.json['revision'] == 2
    assert not _allowed(typist, 'export', target)
    audit = [row for row in pools.history() if row['entity'] == 'grant' and row['detail']['user_id'] == typist.id]
    assert [(row['actor_user_id'], row['detail']['effect']) for row in audit] == [(admin.id, 'allow'), (admin.id, 'deny')]


def test_stale_revision_and_replay_write_nothing(grants_app):
    admin, typist = _account(_user(admin=True)), _user()
    client, headers = _login(grants_app, admin)
    assert client.post(_url(typist), headers=headers, json=_change()).status_code == 200
    before = _writes()
    for _ in range(2):  # stale version, then the identical retry
        resp = client.post(_url(typist), headers=headers, json=_change())
        assert (resp.status_code, resp.json['code']) == (409, 'REVISION_CONFLICT')
    assert _writes() == before


@pytest.mark.parametrize('kind', ['typist', 'identity_admin', 'statistics', 'revoked_admin', 'admin_without_account'])
def test_non_admin_cannot_change_grants_or_gain_clinical_access(grants_app, kind):
    caller, author = _user(admin=kind in ('revoked_admin', 'admin_without_account')), _user()
    if kind != 'admin_without_account':
        _account(caller, status='disabled' if kind == 'revoked_admin' else 'active',
                 identity_admin=kind == 'identity_admin')
    if kind == 'statistics':
        statistics_access.grant_aggregate(caller.id, 'author', author_ids=[author.id])
    target = _dictation(author, None)
    client, headers = _login(grants_app, caller)
    before = _writes()
    for body in (_change(), _change(actor_id=caller.id), _change(role='admin')):
        resp = client.post(_url(caller), headers=headers, json=body)
        assert resp.status_code == 403
    assert _writes() == before
    assert not any(_allowed(caller, action, target) for action in policy.ACTIONS)


def test_read_scope_and_unknown_user(grants_app):
    admin, ident, typist, other = _account(_user(admin=True)), _account(_user(), identity_admin=True), _user(), _user()
    pools.allow(typist.id, 'own', actor_id=admin.id, actions=['export'])
    own, _ = _login(grants_app, typist)
    assert own.get(_url(typist)).json['items'] == [{'effect': 'allow', 'scope': 'own', 'actions': ['export']}]
    assert own.get(_url(other)).status_code == 403
    assert _login(grants_app, ident)[0].get(_url(typist)).json['revision'] == 1
    admin_client, headers = _login(grants_app, admin)
    missing = '/api/clinic/v1/identity/users/999999999/grants'
    assert admin_client.get(missing).status_code == 404
    assert admin_client.post(missing, headers=headers, json=_change()).status_code == 404


def test_invalid_csrf_anonymous_and_mode_off(grants_app, monkeypatch):
    admin, typist = _account(_user(admin=True)), _user()
    pool = pools.create_author_pool(f'A-{_suffix()}', actor_id=admin.id)
    client, headers = _login(grants_app, admin)
    before = _writes()
    for body in (_change(actions=()), _change(actions=('delete_all',)), _change(scope='pool'),
                 _change(scope='all', pool_id=pool.id), _change(effect='grant'), _change(rev=-1),
                 _change(actions=[{'x': 1}]), {'effect': 'allow'}):
        resp = client.post(_url(typist), headers=headers, json=body)
        assert (resp.status_code, resp.json['code']) == (400, 'INVALID_PAYLOAD'), body
    assert client.post(_url(typist), json=_change()).status_code == 403
    grants_app.config['WTF_CSRF_CHECK_DEFAULT'] = False
    anonymous = grants_app.test_client().post(_url(typist), json=_change())
    assert (anonymous.status_code, anonymous.json['code']) == (401, 'UNAUTHORIZED')
    assert _writes() == before
    monkeypatch.setenv('CLINIC_MODE', '0')
    assert client.get(_url(typist)).status_code == 404


def test_store_failure_then_retry(grants_app, monkeypatch):
    admin, typist = _account(_user(admin=True)), _user()
    client, headers = _login(grants_app, admin)
    before = _writes()
    with monkeypatch.context() as patch:
        patch.setattr(pools, 'allow', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('store down')))
        failed = client.post(_url(typist), headers=headers, json=_change())
    assert (failed.status_code, failed.json['code']) == (503, 'DEPENDENCY_UNAVAILABLE')
    assert _writes() == before
    assert client.post(_url(typist), headers=headers, json=_change()).json['revision'] == 1
