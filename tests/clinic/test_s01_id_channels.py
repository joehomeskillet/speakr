"""S01 review: recording ids in every channel a handler reads.

The guard must decide a recording id wherever the handler takes it from: URL,
query string, form, JSON body, nested filter objects. These tests derive the
keys from the handler source instead of listing them by hand, so a new handler
that reads a recording id from a new key fails here until the guard covers it.
"""

import pytest
from flask_login import login_user

from handler_keys import request_keys
from test_s01_routes import _url
from src.app import app, db
from src.clinic import guard
from src.clinic.guard import clinic_guard
from src.clinic.request_ids import is_recording_key
from src.clinic.route_policy import ADMIN, AUTH, DISABLED, PUBLIC, RECORDING, ROUTE_POLICY
from src.models import User

# Keys sent to every route even if its handler does not mention them: a handler
# behind a helper or a framework call can read them without the scan seeing it.
FIXED_KEYS = ('recording_id', 'recording_ids', 'filter_recording_ids', 'recordingId')

# Keys that contain "recording" but are not ids (read from result rows).
NON_ID_KEYS = frozenset({'recording_title', 'recording_meeting_date', 'max_recordings',
                         'recording', 'default_recording_sort'})

ROUTES = sorted(e for e, r in ROUTE_POLICY.items() if r.kind != PUBLIC)


def _view(endpoint):
    return app.view_functions[endpoint]


def _keys(endpoint):
    found = {k for k in request_keys(_view(endpoint)) if is_recording_key(k)}
    return found | set(ROUTE_POLICY[endpoint].body_keys) | set(FIXED_KEYS)


def _run(user_id, method, url, **request_args):
    """Run the guard as the user, without running the handler."""
    with app.test_request_context(url, method=method, **request_args):
        login_user(db.session.get(User, user_id))
        return clinic_guard()


def _channels(key, rid):
    """(label, request arguments) for the same id in each channel."""
    yield 'query', {'query_string': {key: rid}}
    yield 'query-csv', {'query_string': {key: f'{rid},{rid}'}}
    yield 'form', {'data': {key: rid}}
    yield 'json', {'json': {key: rid}}
    yield 'json-list', {'json': {key: [rid]}}
    if is_recording_key(key):  # a declared key like "id" is top level only
        yield 'json-nested', {'json': {'filters': {key: [rid]}}}
        yield 'json-deep', {'json': {'a': [{'b': {key: rid}}]}}


@pytest.fixture
def all_features_open(monkeypatch):
    """Every DISABLED route as if its feature were switched on: it must then
    behave like a recording route, which is the case the guard must hold."""
    monkeypatch.setattr(guard, 'feature_locked', lambda name: False)


@pytest.mark.parametrize('endpoint', ROUTES)
def test_foreign_id_is_denied_in_every_channel(clinic_on, world, all_features_open, endpoint):
    rule = ROUTE_POLICY[endpoint]
    user = world['user_admin'] if rule.kind == ADMIN else world['user_a']
    url, method = _url(endpoint, world, 'rec_a')
    for key in sorted(_keys(endpoint)):
        for label, request_args in _channels(key, world['rec_b']):
            response = _run(user, method, url, **request_args)
            assert response is not None and response.status_code in (400, 403, 404), (endpoint, key, label)


@pytest.mark.parametrize('endpoint', [e for e in ROUTES if ROUTE_POLICY[e].kind == AUTH])
def test_own_id_in_a_filter_does_not_trip_an_auth_route(clinic_on, world, endpoint):
    """Control: the test above is not satisfied by a guard that refuses all."""
    url, method = _url(endpoint, world, 'rec_a')
    response = _run(world['user_a'], method, url, json={'filters': {'recording_ids': [world['rec_a']]}})
    assert response is None, endpoint


# --- the keys the handlers read are covered by the table ----------------------

@pytest.mark.parametrize('endpoint', ROUTES)
def test_every_recording_key_a_handler_reads_is_declared(endpoint):
    """Fails when a handler reads a recording key that neither the generic key
    pattern (src/clinic/request_ids.py) nor the route rule declares."""
    rule = ROUTE_POLICY[endpoint]
    undeclared = sorted(
        k for k in request_keys(_view(endpoint))
        if 'recording' in k.lower() and k not in NON_ID_KEYS
        and not is_recording_key(k) and k not in rule.body_keys
    )
    assert not undeclared, f'{endpoint} reads {undeclared}: add them to the rule or to NON_ID_KEYS'


@pytest.mark.parametrize('endpoint,keys', [
    ('recordings.chat_with_transcription', {'recording_id'}),
    ('inquire.inquire_search', {'filter_recording_ids'}),
    ('inquire.create_inquire_session', {'filter_recording_ids'}),
    ('api_v1.inquire_v1', {'recording_ids'}),
    ('api_v1.search_recordings_v1', {'recording_ids'}),
    ('recordings.merge_recordings_endpoint', {'recording_ids', 'notes_source_id', 'delete_originals',
                                              'remove_original_audio'}),
    ('recordings.save_metadata', {'id'}),
])
def test_the_key_scan_still_finds_the_known_keys(endpoint, keys):
    """The scan is a heuristic: this fails when it stops seeing a key it must."""
    assert keys <= request_keys(_view(endpoint)), endpoint
