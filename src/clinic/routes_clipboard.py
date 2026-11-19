"""Browser clipboard output bound to output attempts and receipts (PAR-EXP-002/007/008).

The server only prepares the flavours and records what the browser reports.
The Clipboard API call itself runs in the browser: no OS focus, window or key
automation. Preparing commits the attempt as UNKNOWN before the flavours leave
the server; only a browser-reported success completes it and appends a receipt.
Denial marks it failed; an abort leaves it UNKNOWN for reconciliation.
"""

from collections.abc import Callable, MutableMapping
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import revisions
from src.clinic.config import clinic_mode_enabled
from src.clinic.kis import LOOKUP_ABSENT, LOOKUP_CONFIRMED, LOOKUP_UNKNOWN
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.output import CHANNEL_CLIPBOARD, FORMAT_FORMATTED, record_output
from src.clinic.output_attempts import (
    OutputAttempt, OutputAttemptError, OutputSource, begin_output, freeze_output_attempts,
    record_output_result,
)
from src.clinic.rich_text_doc import from_plain_text, to_clipboard_flavors
from src.database import db
from src.models import User

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/clipboard/<uuid:revision_id>'
TARGET = 'browser'
MAX_FLAVOR_BYTES = 1048576
OUTCOMES = {'copied': LOOKUP_CONFIRMED, 'denied': LOOKUP_ABSENT, 'aborted': LOOKUP_UNKNOWN}
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('REVISION_CONFLICT', 'Versionskonflikt'),
    413: ('PAYLOAD_TOO_LARGE', 'Text zu gross für die Zwischenablage'),
}


def _problem(status: int) -> Response:
    code, title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title, status=status, code=code,
                       correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    return response


def _frozen(dictation: ClinicDictation, source: OutputSource, actor: int, request_id: str) -> OutputAttempt:
    attempts = freeze_output_attempts(
        dictation, source, source, actor_user_id=actor, request_id=request_id, allowed=True,
        mode='working_copy', targets={(CHANNEL_CLIPBOARD, TARGET): {'format': FORMAT_FORMATTED}},
    )
    return attempts[0]


def prepare_clipboard(
    ledger: MutableMapping[str, OutputAttempt], dictation: ClinicDictation,
    revision: ClinicTranscriptRevision, source: OutputSource, *, actor: int, request_id: str,
    allowed: bool,
) -> tuple[OutputAttempt, dict[str, str]]:
    """Claim the attempt as UNKNOWN and return it with the plain/HTML flavours.

    `source` is the server-resolved current binding. Only the head revision is
    served; a stale revision raises `OutputAttemptError`.
    """
    head = revisions.current_revision(dictation)
    if head is None or head.id != revision.id or revision.id != source.text_revision_id:
        raise OutputAttemptError('stale output revision')
    attempt = _frozen(dictation, source, actor, request_id)
    known = ledger.get(attempt.idempotency_key)
    if known is not None and known.payload_hash != attempt.payload_hash:
        raise OutputAttemptError('request ID already used for another payload')
    claim = begin_output({**ledger, attempt.idempotency_key: known or attempt}, attempt.idempotency_key,
                         dictation, source, allowed=allowed)
    flavors = to_clipboard_flavors(from_plain_text(revision.body, lambda: str(uuid4())))
    if any(len(value.encode('utf-8')) > MAX_FLAVOR_BYTES for value in flavors.values()):
        raise ValueError('clipboard flavour too large')
    ledger[claim.idempotency_key] = claim
    return claim, flavors


def report_clipboard(
    ledger: MutableMapping[str, OutputAttempt], dictation: ClinicDictation,
    revision: ClinicTranscriptRevision, source: OutputSource, *, actor: int, request_id: str,
    outcome: str,
) -> OutputAttempt:
    """Apply the browser-reported outcome; a receipt is appended only for `copied`."""
    if outcome not in OUTCOMES:
        raise OutputAttemptError('unrecognised clipboard outcome')
    key = _frozen(dictation, source, actor, request_id).idempotency_key
    claim = ledger.get(key)
    if claim is None:
        raise OutputAttemptError('no clipboard attempt for this request')
    result = record_output_result(claim, claim, OUTCOMES[outcome])
    if outcome == 'copied':
        record_output(dictation, revision, actor, CHANNEL_CLIPBOARD, TARGET, text_format=FORMAT_FORMATTED)
    ledger[key] = result
    return result


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool],
    source_for: Callable[[ClinicDictation], OutputSource],
    ledger: MutableMapping[str, OutputAttempt],
) -> Blueprint:
    """Mount with the trusted per-object policy (`clipboard`) and a persistent ledger.

    The actor is always `current_user`; nothing in the request names an actor or role.
    """
    bp = Blueprint('clinic_clipboard', __name__)

    def objects(dictation_id: UUID, revision_id: UUID):
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None or not authorize(current_user, 'clipboard', dictation):
            abort(404)
        revision = db.session.get(ClinicTranscriptRevision, str(revision_id))
        if revision is None or revision.dictation_id != dictation.id:
            abort(404)
        return dictation, revision

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

    @bp.get(PATH)
    def prepare(dictation_id: UUID, revision_id: UUID) -> Response:
        dictation, revision = objects(dictation_id, revision_id)
        key = request_id()
        try:
            claim, flavors = prepare_clipboard(
                ledger, dictation, revision, source_for(dictation), actor=current_user.id,
                request_id=key, allowed=True)
        except OutputAttemptError:
            return _problem(409)
        except ValueError:
            return _problem(413)
        db.session.commit()
        return jsonify({'revision_id': revision.id, **flavors, 'status': claim.status,
                        'generation': claim.generation})

    @bp.post(PATH + '/outcome')
    def outcome(dictation_id: UUID, revision_id: UUID) -> Response:
        dictation, revision = objects(dictation_id, revision_id)
        key = request_id()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.keys() != {'outcome'} or body['outcome'] not in OUTCOMES:
            abort(400)
        try:
            result = report_clipboard(
                ledger, dictation, revision, source_for(dictation), actor=current_user.id,
                request_id=key, outcome=body['outcome'])
        except OutputAttemptError:
            return _problem(409)
        db.session.commit()
        return jsonify(status=result.status, generation=result.generation)

    return bp
