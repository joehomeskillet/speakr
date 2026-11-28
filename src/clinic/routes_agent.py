"""HTTP layer of the import-agent channel (machine principal only, no browser session).

Every route the Windows import agent calls (apps/windows-import HttpImportServer.cs) lives
here and delegates to the existing services: ``agent_channel`` (inventory, intents),
``agent_channel_ingest`` (resumable chunks, receipt, outcomes) and ``import_centre``
(finalize through the delivery decision). No channel logic is duplicated.

Authentication follows the machine mTLS contract: TLS is verified at the trusted ingress,
which forwards the SHA-256 fingerprint of the verified client certificate and a shared proxy
secret. A request without the secret, without a fingerprint or with an unknown or disabled
agent is refused with 401. The session cookie is never read, so no browser session or CSRF
token can authenticate here; because of that the blueprint must be CSRF-exempt in the app
(machine routes only) and every route keeps its own credential check.

The agent identity comes only from the credential; an ``agent_id`` in a body or query is
ignored. Bodies, headers and chunks are size-limited and content-type-checked before the
service runs (413, 415). Neither bodies nor chunk bytes are logged.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import uuid
from typing import BinaryIO

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from src.clinic import agent_channel, agent_channel_ingest, import_centre
from src.clinic.agent_channel import AgentChannelError, ChannelPolicy
from src.clinic.author_resolution import AuthorResolutionError
from src.clinic.ingest import IngestError, IngestService
from src.database import db

logger = logging.getLogger(__name__)

PREFIX = '/api/clinic/v1/agent'
PROXY_SECRET_HEADER = 'X-Clinic-Proxy-Secret'
FINGERPRINT_HEADER = 'X-Clinic-Client-Cert-Sha256'
MAX_JSON_BYTES = 2 * 1024 * 1024
MIN_PROXY_SECRET_BYTES = 32

_STATUS = {'invalid': 400, 'forbidden': 403, 'not_found': 404, 'conflict': 409, 'expired': 410}
_INGEST_STATUS = {'invalid': 400, 'conflict': 409, 'author_required': 409, 'storage_failed': 503}
_TITLES = {
    400: 'Ungültige Anfrage', 401: 'Anmeldung des Agenten erforderlich', 403: 'Zugriff verweigert',
    404: 'Nicht gefunden', 409: 'Konflikt', 410: 'Abgelaufen', 413: 'Anfrage zu gross',
    415: 'Nicht unterstütztes Format', 500: 'Interner Fehler', 503: 'Dienst nicht verfügbar',
}
_SHA = re.compile(r'^[0-9a-fA-F]{64}$')
_INGEST_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class _Refusal(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def _problem(status, code):
    response = jsonify(type='about:blank', title=_TITLES.get(status, 'Fehler'), status=status, code=code,
                       correlation_id=str(uuid.uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _ok(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _read_limited(limit):
    """Request body, at most ``limit`` bytes: 413 before or while reading, never the whole oversize body."""
    declared = request.content_length
    if declared is not None and declared > limit:
        raise _Refusal(413, 'payload_too_large')
    data = request.stream.read(limit + 1)
    if len(data) > limit:
        raise _Refusal(413, 'payload_too_large')
    return data


def _json_body():
    if request.mimetype != 'application/json':
        raise _Refusal(415, 'unsupported_media_type')
    try:
        body = json.loads(_read_limited(MAX_JSON_BYTES).decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise _Refusal(400, 'invalid') from None
    if not isinstance(body, dict):
        raise _Refusal(400, 'invalid')
    return body


def _text(body, name, *, required=True):
    value = body.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise _Refusal(400, 'invalid')
    return value


def _idempotency_key():
    key = request.headers.get('Idempotency-Key', '')
    if not 8 <= len(key) <= 255 or re.search(r'[\x00-\x1f]', key):
        raise _Refusal(400, 'invalid')
    return key


def _ingest_id(value):
    if not _INGEST_ID.match(value):
        raise _Refusal(404, 'not_found')
    return value


def read_embedded_author(stream: BinaryIO) -> str | None:
    """Read the fixed-width DSS author ID or the corroborated DS2 AUTHOR field."""
    header = stream.read(1536)
    magic = header[:4]
    size = {b'\x02dss': 1024, b'\x03dss': 1536, b'\x03ds2': 1536}.get(magic)
    if size is None:
        return None
    if len(header) < size:
        raise AuthorResolutionError('truncated_header')

    def field(offset: int, width: int) -> bytes:
        return header[offset:offset + width].partition(b'\0')[0].rstrip(b' ')

    author = field(0x0c, 16)
    if magic == b'\x03ds2':
        label, keyword = field(0x240, 30).lower(), field(0x25e, 30)
        if (author or label == b'author') and (not author or label != b'author' or author != keyword):
            raise AuthorResolutionError('contradictory_header')
    if not author:
        return None
    if re.fullmatch(rb'[A-Za-z0-9_.-]+', author) is None:
        raise AuthorResolutionError('invalid_header_author')
    return author.decode('ascii')


def create_agent_blueprint(
    policy: ChannelPolicy, ingest_service: IngestService, proxy_secret: str | bytes, *,
    device_map=None, alias_map=None, embedded_author_for=None, author_lookup=None,
) -> Blueprint:
    """Blueprint with the agent routes. ``proxy_secret`` is the secret shared with the trusted ingress.

    Author evidence providers are server-owned. The production lookup consumes
    embedded evidence from the verified upload stream, never inventory or finalize hints.
    """
    secret = proxy_secret.encode('utf-8') if isinstance(proxy_secret, str) else bytes(proxy_secret)
    if len(secret) < MIN_PROXY_SECRET_BYTES:
        raise ValueError('proxy secret must be at least %d bytes' % MIN_PROXY_SECRET_BYTES)
    bp = Blueprint('clinic_agent', __name__, url_prefix=PREFIX)

    @bp.before_request
    def authenticate():
        sent = request.headers.get(PROXY_SECRET_HEADER, '').encode('utf-8')
        if not hmac.compare_digest(sent, secret):
            return _problem(401, 'unauthorized')
        try:
            g.clinic_agent = agent_channel.authenticate_agent(request.headers.get(FINGERPRINT_HEADER))
        except AgentChannelError:
            return _problem(401, 'unauthorized')
        return None

    def handled(view):
        def run(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except _Refusal as refusal:
                return _problem(refusal.status, refusal.code)
            except AgentChannelError as exc:
                db.session.rollback()
                return _problem(_STATUS.get(exc.code, 400), exc.code)
            except IngestError as exc:
                db.session.rollback()
                return _problem(_INGEST_STATUS.get(exc.code, 400), exc.code)
            except SQLAlchemyError:
                db.session.rollback()
                logger.error('agent route %s: database error', request.endpoint)
                return _problem(503, 'dependency_unavailable')
            except Exception as exc:  # noqa: BLE001 - never echo internals; type only, no payload
                db.session.rollback()
                logger.error('agent route %s failed: %s', request.endpoint, type(exc).__name__)
                return _problem(500, 'internal_error')
        run.__name__ = view.__name__
        return run

    @bp.put('/inventory')
    @handled
    def inventory():
        body = _json_body()
        items = body.get('items', [])
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise _Refusal(400, 'invalid')
        return _ok(agent_channel.report_inventory(g.clinic_agent, policy, body))

    @bp.get('/intents')
    @handled
    def intents():
        return _ok(agent_channel.poll_intents(g.clinic_agent, policy))

    @bp.post('/ingests')
    @handled
    def begin():
        body = _json_body()
        size = body.get('declared_size')
        if not isinstance(size, int) or isinstance(size, bool):
            raise _Refusal(400, 'invalid')
        key = _idempotency_key()
        return _ok(agent_channel_ingest.begin_ingest(
            g.clinic_agent, policy, intent_id=_text(body, 'intent_id'), declared_size=size,
            declared_sha256=_text(body, 'declared_sha256').lower(), idempotency_key=key), 201)

    @bp.put('/ingests/<ingest_id>/chunks/<int:sequence>')
    @handled
    def chunk(ingest_id, sequence):
        ingest_id = _ingest_id(ingest_id)
        if request.mimetype != 'application/octet-stream':
            raise _Refusal(415, 'unsupported_media_type')
        digest = request.headers.get('X-Chunk-SHA256', '')
        offset_text = request.headers.get('X-Chunk-Offset', '')
        if not _SHA.match(digest) or not offset_text.isascii() or not offset_text.isdigit():
            raise _Refusal(400, 'invalid')
        data = _read_limited(policy.max_chunk_bytes)
        return _ok(agent_channel_ingest.put_chunk(
            g.clinic_agent, policy, ingest_id=ingest_id, sequence=sequence, offset=int(offset_text),
            data=data, chunk_sha256=digest))

    @bp.post('/ingests/<ingest_id>/finalize')
    @handled
    def finalize(ingest_id):
        ingest_id = _ingest_id(ingest_id)
        body = _json_body()
        _idempotency_key()
        chunks = body.get('chunks', [])
        if not isinstance(chunks, list) or not all(isinstance(c, dict) for c in chunks):
            raise _Refusal(400, 'invalid')
        return _ok(import_centre.finalize_delivery(
            g.clinic_agent, policy, ingest_id=ingest_id, body=body, ingest_service=ingest_service,
            device_map=device_map, alias_map=alias_map, embedded_author_for=embedded_author_for,
            verified_author=(read_embedded_author, author_lookup) if author_lookup is not None else None))

    @bp.get('/ingests/<ingest_id>')
    @handled
    def receipt(ingest_id):
        return _ok(agent_channel_ingest.get_receipt(g.clinic_agent, ingest_id=_ingest_id(ingest_id)))

    @bp.post('/ingests/<ingest_id>/abort')
    @handled
    def abort(ingest_id):
        return _ok(agent_channel_ingest.abort_ingest(g.clinic_agent, ingest_id=_ingest_id(ingest_id)))

    @bp.post('/outcomes')
    @handled
    def outcomes():
        body = _json_body()
        detail = _text(body, 'detail', required=False)
        return _ok(agent_channel_ingest.record_outcome(
            g.clinic_agent, intent_id=_text(body, 'intent_id'), outcome=_text(body, 'outcome'),
            ingest_id=_text(body, 'ingest_id', required=False), detail=detail or ''))

    return bp
