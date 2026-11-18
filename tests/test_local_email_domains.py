"""Addresses on special-use domains such as .local (#551).

email_validator refuses special-use domains whatever its options. Sign-in
accepts any well-formed address, so an account an administrator created on an
internal domain can sign in.
"""
import uuid

import pytest

from src.app import app, db
from src.api import auth as auth_mod
from src.models import User

PW = "Reg1ster-Pw!"


@pytest.fixture(autouse=True)
def _no_csrf():
    prev = app.config.get('WTF_CSRF_ENABLED')
    app.config['WTF_CSRF_ENABLED'] = False
    yield
    app.config['WTF_CSRF_ENABLED'] = prev


@pytest.fixture
def emails():
    created = []
    with app.app_context():
        yield created
        for u in User.query.filter(User.email.in_(created or [''])).all():
            db.session.delete(u)
        db.session.commit()


def test_an_account_on_a_local_domain_can_sign_in(emails):
    email = uuid.uuid4().hex[:8] + "@organization.local"
    emails.append(email)
    hashed = auth_mod.bcrypt.generate_password_hash(PW).decode('utf-8')
    user = User(username="loc_" + uuid.uuid4().hex[:10], email=email, password=hashed, email_verified=True)
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    resp = client.post('/login', data={'email': email, 'password': PW})
    assert resp.status_code == 302
    with client.session_transaction() as s:
        assert s.get('_user_id') == str(user.id)


def test_sign_in_still_refuses_a_malformed_address():
    with app.app_context():
        resp = app.test_client().post('/login', data={'email': 'not an address', 'password': PW})
    assert resp.status_code == 200
