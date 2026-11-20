"""Browser endpoints for atomic correction save, text/audio anchors and markers.

PAR-COR-002/006/007/009. A thin layer over ``correction_transaction``,
``correction`` and ``unicode_anchor_map``. The actor always comes from the
session; no handler reads an actor or role from the request. Cookie writes
need CSRF, every object goes through the injected per-object ``authorize``
(actions ``correction_read`` / ``correction_edit``) and every write needs a
current review lease generation and the live-holder check.

Offsets in requests and responses are UTF-16 code units (what the browser
editor counts); the stored offsets are code points of the normalised body.
Text without a stored audio link is reported as ``aligned: false``; no audio
range is invented.

W10: every save derives the links of the new revision from the previous one.
Unchanged words keep their audio range, a replaced span takes the range
between its neighbours, newly typed words get no link.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import correction as corr
from src.clinic import lease, live_session, revisions
from src.clinic.config import clinic_mode_enabled
from src.clinic.correction_transaction import save_correction_transaction
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import JSON, CommandProblem, RunOnce
from src.clinic.unicode_anchor_map import OffsetError, to_codepoint_offset, to_utf16_offset
from src.database import db
from src.models import User

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/correction'
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Nicht gefunden'),
    409: ('REVISION_CONFLICT', 'Versionskonflikt'),
    413: ('PAYLOAD_TOO_LARGE', 'Anfrage zu gross'),
    423: ('LOCKED', 'Diktat ist gesperrt'),
    428: ('PRECONDITION_REQUIRED', 'If-Match fehlt'),
    503: ('DEPENDENCY_UNAVAILABLE', 'Dienst nicht verfügbar'),
}
MAX_BODY_BYTES = 2 * 1024 * 1024
_WORD = re.compile(r'\S+')
# V8 autosave contract: strong validator "<text revision id | none>[.g<lease generation>]".
_IF_MATCH = re.compile(r'^"([0-9A-Za-z-]{1,64})(?:\.g(\d{1,9}))?"$')
LEASE_STALE = 'LEASE_STALE'


def etag_for(head: ClinicTranscriptRevision | None, generation: int | None) -> str:
    """Strong ETag of the transcript resource: text revision plus lease generation (0 = no lease row)."""
    return f'"{head.id if head else "none"}.g{generation or 0}"'


def _problem(status: int, *, conflict: bool = False, code: str | None = None) -> Response:
    code_, title = _PROBLEMS[status]
    if conflict:
        code_, title = 'IDEMPOTENCY_CONFLICT', 'Anfrageschlüssel bereits verwendet'
    elif code:
        code_ = code
    code = code_
    response = jsonify(type='about:blank', title=title, status=status, code=code,
                       correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    return response


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value.lower():
        raise ValueError('Non-canonical UUID')
    return str(UUID(value))


def _int(value: object, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**31:
        abort(400)
    return value


def _body(allowed: set[str], required: set[str]) -> JSON:
    if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
        abort(400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.keys() - allowed or required - body.keys():
        abort(400)
    return body


def derive_links(dictation: ClinicDictation, old: ClinicTranscriptRevision | None,
                 new: ClinicTranscriptRevision) -> int:
    """W10 follow-up: carry audio ranges from ``old`` to ``new`` by word diff."""
    if old is None or old.id == new.id:
        return 0
    links = corr.effective_links(dictation, old)
    a, b = list(_WORD.finditer(old.body)), list(_WORD.finditer(new.body))
    ops = SequenceMatcher(None, [m.group() for m in a], [m.group() for m in b], autojunk=False)
    made = 0

    def add(start: int, end: int, audio: tuple[int, int]) -> None:
        nonlocal made
        db.session.add(corr.ClinicTextAudioLink(
            dictation_id=dictation.id, transcript_revision_id=new.id,
            seq=corr._next_seq(corr.ClinicTextAudioLink, dictation.id), origin=corr.ORIGIN_AUTO,
            text_start=start, text_end=end, audio_start_ms=audio[0], audio_end_ms=audio[1]))
        db.session.flush()
        made += 1

    def audio_of(word: re.Match) -> tuple[int, int] | None:
        for link in links:
            if link.text_start <= word.start() < link.text_end:
                return link.audio_start_ms, link.audio_end_ms
        return None

    for tag, i1, i2, j1, j2 in ops.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                audio = audio_of(a[i1 + k])
                if audio:
                    add(b[j1 + k].start(), b[j1 + k].end(), audio)
        elif tag == 'replace':
            near = [audio_of(a[i]) for i in (i1 - 1, i2) if 0 <= i < len(a)]
            near = [x for x in near if x]
            if near:
                add(b[j1].start(), b[j2 - 1].end(),
                    (min(x[0] for x in near), max(x[1] for x in near)))
    return made


def _link_json(row: corr.ClinicTextAudioLink, body: str) -> JSON:
    return dict(id=row.id, origin=row.origin, audio_start_ms=row.audio_start_ms,
                audio_end_ms=row.audio_end_ms,
                text_start=to_utf16_offset(body, row.text_start), text_end=to_utf16_offset(body, row.text_end))


def _marker_json(marker: corr.ClinicMarker) -> JSON:
    rev = marker.anchor_revision_id
    body = db.session.get(ClinicTranscriptRevision, rev).body if rev else ''
    return dict(
        id=marker.id, marker_type=marker.marker_type, position_ms=marker.position_ms, label=marker.label,
        audio_revision_id=marker.audio_revision_id, version=marker.version, deleted=marker.deleted_at is not None,
        anchor_revision_id=rev,
        anchor_offset=None if rev is None else to_utf16_offset(body, marker.anchor_offset),
        events=[dict(action=e.action, old_position_ms=e.old_position_ms, new_position_ms=e.new_position_ms,
                     audio_revision_id=e.audio_revision_id, transcript_revision_id=rev,
                     marker_version=n) for n, e in enumerate(corr.marker_events(marker), 1)])


def create_blueprint(*, authorize: Callable[[User, str, ClinicDictation], bool], run_once: RunOnce,
                     require_if_match: bool = False) -> Blueprint:
    """Mount with the trusted session channel and current object policy.

    ``require_if_match`` is the V8 autosave contract: a save without ``If-Match`` answers 428
    PRECONDITION_REQUIRED. The default keeps the pre-V8 behaviour (header optional); the mount passes
    True. A header that is sent is always enforced: a stale revision or lease generation answers 409.

    ``run_once`` is the parity dispatcher transaction/receipt protocol keyed by
    (actor, operation, dictation, Idempotency-Key); it commits on success and
    rolls back on failure.
    """
    bp = Blueprint('clinic_correction', __name__)

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

    def write(dictation_id: UUID, operation: str, scope: str, digest_of: JSON,
              apply: Callable[[ClinicDictation, int], JSON]) -> Response:
        """Authorized, idempotent, lease- and holder-checked write."""
        object_for(dictation_id, 'correction_edit')
        actor, target = current_user.id, str(dictation_id)
        try:
            key = _uuid(request.headers.get('Idempotency-Key', ''))
        except (ValueError, TypeError, AttributeError):
            abort(400)

        def run() -> JSON:
            locked = object_for(dictation_id, 'correction_edit', lock=True)
            live_session.assert_edit_allowed(target, actor, datetime.now(timezone.utc))
            return apply(locked, actor)

        digest = sha256(repr(sorted(digest_of.items())).encode()).hexdigest()
        try:
            return jsonify(run_once((actor, f'{operation}:{scope}', target, key), digest, run))
        except corr.CorrectionConflict as error:
            return _problem(423 if str(error) == lease.FOREIGN_HOLDER else 409)
        except lease.LeaseConflict as error:
            if error.reason == lease.FOREIGN_HOLDER:
                return _problem(423)
            return _problem(409, code=LEASE_STALE)
        except live_session.LiveSessionError as error:
            if error.reason != live_session.SPEECH_LOCK_HELD:
                raise
            return _problem(423)
        except corr.CorrectionError as error:
            return _problem(403 if 'not permitted' in str(error) else 400)
        except OffsetError:
            return _problem(400)
        except CommandProblem as error:
            if str(error) == 'IDEMPOTENCY_CONFLICT':
                return _problem(409, conflict=True)
            if str(error) == 'DEPENDENCY_UNAVAILABLE':
                return _problem(503)
            raise

    @bp.get(PATH)
    def get_correction(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'correction_read')
        head = revisions.current_revision(dictation)
        state = corr.get_state(dictation, current_user.id)
        body = head.body if head else ''
        held = lease.current(dictation.id)
        etag = etag_for(head, held.generation if held else None)
        out: JSON = dict(
            dictation_id=dictation.id, text_revision_id=head.id if head else None, text=body, etag=etag,
            state=None if state is None else dict(version=state.version, audio_position_ms=state.audio_position_ms),
            links=[_link_json(row, body) for row in corr.effective_links(dictation, head)] if head else [],
            markers=[_marker_json(m) for m in corr.list_markers(dictation)])
        if 'offset' in request.args:
            try:
                cp = to_codepoint_offset(body, int(request.args['offset']))
            except (ValueError, OffsetError):
                abort(400)
            link = corr.link_at_text(dictation, head, cp) if head else None
            out['selection'] = (dict(aligned=False) if link is None
                                else dict(aligned=True, **_link_json(link, body)))
        response = jsonify(out)
        response.headers['ETag'] = etag
        return response

    @bp.post(f'{PATH}/save')
    def save(dictation_id: UUID) -> Response:
        data = _body({'text', 'audio_position_ms', 'lease_generation', 'expected_state_version',
                      'expected_text_revision_id'},
                     {'text', 'audio_position_ms', 'lease_generation', 'expected_text_revision_id'})
        if not isinstance(data['text'], str) or len(data['text']) > 1_000_000:
            abort(400)
        position, generation = _int(data['audio_position_ms']), _int(data['lease_generation'], 1)
        expected_state = data.get('expected_state_version')
        expected_rev = data['expected_text_revision_id']
        if expected_state is not None:
            _int(expected_state)
        if expected_rev is not None:
            try:
                expected_rev = _uuid(expected_rev)
            except (ValueError, TypeError, AttributeError):
                abort(400)
        object_for(dictation_id, 'correction_edit')
        if_match = request.headers.get('If-Match')
        precondition = None
        if if_match is None:
            if require_if_match:
                return _problem(428)
        else:
            precondition = _IF_MATCH.match(if_match.strip())
            if precondition is None:
                abort(400)

        def apply(dictation: ClinicDictation, actor: int) -> JSON:
            # Lease first: a foreign holder answers 423 and a stale generation 409 LEASE_STALE
            # before the text precondition is looked at.
            held = lease.assert_current(dictation.id, actor, generation, datetime.now(timezone.utc))
            head = revisions.current_revision(dictation)
            if precondition is not None:
                rev, gen = precondition.group(1), precondition.group(2)
                if rev != (head.id if head else 'none') or (gen is not None and int(gen) != held.generation):
                    raise corr.CorrectionConflict('text changed since it was read')
            if (head.id if head else None) != expected_rev:
                raise corr.CorrectionConflict('text changed since it was read')
            try:
                revision, state = save_correction_transaction(
                    dictation, actor, text=data['text'], audio_position_ms=position,
                    lease_generation=generation, expected_state_version=expected_state,
                    expected_text_revision_id=expected_rev)
                linked = derive_links(dictation, head, revision)
            except lease.LeaseConflict:
                raise
            except corr.CorrectionError:
                raise
            except Exception as error:
                raise CommandProblem('DEPENDENCY_UNAVAILABLE') from error
            return dict(text_revision_id=revision.id, etag=etag_for(revision, held.generation),
                        state_version=state.version, audio_position_ms=state.audio_position_ms,
                        derived_links=linked)

        return write(dictation_id, 'save', 'text', data, apply)

    @bp.post(f'{PATH}/markers')
    def add_marker(dictation_id: UUID) -> Response:
        data = _body({'position_ms', 'audio_revision_id', 'lease_generation', 'marker_type', 'label',
                      'anchor_offset'}, {'position_ms', 'audio_revision_id', 'lease_generation'})
        position, generation = _int(data['position_ms']), _int(data['lease_generation'], 1)
        label, kind = data.get('label'), data.get('marker_type', corr.MARKER_SECTION)
        if not isinstance(data['audio_revision_id'], str) or not (label is None or isinstance(label, str)):
            abort(400)
        if 'anchor_offset' in data:
            _int(data['anchor_offset'])

        def apply(dictation: ClinicDictation, actor: int) -> JSON:
            head = anchor = None
            if 'anchor_offset' in data:
                head = revisions.current_revision(dictation)
                if head is None:
                    raise corr.CorrectionError('no transcript to anchor to')
                anchor = to_codepoint_offset(head.body, data['anchor_offset'])
            marker = corr.add_marker(
                dictation, position, actor, marker_type=kind, label=label, anchor_revision=head,
                anchor_offset=anchor, audio_revision_id=data['audio_revision_id'], lease_generation=generation)
            return _marker_json(marker)

        return write(dictation_id, 'marker_add', 'new', data, apply)

    @bp.post(f'{PATH}/markers/<uuid:marker_id>/move', defaults={'operation': 'move'})
    @bp.post(f'{PATH}/markers/<uuid:marker_id>/delete', defaults={'operation': 'delete'})
    def change_marker(dictation_id: UUID, marker_id: UUID, operation: str) -> Response:
        move = operation == 'move'
        data = _body({'position_ms', 'expected_version', 'lease_generation'} if move
                     else {'expected_version', 'lease_generation'},
                     {'position_ms', 'expected_version', 'lease_generation'} if move
                     else {'expected_version', 'lease_generation'})
        version, generation = _int(data['expected_version'], 1), _int(data['lease_generation'], 1)
        position = _int(data['position_ms']) if move else None

        def apply(dictation: ClinicDictation, actor: int) -> JSON:
            marker = db.session.get(corr.ClinicMarker, str(marker_id))
            if marker is None or marker.dictation_id != dictation.id:
                abort(404)
            kwargs = dict(expected_version=version, lease_generation=generation)
            if move:
                corr.move_marker(marker, position, actor, **kwargs)
            else:
                corr.delete_marker(marker, actor, **kwargs)
            return _marker_json(marker)

        return write(dictation_id, operation, str(marker_id), data, apply)

    return bp
