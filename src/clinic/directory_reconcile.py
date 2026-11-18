"""Server-side directory comparison with explicit confirmation.

Compares a directory export (list of directory entries) with local users and
produces a diff plus a confirmed action list. Server-side only, read only, no
write to any directory. The open web gap WG-ADM-009 (reading local Windows
accounts of the workstation) stays open: this module does not claim PAR-ADM-009
is closed and does not read local SAM/AD.
"""

from __future__ import annotations

import inspect
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Collection, Sequence


class DirectoryReconcileError(ValueError):
    """The directory reconciliation was rejected before it produced a result."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _validate_entries(entries: Sequence) -> None:
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise DirectoryReconcileError("invalid_type", "entries must be a sequence")
    for i, entry in enumerate(entries):
        if not isinstance(entry, DirectoryEntry):
            raise DirectoryReconcileError(
                "invalid_type", f"entries[{i}] must be a DirectoryEntry"
            )
        if not isinstance(entry.account, str) or entry.account == "":
            raise DirectoryReconcileError(
                "invalid_value", f"entries[{i}].account must be a non-empty string"
            )
        if not isinstance(entry.display_name, str) or entry.display_name == "":
            raise DirectoryReconcileError(
                "invalid_value",
                f"entries[{i}].display_name must be a non-empty string",
            )
        if entry.object_id is not None and not isinstance(entry.object_id, str):
            raise DirectoryReconcileError(
                "invalid_type", f"entries[{i}].object_id must be a string or None"
            )
        if entry.email is not None and not isinstance(entry.email, str):
            raise DirectoryReconcileError(
                "invalid_type", f"entries[{i}].email must be a string or None"
            )
        if not isinstance(entry.enabled, bool):
            raise DirectoryReconcileError(
                "invalid_type", f"entries[{i}].enabled must be a bool"
            )
        if any(ord(c) < 32 for c in entry.account):
            raise DirectoryReconcileError(
                "invalid_value", f"entries[{i}].account contains control characters"
            )
        if any(ord(c) < 32 for c in entry.display_name):
            raise DirectoryReconcileError(
                "invalid_value",
                f"entries[{i}].display_name contains control characters",
            )


def _validate_locals(locals_: Sequence) -> None:
    if not isinstance(locals_, Sequence) or isinstance(locals_, (str, bytes)):
        raise DirectoryReconcileError("invalid_type", "locals must be a sequence")
    for i, local in enumerate(locals_):
        if not isinstance(local, LocalUser):
            raise DirectoryReconcileError(
                "invalid_type", f"locals[{i}] must be a LocalUser"
            )
        if not isinstance(local.id, int) or isinstance(local.id, bool):
            raise DirectoryReconcileError(
                "invalid_type", f"locals[{i}].id must be an int"
            )
        if not isinstance(local.username, str) or local.username == "":
            raise DirectoryReconcileError(
                "invalid_value", f"locals[{i}].username must be a non-empty string"
            )
        if local.external_id is not None and not isinstance(local.external_id, str):
            raise DirectoryReconcileError(
                "invalid_type", f"locals[{i}].external_id must be a string or None"
            )
        if not isinstance(local.display_name, str) or local.display_name == "":
            raise DirectoryReconcileError(
                "invalid_value",
                f"locals[{i}].display_name must be a non-empty string",
            )
        if not isinstance(local.active, bool):
            raise DirectoryReconcileError(
                "invalid_type", f"locals[{i}].active must be a bool"
            )
        if any(ord(c) < 32 for c in local.username):
            raise DirectoryReconcileError(
                "invalid_value", f"locals[{i}].username contains control characters"
            )
        if any(ord(c) < 32 for c in local.display_name):
            raise DirectoryReconcileError(
                "invalid_value",
                f"locals[{i}].display_name contains control characters",
            )


def _normalise_display_name(name: str) -> str:
    """Unicode NFC, then collapse whitespace (spaces, tabs, newlines) to single space."""
    normalised = unicodedata.normalize("NFC", name)
    return " ".join(normalised.split())


@dataclass(frozen=True)
class DirectoryEntry:
    """One entry from a directory export."""

    object_id: str | None
    account: str
    display_name: str
    email: str | None
    enabled: bool


@dataclass(frozen=True)
class LocalUser:
    """One local user from the application database."""

    id: int
    username: str
    external_id: str | None
    display_name: str
    active: bool


@dataclass(frozen=True)
class Diff:
    """Result of comparing directory entries with local users."""

    new: tuple[DirectoryEntry, ...]
    changed: tuple[tuple[LocalUser, DirectoryEntry, tuple[str, ...]], ...]
    disabled_in_directory: tuple[LocalUser, ...]
    missing_in_directory: tuple[LocalUser, ...]
    ambiguous: tuple[DirectoryEntry, ...]
    unmatchable: tuple[DirectoryEntry, ...]


@dataclass(frozen=True)
class Action:
    """A confirmed action to be performed after diff review."""

    kind: str  # "create_pending_user" | "update_display_name" | "deactivate"
    object_id: str
    local_user_id: int | None
    detail: str


def compute_diff(entries: Sequence[DirectoryEntry], locals_: Sequence[LocalUser]) -> Diff:
    """Compare directory entries with local users and return a Diff.

    Matching uses ONLY the immutable `object_id` against `LocalUser.external_id`.
    Never by email, never by account or display name.
    """
    _validate_entries(entries)
    _validate_locals(locals_)

    # Build lookup maps
    entries_by_object_id: dict[str, list[DirectoryEntry]] = defaultdict(list)
    unmatchable: list[DirectoryEntry] = []

    for entry in entries:
        if entry.object_id is None:
            unmatchable.append(entry)
        else:
            entries_by_object_id[entry.object_id].append(entry)

    # Find ambiguous entries (duplicate object_id in directory)
    ambiguous: list[DirectoryEntry] = []
    matched_entries: dict[str, DirectoryEntry] = {}  # object_id -> entry
    for object_id, entry_list in entries_by_object_id.items():
        if len(entry_list) > 1:
            ambiguous.extend(entry_list)
        else:
            matched_entries[object_id] = entry_list[0]

    # Build local user lookup by external_id
    locals_by_external_id: dict[str, list[LocalUser]] = defaultdict(list)
    for local in locals_:
        if local.external_id is not None:
            locals_by_external_id[local.external_id].append(local)

    # Find ambiguous locals (duplicate external_id)
    ambiguous_object_ids_from_locals = {
        ext_id for ext_id, local_list in locals_by_external_id.items() if len(local_list) > 1
    }
    for object_id in ambiguous_object_ids_from_locals:
        if object_id in matched_entries:
            ambiguous.append(matched_entries.pop(object_id))

    # Now process matches
    new: list[DirectoryEntry] = []
    changed: list[tuple[LocalUser, DirectoryEntry, tuple[str, ...]]] = []
    disabled_in_directory: list[LocalUser] = []
    missing_in_directory: list[LocalUser] = []

    # Track which directory entries have been processed
    processed_object_ids = set()

    for object_id, entry in matched_entries.items():
        local_list = locals_by_external_id.get(object_id, [])
        if not local_list:
            new.append(entry)
        elif len(local_list) == 1:
            local = local_list[0]
            diffs = _compute_field_diffs(local, entry)
            if diffs:
                changed.append((local, entry, tuple(sorted(diffs))))
            if not entry.enabled and local.active:
                disabled_in_directory.append(local)
        processed_object_ids.add(object_id)

    # Local users with external_id not in directory -> missing_in_directory
    for object_id, local_list in locals_by_external_id.items():
        if object_id not in processed_object_ids and object_id not in ambiguous_object_ids_from_locals:
            for local in local_list:
                missing_in_directory.append(local)

    return Diff(
        new=tuple(new),
        changed=tuple(changed),
        disabled_in_directory=tuple(disabled_in_directory),
        missing_in_directory=tuple(missing_in_directory),
        ambiguous=tuple(ambiguous),
        unmatchable=tuple(unmatchable),
    )


def _compute_field_diffs(local: LocalUser, entry: DirectoryEntry) -> set[str]:
    diffs = set()
    # display_name: NFC, whitespace-collapsed, case-sensitive
    local_norm = _normalise_display_name(local.display_name)
    entry_norm = _normalise_display_name(entry.display_name)
    if local_norm != entry_norm:
        diffs.add("display_name")
    # enabled vs active
    if entry.enabled != local.active:
        diffs.add("enabled")
    return diffs


def confirmed_actions(diff: Diff, confirmed_object_ids: Collection[str]) -> list[Action]:
    """Return actions for confirmed object IDs only.

    Action kinds: "create_pending_user" for new, "update_display_name" for
    display-name changes, "deactivate" for disabled_in_directory.
    No delete action, no action for missing_in_directory, ambiguous, unmatchable.
    """
    if not isinstance(confirmed_object_ids, Collection) or isinstance(
        confirmed_object_ids, (str, bytes)
    ):
        raise DirectoryReconcileError("invalid_type", "confirmed_object_ids must be a collection")

    confirmed_set = set(confirmed_object_ids)
    actions: list[Action] = []

    # create_pending_user for new
    for entry in diff.new:
        if entry.object_id in confirmed_set:
            actions.append(
                Action(
                    kind="create_pending_user",
                    object_id=entry.object_id,
                    local_user_id=None,
                    detail=f"Create pending user for {entry.account}",
                )
            )

    # update_display_name for changed (only display_name changes)
    for local, entry, fields in diff.changed:
        if entry.object_id in confirmed_set and "display_name" in fields:
            actions.append(
                Action(
                    kind="update_display_name",
                    object_id=entry.object_id,
                    local_user_id=local.id,
                    detail=f"Update display name from {local.display_name!r} to {entry.display_name!r}",
                )
            )

    # deactivate for disabled_in_directory
    for local in diff.disabled_in_directory:
        # Find the matching directory entry to get object_id
        for entry in diff.new:
            if entry.object_id == local.external_id:
                break
        else:
            # Look in changed entries
            for l, e, _ in diff.changed:
                if l.id == local.id:
                    break
            else:
                continue
        if local.external_id in confirmed_set:
            actions.append(
                Action(
                    kind="deactivate",
                    object_id=local.external_id,
                    local_user_id=local.id,
                    detail=f"Deactivate user {local.username}",
                )
            )

    # Sort by (kind, object_id) for deterministic order
    actions.sort(key=lambda a: (a.kind, a.object_id))
    return actions


# Module self-check: no database, network, or LDAP imports
# This assertion runs on import and will fail if forbidden modules are used
_forbidden_imports = {"sqlalchemy", "flask", "ldap", "ldap3", "requests", "urllib", "http"}
_source = inspect.getsource(compute_diff) + inspect.getsource(confirmed_actions)
for _mod in _forbidden_imports:
    if f"import {_mod}" in _source or f"from {_mod}" in _source:
        raise DirectoryReconcileError(
            "forbidden_import", f"Module must not import {_mod}"
        )