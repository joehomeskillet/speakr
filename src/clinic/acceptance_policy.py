"""Versioned acceptance participants for one dictation or one routing policy.

Thin store over ``acceptance_rules``. Each published version freezes the
acceptance-required flag and the explicit approvers. Author and admin labels
are never a grant. Decisions and older versions are append-only.

A participant change rechecks a pending accept against the new version.
Historical decision objects stay as stored. A release the caller has marked
invalid stays invalid. Database tables are a later models package; the caller
keeps this ledger.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone

from src.clinic.acceptance_rules import (
    AcceptanceDecision,
    AcceptanceRule,
    can_decide,
    decide,
    final_allowed,
    is_current,
)

SCOPE_DICTATION = "dictation"
SCOPE_ROUTING = "routing"
LABEL_AUTHOR = "author"
LABEL_ADMIN = "admin"

_SCOPES = frozenset((SCOPE_DICTATION, SCOPE_ROUTING))


class PolicyError(ValueError):
    """A policy write or read was rejected. ``code`` is stable."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AcceptancePolicyVersion:
    """One immutable participant set for a dictation or a routing policy."""

    scope: str
    subject_id: str
    version: int
    required: bool
    approver_user_ids: frozenset[int]
    approver_pool_ids: frozenset[str]
    deny_user_ids: frozenset[int]
    recorded_at: datetime

    def as_rule(self) -> AcceptanceRule:
        return AcceptanceRule(
            required=self.required,
            acceptor_user_ids=self.approver_user_ids,
            acceptor_pool_ids=self.approver_pool_ids,
            deny_user_ids=self.deny_user_ids,
        )


@dataclass(frozen=True)
class RecordedDecision:
    """A decision stored against the policy version that was current."""

    policy_version: int
    actor_pool_ids: frozenset[str]
    decision: AcceptanceDecision


@dataclass(frozen=True)
class Revalidation:
    """Outcome of a participant change. ``history`` is the prior decisions."""

    version: AcceptancePolicyVersion
    history: tuple[RecordedDecision, ...]
    pending_valid: bool
    release_valid: bool


class AcceptanceLedger:
    """Append-only versions and decisions for many subjects."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[AcceptancePolicyVersion]] = {}
        self._decisions: dict[tuple[str, str], list[RecordedDecision]] = {}
        self._sealed: dict[tuple[str, str], set[str]] = {}

    def publish(
        self,
        scope: str,
        subject_id: str,
        *,
        required: bool,
        approver_user_ids: Collection[int],
        approver_pool_ids: Collection[str] = (),
        deny_user_ids: Collection[int] = (),
        now: datetime,
        expected_version: int | None = None,
    ) -> AcceptancePolicyVersion:
        """Append a version. The same members and a matching version are a retry.

        The first version accepts ``expected_version`` None or 0. A later call
        must pass the current version. A mismatch raises ``stale_version`` and
        writes nothing.
        """
        key = _key(scope, subject_id)
        stamp = _aware(now)
        users = _users(approver_user_ids)
        pools = _pools(approver_pool_ids)
        deny = _users(deny_user_ids)
        _version_arg(expected_version)
        rows = self._versions.get(key)
        if not rows:
            if expected_version not in (None, 0):
                raise PolicyError("stale_version")
            created = _version(scope, subject_id, 1, required, users, pools, deny, stamp)
            self._versions[key] = [created]
            self._decisions.setdefault(key, [])
            return created
        current = rows[-1]
        same = _same(current, required, users, pools, deny)
        if expected_version is None:
            if same:
                return current
            raise PolicyError("stale_version")
        if expected_version != current.version:
            raise PolicyError("stale_version")
        if same:
            return current
        created = _version(
            scope, subject_id, current.version + 1, required, users, pools, deny, stamp
        )
        rows.append(created)
        return created

    def current(self, scope: str, subject_id: str) -> AcceptancePolicyVersion:
        rows = self._versions.get(_key(scope, subject_id))
        if not rows:
            raise PolicyError("missing_policy")
        return rows[-1]

    def versions(self, scope: str, subject_id: str) -> tuple[AcceptancePolicyVersion, ...]:
        return tuple(self._versions.get(_key(scope, subject_id), ()))

    def decisions(self, scope: str, subject_id: str) -> tuple[RecordedDecision, ...]:
        _key(scope, subject_id)
        return tuple(self._decisions.get((scope, subject_id), ()))

    def decide_acceptance(
        self,
        scope: str,
        subject_id: str,
        *,
        kind: str,
        actor_id: int,
        actor_pool_ids: Collection[str],
        revision_id: str,
        reason: str | None,
        previous_assignee_id: int | None,
        now: datetime,
        labels: Collection[str] = (),
        expected_version: int | None = None,
    ) -> RecordedDecision:
        """Accept or reject against the current version only.

        ``labels`` may name ``author`` or ``admin``. They do not grant or deny.
        A repeated call with the same actor, kind, revision and reason returns
        the stored row. A stale ``expected_version`` writes nothing.
        """
        key = _key(scope, subject_id)
        _check_labels(labels)
        _version_arg(expected_version)
        rows = self._versions.get(key)
        if not rows:
            raise PolicyError("missing_policy")
        current = rows[-1]
        if expected_version is not None and expected_version != current.version:
            raise PolicyError("stale_version")
        pools = _pools(actor_pool_ids)
        decision = decide(
            current.as_rule(),
            kind=kind,
            actor_id=actor_id,
            actor_pool_ids=pools,
            revision_id=revision_id,
            reason=reason,
            previous_assignee_id=previous_assignee_id,
            now=_aware(now),
        )
        stored = self._decisions.setdefault(key, [])
        if stored and _retry(stored[-1], decision, current.version, pools):
            return stored[-1]
        row = RecordedDecision(current.version, pools, decision)
        stored.append(row)
        return row

    def mark_release_invalid(self, scope: str, subject_id: str, revision_id: str) -> None:
        """Remember that this revision's release is invalid. Decisions stay put."""
        key = _key(scope, subject_id)
        _revision(revision_id)
        self._sealed.setdefault(key, set()).add(revision_id)

    def change_participants(
        self,
        scope: str,
        subject_id: str,
        *,
        approver_user_ids: Collection[int],
        approver_pool_ids: Collection[str] = (),
        deny_user_ids: Collection[int] = (),
        required: bool | None = None,
        now: datetime,
        expected_version: int,
        head_revision_id: str,
        release_revision_id: str,
        release_valid: bool,
    ) -> Revalidation:
        """Publish new participants and recheck the pending accept.

        Older decision objects are not replaced. An invalid release stays
        invalid even when ``release_valid`` is passed as True.
        """
        key = _key(scope, subject_id)
        _revision(head_revision_id)
        _revision(release_revision_id)
        if not isinstance(release_valid, bool):
            raise PolicyError("invalid_release")
        current = self.current(scope, subject_id)
        flag = current.required if required is None else required
        version = self.publish(
            scope,
            subject_id,
            required=flag,
            approver_user_ids=approver_user_ids,
            approver_pool_ids=approver_pool_ids,
            deny_user_ids=deny_user_ids,
            now=now,
            expected_version=expected_version,
        )
        sealed = self._sealed.setdefault(key, set())
        if release_revision_id in sealed or not release_valid:
            sealed.add(release_revision_id)
            release_out = False
        else:
            release_out = True
        return Revalidation(
            version=version,
            history=tuple(self._decisions.get(key, ())),
            pending_valid=self._pending(key, head_revision_id),
            release_valid=release_out,
        )

    def may_finish(
        self,
        scope: str,
        subject_id: str,
        *,
        head_is_human_reviewed: bool,
        head_revision_id: str,
    ) -> bool:
        """Finish is allowed only for a current rule and a release that still stands."""
        key = _key(scope, subject_id)
        _revision(head_revision_id)
        if head_revision_id in self._sealed.get(key, ()):
            return False
        version = self.current(scope, subject_id)
        return final_allowed(
            version.as_rule(),
            head_is_human_reviewed=head_is_human_reviewed,
            latest_decision=self._effective(key),
            head_revision_id=head_revision_id,
        )

    def _effective(self, key: tuple[str, str]) -> AcceptanceDecision | None:
        decisions = self._decisions.get(key)
        versions = self._versions.get(key)
        if not decisions or not versions:
            return None
        last = decisions[-1]
        if not can_decide(versions[-1].as_rule(), last.decision.actor_id, last.actor_pool_ids):
            return None
        return last.decision

    def _pending(self, key: tuple[str, str], head_revision_id: str) -> bool:
        decision = self._effective(key)
        if decision is None:
            return False
        return is_current(decision, head_revision_id)


def _key(scope: str, subject_id: str) -> tuple[str, str]:
    if scope not in _SCOPES:
        raise PolicyError("invalid_scope")
    if not isinstance(subject_id, str) or subject_id == "":
        raise PolicyError("invalid_subject")
    return scope, subject_id


def _aware(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PolicyError("naive_datetime")
    return now.astimezone(timezone.utc)


def _revision(revision_id: str) -> None:
    if not isinstance(revision_id, str) or revision_id == "":
        raise PolicyError("invalid_revision")


def _version_arg(expected: int | None) -> None:
    if expected is None:
        return
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise PolicyError("stale_version")


def _users(values: Collection[int]) -> frozenset[int]:
    if isinstance(values, str) or not isinstance(values, Collection):
        raise PolicyError("invalid_approvers")
    return frozenset(values)


def _pools(values: Collection[str]) -> frozenset[str]:
    if isinstance(values, str) or not isinstance(values, Collection):
        raise PolicyError("invalid_pools")
    return frozenset(values)


def _check_labels(labels: Collection[str]) -> None:
    """Labels are read and then ignored. They never change the rule."""
    if isinstance(labels, str) or not isinstance(labels, Collection):
        raise PolicyError("invalid_labels")
    for label in labels:
        if not isinstance(label, str):
            raise PolicyError("invalid_labels")


def _version(
    scope: str,
    subject_id: str,
    number: int,
    required: bool,
    users: frozenset[int],
    pools: frozenset[str],
    deny: frozenset[int],
    now: datetime,
) -> AcceptancePolicyVersion:
    row = AcceptancePolicyVersion(
        scope=scope,
        subject_id=subject_id,
        version=number,
        required=required,
        approver_user_ids=users,
        approver_pool_ids=pools,
        deny_user_ids=deny,
        recorded_at=now,
    )
    row.as_rule()
    return row


def _same(
    row: AcceptancePolicyVersion,
    required: bool,
    users: frozenset[int],
    pools: frozenset[str],
    deny: frozenset[int],
) -> bool:
    return (
        row.required is required
        and row.approver_user_ids == users
        and row.approver_pool_ids == pools
        and row.deny_user_ids == deny
    )


def _retry(
    stored: RecordedDecision,
    decision: AcceptanceDecision,
    version: int,
    pools: frozenset[str],
) -> bool:
    old = stored.decision
    return (
        stored.policy_version == version
        and stored.actor_pool_ids == pools
        and old.kind == decision.kind
        and old.actor_id == decision.actor_id
        and old.revision_id == decision.revision_id
        and old.reason == decision.reason
        and old.handler_id == decision.handler_id
    )
