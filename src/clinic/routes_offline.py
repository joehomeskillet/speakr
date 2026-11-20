"""Browser-session capability discovery for managed offline storage (P21)."""

from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user

from src.clinic.offline_policy import offline_capability

blueprint = Blueprint('clinic_offline', __name__, url_prefix='/api/clinic/v1/offline-web')
_PREVIOUS_HEADERS = (
    'X-Offline-Previous-Version', 'X-Offline-Previous-Browser-Id', 'X-Offline-Previous-Account-Version',
)


@blueprint.after_request
def no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@blueprint.errorhandler(400)
def invalid_request(_error):
    response = jsonify(type='about:blank', status=400, code='INVALID_PAYLOAD',
                       title='Ungültige Anfrage', correlation_id=str(uuid4()))
    response.mimetype = 'application/problem+json'
    return response, 400


@blueprint.get('/capability')
def capability():
    if not (current_user.is_authenticated and current_user.is_active
            and session.get('_user_id') == current_user.get_id()):
        abort(401)
    version, browser_id, account_version = previous = tuple(request.headers.get(h) for h in _PREVIOUS_HEADERS)
    if (request.content_length or 0) > 1024:
        abort(400)
    if any(value is not None for value in previous):
        if (not version or len(version) > 10 or not version.isascii() or not version.isdecimal()
                or not 1 <= int(version) <= 2147483647
                or not browser_id or len(browser_id) > 128
                or not account_version or len(account_version) > 128):
            abort(400)

    # No trusted browser and grant provider is wired. Request hints cannot supply either.
    decision = offline_capability(None, None, previous=None)
    return jsonify(available=decision.available, message='Offline-Speicherung ist nicht freigegeben.')
