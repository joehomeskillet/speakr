"""Server-selected, read-only snapshots for directory comparison."""

import json
from functools import partial
from pathlib import Path
from typing import Callable

from src.clinic.directory_diff import DirectoryMembership, DirectorySnapshot, build_preview
from src.clinic.directory_reconcile import DirectoryEntry
from src.clinic.ldap_profile import LdapCapabilityError

PROVIDER_EXTENSION = "clinic_directory_provider"
SnapshotProvider = Callable[[], DirectorySnapshot | None]
_MAX_FIXTURE_BYTES = 1024 * 1024


def select_provider(name: str | None, snapshot_file: str | None) -> SnapshotProvider | None:
    """Enable only the approved synthetic fixture format through server configuration."""
    if name != "synthetic-v1" or not snapshot_file:
        return None
    return partial(_read_synthetic, Path(snapshot_file))


def _read_synthetic(path: Path) -> DirectorySnapshot:
    try:
        if not path.is_file():
            raise LdapCapabilityError()
        with path.open("rb") as source:
            raw = source.read(_MAX_FIXTURE_BYTES + 1)
        if len(raw) > _MAX_FIXTURE_BYTES:
            raise LdapCapabilityError()
        document = json.loads(raw.decode("utf-8"))
        if (not isinstance(document, dict) or document.keys() != {"version", "entries", "memberships"}
                or not isinstance(document["entries"], list) or not isinstance(document["memberships"], list)):
            raise LdapCapabilityError()

        entries = tuple(DirectoryEntry(email=None, **entry) for entry in document["entries"])
        if any(not entry.object_id or "@" in entry.object_id for entry in entries):
            raise LdapCapabilityError()
        memberships = tuple(DirectoryMembership(**row) for row in document["memberships"])
        snapshot = DirectorySnapshot("synthetic", document["version"], entries, memberships)
        return build_preview(snapshot, (), enabled=True).snapshot
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise LdapCapabilityError() from error
