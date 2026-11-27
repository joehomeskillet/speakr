"""Synthetic staging evidence for the real Flask offline capability route."""

import os
import subprocess
import sys
from pathlib import Path
from time import time


ROOT = Path(__file__).resolve().parents[2]
CAPABILITY = '/api/clinic/v1/offline-web/capability'
BROWSER = 'a' * 64
PROXY_SECRET = 'synthetic-ingress-fixture-' * 2


def check_capability():
    from src.app import app, db
    from src.clinic.config import dev_defaults
    from src.init_db import initialize_database
    from src.models import User

    with app.app_context():
        initialize_database(app)
        owner = User(username='managed', email='managed@example.invalid', password='x', session_version=3)
        other = User(username='other', email='other@example.invalid', password='x')
        db.session.add_all([owner, other])
        db.session.commit()
        account_version, other_version = owner.get_id(), other.get_id()

    def signed_in(version):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(_user_id=version, _fresh=True)
        return client

    owner, other = signed_in(account_version), signed_in(other_version)
    policy = dev_defaults()['web_client']
    expires_at = int(time()) + 300
    approval = dict(browser_id=BROWSER, account_version=account_version, version=5,
                    quota_bytes=4096, expires_at=expires_at)
    trusted = {'X-Clinic-Client-Cert-Sha256': BROWSER, 'X-Clinic-Offline-Proxy-Secret': PROXY_SECRET}
    previous = {
        'X-Offline-Previous-Version': '5',
        'X-Offline-Previous-Browser-Id': BROWSER,
        'X-Offline-Previous-Account-Version': account_version,
    }

    def denied(client, headers=None):
        response = client.get(CAPABILITY + '?profile=managed_web_offline', headers=headers)
        assert response.status_code == 200
        assert response.json['available'] is False
        assert set(response.json) == {'available', 'message'}
        assert response.headers['Cache-Control'] == 'no-store'
        assert 'X-Offline-Grant-Expires-At' not in response.headers

    policy['managed_offline_enabled'] = True
    denied(owner, trusted)  # An attested browser still needs explicit approval.
    policy['managed_offline_test_grant'] = approval
    denied(owner, {**previous, 'X-Clinic-Channel': 'managed_web_offline'})
    denied(owner, {'X-Clinic-Client-Cert-Sha256': BROWSER})
    denied(owner, {**trusted, 'X-Clinic-Offline-Proxy-Secret': 'forged-browser-value'})
    denied(owner, {**trusted, 'X-Clinic-Client-Cert-Sha256': 'b' * 64})
    denied(other, trusted)
    assert app.test_client().get(CAPABILITY, headers=trusted).status_code == 401

    expected = dict(available=True, version=5, account_version=account_version,
                    browser_id=BROWSER, quota_bytes=4096, key_policy='encrypted_user_unlock')
    for headers in (trusted, {**trusted, **previous}):
        response = owner.get(CAPABILITY, headers=headers)
        assert response.status_code == 200
        assert response.json == expected
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Offline-Grant-Expires-At'] == str(expires_at)

    assert owner.get(CAPABILITY, headers={**trusted, **previous,
                     'X-Offline-Previous-Version': '4'}).status_code == 412
    approval['expires_at'] = int(time())
    for headers in (trusted, {**trusted, **previous}):
        response = owner.get(CAPABILITY, headers=headers)
        assert response.status_code == 403
        assert response.headers['Cache-Control'] == 'no-store'
        assert 'X-Offline-Grant-Expires-At' not in response.headers

    approval['expires_at'] = expires_at
    policy['managed_offline_enabled'] = False
    denied(owner, trusted)
    policy['managed_offline_enabled'] = True
    os.environ['CLINIC_PROFILE'] = 'production'
    denied(owner, trusted)


def test_managed_staging_grant_discovery_and_reconnect(tmp_path):
    env = {
        **os.environ,
        'CLINIC_MODE': '1',
        'CLINIC_PROFILE': 'staging',
        'CLINIC_OFFLINE_TEST_PROXY_SECRET': PROXY_SECRET,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "app.db"}',
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
        'CLINIC_ORIGINALS_ROOT': str(tmp_path / 'originals'),
        'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
    }
    result = subprocess.run(
        [sys.executable, '-c', 'import runpy, sys; runpy.run_path(sys.argv[1])["check_capability"]()', __file__],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr[-6000:]
