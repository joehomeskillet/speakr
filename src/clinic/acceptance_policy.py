"""Versioned acceptance participants for one dictation or one routing policy.

Thin store over ``acceptance_rules``. Each published version freezes the
acceptance-required flag and the explicit approvers. Author and admin labels
are never a grant. Decisions and older versions are append-only.

A participant change rechecks a pending accept against the new version.
Historical decision objects stay as stored. A release the caller has marked
invalid stays invalid.

With an application context the ledger is durable: versions, decisions and
sealed releases live in ``clinic_acceptance_*`` tables, writes join the
caller's transaction (savepoint, no commit) and a unique key resolves
concurrent duplicates. Without an application context the ledger keeps its
state in process memory, as before.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import has_app_context
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.database import db
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


class ClinicAcceptancePolicyVersion(db.Model):  # type: ignore[name-defined]
    """One immutable published participant set. Unique per subject and version."""

    __tablename__ = "clinic_acceptance_policy_version"
    __table_args__ = (db.UniqueConstraint("scope", "subject_id", "version",
                                          name="uq_clinic_acceptance_policy_version"),)

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=False)
    subject_id = db.Column(db.String(128), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    required = db.Column(db.Boolean, nullable=False)
    approver_user_ids = db.Column(db.Text, nullable=False)
    approver_pool_ids = db.Column(db.Text, nullable=False)
    deny_user_ids = db.Column(db.Text, nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False)


class ClinicAcceptanceDecision(db.Model):  # type: ignore[name-defined]
    """One stored accept or reject decision. ``seq`` orders them per subject."""

    __tablename__ = "clinic_acceptance_decision"
    __table_args__ = (db.UniqueConstraint("scope", "subject_id", "seq",
                                          name="uq_clinic_acceptance_decision_seq"),)

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=False)
    subject_id = db.Column(db.String(128), nullable=False)
    seq = db.Column(db.Integer, nullable=False)
    policy_version = db.Column(db.Integer, nullable=False)
    actor_pool_ids = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    actor_id = db.Column(db.Integer, nullable=False)
    revision_id = db.Column(db.String(128), nullable=False)
    reason = db.Column(db.Text)
    handler_id = db.Column(db.Integer)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=False)


class ClinicAcceptanceSeal(db.Model):  # type: ignore[name-defined]
    """A revision whose release the caller marked invalid. Never lifted."""

    __tablename__ = "clinic_acceptance_seal"

    scope = db.Column(db.String(16), primary_key=True)
    subject_id = db.Column(db.String(128), primary_key=True)
    revision_id = db.Column(db.String(128), primary_key=True)


def _append_only(_mapper, _connection, _row) -> None:
    raise PolicyError("append_only")


for _model in (ClinicAcceptancePolicyVersion, ClinicAcceptanceDecision, ClinicAcceptanceSeal):
    event.listen(_model, "before_update", _append_only)
    event.listen(_model, "before_delete", _append_only)


def _dump(values) -> str:
    return json.dumps(sorted(values))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class _MemoryStore:
    """Process-local state for use without an application context."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[AcceptancePolicyVersion]] = {}
        self._decisions: dict[tuple[str, str], list[RecordedDecision]] = {}
        self._sealed: dict[tuple[str, str], set[str]] = {}

    def versions(self, key):
        return list(self._versions.get(key, ()))

    def add_version(self, row: AcceptancePolicyVersion) -> bool:
        self._versions.setdefault((row.scope, row.subject_id), []).append(row)
        return True

    def decisions(self, key):
        return list(self._decisions.get(key, ()))

    def add_decision(self, key, seq: int, row: RecordedDecision) -> bool:
        self._decisions.setdefault(key, []).append(row)
        return True

    def sealed(self, key) -> set[str]:
        return set(self._sealed.get(key, ()))

    def seal(self, key, revision_id: str) -> None:
        self._sealed.setdefault(key, set()).add(revision_id)


class _DbStore:
    """Rows in the application database, written inside the caller's transaction."""

    def versions(self, key):
        rows = (ClinicAcceptancePolicyVersion.query
                .filter_by(scope=key[0], subject_id=key[1])
                .order_by(ClinicAcceptancePolicyVersion.version).all())
        return [AcceptancePolicyVersion(
            scope=r.scope, subject_id=r.subject_id, version=r.version, required=bool(r.required),
            approver_user_ids=frozenset(json.loads(r.approver_user_ids)),
            approver_pool_ids=frozenset(json.loads(r.approver_pool_ids)),
            deny_user_ids=frozenset(json.loads(r.deny_user_ids)),
            recorded_at=_utc(r.recorded_at)) for r in rows]

    def add_version(self, row: AcceptancePolicyVersion) -> bool:
        return _insert(ClinicAcceptancePolicyVersion(
            scope=row.scope, subject_id=row.subject_id, version=row.version, required=row.required,
            approver_user_ids=_dump(row.approver_user_ids),
            approver_pool_ids=_dump(row.approver_pool_ids),
            deny_user_ids=_dump(row.deny_user_ids), recorded_at=row.recorded_at))

    def decisions(self, key):
        rows = (ClinicAcceptanceDecision.query
                .filter_by(scope=key[0], subject_id=key[1])
                .order_by(ClinicAcceptanceDecision.seq).all())
        return [RecordedDecision(
            policy_version=r.policy_version,
            actor_pool_ids=frozenset(json.loads(r.actor_pool_ids)),
            decision=AcceptanceDecision(
                kind=r.kind, actor_id=r.actor_id, revision_id=r.revision_id, reason=r.reason,
                handler_id=r.handler_id, at=_utc(r.decided_at))) for r in rows]

    def add_decision(self, key, seq: int, row: RecordedDecision) -> bool:
        d = row.decision
        return _insert(ClinicAcceptanceDecision(
            scope=key[0], subject_id=key[1], seq=seq, policy_version=row.policy_version,
            actor_pool_ids=_dump(row.actor_pool_ids), kind=d.kind, actor_id=d.actor_id,
            revision_id=d.revision_id, reason=d.reason, handler_id=d.handler_id, decided_at=d.at))

    def sealed(self, key) -> set[str]:
        rows = ClinicAcceptanceSeal.query.filter_by(scope=key[0], subject_id=key[1]).all()
        return {r.revision_id for r in rows}

    def seal(self, key, revision_id: str) -> None:
        if revision_id not in self.sealed(key):
            _insert(ClinicAcceptanceSeal(scope=key[0], subject_id=key[1], revision_id=revision_id))


def _insert(row) -> bool:
    """Insert in a savepoint. False when the unique key is already taken."""
    try:
        with db.session.begin_nested():
            db.session.add(row)
    except IntegrityError:
        return False
    return True


class AcceptanceLedger:
    """Append-only versions and decisions for many subjects."""

    def __init__(self) -> None:
        self._memory = _MemoryStore()

    @property
    def _store(self):
        return _DbStore() if has_app_context() else self._memory

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
        store = self._store
        # Second pass only after losing a race on the unique (subject, version) key.
        for _attempt in range(2):
            rows = store.versions(key)
            if not rows:
                if expected_version not in (None, 0):
                    raise PolicyError("stale_version")
                created = _version(scope, subject_id, 1, required, users, pools, deny, stamp)
            else:
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
            if store.add_version(created):
                return created
        raise PolicyError("stale_version")

    def current(self, scope: str, subject_id: str) -> AcceptancePolicyVersion:
        rows = self._store.versions(_key(scope, subject_id))
        if not rows:
            raise PolicyError("missing_policy")
        return rows[-1]

    def versions(self, scope: str, subject_id: str) -> tuple[AcceptancePolicyVersion, ...]:
        return tuple(self._store.versions(_key(scope, subject_id)))

    def decisions(self, scope: str, subject_id: str) -> tuple[RecordedDecision, ...]:
        return tuple(self._store.decisions(_key(scope, subject_id)))

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
        store = self._store
        rows = store.versions(key)
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
        row = RecordedDecision(current.version, pools, decision)
        # A lost race on the unique (subject, seq) key re-reads and tries again.
        for _attempt in range(3):
            stored = store.decisions(key)
            if stored and _retry(stored[-1], decision, current.version, pools):
                return stored[-1]
            if store.add_decision(key, len(stored) + 1, row):
                return row
        raise PolicyError("stale_version")

    def mark_release_invalid(self, scope: str, subject_id: str, revision_id: str) -> None:
        """Remember that this revision's release is invalid. Decisions stay put."""
        key = _key(scope, subject_id)
        _revision(revision_id)
        self._store.seal(key, revision_id)

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
        store = self._store
        if release_revision_id in store.sealed(key) or not release_valid:
            store.seal(key, release_revision_id)
            release_out = False
        else:
            release_out = True
        return Revalidation(
            version=version,
            history=tuple(store.decisions(key)),
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
        if head_revision_id in self._store.sealed(key):
            return False
        version = self.current(scope, subject_id)
        return final_allowed(
            version.as_rule(),
            head_is_human_reviewed=head_is_human_reviewed,
            latest_decision=self._effective(key),
            head_revision_id=head_revision_id,
        )

    def _effective(self, key: tuple[str, str]) -> AcceptanceDecision | None:
        store = self._store
        decisions = store.decisions(key)
        versions = store.versions(key)
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
