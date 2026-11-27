"""Summarise processing-job health without exposing free-text error details.

Callers supply rows and a clock value; this module does not access the queue,
database or web application.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from src.clinic.asr_capabilities import CAPABILITY_UNAVAILABLE

_STATUSES = ("queued", "processing", "completed", "failed")
_UTC_MIN = datetime.min.replace(tzinfo=timezone.utc)
_REDACTED = "[…]"
_FAILURE_LABELS = {
    "storage_unavailable": "Ablage nicht verfügbar",
    "engine_unavailable": "Erkennungsengine nicht verfügbar",
    "missing_author_pool": "Autor oder Autorenpool fehlt",
    "corrupt_audio": "Audiodatei beschädigt",
}
_EMAIL_LOCAL = r"[\w.!#$%&'*+/=?^`{|}~-]"
_SENSITIVE = re.compile(
    r"""(["'])(?:\\[\s\S]|(?!\1)[^\\])*(?:\1|\\?\Z)"""
    rf"|(?<![\w.!#$%&*+/=?^`{{|}}~-]){_EMAIL_LOCAL}+@[\w-]+(?:\.[\w-]+)*"
    r"|\d{6,}"
)
_EXCEPTION_NAME = re.compile(
    r"(?:[a-zA-Z_][a-zA-Z_0-9]*\.)*"
    r"(?:[A-Z][a-zA-Z_0-9]*(?:Error|Exception|Warning)|Error|Exception|Warning"
    r"|StopIteration|StopAsyncIteration|KeyboardInterrupt|SystemExit|GeneratorExit)"
)


class HealthError(ValueError):
    """Invalid health input, identified by a stable code without input values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FailureView:
    id: int
    job_type: str
    error_class: str
    message: str
    finished_at: datetime | None


@dataclass(frozen=True)
class HealthSummary:
    counts: dict[str, int]
    stuck_ids: tuple[int, ...]
    oldest_queued_age_s: int | None
    recent_failures: tuple[FailureView, ...]


def scrub_message(message: str | None, limit: int = 200) -> str:
    """Redact recognisable patterns, collapse whitespace and cap the result."""
    limit = _nonnegative(limit, "limit")
    if message is None:
        return ""
    _validate_message(message)
    scrubbed = " ".join(_SENSITIVE.sub(_REDACTED, message).split())
    if not limit:
        return ""
    return scrubbed if len(scrubbed) <= limit else scrubbed[: limit - 1] + "…"


def summarise(
    rows: Iterable[Mapping[str, object]],
    now: datetime,
    *,
    stuck_after_s: int = 1800,
    max_failures: int = 10,
) -> HealthSummary:
    """Summarise a snapshot; interpret naive job timestamps as UTC."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise HealthError("invalid_now")
    now = now.astimezone(timezone.utc)
    stuck_after_s = _nonnegative(stuck_after_s, "stuck_after_s")
    max_failures = _nonnegative(max_failures, "max_failures")
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes, Mapping)):
        raise HealthError("invalid_rows")

    counts = dict.fromkeys(_STATUSES, 0)
    stuck_ids: list[int] = []
    oldest_queued_age_s = None
    failures: list[FailureView] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise HealthError("invalid_row")
        job_id = row.get("id")
        if not isinstance(job_id, int) or isinstance(job_id, bool):
            raise HealthError("invalid_id")
        status = _text(row.get("status"), "status")
        job_type = _text(row.get("job_type"), "job_type")
        created = _row_time(row, "created_at")
        started = _row_time(row, "started_at")
        finished = _row_time(row, "completed_at")
        error = _validate_message(row.get("error_message"))

        counts[status] = counts.get(status, 0) + 1
        if status == "queued" or status == "processing":
            since = (started or created) if status == "processing" else created
            if since is None:
                raise HealthError("invalid_created_at")
            age = (now - since).total_seconds()
            if status == "queued":
                oldest_queued_age_s = max(oldest_queued_age_s or 0, int(age))
            elif age > stuck_after_s:
                stuck_ids.append(job_id)
        elif status == "failed" and max_failures:
            name, separator, _ = (error or "").partition(":")
            name = name.strip()
            error_class = "Error"
            if separator and _EXCEPTION_NAME.fullmatch(name) and not _SENSITIVE.search(name):
                error_class = name
            # Pattern scrubbing cannot reliably detect unquoted clinical text.
            message = "Fehlerdetails ausgeblendet." if error is not None else ""
            # Only an exact leading code is trusted; never classify free-text details.
            if name in _FAILURE_LABELS:
                error_class = name
                message = _FAILURE_LABELS[name]
            elif job_type == "asr_training" and error == CAPABILITY_UNAVAILABLE:
                error_class = CAPABILITY_UNAVAILABLE
                message = "Training nicht verfügbar"
            failures.append(FailureView(job_id, job_type, error_class, message, finished))

    failures.sort(key=lambda failure: (
        failure.finished_at is not None, failure.finished_at or _UTC_MIN, failure.id
    ), reverse=True)
    return HealthSummary(
        counts, tuple(sorted(stuck_ids)), oldest_queued_age_s, tuple(failures[:max_failures])
    )


def degraded(
    summary: HealthSummary, *, max_stuck: int = 0, max_queue_age_s: int = 3600
) -> tuple[str, ...]:
    """Return the breached thresholds in code order."""
    if not isinstance(summary, HealthSummary):
        raise HealthError("invalid_summary")
    max_stuck = _nonnegative(max_stuck, "max_stuck")
    max_queue_age_s = _nonnegative(max_queue_age_s, "max_queue_age_s")
    reasons = []
    if summary.oldest_queued_age_s is not None and summary.oldest_queued_age_s > max_queue_age_s:
        reasons.append("queue_old")
    if len(summary.stuck_ids) > max_stuck:
        reasons.append("stuck_jobs")
    return tuple(reasons)


def _nonnegative(value: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HealthError(f"invalid_{field}")
    return value


def _text(value: object, field: str, allowed_controls: str = "") -> str:
    if not isinstance(value, str) or not value.strip() or any(
        unicodedata.category(char) in {"Cc", "Cf"} and char not in allowed_controls
        for char in value
    ):
        raise HealthError(f"invalid_{field}")
    return value


def _validate_message(value: object) -> str | None:
    return None if value is None else _text(value, "error_message", "\t\n\r\v\f")


def _row_time(row: Mapping[str, object], field: str) -> datetime | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise HealthError(f"invalid_{field}")
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
