"""Explicit scope for a statistics account.

``statistics.summarize`` counts every dictation in the session and does not
check the caller. This module is that check. A statistics account may read
aggregates only inside an explicit ``author`` or ``author_pool`` grant. The
statistics role does not grant audio, transcript, export, identity
administration or scope ``all``.

Rows outside the current grants are kept out of the ``summarize`` call, so
counts and ``excluded_missing_timestamp`` do not include them. Revoking a
grant changes the next read. A caller that sends the version it last saw
gets ``stale_version`` when the grants have moved, and a rejected change
does not bump the version.

shortcut: process-local grant store, replace it when grants must survive a
process restart.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.policy import Decision, RecordingFacts, Subject, decide
from src.clinic.statistics import StatisticsQuery, StatisticsResult, summarize

AGGREGATE = "aggregate"
_GRANTABLE = frozenset({"author", "author_pool"})


class StatisticsAccessError(Exception):
    """The statistics read or grant change cannot proceed. ``code`` is stable."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AggregateGrant:
    """One explicit aggregate grant, after it has been stored."""

    id: str
    user_id: int
    scope: str
    author_ids: frozenset[int]
    pool_ids: frozenset[str]
    version: int


@dataclass
class _StoredGrant:
    id: str
    scope: str
    author_ids: frozenset[int]
    pool_ids: frozenset[str]


@dataclass
class _Account:
    version: int = 0
    grants: dict[str, _StoredGrant] = field(default_factory=dict)


_accounts: dict[int, _Account] = {}


def clear_grants() -> None:
    """Drop every stored grant. Tests use this between cases."""
    _accounts.clear()


def version_of(user_id: int) -> int:
    """Grant-set version for ``user_id``. Unknown accounts are version 0."""
    _require_clinic()
    user_id = _user_id(user_id)
    account = _accounts.get(user_id)
    if account is None:
        return 0
    return account.version


def grant_aggregate(
    user_id: int,
    scope: str,
    *,
    author_ids: Iterable[int] = (),
    pool_ids: Iterable[str] = (),
    expected_version: int | None = None,
) -> AggregateGrant:
    """Store one author or author-pool aggregate grant and bump the version."""
    _require_clinic()
    user_id = _user_id(user_id)
    scope, authors, pools = _scope(scope, author_ids, pool_ids)
    account, current = _current(user_id)
    _check_version(current, expected_version)
    if account is None:
        account = _Account()
        _accounts[user_id] = account
    grant_id = str(uuid.uuid4())
    account.grants[grant_id] = _StoredGrant(grant_id, scope, authors, pools)
    account.version += 1
    return AggregateGrant(grant_id, user_id, scope, authors, pools, account.version)


def revoke_aggregate(
    user_id: int,
    grant_id: str,
    *,
    expected_version: int | None = None,
) -> int:
    """Remove one grant. The next read no longer includes its dictations."""
    _require_clinic()
    user_id = _user_id(user_id)
    if not isinstance(grant_id, str) or not grant_id:
        raise StatisticsAccessError("unknown_grant")
    account, current = _current(user_id)
    _check_version(current, expected_version)
    if account is None or grant_id not in account.grants:
        raise StatisticsAccessError("unknown_grant")
    del account.grants[grant_id]
    account.version += 1
    return account.version


def read_aggregates(
    session: Session,
    user_id: int,
    query: StatisticsQuery,
    *,
    role: str,
    capability: str = AGGREGATE,
    expected_version: int | None = None,
) -> StatisticsResult:
    """Aggregates inside the account's current grants. Nothing is loaded before the checks."""
    _require_clinic()
    user_id = _user_id(user_id)
    if role != "statistics":
        raise StatisticsAccessError("not_statistics")
    if capability != AGGREGATE:
        raise StatisticsAccessError("capability_denied")
    account, current = _current(user_id)
    _check_version(current, expected_version)
    if account is None or not account.grants:
        raise StatisticsAccessError("no_grant")
    author_ids, pool_ids = _union(account)
    # shortcut: wraps session.query for this call only, move the scope into
    # summarize if that function learns an explicit dictation filter.
    with _limited(session, author_ids, pool_ids):
        return summarize(session, query)


def recording_access(user_id: int, action: str, recording: RecordingFacts) -> Decision:
    """Recording decision with no statistics grant attached.

    The statistics role is not a recording grant. Policy decides the action
    from an empty grant list, so export, playback and the other recording
    actions stay denied unless the caller holds a separate recording grant.
    """
    _require_clinic()
    user_id = _user_id(user_id)
    return decide(Subject(user_id=user_id), action, recording)


def _require_clinic() -> None:
    if not clinic_mode_enabled():
        raise StatisticsAccessError("clinic_mode_off")


def _user_id(user_id: int) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise StatisticsAccessError("user_id")
    return user_id


def _scope(
    scope: str,
    author_ids: Iterable[int],
    pool_ids: Iterable[str],
) -> tuple[str, frozenset[int], frozenset[str]]:
    if not isinstance(scope, str):
        raise StatisticsAccessError("unknown_scope")
    name = scope.strip().lower()
    if name == "all":
        raise StatisticsAccessError("all_scope_forbidden")
    if name not in _GRANTABLE:
        raise StatisticsAccessError("scope_not_grantable")
    authors = _members(author_ids, "author_id")
    pools = _members(pool_ids, "pool_id")
    if name == "author" and pools:
        raise StatisticsAccessError("scope_mismatch")
    if name == "author_pool" and authors:
        raise StatisticsAccessError("scope_mismatch")
    if name == "author" and not authors:
        raise StatisticsAccessError("empty_scope")
    if name == "author_pool" and not pools:
        raise StatisticsAccessError("empty_scope")
    return name, authors, pools


def _members(values: Iterable, code: str) -> frozenset:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise StatisticsAccessError(code)
    found = []
    for value in values:
        if code == "author_id":
            if isinstance(value, bool) or not isinstance(value, int):
                raise StatisticsAccessError(code)
        elif not isinstance(value, str) or not value or value != value.strip():
            raise StatisticsAccessError(code)
        found.append(value)
    if len(found) != len(set(found)):
        raise StatisticsAccessError(code)
    return frozenset(found)


def _current(user_id: int) -> tuple[_Account | None, int]:
    account = _accounts.get(user_id)
    if account is None:
        return None, 0
    return account, account.version


def _check_version(current: int, expected: int | None) -> None:
    if expected is None:
        return
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise StatisticsAccessError("version")
    if expected != current:
        raise StatisticsAccessError("stale_version")


def _union(account: _Account) -> tuple[frozenset[int], frozenset[str]]:
    authors: set[int] = set()
    pools: set[str] = set()
    for grant in account.grants.values():
        authors.update(grant.author_ids)
        pools.update(grant.pool_ids)
    return frozenset(authors), frozenset(pools)


def _criterion(author_ids: frozenset[int], pool_ids: frozenset[str]):
    parts = []
    if author_ids:
        parts.append(ClinicDictation.author_user_id.in_(tuple(author_ids)))
    if pool_ids:
        parts.append(ClinicDictation.pool_id.in_(tuple(pool_ids)))
    if not parts:
        raise StatisticsAccessError("no_grant")
    if len(parts) == 1:
        return parts[0]
    return or_(*parts)


def _mentions_dictation(entity) -> bool:
    return entity is ClinicDictation or getattr(entity, "class_", None) is ClinicDictation


@contextmanager
def _limited(session: Session, author_ids: frozenset[int], pool_ids: frozenset[str]):
    criterion = _criterion(author_ids, pool_ids)
    original = session.query

    def query(*entities, **kwargs):
        built = original(*entities, **kwargs)
        if any(_mentions_dictation(entity) for entity in entities):
            return built.filter(criterion)
        return built

    session.query = query
    try:
        yield
    finally:
        session.query = original
