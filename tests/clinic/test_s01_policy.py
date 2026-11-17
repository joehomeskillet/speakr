"""S01 policy function and route-policy table.

Covers T-011 / T-066 at unit level (own-only default, explicit scopes, deny
exceptions, no admin shortcut, pools alone grant nothing) and the inventory
part of T-052 / R-042 (every URL rule of the app is classified).
"""

import pytest

from src.app import app
from src.clinic import policy
from src.clinic.policy import Deny, Grant, RecordingFacts, Subject, decide
from src.clinic.route_policy import (
    ADMIN, AUTH, DISABLED, PUBLIC, RECORDING, ROUTE_POLICY,
)

ME = 1
OTHER = 2
OWN = RecordingFacts(recording_id=10, owner_id=ME)
FOREIGN = RecordingFacts(recording_id=20, owner_id=OTHER, pool_ids=frozenset({7}))


# --- decide(): defaults --------------------------------------------------------

@pytest.mark.parametrize('action', ['view', 'play', 'edit'])
def test_author_may_use_own_recording_by_default(action):
    result = decide(Subject(ME), action, OWN)
    assert result.allowed and result.visible


@pytest.mark.parametrize('action', ['download', 'export', 'delete', 'release'])
def test_sensitive_actions_on_own_recording_need_a_grant(action):
    result = decide(Subject(ME), action, OWN)
    assert not result.allowed
    assert result.reason == 'action_not_granted'
    assert result.visible  # the author knows the recording exists: 403, not 404


@pytest.mark.parametrize('action', sorted(policy.ACTIONS))
def test_foreign_recording_is_denied_and_hidden(action):
    result = decide(Subject(ME), action, FOREIGN)
    assert not result.allowed and not result.visible
    assert result.reason == 'no_grant'


def test_unknown_action_is_denied():
    assert not decide(Subject(ME), 'rummage', OWN).allowed


def test_admin_flag_grants_nothing():
    admin = Subject(ME, is_admin=True)
    assert not decide(admin, 'view', FOREIGN).allowed
    assert not decide(admin, 'download', OWN).allowed


# --- decide(): explicit grants -------------------------------------------------

def test_own_scope_grant_adds_an_action_on_own_recordings_only():
    grant = Grant('own', actions=frozenset({'download'}))
    subject = Subject(ME, grants=(grant,))
    assert decide(subject, 'download', OWN).allowed
    assert not decide(subject, 'download', FOREIGN).allowed


def test_author_scope_grant_reaches_only_the_listed_authors():
    grant = Grant('author', actions=frozenset({'view'}), author_ids=frozenset({OTHER}))
    subject = Subject(ME, grants=(grant,))
    assert decide(subject, 'view', FOREIGN).allowed
    assert not decide(subject, 'edit', FOREIGN).allowed  # action not listed
    assert not decide(subject, 'view', RecordingFacts(30, owner_id=99)).allowed


def test_author_pool_grant_needs_the_recordings_pool():
    grant = Grant('author_pool', actions=frozenset({'view', 'play'}), pool_ids=frozenset({7}))
    subject = Subject(ME, grants=(grant,))
    assert decide(subject, 'play', FOREIGN).allowed
    assert not decide(subject, 'play', RecordingFacts(30, owner_id=OTHER, pool_ids=frozenset({8}))).allowed
    assert not decide(subject, 'play', RecordingFacts(31, owner_id=OTHER)).allowed


def test_pool_grant_without_actions_confers_nothing():
    """Neither a staff pool nor an author pool alone grants rights (R-011)."""
    subject = Subject(ME, grants=(Grant('author_pool', pool_ids=frozenset({7})),))
    assert not decide(subject, 'view', FOREIGN).allowed


def test_all_scope_grant_reaches_every_recording():
    subject = Subject(ME, grants=(Grant('all', actions=frozenset({'view'})),))
    assert decide(subject, 'view', FOREIGN).allowed
    assert not decide(subject, 'export', FOREIGN).allowed


def test_unknown_scope_grants_nothing():
    subject = Subject(ME, grants=(Grant('galaxy', actions=frozenset(policy.ACTIONS)),))
    assert not decide(subject, 'view', FOREIGN).allowed


# --- decide(): deny exceptions ---------------------------------------------------

def test_deny_by_author_wins_over_a_grant():
    subject = Subject(
        ME,
        grants=(Grant('all', actions=frozenset({'view'})),),
        denies=(Deny(author_ids=frozenset({OTHER})),),
    )
    result = decide(subject, 'view', FOREIGN)
    assert not result.allowed and result.reason == 'deny_exception'
    assert decide(subject, 'view', RecordingFacts(30, owner_id=99)).allowed


def test_deny_by_recording_wins_even_over_ownership():
    subject = Subject(ME, denies=(Deny(recording_ids=frozenset({OWN.recording_id})),))
    assert not decide(subject, 'view', OWN).allowed
    assert decide(subject, 'view', RecordingFacts(11, owner_id=ME)).allowed


# --- providers -------------------------------------------------------------------

def test_providers_feed_subject_and_facts():
    policy.register_grant_provider(lambda uid: [
        Grant('author_pool', actions=frozenset({'view'}), pool_ids=frozenset({7})),
        Deny(recording_ids=frozenset({99})),
    ])
    policy.register_pool_provider(lambda rid: {7} if rid == 20 else set())
    subject = policy.build_subject(ME)
    assert len(subject.grants) == 1 and len(subject.denies) == 1
    assert decide(subject, 'view', policy.build_facts(20, OTHER)).allowed
    assert not decide(subject, 'view', policy.build_facts(21, OTHER)).allowed
    assert not decide(subject, 'view', policy.build_facts(99, OTHER)).allowed


def test_without_providers_the_subject_has_no_extra_rights():
    subject = policy.build_subject(ME)
    assert subject.grants == () and subject.denies == ()


# --- route-policy table: inventory (R-042, T-052) ---------------------------------

def _rules():
    return list(app.url_map.iter_rules())


def test_every_url_rule_is_classified():
    """Fails when a new route appears that nobody classified."""
    missing = sorted({r.endpoint for r in _rules()} - set(ROUTE_POLICY))
    assert not missing, f'unclassified endpoints (add them to src/clinic/route_policy.py): {missing}'


def test_table_has_no_stale_entries():
    stale = sorted(set(ROUTE_POLICY) - {r.endpoint for r in _rules()})
    assert not stale, f'route-policy entries without a route: {stale}'


def test_routes_with_a_recording_argument_are_never_public_or_plain_auth():
    """A route that names a recording must be decided by the central policy."""
    wrong = sorted(
        r.endpoint for r in _rules()
        if {'recording_id', 'event_id'} & r.arguments
        and ROUTE_POLICY[r.endpoint].kind in (PUBLIC, AUTH, ADMIN)
    )
    assert not wrong, wrong


def test_recording_rules_name_an_argument_their_route_has():
    by_endpoint = {}
    for r in _rules():
        by_endpoint.setdefault(r.endpoint, set()).update(r.arguments)
    for endpoint, rule in ROUTE_POLICY.items():
        if rule.kind == RECORDING and not rule.body_keys:
            assert rule.arg in by_endpoint[endpoint], endpoint


def test_table_entries_are_well_formed():
    for endpoint, rule in ROUTE_POLICY.items():
        assert rule.kind in (PUBLIC, AUTH, ADMIN, RECORDING, DISABLED), endpoint
        if rule.kind in (RECORDING, DISABLED):
            assert rule.action in policy.ACTIONS, endpoint
        if rule.kind == DISABLED:
            assert rule.feature, endpoint


def test_public_routes_are_the_minimal_set():
    public = {e for e, r in ROUTE_POLICY.items() if r.kind == PUBLIC}
    assert public == {
        'static', 'robots_txt', 'service_worker', 'auth.login', 'auth.sso_login',
        'auth.sso_callback',
        'api_v1.get_sso_status', 'system.get_csrf_token',
    }
