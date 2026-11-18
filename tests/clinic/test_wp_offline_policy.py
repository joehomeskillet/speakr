"""Synthetic evidence for the managed offline policy, before API wiring."""

from dataclasses import replace

import pytest

from clinic_support import login
from src.app import app
from src.clinic import config
from src.clinic.offline_policy import (
    MANAGED_OFFLINE_PROFILE,
    USER_UNLOCK_KEY_POLICY,
    OfflineAuthorization,
    OfflineVersion,
    VerifiedBrowser,
    offline_capability,
)
from src.models import User


@pytest.fixture
def approval(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setitem(config.dev_defaults()['web_client'], 'managed_offline_enabled', True)
    user = User(id=17)
    browser = VerifiedBrowser(user.get_id(), 'browser-1', MANAGED_OFFLINE_PROFILE)
    return OfflineAuthorization(OfflineVersion(browser, 4), 4096, USER_UNLOCK_KEY_POLICY)


def test_explicit_approval_and_retry_use_fresh_grant(approval):
    browser, reference = approval.reference.browser, approval.reference
    granted = offline_capability(browser, approval, previous=None)
    assert granted.available and granted.authorization == approval and granted.message is None

    unavailable = offline_capability(browser, None, previous=reference)
    assert not unavailable.available and unavailable.authorization is None
    assert 'nicht verfügbar' in unavailable.message and 'können verbleiben' in unavailable.message
    assert offline_capability(browser, approval, previous=reference) == granted
    assert not offline_capability(browser, approval, previous=replace(reference, version=4.0)).available

    replacement = replace(approval, reference=replace(reference, version=5), quota_bytes=2048)
    assert not offline_capability(browser, replacement, previous=reference).available
    assert offline_capability(browser, replacement, previous=None).authorization == replacement


def test_forged_hints_cannot_enable_offline_or_disable_online(approval, clinic_on, world):
    url = '/upload/session?managed=true'
    headers = {'X-Clinic-Channel': MANAGED_OFFLINE_PROFILE}
    body = {'mime_type': 'audio/webm', 'offline_capability': True}
    with app.test_request_context(url, method='POST', headers=headers, json=body):
        profile = config.client_profile()
        assert profile == 'managed_web_online'
        assert config.client_capabilities()['profiles'][profile]['audio_capture'] == 'browser'
        assert not offline_capability(None, approval, previous=None).available
        for other in (profile, 'byod_internal'):
            browser = replace(approval.reference.browser, profile=other)
            matching = replace(approval, reference=replace(approval.reference, browser=browser))
            assert not offline_capability(browser, matching, previous=None).available

    response = login(world['user_a']).post(url, headers=headers, json=body)
    assert response.status_code == 201
    assert response.get_json()['kind'] == 'recorder'


def test_account_and_browser_changes_invalidate_existing_authorization(approval):
    reference = approval.reference
    user = User(id=17)
    user.end_all_sessions()
    for changed in (replace(reference.browser, account_version=user.get_id()),
                    replace(reference.browser, account_version=User(id=18).get_id()),
                    replace(reference.browser, browser_id='browser-2')):
        assert not offline_capability(changed, approval, previous=None).available
        new_approval = replace(approval, reference=replace(reference, browser=changed))
        assert not offline_capability(changed, new_approval, previous=reference).available


def test_incomplete_policy_or_disabled_gate_denies(approval, monkeypatch):
    reference = approval.reference
    browser = reference.browser
    for invalid in (replace(approval, quota_bytes=0), replace(approval, quota_bytes=True),
                    replace(approval, key_policy=''), replace(approval, key_policy='cleartext'),
                    replace(approval, reference=replace(reference, version=0))):
        assert not offline_capability(browser, invalid, previous=None).available
    with monkeypatch.context() as disabled:
        disabled.setitem(config.dev_defaults()['web_client'], 'managed_offline_enabled', False)
        assert not offline_capability(browser, approval, previous=None).available
    monkeypatch.delenv('CLINIC_MODE')
    assert not offline_capability(browser, approval, previous=None).available
