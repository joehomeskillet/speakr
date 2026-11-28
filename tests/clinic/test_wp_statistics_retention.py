"""P-152 exercises the optional collection routes with synthetic app actions."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import generate_csrf

from src.clinic import routes_history, routes_recordings, routes_statistics
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.models import AuditEvent, User
from src.services import audit

from test_pools import _user, pool_db as pool_db
from test_wp_v8_statistics_routes import _account, _dictation, _login, db, stats_app as stats_app


PREFIX = '/api/clinic/v1/statistics'
COLLECTION = f'{PREFIX}/collection'
RETENTION = f'{PREFIX}/retention'
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def scene(stats_app, pool_db, monkeypatch):
    monkeypatch.setenv('CLINIC_PROFILE', 'staging')
    stats_app.register_blueprint(routes_recordings.create_blueprint(lambda _actor: ()))
    stats_app.register_blueprint(routes_history.create_blueprint(
        authorize=lambda user, _action, row: user.id == row.author_user_id,
        run_once=None, dictation_snapshot=None,
    ))

    @stats_app.get('/test-csrf')
    def csrf():
        return generate_csrf()

    admin = _user(admin=True)
    db.session.add(ClinicAccountPolicy(user_id=admin.id, status='active'))
    db.session.commit()
    clock = [START]
    monkeypatch.setattr(routes_statistics, '_now', lambda: clock[0])
    operator = _login(stats_app, admin)
    operator.environ_base['HTTP_X_CSRFTOKEN'] = operator.get('/test-csrf').text
    return stats_app, operator, clock


def _switch(client, kind, enabled, days=1):
    response = client.put(COLLECTION, json={'kind': kind, 'enabled': enabled, 'retention_days': days})
    assert response.status_code == 200


def _period(client, kind, start, end=None):
    params = {'from': start.isoformat()}
    if end is not None:
        params['to'] = end.isoformat()
    response = client.get(f'{PREFIX}/{kind}', query_string=params)
    assert response.status_code == 200
    return response.json


def test_collection_switches_control_real_actions_and_preserve_off_windows(scene):
    app, operator, clock = scene
    author = _account()
    row = _dictation(author, START)
    browser = _login(app, author)
    history_url = f'/api/clinic/v1/dictations/{row.id}/history'

    assert browser.get(routes_recordings.PREFIX).status_code == 200
    assert browser.get(history_url).status_code == 200
    clock[0] += timedelta(hours=1)
    usage_start = clock[0]
    _switch(operator, 'usage', True)
    assert browser.get(routes_recordings.PREFIX).status_code == 200
    assert browser.get(history_url).status_code == 200
    clock[0] += timedelta(hours=1)
    history_start = clock[0]
    _switch(operator, 'history', True)
    _switch(operator, 'usage', False)
    assert browser.get(routes_recordings.PREFIX).status_code == 200
    assert browser.get(history_url).status_code == 200
    assert browser.get(f'/api/clinic/v1/dictations/{uuid4()}/history').status_code == 404
    clock[0] += timedelta(hours=1)

    # A separate app instance sees the same policies and samples via the database.
    other_app = Flask('retention-second-worker')
    other_app.config.update(app.config)
    db.init_app(other_app)
    LoginManager(other_app).user_loader(lambda uid: db.session.get(User, int(uid)))
    other_app.register_blueprint(routes_statistics.blueprint)
    with operator.session_transaction() as first, other_app.test_client() as reader:
        with reader.session_transaction() as second:
            second.update(first)
        flags = reader.get(COLLECTION).json
        assert flags == {'usage': {'enabled': False, 'retention_days': 1},
                         'history': {'enabled': True, 'retention_days': 1}}
        off = _period(reader, 'usage', START, usage_start)
        assert (off['coverage'], off['count'], off['status']) == ('uncollected', None, 'nicht erfasst')
        assert _period(reader, 'usage', usage_start, history_start)['count'] == 1
        assert _period(reader, 'usage', history_start)['count'] is None
        assert _period(reader, 'history', START, history_start)['count'] is None
        assert _period(reader, 'history', history_start)['count'] == 1
        mixed = _period(reader, 'history', START)
        assert (mixed['coverage'], mixed['count']) == ('partial', 1)


def test_expiry_preview_and_cleanup_leave_dictations_and_audit_intact(scene):
    app, operator, clock = scene
    author = _account()
    row = _dictation(author, START)
    browser = _login(app, author)
    _switch(operator, 'usage', True)
    _switch(operator, 'history', True, days=2)
    assert browser.get(routes_recordings.PREFIX).status_code == 200
    assert browser.get(f'/api/clinic/v1/dictations/{row.id}/history').status_code == 200
    clock[0] += timedelta(hours=12)
    _switch(operator, 'usage', True, days=3)
    assert browser.get(routes_recordings.PREFIX).status_code == 200

    clock[0] = START + timedelta(days=1)
    audit.flush()
    db.session.expire_all()
    audit_ids = {event.id for event in AuditEvent.query.all()}
    assert audit_ids
    expected = {'usage': 1, 'history': 0}
    for _ in range(2):
        reply = operator.get(RETENTION)
        assert reply.status_code == 200
        assert reply.json == {'expired': expected}
    reply = operator.post(RETENTION)
    assert reply.status_code == 200
    assert reply.json == {'deleted': expected}
    assert operator.post(RETENTION).json == {'deleted': {'usage': 0, 'history': 0}}
    assert _period(operator, 'usage', START)['count'] == 1
    expired_window = _period(operator, 'usage', START, START + timedelta(hours=12))
    assert expired_window['retention_limited'] is True
    assert expired_window['zero_activity'] is False
    assert _period(operator, 'history', START)['count'] == 1
    db.session.expire_all()
    assert db.session.get(ClinicDictation, row.id) is not None
    assert {event.id for event in AuditEvent.query.all()} == audit_ids


def test_statistics_policy_requires_active_admin_csrf_and_staging(scene, monkeypatch):
    app, operator, _clock = scene
    reader = _login(app, _account())
    assert app.test_client().get(COLLECTION).status_code == 401
    assert reader.get(f'{PREFIX}/usage').status_code == 403
    assert reader.post(RETENTION).status_code == 403
    for data in ({'kind': [], 'enabled': True, 'retention_days': 1},
                 {'kind': 'usage', 'enabled': 'true', 'retention_days': 1},
                 {'kind': 'usage', 'enabled': True, 'retention_days': True},
                 {'kind': 'usage', 'enabled': True, 'retention_days': 10**9}):
        assert operator.put(COLLECTION, json=data).status_code == 400
    assert operator.get(f'{PREFIX}/usage?from=2026-01-01T00:00:00').status_code == 400
    del operator.environ_base['HTTP_X_CSRFTOKEN']
    assert operator.put(COLLECTION, json={'kind': 'usage', 'enabled': True, 'retention_days': 1}).status_code == 403
    assert operator.post(RETENTION).status_code == 403
    monkeypatch.setenv('CLINIC_PROFILE', 'production')
    assert operator.get(COLLECTION).status_code == 404
