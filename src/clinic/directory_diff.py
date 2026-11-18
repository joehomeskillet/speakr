"""Versioned, explicitly authorized server-side directory change planning.

The result is a pure state projection. API consumers must persist selected
identity and pool operations through their existing services and audit trail.
Directory reads, identity linking and database writes are outside this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Collection

from src.clinic.directory_reconcile import (
    Action,
    Diff,
    DirectoryEntry,
    DirectoryReconcileError,
    LocalUser,
    compute_diff,
    confirmed_actions,
)


class DirectoryDiffError(DirectoryReconcileError):
    """Rejected directory preview or application, with a machine-readable code."""


@dataclass(frozen=True)
class DirectoryMembership:
    subject_id: str
    pool_kind: str
    pool_id: str


@dataclass(frozen=True)
class LocalMembership:
    user_id: int
    pool_kind: str
    pool_id: str


@dataclass(frozen=True)
class DirectorySnapshot:
    source: str
    version: str
    entries: tuple[DirectoryEntry, ...]
    memberships: tuple[DirectoryMembership, ...] = ()


@dataclass(frozen=True)
class MembershipChange:
    kind: str  # add | remove
    subject_id: str
    user_id: int
    pool_kind: str
    pool_id: str


@dataclass(frozen=True)
class DirectoryPreview:
    snapshot: DirectorySnapshot
    local_users: tuple[LocalUser, ...]
    local_memberships: tuple[LocalMembership, ...]
    managed_pools: frozenset[tuple[str, str]]
    differences: Diff
    actions: tuple[Action, ...]
    membership_changes: tuple[MembershipChange, ...]

    @property
    def source_version(self) -> str:
        return self.snapshot.version


@dataclass(frozen=True)
class AppliedDirectoryDiff:
    actor_id: int
    actions: tuple[Action, ...]
    membership_changes: tuple[MembershipChange, ...]
    local_users: tuple[LocalUser, ...]
    local_memberships: tuple[LocalMembership, ...]


def _enabled(enabled: bool) -> None:
    if enabled is not True:
        raise DirectoryDiffError("disabled", "Directory adapter is disabled")


def _pool(kind: str, pool_id: str) -> None:
    if kind not in ("author", "staff") or not isinstance(pool_id, str) or not pool_id.strip():
        raise DirectoryDiffError("invalid_pool", "Expected an author or staff pool ID")


def _validate_memberships(rows: Collection, row_type: type) -> tuple:
    if not isinstance(rows, Collection) or isinstance(rows, (str, bytes)):
        raise DirectoryDiffError("invalid_type", "Memberships must be a collection")
    values = tuple(rows)
    for row in values:
        if not isinstance(row, row_type):
            raise DirectoryDiffError("invalid_type", "Invalid membership record")
        _pool(row.pool_kind, row.pool_id)
        if row_type is DirectoryMembership:
            if not isinstance(row.subject_id, str) or not row.subject_id.strip():
                raise DirectoryDiffError("invalid_subject", "Missing stable subject identifier")
        elif not isinstance(row.user_id, int) or isinstance(row.user_id, bool):
            raise DirectoryDiffError("invalid_user", "Invalid local user ID")
    if len(set(values)) != len(values):
        raise DirectoryDiffError("ambiguous_membership", "Duplicate membership record")
    return values


def build_preview(
    snapshot: DirectorySnapshot,
    local_users: Collection[LocalUser],
    local_memberships: Collection[LocalMembership] = (),
    managed_pools: Collection[tuple[str, str]] = (),
    *,
    enabled: bool = False,
) -> DirectoryPreview:
    """Preview confirmed-capable changes without touching directory or app data.

    Only explicitly managed pools are compared. Missing/ambiguous subjects do
    not trigger membership removal, protecting against incomplete provider data.
    """
    _enabled(enabled)
    if not isinstance(snapshot, DirectorySnapshot):
        raise DirectoryDiffError("invalid_type", "Expected a directory snapshot")
    if not all(isinstance(value, str) and value.strip() for value in (snapshot.source, snapshot.version)):
        raise DirectoryDiffError("invalid_version", "Source and version are required")
    if not isinstance(local_users, Collection) or isinstance(local_users, (str, bytes)):
        raise DirectoryDiffError("invalid_type", "Local users must be a collection")
    if not isinstance(managed_pools, Collection) or isinstance(managed_pools, (str, bytes)):
        raise DirectoryDiffError("invalid_type", "Managed pools must be a collection")

    users = tuple(local_users)
    entries = tuple(snapshot.entries)
    diff = compute_diff(entries, users)
    if any(entry.object_id is not None and not entry.object_id.strip() for entry in entries):
        raise DirectoryDiffError("invalid_subject", "Stable subject identifier cannot be blank")
    if len({user.id for user in users}) != len(users):
        raise DirectoryDiffError("ambiguous_user", "Duplicate internal user ID")
    source_memberships = _validate_memberships(snapshot.memberships, DirectoryMembership)
    current_memberships = _validate_memberships(local_memberships, LocalMembership)
    try:
        scopes = frozenset(managed_pools)
    except TypeError as exc:
        raise DirectoryDiffError("invalid_pool", "Managed pool must be a pair") from exc
    for scope in scopes:
        if not isinstance(scope, tuple) or len(scope) != 2:
            raise DirectoryDiffError("invalid_pool", "Expected a pool kind and ID")
        _pool(*scope)
    known_ids = {user.id for user in users}
    if any(row.user_id not in known_ids for row in current_memberships):
        raise DirectoryDiffError("invalid_user", "Membership belongs to an unknown user")

    snapshot = replace(snapshot, entries=entries, memberships=source_memberships)
    eligible = {entry.object_id for entry in diff.new}
    eligible.update(entry.object_id for _, entry, _ in diff.changed)
    actions = tuple(confirmed_actions(diff, eligible))

    ambiguous = {entry.object_id for entry in diff.ambiguous}
    linked = {user.external_id: user for user in users if user.external_id is not None}
    desired: dict[str, set[tuple[str, str]]] = {}
    for row in source_memberships:
        desired.setdefault(row.subject_id, set()).add((row.pool_kind, row.pool_id))
    existing: dict[int, set[tuple[str, str]]] = {}
    for row in current_memberships:
        scope = (row.pool_kind, row.pool_id)
        if scope in scopes:
            existing.setdefault(row.user_id, set()).add(scope)
    changes: list[MembershipChange] = []
    for entry in entries:
        subject = entry.object_id
        if subject is None or subject in ambiguous or subject not in linked:
            continue
        user = linked[subject]
        current = existing.get(user.id, set())
        wanted = desired.get(subject, set()) & scopes if entry.enabled else set()
        for kind, pool_id in current - wanted:
            changes.append(MembershipChange("remove", subject, user.id, kind, pool_id))
        for kind, pool_id in wanted - current:
            changes.append(MembershipChange("add", subject, user.id, kind, pool_id))

    changes.sort(key=lambda item: (item.pool_kind, item.pool_id, item.user_id, item.kind))
    return DirectoryPreview(snapshot, users, current_memberships, scopes, diff, actions, tuple(changes))


def apply_preview(
    preview: DirectoryPreview,
    current_snapshot: DirectorySnapshot,
    current_users: Collection[LocalUser],
    current_memberships: Collection[LocalMembership],
    *,
    selected_actions: Collection[Action] = (),
    selected_memberships: Collection[MembershipChange] = (),
    actor_id: int | None = None,
    authorize: Callable[[int, Action | MembershipChange], bool] | None = None,
    enabled: bool = False,
) -> AppliedDirectoryDiff:
    """Project only selected authorized changes against the exact preview state.

    New users remain `create_pending_user` actions for the identity service.
    Existing local IDs are retained and no clinical records are accessed.
    """
    _enabled(enabled)
    if not isinstance(preview, DirectoryPreview):
        raise DirectoryDiffError("invalid_type", "Expected a directory preview")
    if not isinstance(current_snapshot, DirectorySnapshot) or (
        current_snapshot.source != preview.snapshot.source
        or current_snapshot.version != preview.source_version
    ):
        raise DirectoryDiffError("stale_version", "Directory source/version changed")
    fresh = build_preview(
        current_snapshot, current_users, current_memberships, preview.managed_pools, enabled=True
    )
    if fresh != preview:
        raise DirectoryDiffError("stale_state", "Snapshot or local state changed")
    if not isinstance(actor_id, int) or isinstance(actor_id, bool) or not callable(authorize):
        raise DirectoryDiffError("unauthorized", "An authorized actor is required")
    try:
        actions = set(selected_actions)
        memberships = set(selected_memberships)
    except (TypeError, ValueError) as exc:
        raise DirectoryDiffError("invalid_selection", "Invalid selected changes") from exc
    if actions - set(preview.actions) or memberships - set(preview.membership_changes):
        raise DirectoryDiffError("invalid_selection", "Selected change is absent from preview")

    picked_actions = tuple(action for action in preview.actions if action in actions)
    picked_memberships = tuple(row for row in preview.membership_changes if row in memberships)
    for change in (*picked_actions, *picked_memberships):
        if authorize(actor_id, change) is not True:
            raise DirectoryDiffError("unauthorized", "Application authority denied")

    by_subject = {entry.object_id: entry for entry in preview.snapshot.entries}
    updated = {user.id: user for user in preview.local_users}
    for action in picked_actions:
        if action.kind == "update_display_name":
            user = updated[action.local_user_id]
            updated[user.id] = replace(user, display_name=by_subject[action.object_id].display_name)
        elif action.kind == "deactivate":
            user = updated[action.local_user_id]
            updated[user.id] = replace(user, active=False)

    projected = set(preview.local_memberships)
    for change in picked_memberships:
        row = LocalMembership(change.user_id, change.pool_kind, change.pool_id)
        if change.kind == "add":
            projected.add(row)
        else:
            projected.remove(row)
    return AppliedDirectoryDiff(
        actor_id,
        picked_actions,
        picked_memberships,
        tuple(updated[user.id] for user in preview.local_users),
        tuple(sorted(projected, key=lambda row: (row.pool_kind, row.pool_id, row.user_id))),
    )
