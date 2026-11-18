"""Single browser command endpoint; domain services own policy and writes."""

from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from wtforms.validators import ValidationError

from src.clinic.config import clinic_mode_enabled

MAX_BODY_BYTES = 2 * 1024 * 1024
CONTRACTS = Path(__file__).resolve().parents[2] / 'clinic-spec' / 'contracts'
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.IGNORECASE)
_DATETIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:[0-5]\d)',
                       re.ASCII | re.IGNORECASE)
_TYPES = {'object': dict, 'array': list, 'integer': int, 'string': str, 'null': type(None)}
_KEYWORDS = frozenset('type const enum properties required additionalProperties items uniqueItems '
                      'minItems maxItems minLength maxLength minimum format'.split())
_PROBLEMS = {
    'INVALID_PAYLOAD': (400, 'Ungültige Anfrage'),
    'UNAUTHORIZED': (401, 'Anmeldung erforderlich'),
    'FORBIDDEN': (403, 'Zugriff verweigert'),
    'POLICY_DENIED': (403, 'Aktion nicht zulässig'),
    'REVISION_CONFLICT': (409, 'Versionskonflikt'),
    'IDEMPOTENCY_CONFLICT': (409, 'Anfrageschlüssel bereits verwendet'),
    'CAPABILITY_UNAVAILABLE': (422, 'Funktion nicht verfügbar'),
    'RATE_LIMITED': (429, 'Zu viele Anfragen'),
    'DEPENDENCY_UNAVAILABLE': (503, 'Dienst nicht verfügbar'),
}

JSON = dict[str, Any]
RunOnce = Callable[[tuple[int, str, str, str], str, Callable[[], JSON]], JSON]
Authorize = Callable[[int, str, JSON], str | None]
Revision = Callable[[str], int | None]
Execute = Callable[[int, str, JSON], JSON]


class CommandProblem(Exception):
    """An adapter refusal with a public OpenAPI problem code."""


def _problem(code: str):
    status, title = _PROBLEMS[code]
    response = jsonify(type='about:blank', title=title, status=status, code=code)
    response.mimetype = 'application/problem+json'
    return response, status


def _valid(value: Any, schema: JSON) -> bool:
    """Validate the keyword subset used by the pinned command schemas."""
    if schema.keys() - _KEYWORDS:
        raise ValueError('Unsupported parity schema keyword')
    kinds = schema.get('type')
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds and type(value) not in {_TYPES[kind] for kind in kinds}:
        return False
    if ('const' in schema and value != schema['const']) or ('enum' in schema and value not in schema['enum']):
        return False
    if isinstance(value, dict):
        props = schema.get('properties', {})
        return (not value.keys() - props.keys() and not set(schema.get('required', ())) - value.keys()
                and all(_valid(v, props[k]) for k, v in value.items()))
    if isinstance(value, list):
        return (schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000)
                and all(_valid(v, schema['items']) for v in value)
                and (not schema.get('uniqueItems') or len(set(value)) == len(value)))
    if type(value) is int:
        return schema.get('minimum', -(2**63)) <= value < 2**63
    if isinstance(value, str):
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', MAX_BODY_BYTES):
            return False
        if schema.get('format') == 'uuid':
            return bool(_UUID.fullmatch(value))
        if schema.get('format') == 'date-time':
            try:
                return bool(_DATETIME.fullmatch(value) and datetime.fromisoformat(value.upper()).tzinfo)
            except ValueError:
                return False
    return True


def _unique_object(pairs: list[tuple[str, Any]]) -> JSON:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON field')
    return result


class ParityDispatch:
    """Register catalog adapters, then mount ``blueprint`` once.

    authorize(actor_id, permission, payload) checks current action/object scope
    and semantics and returns a canonical target key (None denies).
    revision(target) returns the locked current positive revision or None for
    an authorized creation. execute(actor_id, target, envelope) applies the
    existing service checks and returns the OpenAPI result.

    run_once(scope, digest, operation) must serialize by scope, return the
    durable result on an exact replay, and raise IDEMPOTENCY_CONFLICT for a
    mismatched digest. It must commit the receipt and local state/job/audit
    together, and roll back on failure. Keep the revision locked through the
    write, or use the service's compare-and-set. No external delivery here.
    """

    def __init__(self, run_once: RunOnce):
        self._run_once = run_once
        catalog = json.loads((CONTRACTS / 'parity-command-catalog.json').read_text(encoding='utf-8'))['commands']
        api = json.loads((CONTRACTS / 'parity-api.openapi.json').read_text(encoding='utf-8'))
        self._catalog = {row['command']: row for row in catalog}
        if len(self._catalog) != len(catalog) or catalog != api['x-command-authorization']:
            raise ValueError('Parity command contracts disagree')
        self._schemas = api['components']['schemas']
        self._handlers: dict[str, tuple[Authorize, Revision, Execute]] = {}
        self.blueprint = Blueprint('clinic_parity', __name__)
        self.blueprint.add_url_rule('/api/clinic/v1/parity/commands', 'execute', self._execute, methods=['POST'])
        self.blueprint.register_error_handler(CSRFError, lambda _error: _problem('FORBIDDEN'))

    def register(self, command: str, *, authorize: Authorize, revision: Revision, execute: Execute) -> None:
        if command not in self._catalog or command in self._handlers:
            raise ValueError(f'Unknown or duplicate parity handler: {command}')
        self._handlers[command] = authorize, revision, execute

    def _execute(self):
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            return _problem('UNAUTHORIZED')
        try:
            validate_csrf(request.headers.get('X-CSRF-Token'))
        except ValidationError:
            return _problem('FORBIDDEN')
        key = request.headers.get('Idempotency-Key', '')
        if not 8 <= len(key) <= 128 or not key.strip() or not request.is_json:
            return _problem('INVALID_PAYLOAD')
        request.max_content_length = min(MAX_BODY_BYTES, request.max_content_length or MAX_BODY_BYTES)
        try:
            body = json.loads(request.get_data().decode('utf-8'), object_pairs_hook=_unique_object)
        except (BadRequest, RequestEntityTooLarge, ValueError, RecursionError):
            return _problem('INVALID_PAYLOAD')
        command = body.get('command') if isinstance(body, dict) else None
        row = self._catalog.get(command) if isinstance(command, str) else None
        if row is None or not _valid(body, self._schemas[row['request_schema']]):
            return _problem('INVALID_PAYLOAD')
        if command not in self._handlers:
            return _problem('CAPABILITY_UNAVAILABLE')
        authorize, revision, execute = self._handlers[command]
        actor = current_user.id

        try:
            target = authorize(actor, row['permission'], body['payload'])
            if not target:
                raise CommandProblem('POLICY_DENIED')
            digest = sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

            def operation() -> JSON:
                current = revision(target)
                if current is not None and (type(current) is not int or current < 1):
                    raise RuntimeError('Invalid parity target revision')
                if (0 if current is None else current) != body['expected_revision']:
                    raise CommandProblem('REVISION_CONFLICT')
                result = execute(actor, target, body)
                if not _valid(result, self._schemas['CommandResult']) or result['request_id'] != body['request_id']:
                    raise RuntimeError('Invalid parity command result')
                return result

            return jsonify(self._run_once((actor, command, target, key), digest, operation))
        except CommandProblem as error:
            return _problem(str(error))
