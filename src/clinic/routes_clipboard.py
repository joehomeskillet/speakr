"""Browser clipboard output bound to output attempts and receipts (PAR-EXP-002/007/008).

The server only prepares the flavours and records what the browser reports.
The Clipboard API call itself runs in the browser: no OS focus, window or key
automation. Preparing commits the attempt as UNKNOWN before the flavours leave
the server; only a browser-reported success completes it and appends a receipt.
Denial marks it failed; an abort leaves it UNKNOWN for reconciliation.
"""

from collections.abc import Callable, MutableMapping
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import config, release, revisions, richtext, richtext_formats, startup
from src.clinic.clipboard_ledger import AuditClipboardLedger
from src.clinic.kis import LOOKUP_ABSENT, LOOKUP_CONFIRMED, LOOKUP_UNKNOWN
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.output import CHANNEL_CLIPBOARD, FORMAT_FORMATTED, FORMAT_PLAIN, record_output
from src.clinic.output_attempts import (
    FAILED, SUCCEEDED, UNKNOWN, OutputAttempt, OutputAttemptError, OutputSource,
    begin_output, freeze_output_attempts,
    output_attempt_key, record_output_result,
)
from src.clinic.rich_text_doc import RichTextError, from_plain_text, to_clipboard_flavors, to_plain_text
from src.clinic.richtext_graphics import GraphicStore
from src.database import db
from src.models import User

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/clipboard/<uuid:revision_id>'
TARGET = 'browser'
MAX_FLAVOR_BYTES = 1048576
OUTCOMES = {'copied': LOOKUP_CONFIRMED, 'denied': LOOKUP_ABSENT, 'aborted': LOOKUP_UNKNOWN}
FORMATS = {'formatted': FORMAT_FORMATTED, 'plain': FORMAT_PLAIN}
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


def _frozen(
    dictation: ClinicDictation, source: OutputSource, actor: int, request_id: str,
    text_format: str, document_version: int, release_id: str | None, live: bool,
) -> OutputAttempt:
    settings = {'format': text_format}
    if live:
        settings['live'] = '1'
    if document_version:
        settings['document_version'] = str(document_version)
    attempts = freeze_output_attempts(
        dictation, source, source, actor_user_id=actor, request_id=request_id, allowed=True,
        mode='accepted' if release_id else 'working_copy', release_id=release_id,
        targets={(CHANNEL_CLIPBOARD, TARGET): settings},
    )
    return attempts[0]


def _lock_live_dictation(ledger, dictation_id: str) -> None:
    if isinstance(ledger, AuditClipboardLedger):
        # Match the ledger's row lock and acquire it before reading the release or claim.
        (db.session.query(ClinicDictation.id).filter_by(id=dictation_id)
         .with_for_update(key_share=True).one())


def prepare_clipboard(
    ledger: MutableMapping[str, OutputAttempt], dictation: ClinicDictation,
    revision: ClinicTranscriptRevision, source: OutputSource, *, actor: int, request_id: str,
    allowed: bool, text_format: str = FORMAT_FORMATTED, release_id: str | None = None,
    live: bool = False,
) -> tuple[OutputAttempt, dict[str, str]]:
    """Claim the attempt as UNKNOWN and return it with the requested flavours.

    `source` is the server-resolved current binding. Only the head revision is
    served; a stale revision raises `OutputAttemptError`.
    """
    head = revisions.current_revision(dictation)
    if head is None or head.id != revision.id or revision.id != source.text_revision_id:
        raise OutputAttemptError('stale output revision')
    if allowed is not True:
        raise OutputAttemptError('output not authorised')
    if text_format not in FORMATS.values():
        raise OutputAttemptError('unrecognised clipboard format')
    if live and release_id is None:
        raise OutputAttemptError('live clipboard requires a release')
    if live:
        active = release.active_release(dictation)
        if active is None or active.id != release_id or active.transcript_revision_id != revision.id:
            raise OutputAttemptError('output mode requires a matching release binding')
        version, doc = release.load_released_document(active)
    elif release_id is None:
        version, doc = richtext.load_latest_document(revision.id)
    elif text_format == FORMAT_PLAIN:
        version, doc = 0, None
    else:
        raise OutputAttemptError('released clipboard requires plain text')
    attempt = _frozen(dictation, source, actor, request_id, text_format, version, release_id, live)
    known = ledger.get(attempt.idempotency_key)
    if known is not None and known.payload_hash != attempt.payload_hash:
        raise OutputAttemptError('request ID already used for another payload')
    replay = live and known is not None and known.status == UNKNOWN
    if replay:
        claim = known
    else:
        attempts = ledger.attempts_for(dictation.id) if isinstance(ledger, AuditClipboardLedger) else ledger
        claim = begin_output({**attempts, attempt.idempotency_key: known or attempt},
                             attempt.idempotency_key, dictation, source, allowed=allowed)
    if text_format == FORMAT_PLAIN:
        flavors = {'text/plain': revision.body if doc is None else to_plain_text(doc)}
    elif live and doc is not None:
        store = GraphicStore(startup.originals_root(current_app))
        graphics = {}
        graphic_bytes = 0
        for node in doc.nodes:
            if node.kind == 'graphic' and node.object_id not in graphics:
                data = store.get(dictation.id, node.object_id, actor_user_id=actor)[1]
                graphic_bytes += len(data)
                if graphic_bytes > MAX_FLAVOR_BYTES:
                    raise ValueError('clipboard graphics too large')
                graphics[node.object_id] = data
        flavors = {'text/plain': to_plain_text(doc), 'text/html': richtext_formats.html_with_graphics(doc, graphics)}
    else:
        # Legacy text revisions have no saved formatting.
        document = doc if doc is not None else from_plain_text(revision.body, lambda: str(uuid4()))
        flavors = to_clipboard_flavors(document)
    if any(len(value.encode('utf-8')) > MAX_FLAVOR_BYTES for value in flavors.values()):
        raise ValueError('clipboard flavour too large')
    if not replay:
        ledger[claim.idempotency_key] = claim
    return claim, flavors


def report_clipboard(
    ledger: MutableMapping[str, OutputAttempt], dictation: ClinicDictation,
    revision: ClinicTranscriptRevision, *, actor: int, request_id: str,
    outcome: str, release_id: str | None = None, live: bool = False, generation: int | None = None,
) -> OutputAttempt:
    """Apply the browser-reported outcome; a receipt is appended only for `copied`."""
    if outcome not in OUTCOMES:
        raise OutputAttemptError('unrecognised clipboard outcome')
    if live:
        _lock_live_dictation(ledger, dictation.id)
    # The key scopes the actor and request. The stored claim supplies format and revision evidence.
    key = output_attempt_key(actor, request_id, dictation.id, CHANNEL_CLIPBOARD, TARGET)
    claim = ledger.get(key)
    if claim is None:
        raise OutputAttemptError('no clipboard attempt for this request')
    if claim.source.text_revision_id != revision.id or claim.release_id != release_id:
        raise OutputAttemptError('clipboard outcome names a different revision or release')
    settings = dict(claim.format_settings)
    if live != (settings.get('live') == '1'):
        raise OutputAttemptError('clipboard outcome names a different contract')
    if live:
        if type(generation) is not int or generation != claim.generation:
            raise OutputAttemptError('clipboard outcome names a different generation')
        if (outcome, claim.status) in (('copied', SUCCEEDED), ('denied', FAILED)):
            return claim
    result = record_output_result(claim, claim, OUTCOMES[outcome])
    if outcome == 'copied':
        record_output(dictation, revision, actor, CHANNEL_CLIPBOARD, TARGET,
                      text_format=settings['format'])
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
        if config.client_capabilities()['profiles'][config.client_profile()].get('document_export') is False:
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

    def requested_release() -> str | None:
        value = request.args.get('release_id')
        if value is not None:
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except ValueError:
                abort(400)
        return value

    @bp.get(PATH)
    def prepare(dictation_id: UUID, revision_id: UUID) -> Response:
        dictation, revision = objects(dictation_id, revision_id)
        key = request_id()
        text_format = FORMATS.get(request.args.get('format', 'formatted'))
        if text_format is None:
            abort(400)
        live = request.args.get('live') == '1'
        try:
            if live:
                _lock_live_dictation(ledger, dictation.id)
            claim, flavors = prepare_clipboard(
                ledger, dictation, revision, source_for(dictation), actor=current_user.id,
                request_id=key, allowed=True, text_format=text_format, release_id=requested_release(), live=live)
            if claim.release_id:
                active = release.active_release(dictation)
                if active is None or active.id != claim.release_id:
                    db.session.rollback()
                    return _problem(409)
        except (OutputAttemptError, release.ReleaseError, RichTextError, richtext_formats.FormatError, OSError):
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
        live = request.args.get('live') == '1'
        body = request.get_json(silent=True)
        fields = {'outcome', 'generation'} if live else {'outcome'}
        if (not isinstance(body, dict) or body.keys() != fields
                or not isinstance(body['outcome'], str) or body['outcome'] not in OUTCOMES
                or (live and (type(body['generation']) is not int or body['generation'] < 1))):
            abort(400)
        try:
            result = report_clipboard(
                ledger, dictation, revision, actor=current_user.id,
                request_id=key, outcome=body['outcome'], release_id=requested_release(),
                live=live, generation=body.get('generation'))
        except OutputAttemptError:
            return _problem(409)
        # A historical browser copy can be recorded and free the ledger without authorising a follow-up.
        status = {'status': result.status, 'generation': result.generation}
        if live and result.status == SUCCEEDED:
            active = release.active_release(dictation)
            if active is None or active.id != result.release_id:
                status['current'] = False
        db.session.commit()
        return jsonify(status)

    return bp
