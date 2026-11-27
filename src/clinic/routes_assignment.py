"""W13 assignment and candidate selection for a sent dictation."""

import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy import update
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import lease, pools, recipient_defaults, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.dictation_roles import display_name
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.parity_dispatch import CommandProblem, RunOnce
from src.clinic.routes_history import dictation_etag
from src.clinic.worklist_entries import LEAD_ROLE
from src.database import db
from src.models import GroupMembership, Recording, User
from src.services import audit

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/assignment'
_SENT_STATES = frozenset({workflow.PREP_OFFLINE, workflow.PREP_ONLINE, workflow.TO_WRITE,
                          workflow.TO_CORRECT, workflow.IN_PROGRESS, workflow.TO_ACCEPT,
                          workflow.ACCEPTED, workflow.DONE})
_HTTP_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'), 401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'), 404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('NOT_ASSIGNABLE', 'Diktat kann in diesem Zustand nicht zugewiesen werden'),
    413: ('PAYLOAD_TOO_LARGE', 'Anfrage zu gross'), 428: ('VERSION_REQUIRED', 'Version erforderlich'),
}
_COMMAND_PROBLEMS = {
    'STALE_VERSION': (409, 'Versionskonflikt'),
    'IDEMPOTENCY_CONFLICT': (409, 'Anfrageschlüssel bereits verwendet'),
    'INELIGIBLE_RECIPIENT': (422, 'Empfänger nicht zulässig'),
    'AUDIT_UNAVAILABLE': (503, 'Auditprotokoll nicht verfügbar'),
}


def _problem(status, code, title):
    response = jsonify(type='about:blank', status=status, code=code, title=title, correlation_id=str(uuid4()))
    response.mimetype = 'application/problem+json'
    return response, status


def _authorized_dictation(dictation_id, *, lock=False):
    row = db.session.get(ClinicDictation, str(dictation_id), populate_existing=lock, with_for_update=lock)
    admin = current_user.is_admin
    if row is None:
        abort(404 if admin else 403)
    actor = current_user.id
    if not admin:
        staff_ids = pools.routable_staff_pool_ids(row.pool_id) if row.pool_id else ()
        if not staff_ids:
            abort(403)
        lead = (db.session.query(GroupMembership.id)
                .join(pools.ClinicStaffPool, pools.ClinicStaffPool.group_id == GroupMembership.group_id)
                .filter(pools.ClinicStaffPool.id.in_(staff_ids), GroupMembership.user_id == actor,
                        GroupMembership.role == LEAD_ROLE).first())
        if lead is None:
            abort(403)
    grants = pools.grants_for(actor, row)
    denied = any('reassign' in grant['actions'] for grant in grants['denies'])
    if denied or (not admin and 'view' not in grants['allowed']):
        abort(403)
    return row


def _sent_workflow(dictation, *, lock=False):
    item = db.session.get(workflow.ClinicWorkflowDictation, dictation.id, with_for_update=lock)
    # Archive and delete can be requested directly from an unsent state.
    if item is None or item.status not in _SENT_STATES:
        abort(409)
    return item


def _eligible(dictation):
    try:
        return recipient_defaults.eligible_user_ids(dictation.pool_id) if dictation.pool_id else ()
    except recipient_defaults.RecipientDefaultError:
        return ()  # Legacy rows may lack a current author pool.


def _end_previous_assignment(item, dictation, actor):
    active = lease.current(item.id) if item.assignee_user_id is not None else None
    now = datetime.now(timezone.utc)
    if active is not None and active.holder_user_id == item.assignee_user_id and active.expires_at > now:
        lease.release(active, active.generation, now, user_id=item.assignee_user_id)
        lease.audit_operation('release', active, actor, dictation, previous_holder=active.holder_user_id)
        if item.status == workflow.IN_PROGRESS:
            workflow.transition(db.session(), item.id, 'close')


def create_blueprint(run_once: RunOnce) -> Blueprint:
    bp = Blueprint('clinic_assignment', __name__)

    @bp.before_request
    def authenticate():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        account = db.session.get(ClinicAccountPolicy, current_user.id)
        if account is None or account.status != 'active':
            abort(403)
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error):
        status = 403 if isinstance(error, CSRFError) else error.code
        return _problem(status, *_HTTP_PROBLEMS[status]) if status in _HTTP_PROBLEMS else error

    @bp.after_request
    def no_store(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get(f'{PATH}/candidates')
    def candidates(dictation_id):
        dictation = _authorized_dictation(dictation_id)
        _sent_workflow(dictation)
        users = User.query.filter(User.id.in_(_eligible(dictation))).order_by(User.id)
        return jsonify([{'id': user.id, 'display_name': display_name(user)} for user in users])

    @bp.post(PATH)
    def assign(dictation_id):
        # Lock before receipt lookup so overlapping requests see the committed replay or version.
        dictation = _authorized_dictation(dictation_id, lock=True)
        try:
            key = str(UUID(request.headers.get('Idempotency-Key', '')))
        except ValueError:
            abort(400)
        match = request.headers.get('If-Match')
        if match is None:
            abort(428)
        match = match.strip()
        if not re.fullmatch(rf'"{dictation.id}:[0-9]+"', match):
            abort(400)
        request.max_content_length = min(4096, request.max_content_length or 4096)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.keys() != {'assignee_user_id'}:
            abort(400)
        recipient = body['assignee_user_id']
        if recipient is not None and (type(recipient) is not int or recipient <= 0):
            abort(400)
        actor = current_user.id
        digest = sha256(json.dumps([match, recipient]).encode()).hexdigest()

        def operation():
            item = _sent_workflow(dictation, lock=True)
            if match != dictation_etag(dictation):
                raise CommandProblem('STALE_VERSION')
            if recipient is not None and recipient not in _eligible(dictation):
                raise CommandProblem('INELIGIBLE_RECIPIENT')
            if recipient != item.assignee_user_id:
                bumped = db.session.execute(update(ClinicDictation).where(
                    ClinicDictation.id == dictation.id, ClinicDictation.version_counter == dictation.version_counter,
                ).values(version_counter=dictation.version_counter + 1))
                if bumped.rowcount != 1:
                    raise CommandProblem('STALE_VERSION')
                _end_previous_assignment(item, dictation, actor)
                item.assignee_user_id = recipient
                item.assigned_at = None if recipient is None else datetime.now(timezone.utc)
                recording = db.session.get(Recording, dictation.recording_id)
                if audit.record_in_tx('recording.edited', recording=recording, actor_kind='session',
                                      actor_user_id=actor, target_user_id=recipient,
                                      details={'fields': ['assignee']}, force=True) is None:
                    raise CommandProblem('AUDIT_UNAVAILABLE')
            return dict(dictation_id=dictation.id, assignee_user_id=recipient,
                        version=dictation.version_counter, etag=dictation_etag(dictation))

        try:
            result = run_once((actor, 'assignment', dictation.id, key), digest, operation)
        except CommandProblem as error:
            code = str(error)
            if code not in _COMMAND_PROBLEMS:
                raise
            status, title = _COMMAND_PROBLEMS[code]
            return _problem(status, code, title)
        return jsonify(result), 200, {'ETag': result['etag']}

    return bp
