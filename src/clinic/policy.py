"""Central access decision for recordings in clinic mode.

One pure function, ``decide``, answers "may this subject do this action on this
recording". Nothing else in the clinic code decides access; the Flask guard
(src/clinic/guard.py) only collects the inputs and calls it.

Rules (secure defaults, R-011):

* An author may view, play and edit their own recordings. Every other action on
  them (download, export, delete, release) needs an explicit grant.
* Access to someone else's recording needs an explicit grant that names the
  action. A grant by scope reaches only what its scope names:
  ``own`` (the subject's own recordings), ``author`` (listed author ids),
  ``author_pool`` (listed pool ids, matched with the recording's pools) or
  ``all``. A grant without actions allows nothing, so neither a staff pool nor
  an author pool confers rights by itself.
* A deny exception always wins, over every grant and over ownership.
* Being a site admin confers nothing. There is no owner or admin shortcut.

Pools come from a later work package. This module takes them as input:
``Subject.grants`` and ``RecordingFacts.pool_ids``. Providers that fill them
from storage are registered with ``register_grant_provider`` and
``register_pool_provider``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

ACTIONS = frozenset({'view', 'play', 'download', 'edit', 'export', 'delete', 'release'})
OWN_DEFAULT_ACTIONS = frozenset({'view', 'play', 'edit'})
SCOPES = frozenset({'own', 'author', 'author_pool', 'all'})


@dataclass(frozen=True)
class Grant:
    """An explicit right: the actions allowed within one scope."""

    scope: str
    actions: frozenset[str] = frozenset()
    author_ids: frozenset[int] = frozenset()
    pool_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Deny:
    """A deny exception, by author and/or by recording."""

    author_ids: frozenset[int] = frozenset()
    recording_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Subject:
    user_id: int
    is_admin: bool = False  # carried for audit, never used to grant access
    grants: tuple[Grant, ...] = ()
    denies: tuple[Deny, ...] = ()


@dataclass(frozen=True)
class RecordingFacts:
    recording_id: int
    owner_id: int
    pool_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    # True when the subject may know the recording exists (their own): a denial
    # is then 403. Otherwise a denial is answered like a missing recording.
    visible: bool = False


def _denied(subject: Subject, rec: RecordingFacts) -> bool:
    return any(
        rec.recording_id in d.recording_ids or rec.owner_id in d.author_ids
        for d in subject.denies
    )


def _grant_reaches(subject: Subject, grant: Grant, rec: RecordingFacts) -> bool:
    if grant.scope == 'all':
        return True
    if grant.scope == 'own':
        return rec.owner_id == subject.user_id
    if grant.scope == 'author':
        return rec.owner_id in grant.author_ids
    if grant.scope == 'author_pool':
        return bool(grant.pool_ids & rec.pool_ids)
    return False  # unknown scope: nothing


def decide(subject: Subject, action: str, rec: RecordingFacts) -> Decision:
    """The one access decision for a recording."""
    if action not in ACTIONS:
        return Decision(False, 'unknown_action')
    owner = rec.owner_id == subject.user_id
    if _denied(subject, rec):
        return Decision(False, 'deny_exception', visible=owner)
    if owner and action in OWN_DEFAULT_ACTIONS:
        return Decision(True, 'own_default', visible=True)
    for grant in subject.grants:
        if action in grant.actions and _grant_reaches(subject, grant, rec):
            return Decision(True, f'grant_{grant.scope}', visible=True)
    if owner:
        return Decision(False, 'action_not_granted', visible=True)
    return Decision(False, 'no_grant')


# --- providers (filled by later work packages: pools, staff grants) ----------

GrantProvider = Callable[[int], Iterable[Grant | Deny]]
PoolProvider = Callable[[int], Iterable[int]]

_grant_providers: list[GrantProvider] = []
_pool_providers: list[PoolProvider] = []


def register_grant_provider(provider: GrantProvider) -> None:
    """provider(user_id) -> Grant and Deny objects of that user."""
    _grant_providers.append(provider)


def register_pool_provider(provider: PoolProvider) -> None:
    """provider(recording_id) -> pool ids the recording belongs to."""
    _pool_providers.append(provider)


def clear_providers() -> None:
    """Remove all providers (tests)."""
    _grant_providers.clear()
    _pool_providers.clear()


def build_subject(user_id: int, is_admin: bool = False) -> Subject:
    grants: list[Grant] = []
    denies: list[Deny] = []
    for provider in _grant_providers:
        for item in provider(user_id):
            (denies if isinstance(item, Deny) else grants).append(item)
    return Subject(user_id, is_admin, tuple(grants), tuple(denies))


def build_facts(recording_id: int, owner_id: int) -> RecordingFacts:
    pools: set[int] = set()
    for provider in _pool_providers:
        pools.update(provider(recording_id))
    return RecordingFacts(recording_id, owner_id, frozenset(pools))
