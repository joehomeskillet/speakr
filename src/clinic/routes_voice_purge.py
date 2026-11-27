"""Clinic admin preview and confirmation for the database voice purge."""

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import OperationalError
from wtforms.validators import ValidationError

from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.upstream_voice_purge import purge_upstream_voice
from src.database import db


blueprint = Blueprint('clinic_voice_purge', __name__)


def _reserve_purge_writes() -> None:
    """Keep writers out between the service's inventory and its database commit."""
    connection = db.session.connection()
    if connection.dialect.name == 'postgresql':
        connection.exec_driver_sql(
            'LOCK TABLE recording, speaker, speaker_snippet, speaker_voice_sample, transcript_chunk '
            'IN EXCLUSIVE MODE NOWAIT'
        )
    elif connection.dialect.name == 'sqlite':
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
    else:
        abort(503)


@blueprint.errorhandler(CSRFError)
def _csrf_error(_error):
    return jsonify(code='csrf_failed'), 403


@blueprint.post('/api/clinic/v1/admin/voice-purge')
def purge():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active
            and session.get('_user_id') == current_user.get_id()):
        return jsonify(code='unauthenticated'), 401
    if not _is_active_site_admin(db.session, current_user):
        return jsonify(code='forbidden'), 403
    try:
        validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
    except ValidationError:
        return _csrf_error(None)

    body = request.get_json(silent=True) if request.get_data() else {}
    if not isinstance(body, dict) or body.keys() - {'expected_version'}:
        return jsonify(code='invalid_payload'), 422
    dry_run = 'expected_version' not in body
    version = body.get('expected_version')
    if not dry_run:
        if not isinstance(version, str) or not version:
            return jsonify(code='invalid_payload'), 422
        try:
            _reserve_purge_writes()
        except OperationalError:
            db.session.rollback()
            return jsonify(code='purge_unavailable'), 503

    try:
        inventory = purge_upstream_voice(actor_user_id=current_user.id, dry_run=dry_run, expected_version=version)
    except PermissionError:
        return jsonify(code='forbidden'), 403
    except ValueError as error:
        if error.args != ('stale_purge_inventory',):
            raise
        return jsonify(code='stale_purge_inventory'), 409

    response = jsonify(dry_run=dry_run, version=inventory.version, counts=inventory.counts)
    response.headers['Cache-Control'] = 'no-store'
    return response
