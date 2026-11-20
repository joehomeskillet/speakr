"""Browser endpoints for the V8 alignment contract 2.0.

Token document, alignment read, alignment request and operation status for one
dictation. A thin layer over `alignment_tokens`, `alignment` and
`alignment_job`. The actor always comes from the session; every object goes
through the injected per-object `authorize` (`correction_read` for reads,
`correction_edit` for the request; no lease, the job never changes text).
Unknown or denied objects answer 404, so existence is not disclosed.

Nothing here logs or returns transcript text beyond the token document itself.
Mounting and route policy belong to the wiring package.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import alignment as al
from src.clinic import alignment_tokens as at
from src.clinic.alignment_job import AlignmentBaseConflict, AlignmentUnavailable, request_alignment
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import JSON, CommandProblem, RunOnce
from src.database import db
from src.models import User

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>'
SCHEMA_VERSION = '2.0'
MAX_BODY_BYTES = 16 * 1024
REQUEST_KEYS = frozenset({'schema_version', 'audio_revision_id', 'transcript_revision_id', 'document_hash'})
_ID = re.compile(r'^\S{1,160}$')
_HASH = re.compile(r'^[a-f0-9]{64}$')
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Nicht gefunden'),
    409: ('BASE_CONFLICT', 'Grundlage nicht mehr aktuell'),
    413: ('PAYLOAD_TOO_LARGE', 'Anfrage zu gross'),
    503: ('DEPENDENCY_UNAVAILABLE', 'Dienst nicht verfügbar'),
}


def _problem(status: int, *, code: str | None = None, title: str | None = None) -> Response:
    default_code, default_title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title or default_title, status=status,
                       code=code or default_code, correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    return response


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Non-canonical UUID')
    return str(UUID(value))


def _id_arg(value: object) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        abort(400)
    return value


def create_blueprint(*, authorize: Callable[[User, str, ClinicDictation], bool], run_once: RunOnce,
                     queue=None) -> Blueprint:
    """Mount with the trusted session channel and current object policy.

    `run_once` is the parity receipt protocol keyed by (actor, operation, dictation,
    Idempotency-Key). `queue` defaults to the application job queue.
    """
    bp = Blueprint('clinic_alignment', __name__)

    def job_queue():
        if queue is not None:
            return queue
        from src.services.job_queue import job_queue as default_queue
        return default_queue

    def object_for(dictation_id: UUID, action: str) -> ClinicDictation:
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None or not authorize(current_user, action, dictation):
            abort(404)
        return dictation

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException) -> Response | HTTPException:
        if isinstance(error, CSRFError):
            return _problem(403)
        return _problem(error.code) if error.code in _PROBLEMS else error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get(f'{PATH}/transcript-revisions/<uuid:revision_id>/document')
    def get_document(dictation_id: UUID, revision_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'correction_read')
        revision = db.session.get(ClinicTranscriptRevision, str(revision_id))
        if revision is None or revision.dictation_id != dictation.id:
            abort(404)
        try:
            row = at.tokens_for(dictation, revision)
            db.session.commit()  # a legacy revision gets its deterministic row once
        except at.AlignmentTokensError:
            db.session.rollback()
            abort(404)
        return jsonify(schema_version=SCHEMA_VERSION, transcript_revision_id=revision.id,
                       tokenization_version=row.tokenization_version, document_hash=row.document_hash,
                       canonical_document=row.document)

    @bp.get(f'{PATH}/alignment')
    def get_alignment(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'correction_read')
        audio_id = _id_arg(request.args.get('audio_revision_id'))
        transcript_id = _id_arg(request.args.get('transcript_revision_id'))
        found = al.read_for(dictation.id, audio_id, transcript_id)
        if found is None:
            return _problem(404, code='ALIGNMENT_NOT_FOUND', title='Keine Ausrichtung vorhanden')
        row, state = found
        return jsonify(al.to_contract(row, state))

    @bp.post(f'{PATH}/alignment')
    def post_alignment(dictation_id: UUID) -> Response:
        object_for(dictation_id, 'correction_edit')
        try:
            key = _canonical_uuid(request.headers.get('Idempotency-Key', ''))
        except (ValueError, TypeError, AttributeError):
            abort(400)
        if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
            abort(400)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) != REQUEST_KEYS or data['schema_version'] != SCHEMA_VERSION:
            abort(400)
        audio_id = _id_arg(data['audio_revision_id'])
        transcript_id = _id_arg(data['transcript_revision_id'])
        document_hash = data['document_hash']
        if not isinstance(document_hash, str) or not _HASH.match(document_hash):
            abort(400)
        actor, target = current_user.id, str(dictation_id)

        def run() -> JSON:
            dictation = object_for(dictation_id, 'correction_edit')
            row = request_alignment(dictation, actor, audio_revision_id=audio_id,
                                    transcript_revision_id=transcript_id, document_hash=document_hash,
                                    queue=job_queue())
            return al.to_operation(row)

        digest = sha256(repr(sorted((k, data[k]) for k in REQUEST_KEYS)).encode()).hexdigest()
        try:
            result = run_once((actor, 'alignment:request', target, key), digest, run)
        except AlignmentBaseConflict:
            return _problem(409, code='BASE_CONFLICT')
        except AlignmentUnavailable:
            return _problem(503)
        except CommandProblem as error:
            if str(error) == 'IDEMPOTENCY_CONFLICT':
                return _problem(409, code='IDEMPOTENCY_CONFLICT', title='Anfrageschlüssel bereits verwendet')
            if str(error) == 'DEPENDENCY_UNAVAILABLE':
                return _problem(503)
            raise
        response = jsonify(result)
        response.status_code = 202 if result.get('state') == 'pending' else 200
        return response

    @bp.get(f'{PATH}/operations/<uuid:operation_id>')
    def get_operation(dictation_id: UUID, operation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'correction_read')
        row = db.session.get(al.ClinicAlignmentRevision, str(operation_id))
        if row is None or row.dictation_id != dictation.id:
            abort(404)
        return jsonify(al.to_operation(row))

    return bp
