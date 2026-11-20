"""Bind a synthetic Fall-ID through the staging KIS adapter."""

import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.database import db
from src.services import audit

from . import lease, live_session, release
from .config import client_profile, clinic_mode_enabled
from .context_grants import ContextGrantError, VerifiedContextGrant, bind_context_grant
from .finalize_gates import trusted_context
from .guard import assert_dictation_edit_allowed
from .kis import KisAdapter, MockKisAdapter, _profile
from .models import ClinicContextRevision, ClinicDictation
from .routes_workflow import PERM_METADATA, PERM_VIEW, Authorize

_PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/context'
_CASE = re.compile(r'SYN-[0-9]{4,8}')
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Eine synthetische Fall-ID im Format SYN-0001 ist erforderlich'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('REVISION_CONFLICT', 'Fall-ID konnte nicht verknüpft werden'),
    413: ('PAYLOAD_TOO_LARGE', 'Anfrage zu gross'),
    422: ('CASE_UNRESOLVED', 'Fall-ID konnte nicht verifiziert werden'),
    423: ('LOCKED', 'Eine aktive Bearbeitungssperre ist erforderlich'),
    503: ('CAPABILITY_UNAVAILABLE', 'Fall-ID-Simulation nicht verfügbar'),
}
_GRANT_STATUS = {
    'grant_replayed': 409, 'stale_version': 409, 'context_history_conflict': 409,
    'binding_denied': 403, 'case_unresolved': 422, 'context_capability_unavailable': 503,
}


def _problem(status: int, code: str | None = None) -> Response:
    default, title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title, status=status, code=code or default,
                       correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    return response


def _case_display(dictation: ClinicDictation, adapter: KisAdapter) -> str | None:
    heads = release._heads(ClinicContextRevision, dictation.id)
    if len(heads) != 1:
        return None
    revision = db.session.get(ClinicContextRevision, next(iter(heads)))
    if revision and _CASE.fullmatch(revision.case_ref or '') and trusted_context(revision, adapter):
        return revision.case_ref
    return None


def create_blueprint(*, authorize: Authorize, adapter_for: Callable[[], KisAdapter]) -> Blueprint:
    bp = Blueprint('clinic_context', __name__)

    def staging_adapter() -> MockKisAdapter:
        if _profile() != 'staging':
            abort(503)
        adapter = adapter_for()
        if not isinstance(adapter, MockKisAdapter) or not adapter.enabled:
            abort(503)
        return adapter

    def visible_dictation(dictation_id: UUID, *, lock: bool = False) -> ClinicDictation:
        row = db.session.get(ClinicDictation, str(dictation_id), with_for_update=lock)
        if row is None or not authorize(current_user.id, PERM_VIEW, row):
            abort(404)
        return row

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if client_profile() not in ('managed_web_online', 'managed_web_offline'):
            abort(403)
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException) -> Response | HTTPException:
        return _problem(error.code) if error.code in _PROBLEMS else error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get(_PATH)
    def get_context(dictation_id: UUID) -> Response:
        # P03 uses this status to decide whether to offer the simulation.
        adapter = staging_adapter()
        row = visible_dictation(dictation_id)
        case_display = _case_display(row, adapter)
        audit.record('recording.content.read', recording_id=row.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'context'})
        return jsonify(case_display=case_display)

    @bp.post(_PATH)
    def post_context(dictation_id: UUID) -> Response:
        adapter = staging_adapter()
        request.max_content_length = min(1024, request.max_content_length or 1024)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.keys() != {'case_reference'}:
            abort(400)
        case = body['case_reference']
        if not isinstance(case, str) or not _CASE.fullmatch(case):
            abort(400)

        connection = db.session.connection()
        # SQLite has no SELECT FOR UPDATE. Reserve the writer before checking the lease.
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        row = visible_dictation(dictation_id, lock=True)
        actor = current_user.id
        now = datetime.now(timezone.utc)

        def authorize_binding(target: ClinicDictation, user_id: int) -> bool:
            if not (authorize(user_id, PERM_VIEW, target) and authorize(user_id, PERM_METADATA, target)):
                return False
            held = lease.current(target.id)
            assert_dictation_edit_allowed(target.id, user_id, held.generation if held else 0, now)
            if _case_display(target, adapter) == case:
                abort(409)
            return True

        evidence = VerifiedContextGrant(
            issuer=adapter.name, audience='speakr', user_id=actor, dictation_id=row.id,
            source_system=adapter.name, subject_reference=f'speakr-user-{actor}', tenant_reference='staging',
            case_reference=case, patient_reference=uuid4().hex, purpose='bind_existing',
            nonce=token_urlsafe(32), expires_at=now + timedelta(seconds=60),
        )
        try:
            revision = bind_context_grant(
                evidence.nonce, dictation_id=row.id, actor_user_id=actor, tenant=evidence.tenant_reference,
                expected_version=row.version_counter, issuer=evidence.issuer, audience=evidence.audience, now=now,
                resolve=lambda token, uid: evidence if (token, uid) == (evidence.nonce, actor) else None,
                adapter=adapter, authorize_binding=authorize_binding,
            )
            case_display = revision.case_ref
            db.session.commit()
        except (lease.LeaseConflict, live_session.LiveSessionError):
            db.session.rollback()
            return _problem(423)
        except ContextGrantError as error:
            db.session.rollback()
            code = str(error)
            if code not in _GRANT_STATUS:
                raise
            return _problem(_GRANT_STATUS[code], 'GRANT_REPLAYED' if code == 'grant_replayed' else None)
        except SQLAlchemyError as error:
            db.session.rollback()
            # SQL errors can contain the opaque reference in their bound parameters.
            current_app.logger.error('Fall-ID context transaction failed (%s)', type(error).__name__)
            return _problem(503, 'DEPENDENCY_UNAVAILABLE')
        return jsonify(case_display=case_display), 201

    return bp
