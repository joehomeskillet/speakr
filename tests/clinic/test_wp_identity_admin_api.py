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
