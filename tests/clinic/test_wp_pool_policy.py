"""Synthetic database checks for the pool-to-policy adapter."""

from dataclasses import replace
from typing import cast

import pytest

from src.clinic import policy, pool_policy, pools

from test_pools import _dictation, _suffix, _user, pool_db as pool_db


def test_explicit_actions_keep_their_own_pool_and_all_scopes() -> None:
    author, other = _user(), _user()
    pool = pools.create_author_pool(f'A-{_suffix()}', actor_id=author.id)
    own = _dictation(author, pool.id)
    peer = _dictation(other, pool.id)
    outside = _dictation(other, None)
    subject = policy.Subject(author.id)
    assert pool_policy.decide(subject, 'view', own).reason == 'own_default'

    pools.allow(author.id, 'own', actor_id=author.id, actions=['release'])
    pools.allow(author.id, 'pool', actor_id=author.id, pool_id=pool.id, actions=['edit'])
    pools.allow(author.id, 'all', actor_id=author.id, actions=['export'])
    assert pool_policy.decide(subject, 'release', own).reason == 'grant_own'
    assert not pool_policy.decide(subject, 'release', peer).allowed
    assert pool_policy.decide(subject, 'edit', peer).reason == 'grant_author_pool'
    assert not pool_policy.decide(subject, 'edit', outside).allowed
    assert pool_policy.decide(subject, 'export', outside).reason == 'grant_all'
    assert not pool_policy.decide(subject, 'download', peer).allowed


@pytest.mark.parametrize('owner', [True, False], ids=['own', 'foreign'])
def test_action_deny_overrides_every_grant_on_the_matching_target(owner: bool) -> None:
    author, viewer = _user(), _user()
    user_id = author.id if owner else viewer.id
    pool = pools.create_author_pool(f'A-{_suffix()}', actor_id=author.id)
    staff = pools.create_staff_pool(f'S-{_suffix()}', actor_id=author.id)
    pools.link_staff_pool(pool.id, staff.id, actor_id=author.id)
    pools.add_staff_member(staff.id, user_id, actor_id=author.id)
    target = _dictation(author, pool.id)
    other = _dictation(author, None)
    subject = policy.Subject(user_id)
    assert pool_policy.decide(subject, 'view', target).allowed

    subject = replace(subject, grants=(
        policy.Grant('author', frozenset({'view'}), author_ids=frozenset({author.id})),
    ))
    pools.allow(user_id, 'all', actor_id=author.id, actions=['view', 'play'])
    pools.deny(user_id, 'pool', actor_id=author.id, pool_id=pool.id, actions=['view'])
    assert pool_policy.decide(subject, 'view', target) == policy.Decision(
        False, 'deny_exception', visible=owner,
    )
    assert pool_policy.decide(subject, 'play', target).allowed
    assert pool_policy.decide(subject, 'view', other).allowed


def test_same_name_and_author_memberships_are_not_grants() -> None:
    author, admin = _user(), _user(admin=True)
    first = pools.create_author_pool(f'A-{_suffix()}', actor_id=author.id)
    second = pools.create_author_pool(f'B-{_suffix()}', actor_id=author.id)
    staff = pools.create_staff_pool(first.name, actor_id=author.id)
    for pool in (first, second):
        pools.add_author_member(pool.id, author.id, actor_id=author.id)
    pools.add_staff_member(staff.id, admin.id, actor_id=author.id)
    a, b = (_dictation(author, pool.id) for pool in (first, second))
    subject = policy.Subject(admin.id, is_admin=True)
    assert not pool_policy.decide(subject, 'view', a).allowed
    assert pool_policy.author_pool_ids(admin.id) == frozenset()

    pool_ids = cast(frozenset[int], frozenset({first.id, second.id}))
    scoped = replace(subject, grants=(policy.Grant('author_pool', frozenset({'view'}),
                                                   pool_ids=pool_ids),))
    assert pool_policy.decide(scoped, 'view', a).allowed
    assert pool_policy.decide(scoped, 'view', b).allowed
    pools.remove_author_member(first.id, author.id, actor_id=author.id)
    assert pool_policy.author_pool_ids(author.id) == {second.id}
    assert not pool_policy.decide(scoped, 'view', a).allowed
    assert pool_policy.decide(scoped, 'view', b).allowed


def test_staff_retry_after_removal_uses_current_memberships() -> None:
    author, viewer = _user(), _user()
    a = pools.create_author_pool(f'A-{_suffix()}', actor_id=author.id)
    b = pools.create_author_pool(f'B-{_suffix()}', actor_id=author.id)
    first = pools.create_staff_pool(f'S1-{_suffix()}', actor_id=author.id)
    second = pools.create_staff_pool(f'S2-{_suffix()}', actor_id=author.id)
    for pool, staff in ((a, first), (b, second)):
        pools.link_staff_pool(pool.id, staff.id, actor_id=author.id)
        pools.add_staff_member(staff.id, viewer.id, actor_id=author.id)
    target, other = (_dictation(author, pool.id) for pool in (a, b))
    subject = policy.Subject(viewer.id)
    assert pool_policy.decide(subject, 'view', target).allowed
    assert not pool_policy.decide(subject, 'edit', target).allowed

    pools.remove_staff_member(first.id, viewer.id, actor_id=author.id)
    assert not pool_policy.decide(subject, 'view', target).allowed
    assert pool_policy.decide(subject, 'view', other).allowed
    pools.add_staff_member(first.id, viewer.id, actor_id=author.id)
    assert pool_policy.decide(subject, 'view', target).allowed
