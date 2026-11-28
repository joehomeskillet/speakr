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
range is invented. A rich ``document`` is optional on save. With it, the same
canonical snapshot is returned by GET and by the save result, guarded by
``expected_document_version``. A request without it keeps the plain-text
contract and does not delete a stored snapshot.

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

from src.clinic import alignment_tokens as at
from src.clinic import correction as corr
from src.clinic import caution_words, lease, live_session, revisions, vocabulary
from src.clinic.alignment import ClinicAlignmentRevision
from src.clinic.audio_invalidation import get_compatible_links
from src.clinic.audio_revisions import current_audio_revision
from src.clinic.config import clinic_mode_enabled
from src.clinic.correction_recognition_marks import current_marks
from src.clinic.correction_transaction import CorrectionOriginalError, save_correction_transaction
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import JSON, CommandProblem, RunOnce
from src.clinic.rich_text_doc import RichTextError, to_plain_text
from src.clinic.richtext import CanonicalDocument, ClinicRichTextSnapshot
from src.clinic.routes_vocabulary import ApiError, canonical_selection
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
_DOCUMENT_KEYS = frozenset({'schema_version', 'nodes'})
_NODE_KEYS = frozenset({'id', 'kind', 'level', 'runs'})
_RUN_KEYS = frozenset({'id', 'text', 'bold', 'italic', 'underline', 'audio_anchor_id'})


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


def _api_document(document: CanonicalDocument) -> JSON:
    """Correction-API projection: only the fields this save contract accepts."""
    return {
        'schema_version': 1,
        'nodes': [{
            'id': node.id, 'kind': node.kind, 'level': node.level,
            'runs': [{
                'id': run.id, 'text': run.text, 'bold': run.bold, 'italic': run.italic,
                'underline': run.underline, 'audio_anchor_id': run.audio_anchor_id,
            } for run in node.runs],
        } for node in document.nodes],
    }


def _parse_document(payload: object) -> CanonicalDocument:
    """Unknown fields are a 400. Nothing is stored until the canonical parser accepts the object."""
    nodes = payload.get('nodes') if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or set(payload) - _DOCUMENT_KEYS or not isinstance(nodes, list):
        abort(400)
    for node in nodes:
        runs = node.get('runs') if isinstance(node, dict) else None
        if not isinstance(node, dict) or set(node) - _NODE_KEYS or not isinstance(runs, list):
            abort(400)
        for run in runs:
            if not isinstance(run, dict) or set(run) - _RUN_KEYS:
                abort(400)
    try:
        return CanonicalDocument.from_payload(payload)
    except (RichTextError, TypeError, ValueError):
        abort(400)


def _stored_document(revision_id: str | None) -> tuple[int, JSON | None]:
    """Latest snapshot on one text revision. Version 0 means there is none."""
    if not revision_id:
        return 0, None
    row = (db.session.query(ClinicRichTextSnapshot).filter_by(revision_id=revision_id)
           .order_by(ClinicRichTextSnapshot.version.desc()).limit(1).first())
    if row is None:
        return 0, None
    return row.version, _api_document(CanonicalDocument.from_payload(row.payload))


def _link_json(row: corr.ClinicTextAudioLink, body: str) -> JSON:
    # Every stored link is a segment range (W10-derived or manual), never a word timing.
    return dict(id=row.id, origin=row.origin, granularity='segment', audio_start_ms=row.audio_start_ms,
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

    The clinic app requires the validator from GET /correction for saves. A
    missing header returns 428, a stale revision or lease generation returns
    409. Standalone legacy mounts may keep the header optional.

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
        except CorrectionOriginalError as error:
            return _problem(409, code=str(error))
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
        except RichTextError as error:
            if error.code in {'stale_version', 'stale_revision'}:
                return _problem(409)
            return _problem(403 if error.code == 'forbidden' else 400)
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
        audio = current_audio_revision(dictation)
        links = get_compatible_links(dictation, head, audio) if head else []
        out: JSON = dict(
            recording_id=dictation.recording_id,
            dictation_id=dictation.id, text_revision_id=head.id if head else None, text=body, etag=etag,
            state=None if state is None else dict(version=state.version, audio_position_ms=state.audio_position_ms),
            audio_revision_id=audio.id if audio else None,
            links=[_link_json(row, body) for row in links],
            markers=[_marker_json(m) for m in corr.list_markers(dictation) if not caution_words.is_review_marker(m)])
        _version, payload = _stored_document(head.id if head else None)
        if payload is not None:
            out['document'] = payload
            out['document_version'] = _version
        if clinic_mode_enabled():
            from src.clinic.bundle_store import ClinicBundleDocument, audio_synced

            saved = db.session.get(ClinicBundleDocument, head.id) if head else None
            if saved is not None:
                out['audio_synced'] = audio_synced(saved, audio)
            evidence = current_marks(dictation.id, audio.id, head.id) if head and audio else []
            out['recognition_spans'] = [
                dict(id=row.id, kind=row.kind, start_ms=row.start_ms, end_ms=row.end_ms)
                for row in evidence if row.kind in ('uncertain', 'unrecognized')
            ]
        if 'offset' in request.args:
            try:
                cp = to_codepoint_offset(body, int(request.args['offset']))
            except (ValueError, OffsetError):
                abort(400)
            link = corr.link_at_text(dictation, head, cp) if head else None
            if link is not None and link.id not in {row.id for row in links}:
                link = None
            out['selection'] = (dict(aligned=False) if link is None
                                else dict(aligned=True, **_link_json(link, body)))
        response = jsonify(out)
        response.headers['ETag'] = etag
        return response

    @bp.post(f'{PATH}/save')
    def save(dictation_id: UUID) -> Response:
        data = _body({'text', 'audio_position_ms', 'lease_generation', 'expected_state_version',
                      'expected_text_revision_id', 'document', 'expected_document_version'},
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
        parsed = None
        expected_doc = None
        if 'document' in data or 'expected_document_version' in data:
            if 'document' not in data or 'expected_document_version' not in data:
                abort(400)
            expected_doc = data['expected_document_version']
            if type(expected_doc) is not int or not 0 <= expected_doc < 2**31:
                abort(400)
            parsed = _parse_document(data['document'])
            if revisions.normalise_text(data['text']) != revisions.normalise_text(to_plain_text(parsed)):
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
            doc_version = None
            if parsed is not None:
                same_text = head is not None and head.body == revisions.normalise_text(data['text'])
                if same_text:
                    doc_version = expected_doc
                else:
                    # A new text revision has no snapshot. The version the client read
                    # belongs to expected_text_revision_id; 0 there means none.
                    if expected_doc != _stored_document(expected_rev)[0]:
                        raise corr.CorrectionConflict('document changed since it was read')
                    doc_version = 0
            try:
                revision, state = save_correction_transaction(
                    dictation, actor, text=data['text'], audio_position_ms=position,
                    lease_generation=generation, expected_state_version=expected_state,
                    expected_text_revision_id=expected_rev, document=parsed,
                    expected_document_version=doc_version)
                linked = derive_links(dictation, head, revision)
                at.tokens_for(dictation, revision)
                has_alignment = db.session.query(ClinicAlignmentRevision.id).filter_by(
                    dictation_id=dictation.id).first() is not None
            except lease.LeaseConflict:
                raise
            except corr.CorrectionError:
                raise
            except RichTextError:
                raise
            except Exception as error:
                raise CommandProblem('DEPENDENCY_UNAVAILABLE') from error
            result = dict(text_revision_id=revision.id, etag=etag_for(revision, held.generation),
                          state_version=state.version, audio_position_ms=state.audio_position_ms,
                          derived_links=linked, alignment_state='stale' if has_alignment else 'unaligned')
            if parsed is not None:
                stored_version, stored = _stored_document(revision.id)
                if stored is None:
                    raise CommandProblem('DEPENDENCY_UNAVAILABLE')
                result['document'] = stored
                result['document_version'] = stored_version
            return result

        return write(dictation_id, 'save', 'text', data, apply)

    @bp.get(f'{PATH}/review-markers')
    def get_review_markers(dictation_id: UUID) -> Response:
        dictation = object_for(dictation_id, 'correction_read')
        head = revisions.current_revision(dictation)
        hits = caution_words.open_review_hits(dictation, head)
        return jsonify(text_revision_id=head.id if head else None, review_markers=[
            dict(id=hit.marker_id, label=hit.word, anchor_offset=to_utf16_offset(head.body, hit.offset))
            for hit in hits
        ] if head else [])

    @bp.post(f'{PATH}/review-markers/<uuid:marker_id>/confirm')
    def confirm_review_marker(dictation_id: UUID, marker_id: UUID) -> Response:
        data = _body({'expected_text_revision_id', 'lease_generation'},
                     {'expected_text_revision_id', 'lease_generation'})
        generation = _int(data['lease_generation'], 1)
        try:
            expected = _uuid(data['expected_text_revision_id'])
        except ValueError:
            abort(400)

        def current_head(dictation: ClinicDictation) -> ClinicTranscriptRevision:
            head = revisions.current_revision(dictation)
            if head is None or head.id != expected:
                abort(409)
            return head

        dictation = object_for(dictation_id, 'correction_edit')
        current_head(dictation)  # A receipt from a superseded revision is stale too.
        pool_id = dictation.pool_id

        def apply(dictation: ClinicDictation, actor: int) -> JSON:
            head = current_head(dictation)
            if dictation.pool_id != pool_id:
                abort(409)
            caution_words.confirm_review_hit(dictation, head, str(marker_id), actor, lease_generation=generation)
            return dict(id=str(marker_id), text_revision_id=head.id, confirmed=True)

        return write(dictation_id, 'review_marker_confirm', f'{pool_id}:{marker_id}', data, apply)

    @bp.post(f'{PATH}/vocabulary-proposal')
    def propose_vocabulary(dictation_id: UUID) -> Response:
        """PAR-COR-009: mark the saved selection as one pending vocabulary proposal.

        The phrase is taken from the confirmed transcript revision, not from a
        free-standing body, and only an editor who may correct this dictation
        can submit it. A repeated mark of the same phrase returns the existing
        PENDING item. The call never writes a user or pool vocabulary entry.
        """
        data = _body({'expected_text_revision_id', 'selection_start', 'selection_end', 'lease_generation'},
                     {'expected_text_revision_id', 'selection_start', 'selection_end', 'lease_generation'})
        generation = _int(data['lease_generation'], 1)
        start, end = _int(data['selection_start']), _int(data['selection_end'])
        if start >= end:
            abort(400)
        try:
            expected = _uuid(data['expected_text_revision_id'])
        except (ValueError, TypeError, AttributeError):
            abort(400)

        def apply(dictation: ClinicDictation, actor: int) -> JSON:
            lease.assert_current(dictation.id, actor, generation, datetime.now(timezone.utc))
            head = revisions.current_revision(dictation)
            if head is None or head.id != expected:
                raise corr.CorrectionConflict('text changed since it was read')
            try:
                start_cp = to_codepoint_offset(head.body, start)
                end_cp = to_codepoint_offset(head.body, end)
            except OffsetError:
                raise corr.CorrectionError('selection is outside the confirmed revision')
            if start_cp >= end_cp:
                raise corr.CorrectionError('selection is empty')
            try:
                phrase = canonical_selection(head.body[start_cp:end_cp])
            except ApiError:
                raise corr.CorrectionError('selection is not a phrase')
            pending = vocabulary.list_review_items(
                user_id=actor, status=vocabulary.REVIEW_STATUS_PENDING)
            item = next((
                row for row in pending
                if row.dictation_id == dictation.id and row.written_form == phrase
                and row.operation == vocabulary.REVIEW_OP_ADD
                and row.origin == vocabulary.ORIGIN_CORRECTION and row.pool_id is None
            ), None)
            if item is None:
                item = vocabulary.submit_review_item(
                    user_id=actor, written_form=phrase, operation=vocabulary.REVIEW_OP_ADD,
                    origin=vocabulary.ORIGIN_CORRECTION, dictation_id=dictation.id,
                    information=f'text_revision_id={head.id}')
            return dict(id=item.id, status=item.status, written=item.written_form,
                        text_revision_id=head.id, published=False)

        return write(dictation_id, 'vocabulary_proposal', expected, data, apply)

    @bp.post(f'{PATH}/markers')
    def add_marker(dictation_id: UUID) -> Response:
        data = _body({'position_ms', 'audio_revision_id', 'lease_generation', 'marker_type', 'label',
                      'anchor_offset'}, {'position_ms', 'audio_revision_id', 'lease_generation'})
        position, generation = _int(data['position_ms']), _int(data['lease_generation'], 1)
        label, kind = data.get('label'), data.get('marker_type', corr.MARKER_SECTION)
        if not isinstance(data['audio_revision_id'], str) or not (label is None or isinstance(label, str)):
            abort(400)
        if kind == corr.MARKER_INDEX and caution_words.reserved_label(label):
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
            if marker is None or marker.dictation_id != dictation.id or caution_words.is_review_marker(marker):
                abort(404)
            kwargs = dict(expected_version=version, lease_generation=generation)
            if move:
                corr.move_marker(marker, position, actor, **kwargs)
            else:
                corr.delete_marker(marker, actor, **kwargs)
            return _marker_json(marker)

        return write(dictation_id, operation, str(marker_id), data, apply)

    return bp
