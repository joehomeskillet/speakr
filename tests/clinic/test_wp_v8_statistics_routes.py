"""Statistics route (v8-30): 403 without scope, service aggregate shape, Zurich bounds.

Synthetic users and dictations in the shared clinic test database.
"""

from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user

from src.app import app as main_app, db
from src.clinic import statistics_access
from src.clinic.identity import ClinicAccountPolicy, create_tables as create_identity_tables
from src.clinic.models import ClinicDictation
from src.clinic.routes_statistics import PATH, blueprint
from src.models import Recording, User

from test_pools import _user, pool_db as pool_db


@pytest.fixture
def stats_app(clinic_on):
    create_identity_tables()
    statistics_access.clear_grants()
    app = Flask('stats-routes-test')
    app.config.update(TESTING=True, SECRET_KEY='synthetic-test-key',
                      SQLALCHEMY_DATABASE_URI=main_app.config['SQLALCHEMY_DATABASE_URI'])
    db.init_app(app)
    LoginManager(app).user_loader(lambda uid: db.session.get(User, int(uid)))

    @app.get('/test-session/<int:uid>')
    def browser_session(uid):
        login_user(db.session.get(User, uid))
        return jsonify(ok=True)

    app.register_blueprint(blueprint)
    yield app
    statistics_access.clear_grants()


def _account(status='active'):
    user = _user()
    db.session.add(ClinicAccountPolicy(user_id=user.id, status=status))
    db.session.commit()
    return user


def _login(app, user):
    client = app.test_client()
    client.get(f'/test-session/{user.id}')
    return client


def _dictation(author, when, seconds=None):
    recording = Recording(user_id=author.id, title='d', status='PENDING', audio_duration_seconds=seconds)
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(recording_id=recording.id, author_user_id=author.id, created_at=when)
    db.session.add(row)
    db.session.commit()
    return row


def test_anonymous_gets_401_and_clinic_off_gets_404(stats_app, monkeypatch):
    assert stats_app.test_client().get(PATH).status_code == 401
    monkeypatch.delenv('CLINIC_MODE')
    assert stats_app.test_client().get(PATH).status_code == 404


def test_account_without_grant_or_with_inactive_account_gets_403(stats_app):
    no_grant = _account()
    disabled = _account('disabled')
    statistics_access.grant_aggregate(disabled.id, 'author', author_ids=[no_grant.id])
    assert _login(stats_app, no_grant).get(PATH).status_code == 403
    assert _login(stats_app, disabled).get(PATH).status_code == 403
    admin = _user(admin=True)
    db.session.add(ClinicAccountPolicy(user_id=admin.id, status='active'))
    db.session.commit()
    assert _login(stats_app, admin).get(PATH).status_code == 403


def test_authorised_user_gets_service_shape_inside_grant_only(stats_app):
    stats, author, other = _account(), _user(), _user()
    when = datetime(2026, 3, 2, 8, tzinfo=timezone.utc)
    _dictation(author, when, 10)
    _dictation(other, when, 99)
    grant = statistics_access.grant_aggregate(stats.id, 'author', author_ids=[author.id])
    reply = _login(stats_app, stats).get(PATH)
    assert reply.status_code == 200
    assert reply.headers['X-Grant-Version'] == str(grant.version)
    body = reply.json
    assert set(body) == {'time_basis', 'zone', 'window', 'start', 'end', 'grain', 'group_by',
                         'as_of', 'excluded_missing_timestamp', 'buckets'}
    assert body['zone'] == 'Europe/Zurich'
    assert body['buckets'] == [{'count': 1, 'total_seconds': 10.0, 'average_seconds': 10.0,
                                'known_duration_count': 1, 'incomplete': False}]
    grouped = _login(stats_app, stats).get(PATH + '?group_by=author').json['buckets']
    assert [b['author_user_id'] for b in grouped] == [author.id]


def test_request_cannot_name_actor_or_role_and_bad_input_is_400(stats_app):
    stats, author = _account(), _user()
    statistics_access.grant_aggregate(stats.id, 'author', author_ids=[author.id])
    client = _login(stats_app, stats)
    for query in ('?role=statistics', '?actor_id=1', '?time_basis=bogus', '?from=2026-13-40',
                  '?from=2026-03-02T00:00:00', '?from=2026-03-05&to=2026-03-01',
                  '?group_by=period', '?expected_version=x'):
        assert client.get(PATH + query).status_code == 400, query


def test_stale_version_is_409(stats_app):
    stats, author = _account(), _user()
    statistics_access.grant_aggregate(stats.id, 'author', author_ids=[author.id])
    client = _login(stats_app, stats)
    assert client.get(PATH + '?expected_version=1').status_code == 200
    assert client.get(PATH + '?expected_version=0').status_code == 409


def test_date_bounds_are_europe_zurich_across_the_dst_switch(stats_app):
    stats, author = _account(), _user()
    statistics_access.grant_aggregate(stats.id, 'author', author_ids=[author.id])
    # Zurich switches to summer time on 2026-03-29: 28 Mar 00:00 is +01:00, 30 Mar 00:00 is +02:00.
    body = _login(stats_app, stats).get(PATH + '?from=2026-03-28&to=2026-03-30').json
    assert body['start'] == '2026-03-27T23:00:00+00:00'
    assert body['end'] == '2026-03-29T22:00:00+00:00'
    # Winter and summer day edges: 23:30 UTC on 27 Mar is already 28 Mar in Zurich.
    inside = _dictation(author, datetime(2026, 3, 27, 23, 30, tzinfo=timezone.utc), 5)
    before = _dictation(author, datetime(2026, 3, 27, 22, 59, tzinfo=timezone.utc), 7)
    last = _dictation(author, datetime(2026, 3, 29, 21, 59, tzinfo=timezone.utc), 11)
    after = _dictation(author, datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc), 13)
    assert {inside.id, before.id, last.id, after.id}
    daily = _login(stats_app, stats).get(PATH + '?from=2026-03-28&to=2026-03-30&group_by=period&grain=day').json
    assert [(b['period_start'], b['count']) for b in daily['buckets']] == [
        ('2026-03-28', 1), ('2026-03-29', 1)]
    assert sum(b['total_seconds'] or 0 for b in daily['buckets']) == 5 + 11
    explicit = _login(stats_app, stats).get(PATH + '?from=2026-03-28T00:00:00%2B01:00&to=2026-03-30T00:00:00%2B02:00').json
    assert (explicit['start'], explicit['end']) == (body['start'], body['end'])
