"""Versioned aggregate grants through the clinic browser routes."""

import pytest

from src.api.admin import admin_bp
from src.clinic import guard, pools, route_policy, routes_statistics, statistics_access
from src.clinic.routes_statistics import PATH
from src.database import db
from src.models import SystemSetting

from test_pools import _dictation, _suffix, _user, pool_db as pool_db
from test_wp_grants_api import _account, _login, grants_app as grants_app


@pytest.fixture
def api(grants_app, monkeypatch):
    grants_app.register_blueprint(routes_statistics.blueprint)
    grants_app.register_blueprint(admin_bp)
    catalog = route_policy._build()
    catalog['browser_session'] = route_policy.RouteRule(route_policy.PUBLIC)
    monkeypatch.setattr(guard, 'ROUTE_POLICY', catalog)
    grants_app.before_request(guard.clinic_guard)
    return grants_app


def _url(user):
    return f'/api/clinic/v1/statistics/users/{user.id}/grants'


def _grant(author, version=0, **overrides):
    return {'expected_version': version, 'scope': 'author', 'author_ids': [author.id], **overrides}


def _authors(client, version):
    response = client.get(PATH, query_string={'expected_version': version, 'group_by': 'author'})
    assert response.status_code == 200
    assert response.headers['X-Grant-Version'] == str(version)
    return {bucket['author_user_id'] for bucket in response.json['buckets']}


def test_admin_changes_live_author_and_pool_scopes_with_version_checks(api):
    admin = _account(_user(admin=True))
    stats, other_stats = _account(_user()), _account(_user())
    author, pool_author, excluded = _user(), _user(), _user()
    pool = pools.create_author_pool(f'A-{_suffix()}', actor_id=admin.id)
    _dictation(author, None)
    _dictation(pool_author, pool.id)
    _dictation(excluded, None)
    client, headers = _login(api, admin)
    reader, _ = _login(api, stats)
    url = _url(stats)

    initial = client.get(url)
    assert initial.status_code == 200
    assert initial.headers['Cache-Control'] == 'no-store'
    assert initial.headers['X-Grant-Version'] == '0'
    assert initial.json == {'user_id': stats.id, 'version': 0, 'items': []}
    assert reader.get(PATH).status_code == 403
    assert client.get(PATH).status_code == 403

    author_grant = client.post(url, headers=headers, json=_grant(author))
    assert author_grant.status_code == 201
    assert author_grant.headers['X-Grant-Version'] == '1'
    grant = author_grant.json['grant']
    assert grant == {'id': grant['id'], 'scope': 'author', 'author_ids': [author.id],
                     'pool_ids': [], 'version': 1}
    stale = client.post(url, headers=headers, json=_grant(excluded))
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    assert _authors(reader, 1) == {author.id}

    pool_grant = client.post(url, headers=headers, json={
        'expected_version': 1, 'scope': 'author_pool', 'pool_ids': [pool.id],
    })
    assert pool_grant.status_code == 201
    assert pool_grant.json['grant']['pool_ids'] == [pool.id]
    assert _authors(reader, 2) == {author.id, pool_author.id}
    assert reader.get(PATH, query_string={'expected_version': 1}).status_code == 409

    assert client.delete(f"{_url(other_stats)}/{grant['id']}", headers=headers,
                         json={'expected_version': 0}).status_code == 404
    grant_url = f"{url}/{grant['id']}"
    assert client.delete(grant_url, json={'expected_version': 2}).status_code == 403
    assert client.delete(grant_url, headers=headers, json={}).status_code == 400
    stale = client.delete(grant_url, headers=headers, json={'expected_version': 1})
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    revoked = client.delete(grant_url, headers=headers, json={'expected_version': 2})
    assert revoked.status_code == 200
    assert revoked.json == {'user_id': stats.id, 'version': 3}
    assert _authors(reader, 3) == {pool_author.id}
    assert client.get(url).json == {
        'user_id': stats.id, 'version': 3, 'items': [pool_grant.json['grant']],
    }
    assert client.delete(grant_url, headers=headers, json={'expected_version': 3}).status_code == 404
    last = f"{url}/{pool_grant.json['grant']['id']}"
    assert client.delete(last, headers=headers, json={'expected_version': 3}).status_code == 200
    assert reader.get(PATH).status_code == 403


def test_statistics_reader_cannot_administer_grants_users_or_settings(api):
    admin, stats, author = _account(_user(admin=True)), _account(_user()), _user()
    _dictation(author, None)
    owner, admin_csrf = _login(api, admin)
    reader, headers = _login(api, stats)
    url = _url(stats)
    added = owner.post(url, headers=admin_csrf, json=_grant(author))
    assert added.status_code == 201
    grant_url = f"{url}/{added.json['grant']['id']}"
    assert _authors(reader, 1) == {author.id}
    assert reader.get(PATH, query_string={'scope': 'all'}).status_code == 400
    assert reader.get(url).status_code == 403
    assert reader.post(url, headers=headers, json=_grant(admin, 1)).status_code == 403
    assert reader.delete(grant_url, headers=headers, json={'expected_version': 1}).status_code == 403
    assert reader.put(f'/admin/users/{author.id}', headers=headers,
                      json={'username': 'denied-rename'}).status_code == 403
    assert reader.post('/admin/settings', headers=headers,
                       json={'key': 'p120_probe', 'value': 'set'}).status_code == 403
    assert SystemSetting.query.filter_by(key='p120_probe').first() is None
    db.session.refresh(author)
    assert author.username != 'denied-rename'
    assert owner.get(url).json['version'] == 1
    assert _authors(reader, 1) == {author.id}


def test_only_active_site_admin_with_csrf_can_change_explicit_grants(api, monkeypatch):
    admin, stats, author = _account(_user(admin=True)), _account(_user()), _user()
    disabled = _account(_user(admin=True), status='disabled')
    no_policy = _user(admin=True)
    identity = _account(_user(), identity_admin=True)
    client, headers = _login(api, admin)
    url = _url(stats)
    for caller in (disabled, no_policy, identity):
        denied, csrf = _login(api, caller)
        assert denied.get(url).status_code == 403
        assert denied.post(url, headers=csrf, json=_grant(author)).status_code == 403
    assert api.test_client().get(url).status_code == 401
    assert client.post(url, json=_grant(author)).status_code == 403
    assert client.post(url, headers=headers, json=_grant(author, scope=' ALL ')).status_code == 400
    for body in ({}, _grant(author, True), _grant(author, actor_id=admin.id),
                 _grant(author, author_ids=[True]), _grant(author, author_ids={'1': 1}),
                 _grant(author, author_ids=[2**31]), _grant(author, author_ids=[author.id, author.id]),
                 {'expected_version': 0, 'scope': 'author_pool', 'pool_ids': ['unknown']},
                 {'expected_version': 0, 'scope': 'author_pool', 'pool_ids': ['\ud800']}):
        invalid = client.post(url, headers=headers, json=body)
        assert (invalid.status_code, invalid.json['code']) == (400, 'INVALID_PAYLOAD'), body
    assert client.get(url).json['version'] == 0
    monkeypatch.setenv('CLINIC_MODE', '0')
    assert client.get(url).status_code == 404
    assert client.post(url, headers=headers, json=_grant(author)).status_code == 404


def test_aggregate_rejects_scope_that_changes_before_the_response(api, monkeypatch):
    stats, author = _account(_user()), _user()
    _dictation(author, None)
    grant = statistics_access.grant_aggregate(stats.id, 'author', author_ids=[author.id])
    reader, _ = _login(api, stats)
    original_read = routes_statistics.read_aggregates

    def read_then_revoke(session, user_id, *args, **kwargs):
        result = original_read(session, user_id, *args, **kwargs)
        statistics_access.revoke_aggregate(user_id, grant.id, expected_version=1, session=session)
        return result

    monkeypatch.setattr(routes_statistics, 'read_aggregates', read_then_revoke)
    stale = reader.get(PATH)
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    assert 'buckets' not in stale.json
    assert reader.get(PATH).status_code == 403
