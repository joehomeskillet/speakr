"""Browser endpoints for non-destructive audio editing and derived playback.

Thin HTTP layer over ``audio_invalidation`` (edit + anchor invalidation +
release supersession in one transaction), ``audio_revisions`` (head, edit list)
and ``audio_render`` (derived WAV). The original stays immutable: only the
derived rendering of the current head is ever served.
"""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import tempfile
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import audio_invalidation, audio_render, audio_revisions, lease, live_session
from src.clinic.audio_format_choice import FormatError, parse_mime
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.parity_dispatch import JSON, CommandProblem, RunOnce
from src.database import db
from src.models import User
from src.services import audit
from src.services.waveform_peaks import PeaksError, compute_peaks

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>'
DERIVED_MIME = 'audio/wav'
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('REVISION_CONFLICT', 'Versionskonflikt'),
    413: ('PAYLOAD_TOO_LARGE', 'Anfrage zu gross'),
    415: ('UNSUPPORTED_MEDIA_TYPE', 'Nicht unterstütztes Format'),
    423: ('LOCKED', 'Diktat ist gesperrt'),
    500: ('PEAKS_FAILED', 'Wellenform konnte nicht berechnet werden'),
    503: ('DEPENDENCY_UNAVAILABLE', 'Dienst nicht verfügbar'),
}
# Allowed body keys per operation; the actor and role always come from the session.
_COMMON = {'operation', 'audio_revision_id', 'lease_generation'}
_KEYS = {
    'insert': _COMMON | {'position_ms', 'source_segment_id', 'duration_ms', 'source_start_ms'},
    'overwrite': _COMMON | {'position_ms', 'source_segment_id', 'duration_ms', 'source_start_ms'},
    'cut': _COMMON | {'start_ms', 'end_ms'},
    'trim': _COMMON | {'position_ms'},
    'undo': _COMMON,
    'redo': _COMMON,
}
_REQUIRED = {
    'insert': {'position_ms', 'source_segment_id'},
    'overwrite': {'position_ms', 'source_segment_id'},
    'cut': {'start_ms', 'end_ms'},
    'trim': {'position_ms'},
    'undo': set(),
    'redo': set(),
}
SourceLoader = Callable[[ClinicDictation, audio_revisions.EditDecisionList],
                        Mapping[tuple[str, str], audio_render.AudioSource]]


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


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('UUID must be a string')
    canonical = str(UUID(value))
    if canonical != value.lower():
        raise ValueError('Non-canonical UUID')
    return canonical


def _int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**31:
        raise ValueError('invalid integer')
    return value


def revision_etag(revision_id: str) -> str:
    """Opaque head token: a different head is a different resource version."""
    return f'"{revision_id}"'


def parse_edit(body: object) -> dict:
    """Validate one edit request; unknown keys (such as an actor or role) are refused."""
    if not isinstance(body, dict) or body.get('operation') not in _KEYS:
        raise ValueError('unknown operation')
    operation = body['operation']
    if body.keys() - _KEYS[operation] or (_REQUIRED[operation] | {'audio_revision_id', 'lease_generation'}) - body.keys():
        raise ValueError('unexpected or missing fields')
    parsed = dict(operation=operation, audio_revision_id=_uuid(body['audio_revision_id']),
                  lease_generation=_int(body['lease_generation'], minimum=1))
    for key in ('position_ms', 'start_ms', 'end_ms', 'source_start_ms'):
        if key in body:
            parsed[key] = _int(body[key])
    if 'duration_ms' in body:
        parsed['duration_ms'] = _int(body['duration_ms'], minimum=1)
    if 'source_segment_id' in body:
        parsed['source_segment_id'] = _uuid(body['source_segment_id'])
    return parsed


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool], run_once: RunOnce,
    load_sources: SourceLoader,
) -> Blueprint:
    """Mount with trusted channel and current object policy (`play` / `edit`).

    `authorize` is the explicit per-object policy (admin access included); the
    handler never reads an actor or role from the request. `run_once` is the
    parity dispatcher transaction/receipt protocol (a failed edit leaves the
    head unchanged). `load_sources` returns the decoded WAV sources of the
    dictation for the given edit list and enforces that they belong to it.
    """
    bp = Blueprint('clinic_audio', __name__)

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
        response.headers.setdefault('Cache-Control', 'no-store')
        return response

    @bp.get(f'{PATH}/audio-revisions/current')
    def get_current(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'play')
        head = audio_revisions.current_audio_revision(dictation)
        if head is None:
            abort(404)
        edl = audio_revisions.get_revision_edl(dictation, head)
        audit.record('recording.content.read', recording_id=dictation.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'audio_revision'})
        response = jsonify(revision_id=head.id, segments=[
            dict(source_id=e.source_id, start_ms=e.source_start_ms, end_ms=e.source_end_ms)
            for e in edl.entries])
        response.headers['ETag'] = revision_etag(head.id)
        return response

    @bp.post(f'{PATH}/audio-edits')
    def edit(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'edit')
        actor, target = current_user.id, dictation.id
        request.max_content_length = min(4096, request.max_content_length or 4096)
        try:
            key = _uuid(request.headers.get('Idempotency-Key', ''))
            spec = parse_edit(request.get_json())
        except (ValueError, TypeError, AttributeError, RecursionError, KeyError):
            abort(400)

        def apply() -> JSON:
            locked = object_for(dictation_id, 'edit', lock=True)
            now = datetime.now(timezone.utc)
            live_session.assert_edit_allowed(target, actor, now)
            args: dict = {}
            op = spec['operation']
            if op in ('insert', 'overwrite'):
                segment = db.session.get(audio_revisions.ClinicAudioSegment, spec['source_segment_id'])
                if segment is None or segment.dictation_id != target:
                    abort(404)
                args = dict(position_ms=spec['position_ms'], source=segment,
                            duration_ms=spec.get('duration_ms'),
                            source_start_ms=spec.get('source_start_ms', 0))
            elif op == 'cut':
                args = dict(start_ms=spec['start_ms'], end_ms=spec['end_ms'])
            elif op == 'trim':
                args = dict(position_ms=spec['position_ms'])
            result = audio_invalidation.apply_audio_edit_and_invalidate(
                locked, op, actor, spec['lease_generation'], now,
                expected_revision_id=spec['audio_revision_id'], **args)
            # The edit's actor and lease generation are stored with its append-only
            # invalidation events; the audit vocabulary has no audio edit field yet.
            return dict(dictation_id=target, revision_id=result.new_revision.id,
                        previous_revision_id=result.previous_revision.id if result.previous_revision else None,
                        etag=revision_etag(result.new_revision.id),
                        preserved_link_ids=list(result.preserved_link_ids),
                        invalidated_link_ids=list(result.invalidated_link_ids),
                        superseded_release_ids=[r.id for r in result.superseded_releases])

        digest = sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        try:
            return jsonify(run_once((actor, 'audio_edit', target, key), digest, apply))
        except audio_invalidation.AudioInvalidationConflict:
            return _problem(409)
        except audio_invalidation.AudioInvalidationPermissionDenied:
            return _problem(403)
        except audio_revisions.AudioUndoRedoError:
            return _problem(409)
        except (audio_revisions.AudioRevisionError, audio_invalidation.AudioInvalidationError):
            return _problem(400)
        except lease.LeaseConflict as error:
            return _problem({lease.FOREIGN_HOLDER: 423, lease.NOT_FOUND: 404}.get(error.reason, 409))
        except live_session.LiveSessionError as error:
            if error.reason != live_session.SPEECH_LOCK_HELD:
                raise
            return _problem(423)
        except CommandProblem as error:
            if str(error) == 'IDEMPOTENCY_CONFLICT':
                return _problem(409, conflict=True)
            if str(error) == 'DEPENDENCY_UNAVAILABLE':
                return _problem(503)
            raise

    @bp.get(f'{PATH}/audio/derived')
    def derived(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'play')
        wanted = request.args.get('format', 'wav')
        try:
            mime, _ = parse_mime(wanted if '/' in wanted else f'audio/{wanted}')
        except FormatError:
            abort(415)
        if mime != DERIVED_MIME:
            abort(415)
        head = audio_revisions.current_audio_revision(dictation)
        if head is None:
            abort(404)
        expected = request.args.get('audio_revision_id')
        if expected is not None:
            try:
                expected = _uuid(expected)
            except ValueError:
                abort(400)
            if expected != head.id:
                abort(409)
        try:
            edl = audio_revisions.get_revision_edl(dictation, head)
            rendered = audio_render.render_current_revision(dictation, head.id, load_sources(dictation, edl))
        except audio_revisions.AudioRevisionError:
            abort(503)
        audit.record('recording.content.read', recording_id=dictation.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'audio_derived'})
        response = Response(rendered.wav, mimetype=DERIVED_MIME)
        response.headers['ETag'] = revision_etag(head.id)
        response.headers['X-Audio-Revision'] = head.id
        return response.make_conditional(request, accept_ranges=True, complete_length=len(rendered.wav))

    @bp.get(f'{PATH}/audio-revisions/<uuid:revision_id>/peaks')
    def peaks(dictation_id: UUID, revision_id: UUID) -> Response:
        """Waveform peaks of exactly one audio revision, for the correction view.

        Same object policy as the correction view; a foreign dictation, an unknown
        dictation and a revision that is not this dictation's all answer 404. The
        peaks are computed by the upstream routine from the rendered revision. The
        body is the binary peaks file only (no transcript text); the ETag is the
        immutable revision, so an unchanged revision is answered 304 without a render.
        """
        dictation = object_for(dictation_id, 'correction_read')
        revision = db.session.get(audio_revisions.ClinicAudioRevision, str(revision_id))
        if revision is None or revision.dictation_id != dictation.id:
            abort(404)
        token = f'{revision.id}.peaks'
        if request.if_none_match.contains(token):
            response = Response(status=304)
            response.headers['ETag'] = f'"{token}"'
            response.headers['Cache-Control'] = 'private, no-cache'
            return response
        try:
            edl = audio_revisions.get_revision_edl(dictation, revision)
            wav = audio_render.render_audio(edl, load_sources(dictation, edl)).wav
        except audio_revisions.AudioRevisionNotFoundError:
            abort(404)
        except audio_revisions.AudioRevisionError:
            abort(503)
        handle, path = tempfile.mkstemp(suffix='.wav')
        try:
            with os.fdopen(handle, 'wb') as out:
                out.write(wav)
            data = compute_peaks(path)
        except PeaksError:
            return _problem(500)
        finally:
            os.unlink(path)
        audit.record('recording.content.read', recording_id=dictation.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'audio_peaks'})
        response = Response(data, mimetype='application/octet-stream')
        response.headers['ETag'] = f'"{token}"'
        response.headers['Cache-Control'] = 'private, no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Audio-Revision'] = revision.id
        return response

    return bp
