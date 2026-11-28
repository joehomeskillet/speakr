"""Serve dictation-scoped inline graphics; no remote sources or attachments."""

from dataclasses import asdict

from flask import Blueprint, Response, abort, current_app, jsonify, request, session, url_for
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError

from src.clinic.config import clinic_mode_enabled
from src.clinic.rich_text_doc import RichTextError
from src.clinic.richtext_graphics import MAX_BYTES, GraphicStore

PREFIX = '/api/clinic/v1/dictations'
_PNG = 'image/png'


def create_blueprint(store: GraphicStore) -> Blueprint:
    blueprint = Blueprint('clinic_richtext_graphics', __name__, url_prefix=PREFIX)

    @blueprint.before_request
    def authenticate():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            return jsonify(code='unauthorized'), 401
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                return jsonify(code='forbidden'), 403

    @blueprint.after_request
    def protect_response(response: Response) -> Response:
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
        return response

    @blueprint.errorhandler(RichTextError)
    def graphic_error(error: RichTextError):
        if error.code in ('forbidden', 'graphic_not_found', 'invalid_graphic_reference'):
            return jsonify(code='graphic_not_found'), 404
        if error.code == 'graphic_too_large':
            return jsonify(code=error.code), 413
        if request.method == 'POST' and error.code in ('invalid_graphic', 'unsupported_graphic'):
            return jsonify(code=error.code), 415 if error.code == 'unsupported_graphic' else 400
        current_app.logger.error('Clinic graphic store failed: %s', error.code)
        return jsonify(code='graphic_unavailable'), 500

    @blueprint.post('/<dictation_id>/graphics')
    def upload(dictation_id: str):
        if request.mimetype != _PNG:
            return jsonify(code='unsupported_graphic'), 415
        if request.content_length is not None and request.content_length > MAX_BYTES:
            return jsonify(code='graphic_too_large'), 413
        ref = store.put(dictation_id, request.stream.read(MAX_BYTES + 1), actor_user_id=current_user.id)
        response = jsonify(asdict(ref))
        response.headers['Location'] = url_for('.get_graphic', dictation_id=ref.dictation_id,
                                               object_id=ref.object_id)
        return response, 201

    @blueprint.get('/<dictation_id>/graphics/<object_id>')
    def get_graphic(dictation_id: str, object_id: str) -> Response:
        ref, data = store.get(dictation_id, object_id, actor_user_id=current_user.id)
        return Response(data, mimetype=ref.content_type)

    return blueprint
