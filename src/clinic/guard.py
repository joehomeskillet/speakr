"""Flask guard: one app-level before_request hook for clinic mode.

Every request is looked up in the route-policy table (src/clinic/route_policy.py)
before its handler runs. An endpoint that is not in the table is refused, so a
new route is denied until someone classifies it. Requests that address a
recording are decided by src/clinic/policy.py, whatever the handler does after.

Inert when clinic mode is off.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, request, session
from flask_login import current_user

from src.clinic import policy
from src.clinic.config import clinic_mode_enabled, feature_locked, setting_ceiling
from src.clinic.route_policy import ADMIN, AUTH, DISABLED, PUBLIC, RECORDING, ROUTE_POLICY, RouteRule

logger = logging.getLogger(__name__)

SETTINGS_WRITE_ENDPOINT = 'admin.admin_update_setting'


def _refuse(status: int, code: str, **extra: Any):
    response = jsonify({'error': code.replace('_', ' '), 'code': code, **extra})
    response.status_code = status
    return response


def _unauthenticated():
    from flask import current_app
    return current_app.login_manager.unauthorized()


def _owner_of_recording(recording_id: int) -> int | None:
    from src.database import db
    from src.models import Recording
    return db.session.query(Recording.user_id).filter(Recording.id == recording_id).scalar()


def _recording_of_event(event_id: int) -> int | None:
    from src.database import db
    from src.models import Event
    return db.session.query(Event.recording_id).filter(Event.id == event_id).scalar()


def _as_id(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(value)
    return int(value)


def _body_ids(keys: tuple[str, ...]) -> list[int]:
    """Recording ids named in the JSON or form body under the given keys.

    Raises ValueError for a value that is not an id: a request the policy
    cannot read is refused, not passed on.
    """
    if not keys:
        return []
    payload = request.get_json(silent=True)
    source = payload if isinstance(payload, dict) else request.form
    ids: list[int] = []
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        for item in (value if isinstance(value, list) else [value]):
            ids.append(_as_id(item))
    return ids


def _target_ids(rule: RouteRule) -> list[int]:
    ids: list[int] = []
    arg = (request.view_args or {}).get(rule.arg)
    if arg is not None:
        if rule.arg == 'event_id':
            recording_id = _recording_of_event(arg)
            if recording_id is None:
                raise LookupError(arg)
            arg = recording_id
        ids.append(_as_id(arg))
    ids.extend(_body_ids(rule.body_keys))
    return ids


def _check_recordings(rule: RouteRule):
    """Decide every recording the request addresses; a response means refused."""
    try:
        ids = _target_ids(rule)
    except LookupError:
        return _refuse(404, 'not_found')
    except (TypeError, ValueError):
        return _refuse(400, 'invalid_recording_id')
    if not ids:
        return None
    subject = policy.build_subject(current_user.id, bool(current_user.is_admin))
    for recording_id in ids:
        owner_id = _owner_of_recording(recording_id)
        if owner_id is None:
            return _refuse(404, 'not_found')
        decision = policy.decide(subject, rule.action or 'view',
                                 policy.build_facts(recording_id, owner_id))
        if not decision.allowed:
            logger.warning('clinic policy denied %s %s on recording %s for user %s: %s',
                           rule.action, request.endpoint, recording_id, current_user.id, decision.reason)
            if decision.visible:
                return _refuse(403, 'forbidden')
            return _refuse(404, 'not_found')
    return None


def _check_setting_write():
    """A stock settings write may not lift a clinic ceiling (R-050)."""
    data = request.get_json(silent=True)
    key = data.get('key') if isinstance(data, dict) else None
    if not isinstance(key, str):
        return None
    locked, forced = setting_ceiling(key)
    if not locked:
        return None
    value = data.get('value')
    if value is None or str(value).strip() == '' or str(value).strip().lower() == str(forced).lower():
        return None
    return _refuse(403, 'clinic_policy_ceiling', key=key)


def clinic_guard():
    """before_request hook; returns a response to refuse, None to continue."""
    if not clinic_mode_enabled():
        return None
    if request.endpoint is None:
        return None  # no route matched: Flask answers 404, nothing is served
    rule = ROUTE_POLICY.get(request.endpoint)
    if rule is None:
        logger.warning('clinic policy: unclassified route %s %s refused', request.method, request.path)
        return _refuse(403, 'clinic_unclassified_route')

    if rule.kind != PUBLIC and feature_locked('personal_api_tokens') and not session.get('_user_id'):
        from src.utils.token_auth import resolve_request_token
        if resolve_request_token()[0] is not None:
            return _refuse(403, 'clinic_feature_disabled', feature='personal_api_tokens')

    if rule.kind == PUBLIC:
        return None
    if rule.kind == DISABLED and feature_locked(rule.feature or ''):
        return _refuse(403, 'clinic_feature_disabled', feature=rule.feature)

    if not current_user.is_authenticated:
        return _unauthenticated()
    if rule.kind == ADMIN:
        if not current_user.is_admin:
            return _refuse(403, 'forbidden')
        if request.endpoint == SETTINGS_WRITE_ENDPOINT:
            return _check_setting_write()
        return None
    if rule.kind in (RECORDING, DISABLED):
        return _check_recordings(rule)
    return None if rule.kind == AUTH else _refuse(403, 'clinic_unclassified_route')


def install_clinic_guard(app: Flask) -> None:
    """Register the guard on the app. Call before the other before_request hooks."""
    app.before_request(clinic_guard)
    if not clinic_mode_enabled():
        return
    try:
        with app.app_context():
            from src.clinic.config import find_setting_conflicts
            for conflict in find_setting_conflicts():
                logger.warning('clinic policy overrides stored setting %s=%s (effective: %s)',
                               conflict['key'], conflict['stored'], conflict['effective'])
    except Exception as exc:  # noqa: BLE001 - the schema may not exist yet at import time
        logger.debug('clinic startup setting check skipped: %s', exc)
