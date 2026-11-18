"""Adapt live pool permissions for one central clinic policy decision."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from src.clinic import policy, pools
from src.clinic.models import ClinicDictation
from src.database import db


def author_pool_ids(author_user_id: int) -> frozenset[str]:
    """Read current author memberships by ID, independently of staff groups."""
    member = pools.ClinicAuthorPoolMember
    rows = db.session.query(member.pool_id).filter(member.user_id == author_user_id)
    return frozenset(pool_id for (pool_id,) in rows)


def decide(
    subject: policy.Subject, action: str, dictation: ClinicDictation
) -> policy.Decision:
    """Evaluate one action on a server-loaded dictation with a base policy subject.

    The assigned pool scopes the pool service's rules. A current author
    membership can match an explicit central grant for that assigned pool.
    """
    facts = policy.build_facts(dictation.recording_id, dictation.author_user_id)
    pool_id = dictation.pool_id
    result = pools.grants_for(subject.user_id, dictation)
    grants = subject.grants
    denies = subject.denies
    if any(action in row['actions'] for row in result['denies']):
        # The pool service has already matched scope. Bind the action's deny to
        # this recording because a central Deny has no action field.
        denies += (policy.Deny(recording_ids=frozenset({facts.recording_id})),)
    entry = result['allowed'].get(action)
    scope = entry['reason'] if entry is not None else None
    pool_grant = scope in ('pool', 'staff_link')
    if pool_id is not None and (pool_grant or pool_id in author_pool_ids(facts.owner_id)):
        # Central policy compares pool IDs by equality; its legacy annotation is int-only.
        assigned = cast(frozenset[int], frozenset({pool_id}))
        facts = replace(facts, pool_ids=facts.pool_ids | assigned)
        if pool_grant:
            grants += (policy.Grant('author_pool', frozenset({action}), pool_ids=assigned),)
    if scope in ('own', 'all'):
        grants += (policy.Grant(scope, frozenset({action})),)
    return policy.decide(replace(subject, grants=grants, denies=denies), action, facts)
