"""Backup copy retention plan tests.

Covers PAR-IMP-006 'Backupkopie und Aufbewahrungsfrist'.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.clinic.backup_retention_plan import (
    BackupEntry,
    CleanupPlan,
    DEFAULT_RETENTION_DAYS,
    RetentionError,
    next_expiry,
    plan_cleanup,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


class TestPlanCleanup:
    """Tests for plan_cleanup."""

    def test_boundary_exact_retention_is_expired(self):
        """Entry exactly at retention boundary is expired (inclusive)."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.expired == (entry,)
        assert plan.kept == ()

    def test_one_second_younger_is_kept(self):
        """Entry one second younger than boundary is kept."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 1))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.expired == ()
        assert plan.kept == (entry,)

    def test_hold_true_never_expired(self):
        """Entry with hold=True is never expired, even when very old."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(
            path="backup/old.txt",
            moved_at=_utc(2016, 1, 1, 12, 0, 0),
            hold=True,
        )
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.expired == ()
        assert plan.held == (entry,)
        assert plan.kept == ()

    def test_invalid_future_timestamp(self):
        """Entry with moved_at > now goes to invalid with future_timestamp."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 2, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "future_timestamp"),)
        assert plan.expired == ()
        assert plan.kept == ()

    def test_invalid_unsafe_path_absolute(self):
        """Absolute path is invalid."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="/absolute/path.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "unsafe_path"),)

    def test_invalid_unsafe_path_drive_letter(self):
        """Windows drive letter path is invalid."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="C:\\backup\\file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "unsafe_path"),)

    def test_invalid_unsafe_path_backslash(self):
        """Backslash in path is invalid."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup\\file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "unsafe_path"),)

    def test_invalid_unsafe_path_dotdot(self):
        """Path with .. segment is invalid."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/../file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "unsafe_path"),)

    def test_invalid_unsafe_path_nul(self):
        """Path with NUL byte is invalid."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/\x00file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "unsafe_path"),)

    def test_invalid_unsafe_path_empty(self):
        """Empty path is invalid."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([entry], now, retention_days=30)
        assert plan.invalid == ((entry, "unsafe_path"),)

    def test_expired_sorted_oldest_first(self):
        """Expired entries are sorted oldest first, ties by path."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        e1 = BackupEntry(path="b.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        e2 = BackupEntry(path="a.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        e3 = BackupEntry(path="c.txt", moved_at=_utc(2025, 12, 31, 12, 0, 0))
        plan = plan_cleanup([e1, e2, e3], now, retention_days=30)
        assert plan.expired == (e3, e2, e1)

    def test_max_batch_limits_expired(self):
        """max_batch limits expired to the oldest N entries."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        # All three are expired (cutoff is 2026-01-01)
        e1 = BackupEntry(path="a.txt", moved_at=_utc(2025, 12, 30, 12, 0, 0))
        e2 = BackupEntry(path="b.txt", moved_at=_utc(2025, 12, 31, 12, 0, 0))
        e3 = BackupEntry(path="c.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        plan = plan_cleanup([e1, e2, e3], now, retention_days=30, max_batch=2)
        assert plan.expired == (e1, e2)
        # Remaining expired entries are NOT in kept
        assert plan.kept == ()

    def test_max_batch_subsequent_call_returns_next(self):
        """Calling again with first batch removed returns next expired entries."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        # All three are expired (cutoff is 2026-01-01)
        e1 = BackupEntry(path="a.txt", moved_at=_utc(2025, 12, 30, 12, 0, 0))
        e2 = BackupEntry(path="b.txt", moved_at=_utc(2025, 12, 31, 12, 0, 0))
        e3 = BackupEntry(path="c.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        entries = [e1, e2, e3]

        plan1 = plan_cleanup(entries, now, retention_days=30, max_batch=2)
        assert plan1.expired == (e1, e2)

        remaining = [e for e in entries if e not in plan1.expired]
        plan2 = plan_cleanup(remaining, now, retention_days=30, max_batch=2)
        assert plan2.expired == (e3,)

    def test_bad_retention_days_raises(self):
        """retention_days < 1, bool, float, None raises RetentionError('bad_retention')."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))

        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=0)
        assert exc.value.code == "bad_retention"

        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=-1)
        assert exc.value.code == "bad_retention"

        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=True)
        assert exc.value.code == "bad_retention"

        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=30.5)
        assert exc.value.code == "bad_retention"

        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=None)
        assert exc.value.code == "bad_retention"

    def test_naive_now_raises_value_error(self):
        """Naive now raises ValueError."""
        naive_now = datetime(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        with pytest.raises(ValueError, match="now must be timezone-aware"):
            plan_cleanup([entry], naive_now, retention_days=30)

    def test_naive_moved_at_raises_value_error(self):
        """Naive moved_at raises ValueError."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        naive_moved = datetime(2026, 1, 1, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=naive_moved)
        with pytest.raises(ValueError, match="moved_at must be timezone-aware"):
            plan_cleanup([entry], now, retention_days=30)

    def test_max_batch_negative_raises(self):
        """Negative max_batch raises RetentionError."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=30, max_batch=-1)
        assert exc.value.code == "bad_retention"

    def test_max_batch_bool_raises(self):
        """Boolean max_batch raises RetentionError."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))
        with pytest.raises(RetentionError) as exc:
            plan_cleanup([entry], now, retention_days=30, max_batch=True)
        assert exc.value.code == "bad_retention"

    def test_input_entries_unchanged(self):
        """Input sequence and entries are unchanged after the call."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        entry = BackupEntry(path="backup/file.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0), size=100, hold=False)
        entries = [entry]
        original_path = entry.path
        original_moved = entry.moved_at
        original_size = entry.size
        original_hold = entry.hold

        plan_cleanup(entries, now, retention_days=30)

        assert entry.path == original_path
        assert entry.moved_at == original_moved
        assert entry.size == original_size
        assert entry.hold == original_hold
        assert entries == [entry]

    def test_held_entries_sorted(self):
        """Held entries are sorted oldest first."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        e1 = BackupEntry(path="b.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0), hold=True)
        e2 = BackupEntry(path="a.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0), hold=True)
        e3 = BackupEntry(path="c.txt", moved_at=_utc(2025, 12, 31, 12, 0, 0), hold=True)
        plan = plan_cleanup([e1, e2, e3], now, retention_days=30)
        assert plan.held == (e3, e2, e1)

    def test_kept_entries_sorted(self):
        """Kept entries are sorted oldest first."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        e1 = BackupEntry(path="b.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        e2 = BackupEntry(path="a.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        e3 = BackupEntry(path="c.txt", moved_at=_utc(2026, 1, 10, 12, 0, 0))
        plan = plan_cleanup([e1, e2, e3], now, retention_days=30)
        assert plan.kept == (e3, e2, e1)

    def test_mixed_entries_categorized_correctly(self):
        """Mixed expired, kept, held, invalid entries go to correct buckets."""
        now = _utc(2026, 1, 31, 12, 0, 0)
        expired = BackupEntry(path="expired.txt", moved_at=_utc(2025, 12, 31, 12, 0, 0))
        kept = BackupEntry(path="kept.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        held = BackupEntry(path="held.txt", moved_at=_utc(2025, 1, 1, 12, 0, 0), hold=True)
        future = BackupEntry(path="future.txt", moved_at=_utc(2026, 2, 1, 12, 0, 0))
        unsafe = BackupEntry(path="/absolute.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))

        plan = plan_cleanup([expired, kept, held, future, unsafe], now, retention_days=30)

        assert plan.expired == (expired,)
        assert plan.kept == (kept,)
        assert plan.held == (held,)
        assert plan.invalid == ((future, "future_timestamp"), (unsafe, "unsafe_path"))


class TestNextExpiry:
    """Tests for next_expiry."""

    def test_returns_earliest_expiry(self):
        """Returns earliest moved_at + retention_days of non-held valid entries."""
        e1 = BackupEntry(path="a.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        e2 = BackupEntry(path="b.txt", moved_at=_utc(2026, 1, 10, 12, 0, 0))
        e3 = BackupEntry(path="c.txt", moved_at=_utc(2026, 1, 20, 12, 0, 0))

        result = next_expiry([e1, e2, e3], retention_days=30)
        expected = _utc(2026, 1, 10, 12, 0, 0) + timedelta(days=30)
        assert result == expected

    def test_ignores_held_entries(self):
        """Held entries are not considered for next_expiry."""
        e1 = BackupEntry(path="kept.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        e2 = BackupEntry(path="held.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0), hold=True)

        result = next_expiry([e1, e2], retention_days=30)
        expected = _utc(2026, 1, 15, 12, 0, 0) + timedelta(days=30)
        assert result == expected

    def test_ignores_invalid_entries(self):
        """Invalid entries (future, unsafe path) are not considered."""
        e1 = BackupEntry(path="kept.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        e2 = BackupEntry(path="future.txt", moved_at=_utc(2026, 2, 1, 12, 0, 0))
        e3 = BackupEntry(path="/absolute.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))

        result = next_expiry([e1, e2, e3], retention_days=30)
        expected = _utc(2026, 1, 15, 12, 0, 0) + timedelta(days=30)
        assert result == expected

    def test_returns_none_when_no_valid_non_held(self):
        """Returns None when there are no non-held valid entries."""
        e1 = BackupEntry(path="held.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0), hold=True)
        e2 = BackupEntry(path="/absolute.txt", moved_at=_utc(2026, 1, 1, 12, 0, 0))

        result = next_expiry([e1, e2], retention_days=30)
        assert result is None

    def test_returns_none_for_empty_list(self):
        """Returns None for empty input."""
        result = next_expiry([], retention_days=30)
        assert result is None

    def test_bad_retention_days_raises(self):
        """Invalid retention_days raises RetentionError."""
        e1 = BackupEntry(path="kept.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))

        with pytest.raises(RetentionError) as exc:
            next_expiry([e1], retention_days=0)
        assert exc.value.code == "bad_retention"

    def test_naive_moved_at_raises_value_error(self):
        """Naive moved_at raises ValueError."""
        naive_moved = datetime(2026, 1, 15, 12, 0, 0)
        e1 = BackupEntry(path="kept.txt", moved_at=naive_moved)

        with pytest.raises(ValueError, match="moved_at must be timezone-aware"):
            next_expiry([e1], retention_days=30)

    def test_result_is_timezone_aware_utc(self):
        """Returned datetime is timezone-aware UTC."""
        e1 = BackupEntry(path="kept.txt", moved_at=_utc(2026, 1, 15, 12, 0, 0))
        result = next_expiry([e1], retention_days=30)
        assert result.tzinfo is not None
        assert result.tzinfo.utcoffset(result) is not None
        assert result == result.astimezone(timezone.utc)


class TestModuleProofNoDeletion:
    """Proof that the module contains no deletion operations."""

    def test_no_deletion_functions_in_source(self):
        """Module source contains no os.remove, unlink, rmtree, shutil, os.rename, open(."""
        import inspect
        from src.clinic import backup_retention_plan

        source = inspect.getsource(backup_retention_plan)
        forbidden = [
            "os.remove",
            "unlink",
            "rmtree",
            "shutil",
            "os.rename",
            "open(",
        ]
        for forbidden_call in forbidden:
            assert forbidden_call not in source, f"Found {forbidden_call} in module source"