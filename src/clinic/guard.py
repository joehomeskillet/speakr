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
from src.clinic.request_ids import as_id, body_flag, request_ids
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


def _job_target(job_id: int) -> tuple[int, bool]:
    """(recording of the job, True when the call deletes that recording).

    Deleting a failed new upload removes its recording with it (stock behaviour
    of DELETE /api/recordings/jobs/<id>), so that call is a hard delete.
    """
    from src.database import db
    from src.models import ProcessingJob
    row = db.session.query(ProcessingJob.recording_id, ProcessingJob.status,
                           ProcessingJob.is_new_upload).filter(ProcessingJob.id == job_id).first()
    if row is None:
        raise LookupError(job_id)
    destructive = request.method == 'DELETE' and row.status == 'failed' and bool(row.is_new_upload)
    return row.recording_id, destructive


def _own_recording_ids() -> list[int]:
    from src.database import db
    from src.models import Recording
    return [rid for (rid,) in db.session.query(Recording.id).filter(Recording.user_id == current_user.id)]


def _targets(rule: RouteRule) -> tuple[list[int], str]:
    """(recording ids the request addresses, action it needs).

    Ids come from the URL, the query string, the form and the JSON body (see
    src/clinic/request_ids.py), whatever the route table says about the route.
    """
    action = rule.action or 'view'
    ids = request_ids(rule.body_keys)
    arg = (request.view_args or {}).get(rule.arg)
    if arg is not None:
        if rule.arg == 'job_id':
            recording_id, destructive = _job_target(arg)
            ids.append(recording_id)
            if destructive:
                action = 'delete'
        else:
            ids.append(as_id(arg))
    if rule.delete_flags and body_flag(rule.delete_flags):
        action = 'delete'
    if rule.batch_all:
        ids.extend(_own_recording_ids())
    return list(dict.fromkeys(ids)), action


def _check_recordings(rule: RouteRule):
    """Decide every recording the request addresses; a response means refused."""
    try:
        ids, action = _targets(rule)
    except LookupError:
        return _refuse(404, 'not_found')
    except (TypeError, ValueError):
        return _refuse(400, 'invalid_recording_id')
    if action == 'delete' and rule.action != 'delete' and feature_locked('clinical_user_hard_delete'):
        return _refuse(403, 'clinic_feature_disabled', feature='clinical_user_hard_delete')
    if not ids:
        if rule.batch_all:
            return _refuse(403, 'clinic_no_target')  # nothing to check, nothing to export
        if rule.kind == DISABLED:
            return _refuse(403, 'clinic_feature_unsupported')  # cannot tell which recording
        return None
    subject = policy.build_subject(current_user.id, bool(current_user.is_admin))
    for recording_id in ids:
        owner_id = _owner_of_recording(recording_id)
        if owner_id is None:
            return _refuse(404, 'not_found')
        decision = policy.decide(subject, action, policy.build_facts(recording_id, owner_id))
        if not decision.allowed:
            logger.warning('clinic policy denied %s %s on recording %s for user %s: %s',
                           action, request.endpoint, recording_id, current_user.id, decision.reason)
            if decision.visible:
                return _refuse(403, 'forbidden')
            return _refuse(404, 'not_found')
    return None


def _check_incidental_ids(rule: RouteRule):
    """AUTH and ADMIN handlers work on the caller's own data, but a request may
    still name a recording (a filter, a query): it is decided like a view."""
    for name, value in rule.forbid_args:
        if any(v.strip().lower() == value for v in request.args.getlist(name)):
            return _refuse(403, 'clinic_scope_forbidden', arg=name)
    return _check_recordings(RouteRule(RECORDING, action='view', arg='-'))


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
            refusal = _check_setting_write()
            if refusal is not None:
                return refusal
        return _check_incidental_ids(rule)
    if rule.kind in (RECORDING, DISABLED):
        return _check_recordings(rule)
    return _check_incidental_ids(rule) if rule.kind == AUTH else _refuse(403, 'clinic_unclassified_route')


def ensure_guard_first(app: Flask) -> None:
    """Put the guard at the front of the app-level before_request hooks.

    Other initialisation may prepend its own hook (the session idle check does);
    call this after all of it so that a refused request runs nothing else.
    """
    hooks = app.before_request_funcs.setdefault(None, [])
    while clinic_guard in hooks:
        hooks.remove(clinic_guard)
    hooks.insert(0, clinic_guard)


def install_clinic_guard(app: Flask) -> None:
    """Register the guard on the app; see ensure_guard_first for the order."""
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
