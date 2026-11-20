"""Read and update local clinic task models as an active site administrator."""

from flask import Blueprint, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError

from src.clinic import task_models
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.database import db

blueprint = Blueprint('clinic_task_models', __name__)


@blueprint.route('/api/clinic/v1/admin/task-models', methods=['GET', 'PUT'])
def settings():
    if not clinic_mode_enabled():
        return jsonify(error='not_found'), 404
    if not current_user.is_authenticated or session.get('_user_id') != current_user.get_id():
        return jsonify(error='unauthorized'), 401
    if not _is_active_site_admin(db.session, current_user):
        return jsonify(error='forbidden'), 403
    if request.method == 'PUT':
        try:
            validate_csrf(request.headers.get('X-CSRF-Token') or request.headers.get('X-CSRFToken'))
        except ValidationError:
            return jsonify(error='forbidden'), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'revision', 'models'} or not isinstance(body['revision'], str):
            return jsonify(error='invalid_model'), 400
    db.session.rollback()
    catalog = task_models.catalog()
    if not _is_active_site_admin(db.session, current_user):
        return jsonify(error='forbidden'), 403
    try:
        if request.method == 'PUT':
            revision, values = task_models.save(body['revision'], body['models'], catalog['choices'])
        else:
            _, revision, values = task_models.stored()
    except task_models.ModelProblem as exc:
        return jsonify(error=str(exc)), 409 if str(exc) == 'stale_revision' else 400
    return jsonify(task_models.representation(revision, values, catalog))
