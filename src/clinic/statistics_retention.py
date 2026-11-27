"""Collection switches, periods and expiry for disposable statistics.

Usage and dictation-history samples are optional and independent of each
other. The security audit is a different store: an audit row is refused
here and is never written. A period whose switch was off is uncollected.
Its count is None, which is not zero activity.

Samples carry the retention limit that was in force when they were
accepted. Expiry only reclassifies those samples. This module does not
delete a dictation, a recording or an audit row.

shortcut: in-memory store, replace it when samples must survive a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

USAGE = "usage"
HISTORY = "history"
KINDS = frozenset({USAGE, HISTORY})

UNCOLLECTED = "uncollected"
COLLECTED = "collected"
PARTIAL = "partial"

_AUDIT_TABLES = frozenset({"audit_event"})
_DICTATION_TABLES = frozenset({"recording", "clinic_dictation"})
_DICTATION_TYPES = frozenset({"Recording", "ClinicDictation"})


class CollectionError(ValueError):
    """The sample or the period was rejected. ``code`` is a stable token."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Sample:
    """One disposable statistical action. ``retention_days`` is set by the store."""

    kind: str
    occurred_at: datetime
    action: str
    retention_days: int = 0

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise CollectionError("bad_kind")
        if not isinstance(self.action, str) or self.action == "" or len(self.action) > 80 or "\x00" in self.action:
            raise CollectionError("bad_action")
        if isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int) or self.retention_days < 0:
            raise CollectionError("bad_retention")
        object.__setattr__(self, "occurred_at", _aware_utc(self.occurred_at))


@dataclass(frozen=True)
class _Switch:
    kind: str
    enabled: bool
    retention_days: int
    at: datetime


@dataclass(frozen=True)
class Period:
    """One half-open window ``[start, end)`` for a single kind.

    ``count`` is None when ``coverage`` is uncollected. Zero is a collected
    window with no samples.
    """

    kind: str
    start: datetime
    end: datetime
    coverage: str
    count: int | None
    expired: int

    @property
    def zero_activity(self) -> bool:
        return self.coverage == COLLECTED and self.count == 0 and self.expired == 0


@dataclass(frozen=True)
class PeriodComparison:
    """Difference between two periods. Uncollected is never the same activity as zero."""

    left: Period
    right: Period
    same_activity: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "same_activity": self.same_activity,
            "left": _period_dict(self.left),
            "right": _period_dict(self.right),
        }


class RetentionStore:
    """In-memory samples and the switch timeline that decided to keep them."""

    def __init__(self) -> None:
        self._switches: list[_Switch] = []
        self._samples: list[Sample] = []

    def set_switch(self, kind: str, enabled: bool, at: datetime, retention_days: int) -> None:
        """Record that ``kind`` is on or off from ``at`` onward, with its own limit."""
        if kind not in KINDS:
            raise CollectionError("bad_kind")
        if not isinstance(enabled, bool):
            raise CollectionError("bad_switch")
        _retention(retention_days)
        self._switches.append(_Switch(kind, enabled, retention_days, _aware_utc(at)))

    def add(self, row: object, *, now: datetime) -> Sample | None:
        """Keep a sample when its kind is enabled at ``occurred_at``.

        Returns None when the switch is off. That gap is uncollected, not a
        stored zero. Audit rows and dictation rows raise and are not stored.
        """
        if _is_audit(row):
            raise CollectionError("audit_refused")
        if _is_dictation(row):
            raise CollectionError("dictation_refused")
        if not isinstance(row, Sample):
            raise CollectionError("not_a_sample")
        now_utc = _aware_utc(now)
        if row.occurred_at > now_utc:
            raise CollectionError("future_timestamp")
        switch = self._switch_at(row.kind, row.occurred_at)
        if switch is None or not switch.enabled:
            return None
        stored = Sample(row.kind, row.occurred_at, row.action, switch.retention_days)
        self._samples.append(stored)
        return stored

    def samples(self) -> tuple[Sample, ...]:
        return tuple(self._samples)

    def period(self, kind: str, start: datetime, end: datetime, *, now: datetime) -> Period:
        """Classify ``[start, end)`` as of ``now``. ``end`` must not be in the future."""
        if kind not in KINDS:
            raise CollectionError("bad_kind")
        start_utc = _aware_utc(start)
        end_utc = _aware_utc(end)
        now_utc = _aware_utc(now)
        if start_utc >= end_utc:
            raise CollectionError("bad_period")
        if end_utc > now_utc:
            raise CollectionError("open_period")
        coverage = self._coverage(kind, start_utc, end_utc)
        if coverage == UNCOLLECTED:
            if any(start_utc <= sample.occurred_at < end_utc and sample.kind == kind for sample in self._samples):
                raise CollectionError("collected_while_off")
            return Period(kind, start_utc, end_utc, UNCOLLECTED, None, 0)
        active = 0
        expired = 0
        for sample in self._samples:
            if sample.kind != kind or not (start_utc <= sample.occurred_at < end_utc):
                continue
            cutoff = now_utc - timedelta(days=sample.retention_days)
            if sample.occurred_at <= cutoff:
                expired += 1
            else:
                active += 1
        return Period(kind, start_utc, end_utc, coverage, active, expired)

    def _switch_at(self, kind: str, at: datetime) -> _Switch | None:
        found: _Switch | None = None
        found_at: datetime | None = None
        found_index = -1
        for index, switch in enumerate(self._switches):
            if switch.kind != kind or switch.at > at:
                continue
            if found_at is None or switch.at > found_at or (switch.at == found_at and index > found_index):
                found = switch
                found_at = switch.at
                found_index = index
        return found

    def _coverage(self, kind: str, start: datetime, end: datetime) -> str:
        """On, off, or both inside the half-open window. A zero-length slice does not count."""
        enabled = False
        current = self._switch_at(kind, start)
        if current is not None:
            enabled = current.enabled
        saw_on = False
        saw_off = False
        cursor = start
        interior = [
            (index, switch)
            for index, switch in enumerate(self._switches)
            if switch.kind == kind and start < switch.at < end
        ]
        interior.sort(key=lambda item: (item[1].at, item[0]))
        for _index, switch in interior:
            if switch.at > cursor:
                if enabled:
                    saw_on = True
                else:
                    saw_off = True
                cursor = switch.at
            enabled = switch.enabled
        if cursor < end:
            if enabled:
                saw_on = True
            else:
                saw_off = True
        if saw_on and saw_off:
            return PARTIAL
        if saw_on:
            return COLLECTED
        return UNCOLLECTED


def compare_periods(left: Period, right: Period) -> PeriodComparison:
    """Compare two periods before a report treats them as the same activity.

    Same activity requires both windows to have been collected, for the same
    kind, with the same active and expired counts. An uncollected window is
    not zero.
    """
    same = (
        left.coverage == COLLECTED
        and right.coverage == COLLECTED
        and left.kind == right.kind
        and left.count is not None
        and right.count is not None
        and left.count == right.count
        and left.expired == right.expired
    )
    return PeriodComparison(left, right, same)


def _period_dict(period: Period) -> dict[str, object]:
    return {
        "kind": period.kind,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "coverage": period.coverage,
        "count": period.count,
        "expired": period.expired,
        "zero_activity": period.zero_activity,
    }


def _retention(retention_days: int) -> None:
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise CollectionError("bad_retention")


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise CollectionError("naive_datetime")
    return value.astimezone(timezone.utc)


def _is_audit(row: object) -> bool:
    if type(row).__name__ == "AuditEvent":
        return True
    if getattr(row, "__tablename__", None) in _AUDIT_TABLES:
        return True
    return getattr(row, "kind", None) == "audit"


def _is_dictation(row: object) -> bool:
    if type(row).__name__ in _DICTATION_TYPES:
        return True
    return getattr(row, "__tablename__", None) in _DICTATION_TABLES
