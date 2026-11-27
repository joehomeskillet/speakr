"""Browser clipboard output bound to output attempts and receipts (PAR-EXP-002/007/008).

The server only prepares the flavours and records what the browser reports.
The Clipboard API call itself runs in the browser: no OS focus, window or key
automation. Preparing commits the attempt as UNKNOWN before the flavours leave
the server; only a browser-reported success completes it and appends a receipt.
Denial marks it failed; an abort leaves it UNKNOWN for reconciliation.
"""

import json
from collections.abc import Callable, MutableMapping
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import config, release, revisions, richtext
from src.clinic.clipboard_ledger import AuditClipboardLedger
from src.clinic.kis import LOOKUP_ABSENT, LOOKUP_CONFIRMED, LOOKUP_UNKNOWN
from src.clinic.models import ClinicDictation, ClinicRelease, ClinicTranscriptRevision
from src.clinic.output import CHANNEL_CLIPBOARD, FORMAT_FORMATTED, FORMAT_PLAIN, record_output
from src.clinic.output_attempts import (
    OutputAttempt, OutputAttemptError, OutputSource, begin_output, freeze_output_attempts,
    output_attempt_key, record_output_result,
)
from src.clinic.rich_text_doc import RichTextError, from_plain_text, to_clipboard_flavors, to_plain_text
from src.database import db
from src.models import User

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/clipboard/<uuid:revision_id>'
TARGET = 'browser'
MAX_FLAVOR_BYTES = 1048576
OUTCOMES = {'copied': LOOKUP_CONFIRMED, 'denied': LOOKUP_ABSENT, 'aborted': LOOKUP_UNKNOWN}
FORMATS = {'formatted': FORMAT_FORMATTED, 'plain': FORMAT_PLAIN}
WORKING_COPY = 'working_copy'
WORKING_COPY_LABEL = 'Arbeitskopie'
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


def client_can_copy() -> bool:
    return config.client_capabilities()['profiles'][config.client_profile()].get('document_export') is not False


def clipboard_binding(
    revision: ClinicTranscriptRevision, source: OutputSource, active: ClinicRelease | None,
) -> str:
    """Return the release ID or the working-copy marker for a server-resolved source."""
    if revision.id != source.text_revision_id or revision.dictation_id != source.dictation_id:
        raise OutputAttemptError('stale output revision')
    if active is not None:
        if not source.context_complete or (
            active.audio_revision_id, active.transcript_revision_id, active.context_revision_id, active.text_hash,
        ) != (source.audio_revision_id, revision.id, source.context_revision_id, source.text_hash):
            raise OutputAttemptError('stale release binding')
        return active.id
    if revision.kind != revisions.KIND_HUMAN:
        raise OutputAttemptError('working copy requires a human correction')
    return WORKING_COPY


def _released_document(active: ClinicRelease):
    # Legacy releases bind text only. Never infer their formatting from later snapshots.
    try:
        evidence = json.loads(active.authorization_decision)
    except json.JSONDecodeError as error:
        raise OutputAttemptError('invalid released document evidence') from error
    if not isinstance(evidence, dict):
        raise OutputAttemptError('invalid released document evidence')
    version = evidence.get(release.CLIPBOARD_DOCUMENT_VERSION, 0)
    if type(version) is not int or version < 0:
        raise OutputAttemptError('invalid released document version')
    if version == 0:
        return 0, None
    document = richtext.load_document(active.transcript_revision_id, version)
    if document is None or revisions.text_hash(to_plain_text(document)) != active.text_hash:
        raise OutputAttemptError('released document is unavailable or no longer matches')
    return version, document


def prepare_clipboard(
    ledger: MutableMapping[str, OutputAttempt], dictation: ClinicDictation,
    revision: ClinicTranscriptRevision, source: OutputSource, *, actor: int, request_id: str,
    allowed: bool, text_format: str = FORMAT_FORMATTED, expected_binding: str | None = None,
) -> tuple[OutputAttempt, dict[str, str]]:
    """Claim the attempt as UNKNOWN and return it with the requested flavours.

    `source` is the server-resolved current binding. The active release wins;
    otherwise only a corrected head is eligible as a working copy.
    """
    head = revisions.current_revision(dictation)
    if head is None or head.id != revision.id or revision.id != source.text_revision_id:
        raise OutputAttemptError('stale output revision')
    if allowed is not True:
        raise OutputAttemptError('output not authorised')
    if text_format not in FORMATS.values():
        raise OutputAttemptError('unrecognised clipboard format')
    active = release.active_release(dictation)
    binding = clipboard_binding(revision, source, active)
    if expected_binding is not None and expected_binding != binding:
        raise OutputAttemptError('stale clipboard binding')
    version, doc = (_released_document(active) if active is not None
                    else richtext.load_latest_document(revision.id))
    settings = {'format': text_format}
    if version or active is not None:
        settings['document_version'] = str(version)
    attempt, = freeze_output_attempts(
        dictation, source, source, actor_user_id=actor, request_id=request_id, allowed=allowed,
        mode='accepted' if active is not None else WORKING_COPY, release_id=active.id if active else None,
        targets={(CHANNEL_CLIPBOARD, TARGET): settings},
    )
    known = ledger.get(attempt.idempotency_key)
    if known is not None and known.payload_hash != attempt.payload_hash:
        raise OutputAttemptError('request ID already used for another payload')
    attempts = ledger.attempts_for(dictation.id) if isinstance(ledger, AuditClipboardLedger) else ledger
    claim = begin_output({**attempts, attempt.idempotency_key: known or attempt}, attempt.idempotency_key,
                         dictation, source, allowed=allowed)
    if text_format == FORMAT_PLAIN:
        flavors = {'text/plain': revision.body if doc is None or active is not None else to_plain_text(doc)}
    else:
        # Legacy text revisions have no saved formatting.
        document = doc if doc is not None else from_plain_text(revision.body, lambda: str(uuid4()))
        flavors = to_clipboard_flavors(document)
        if active is not None:
            flavors['text/plain'] = revision.body
    if any(len(value.encode('utf-8')) > MAX_FLAVOR_BYTES for value in flavors.values()):
        raise ValueError('clipboard flavour too large')
    ledger[claim.idempotency_key] = claim
    return claim, flavors


def report_clipboard(
    ledger: MutableMapping[str, OutputAttempt], dictation: ClinicDictation,
    revision: ClinicTranscriptRevision, *, actor: int, request_id: str, outcome: str,
) -> OutputAttempt:
    """Apply the browser-reported outcome; a receipt is appended only for `copied`."""
    if outcome not in OUTCOMES:
        raise OutputAttemptError('unrecognised clipboard outcome')
    # The key scopes the actor and request. The stored claim supplies format and revision evidence.
    key = output_attempt_key(actor, request_id, dictation.id, CHANNEL_CLIPBOARD, TARGET)
    claim = ledger.get(key)
    if claim is None:
        raise OutputAttemptError('no clipboard attempt for this request')
    if (claim.source.dictation_id != dictation.id or claim.source.text_revision_id != revision.id
            or claim.source.text_hash != revision.normalised_text_hash):
        raise OutputAttemptError('clipboard outcome names a different revision')
    result = record_output_result(claim, claim, OUTCOMES[outcome])
    if outcome == 'copied':
        record_output(dictation, revision, actor, CHANNEL_CLIPBOARD, TARGET,
                      text_format=dict(claim.format_settings)['format'])
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
        if not config.clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)
        if not client_can_copy():
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
        text_format = FORMATS.get(request.args.get('format', 'formatted'))
        if text_format is None:
            abort(400)
        try:
            claim, flavors = prepare_clipboard(
                ledger, dictation, revision, source_for(dictation), actor=current_user.id,
                request_id=key, allowed=True, text_format=text_format,
                expected_binding=request.args.get('binding'))
        except (OutputAttemptError, RichTextError):
            return _problem(409)
        except ValueError:
            return _problem(413)
        db.session.commit()
        response = jsonify({'revision_id': revision.id, **flavors, 'status': claim.status,
                            'generation': claim.generation})
        if claim.mode == WORKING_COPY:
            response.headers['X-Clipboard-Label'] = WORKING_COPY_LABEL
        return response

    @bp.post(PATH + '/outcome')
    def outcome(dictation_id: UUID, revision_id: UUID) -> Response:
        dictation, revision = objects(dictation_id, revision_id)
        key = request_id()
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or body.keys() != {'outcome'}
                or not isinstance(body['outcome'], str) or body['outcome'] not in OUTCOMES):
            abort(400)
        try:
            result = report_clipboard(
                ledger, dictation, revision, actor=current_user.id,
                request_id=key, outcome=body['outcome'])
        except OutputAttemptError:
            return _problem(409)
        db.session.commit()
        return jsonify(status=result.status, generation=result.generation)

    return bp
