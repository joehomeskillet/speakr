"""Output routes: targets, export requests and export status (P16 backend reachability).

Thin HTTP layer over `export` (DOCX/text render), `kis` (delivery ledger) and
`release` (the active release). Nothing here renders, delivers or decides
policy itself:

* Only the active release of a dictation can be exported. The request names the
  transcript revision it expects; a different or unreleased revision is a 409.
* File exports are rendered from the revision the release binds. The job keeps
  the release and revision ids and the content hash. Jobs are process-local
  until a durable store exists; the same Idempotency-Key from the same actor
  returns the same job and never renders a second one.
* KIS delivery goes through `kis.deliver`, whose delivery ledger is the
  idempotency record. A disabled adapter answers 503 `KIS_UNAVAILABLE`; no
  success is ever reported without an adapter answer.
* The actor is always `current_user`. Permission is the injected per-object
  policy, called with the action name `export`; an unknown action is a deny.
"""

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from flask import Blueprint, Flask, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import export as export_service
from src.clinic import kis, release
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDelivery, ClinicDictation, ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.output_attempts import OutputAttemptError, OutputSource, bind_output_source
from src.database import db
from src.models import User

BASE = '/api/clinic/v1/dictations/<uuid:dictation_id>'
FILE_FORMATS = {'txt': export_service.render_text, 'docx': export_service.render_docx}
KIS_FORMAT = 'kis'
FORMATS = (*FILE_FORMATS, KIS_FORMAT)
ACTION = 'export'
_DELIVERY_STATUS = {'ACKNOWLEDGED': 'delivered', 'FAILED': 'rejected', 'UNKNOWN': 'unknown', 'PENDING': 'pending'}
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('EXPORT_CONFLICT', 'Export nicht möglich'),
    503: ('KIS_UNAVAILABLE', 'KIS-Anbindung nicht verfügbar'),
}


def _problem(status: int, code: str | None = None, **extra) -> Response:
    default_code, title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title, status=status, code=code or default_code,
                       correlation_id=str(uuid4()), **extra)
    response.status_code = status
    response.mimetype = 'application/problem+json'
    return response


def configured_adapter(app: Flask) -> kis.KisAdapter:
    """Same selection as finalisation: configured adapter, MockKIS on staging, else disabled."""
    import os

    adapter = app.extensions.get('clinic_kis_adapter')
    if adapter is None:
        staging = os.environ.get('CLINIC_PROFILE', '').strip().lower() == 'staging'
        adapter = app.extensions.setdefault(
            'clinic_kis_adapter', kis.MockKisAdapter() if staging else kis.DisabledAdapter())
    return adapter


def _leaf(model, dictation: ClinicDictation):
    heads = release._heads(model, dictation.id)
    return db.session.get(model, next(iter(heads))) if len(heads) == 1 else None


def output_source(dictation: ClinicDictation) -> OutputSource:
    """Server-resolved current binding of a dictation; fails closed without a single head."""
    rows = (_leaf(ClinicAudioRevision, dictation), _leaf(ClinicTranscriptRevision, dictation),
            _leaf(ClinicContextRevision, dictation))
    if any(row is None for row in rows):
        raise OutputAttemptError('dictation has no single current revision')
    return bind_output_source(dictation, *rows)


def _job_view(job: dict) -> dict:
    return {key: value for key, value in job.items() if key not in ('actor_id', 'request')}


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool],
    adapter_for: Callable[[], kis.KisAdapter],
    jobs: dict | None = None,
) -> Blueprint:
    """Mount with the trusted per-object policy and the configured KIS adapter."""
    bp = Blueprint('clinic_output', __name__)
    ledger: dict = {} if jobs is None else jobs
    lock = threading.Lock()

    def dictation_of(dictation_id: UUID) -> ClinicDictation:
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None or not authorize(current_user, ACTION, dictation):
            abort(404)
        return dictation

    def request_id() -> str:
        value = request.headers.get('Idempotency-Key', '')
        try:
            if str(UUID(value)) != value.lower():
                raise ValueError
        except ValueError:
            abort(400)
        return value.lower()

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

    @bp.get(BASE + '/output-targets')
    def targets(dictation_id: UUID) -> Response:
        dictation = dictation_of(dictation_id)
        active = release.active_release(dictation)
        adapter = adapter_for()
        gate = None if active is not None else 'not_released'
        listed = [{'id': fmt, 'channel': 'file', 'available': gate is None, 'reason': gate}
                  for fmt in FILE_FORMATS]
        kis_reason = gate or (None if adapter.enabled else 'kis_unavailable')
        listed.append({'id': KIS_FORMAT, 'channel': 'kis', 'available': kis_reason is None,
                       'reason': kis_reason})
        listed.append({'id': 'clipboard', 'channel': 'clipboard', 'available': True, 'reason': None})
        return jsonify(dictation_id=dictation.id,
                       released_revision_id=None if active is None else active.transcript_revision_id,
                       targets=listed)

    @bp.post(BASE + '/exports')
    def request_export(dictation_id: UUID) -> Response:
        dictation = dictation_of(dictation_id)
        key = request_id()
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or body.keys() != {'revision_id', 'format'}
                or body['format'] not in FORMATS or not isinstance(body['revision_id'], str)):
            abort(400)
        revision = db.session.get(ClinicTranscriptRevision, body['revision_id'])
        if revision is None or revision.dictation_id != dictation.id:
            abort(404)
        active = release.active_release(dictation)
        if active is None or active.transcript_revision_id != revision.id:
            return _problem(409, 'NOT_RELEASED', detail='Nur die freigegebene Version kann ausgegeben werden')
        if body['format'] == KIS_FORMAT:
            return _kis_export(dictation, active, key)
        return _file_export(dictation, active, body['format'], key)

    def _file_export(dictation: ClinicDictation, active: ClinicRelease, fmt: str, key: str) -> Response:
        ledger_key = (current_user.id, dictation.id, key)
        with lock:
            known = ledger.get(ledger_key)
            if known is not None:
                if (known['release_id'], known['format']) != (active.id, fmt):
                    return _problem(409, 'IDEMPOTENCY_CONFLICT')
                return jsonify(_job_view(known) | {'replayed': True})
            try:
                rendered = FILE_FORMATS[fmt](active)
            except export_service.ExportRefused as refused:
                return _problem(409, 'EXPORT_REFUSED', reason=refused.reason)
            job = {
                'id': str(uuid4()), 'kind': 'file', 'status': 'ready', 'format': fmt,
                'dictation_id': dictation.id, 'release_id': active.id,
                'revision_id': rendered.transcript_revision_id, 'filename': rendered.filename,
                'mimetype': rendered.mimetype, 'sha256': rendered.sha256, 'actor_id': current_user.id,
            }
            ledger[ledger_key] = job
            ledger[('id', job['id'])] = job
        response = jsonify(_job_view(job) | {'replayed': False})
        response.status_code = 201
        return response

    def _kis_export(dictation: ClinicDictation, active: ClinicRelease, key: str) -> Response:
        adapter = adapter_for()
        if not adapter.enabled:
            return _problem(503, export_status='unavailable', detail='Keine KIS-Anbindung konfiguriert')
        context = db.session.get(ClinicContextRevision, active.context_revision_id)
        result = kis.deliver(active, context, adapter, f'export:{dictation.id}:{key}',
                             datetime.now(timezone.utc))
        if result.outcome == kis.REFUSED:
            return _problem(409, 'KIS_REFUSED', reason=result.reason)
        response = jsonify({
            'id': result.delivery_id, 'kind': 'kis', 'status': _status_of(result.outcome),
            'format': KIS_FORMAT, 'dictation_id': dictation.id, 'release_id': active.id,
            'revision_id': active.transcript_revision_id, 'replayed': result.replayed,
        })
        response.status_code = 200 if result.replayed else 201
        return response

    def _status_of(outcome: str) -> str:
        return {kis.DELIVERED: 'delivered', kis.REJECTED: 'rejected'}.get(outcome, 'unknown')

    @bp.get(BASE + '/exports/<uuid:job_id>')
    def export_status(dictation_id: UUID, job_id: UUID) -> Response:
        dictation = dictation_of(dictation_id)
        job = ledger.get(('id', str(job_id)))
        if job is not None:
            if job['dictation_id'] != dictation.id or job['actor_id'] != current_user.id:
                abort(404)
            return jsonify(_job_view(job))
        row = db.session.get(ClinicDelivery, str(job_id))
        owner = None if row is None else db.session.get(ClinicRelease, row.release_id)
        if owner is None or owner.dictation_id != dictation.id:
            abort(404)
        return jsonify(id=row.id, kind='kis', status=_DELIVERY_STATUS.get(row.state, 'unknown'),
                       format=KIS_FORMAT, dictation_id=dictation.id, release_id=owner.id,
                       revision_id=owner.transcript_revision_id)

    return bp
