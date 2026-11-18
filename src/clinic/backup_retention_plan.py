"""Backup copy retention: expiry plan (plans only, deletes nothing).

Computes which backup copies of imported device files have outlived their
retention period. The module only computes a plan. It never touches the file
system and never deletes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence


class RetentionError(ValueError):
    """The retention configuration or input was rejected."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BackupEntry:
    """A single backup copy candidate for retention evaluation.

    Args:
        path: Relative path of the backup copy. Must not be absolute, contain
            ``..`` segments, backslashes, drive letters or NUL bytes.
        moved_at: When the file was moved to backup storage. Must be timezone-aware UTC.
        size: File size in bytes (default 0).
        hold: When True, the entry is never expired regardless of age.
    """

    path: str
    moved_at: datetime
    size: int = 0
    hold: bool = False


@dataclass(frozen=True)
class CleanupPlan:
    """Result of a retention evaluation.

    Args:
        expired: Valid entries whose retention period has elapsed, sorted oldest
            first (ties by path). Limited by ``max_batch`` if provided.
        kept: Valid entries that are not expired and not held.
        held: Valid entries with ``hold=True`` (never expired).
        invalid: Pairs of (entry, reason) for entries that failed validation.
            Reasons: ``future_timestamp``, ``unsafe_path``.
    """

    expired: tuple[BackupEntry, ...]
    kept: tuple[BackupEntry, ...]
    held: tuple[BackupEntry, ...]
    invalid: tuple[tuple[BackupEntry, str], ...]


DEFAULT_RETENTION_DAYS = 30


def _validate_retention_days(retention_days: int) -> None:
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise RetentionError("bad_retention")


def _require_aware_utc(dt: datetime, label: str) -> datetime:
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{label} must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _validate_path(path: str) -> str | None:
    """Return error reason or None if path is safe."""
    if not isinstance(path, str) or path == "":
        return "unsafe_path"
    if "\x00" in path:
        return "unsafe_path"
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":" and path[0].isalpha()):
        return "unsafe_path"
    if "\\" in path:
        return "unsafe_path"
    if ".." in path.split("/"):
        return "unsafe_path"
    return None


def _is_expired(entry: BackupEntry, now: datetime, retention_days: int) -> bool:
    cutoff = now - timedelta(days=retention_days)
    return entry.moved_at <= cutoff


def plan_cleanup(
    entries: Sequence[BackupEntry],
    now: datetime,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    *,
    max_batch: int | None = None,
) -> CleanupPlan:
    """Compute a retention cleanup plan.

    Args:
        entries: Backup entries to evaluate.
        now: Current time (timezone-aware UTC). Naive datetime raises ValueError.
        retention_days: Retention period in days (default 30). Must be int >= 1.
            Bool, float, None or < 1 raises RetentionError('bad_retention').
        max_batch: Optional limit on the number of expired entries returned.
            When set, only the oldest ``max_batch`` expired entries are in
            ``expired``; the rest are omitted (not moved to ``kept``).

    Returns:
        CleanupPlan with expired, kept, held and invalid entries.

    Raises:
        RetentionError: If retention_days is invalid.
        ValueError: If now or any moved_at is naive.
    """
    _validate_retention_days(retention_days)
    now = _require_aware_utc(now, "now")

    valid_entries: list[BackupEntry] = []
    invalid: list[tuple[BackupEntry, str]] = []

    for entry in entries:
        if not isinstance(entry, BackupEntry):
            invalid.append((entry, "unsafe_path"))
            continue

        moved_at = _require_aware_utc(entry.moved_at, "moved_at")
        if moved_at > now:
            invalid.append((entry, "future_timestamp"))
            continue

        path_reason = _validate_path(entry.path)
        if path_reason is not None:
            invalid.append((entry, path_reason))
            continue

        # Reconstruct with validated moved_at (ensures UTC)
        validated_entry = BackupEntry(
            path=entry.path,
            moved_at=moved_at,
            size=entry.size,
            hold=entry.hold,
        )
        valid_entries.append(validated_entry)

    held = [e for e in valid_entries if e.hold]
    non_held = [e for e in valid_entries if not e.hold]

    expired_all = [e for e in non_held if _is_expired(e, now, retention_days)]
    kept_all = [e for e in non_held if not _is_expired(e, now, retention_days)]

    expired_all.sort(key=lambda e: (e.moved_at, e.path))
    kept_all.sort(key=lambda e: (e.moved_at, e.path))
    held.sort(key=lambda e: (e.moved_at, e.path))

    if max_batch is not None:
        if isinstance(max_batch, bool) or not isinstance(max_batch, int) or max_batch < 0:
            raise RetentionError("bad_retention")
        expired = tuple(expired_all[:max_batch])
        # Remaining expired entries (beyond max_batch) are omitted entirely,
        # not moved to kept. They will be returned in subsequent calls.
    else:
        expired = tuple(expired_all)

    return CleanupPlan(
        expired=expired,
        kept=tuple(kept_all),
        held=tuple(held),
        invalid=tuple(invalid),
    )


def next_expiry(
    entries: Sequence[BackupEntry],
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> datetime | None:
    """Return the earliest expiry among non-held valid entries.

    Args:
        entries: Backup entries to evaluate.
        retention_days: Retention period in days (default 30). Must be int >= 1.

    Returns:
        The earliest ``moved_at + retention_days`` as timezone-aware UTC,
        or None when there are no non-held valid entries.

    Raises:
        RetentionError: If retention_days is invalid.
        ValueError: If any moved_at is naive.
    """
    _validate_retention_days(retention_days)

    valid_non_held: list[BackupEntry] = []

    for entry in entries:
        if not isinstance(entry, BackupEntry):
            continue
        moved_at = _require_aware_utc(entry.moved_at, "moved_at")
        path_reason = _validate_path(entry.path)
        if path_reason is not None:
            continue
        if entry.hold:
            continue
        valid_non_held.append(BackupEntry(path=entry.path, moved_at=moved_at, size=entry.size, hold=False))

    if not valid_non_held:
        return None

    earliest = min(valid_non_held, key=lambda e: e.moved_at)
    return earliest.moved_at + timedelta(days=retention_days)