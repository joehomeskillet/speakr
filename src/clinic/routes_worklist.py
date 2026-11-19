"""HTTP adapters for the clinic worklist and its actor-owned saved filters."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from sys import maxsize
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from werkzeug.exceptions import HTTPException

from src.clinic import policy, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.list_layout import Group
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.clinic.saved_filters import FilterError
from src.clinic.worklist_etag import list_etag
from src.clinic.worklist_filters import SavedFilter, WorklistFilterStore, WorklistPage, query_worklist
from src.clinic.worklist_layout import LayoutStore, StoredLayout
from src.models import Recording
from src.services.recording_changes import conditional, updated_at_us

PREFIX = '/api/clinic/v1/worklist'
COLUMNS = (
    'dictation_id', 'title', 'status', 'author_user_id', 'priority', 'deadline_at',
    'assignee_user_id', 'assigned_at', 'notes', 'created_at', 'duration_seconds',
    'recipient', 'source_kind', 'open_mode', 'revision',
)
_DEFAULT_LAYOUT = {'version': 1, 'sort': [], 'group_by': [],
                   'columns': [{'key': key, 'width': 140, 'visible': True} for key in COLUMNS]}
_PROBLEMS = {400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
             401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
             404: ('NOT_FOUND', 'Nicht gefunden')}


@dataclass(frozen=True)
class WorklistEntry:
    """Server-loaded recording and its bound WorklistRow metadata (ISO UTC timestamps)."""

    recording: Recording
    row: Mapping
    pool_id: str | None = None


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
                **{'meta.notes': row['notes'], 'meta.recipient': row['recipient']})


def get_worklist(
    entries: Iterable[WorklistEntry], subject: policy.Subject, layouts: LayoutStore, *,
    spec: Mapping | None = None, search: str = '', page: int = 1, page_size: int = 20,
) -> WorklistSnapshot:
    """Apply current object policy, search and layout before counting or paging."""
    if not (type(page) is int and 1 <= page <= 100_000
            and type(page_size) is int and 1 <= page_size <= 100):
        raise FilterError('bad_value', 'invalid page')
    # The filter service must return all matches so layout ordering precedes paging.
    scoped = query_worklist((_query_row(entry) for entry in entries), subject, spec=spec,
                            query=search, keys=('title', 'meta.notes', 'meta.recipient'),
                            page_size=maxsize)
    # The layout sort is stable, so a unique base key makes ties independent of load order.
    stamps = {row['dictation_id']: row['recording_version'] for row in scoped.items}
    base = sorted(({key: row[key] for key in COLUMNS} for row in scoped.items),
                  key=lambda row: row['dictation_id'])
    layout, ordered, groups = layouts.refresh(subject.user_id, subject.user_id, base,
                                              _DEFAULT_LAYOUT, COLUMNS)
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
    result = WorklistPage(tuple(ordered[start:start + page_size]), scoped.total, page, page_size)
    # Include workflow-only changes as well as the existing recording change stamp.
    versions = [{'id': row['dictation_id'],
                 'version': json.dumps([stamps[row['dictation_id']], row], sort_keys=True)}
                for row in ordered]
    versions.append({'id': 'view', 'version': json.dumps(
        [subject.user_id, layout.version, layout.layout, page, page_size], sort_keys=True)})
    return WorklistSnapshot(result, layout, tuple(groups), statuses, list_etag(versions))


def create_blueprint(
    load_entries: Callable[[int], Iterable[WorklistEntry]],
    filters: WorklistFilterStore, layouts: LayoutStore,
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
        actor = current_user.id
        try:
            page = int(request.args.get('page', '1'))
            page_size = int(request.args.get('page_size', '20'))
        except ValueError:
            abort(400)
        try:
            filter_id = request.args.get('filter_id')
            spec = filters.get(actor, filter_id).spec if filter_id is not None else None
            snapshot = get_worklist(
                load_entries(actor), policy.build_subject(actor), layouts, spec=spec,
                search=request.args.get('search', ''), page=page, page_size=page_size,
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

    @blueprint.get('/layout')
    def own_layout():
        actor = current_user.id
        stored = layouts.load(actor, actor, _DEFAULT_LAYOUT, COLUMNS)
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
