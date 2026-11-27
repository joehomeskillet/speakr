"""Acceptance policy can publish an author pool and a deny exception.

Synthetic users only. Alpha publishes. Beta may decide through the pool.
Gamma is in that pool and is also named as an approver; the deny still wins.
An older body that lists only approver users stays valid and does not clear scopes.
"""

from datetime import datetime, timezone

import pytest
from flask import Flask, g
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.acceptance_rules import AcceptanceError
from src.clinic.models import ClinicDictation
from src.clinic.pool_policy import author_pool_ids
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.clinic.routes_acceptance_policy import create_blueprint
from src.database import db
from src.models import Recording, User


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-scopes', SQLALCHEMY_DATABASE_URI='sqlite://')
    db.init_app(app)
    with app.app_context():
        db.create_all()
        users = []
        for name in ('alpha', 'beta', 'gamma', 'outsider'):
            row = User(username=name, email=f'{name}@example.invalid', password='synthetic')
            db.session.add(row)
            db.session.flush()
            users.append(row)
        alpha, beta, gamma, outsider = users
        pool = ClinicAuthorPool(name='Autoren', created_by=alpha.id)
        dead = ClinicAuthorPool(name='Archiv', created_by=alpha.id, active=False)
        db.session.add_all((pool, dead))
        db.session.flush()
        for member in (beta, gamma):
            db.session.add(ClinicAuthorPoolMember(
                pool_id=pool.id, user_id=member.id, created_by=alpha.id))
        recording = Recording(user_id=alpha.id, title='Synthetic')
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(id='33333333-3333-4333-8333-333333333333',
                                    recording_id=recording.id, author_user_id=alpha.id)
        db.session.add(dictation)
        db.session.commit()
        members = {alpha.id, beta.id, gamma.id}
        ledger = AcceptanceLedger()

        def authorize(actor, permission, _row):
            if permission.endswith('read'):
                return actor != outsider.id
            return actor == alpha.id

        app.register_blueprint(create_blueprint(
            authorize=authorize, ledger=ledger, default_required=lambda _r: True,
            is_acceptance_member=lambda uid: uid in members, record_audit=lambda rows: None))

        @app.before_request
        def fresh_request_state():
            g.pop('csrf_token', None)
            g.pop('_login_user', None)

        login = LoginManager(app)

        class Actor(UserMixin):
            def __init__(self, uid):
                self.id = int(uid)

        login.user_loader(Actor)

        @app.get('/synthetic-login/<int:uid>')
        def browser(uid):
            login_user(Actor(uid))
            return {'csrf': generate_csrf()}

        clients = {}

        def client_for(user):
            if user.id not in clients:
                client = app.test_client()
                clients[user.id] = client, client.get(f'/synthetic-login/{user.id}').json['csrf']
            return clients[user.id]

        yield dict(ledger=ledger, dictation=dictation, client_for=client_for, alpha=alpha, beta=beta,
                   gamma=gamma, outsider=outsider, pool=pool, dead=dead,
                   url=f'/api/clinic/v1/dictations/{dictation.id}/acceptance-policy')


def _put(w, body, version):
    client, csrf = w['client_for'](w['alpha'])
    return client.put(w['url'], json=body, headers={'If-Match': f'"{version}"', 'X-CSRFToken': csrf})


def _users(w, extra=None):
    body = {'acceptance_required': True, 'approver_ids': [w['beta'].id]}
    if extra:
        body.update(extra)
    return body


def test_user_list_stays_compatible(world):
    saved = _put(world, _users(world), 0)
    assert saved.status_code == 200
    assert saved.json == {'version': 1, 'acceptance_required': True, 'approver_ids': [world['beta'].id]}
    client, _ = world['client_for'](world['alpha'])
    assert client.get(world['url']).json == saved.json
    stored = world['ledger'].current(SCOPE_DICTATION, world['dictation'].id)
    assert stored.approver_pool_ids == frozenset() and stored.deny_user_ids == frozenset()
    assert _put(world, {**_users(world), 'note': []}, 1).status_code == 400
    assert world['ledger'].current(SCOPE_DICTATION, world['dictation'].id).version == 1


def test_pool_and_deny_are_validated_and_versioned(world):
    w = world
    users = _users(w)
    assert _put(w, users, 0).status_code == 200
    unknown = '44444444-4444-4444-8444-444444444444'
    for body, code in (
            ({**users, 'approver_pool_ids': [unknown]}, 'INVALID_POOL'),
            ({**users, 'approver_pool_ids': [w['dead'].id]}, 'INVALID_POOL'),
            ({**users, 'approver_pool_ids': ['']}, 'INVALID_POOL'),
            ({**users, 'approver_pool_ids': [1]}, 'INVALID_POOL'),
            ({**users, 'deny_user_ids': [w['outsider'].id]}, 'INVALID_DENY'),
            ({**users, 'deny_user_ids': [True]}, 'INVALID_DENY'),
    ):
        refused = _put(w, body, 1)
        assert (refused.status_code, refused.json['code']) == (422, code)
    assert w['ledger'].current(SCOPE_DICTATION, w['dictation'].id).version == 1

    scoped = {**users, 'approver_ids': [w['gamma'].id], 'approver_pool_ids': [w['pool'].id],
              'deny_user_ids': [w['gamma'].id]}
    saved = _put(w, scoped, 1)
    assert saved.status_code == 200
    assert saved.json == {'version': 2, 'acceptance_required': True, 'approver_ids': [w['gamma'].id],
                          'approver_pool_ids': [w['pool'].id], 'deny_user_ids': [w['gamma'].id]}
    retry = _put(w, scoped, 1)
    assert retry.status_code == 200 and retry.json == saved.json
    assert len(w['ledger'].versions(SCOPE_DICTATION, w['dictation'].id)) == 2
    stale = _put(w, {**scoped, 'deny_user_ids': []}, 1)
    assert (stale.status_code, stale.json['version']) == (409, 2)

    kept = _put(w, {'acceptance_required': True, 'approver_ids': [w['beta'].id]}, 2)
    assert kept.status_code == 200
    assert kept.json['version'] == 3
    assert kept.json['approver_pool_ids'] == [w['pool'].id]
    assert kept.json['deny_user_ids'] == [w['gamma'].id]
    cleared = _put(w, {'acceptance_required': True, 'approver_ids': [w['beta'].id],
                       'approver_pool_ids': [], 'deny_user_ids': []}, 3)
    assert cleared.status_code == 200
    assert cleared.json == {'version': 4, 'acceptance_required': True, 'approver_ids': [w['beta'].id]}
    stored = w['ledger'].current(SCOPE_DICTATION, w['dictation'].id)
    assert stored.approver_pool_ids == frozenset() and stored.deny_user_ids == frozenset()


def test_denied_user_loses_even_with_a_user_grant_and_the_pool(world):
    w = world
    body = {'acceptance_required': True, 'approver_ids': [w['gamma'].id],
            'approver_pool_ids': [w['pool'].id], 'deny_user_ids': [w['gamma'].id]}
    assert _put(w, body, 0).status_code == 200
    assert author_pool_ids(w['beta'].id) == frozenset({w['pool'].id})
    assert author_pool_ids(w['gamma'].id) == frozenset({w['pool'].id})
    rule = w['ledger'].current(SCOPE_DICTATION, w['dictation'].id).as_rule()
    assert w['gamma'].id in rule.acceptor_user_ids and w['gamma'].id in rule.deny_user_ids

    def decide(actor):
        return w['ledger'].decide_acceptance(
            SCOPE_DICTATION, w['dictation'].id, kind='accept', actor_id=actor.id,
            actor_pool_ids=author_pool_ids(actor.id), revision_id='rev-1', reason=None,
            previous_assignee_id=None, now=datetime(2026, 11, 27, tzinfo=timezone.utc))

    with pytest.raises(AcceptanceError) as refused:
        decide(w['gamma'])
    assert refused.value.code == 'forbidden'
    assert w['ledger'].decisions(SCOPE_DICTATION, w['dictation'].id) == ()
    accepted = decide(w['beta'])
    assert accepted.decision.actor_id == w['beta'].id and accepted.policy_version == 1
