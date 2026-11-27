"""Collection schema for disposable usage and history samples (PAR-OPS-005).

Uncollected periods stay distinct from zero activity. Audit rows and
dictation rows never enter the statistics retention store.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.clinic.statistics_retention import (
    COLLECTED,
    HISTORY,
    PARTIAL,
    UNCOLLECTED,
    USAGE,
    CollectionError,
    PeriodComparison,
    RetentionStore,
    Sample,
    compare_periods,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def test_compare_two_synthetic_periods_before_reporting():
    """Off-window with two actions is not the later collected window with no actions."""
    store = RetentionStore()
    off_start = _utc(2026, 1, 1)
    on_start = _utc(2026, 2, 1)
    on_end = _utc(2026, 3, 1)

    assert store.add(Sample(USAGE, _utc(2026, 1, 10), "open"), now=_utc(2026, 1, 10)) is None
    assert store.add(Sample(HISTORY, _utc(2026, 1, 11), "correct"), now=_utc(2026, 1, 11)) is None
    assert store.samples() == ()

    off = store.period(USAGE, off_start, on_start, now=on_start)
    assert off.coverage == UNCOLLECTED
    assert off.count is None
    assert off.zero_activity is False

    store.set_switch(USAGE, True, on_start, 30)
    idle = store.period(USAGE, on_start, on_end, now=on_end)
    assert idle.coverage == COLLECTED
    assert idle.count == 0
    assert idle.expired == 0
    assert idle.zero_activity is True

    compared = compare_periods(off, idle)
    assert isinstance(compared, PeriodComparison)
    assert compared.same_activity is False
    body = compared.as_dict()
    assert body["left"]["count"] is None
    assert body["left"]["coverage"] == UNCOLLECTED
    assert body["right"]["count"] == 0
    assert body["right"]["zero_activity"] is True


def test_usage_and_history_switches_are_independent():
    store = RetentionStore()
    start = _utc(2026, 2, 1)
    end = _utc(2026, 3, 1)
    at = _utc(2026, 2, 5)
    store.set_switch(USAGE, True, start, 30)

    kept = store.add(Sample(USAGE, at, "open"), now=at)
    assert kept is not None
    assert kept.retention_days == 30
    assert store.add(Sample(HISTORY, at, "correct"), now=at) is None

    usage = store.period(USAGE, start, end, now=end)
    history = store.period(HISTORY, start, end, now=end)
    assert usage.coverage == COLLECTED
    assert usage.count == 1
    assert history.coverage == UNCOLLECTED
    assert history.count is None
    assert compare_periods(usage, history).same_activity is False

    store.set_switch(HISTORY, True, _utc(2026, 2, 10), 14)
    history_at = _utc(2026, 2, 12)
    assert store.add(Sample(HISTORY, history_at, "finish"), now=history_at) is not None
    usage_after = store.period(USAGE, start, end, now=end)
    assert usage_after.count == 1
    assert usage_after.expired == 0
    history_after = store.period(HISTORY, start, end, now=end)
    assert history_after.coverage == PARTIAL
    assert history_after.count == 0
    assert history_after.expired == 1
    assert history_after.zero_activity is False


def test_audit_and_dictation_rows_never_enter_the_store():
    from src.clinic.models import ClinicDictation
    from src.models.audit import AuditEvent
    from src.models.recording import Recording

    store = RetentionStore()
    now = _utc(2026, 3, 1)
    store.set_switch(USAGE, True, now, 30)
    store.set_switch(HISTORY, True, now, 30)
    kept = store.add(Sample(USAGE, now, "open"), now=now)
    assert kept is not None

    class _AuditTable:
        __tablename__ = "audit_event"

    class _KindAudit:
        kind = "audit"

    rows = (
        (AuditEvent(event="login", event_group="auth", actor_kind="user"), "audit_refused"),
        (_AuditTable(), "audit_refused"),
        (_KindAudit(), "audit_refused"),
        (Recording(), "dictation_refused"),
        (ClinicDictation(), "dictation_refused"),
    )
    for row, code in rows:
        with pytest.raises(CollectionError) as exc:
            store.add(row, now=now)
        assert exc.value.code == code
    assert store.samples() == (kept,)


def test_expired_samples_stay_apart_from_active_dictations():
    store = RetentionStore()
    start = _utc(2026, 1, 1)
    store.set_switch(USAGE, True, start, 30)
    assert store.add(Sample(USAGE, start, "open"), now=start) is not None

    exactly = store.period(USAGE, start, start + timedelta(days=1), now=start + timedelta(days=30))
    assert exactly.coverage == COLLECTED
    assert exactly.count == 0
    assert exactly.expired == 1
    assert exactly.zero_activity is False

    younger_store = RetentionStore()
    younger_store.set_switch(USAGE, True, start, 30)
    younger_at = start + timedelta(seconds=1)
    assert younger_store.add(Sample(USAGE, younger_at, "open"), now=younger_at) is not None
    kept = younger_store.period(USAGE, start, start + timedelta(days=1), now=start + timedelta(days=30))
    assert kept.count == 1
    assert kept.expired == 0
    assert all(isinstance(row, Sample) for row in store.samples())


def test_partial_window_is_not_zero_activity():
    store = RetentionStore()
    start = _utc(2026, 4, 1)
    mid = _utc(2026, 4, 15)
    end = _utc(2026, 5, 1)
    store.set_switch(USAGE, True, mid, 30)
    period = store.period(USAGE, start, end, now=end)
    assert period.coverage == PARTIAL
    assert period.count == 0
    assert period.zero_activity is False
    assert compare_periods(period, store.period(USAGE, mid, end, now=end)).same_activity is False


def test_rejected_inputs():
    store = RetentionStore()
    now = _utc(2026, 3, 1)
    with pytest.raises(CollectionError) as exc:
        store.set_switch(USAGE, True, now, True)  # type: ignore[arg-type]
    assert exc.value.code == "bad_retention"
    with pytest.raises(CollectionError) as exc:
        store.set_switch(USAGE, True, now, 0)
    assert exc.value.code == "bad_retention"
    with pytest.raises(CollectionError) as exc:
        store.set_switch("audit", True, now, 30)
    assert exc.value.code == "bad_kind"
    with pytest.raises(CollectionError) as exc:
        store.set_switch(USAGE, True, datetime(2026, 3, 1), 30)
    assert exc.value.code == "naive_datetime"
    store.set_switch(USAGE, True, now, 30)
    with pytest.raises(CollectionError) as exc:
        store.period(USAGE, now, now, now=now)
    assert exc.value.code == "bad_period"
    with pytest.raises(CollectionError) as exc:
        store.add(Sample(USAGE, now, "open"), now=now - timedelta(seconds=1))
    assert exc.value.code == "future_timestamp"
    with pytest.raises(CollectionError) as exc:
        Sample(USAGE, now, "")
    assert exc.value.code == "bad_action"


def test_offset_timestamp_is_stored_as_utc():
    store = RetentionStore()
    start = _utc(2026, 2, 1)
    store.set_switch(USAGE, True, start, 30)
    local = datetime(2026, 2, 5, 12, 0, tzinfo=timezone(timedelta(hours=1)))
    stored = store.add(Sample(USAGE, local, "open"), now=local)
    assert stored is not None
    assert stored.occurred_at == local.astimezone(timezone.utc)
    period = store.period(USAGE, start, _utc(2026, 3, 1), now=_utc(2026, 3, 1))
    assert period.count == 1
