"""Change token and refresh policy for the clinic work list (PAR-WF-002).

Scope: a cheap weak change token over the work-list rows (``list_etag``),
the conditional-request check against a client copy (``if_none_match``)
and the polling-delay policy for the automatic refresh (``RefreshPolicy``,
``clamp_interval``, ``next_delay``). The token is a digest over the rows'
``id`` and ``version`` (or ``updated_at``) only, so an unchanged list can
answer 304 without rebuilding or resending the payload.

Manual refresh is never delayed. ``next_delay`` governs the automatic
poll loop only; a route may serve a user-triggered refresh at any time
without consulting it.

Non-scope: no Flask routes, no database access, no persistence and no
clock. The module is pure Python, state lives in the frozen
``RefreshPolicy`` dataclass, and a later wiring package turns these
functions into HTTP 304 answers and a client poll loop.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

MISSING_ID = "missing_id"
BAD_ID = "bad_id"
BAD_ROW = "bad_row"
MISSING_VERSION = "missing_version"
BAD_VERSION = "bad_version"
BAD_ETAG = "bad_etag"
BAD_HEADER = "bad_header"
BAD_INTERVAL = "bad_interval"
BAD_COUNTS = "bad_counts"
BAD_POLICY = "bad_policy"


class RefreshError(ValueError):
    """The caller's input was rejected before anything changed.

    ``code`` is a stable string, one of the module constants above, that
    routes can branch on instead of the message.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RefreshPolicy:
    """Polling-delay policy for the automatic work-list refresh.

    ``interval_s`` is the base delay between automatic polls. ``min_s``
    and ``max_s`` bound every delay ``next_delay`` computes. Construction
    validates ``1 <= min_s <= interval_s <= max_s``.
    """

    auto_enabled: bool = True
    interval_s: int = 30
    min_s: int = 10
    max_s: int = 300

    def __post_init__(self):
        if not isinstance(self.auto_enabled, bool):
            raise RefreshError(BAD_POLICY, "auto_enabled must be a bool")
        for name in ("interval_s", "min_s", "max_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise RefreshError(BAD_POLICY, f"{name} must be an int, not a bool or float")
        if not 1 <= self.min_s <= self.interval_s <= self.max_s:
            raise RefreshError(BAD_POLICY, "policy requires 1 <= min_s <= interval_s <= max_s")


def list_etag(rows: Iterable[Mapping]) -> str:
    """Weak ETag ``W/"<32 hex>"`` over the rows' ``id`` and ``version``.

    The token is the first 32 hex characters of a sha256 over the
    newline-joined, lexicographically sorted ``"<id>:<version>"`` lines,
    so it is independent of row order and changes when a row is added,
    removed or its version changes. ``version`` falls back to
    ``updated_at`` and may be an int, a str or a timezone-aware datetime
    (hashed as ``isoformat``).
    """
    try:
        lines = sorted(_row_line(row) for row in rows)
    except TypeError:
        raise RefreshError(BAD_ROW, "rows must be an iterable of mappings") from None
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f'W/"{digest[:32]}"'


def if_none_match(header: str | None, etag: str) -> bool:
    """True when the client copy is current and the route can answer 304.

    ``header`` is the raw ``If-None-Match`` value. ``None`` or an empty
    header means the client has no copy (False). ``*`` matches any tag.
    A comma-separated list matches when any item equals ``etag`` after
    the weak prefix ``W/`` is ignored on both sides (weak comparison).
    """
    _check_str(etag, BAD_ETAG, "etag")
    if header is None:
        return False
    if not isinstance(header, str):
        raise RefreshError(BAD_HEADER, "If-None-Match header must be a str or None")
    header = header.strip()
    if header == "":
        return False
    if _has_control(header):
        raise RefreshError(BAD_HEADER, "If-None-Match header must not contain control characters")
    if header == "*":
        return True
    bare = _bare(etag)
    return any(_bare(item) == bare for item in header.split(",") if item.strip() != "")


def clamp_interval(value: int, policy: RefreshPolicy = RefreshPolicy()) -> int:
    """Clamp a client- or config-supplied interval into the policy bounds.

    Returns ``min_s`` when the value is smaller and ``max_s`` when it is
    larger. A bool, a non-int or a negative value is rejected.
    """
    _require_policy(policy)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RefreshError(BAD_INTERVAL, "interval must be an int, not a bool or float")
    if value < 0:
        raise RefreshError(BAD_INTERVAL, "interval must not be negative")
    return max(min(value, policy.max_s), policy.min_s)


def next_delay(
    policy: RefreshPolicy, *, unchanged_polls: int = 0, failures: int = 0
) -> float | None:
    """Delay in seconds before the next automatic poll, or ``None``.

    Automatic refresh backs off deterministically, without jitter: every
    poll that found no change multiplies the delay by 1.5 and every
    failure multiplies it by 2. The factors combine multiplicatively and
    the result is capped at ``max_s`` and never below ``min_s``. When
    automatic refresh is off the caller does not poll and the result is
    ``None``. Manual refresh is never delayed.
    """
    _require_policy(policy)
    unchanged_polls = _check_count(unchanged_polls, "unchanged_polls")
    failures = _check_count(failures, "failures")
    if not policy.auto_enabled:
        return None
    delay = float(policy.interval_s)
    for _ in range(unchanged_polls):
        delay *= 1.5
        if delay >= policy.max_s:
            break
    for _ in range(failures):
        delay *= 2.0
        if delay >= policy.max_s:
            break
    return min(max(delay, float(policy.min_s)), float(policy.max_s))


def _row_line(row: Mapping) -> str:
    if not isinstance(row, Mapping):
        raise RefreshError(BAD_ROW, "each work-list row must be a mapping")
    row_id = row.get("id")
    if row_id is None:
        raise RefreshError(MISSING_ID, "work-list row is missing id")
    if isinstance(row_id, bool) or not isinstance(row_id, (int, str)):
        raise RefreshError(BAD_ID, "work-list row id must be an int or a str")
    id_part = _check_str(row_id, BAD_ID, "work-list row id") if isinstance(row_id, str) else str(row_id)
    version = row.get("version")
    if version is None:
        version = row.get("updated_at")
    if version is None:
        raise RefreshError(MISSING_VERSION, "work-list row is missing version")
    return f"{id_part}:{_version_part(version)}"


def _version_part(version) -> str:
    if isinstance(version, bool):
        raise RefreshError(BAD_VERSION, "version must not be a bool")
    if isinstance(version, int):
        return str(version)
    if isinstance(version, str):
        return _check_str(version, BAD_VERSION, "version")
    if isinstance(version, datetime):
        if version.tzinfo is None or version.tzinfo.utcoffset(version) is None:
            raise RefreshError(BAD_VERSION, "version datetime must be timezone-aware")
        return version.isoformat()
    raise RefreshError(BAD_VERSION, "version must be an int, a str or a timezone-aware datetime")


def _bare(tag: str) -> str:
    tag = tag.strip()
    if tag.startswith("W/"):
        tag = tag[2:]
    return tag.strip()


def _has_control(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _check_str(value: object, code: str, label: str) -> str:
    if not isinstance(value, str) or value == "" or _has_control(value):
        raise RefreshError(code, f"{label} must be a non-empty string without control characters")
    return value


def _check_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RefreshError(BAD_COUNTS, f"{label} must be an int, not a bool or float")
    if value < 0:
        raise RefreshError(BAD_COUNTS, f"{label} must not be negative")
    return value


def _require_policy(policy: object) -> RefreshPolicy:
    if not isinstance(policy, RefreshPolicy):
        raise RefreshError(BAD_POLICY, "policy must be a RefreshPolicy")
    return policy
