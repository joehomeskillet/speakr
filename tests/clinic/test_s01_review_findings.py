"""S01 review findings 1-13: one regression test group per finding.

Each test names the finding it pins. They run the real app (client) where the
handler can be reached safely and the guard alone where the handler would call
a model or write files.
"""

import pytest
from flask_login import login_user

from clinic_support import SECRET_MARKER, dump, login
from src.app import app, db
from src.clinic import config, guard, policy
from src.clinic.guard import clinic_guard
from src.clinic.policy import Grant
from src.clinic.route_policy import ROUTE_POLICY
from src.models import ProcessingJob, Recording, User


def _guard(user_id, method, url, **request_args):
    with app.test_request_context(url, method=method, **request_args):
        login_user(db.session.get(User, user_id))
        return clinic_guard()


def _code(response):
    return (response.get_json(silent=True) or {}).get('code')


@pytest.fixture
def local_models(monkeypatch):
    """Chat, inquire and summaries are open: every model URL is local."""
    for name in ('CHAT_MODEL_BASE_URL', 'TRANSLATION_MODEL_BASE_URL', 'EMBEDDING_BASE_URL',
                 'CLINIC_LOCAL_MODEL_HOSTS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'http://localhost:11434/v1')


@pytest.fixture
def features_open(monkeypatch):
    monkeypatch.setattr(guard, 'feature_locked', lambda name: False)


# --- 1: merge can hard-delete originals under the edit action ---------------------

@pytest.mark.parametrize('flag', ['delete_originals', 'remove_original_audio'])
@pytest.mark.parametrize('value', [True, 'false', 1])
def test_1_merge_with_a_delete_flag_is_refused_while_hard_delete_is_locked(clinic_on, world, flag, value):
    body = {'recording_ids': [world['rec_a'], world['rec_a']], flag: value}
    response = login(world['user_a']).post('/api/recordings/merge', json=body)
    assert response.status_code == 403 and _code(response) == 'clinic_feature_disabled'
    with app.app_context():
        assert db.session.get(Recording, world['rec_a']) is not None


@pytest.mark.parametrize('flag', ['delete_originals', 'remove_original_audio'])
def test_1_merge_flag_is_read_from_query_and_form_too(clinic_on, world, flag):
    for args in ({'query_string': {flag: '1'}}, {'data': {flag: '1'}}):
        response = _guard(world['user_a'], 'POST', '/api/recordings/merge', **args)
        assert response is not None and response.status_code == 403, args


def test_1_merge_flag_needs_the_delete_grant_when_hard_delete_is_open(clinic_on, world, features_open):
    body = {'recording_ids': [world['rec_a']], 'delete_originals': True}
    response = _guard(world['user_a'], 'POST', '/api/recordings/merge', json=body)
    assert response is not None and response.status_code == 403  # edit is not enough
    policy.register_grant_provider(lambda uid: [Grant('own', actions=frozenset({'delete'}))])
    assert _guard(world['user_a'], 'POST', '/api/recordings/merge', json=body) is None


def test_1_merge_without_a_flag_still_passes_the_guard(clinic_on, world):
    body = {'recording_ids': [world['rec_a']], 'delete_originals': False, 'remove_original_audio': ''}
    assert _guard(world['user_a'], 'POST', '/api/recordings/merge', json=body) is None


# --- 2, 3: the singular recording_id and the filter ids ----------------------------

def test_2_chat_with_a_foreign_singular_recording_id_is_denied(clinic_on, world, local_models):
    client = login(world['user_a'])
    response = client.post('/chat', json={'recording_id': world['rec_b'], 'message': 'hi'})
    assert response.status_code == 404
    assert SECRET_MARKER not in dump(response)
    assert _guard(world['user_a'], 'POST', '/chat', json={'recording_id': world['rec_a']}) is None


@pytest.mark.parametrize('method,url,body', [
    ('POST', '/api/inquire/search', {'query': 'x', 'filter_recording_ids': ['B']}),
    ('POST', '/api/inquire/chat', {'message': 'x', 'filter_recording_ids': ['B']}),
    ('POST', '/api/inquire/sessions', {'filter_recording_ids': ['B']}),
    ('POST', '/api/inquire/search', {'query': 'x', 'filters': {'recording_ids': ['B']}}),
    ('POST', '/api/v1/inquire', {'question': 'x', 'filters': {'recording_ids': ['B']}}),
])
def test_3_inquire_filter_ids_are_checked(clinic_on, world, local_models, method, url, body):
    def fill(node, rid):
        if node == 'B':
            return rid
        if isinstance(node, list):
            return [fill(n, rid) for n in node]
        if isinstance(node, dict):
            return {k: fill(v, rid) for k, v in node.items()}
        return node

    response = login(world['user_a']).open(url, method=method, json=fill(body, world['rec_b']))
    assert response.status_code == 404, url
    assert SECRET_MARKER not in dump(response)
    assert _guard(world['user_a'], method, url, json=fill(body, world['rec_a'])) is None


# --- 4: every model URL counts ------------------------------------------------------

@pytest.mark.parametrize('name', ['CHAT_MODEL_BASE_URL', 'TRANSLATION_MODEL_BASE_URL', 'EMBEDDING_BASE_URL'])
def test_4_one_public_model_url_locks_the_text_features(clinic_on, local_models, monkeypatch, name):
    assert config.text_model_is_local() is True
    monkeypatch.setenv(name, 'https://api.openai.com/v1')
    assert config.text_model_is_local() is False
    for feature in ('chat', 'inquire', 'generative_summary'):
        assert config.feature_locked(feature) is True
    monkeypatch.setenv(name, 'http://10.0.0.5:8000/v1')
    assert config.text_model_is_local() is True
    monkeypatch.setenv(name, '')
    assert config.text_model_is_local() is True  # unset: it falls back to the text model


def test_4_a_public_chat_model_keeps_chat_closed_end_to_end(clinic_on, world, local_models, monkeypatch):
    monkeypatch.setenv('CHAT_MODEL_BASE_URL', 'https://api.openai.com/v1')
    response = login(world['user_a']).post('/chat', json={'recording_id': world['rec_a']})
    assert response.status_code == 403 and _code(response) == 'clinic_feature_disabled'


def test_4_the_text_model_itself_must_be_set_and_local(clinic_on, monkeypatch):
    monkeypatch.delenv('TEXT_MODEL_BASE_URL', raising=False)
    assert config.text_model_is_local() is False  # the client then defaults to a public service


def test_4_the_comment_split_matches_the_llm_client(clinic_on, monkeypatch):
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'http://localhost:11434/v1 # local')
    assert config.text_model_is_local() is True


# --- 5: job ids resolve to their recording ------------------------------------------

def test_5_admin_cannot_cancel_another_users_job(clinic_on, world):
    response = login(world['user_admin']).delete(f'/api/recordings/jobs/{world["job_a"]}')
    assert response.status_code == 404
    with app.app_context():
        assert db.session.get(ProcessingJob, world['job_a']).status == 'queued'


def test_5_a_user_cannot_touch_a_foreign_job(clinic_on, world):
    for method, url in (('DELETE', f'/api/recordings/jobs/{world["job_b"]}'),
                        ('POST', f'/api/recordings/jobs/{world["job_b"]}/retry')):
        assert login(world['user_a']).open(url, method=method).status_code == 404, url


def test_5_the_owner_may_cancel_an_own_job(clinic_on, world):
    assert _guard(world['user_a'], 'DELETE', f'/api/recordings/jobs/{world["job_a"]}') is None


def test_5_a_missing_job_is_a_404_from_the_guard(clinic_on, world):
    assert _guard(world['user_a'], 'DELETE', '/api/recordings/jobs/987654321').status_code == 404


def test_5_deleting_a_failed_new_upload_is_a_hard_delete(clinic_on, world):
    """Stock behaviour: DELETE on a failed new-upload job removes the recording."""
    response = login(world['user_a']).delete(f'/api/recordings/jobs/{world["job_a_new"]}')
    assert response.status_code == 403 and _code(response) == 'clinic_feature_disabled'
    with app.app_context():
        assert db.session.get(Recording, world['rec_a']) is not None
        assert db.session.get(ProcessingJob, world['job_a_new']) is not None


# --- 6: export-existing has no ids ---------------------------------------------------

def test_6_export_existing_needs_the_export_grant(clinic_on, world, features_open):
    url = '/api/recordings/export-existing'
    response = _guard(world['user_a'], 'POST', url)
    assert response is not None and response.status_code == 403  # no export grant
    policy.register_grant_provider(lambda uid: [Grant('own', actions=frozenset({'export'}))])
    assert _guard(world['user_a'], 'POST', url) is None


def test_6_export_existing_with_no_recording_is_refused(clinic_on, world, features_open):
    response = _guard(world['user_admin'], 'POST', '/api/recordings/export-existing')
    assert response is not None and _code(response) == 'clinic_no_target'


def test_6_export_existing_is_closed_by_default(clinic_on, world):
    response = login(world['user_a']).post('/api/recordings/export-existing')
    assert response.status_code == 403 and _code(response) == 'clinic_feature_disabled'


# --- 8, 9: disabled share routes -------------------------------------------------------

@pytest.mark.parametrize('endpoint', sorted(
    e for e, r in ROUTE_POLICY.items() if r.kind == 'disabled' and r.feature in ('public_sharing', 'internal_sharing')))
def test_8_share_routes_are_denied_before_any_id_is_read(clinic_on, world, endpoint):
    from test_s01_routes import _url
    url, method = _url(endpoint, world, 'rec_a')
    for args in ({'query_string': {'recording_id': 'not-a-number'}}, {'json': {'recording_ids': [None, 'x']}},
                 {'json': ['junk']}, {}):
        for user in (world['user_a'], world['user_admin']):
            response = _guard(user, method, url, **args)
            assert response is not None and response.status_code == 403, (endpoint, args)
            assert _code(response) == 'clinic_feature_disabled'


def test_9_a_share_route_that_gets_enabled_without_a_recording_id_is_refused(clinic_on, world, features_open):
    """public_id / share_id name no recording: with the lock lifted the guard
    still cannot decide, so it refuses instead of letting the request through."""
    from test_s01_routes import _url
    for endpoint in ('shares.view_shared_recording', 'shares.update_share'):
        url, method = _url(endpoint, world)
        response = _guard(world['user_a'], method, url, json={})
        assert response is not None and _code(response) == 'clinic_feature_unsupported', endpoint


# --- 10: the guard is the first hook of the built app ------------------------------------

def test_10_the_guard_is_the_first_before_request_hook():
    hooks = app.before_request_funcs[None]
    assert hooks[0] is clinic_guard
    assert hooks.count(clinic_guard) == 1


def test_10_the_idle_timeout_hook_runs_after_the_guard():
    from src.services import session_policy
    hooks = app.before_request_funcs[None]
    assert session_policy._enforce_idle_timeout in hooks
    assert hooks.index(clinic_guard) < hooks.index(session_policy._enforce_idle_timeout)


def test_10_ensure_guard_first_repairs_a_prepended_hook():
    hooks = app.before_request_funcs[None]
    saved = list(hooks)
    try:
        hooks.insert(0, lambda: None)
        assert hooks[0] is not clinic_guard
        guard.ensure_guard_first(app)
        assert hooks[0] is clinic_guard and hooks.count(clinic_guard) == 1
    finally:
        hooks[:] = saved


# --- 11: single-label names are local only on the list ----------------------------------

@pytest.mark.parametrize('url', ['http://ai/', 'http://ai:8000/v1', 'http://internal/v1', 'ai',
                                 'http://ollama:11434/v1'])
def test_11_a_bare_single_label_host_is_not_local(monkeypatch, url):
    monkeypatch.delenv('CLINIC_LOCAL_MODEL_HOSTS', raising=False)
    assert config.is_local_model_url(url) is False


@pytest.mark.parametrize('url', ['http://speaches:8000/v1', 'http://host.docker.internal:11434/v1',
                                 'http://localhost/v1', 'http://LOCALHOST:1/v1', 'http://127.0.0.1/v1'])
def test_11_the_default_list_is_local(monkeypatch, url):
    monkeypatch.delenv('CLINIC_LOCAL_MODEL_HOSTS', raising=False)
    assert config.is_local_model_url(url) is True


def test_11_the_list_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv('CLINIC_LOCAL_MODEL_HOSTS', 'ai, Gpu-Box')
    assert config.is_local_model_url('http://ai/') is True
    assert config.is_local_model_url('http://gpu-box:80') is True
    assert config.is_local_model_url('http://speaches:8000') is False  # replaced, not extended
    assert config.is_local_model_url('http://10.0.0.1') is True        # addresses always count


@pytest.mark.parametrize('url', ['http://evil.example.com\\@localhost/v1', 'http://localhost\\.evil.com',
                                 'http://localhost@evil.com', 'http://local host/v1', 'ftp://localhost/',
                                 'http://localhost:abc/'])
def test_11_urls_that_parsers_read_differently_are_refused(monkeypatch, url):
    monkeypatch.delenv('CLINIC_LOCAL_MODEL_HOSTS', raising=False)
    assert config.is_local_model_url(url) is False


# --- 12: ids in the query string of an AUTH route ----------------------------------------

def test_12_search_with_a_foreign_recording_id_in_the_query_is_denied(clinic_on, world):
    client = login(world['user_a'])
    response = client.get(f'/api/v1/search?recording_ids={world["rec_b"]}')
    assert response.status_code == 404
    assert SECRET_MARKER not in dump(response)
    response = client.get(f'/api/v1/search?recording_ids={world["rec_a"]},{world["rec_b"]}')
    assert response.status_code == 404
    assert client.get(f'/api/v1/search?q=dictation&recording_ids={world["rec_a"]}').status_code == 200


def test_12_an_unreadable_id_in_the_query_is_a_400(clinic_on, world):
    assert login(world['user_a']).get('/api/v1/search?recording_ids=1,x').status_code == 400


# --- 13: global counts -------------------------------------------------------------------

@pytest.mark.parametrize('value', ['all', 'ALL', ' all '])
def test_13_stats_scope_all_is_forbidden(clinic_on, world, value):
    response = login(world['user_admin']).get('/api/v1/stats', query_string={'scope': value})
    assert response.status_code == 403 and _code(response) == 'clinic_scope_forbidden'


def test_13_personal_stats_still_work(clinic_on, world):
    assert login(world['user_admin']).get('/api/v1/stats').status_code == 200
    assert login(world['user_admin']).get('/api/v1/stats?scope=user').status_code == 200


def test_13_stats_scope_all_is_unchanged_outside_clinic_mode(clinic_off, world):
    assert login(world['user_admin']).get('/api/v1/stats?scope=all').status_code == 200


# --- own findings -------------------------------------------------------------------------

def test_own_a_form_encoded_foreign_id_is_checked(clinic_on, world):
    response = _guard(world['user_a'], 'POST', '/api/recordings/bulk-toggle',
                      data={'recording_ids': str(world['rec_b'])})
    assert response is not None and response.status_code == 404


def test_own_an_oversized_id_list_is_refused(clinic_on, world):
    body = {'recording_ids': [world['rec_a']] * 3000}
    response = _guard(world['user_a'], 'POST', '/api/recordings/bulk-toggle', json=body)
    assert response is not None and response.status_code == 400
