"""Browser read and save of the acceptance policy of one dictation.

Thin HTTP layer over ``acceptance_policy.AcceptanceLedger`` (versions, retry
semantics) and the central per-object ``authorize`` callback. The actor is
always the logged-in session user. A save needs CSRF and an ``If-Match``
policy version; a stale version gets 409 with the authoritative ``{version}``.
A body equal to the current policy is a retry and changes nothing, so it
creates no second version and no second audit row.

The app wiring injects the callbacks. A published version, the workflow flag and the audit row
are committed together. A refused write is audited as a denied ``recording.edited`` (field names
only) and committed on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger, PolicyError
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.database import db
from src.services import audit

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/acceptance-policy'
PERM_READ = 'clinic.acceptance_policy.read'
PERM_WRITE = 'clinic.acceptance_policy.write'
Authorize = Callable[[int, str, ClinicDictation], bool]
_TITLES = {400: 'Ungültige Anfrage', 401: 'Anmeldung erforderlich', 403: 'Zugriff verweigert',
           404: 'Diktat nicht gefunden', 409: 'Versionskonflikt', 422: 'Abnahmerichtlinie ungültig',
           428: 'Version erforderlich', 503: 'Dienst nicht verfügbar'}


def _problem(status: int, code: str | None = None, **extra: Any) -> Response:
    response = jsonify(type='about:blank', title=_TITLES[status], status=status,
                       code=code or {404: 'NOT_FOUND'}.get(status, 'ERROR'),
                       correlation_id=str(uuid4()), **extra)
    response.status_code = status
    response.mimetype = 'application/problem+json'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _view(version: int, required: bool, approvers: Sequence[int]) -> dict[str, Any]:
    return {'version': version, 'acceptance_required': required, 'approver_ids': sorted(approvers)}


def create_blueprint(
    *, authorize: Authorize, ledger: AcceptanceLedger,
    default_required: Callable[[ClinicDictation], bool],
    is_acceptance_member: Callable[[int], bool],
    record_audit: Callable[[Sequence[Mapping]], None],
) -> Blueprint:
    """``authorize`` decides read and write per object (author, pool lead, admin).

    Before the first version, the workflow flag is used when present and
    ``default_required`` supplies the fallback for dictations without a workflow row.
    ``is_acceptance_member`` is the w014 membership check for each approver.
    """
    bp = Blueprint('clinic_acceptance_policy', __name__)

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException):
        status = 403 if isinstance(error, CSRFError) else error.code
        return _problem(status) if status in _TITLES else error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers['Cache-Control'] = 'no-store'
        return response

    def visible(dictation_id: UUID) -> ClinicDictation:
        row = db.session.get(ClinicDictation, str(dictation_id))
        if row is None or authorize(current_user.id, PERM_READ, row) is not True:
            abort(404)
        return row

    def current(row: ClinicDictation):
        try:
            return ledger.current(SCOPE_DICTATION, row.id)
        except PolicyError:
            return None

    def initial_required(row: ClinicDictation) -> bool:
        item = db.session.get(workflow.ClinicWorkflowDictation, row.id)
        return item.acceptance_required if item is not None else bool(default_required(row))

    @bp.get(PATH)
    def read(dictation_id: UUID):
        row = visible(dictation_id)
        stored = current(row)
        if stored is None:
            return jsonify(_view(0, initial_required(row), ()))
        return jsonify(_view(stored.version, stored.required, stored.approver_user_ids))

    @bp.put(PATH)
    def save(dictation_id: UUID):
        row = visible(dictation_id)
        if authorize(current_user.id, PERM_WRITE, row) is not True:
            audit.record_in_tx('recording.edited', recording_id=row.recording_id,
                               recording_owner_id=row.author_user_id, actor_kind='session',
                               actor_user_id=current_user.id, outcome='denied', force=True,
                               details={'fields': ['acceptance_policy']})
            db.session.commit()
            abort(403)
        match = request.headers.get('If-Match')
        if match is None:
            abort(428)
        if not match.strip('"').isdigit():
            abort(400)
        expected = int(match.strip('"'))
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or body.keys() != {'acceptance_required', 'approver_ids'}
                or type(body['acceptance_required']) is not bool
                or not isinstance(body['approver_ids'], list)):
            abort(400)
        ids = body['approver_ids']
        if any(type(item) is not int for item in ids) or not all(is_acceptance_member(i) for i in ids):
            return _problem(422, 'INVALID_APPROVER')
        required, approvers = body['acceptance_required'], frozenset(ids)
        # Serialize with finalization. SQLite needs an outer transaction before the ledger's savepoint.
        connection = db.session.connection()
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        db.session.refresh(row, with_for_update=True)
        stored = current(row)
        version = stored.version if stored else 0
        if stored and stored.required is required and stored.approver_user_ids == approvers:
            return jsonify(_view(version, required, approvers))
        if expected != version:
            return _problem(409, 'STALE_VERSION', version=version)
        now = datetime.now(timezone.utc)
        try:
            record_audit([{'actor_id': current_user.id, 'dictation_id': row.id,
                           'field': 'acceptance_policy',
                           'old': _view(version, stored.required if stored else initial_required(row),
                                        stored.approver_user_ids if stored else ()),
                           'new': _view(version + 1, required, approvers), 'at': now}])
        except Exception:
            db.session.rollback()
            return _problem(503, 'AUDIT_UNAVAILABLE')
        created = ledger.publish(
            SCOPE_DICTATION, row.id, required=required, approver_user_ids=approvers,
            approver_pool_ids=stored.approver_pool_ids if stored else (),
            deny_user_ids=stored.deny_user_ids if stored else (),
            now=now, expected_version=version)
        if db.session.get(workflow.ClinicWorkflowDictation, row.id) is not None:
            workflow.set_acceptance(db.session(), row.id, created.required)
        db.session.commit()
        return jsonify(_view(created.version, created.required, created.approver_user_ids))

    return bp
