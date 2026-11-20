"""Browser endpoints for one dictation's history and review lease."""

from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import lease, live_session, revisions
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicArchiveEvent, ClinicDictation, ClinicRelease
from src.clinic.parity_dispatch import JSON, CommandProblem, RunOnce
from src.clinic.release import ClinicReleaseSupersession, ClinicWorkStateTransfer
from src.database import db
from src.models import Recording, User
from src.services import audit

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>'
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('REVISION_CONFLICT', 'Versionskonflikt'),
    413: ('PAYLOAD_TOO_LARGE', 'Anfrage zu gross'),
    415: ('UNSUPPORTED_MEDIA_TYPE', 'Nicht unterstütztes Format'),
    423: ('LOCKED', 'Diktat ist gesperrt'),
    503: ('DEPENDENCY_UNAVAILABLE', 'Dienst nicht verfügbar'),
}


def _problem(status: int, *, conflict: bool = False) -> Response:
    code, title = _PROBLEMS[status]
    if conflict:
        code, title = 'IDEMPOTENCY_CONFLICT', 'Anfrageschlüssel bereits verwendet'
    response = jsonify(type='about:blank', title=title, status=status, code=code,
                       correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _uuid(value: str) -> str:
    canonical = str(UUID(value))
    if canonical != value.lower():
        raise ValueError('Non-canonical UUID')
    return canonical


def _utc(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def dictation_etag(dictation: ClinicDictation) -> str:
    """Version token shared with the dictation snapshot and conditional saves."""
    return f'"{dictation.id}:{dictation.version_counter}"'


def _history(dictation: ClinicDictation) -> list[JSON]:
    events: list[JSON] = [
        dict(kind='revision', id=row.id, revision_kind=row.kind, parent_revision_id=row.parent_revision_id,
             author=revisions.revision_author(row),
             created_at=_utc(row.created_at) if row.created_at is not None else None)
        for row in revisions.history(dictation)
    ]
    # Revisions keep their lineage order (legacy rows have no timestamp), then
    # merge the timestamped ledgers. Pool-wide configuration is not dictation history.
    timed = [
        dict(kind='routing', id=row.id, action='work_state_transferred', at=_utc(row.transferred_at))
        for row in ClinicWorkStateTransfer.query.filter_by(dictation_id=dictation.id)
    ]
    recording = db.session.get(Recording, dictation.recording_id)
    if recording is not None:
        archive = ClinicArchiveEvent.query.filter_by(
            recording_id=recording.id, recording_created_at=recording.created_at or datetime.min,
            target_user_id=None,
        )
        timed.extend(dict(kind='workflow', id=row.id, action=row.event, at=_utc(row.created_at))
                     for row in archive if row.details.get('scope') == 'recording')
    releases = ClinicRelease.query.filter_by(dictation_id=dictation.id)
    timed.extend(dict(kind='release', id=row.id, action='released', at=_utc(row.created_at),
                      audio_revision_id=row.audio_revision_id, transcript_revision_id=row.transcript_revision_id,
                      context_revision_id=row.context_revision_id) for row in releases)
    supersessions = ClinicReleaseSupersession.query.join(ClinicRelease).filter(
        ClinicRelease.dictation_id == dictation.id,
    )
    timed.extend(dict(kind='release', id=row.id, action='superseded', at=_utc(row.superseded_at),
                      release_id=row.release_id) for row in supersessions)
    return events + sorted(timed, key=lambda row: (row['at'], row['action'] == 'superseded', row['id']))


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool], run_once: RunOnce,
    dictation_snapshot: Callable[[ClinicDictation], JSON],
) -> Blueprint:
    """Mount with trusted channel and current object policy (`history`/`review`).

    `review` requires explicit review authority in addition to edit scope.
    `run_once` uses the parity dispatcher transaction/receipt protocol and must
    begin a real outer transaction before invoking a lease service savepoint.
    The release response uses the worklist's contract Dictation serializer.
    """
    bp = Blueprint('clinic_history', __name__)

    def object_for(dictation_id: UUID, action: str, *, lock: bool = False) -> ClinicDictation:
        dictation = db.session.get(ClinicDictation, str(dictation_id),
                                   populate_existing=lock, with_for_update=lock)
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

    @bp.get(f'{PATH}/history')
    def get_history(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'history')
        events = _history(dictation)
        audit.record('recording.content.read', recording_id=dictation.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'history'})
        return jsonify(dictation_id=dictation.id, events=events)

    @bp.post(f'{PATH}/leases', defaults={'operation': 'acquire'})
    @bp.post(f'{PATH}/leases/renew', defaults={'operation': 'renew'})
    @bp.post(f'{PATH}/leases/release', defaults={'operation': 'release'})
    def review_lease(dictation_id: UUID, operation: str) -> Response:
        dictation = object_for(dictation_id, 'review')
        actor, target = current_user.id, dictation.id
        request.max_content_length = min(4096, request.max_content_length or 4096)
        try:
            key = _uuid(request.headers.get('Idempotency-Key', ''))
            proof = request.get_json() if request.get_data() or operation != 'acquire' else {}
            if operation == 'acquire':
                if proof != {}:
                    abort(400)
                lease_id, generation = None, None
            else:
                if not isinstance(proof, dict) or proof.keys() != {'lease_id', 'generation'}:
                    abort(400)
                lease_id = _uuid(proof['lease_id'])
                generation = proof['generation']
                if type(generation) is not int or not 1 <= generation < 2**63:
                    abort(400)
        except (ValueError, TypeError, AttributeError, RecursionError):
            abort(400)

        def apply() -> JSON:
            locked = object_for(dictation_id, 'review', lock=True)
            now = datetime.now(timezone.utc)
            if operation != 'release':
                live_session.assert_edit_allowed(target, actor, now)
            if operation == 'acquire':
                row = lease.acquire(target, actor, now)
            else:
                row = lease.assert_current(target, actor, generation, now)
                if row.id != lease_id:
                    abort(409)
                if operation == 'release':
                    lease.release(row, generation, now, user_id=actor)
                    return dict(dictation_snapshot(locked), etag=dictation_etag(locked))
                row = lease.heartbeat(row, generation, now, user_id=actor)
            return dict(dictation_id=target, lease_id=row.id, generation=row.generation,
                        expires_at=_utc(row.expires_at), etag=dictation_etag(locked))

        digest = sha256(f'{lease_id}:{generation}'.encode()).hexdigest()
        try:
            return jsonify(run_once((actor, operation, target, key), digest, apply))
        except lease.LeaseConflict as error:
            return _problem({lease.FOREIGN_HOLDER: 423, lease.NOT_FOUND: 404}.get(error.reason, 409))
        except live_session.LiveSessionError as error:
            if error.code != live_session.SPEECH_LOCK_HELD:
                raise
            return _problem(423)
        except CommandProblem as error:
            if str(error) == 'IDEMPOTENCY_CONFLICT':
                return _problem(409, conflict=True)
            if str(error) == 'DEPENDENCY_UNAVAILABLE':
                return _problem(503)
            raise

    return bp
