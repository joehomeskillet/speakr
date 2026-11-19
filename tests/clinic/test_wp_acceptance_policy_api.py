"""HTTP evidence for the acceptance-policy read and save endpoint. Synthetic data only."""

import pytest
from flask import Flask, g
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.models import ClinicDictation
from src.clinic.routes_acceptance_policy import create_blueprint
from src.database import db
from src.models import Recording, User


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-policy-api', SQLALCHEMY_DATABASE_URI='sqlite://')
    db.init_app(app)
    with app.app_context():
        db.create_all()
        users = []
        for name in ('author', 'member', 'outsider'):
            row = User(username=name, email=f'{name}@example.invalid', password='synthetic')
            db.session.add(row)
            db.session.flush()
            users.append(row)
        author, member, outsider = users
        recording = Recording(user_id=author.id, title='Synthetic')
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(id='11111111-1111-4111-8111-111111111111',
                                    recording_id=recording.id, author_user_id=author.id)
        db.session.add(dictation)
        db.session.commit()
        state = {'read': {author.id, member.id}, 'write': {author.id}, 'members': {member.id, author.id},
                 'audit': [], 'audit_down': False}
        ledger = AcceptanceLedger()

        def authorize(actor, permission, _row):
            return actor in state['read' if permission.endswith('read') else 'write']

        def audit(rows):
            if state['audit_down']:
                raise RuntimeError('down')
            state['audit'].extend(rows)

        app.register_blueprint(create_blueprint(
            authorize=authorize, ledger=ledger, default_required=lambda _r: True,
            is_acceptance_member=lambda uid: uid in state['members'], record_audit=audit))
        @app.before_request
        def fresh_request_state():
            # The fixture keeps one app context open, so g would leak between users.
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

        yield dict(app=app, state=state, ledger=ledger, dictation=dictation, client_for=client_for,
                   author=author, member=member, outsider=outsider, url=f'/api/clinic/v1/dictations/{dictation.id}/acceptance-policy')


def _put(w, who, body, version, token=True):
    client, csrf = w['client_for'](who)
    headers = {'If-Match': f'"{version}"'} | ({'X-CSRFToken': csrf} if token else {})
    return client.put(w['url'], json=body, headers=headers)


def test_read_default_and_hidden_for_other_actor(world):
    w = world
    client, _ = w['client_for'](w['member'])
    assert client.get(w['url']).json == {'version': 0, 'acceptance_required': True, 'approver_ids': []}
    hidden, _ = w['client_for'](w['outsider'])
    assert hidden.get(w['url']).status_code == 404
    assert hidden.get(w['url'].replace('1111', '2222')).status_code == 404


def test_guards_login_csrf_and_clinic_mode(world, monkeypatch):
    w = world
    with w['app'].test_client() as anonymous:
        assert anonymous.get(w['url']).status_code == 401
    body = {'acceptance_required': True, 'approver_ids': []}
    assert _put(w, w['author'], body, 0, token=False).status_code == 403
    monkeypatch.setenv('CLINIC_MODE', '0')
    client, _ = w['client_for'](w['author'])
    assert client.get(w['url']).status_code == 404


def test_save_then_read_audited_and_stale_version(world):
    w = world
    saved = _put(w, w['author'], {'acceptance_required': True, 'approver_ids': [w['member'].id]}, 0)
    assert saved.status_code == 200
    assert saved.json == {'version': 1, 'acceptance_required': True, 'approver_ids': [w['member'].id]}
    assert len(w['state']['audit']) == 1 and w['state']['audit'][0]['actor_id'] == w['author'].id
    client, _ = w['client_for'](w['member'])
    assert client.get(w['url']).json == saved.json
    stale = _put(w, w['author'], {'acceptance_required': False, 'approver_ids': []}, 0)
    assert (stale.status_code, stale.json['version']) == (409, 1)
    missing = w['client_for'](w['author'])
    assert missing[0].put(w['url'], json={}, headers={'X-CSRFToken': missing[1]}).status_code == 428
    assert w['ledger'].current(SCOPE_DICTATION, w['dictation'].id).version == 1


def test_unauthorized_writer_and_invalid_approver(world):
    w = world
    body = {'acceptance_required': True, 'approver_ids': [w['member'].id]}
    assert _put(w, w['member'], body, 0).status_code == 403
    assert _put(w, w['outsider'], body, 0).status_code == 404
    bad = _put(w, w['author'], {'acceptance_required': True, 'approver_ids': [w['outsider'].id]}, 0)
    assert (bad.status_code, bad.json['code']) == (422, 'INVALID_APPROVER')
    assert _put(w, w['author'], {'acceptance_required': True, 'approver_ids': ['x']}, 0).status_code == 422
    assert w['state']['audit'] == []
    with pytest.raises(Exception):
        w['ledger'].current(SCOPE_DICTATION, w['dictation'].id)


def test_retry_has_no_double_effect_and_audit_outage_writes_nothing(world):
    w = world
    body = {'acceptance_required': True, 'approver_ids': [w['member'].id]}
    w['state']['audit_down'] = True
    assert _put(w, w['author'], body, 0).status_code == 503
    assert w['ledger'].versions(SCOPE_DICTATION, w['dictation'].id) == ()
    w['state']['audit_down'] = False
    first = _put(w, w['author'], body, 0)
    retry = _put(w, w['author'], body, 0)
    assert (first.status_code, retry.status_code) == (200, 200) and retry.json == first.json
    assert len(w['ledger'].versions(SCOPE_DICTATION, w['dictation'].id)) == 1
    assert len(w['state']['audit']) == 1
