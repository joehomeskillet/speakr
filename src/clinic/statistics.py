"""Counts and audio lengths for clinic dictations.

No new table. Each dictation contributes one piece. Status and typist are
derived from rows that already exist:

* ``released`` — the release service would return an active release: a
  ``ClinicRelease`` with no supersession row whose audio, transcript and
  context revisions are all still heads.
* ``in_review`` — no active release, and a review lease with ``expires_at``
  strictly after ``now``. An expiry equal to ``now`` is already over, the
  same rule as the lease service.
* ``open`` — anything else.

The typist (Schreibkraft) is the live lease holder when the lease is still
running. Otherwise it is the recipient of the latest work-state transfer
(latest ``transferred_at``, then the highest id). A release actor is the
person who accepted the text, not the typist. No lease and no transfer
means the dictation is unassigned.

Audio length is ``Recording.audio_duration_seconds``. A missing, negative
or non-finite length stays out of the sum and the average, and the bucket
is ``incomplete``. It is not stored as zero. ``total_seconds`` is ``None``
when the bucket has dictations but not a single usable length. The average
divides the known sum by the known count.

Time basis, because reports need a choice of clock and both instants exist:

* ``created`` — ``ClinicDictation.created_at``. Every dictation has one.
  This is the default: the work list is built around when the dictation
  arrived.
* ``completed`` — ``created_at`` of the active release. Dictations without
  one are counted in ``excluded_missing_timestamp`` and appear in no
  bucket. Processing time and review time are not bases: the lease stores
  only its expiry, and a transfer is a handover instant, not a duration.

The window is half-open ``[start, end)`` on the absolute instant. Period
buckets (day, ISO week, month) use Europe/Zurich, the wall clock the UI
already converts to. A week bucket starts on Monday, a month bucket on
the first. Empty periods are filled with zeros only when the grouping is
the period alone and both bounds are set; other dimensions are not invented.

Naive datetimes from SQLite are read as UTC. Clinic writers store UTC.
Query bounds must be timezone-aware.

This service does not check who may see author or typist figures. The
caller has to do that before calling.

shortcut: aggregation is in Python and a period query refuses more than
4000 buckets. Move the GROUP BY into SQL, and raise the cap, when one
report regularly passes a few tens of thousands of dictations or needs a
longer daily range.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicRelease,
    ClinicReviewLease,
    ClinicTranscriptRevision,
)
from src.clinic.release import ClinicReleaseSupersession, ClinicWorkStateTransfer
from src.models import Recording

ZONE_NAME = "Europe/Zurich"
ZONE = ZoneInfo(ZONE_NAME)
TIME_BASES = ("created", "completed")
GRAINS = ("day", "week", "month")
GROUP_KEYS = ("status", "author", "typist", "period")
STATUSES = ("open", "in_review", "released")
WINDOW = "[start, end)"

# shortcut: 4000 buckets, raise the cap when a report needs a longer daily range
_MAX_PERIODS = 4000

_FIELD = {
    "status": "status",
    "author": "author_user_id",
    "typist": "typist_user_id",
    "period": "period_start",
}


class StatisticsError(ValueError):
    """The query cannot be run. ``code`` is a stable token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StatisticsQuery:
    """One aggregate request. ``group_by`` keeps the caller's order."""

    time_basis: str = "created"
    start: datetime | None = None
    end: datetime | None = None
    grain: str | None = None
    group_by: tuple[str, ...] = ()
    now: datetime | None = None


@dataclass(frozen=True)
class StatisticsBucket:
    """One group. Lengths are seconds. ``incomplete`` means a length is missing."""

    count: int
    total_seconds: float | None
    average_seconds: float | None
    known_duration_count: int
    incomplete: bool
    status: str | None = None
    author_user_id: int | None = None
    typist_user_id: int | None = None
    period_start: str | None = None

    def as_dict(self, group_by: tuple[str, ...]) -> dict:
        body = {}
        if "status" in group_by:
            body["status"] = self.status
        if "author" in group_by:
            body["author_user_id"] = self.author_user_id
        if "typist" in group_by:
            body["typist_user_id"] = self.typist_user_id
        if "period" in group_by:
            body["period_start"] = self.period_start
        body["count"] = self.count
        body["total_seconds"] = self.total_seconds
        body["average_seconds"] = self.average_seconds
        body["known_duration_count"] = self.known_duration_count
        body["incomplete"] = self.incomplete
        return body


@dataclass(frozen=True)
class StatisticsResult:
    """Serializable aggregate. ``start`` and ``end`` are UTC instants."""

    time_basis: str
    zone: str
    window: str
    start: datetime | None
    end: datetime | None
    grain: str | None
    group_by: tuple[str, ...]
    as_of: datetime
    excluded_missing_timestamp: int
    buckets: tuple[StatisticsBucket, ...]

    def as_dict(self) -> dict:
        return {
            "time_basis": self.time_basis,
            "zone": self.zone,
            "window": self.window,
            "start": _iso(self.start),
            "end": _iso(self.end),
            "grain": self.grain,
            "group_by": list(self.group_by),
            "as_of": _iso(self.as_of),
            "excluded_missing_timestamp": self.excluded_missing_timestamp,
            "buckets": [bucket.as_dict(self.group_by) for bucket in self.buckets],
        }


@dataclass(frozen=True)
class _Fact:
    status: str
    author_user_id: int
    typist_user_id: int | None
    timestamp: datetime | None
    duration: float | None


def summarize(session: Session, query: StatisticsQuery) -> StatisticsResult:
    """Group dictations in ``session``. Reads only; the caller commits."""
    query = _validate(query)
    facts, excluded = _load(session, query)
    kept = [fact for fact in facts if fact.timestamp is not None and _in_window(fact, query)]
    return StatisticsResult(
        time_basis=query.time_basis,
        zone=ZONE_NAME,
        window=WINDOW,
        start=query.start,
        end=query.end,
        grain=query.grain,
        group_by=query.group_by,
        as_of=query.now,
        excluded_missing_timestamp=excluded,
        buckets=_aggregate(kept, query),
    )


def _validate(query: StatisticsQuery) -> StatisticsQuery:
    if query.time_basis not in TIME_BASES:
        raise StatisticsError("time_basis")
    start = _input_time(query.start)
    end = _input_time(query.end)
    now = _input_time(query.now) or datetime.now(timezone.utc)
    if start is not None and end is not None and start >= end:
        raise StatisticsError("window")
    names = _group_by(query.group_by)
    grain = query.grain
    if grain is not None and grain not in GRAINS:
        raise StatisticsError("grain")
    if ("period" in names) != (grain is not None):
        raise StatisticsError("grain")
    if "period" in names and (start is None or end is None):
        raise StatisticsError("period_bounds")
    return StatisticsQuery(
        time_basis=query.time_basis,
        start=start,
        end=end,
        grain=grain,
        group_by=names,
        now=now,
    )


def _group_by(value) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise StatisticsError("group_by")
    names = tuple(value)
    if len(names) != len(set(names)) or any(name not in GROUP_KEYS for name in names):
        raise StatisticsError("group_by")
    return names


def _input_time(value) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StatisticsError("naive_datetime")
    return value.astimezone(timezone.utc)


def _as_utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _duration(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or not math.isfinite(number):
        return None
    return number


def _in_window(fact: _Fact, query: StatisticsQuery) -> bool:
    stamp = fact.timestamp
    if query.start is not None and stamp < query.start:
        return False
    if query.end is not None and stamp >= query.end:
        return False
    return True


def _floor(local: datetime, grain: str) -> datetime:
    if grain == "day":
        day = local.date()
    elif grain == "week":
        day = local.date() - timedelta(days=local.weekday())
    else:
        day = local.date().replace(day=1)
    return datetime(day.year, day.month, day.day, tzinfo=ZONE)


def _advance(local: datetime, grain: str) -> datetime:
    """Next bucket start, by calendar date, so the DST switch stays one day."""
    if grain == "day":
        nxt = local.date() + timedelta(days=1)
    elif grain == "week":
        nxt = local.date() + timedelta(days=7)
    else:
        year = local.year + (1 if local.month == 12 else 0)
        month = 1 if local.month == 12 else local.month + 1
        nxt = local.date().replace(year=year, month=month, day=1)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=ZONE)


def _period_starts(start: datetime, end: datetime, grain: str) -> list[str]:
    local_start = start.astimezone(ZONE)
    local_end = end.astimezone(ZONE)
    cursor = _floor(local_start, grain)
    found = []
    while cursor < local_end:
        nxt = _advance(cursor, grain)
        if nxt > local_start:
            found.append(cursor.date().isoformat())
        cursor = nxt
        if len(found) > _MAX_PERIODS:
            raise StatisticsError("range_too_wide")
    return found


def _period_of(stamp: datetime, grain: str) -> str:
    return _floor(stamp.astimezone(ZONE), grain).date().isoformat()


def _load(session: Session, query: StatisticsQuery) -> tuple[list[_Fact], int]:
    dictations = session.query(
        ClinicDictation.id,
        ClinicDictation.author_user_id,
        ClinicDictation.created_at,
        Recording.audio_duration_seconds,
    ).join(Recording, Recording.id == ClinicDictation.recording_id).all()
    leases = {
        row.dictation_id: row
        for row in session.query(
            ClinicReviewLease.dictation_id,
            ClinicReviewLease.holder_user_id,
            ClinicReviewLease.expires_at,
        ).all()
    }
    transfers = _by_dictation(session.query(
        ClinicWorkStateTransfer.dictation_id,
        ClinicWorkStateTransfer.to_user_id,
        ClinicWorkStateTransfer.transferred_at,
        ClinicWorkStateTransfer.id,
    ).all())
    releases = _by_dictation(session.query(
        ClinicRelease.id,
        ClinicRelease.dictation_id,
        ClinicRelease.created_at,
        ClinicRelease.audio_revision_id,
        ClinicRelease.transcript_revision_id,
        ClinicRelease.context_revision_id,
    ).all())
    superseded = {
        row.release_id
        for row in session.query(ClinicReleaseSupersession.release_id).all()
    }
    audio = _by_dictation(session.query(
        ClinicAudioRevision.dictation_id,
        ClinicAudioRevision.id,
        ClinicAudioRevision.parent_revision_id,
    ).all())
    transcripts = _by_dictation(session.query(
        ClinicTranscriptRevision.dictation_id,
        ClinicTranscriptRevision.id,
        ClinicTranscriptRevision.parent_revision_id,
    ).all())
    contexts = _by_dictation(session.query(
        ClinicContextRevision.dictation_id,
        ClinicContextRevision.id,
        ClinicContextRevision.parent_revision_id,
    ).all())

    facts = []
    excluded = 0
    for row in dictations:
        active = _active_release(
            releases.get(row.id, ()),
            superseded,
            audio.get(row.id, ()),
            transcripts.get(row.id, ()),
            contexts.get(row.id, ()),
        )
        status = "released" if active is not None else _open_or_review(leases.get(row.id), query.now)
        stamp = _as_utc(row.created_at) if query.time_basis == "created" else (
            _as_utc(active.created_at) if active is not None else None
        )
        if stamp is None:
            excluded += 1
        facts.append(_Fact(
            status=status,
            author_user_id=row.author_user_id,
            typist_user_id=_typist(transfers.get(row.id, ()), leases.get(row.id), query.now),
            timestamp=stamp,
            duration=_duration(row.audio_duration_seconds),
        ))
    return facts, excluded


def _by_dictation(rows) -> dict:
    grouped = {}
    for row in rows:
        grouped.setdefault(row.dictation_id, []).append(row)
    return grouped


def _heads(rows) -> set:
    parents = {row.parent_revision_id for row in rows if row.parent_revision_id is not None}
    return {row.id for row in rows if row.id not in parents}


def _active_release(releases, superseded, audio, transcripts, contexts):
    """Newest release that ``active_release`` would still return."""
    audio_heads = _heads(audio)
    transcript_heads = _heads(transcripts)
    context_heads = _heads(contexts)
    usable = []
    for row in releases:
        stamp = _as_utc(row.created_at)
        if stamp is None or row.id in superseded:
            continue
        if (
            row.audio_revision_id in audio_heads
            and row.transcript_revision_id in transcript_heads
            and row.context_revision_id in context_heads
        ):
            usable.append((stamp, row))
    usable.sort(key=lambda item: (-item[0].timestamp(), item[1].id))
    if not usable:
        return None
    return usable[0][1]


def _open_or_review(lease, now: datetime) -> str:
    if lease is None:
        return "open"
    expires = _as_utc(lease.expires_at)
    if expires is not None and expires > now:
        return "in_review"
    return "open"


def _typist(transfers, lease, now: datetime):
    if lease is not None:
        expires = _as_utc(lease.expires_at)
        if expires is not None and expires > now:
            return lease.holder_user_id
    latest = None
    latest_key = None
    for row in transfers:
        stamp = _as_utc(row.transferred_at)
        if stamp is None:
            continue
        key = (stamp, row.id)
        if latest_key is None or key > latest_key:
            latest_key = key
            latest = row
    if latest is None:
        return None
    return latest.to_user_id


def _aggregate(facts: list[_Fact], query: StatisticsQuery) -> tuple[StatisticsBucket, ...]:
    groups = {}
    for fact in facts:
        key = tuple(_group_value(fact, name, query.grain) for name in query.group_by)
        count, total, known, incomplete = groups.get(key, (0, 0.0, 0, False))
        count += 1
        if fact.duration is None:
            incomplete = True
        else:
            total += fact.duration
            known += 1
        groups[key] = (count, total, known, incomplete)
    if query.group_by == ("period",):
        for period in _period_starts(query.start, query.end, query.grain):
            groups.setdefault((period,), (0, 0.0, 0, False))
    if not query.group_by and not groups:
        groups[()] = (0, 0.0, 0, False)
    buckets = [_bucket(query.group_by, key, *values) for key, values in groups.items()]
    buckets.sort(key=lambda bucket: _sort_key(bucket, query.group_by))
    return tuple(buckets)


def _group_value(fact: _Fact, name: str, grain: str | None):
    if name == "status":
        return fact.status
    if name == "author":
        return fact.author_user_id
    if name == "typist":
        return fact.typist_user_id
    return _period_of(fact.timestamp, grain)


def _bucket(group_by, key, count, total, known, incomplete) -> StatisticsBucket:
    if known:
        total_out = total
        average = total / known
    elif count:
        total_out = None
        average = None
        incomplete = True
    else:
        total_out = 0.0
        average = None
    fields = {_FIELD[name]: value for name, value in zip(group_by, key)}
    return StatisticsBucket(
        count=count,
        total_seconds=total_out,
        average_seconds=average,
        known_duration_count=known,
        incomplete=incomplete,
        status=fields.get("status"),
        author_user_id=fields.get("author_user_id"),
        typist_user_id=fields.get("typist_user_id"),
        period_start=fields.get("period_start"),
    )


def _sort_key(bucket: StatisticsBucket, group_by: tuple[str, ...]):
    pieces = []
    for name in group_by:
        value = getattr(bucket, _FIELD[name])
        if value is None:
            pieces.append((0, ""))
        elif isinstance(value, int):
            pieces.append((1, value))
        else:
            pieces.append((1, value))
    return tuple(pieces)
