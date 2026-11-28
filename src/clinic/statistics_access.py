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

Grants and their versions live in the clinic database. Reports read them
through the same session used for the aggregate query.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.clinic import statistics_grants
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.policy import Decision, RecordingFacts, Subject, decide
from src.clinic.statistics import StatisticsQuery, StatisticsResult, summarize

AGGREGATE = "aggregate"
AggregateGrant = statistics_grants.AggregateGrant
StatisticsAccessError = statistics_grants.StatisticsGrantError


def version_of(user_id: int, *, session: Session | None = None) -> int:
    """Grant-set version for ``user_id``. Unknown accounts are version 0."""
    _require_clinic()
    return statistics_grants.version_of(user_id, session=session)


def grant_aggregate(
    user_id: int,
    scope: str,
    *,
    author_ids: Iterable[int] = (),
    pool_ids: Iterable[str] = (),
    expected_version: int | None = None,
    session: Session | None = None,
) -> AggregateGrant:
    """Store one author or author-pool aggregate grant and bump the version."""
    _require_clinic()
    return statistics_grants.grant_aggregate(
        user_id, scope, author_ids=author_ids, pool_ids=pool_ids,
        expected_version=expected_version, session=session,
    )


def revoke_aggregate(
    user_id: int,
    grant_id: str,
    *,
    expected_version: int | None = None,
    session: Session | None = None,
) -> int:
    """Remove one grant. The next read no longer includes its dictations."""
    _require_clinic()
    return statistics_grants.revoke_aggregate(
        user_id, grant_id, expected_version=expected_version, session=session,
    )


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
    grants = statistics_grants.grants_for(user_id, expected_version=expected_version, session=session)
    if not grants:
        raise StatisticsAccessError("no_grant")
    author_ids, pool_ids = _union(grants)
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


def _union(grants: Iterable[AggregateGrant]) -> tuple[frozenset[int], frozenset[str]]:
    authors: set[int] = set()
    pools: set[str] = set()
    for grant in grants:
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
