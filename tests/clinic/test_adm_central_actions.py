"""PAR-ADM-005 / PAR-WF-012: independent central action grants and denies."""

from dataclasses import replace

import pytest

from src.clinic import policy, pools

from test_pools import _dictation, _user, pool_db as pool_db


GRANT_ACTIONS = (
    'view', 'play', 'edit', 'export', 'release', 'delete', 'archive',
    'rename', 'priority', 'audio_export', 'reassign', 'reopen',
)
ALL_ACTIONS = (*GRANT_ACTIONS, 'download')
OWN_DEFAULTS = frozenset({'view', 'play', 'edit'})


def test_central_policy_covers_stored_grants_and_legacy_download():
    assert set(GRANT_ACTIONS) == set(pools.GRANTABLE_ACTIONS)
    assert set(ALL_ACTIONS) == policy.ACTIONS


@pytest.mark.parametrize('action', ALL_ACTIONS)
@pytest.mark.parametrize('owner', [False, True], ids=['foreign', 'own'])
def test_single_grant_changes_only_its_action(action, owner):
    rec = policy.RecordingFacts(10, owner_id=1 if owner else 2)
    subject = policy.Subject(1)
    granted = replace(subject, grants=(policy.Grant('all', frozenset({action})),))
    for candidate in ALL_ACTIONS:
        baseline = policy.decide(subject, candidate, rec)
        assert baseline.allowed == (owner and candidate in OWN_DEFAULTS)
        result = policy.decide(granted, candidate, rec)
        if candidate == action:
            assert result.allowed and result.visible
        else:
            assert result == baseline


@pytest.mark.parametrize('action', ALL_ACTIONS)
@pytest.mark.parametrize('owner', [False, True], ids=['foreign', 'own'])
def test_deny_wins_over_explicit_grants_and_ownership(action, owner):
    rec = policy.RecordingFacts(10, owner_id=1 if owner else 2)
    for deny in (
        policy.Deny(author_ids=frozenset({rec.owner_id})),
        policy.Deny(recording_ids=frozenset({rec.recording_id})),
    ):
        subject = policy.Subject(
            1, grants=(policy.Grant('all', frozenset({action})),), denies=(deny,),
        )
        assert policy.decide(subject, action, rec) == policy.Decision(
            False, 'deny_exception', visible=owner,
        )


@pytest.mark.parametrize('action', GRANT_ACTIONS)
def test_stored_single_grant_and_action_deny_apply_live(clinic_on, action):
    admin = _user(admin=True)
    author, typist = _user(), _user()
    target = _dictation(author, None)
    subject = policy.Subject(typist.id)
    rec = policy.RecordingFacts(target.recording_id, author.id)
    before = {candidate: policy.decide(subject, candidate, rec) for candidate in ALL_ACTIONS}
    assert not any(result.allowed for result in before.values())

    pools.allow(typist.id, 'all', actor_id=admin.id, actions=[action])
    for candidate in ALL_ACTIONS:
        result = policy.decide(subject, candidate, rec)
        if candidate == action:
            assert result == policy.Decision(True, 'grant_all', visible=True)
        else:
            assert result == before[candidate]

    pools.allow(typist.id, 'all', actor_id=admin.id, actions=list(GRANT_ACTIONS))
    pools.deny(typist.id, 'all', actor_id=admin.id, actions=[action])
    for candidate in GRANT_ACTIONS:
        result = policy.decide(subject, candidate, rec)
        assert result.allowed == (candidate != action)
        if candidate == action:
            assert result.reason == 'deny_exception'
