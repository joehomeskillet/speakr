"""Role projection of a dictation for list and detail views (V8 P01, P04).

One batch function answers three questions per dictation: who dictated it
(the author, staff data), which staff pools are linked to the dictation's
author pool (an empty list means "Kein Pool zugewiesen" in the UI) and who
holds the review lease right now (an active lease only). It carries identifiers
and staff display names only: never patient names, titles or patient references.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from src.clinic.lease import _as_utc
from src.clinic.models import ClinicDictation, ClinicReviewLease
from src.clinic.pools import ClinicPoolRoute, ClinicStaffPool
from src.clinic.worklist_entries import stamp
from src.database import db
from src.models import User

_CHUNK = 500


def display_name(user: User | None) -> str | None:
    """Staff display name: the full name when set, else the account name."""
    if user is None:
        return None
    return user.name or user.username


def _chunks(values: list):
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def _person(user: User | None) -> dict | None:
    return None if user is None else {"id": str(user.id), "display_name": display_name(user)}


def project_roles(
    dictations: Iterable[ClinicDictation], now: datetime | None = None
) -> dict[str, dict]:
    """Map dictation id to ``dictated_by``, ``staff_pools`` and ``lease_holder``.

    ``lease_holder`` is set only while the lease has not expired at ``now``
    (default: the current time).
    """
    rows = list(dictations)
    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    ids = [row.id for row in rows]

    user_ids = {row.author_user_id for row in rows}
    leases: dict[str, ClinicReviewLease] = {}
    for part in _chunks(ids):
        for lease in db.session.query(ClinicReviewLease).filter(ClinicReviewLease.dictation_id.in_(part)):
            if _as_utc(lease.expires_at) > now:
                leases[lease.dictation_id] = lease
                user_ids.add(lease.holder_user_id)
    users = {}
    for part in _chunks(sorted(user_ids)):
        users.update({user.id: user for user in db.session.query(User).filter(User.id.in_(part))})

    author_pools = sorted({row.pool_id for row in rows if row.pool_id is not None})
    staff: dict[str, list[dict]] = {}
    for part in _chunks(author_pools):
        query = (
            db.session.query(ClinicPoolRoute.author_pool_id, ClinicStaffPool)
            .join(ClinicStaffPool, ClinicStaffPool.id == ClinicPoolRoute.staff_pool_id)
            .filter(ClinicPoolRoute.author_pool_id.in_(part))
        )
        for author_pool_id, pool in query:
            staff.setdefault(author_pool_id, []).append({"id": pool.id, "name": pool.name})
    for pools in staff.values():
        pools.sort(key=lambda pool: (pool["name"] or "", pool["id"]))

    result = {}
    for row in rows:
        lease = leases.get(row.id)
        holder = None
        if lease is not None:
            person = _person(users.get(lease.holder_user_id)) or {
                "id": str(lease.holder_user_id), "display_name": None}
            holder = dict(person, until=stamp(_as_utc(lease.expires_at)))
        result[row.id] = {
            "dictated_by": _person(users.get(row.author_user_id)),
            "staff_pools": list(staff.get(row.pool_id, [])),
            "lease_holder": holder,
        }
    return result
