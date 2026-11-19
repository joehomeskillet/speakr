from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import policy, workflow
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.clinic.routes_worklist import COLUMNS, PREFIX, WorklistEntry, create_blueprint, get_worklist, register_save_filter
from src.clinic.worklist_filters import WorklistFilterStore
from src.clinic.worklist_layout import LayoutStore
from src.models import Recording

COMMANDS = '/api/clinic/v1/parity/commands'
ROWS = f'{PREFIX}/rows'


def entry(rid, *, owner=7, status=workflow.TO_WRITE, title='Test', notes='Hinweis'):
    rec = Recording(id=rid, user_id=owner, created_at=datetime(2026, 1, 1))
    row = dict(dictation_id=str(UUID(int=rid)), title=title, status=status, author_user_id=owner,
               priority=1, deadline_at=None, assignee_user_id=None, assigned_at=None, notes=notes,
               created_at='2026-01-01T00:00:00Z', duration_seconds=rid * 10, recipient=None,
               source_kind='audio', open_mode='author_case', revision=1, transcript='Nicht durchsuchbar')
    return WorklistEntry(rec, row, 'pool-a')


def test_snapshot_scopes_totals_and_applies_layout_before_paging():
    entries = [entry(3), entry(2, status=workflow.DONE), entry(1), entry(4, owner=8), entry(5)]
    subject = policy.Subject(7, denies=(policy.Deny(recording_ids=frozenset({5})),))
    layouts = LayoutStore()
    layout = {'version': 1, 'columns': [], 'sort': [['status', 'asc']], 'group_by': ['status']}
    layouts.save(7, 7, layout, COLUMNS, expected_version=0)
    first = get_worklist(entries, subject, layouts, page_size=1)
    second = get_worklist(reversed(entries), subject, layouts, page=2, page_size=1)
    assert first.layout.layout['sort'] == layout['sort']
    assert first.layout.layout['group_by'] == layout['group_by']
    assert first.page.items[0]['dictation_id'] == entries[1].row['dictation_id']
    assert second.page.items[0]['dictation_id'] == entries[2].row['dictation_id']
    assert first.page.total == second.page.total == 3
    assert [(g.label, g.count, g.total_audio_seconds, g.finished) for g in first.statuses] == [
        ('Zu schreiben', 2, 40, False), ('Fertiggestellt', 1, 20, True),
    ]
    assert [group.key_path for group in first.groups] == [(workflow.DONE,), (workflow.TO_WRITE,)]
    assert set(first.groups[0].rows[0]) == set(COLUMNS)
    assert get_worklist(reversed(entries), subject, layouts, page_size=1).cursor == first.cursor
    assert get_worklist(entries, policy.Subject(8), layouts).layout.version == 0


@pytest.fixture
def endpoint(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-worklist-test')
    login = LoginManager(app)
    entries = [entry(1, title='Befund'), entry(2, notes='Termin'), entry(3, owner=8, title='Befund')]
    fail = set()

    def write(_snapshot):
        if fail:
            raise OSError('synthetic write failure')

    filters, layouts = WorklistFilterStore(writer=write), LayoutStore()
    app.register_blueprint(create_blueprint(lambda _actor: entries, filters, layouts))
    receipts = {}

    def once(scope, digest, operation):
        if scope in receipts:
            old_digest, result = receipts[scope]
            if old_digest != digest:
                raise CommandProblem('IDEMPOTENCY_CONFLICT')
            return result
        result = operation()
        receipts[scope] = digest, result
        return result

    dispatch = ParityDispatch(once)
    register_save_filter(dispatch, filters.create)
    app.register_blueprint(dispatch.blueprint)

    class Actor(UserMixin):
        def __init__(self, uid):
            self.id = int(uid)

    login.user_loader(Actor)
    login.request_loader(lambda req: Actor(7) if req.headers.get('Authorization') else None)

    @app.get('/synthetic-login/<int:uid>')
    def browser(uid):
        login_user(Actor(uid))
        return {'csrf': generate_csrf()}

    return app, entries, filters, fail


def test_http_scoped_search_filter_and_refresh(endpoint, monkeypatch):
    app, entries, filters, _ = endpoint
    with app.test_client() as client:
        assert client.get(ROWS).status_code == 401
        assert client.get(ROWS, headers={'Authorization': 'synthetic'}).status_code == 401
        client.get('/synthetic-login/7')
        layout = client.get(f'{PREFIX}/layout').json
        assert layout['version'] == 0
        assert {column['key'] for column in layout['layout']['columns']} == set(COLUMNS)
        assert client.get(f'{ROWS}?page_size=101').status_code == 400
        own = filters.create(7, 'Offen', {'all': [{'field': 'pool', 'op': 'eq', 'value': 'pool-a'}]})
        filters.create(8, 'Fremd', {})
        foreign = filters.create(8, 'Nur fremd', {})
        assert client.get(f'{ROWS}?filter_id={foreign.id}').status_code == 404
        assert client.get(f'{ROWS}?search=durchsuchbar').json['total'] == 0
        assert client.get(f'{ROWS}?search=termin').json['total'] == 1
        url = f'{ROWS}?filter_id={own.id}&search=Befund+Hinweis&actor=8&role=admin'
        first = client.get(url)
        assert first.json == {'items': [{key: entries[0].row[key] for key in COLUMNS}],
                              'total': 1, 'page': 1, 'page_size': 20}
        assert first.headers['Cache-Control'] == 'private, no-store'
        headers = {'If-None-Match': first.headers['ETag']}
        assert client.get(url, headers=headers).status_code == 304
        entries[2].recording.updated_at = datetime(2026, 1, 2)
        assert client.get(url, headers=headers).status_code == 304
        entries[0].recording.updated_at = entries[0].recording.created_at + timedelta(seconds=1)
        changed = client.get(url, headers=headers)
        assert changed.status_code == 200 and changed.headers['ETag'] != headers['If-None-Match']
        entries[0].row['status'] = workflow.DONE
        assert client.get(url).headers['ETag'] != changed.headers['ETag']
        monkeypatch.delenv('CLINIC_MODE')
        assert client.get(url).status_code == 404


def test_save_filter_rejects_forgery_stale_and_failed_writes_then_replays(endpoint):
    app, _, filters, fail = endpoint
    body = {'command': 'save_filter', 'request_id': str(uuid4()), 'expected_revision': 0,
            'payload': {'name': 'Offen', 'criteria': {'author_ids': [7], 'statuses': [workflow.TO_WRITE]}}}
    with app.test_client() as client:
        assert client.post(COMMANDS, json=body).status_code == 401
        token = client.get('/synthetic-login/7').json['csrf']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': 'synthetic-save'}
        assert client.post(COMMANDS, json=body).status_code == 403
        forged = dict(body, payload=dict(body['payload'], actor=8))
        assert client.post(COMMANDS, headers=headers, json=forged).status_code == 400
        stale = client.post(COMMANDS, headers=headers, json=dict(body, expected_revision=1))
        assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
        fail.add(True)
        failed = client.post(COMMANDS, headers=headers, json=body)
        assert (failed.status_code, failed.json['code']) == (503, 'DEPENDENCY_UNAVAILABLE')
        assert filters.list(7) == []
        fail.clear()
        first = client.post(COMMANDS, headers=headers, json=body)
        assert first.status_code == 200 and first.json['revision'] == 1
        assert client.post(COMMANDS, headers=headers, json=body).json == first.json
        changed = client.post(COMMANDS, headers=headers, json=dict(body, request_id=str(uuid4())))
        assert (changed.status_code, changed.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
        saved, = filters.list(7)
        assert filters.list(8) == []
        assert client.get(f'{ROWS}?filter_id={saved.id}').json['total'] == 2
        assert client.get(f'{PREFIX}/filters').json == {'items': [{'id': saved.id, 'name': 'Offen', 'version': 1}]}
