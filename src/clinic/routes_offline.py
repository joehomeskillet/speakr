"""Browser-session capability discovery for managed offline storage (P21)."""

import hmac
import os
import re
from time import time
from uuid import uuid4

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user

from src.clinic.config import dev_defaults
from src.clinic.offline_policy import (
    MANAGED_OFFLINE_PROFILE, USER_UNLOCK_KEY_POLICY, OfflineAuthorization,
    OfflineVersion, VerifiedBrowser, offline_capability,
)

blueprint = Blueprint('clinic_offline', __name__, url_prefix='/api/clinic/v1/offline-web')
GRANT_PROVIDER = 'clinic_offline_grant_provider'
_PREVIOUS_HEADERS = (
    'X-Offline-Previous-Version', 'X-Offline-Previous-Browser-Id', 'X-Offline-Previous-Account-Version',
)
_MAX_VERSION = 2147483647
_MAX_QUOTA = 128 * 1024 * 1024


def staging_grant(account_version):
    """Resolve the single test approval against the ingress-verified browser certificate."""
    if os.environ.get('CLINIC_PROFILE', '').strip().lower() != 'staging':
        return None
    policy = dev_defaults().get('web_client')
    if not isinstance(policy, dict) or policy.get('managed_offline_enabled') is not True:
        return None
    approval = policy.get('managed_offline_test_grant')
    if not isinstance(approval, dict) or approval.get('account_version') != account_version:
        return None

    # Ingress must strip these headers and set them only after successful managed-browser mTLS.
    secret = os.environ.get('CLINIC_OFFLINE_TEST_PROXY_SECRET', '').encode('utf-8')
    attestation = request.headers.get('X-Clinic-Offline-Proxy-Secret', '').encode('utf-8')
    browser_id = request.headers.get('X-Clinic-Client-Cert-Sha256', '').lower()
    if (len(secret) < 32 or not hmac.compare_digest(secret, attestation)
            or not re.fullmatch(r'[0-9a-f]{64}', browser_id) or approval.get('browser_id') != browser_id):
        return None

    version, quota, expires_at = (approval.get(key) for key in ('version', 'quota_bytes', 'expires_at'))
    if (type(version) is not int or not 1 <= version <= _MAX_VERSION
            or type(quota) is not int or not 1 <= quota <= _MAX_QUOTA
            or type(expires_at) is not int or not 1 <= expires_at <= _MAX_VERSION):
        return None
    browser = VerifiedBrowser(account_version, browser_id, MANAGED_OFFLINE_PROFILE)
    grant = OfflineAuthorization(OfflineVersion(browser, version), quota, USER_UNLOCK_KEY_POLICY)
    return browser, grant, expires_at


@blueprint.after_request
def no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@blueprint.errorhandler(400)
def invalid_request(_error):
    return _problem(400, 'INVALID_PAYLOAD', 'Ungültige Anfrage')


def _problem(status, code, title):
    response = jsonify(type='about:blank', status=status, code=code, title=title, correlation_id=str(uuid4()))
    response.mimetype = 'application/problem+json'
    return response, status


@blueprint.get('/capability')
def capability():
    account = current_user.get_id() if current_user.is_authenticated and current_user.is_active else None
    if not account or session.get('_user_id') != account:
        abort(401)
    version, browser_id, account_version = headers = tuple(request.headers.get(h) for h in _PREVIOUS_HEADERS)
    if (request.content_length or 0) > 1024:
        abort(400)
    previous = None
    if any(value is not None for value in headers):
        if (not version or len(version) > 10 or not version.isascii() or not version.isdecimal()
                or not 1 <= int(version) <= _MAX_VERSION
                or not browser_id or len(browser_id) > 128
                or not account_version or len(account_version) > 128):
            abort(400)
        previous = OfflineVersion(VerifiedBrowser(account_version, browser_id, MANAGED_OFFLINE_PROFILE), int(version))

    provider = current_app.extensions.get(GRANT_PROVIDER)
    facts = provider(account) if provider else None
    if facts:
        browser, grant, expires_at = facts
        if offline_capability(browser, grant, previous=None).available:
            if expires_at <= time():
                return _problem(403, 'OFFLINE_GRANT_EXPIRED', 'Offline-Freigabe abgelaufen')
            if previous is not None and not offline_capability(browser, grant, previous=previous).available:
                return _problem(412, 'OFFLINE_GRANT_STALE', 'Offline-Freigabe geändert')
            response = jsonify(available=True, version=grant.reference.version, account_version=browser.account_version,
                               browser_id=browser.browser_id, quota_bytes=grant.quota_bytes, key_policy=grant.key_policy)
            response.headers['X-Offline-Grant-Expires-At'] = str(expires_at)
            return response
    return jsonify(available=False, message='Offline-Speicherung ist nicht freigegeben.')
