"""S01 Flask behaviour: guard, feature locks, ceilings, foreign-recording denial.

Covers T-052 (legacy routes, unknown route), T-066 (author is no pool reader),
T-058 (forged channel header), T-060 (settings API cannot lift the ceiling) and
the route-class part of T-011. Clinic mode off is checked against upstream.
"""

import pytest
from flask_login import login_user

from clinic_support import SECRET_MARKER, dump, login, set_setting
from src.app import app, db
from src.clinic.guard import clinic_guard
from src.clinic.route_policy import DISABLED, RECORDING, ROUTE_POLICY
from src.models import SystemSetting, User

_STRING_ARGS = {'public_id', 'session_id', 'group', 'label_name', 'token', 'filename'}


def _rule_for(endpoint, arg=None):
    rules = app.url_map._rules_by_endpoint[endpoint]
    return next((r for r in rules if arg in r.arguments), rules[0])


def _method(rule):
    return sorted(rule.methods - {'HEAD', 'OPTIONS'})[0]


def _url(endpoint, world, rec_key='rec_b'):
    """URL of the endpoint with the recording of the world filled in."""
    arg = ROUTE_POLICY[endpoint].arg if endpoint in ROUTE_POLICY else None
    rule = _rule_for(endpoint, 'recording_id' if arg is None else arg)
    values = {}
    for name in rule.arguments:
        if name == 'recording_id':
            values[name] = world[rec_key]
        elif name == 'job_id':
            values[name] = world['job_' + rec_key[-1]]
        elif name == 'public_id':
            values[name] = world['public_id']
        elif name in _STRING_ARGS:
            values[name] = 'x'
        else:
            values[name] = 1
    return app.url_map.bind('localhost').build(endpoint, values), _method(rule)


def _body(world, rec_key='rec_b'):
    rid = world[rec_key]
    return {'recording_ids': [rid], 'id': rid, 'notes_source_id': rid}


def _endpoints(kind):
    return sorted(e for e, r in ROUTE_POLICY.items() if r.kind == kind)


def _guard(user_id, method, url, body=None):
    """Run the guard for a request as the user, without running the handler."""
    with app.test_request_context(url, method=method, json=body):
        login_user(db.session.get(User, user_id))
        return clinic_guard()


def _is_clinic_refusal(response):
    code = (response.get_json(silent=True) or {}).get('code', '')
    return code.startswith('clinic_')


# --- (a) clinic mode off leaves upstream behaviour alone ------------------------

def test_off_the_guard_never_refuses(clinic_off, world):
    for endpoint in ('shares.view_shared_recording', 'webhooks.list_webhooks', 'tokens.list_tokens',
                     'recordings.chat_with_transcription', 'api_v1.chat_with_recording'):
        url, method = _url(endpoint, world)
        response = login(world['user_a']).open(url, method=method, json={})
        assert not _is_clinic_refusal(response), endpoint


def test_off_an_unclassified_route_is_served(clinic_off, monkeypatch):
    monkeypatch.delitem(ROUTE_POLICY, 'robots_txt')
    assert app.test_client().get('/robots.txt').status_code == 200


def test_off_an_api_token_still_works(clinic_off, world):
    response = app.test_client().get('/api/v1/recordings',
                                     headers={'Authorization': f'Bearer {world["token"]}'})
    assert response.status_code == 200


def test_off_webhook_dispatch_is_unchanged(clinic_off):
    from src.services import webhook_dispatch
    assert webhook_dispatch._global_enabled() is True


def test_off_a_stored_sharing_setting_still_applies(clinic_off, world):
    set_setting('enable_internal_sharing', 'true')
    with app.app_context():
        assert SystemSetting.get_setting('enable_internal_sharing', False) is True


# --- (b) clinic mode on blocks the forbidden functions --------------------------

def test_public_share_link_is_blocked(clinic_on, world):
    client = app.test_client()
    for url in (f'/share/{world["public_id"]}', f'/share/audio/{world["public_id"]}'):
        response = client.get(url)
        assert response.status_code == 403 and _is_clinic_refusal(response)
        assert SECRET_MARKER not in dump(response)


def test_creating_a_public_share_is_blocked(clinic_on, world):
    response = login(world['user_a']).post(f'/api/recording/{world["rec_a"]}/share', json={})
    assert response.status_code == 403 and _is_clinic_refusal(response)
    response = login(world['user_a']).post(f'/api/v1/recordings/{world["rec_a"]}/share', json={})
    assert response.status_code == 403 and _is_clinic_refusal(response)


@pytest.mark.parametrize('method,url', [
    ('POST', '/chat'), ('POST', '/api/inquire/chat'), ('POST', '/api/inquire/search'),
    ('GET', '/inquire'), ('POST', '/api/v1/inquire'), ('POST', '/api/recordings/incognito/chat'),
])
def test_chat_and_inquire_are_blocked(clinic_on, world, method, url):
    response = login(world['user_a']).open(url, method=method, json={'recording_id': world['rec_a']})
    assert response.status_code == 403 and _is_clinic_refusal(response)


def test_chat_with_an_own_recording_is_blocked(clinic_on, world):
    response = login(world['user_a']).post(f'/api/v1/recordings/{world["rec_a"]}/chat', json={'message': 'hi'})
    assert response.status_code == 403 and _is_clinic_refusal(response)


@pytest.mark.parametrize('method,url', [
    ('GET', '/api/v1/webhooks'), ('POST', '/api/v1/webhooks'), ('GET', '/api/v1/webhooks/1'),
    ('PATCH', '/api/v1/webhooks/1'), ('DELETE', '/api/v1/webhooks/1'),
    ('POST', '/api/v1/webhooks/1/test'),
])
def test_webhooks_are_blocked(clinic_on, world, method, url):
    response = login(world['user_a']).open(url, method=method, json={})
    assert response.status_code == 403 and _is_clinic_refusal(response)


def test_webhook_dispatch_is_off_in_clinic_mode(clinic_on):
    from src.services import webhook_dispatch
    assert webhook_dispatch._global_enabled() is False


def test_api_token_in_a_header_is_blocked(clinic_on, world):
    response = app.test_client().get('/api/v1/recordings',
                                     headers={'Authorization': f'Bearer {world["token"]}'})
    assert response.status_code == 403
    assert response.get_json()['feature'] == 'personal_api_tokens'
    for header in ('X-API-Token', 'API-Token'):
        response = app.test_client().get(f'/api/v1/recordings/{world["rec_a"]}', headers={header: world['token']})
        assert response.status_code == 403
        assert SECRET_MARKER not in dump(response)


def test_api_token_in_the_query_is_blocked(clinic_on, world):
    response = app.test_client().get(f'/api/v1/recordings/{world["rec_a"]}?token={world["token"]}')
    assert response.status_code in (401, 403)
    assert SECRET_MARKER not in dump(response)


def test_token_management_is_blocked(clinic_on, world):
    for method, url in (('GET', '/api/tokens'), ('POST', '/api/tokens')):
        response = login(world['user_a']).open(url, method=method, json={'name': 'x'})
        assert response.status_code == 403 and _is_clinic_refusal(response)


@pytest.mark.parametrize('endpoint', _endpoints(DISABLED))
def test_every_disabled_route_is_refused(clinic_on, world, endpoint):
    url, method = _url(endpoint, world, 'rec_a')
    for client in (login(world['user_a']), login(world['user_admin']), app.test_client()):
        response = client.open(url, method=method, json=_body(world, 'rec_a'))
        assert response.status_code == 403 and _is_clinic_refusal(response), (endpoint, url)


# --- (b) fail closed: an unclassified route is denied ---------------------------

def test_unclassified_route_is_denied(clinic_on, world, monkeypatch):
    monkeypatch.delitem(ROUTE_POLICY, 'robots_txt')
    response = app.test_client().get('/robots.txt')
    assert response.status_code == 403
    assert response.get_json()['code'] == 'clinic_unclassified_route'


def test_unclassified_route_is_denied_for_a_signed_in_user_too(clinic_on, world, monkeypatch):
    monkeypatch.delitem(ROUTE_POLICY, 'recordings.get_recording_detail')
    response = login(world['user_a']).get(f'/api/recordings/{world["rec_a"]}')
    assert response.status_code == 403
    assert SECRET_MARKER not in dump(response)


def test_classified_public_route_still_works(clinic_on):
    assert app.test_client().get('/robots.txt').status_code == 200


def test_anonymous_gets_the_login_answer_not_data(clinic_on, world):
    response = app.test_client().get(f'/api/recordings/{world["rec_a"]}')
    assert response.status_code in (401, 302)
    assert SECRET_MARKER not in dump(response)


# --- (c) another user's recording is denied on every route class ----------------

@pytest.mark.parametrize('as_user', ['user_a', 'user_admin'])
@pytest.mark.parametrize('endpoint', _endpoints(RECORDING))
def test_foreign_recording_is_denied_on_every_recording_route(clinic_on, world, endpoint, as_user):
    """Admin included: there is no owner or admin shortcut. b's recording is
    also shared with a internally and publicly, and internal sharing is
    switched on in the stored settings, which must not matter."""
    set_setting('enable_internal_sharing', 'true')
    url, method = _url(endpoint, world)
    response = login(world[as_user]).open(url, method=method, json=_body(world))
    assert response.status_code == 404, (endpoint, url, response.status_code)
    assert SECRET_MARKER not in dump(response)
    assert 'B dictation' not in dump(response)


@pytest.mark.parametrize('endpoint', _endpoints(RECORDING))
def test_own_recording_passes_the_guard_for_default_actions(clinic_on, world, endpoint):
    url, method = _url(endpoint, world, 'rec_a')
    result = _guard(world['user_a'], method, url, _body(world, 'rec_a'))
    if ROUTE_POLICY[endpoint].action in ('view', 'play', 'edit'):
        assert result is None, (endpoint, url)
    else:  # download / export need an explicit grant, even for the author
        assert result is not None and result.status_code == 403


def test_nonexistent_recording_looks_like_a_foreign_one(clinic_on, world):
    response = login(world['user_a']).get('/api/recordings/987654321')
    assert response.status_code == 404


def test_malformed_recording_id_in_the_body_is_refused(clinic_on, world):
    response = login(world['user_a']).post('/api/recordings/bulk-toggle', json={'recording_ids': ['x', None]})
    assert response.status_code == 400


def test_one_foreign_id_in_a_batch_refuses_the_whole_request(clinic_on, world):
    response = login(world['user_a']).post(
        '/api/recordings/bulk-toggle',
        json={'recording_ids': [world['rec_a'], world['rec_b']], 'field': 'inbox', 'value': True})
    assert response.status_code == 404


def test_own_recording_is_readable(clinic_on, world):
    for url in (f'/api/recordings/{world["rec_a"]}', f'/api/v1/recordings/{world["rec_a"]}'):
        response = login(world['user_a']).get(url)
        assert response.status_code == 200, url
        assert 'A own dictation' in dump(response)


# --- T-066: an author is not a pool reader --------------------------------------

def test_lists_counts_and_search_show_only_own_recordings(clinic_on, world):
    client = login(world['user_a'])
    for url in ('/api/recordings', '/api/recordings?shared=true', '/api/v1/recordings?scope=all',
                '/api/v1/recordings?scope=shared', '/api/v1/search?q=CONFIDENTIAL',
                '/api/inbox_recordings', '/api/recordings/archived'):
        response = client.get(url)
        assert response.status_code == 200, url
        assert 'B dictation' not in dump(response), url
        assert f'{SECRET_MARKER}B' not in dump(response), url
    assert 'A own dictation' in dump(client.get('/api/recordings'))
    stats = client.get('/api/v1/stats').get_json()
    assert stats['recordings']['total'] == 1


def test_internal_shares_are_closed_even_when_the_setting_is_on(clinic_on, world):
    set_setting('enable_internal_sharing', 'true')
    with app.app_context():
        assert SystemSetting.get_setting('enable_internal_sharing', True) is False
    client = login(world['user_a'])
    assert 'B dictation' not in dump(client.get('/api/v1/recordings?scope=all'))
    assert client.get(f'/api/recordings/{world["rec_b"]}').status_code == 404
    assert client.get('/api/recordings/shared-with-me').status_code == 403


def test_without_clinic_mode_the_same_setting_opens_the_share(clinic_off, world):
    """Control: the ceiling above is what closes it, not a test artefact."""
    set_setting('enable_internal_sharing', 'true')
    client = login(world['user_a'])
    assert 'B dictation' in dump(client.get('/api/v1/recordings?scope=all'))


# --- T-058: forged channel ------------------------------------------------------

def test_forged_channel_headers_grant_nothing(clinic_on, world):
    headers = {'X-Clinic-Channel': 'managed', 'User-Agent': 'ManagedClient/1.0'}
    client = login(world['user_a'])
    response = client.get(f'/api/v1/recordings/{world["rec_a"]}/audio', headers=headers)
    assert response.status_code == 403  # download needs an explicit grant
    response = client.get(f'/api/v1/recordings/{world["rec_b"]}', headers=headers)
    assert response.status_code == 404


# --- T-060: the settings API cannot lift the ceiling ----------------------------

@pytest.mark.parametrize('key,value', [
    ('enable_public_sharing', 'true'), ('enable_internal_sharing', 'true'),
    ('api_query_tokens_enabled', 'true'), ('enable_auto_deletion', 'true'),
    ('allow_registration', 'true'), ('global_retention_days', '30'),
])
def test_admin_cannot_switch_a_locked_setting_on(clinic_on, world, key, value):
    def stored():
        with app.app_context():
            row = SystemSetting.query.filter_by(key=key).first()
            return row.value if row else None

    before = stored()
    response = login(world['user_admin']).post(
        '/admin/settings', json={'key': key, 'value': value, 'setting_type': 'boolean'})
    assert response.status_code == 403
    assert response.get_json()['code'] == 'clinic_policy_ceiling'
    assert stored() == before


def test_admin_may_switch_a_locked_setting_off(clinic_on, world):
    response = login(world['user_admin']).post(
        '/admin/settings', json={'key': 'enable_public_sharing', 'value': 'false', 'setting_type': 'boolean'})
    assert response.status_code == 200


def test_a_stored_conflicting_setting_is_reported_and_overridden(clinic_off, world, monkeypatch):
    from src.clinic.config import find_setting_conflicts
    set_setting('enable_public_sharing', 'true')
    monkeypatch.setenv('CLINIC_MODE', '1')
    with app.app_context():
        assert SystemSetting.get_setting('enable_public_sharing', True) is False
        conflicts = find_setting_conflicts()
    assert {'key': 'enable_public_sharing', 'stored': 'true', 'effective': False} in conflicts


def test_non_admin_cannot_use_admin_routes(clinic_on, world):
    response = login(world['user_a']).post('/admin/settings', json={'key': 'x', 'value': 'y'})
    assert response.status_code == 403
