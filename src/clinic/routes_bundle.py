"""HTTP adapter for the audio-text session bundle (``.sdict``) and plain-text files.

Thin layer over ``sdict_bundle`` (ZIP, hash and size checks) and
``richtext_formats`` (canonical text round-trip). The actor always comes from
the authenticated browser session, never from the request. Reads and writes go
through trusted callables supplied by the mount (object policy, storage), so
this module holds no persistence and never deletes anything.

BYOD clients (``is_managed_client`` false) get neither an export nor a
persisting import. Imports need CSRF, ``If-Match`` (current revision) and an
``Idempotency-Key``; a retry with the same key returns the stored result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import output_rtf, richtext_formats as formats, startup
from src.clinic.config import clinic_mode_enabled
from src.clinic.parity_dispatch import _PROBLEMS, JSON, CommandProblem, RunOnce
from src.clinic.rich_text_doc import RichTextError
from src.clinic.richtext import SCHEMA_VERSION, CanonicalDocument, Node, Segment
from src.clinic.richtext_graphics import GraphicStore
from src.clinic.sdict_bundle import BundleError, read_bundle

PREFIX = '/api/clinic/v1/bundles'
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
_TITLES = {400: 'Ungültige Anfrage', 401: 'Anmeldung erforderlich', 403: 'Zugriff verweigert',
           404: 'Nicht gefunden', 409: 'Versionskonflikt', 413: 'Datei zu gross',
           428: 'Versionsangabe erforderlich', 503: 'Dienst nicht verfügbar'}


class StaleVersion(Exception):
    """The stored revision differs from the caller's ``If-Match``."""


@dataclass(frozen=True)
class BundleSession:
    """Server-loaded dictation text and audio at ``version``."""

    doc: CanonicalDocument
    audio: bytes
    version: int
    audio_synced: bool | None = None


Load = Callable[[int, str], BundleSession | None]
Authorize = Callable[[int, str, str], bool]  # (actor, dictation_id, 'export' | 'import')
Persist = Callable[[int, str, CanonicalDocument, bytes | None, int], AbstractContextManager[int]]
LoadGraphic = Callable[[int, str, str], bytes]


def text_to_document(raw: bytes) -> CanonicalDocument:
    """Strict UTF-8 plain text into a canonical document, one paragraph per line."""
    try:
        text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        lines = text.split('\n')
        return CanonicalDocument(tuple(
            Node(id=f'n{i}', kind='paragraph', runs=(Segment(id=f's{i}', text=line),))
            for i, line in enumerate(lines, 1)))
    except (UnicodeDecodeError, RichTextError) as error:
        raise formats.FormatError('malformed') from error


def create_blueprint(
    load: Load, authorize: Authorize, persist: Persist, run_once: RunOnce,
    is_managed_client: Callable[[], bool], load_graphic: LoadGraphic | None = None,
) -> Blueprint:
    blueprint = Blueprint('clinic_bundle', __name__, url_prefix=PREFIX)

    def graphic_bytes(actor: int, dictation_id: str, object_id: str) -> bytes:
        if load_graphic is not None:
            return load_graphic(actor, dictation_id, object_id)
        return GraphicStore(startup.originals_root(current_app)).get(
            dictation_id, object_id, actor_user_id=actor,
        )[1]

    def problem(status: int, code: str):
        response = jsonify(type='about:blank', status=status, code=code,
                           title=_TITLES.get(status, 'Fehler'), correlation_id=str(uuid4()))
        response.mimetype = 'application/problem+json'
        return response, status

    @blueprint.errorhandler(HTTPException)
    def http_problem(error: HTTPException):
        status = error.code or 400
        return problem(status if status in _TITLES else 400, error.name.upper().replace(' ', '_'))

    @blueprint.before_request
    def authenticate():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if request.method not in ('GET', 'HEAD'):
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)

    @blueprint.after_request
    def no_store(response: Response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    def target(dictation_id: str, action: str) -> str:
        try:
            dictation_id = str(UUID(dictation_id))
        except ValueError:
            abort(404)
        if not is_managed_client():
            abort(403)  # BYOD: no export, no persistence
        # Object policy first; an unreadable object looks like a missing one.
        if not authorize(current_user.id, dictation_id, action):
            abort(404)
        return dictation_id

    @blueprint.get('/<dictation_id>/export')
    def export(dictation_id: str):
        try:
            current = load(current_user.id, target(dictation_id, 'export'))
        except StaleVersion:
            return problem(409, 'REVISION_CONFLICT')
        except RichTextError:
            abort(409)
        if current is None:
            abort(404)
        wanted = request.args.get('version')
        if wanted is not None and (not wanted.isdigit() or int(wanted) != current.version):
            return problem(409, 'REVISION_CONFLICT')
        kind = request.args.get('format', 'sdict')
        try:
            if kind == 'txt':
                body, mime, ext = formats.to_plain_text(current.doc).encode('utf-8'), 'text/plain; charset=utf-8', 'txt'
            elif kind == 'html':
                graphics = {}
                for node in current.doc.nodes:
                    if node.kind == 'graphic' and node.object_id not in graphics:
                        try:
                            graphics[node.object_id] = graphic_bytes(
                                current_user.id, str(UUID(dictation_id)), node.object_id,
                            )
                        except (RichTextError, OSError) as error:
                            raise formats.FormatError('graphic_unavailable') from error
                body = formats.web_html(current.doc, graphics).encode('utf-8')
                mime, ext = 'text/html; charset=utf-8', 'html'
            elif kind in ('sdict', 'rtf'):
                out = formats.export_document(current.doc, expected_version=SCHEMA_VERSION,
                                              allowed=True, audio=current.audio)
                body, mime, ext = ((out.bundle, 'application/zip', 'sdict') if kind == 'sdict'
                                   else (out.rtf, 'application/rtf', 'rtf'))
            else:
                return problem(400, 'INVALID_PAYLOAD')
        except (formats.FormatError, output_rtf.RTFValidationError):
            return problem(400, 'INVALID_PAYLOAD')
        response = Response(body, mimetype=mime.split(';')[0])
        response.headers['Content-Type'] = mime
        response.headers['Content-Disposition'] = f'attachment; filename="diktat.{ext}"'
        response.headers['ETag'] = f'"{current.version}"'
        response.headers['X-Content-SHA256'] = sha256(body).hexdigest()
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Export-Losses'] = json.dumps([
            {'code': loss.code, 'detail': loss.detail}
            for loss in formats.export_losses(current.doc, kind)
        ], separators=(',', ':'))
        if kind == 'html':
            response.headers['Content-Security-Policy'] = formats.WEB_CSP
        if current.audio_synced is not None:
            response.headers['X-Audio-Synced'] = 'true' if current.audio_synced else 'false'
        return response

    @blueprint.post('/<dictation_id>/import')
    def import_session(dictation_id: str):
        kind = request.args.get('format', 'sdict')
        limit = MAX_BUNDLE_BYTES if kind == 'sdict' else MAX_TEXT_BYTES
        if kind not in ('sdict', 'txt'):
            return problem(400, 'INVALID_PAYLOAD')
        if (request.content_length or 0) > limit:
            return problem(413, 'TOO_LARGE')
        match = request.headers.get('If-Match', '').strip('"')
        key = request.headers.get('Idempotency-Key', '')
        if not match.isascii() or not match.isdecimal() or len(match) > 10:
            return problem(428, 'VERSION_REQUIRED')
        if not 8 <= len(key) <= 128 or not key.strip():
            return problem(400, 'INVALID_PAYLOAD')
        expected = int(match)
        dictation = target(dictation_id, 'import')
        request.max_content_length = min(limit, request.max_content_length or limit)
        data = request.get_data(cache=False)
        if len(data) > limit or not data:
            return problem(413 if data else 400, 'TOO_LARGE' if data else 'INVALID_PAYLOAD')
        declared = request.headers.get('X-Content-SHA256')
        if declared is not None and declared.lower() != sha256(data).hexdigest():
            return problem(400, 'CHECKSUM_MISMATCH')
        try:
            if kind == 'txt':
                doc, audio = text_to_document(data), None
            else:
                bundle = read_bundle(data)
                if (set(bundle.files) != {'audio/a.wav', 'text/body.txt', 'text/document.json'}
                        or [source['path'] for source in bundle.manifest['audio_sources']] != ['audio/a.wav']
                        or bundle.manifest.get('edit_list')):
                    return problem(400, 'INVALID_PAYLOAD')
                doc = formats.document_from_bundle(bundle)
                audio = bundle.files['audio/a.wav']
        except (formats.FormatError, BundleError):
            return problem(400, 'INVALID_PAYLOAD')

        actor = current_user.id

        def apply(staged: ExitStack) -> JSON:
            try:
                revision = staged.enter_context(persist(actor, dictation, doc, audio, expected))
            except StaleVersion as error:
                raise CommandProblem('REVISION_CONFLICT') from error
            except OSError as error:
                raise CommandProblem('DEPENDENCY_UNAVAILABLE') from error
            return {'status': 'committed', 'revision': revision,
                    'text_identity': formats.text_hash(formats.to_plain_text(doc)),
                    'audio_synced': audio is not None}

        digest = hashlib.sha256(f'{kind}:{expected}:'.encode())
        digest.update(data)
        try:
            with ExitStack() as staged:
                result = run_once((actor, 'import_bundle', dictation, key), digest.hexdigest(),
                                  lambda: apply(staged))
            return jsonify(result)
        except CommandProblem as error:
            status, _title = _PROBLEMS[str(error)]
            return problem(status, str(error))

    return blueprint
