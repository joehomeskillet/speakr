"""Review fixes on the identity wire: lock, audit and re-authentication evidence (synthetic data)."""

import re
import time

import pytest

from src.api import auth
from src.app import app, db
from src.clinic import identity
from src.models import AuditEvent, LoginFailure, User
from src.services import audit, login_lock

PASSWORD = 'synthetic review phrase 41 abc'
NEW_PASSWORD = 'synthetic replacement phrase 77'


@pytest.fixture(autouse=True)
def clinic(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    monkeypatch.setattr(login_lock, 'lock_settings', lambda: (2, 15))
    with app.app_context():  # user ids are reused between tests, lock rows are not reset by the fixtures
        LoginFailure.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        LoginFailure.query.delete()
        db.session.commit()


def csrf(client):
    page = client.get('/login')
    if page.status_code == 200:
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True))[1]
    return client.get('/api/csrf-token').json['csrf_token']


def make_admin(name):
    with app.app_context():
        assert identity.bootstrap_first_admin(name, f'{name}@example.invalid', PASSWORD)
        return User.find_by_email(f'{name}@example.invalid').id


def sign_in(client, name, password=PASSWORD, addr='10.1.0.1'):
    return client.post('/login', data={'login_id': name, 'password': password, 'csrf_token': csrf(client)},
                       environ_base={'REMOTE_ADDR': addr})


def events(user_id, event='auth.login.failed'):
    assert audit.flush()
    return AuditEvent.query.filter_by(event=event, target_user_id=user_id).all()


def test_change_password_counts_audits_and_respects_lock():
    uid = make_admin('review_pw')
    with app.app_context():
        for _ in range(2):
            with pytest.raises(identity.AuthenticationFailed):
                identity.change_password(uid, 'wrong guess', NEW_PASSWORD)
        rows = [r for r in events(uid) if r.details['method'] == 'password_change']
        assert [r.details['reason'] for r in rows] == ['bad_current_password'] * 2
        assert all(PASSWORD not in str(r.details) and 'wrong guess' not in str(r.details) for r in rows)
        with pytest.raises(identity.AuthenticationFailed):
            identity.change_password(uid, PASSWORD, NEW_PASSWORD)  # correct password, but locked
        login_lock.clear_failures(uid)
        db.session.commit()
        assert isinstance(identity.change_password(uid, PASSWORD, NEW_PASSWORD), int)


def test_account_wide_cap_blocks_distributed_guessing_and_success_resets():
    make_admin('review_wide')
    client = app.test_client()
    for i in range(7):  # per-client cap is 2; account-wide cap is 4 * 2 = 8
        assert sign_in(client, 'review_wide', 'wrong', addr=f'10.2.0.{i}').status_code == 200
    assert sign_in(client, 'review_wide', addr='10.2.9.9').status_code == 302  # below the cap, resets
    with client.session_transaction() as state:
        state.clear()
    for i in range(8):
        assert sign_in(client, 'review_wide', 'wrong', addr=f'10.3.0.{i}').status_code == 200
    assert sign_in(client, 'review_wide', addr='10.3.9.9').status_code == 200  # locked account-wide
    with app.app_context():
        uid = User.find_by_email('review_wide@example.invalid').id
        assert login_lock.is_locked(uid, identity.ACCOUNT_LOCK_KEY)


def test_create_user_requires_recent_reauthentication():
    make_admin('review_create')
    client = app.test_client()
    assert sign_in(client, 'review_create').status_code == 302
    body = {'username': 'review_new', 'email': 'review-new@example.invalid'}
    with client.session_transaction() as state:
        state['clinic_reauth_at'] = time.time() - 3600
    stale = client.post('/admin/users', json=body, headers={'X-CSRFToken': csrf(client)})
    assert stale.status_code == 403 and stale.json['code'] == 'reauth_required'
    with client.session_transaction() as state:
        state['clinic_reauth_at'] = time.time()
    assert client.post('/admin/users', json=body, headers={'X-CSRFToken': csrf(client)}).status_code == 201


def test_failed_recovery_redemption_is_audited_and_counted():
    uid = make_admin('review_rec')
    with app.app_context():
        issued = identity.issue_recovery(uid, issued_by=uid)
    client = app.test_client()

    def redeem(code, purpose):
        return client.post('/login', json={'code': code, 'purpose': purpose, 'new_password': NEW_PASSWORD,
                                           'confirmation': NEW_PASSWORD}, headers={'X-CSRFToken': csrf(client)},
                           environ_base={'REMOTE_ADDR': '10.4.0.1'})

    assert redeem(issued.code, 'activate_local').status_code == 400  # wrong purpose
    with app.app_context():
        rows = [r for r in events(uid) if r.details['method'] == 'recovery']
        assert [r.details for r in rows] == [{'reason': 'bad_recovery_code', 'method': 'recovery'}]
        assert all(issued.code not in str(r.details) for r in rows)
        assert db.session.query(LoginFailure).filter_by(user_id=uid, address='10.4.0.1').one().failed_count == 1
    assert redeem(issued.code, 'reset_local').status_code == 204  # still works
