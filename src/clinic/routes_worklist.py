"""HTTP adapters for the clinic worklist and its actor-owned saved filters."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from sys import maxsize
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError
from werkzeug.exceptions import HTTPException

from src.clinic import policy, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.list_layout import Group
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.clinic.presets import ClinicMetadataField
from src.clinic.saved_filters import FilterError, validate_filter
from src.clinic.worklist_etag import list_etag
from src.clinic.worklist_filters import SavedFilter, WorklistFilterStore, WorklistPage, query_worklist
from src.clinic.worklist_layout import LayoutStore, StoredLayout
from src.database import db
from src.models import Recording, User
from src.services.recording_changes import conditional, updated_at_us

PREFIX = '/api/clinic/v1/worklist'
COLUMNS = (
    'dictation_id', 'title', 'status', 'author_user_id', 'priority', 'deadline_at',
    'assignee_user_id', 'assigned_at', 'notes', 'created_at', 'duration_seconds',
    'recipient', 'source_kind', 'open_mode', 'revision',
)
_DEFAULT_LAYOUT = {'version': 1, 'sort': [], 'group_by': [],
                   'columns': [{'key': key, 'width': 140, 'visible': True} for key in COLUMNS]}
# Additive row fields (v8-119): the role fields the Aufnahmen API returns, set by the loader.
ROLE_COLUMNS = ('dictated_by', 'staff_pools', 'lease_holder', 'source_label', 'asr_state')
LAYOUT_COLUMNS = COLUMNS + ('dictated_by', 'staff_pools', 'lease_holder')
# WorklistRow.source_kind describes the medium.
# ClinicOriginal receipt origin -> German source label; unknown origins get no label.
ORIGIN_LABELS = {'web-recorder': 'Mikrofon', 'web-upload': 'Upload', 'agent': 'Import',
                 'batch': 'Import', 'legacy-backfill': 'Import', 'live': 'Live'}
_PROBLEMS = {400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
             401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
             404: ('NOT_FOUND', 'Nicht gefunden')}


def layout_columns(metadata_fields: Sequence[ClinicMetadataField]) -> tuple[str, ...]:
    return LAYOUT_COLUMNS + tuple(metadata_field.name for metadata_field in metadata_fields)


@dataclass(frozen=True)
class WorklistEntry:
    """Server-loaded recording and its bound WorklistRow metadata (ISO UTC timestamps)."""

    recording: Recording
    row: Mapping
    pool_id: str | None = None
    metadata_xml: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class WorklistSnapshot:
    page: WorklistPage
    layout: StoredLayout
    groups: tuple[Group, ...]
    statuses: tuple[workflow.WorkGroup, ...]
    cursor: str


def _query_row(entry: WorklistEntry) -> dict:
    row = entry.row
    rec = entry.recording
    facts = policy.build_facts(rec.id, rec.user_id)
    return dict(row, id=facts.recording_id, owner_id=facts.owner_id, pool_ids=facts.pool_ids,
                author=row['author_user_id'], assignee=row['assignee_user_id'], pool=entry.pool_id,
                created=row['created_at'][:10], deadline=(row['deadline_at'] or '')[:10] or None,
                audio_seconds=row['duration_seconds'], recording_version=updated_at_us(rec),
                metadata_xml=entry.metadata_xml,
                **{'meta.notes': row['notes'], 'meta.recipient': row['recipient']})


def _with_names(rows: list[Mapping]) -> list[dict]:
    """For loader rows (those carrying the role fields) add assignee {id, display_name} to projected rows.

    Names come only from users that take part in a row the policy already admitted
    (author, assignee, lease holder); a user that cannot be resolved has no name.
    """
    from src.clinic.dictation_roles import _chunks, _person  # lazy: avoids the import cycle

    ids = sorted({row['assignee_user_id'] for row in rows
                  if 'dictated_by' in row and row['assignee_user_id'] is not None})
    users: dict = {}
    for part in _chunks(ids):
        users.update({user.id: user for user in db.session.query(User).filter(User.id.in_(part))})
    result = []
    for row in rows:
        if 'dictated_by' not in row:  # not built by the loader: keep the plain projection
            result.append(dict(row))
            continue
        assignee_id = row['assignee_user_id']
        assignee = None if assignee_id is None else (
            _person(users.get(assignee_id)) or {'id': str(assignee_id), 'display_name': None})
        result.append(dict(row, assignee=assignee))
    return result


def get_worklist(
    entries: Iterable[WorklistEntry], subject: policy.Subject, layouts: LayoutStore, *,
    spec: Mapping | None = None, search: str = '', page: int = 1, page_size: int = 20,
    load_case_displays: Callable[..., Mapping] | None = None, case_ref: str | None = None,
    metadata_fields: Sequence[ClinicMetadataField] = (),
) -> WorklistSnapshot:
    """Apply current object policy, search and layout before counting or paging."""
    from src.clinic.worklist_entries import project_metadata_values

    if not (type(page) is int and 1 <= page <= 100_000
            and type(page_size) is int and 1 <= page_size <= 100):
        raise FilterError('bad_value', 'invalid page')
    # The filter service must return all matches so layout ordering precedes paging.
    candidates = (_query_row(entry) for entry in entries
                  if case_ref is not None or entry.row['status'] != workflow.TO_ARCHIVE)
    scoped = query_worklist(candidates, subject, spec=spec,
                            query=search, keys=('title', 'meta.notes', 'meta.recipient'),
                            page_size=maxsize)
    # The layout sort is stable, so a unique base key makes ties independent of load order.
    matches = scoped.items
    displays = {}
    if case_ref is not None:
        displays = (load_case_displays([row['dictation_id'] for row in matches], trusted_case=case_ref)
                    if case_ref and load_case_displays else {})
        matches = tuple(row for row in matches if row['dictation_id'] in displays)
    stamps = {row['dictation_id']: row['recording_version'] for row in matches}
    base = []
    for source in sorted(matches, key=lambda row: row['dictation_id']):
        row = {key: source[key] for key in COLUMNS + ROLE_COLUMNS if key in source}
        if metadata_fields:
            facts = policy.RecordingFacts(source['id'], source['owner_id'], source['pool_ids'])
            row.update(project_metadata_values(subject, facts, metadata_fields, source['metadata_xml']))
        base.append(row)
    layout, ordered, groups = layouts.refresh(subject.user_id, subject.user_id, base,
                                              _DEFAULT_LAYOUT, layout_columns(metadata_fields))
    ordered = _with_names(ordered)
    buckets: dict[str, list[Mapping]] = {}
    for row in ordered:
        buckets.setdefault(row['status'], []).append(row)
    statuses = tuple(
        workflow.WorkGroup(status, workflow.LABELS[status], len(items),
                           sum(row['duration_seconds'] for row in items), status == workflow.DONE,
                           tuple(row['dictation_id'] for row in items))
        for status in workflow.STATUS_ORDER if (items := buckets.get(status))
    )
    start = (page - 1) * page_size
    page_rows = ordered[start:start + page_size]
    if case_ref is None and load_case_displays and page_rows:
        displays = load_case_displays([row['dictation_id'] for row in page_rows])
    for row in page_rows:
        display = displays.get(row['dictation_id'])
        if display:
            row['case_display'] = display
    result = WorklistPage(tuple(page_rows), len(matches), page, page_size)
    # Include workflow-only changes as well as the existing recording change stamp.
    versions = [{'id': row['dictation_id'],
                 'version': json.dumps([stamps[row['dictation_id']], row], sort_keys=True)}
                for row in ordered]
    versions.append({'id': 'view', 'version': json.dumps(
        [subject.user_id, layout.version, layout.layout, page, page_size], sort_keys=True)})
    return WorklistSnapshot(result, layout, tuple(groups), statuses, list_etag(versions))


def create_blueprint(
    load_entries: Callable[[int], Iterable[WorklistEntry]],
    filters: WorklistFilterStore, layouts: LayoutStore, *,
    load_case_displays: Callable[..., Mapping] | None = None,
    load_metadata_fields: Callable[[], Sequence[ClinicMetadataField]] = tuple,
) -> Blueprint:
    """Mount with a trusted loader that narrows writer assignments and clarification scope.

    The loader binds workflow metadata to its recording and applies live staff
    routing (including W13). Every candidate still passes the live view policy here.
    The separate admin overview owns the W12 full-access path.
    """
    blueprint = Blueprint('clinic_worklist', __name__, url_prefix=PREFIX)

    def problem(error: HTTPException):
        status = error.code or 400
        code, title = _PROBLEMS[status]
        response = jsonify(type='about:blank', status=status, code=code, title=title,
                           correlation_id=str(uuid4()))
        response.mimetype = 'application/problem+json'
        return response, status

    for status in _PROBLEMS:
        blueprint.register_error_handler(status, problem)

    @blueprint.before_request
    def require_browser_session():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)

    @blueprint.after_request
    def no_store(response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @blueprint.get('/rows')
    def rows():
        from .case_handoff import VIEW_HEADER, case_reference

        actor = current_user.id
        try:
            page = int(request.args.get('page', '1'))
            page_size = int(request.args.get('page_size', '20'))
        except ValueError:
            abort(400)
        try:
            filter_id = request.args.get('filter_id')
            spec = filters.get(actor, filter_id).spec if filter_id is not None else None
            marker = request.headers.get(VIEW_HEADER)
            case_ref = case_reference(marker) if marker is not None else None
            snapshot = get_worklist(
                load_entries(actor) if case_ref != '' else (), policy.build_subject(actor), layouts, spec=spec,
                search=request.args.get('search', ''), page=page, page_size=page_size,
                load_case_displays=load_case_displays, case_ref=case_ref,
                metadata_fields=load_metadata_fields(),
            )
        except FilterError as error:
            abort(404 if error.code == 'not_found' else 400)
        result = snapshot.page
        return conditional(request, snapshot.cursor, lambda: jsonify(
            items=result.items, total=result.total, page=result.page, page_size=result.page_size))

    @blueprint.get('/filters')
    def own_filters():
        return jsonify(items=[{'id': item.id, 'name': item.name, 'version': item.version}
                              for item in filters.list(current_user.id)])

    def filter_problem(status: int, code: str, title: str, **extra):
        response = jsonify(type='about:blank', status=status, code=code, title=title,
                           correlation_id=str(uuid4()), **extra)
        response.mimetype = 'application/problem+json'
        return response, status

    def filter_body(item: SavedFilter) -> dict:
        return {'id': item.id, 'name': item.name, 'version': item.version, 'spec': item.spec}

    def if_match() -> int | None:
        raw = (request.headers.get('If-Match') or '').strip()
        if raw[:2] == 'W/':
            raw = raw[2:]
        raw = raw.strip('"')
        return int(raw) if raw.isascii() and raw.isdigit() else None

    def run_filter_write(filter_id: str | None, action: Callable[[], object]):
        """Run one store write for the session actor; map store errors to problems."""
        try:
            validate_csrf(request.headers.get('X-CSRF-Token'))
        except ValidationError:
            return filter_problem(403, 'FORBIDDEN', 'Ungültiges CSRF-Token')
        try:
            return action()
        except FilterError as error:
            if error.code == 'not_found':
                abort(404)
            if error.code == 'stale_version':
                current = filters.get(current_user.id, filter_id)
                return filter_problem(409, 'stale_version', 'Versionskonflikt',
                                      id=current.id, version=current.version)
            if error.code in ('duplicate_name', 'limit_reached'):
                return filter_problem(409, error.code, 'Konflikt')
            abort(400)
        except OSError:
            return filter_problem(503, 'DEPENDENCY_UNAVAILABLE', 'Dienst nicht verfügbar')

    @blueprint.get('/filters/<filter_id>')
    def own_filter(filter_id: str):
        try:
            return jsonify(filter_body(filters.get(current_user.id, filter_id)))
        except FilterError:
            abort(404)

    @blueprint.post('/filters')
    def create_filter():
        payload = request.get_json(silent=True)
        if not (isinstance(payload, dict) and isinstance(payload.get('name'), str)
                and isinstance(payload.get('spec'), dict)):
            abort(400)
        response = run_filter_write(None, lambda: (
            jsonify(filter_body(filters.create(current_user.id, payload['name'], payload['spec']))),
            201))
        return response

    @blueprint.put('/filters/<filter_id>')
    def update_filter(filter_id: str):
        payload = request.get_json(silent=True)
        if not (isinstance(payload, dict) and payload and set(payload) <= {'name', 'spec'}
                and isinstance(payload.get('name', ''), str)
                and isinstance(payload.get('spec', {}), dict)):
            abort(400)
        version = if_match()
        if version is None:
            abort(400)

        def apply():
            actor = current_user.id
            if 'spec' in payload:
                validate_filter(payload['spec'])
            saved = filters.get(actor, filter_id)
            if 'name' in payload:
                saved = filters.rename(actor, filter_id, payload['name'], expected_version=version)
                version_now = saved.version
            else:
                version_now = version
            if 'spec' in payload:
                saved = filters.replace(actor, filter_id, payload['spec'], expected_version=version_now)
            return jsonify(filter_body(saved))

        return run_filter_write(filter_id, apply)

    @blueprint.delete('/filters/<filter_id>')
    def delete_filter(filter_id: str):
        version = if_match()
        if version is None:
            abort(400)

        def apply():
            filters.delete(current_user.id, filter_id, expected_version=version)
            return '', 204

        return run_filter_write(filter_id, apply)

    @blueprint.get('/layout')
    def own_layout():
        actor = current_user.id
        stored = layouts.load(actor, actor, _DEFAULT_LAYOUT, layout_columns(load_metadata_fields()))
        return jsonify(version=stored.version, layout=stored.layout)

    return blueprint


def register_save_filter(
    dispatch: ParityDispatch, create_filter: Callable[[int, str, Mapping], SavedFilter],
) -> None:
    """Bind actor-owned creation; the caller supplies the audited transactional writer."""

    def execute(actor: int, _target: str, envelope: dict) -> dict:
        payload = envelope['payload']
        criteria = payload['criteria']
        spec = {'all': [{'field': field, 'op': 'in', 'value': criteria[key]}
                        for key, field in (('author_ids', 'author'), ('statuses', 'status'))
                        if key in criteria]}
        try:
            saved = create_filter(actor, payload['name'], spec)
        except FilterError as error:
            code = {'duplicate_name': 'REVISION_CONFLICT', 'limit_reached': 'CAPABILITY_UNAVAILABLE'}
            raise CommandProblem(code.get(error.code, 'INVALID_PAYLOAD')) from error
        except OSError as error:
            raise CommandProblem('DEPENDENCY_UNAVAILABLE') from error
        return {'request_id': envelope['request_id'], 'command_id': str(uuid4()),
                'status': 'committed', 'revision': saved.version}

    dispatch.register('save_filter',
                      authorize=lambda actor, permission, _: f'preferences:{actor}'
                      if permission == 'clinic.preferences' else None,
                      revision=lambda _target: None, execute=execute)
