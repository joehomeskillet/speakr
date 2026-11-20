"""Synthetic HTTP evidence for identity administration and T-074."""

import re

import pytest

from src.app import app, db
from src.clinic import entra, identity
from src.models import AuditEvent, Recording, User
from src.services import audit

PASSWORD = 'synthetic administration phrase 37'


@pytest.fixture
def admin_client(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    with app.app_context():
        audit.flush()  # queued rows of earlier tests must land before they are cleared
        AuditEvent.query.delete()  # rows of earlier tests keep user ids that this test reuses
        db.session.commit()
        assert identity.bootstrap_first_admin('identity_admin', 'identity-admin@example.invalid', PASSWORD)
        uid = User.find_by_email('identity-admin@example.invalid').id
    client = app.test_client()
    assert client.post('/login', data={'login_id': 'identity_admin', 'password': PASSWORD,
                                      'csrf_token': csrf(client)}).status_code == 302
    return client, uid


def csrf(client):
    page = client.get('/login')
    if page.status_code == 200:
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True))[1]
    return client.get('/api/csrf-token').json['csrf_token']


def command(client, method, path, **data):
    return client.open(path, method=method, json=data, headers={'X-CSRFToken': csrf(client)})


def test_pending_edit_duplicate_and_explicit_role_checks(admin_client):
    client, actor = admin_client
    path = '/admin/users'
    fields = {'username': 'pending_user', 'email': 'pending@example.invalid'}
    assert client.post(path, json=fields).status_code == 400
    assert command(client, 'POST', path, **fields, roles=['typist']).status_code == 403
    created = command(client, 'POST', path, **fields)
    assert created.status_code == 201
    row = created.json
    assert (row['status'], row['login_policy'], row['is_admin']) == ('pending_activation', 'local_only', False)
    assert command(client, 'POST', path, **fields).status_code == 409
    target = f'{path}/{row["id"]}'
    updated = command(client, 'PUT', target, expected_version=row['version'], name='Neuer Name')
    assert updated.status_code == 200 and updated.json['name'] == 'Neuer Name'
    assert command(client, 'PUT', target, expected_version=row['version'], name='Veraltet').status_code == 409
    assert command(client, 'PUT', target, expected_version=updated.json['version'], password=PASSWORD).json['code'] == 'use_recovery_code'
    assert command(client, 'DELETE', target).status_code == 403
    with app.app_context():
        user = db.session.get(User, row['id'])
        assert user.name == 'Neuer Name' and user.password is None
        event = AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=user.id).one()
        assert event.actor_user_id == actor and event.details == {'fields': ['name']}
    listing = client.get('/admin/users?search=pending_user&per_page=1000').json['users']
    assert next(user for user in listing if user['id'] == row['id'])['version'] == updated.json['version']
    disabled = command(client, 'PUT', target, expected_version=updated.json['version'], status='disabled')
    assert disabled.json['status'] == 'disabled'
    reactivated = command(client, 'PUT', target, expected_version=disabled.json['version'], status='active')
    assert reactivated.json['status'] == 'pending_activation'
    recovery = command(client, 'PUT', target, expected_version=reactivated.json['version'], issue_recovery=True)
    assert recovery.status_code == 201 and recovery.headers['Cache-Control'] == 'no-store'
    assert recovery.json['purpose'] == 'activate_local'
    assert command(client, 'PUT', target, expected_version=reactivated.json['version'], issue_recovery=True).status_code == 409
    with app.app_context():
        assert all(recovery.json['code'] not in str(event.details) for event in
                   AuditEvent.query.filter_by(target_user_id=row['id']).all())


@pytest.mark.parametrize('mutation,field', [({'status': 'disabled'}, 'status'),
                                            ({'is_admin': False}, 'is_identity_admin'),
                                            ({'login_policy': 'entra_only'}, 'login_policy')])
def test_t074_last_local_admin_protected_then_change_is_audited(admin_client, mutation, field):
    client, first_id = admin_client
    path = f'/admin/users/{first_id}'
    with app.app_context():
        first = db.session.get(User, first_id)
        recording = Recording(user_id=first_id, title='Synthetic dictation', original_filename='test.wav')
        db.session.add(recording)
        db.session.add(entra.ClinicExternalIdentity(user_id=first_id, issuer='https://issuer.example.invalid',
                                                  tenant_id='synthetic', subject='subject', created_by=first_id))
        db.session.commit()
        recording_id = recording.id
        password_hash = first.password
        existing_events = {event.id for event in AuditEvent.query.filter_by(event='admin.user.changed',
                                                                           target_user_id=first_id)}
    refused = command(client, 'PUT', path, expected_version=1, **mutation)
    assert refused.status_code == 409 and refused.json['code'] == 'last_local_identity_admin'
    with app.app_context():
        new_events = AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=first_id).all()
        assert {event.id for event in new_events} == existing_events
        second = User(username='second_admin', email='second@example.invalid', password=password_hash, is_admin=True)
        db.session.add(second)
        db.session.flush()
        db.session.add(identity.ClinicAccountPolicy(user_id=second.id, status='pending_activation', is_identity_admin=True))
        db.session.commit()
        second_id = second.id
    assert command(client, 'PUT', path, expected_version=1, **mutation).status_code == 409
    with app.app_context():
        db.session.get(identity.ClinicAccountPolicy, second_id).status = 'active'
        db.session.commit()
    changed = command(client, 'PUT', path, expected_version=1, **mutation)
    assert changed.status_code == 200 and changed.json['version'] == 2
    assert client.get('/account').status_code == 302
    with app.app_context():
        assert db.session.get(Recording, recording_id).user_id == first_id
        event = AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=first_id).filter(
            AuditEvent.id.not_in(existing_events)).one()
        assert event.actor_user_id == first_id and field in event.details['fields']
        if field == 'login_policy':
            assert db.session.get(User, first_id).password is None


def test_technical_admin_cannot_manage_identities_without_explicit_grant(admin_client):
    client, actor = admin_client
    with app.app_context():
        db.session.get(identity.ClinicAccountPolicy, actor).is_identity_admin = False
        db.session.commit()
    response = command(client, 'POST', '/admin/users', username='new_user', email='new@example.invalid')
    assert response.status_code == 403 and response.json['code'] == 'unauthorized'


def test_explicit_adoption_preserves_existing_account_and_retries(admin_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from datetime import datetime, timedelta, timezone
    import bcrypt
    from src.clinic.models import ClinicDictation
    from src.clinic.pools import ClinicVisibilityGrant

    client, actor = admin_client
    old_hash = bcrypt.hashpw(b'synthetic existing password', bcrypt.gensalt(rounds=4)).decode()
    with app.app_context():
        user = User(username='legacy_admin', email='legacy@example.invalid', password=old_hash,
                    is_admin=True, session_version=17)
        db.session.add(user)
        db.session.flush()
        uid = user.id
        recording = Recording(user_id=uid, title='Legacy', original_filename='legacy.wav')
        grant = ClinicVisibilityGrant(user_id=uid, created_by=actor, scope='own', effect='allow')
        recovery = identity.ClinicLocalRecovery(user_id=uid, issued_by=actor, purpose='reset_local',
                                               token_hash='a' * 64, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        db.session.add_all([recording, grant, recovery])
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=uid)
        db.session.add(dictation)
        db.session.commit()
        did, rid, gid, cid = dictation.id, recording.id, grant.id, recovery.id
    path = f'/admin/users/{uid}'
    body = {'adopt_existing_local': True, 'expected_version': 0}
    assert client.put(path, json=body).status_code == 400
    for invalid in ({'expected_version': False}, {'email': 'other@example.invalid'}, {'is_admin': False}):
        assert command(client, 'PUT', path, **(body | invalid)).status_code == 400
    token = csrf(client)
    cookie = client.get_cookie(app.config['SESSION_COOKIE_NAME'])
    ready = Barrier(2)

    def submit(_):
        browser = app.test_client()
        browser.set_cookie(cookie.key, cookie.value)
        ready.wait(timeout=10)
        return browser.put(path, json=body, headers={'X-CSRFToken': token})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    for response in responses:
        assert response.status_code == 200
        assert response.json == dict(id=uid, version=1, status='active', login_policy='local_only', is_identity_admin=False)
    with app.app_context():
        target = db.session.get(User, uid)
        assert (target.password, target.is_admin, target.session_version) == (old_hash, True, 17)
        assert db.session.get(ClinicDictation, did).author_user_id == db.session.get(Recording, rid).user_id == uid
        assert db.session.get(ClinicVisibilityGrant, gid).user_id == uid
        code = db.session.get(identity.ClinicLocalRecovery, cid)
        assert (code.version, code.revoked_at, code.consumed_at) == (1, None, None)
        policy = db.session.get(identity.ClinicAccountPolicy, uid)
        assert (policy.security_version, policy.password_changed_at, policy.must_change_password) == (0, None, False)
        event = AuditEvent.query.filter_by(event='admin.user.changed', target_user_id=uid).one()
        assert event.actor_user_id == actor and event.details == {'fields': ['local_account_policy_adopted']}


def test_adoption_refuses_external_and_managed_accounts_and_requires_fresh_admin(admin_client):
    from src.clinic.entra_link_intent import ClinicIdentityLinkRequest

    client, actor = admin_client
    body = {'adopt_existing_local': True, 'expected_version': 0}
    with app.app_context():
        source = db.session.get(User, actor)
        targets = {}
        for kind in ('sso_provider', 'sso_subject', 'active_link', 'revoked_link', 'link_intent',
                     'entra_only', 'local_and_entra', 'device_only', 'disabled', 'pending_activation',
                     'no_password', 'unknown_hash', 'plain_local'):
            target = User(username=kind, email=f'{kind}@example.invalid', password=source.password)
            db.session.add(target)
            db.session.flush()
            targets[kind] = target.id
            if kind in ('sso_provider', 'sso_subject'):
                setattr(target, kind, 'synthetic')
            elif kind in ('active_link', 'revoked_link'):
                db.session.add(entra.ClinicExternalIdentity(user_id=target.id, issuer='synthetic', subject=kind,
                                                          tenant_id='synthetic', created_by=actor,
                                                          status=kind.split('_')[0]))
            elif kind == 'link_intent':
                db.session.add(ClinicIdentityLinkRequest(user_id=target.id, purpose='link', requested_by=actor,
                                                        expected_tenant_id='synthetic', expires_at=2**31))
            elif kind in ('entra_only', 'local_and_entra', 'device_only', 'disabled', 'pending_activation'):
                status = kind if kind in ('disabled', 'pending_activation') else 'active'
                login = kind if status == 'active' else 'local_only'
                db.session.add(identity.ClinicAccountPolicy(user_id=target.id, status=status, login_policy=login))
            elif kind in ('no_password', 'unknown_hash'):
                target.password = None if kind == 'no_password' else 'unknown'
        db.session.commit()
    assert command(client, 'PUT', '/admin/users/999999', **body).status_code == 404
    assert command(client, 'PUT', '/admin/users/9999999999999999999999', **body).status_code == 400
    for kind, uid in targets.items():
        if kind != 'plain_local':
            assert command(client, 'PUT', f'/admin/users/{uid}', **body).status_code == 409, kind
    local = targets['plain_local']
    path = f'/admin/users/{local}'
    outsider = app.test_client()
    assert command(outsider, 'PUT', path, **body).status_code in (302, 401, 403)
    with client.session_transaction() as session:
        fresh = session['clinic_reauth_at']
        session['clinic_reauth_at'] = fresh - 301
    assert command(client, 'PUT', path, **body).json['code'] == 'reauth_required'
    with client.session_transaction() as session:
        session['clinic_reauth_at'] = fresh
    with app.app_context():
        db.session.get(identity.ClinicAccountPolicy, actor).is_identity_admin = False
        db.session.commit()
    assert command(client, 'PUT', path, **body).status_code == 403
    with app.app_context():
        db.session.get(User, actor).is_admin = False
        db.session.commit()
    assert command(client, 'PUT', path, **body).status_code == 403
    with app.app_context():
        assert db.session.get(identity.ClinicAccountPolicy, local) is None
        assert AuditEvent.query.filter_by(event='admin.user.changed').filter(AuditEvent.target_user_id.in_(targets.values())).count() == 0
