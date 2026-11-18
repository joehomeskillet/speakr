"""Exercise existing authentication endpoints with synthetic local accounts."""

import re

import pytest

from src.api import auth
from src.app import app, db
from src.clinic import identity
from src.models import User

PASSWORD = 'synthetic local passphrase 16'
NEW_PASSWORD = 'synthetic replacement phrase 94'


@pytest.fixture(autouse=True)
def local_mode(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    monkeypatch.setattr(auth, 'is_sso_enabled', lambda: pytest.fail('Legacy provider consulted'))


@pytest.fixture(scope='module')
def password_hash():
    return identity.hash_password(PASSWORD)


def user(login_policy='local_only', *, password_hash=None, status='active'):
    with app.app_context():
        row = User(username=login_policy, email=f'{login_policy}@example.invalid', password=password_hash,
                   is_admin=True)
        db.session.add(row)
        db.session.flush()
        db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status=status, login_policy=login_policy))
        db.session.commit()
        return row.id


def token(client):
    page = client.get('/login')
    if page.status_code == 200:
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True))[1]
    return client.get('/api/csrf-token').json['csrf_token']


def login(client, name='local_only', password=PASSWORD):
    return client.post('/login', data={'login_id': name, 'password': password, 'remember': '1',
                                      'csrf_token': token(client)})


@pytest.mark.parametrize('policy,allowed', [('local_only', True), ('local_and_entra', True),
                                           ('entra_only', False), ('device_only', False)])
def test_local_login_policy_even_for_upstream_admin(policy, allowed, password_hash):
    uid = user(policy, password_hash=password_hash)
    client = app.test_client()
    assert client.post('/login', data={'login_id': policy, 'password': PASSWORD}).status_code == 400
    response = login(client, policy.upper() + '@EXAMPLE.INVALID')
    assert response.status_code == (302 if allowed else 200)
    with client.session_transaction() as state:
        assert state.get('_user_id') == (str(uid) if allowed else None)
    assert client.get_cookie('remember_token') is None
    for path in ('/auth/sso/login', '/auth/sso/callback'):
        assert app.test_client().get(path).location.endswith('/login')


def test_activation_and_reset_bind_purpose_and_never_create_session():
    uid = user(status='pending_activation')
    with app.app_context():
        activation = identity.issue_recovery(uid, issued_by=uid, expected_version=1)
    client = app.test_client()

    def redeem(code, purpose, password=PASSWORD):
        return client.post('/login', json={'code': code, 'purpose': purpose, 'new_password': password,
                                          'confirmation': password}, headers={'X-CSRFToken': token(client)})

    assert login(client).status_code == 200
    assert redeem(activation.code, 'reset_local').json['code'] == 'invalid_recovery_code'
    assert redeem(activation.code, 'activate_local', 'too short').json['code'] == 'too_short'
    assert redeem(activation.code, 'activate_local').status_code == 204
    assert redeem(activation.code, 'activate_local').status_code == 400
    assert login(client).status_code == 302
    with app.app_context():
        reset = identity.issue_recovery(uid, issued_by=uid, expected_version=3)
    assert client.get('/account').status_code == 302
    assert login(client).status_code == 200
    assert redeem(reset.code, 'reset_local', NEW_PASSWORD).status_code == 204
    with client.session_transaction() as state:
        assert '_user_id' not in state
    assert login(client, password=NEW_PASSWORD).status_code == 302


def test_password_change_rotates_only_current_session(password_hash):
    user(password_hash=password_hash)
    current, other = app.test_client(), app.test_client()
    assert login(current).status_code == login(other).status_code == 302
    data = {'current_password': PASSWORD, 'new_password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
    assert current.post('/change_password', data=data).status_code == 400
    response = current.post('/change_password', data=data, headers={'X-CSRFToken': token(current)})
    assert response.location == '/account'
    assert current.get('/account').status_code == 200
    assert other.get('/account').status_code == 302


def test_lock_and_account_switch_isolate_signed_cookie_state(password_hash, monkeypatch):
    user(password_hash=password_hash)
    second_id = user('local_and_entra', password_hash=password_hash)
    monkeypatch.setattr(auth.login_lock, 'lock_settings', lambda: (2, 15))
    client = app.test_client()
    assert login(client, password='wrong').status_code == 200
    assert login(client, password='wrong').status_code == 200
    assert login(client).status_code == 200
    with app.app_context():
        auth.login_lock.clear_failures(User.find_by_email('local_only@example.invalid').id)
        db.session.commit()
    assert login(client).status_code == 302
    with client.session_transaction() as state:
        state['account_local_view'] = 'old-account'
    response = client.post('/logout', headers={'X-CSRFToken': token(client)})
    assert response.status_code == 302 and client.get_cookie(auth.SIGNED_OUT_COOKIE).value == '1'
    assert login(client, 'local_and_entra').status_code == 302
    with client.session_transaction() as state:
        assert state.get('_user_id') == str(second_id) and 'account_local_view' not in state
