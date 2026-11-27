"""Job-health calculations and operator privacy, using synthetic rows only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.clinic.job_health import HealthError, degraded, scrub_message, summarise

_NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


def _ago(seconds: float) -> datetime:
    return (_NOW - timedelta(seconds=seconds)).replace(tzinfo=None)


def _row(job_id: int, status: str, **fields: object) -> dict[str, object]:
    return {
        "id": job_id, "status": status, "job_type": "transcribe",
        "created_at": _ago(0), **fields,
    }


def test_counts_all_statuses_including_unrecognised_ones() -> None:
    rows = (_row(job_id, status) for job_id, status in enumerate(
        ("completed", "cancelled", "cancelled", "processing"), start=1
    ))
    assert summarise(rows, _NOW).counts == {
        "queued": 0, "processing": 1, "completed": 1, "failed": 0, "cancelled": 2,
    }


def test_queue_age_and_stuck_jobs_use_the_relevant_timestamps() -> None:
    rows = [
        _row(5, "processing", started_at=_ago(1800.5)),
        _row(1, "processing", started_at=_ago(1800), created_at=_ago(6000)),
        _row(3, "processing", started_at=None, created_at=_ago(1801)),
        _row(4, "queued", created_at=_ago(4200.5)),
        _row(2, "queued", created_at=_ago(20)),
    ]
    summary = summarise(rows, _NOW.astimezone(timezone(timedelta(hours=1))))
    assert summary.stuck_ids == (3, 5)
    assert summary.oldest_queued_age_s == 4200
    assert summarise([rows[0]], _NOW).oldest_queued_age_s is None
    assert summarise([_row(1, "queued", created_at=_ago(-10))], _NOW).oldest_queued_age_s == 0


def test_recent_failures_use_completion_time_then_id_and_extract_classes() -> None:
    rows = [
        _row(9, "failed", completed_at=None),
        _row(4, "failed", completed_at=_ago(9), error_message="keine Klasse: Details"),
        _row(8, "failed", completed_at=_ago(10), error_message="ValueError: Details"),
        _row(2, "failed", completed_at=_NOW, error_message="worker.media.DecodeError: Details"),
        _row(6, "failed", completed_at=_ago(9), error_message="KeyboardInterrupt: Details"),
        _row(7, "completed", completed_at=_NOW),
    ]
    summary = summarise(rows, _NOW, max_failures=4)
    assert summary.counts["failed"] == 5
    assert [(failure.id, failure.error_class) for failure in summary.recent_failures] == [
        (2, "worker.media.DecodeError"), (6, "KeyboardInterrupt"), (4, "Error"), (8, "ValueError"),
    ]
    assert summary.recent_failures[0].job_type == "transcribe"
    assert summary.recent_failures[1].finished_at == _ago(9).replace(tzinfo=timezone.utc)
    assert summarise(rows, _NOW).recent_failures[-1].finished_at is None
    assert summarise(rows, _NOW, max_failures=0).recent_failures == ()


@pytest.mark.parametrize(("message", "expected"), [
    (None, ""),
    ("  mehrere\tWörter\n über Zeilen  ", "mehrere Wörter über Zeilen"),
    ("Kontakt a+b@sub.example.ch.", "Kontakt […]."),
    ("'Zitat'anna.o'neill@example.ch", "[…][…]"),
    ("Nummern 12345 und 123456", "Nummern 12345 und […]"),
    ("'erste' und \"zweite\"", "[…] und […]"),
    (r'''Fehler "Text mit \"Zitat\" und Rest" Ende''', "Fehler […] Ende"),
    ("Fehler 'nicht geschlossen", "Fehler […]"),
    ("ValueError: bad text 'Patient Meier hat 123456789' mail a@b.ch",
     "ValueError: bad text […] mail […]"),
])
def test_scrub_message_redacts_and_normalises(message: str | None, expected: str) -> None:
    assert scrub_message(message) == expected


def test_scrub_message_caps_length_after_redaction() -> None:
    assert scrub_message("abcdef", limit=5) == "abcd…"
    assert scrub_message("abcde", limit=5) == "abcde"
    assert scrub_message("'langes Zitat'", limit=3) == "[…]"
    assert scrub_message("abcdef", limit=1) == "…"
    assert scrub_message("abcdef", limit=0) == ""


def test_operator_failure_never_copies_quoted_or_unquoted_details() -> None:
    excerpt = "Synthetischer Befund zur Verlaufskontrolle mit mehreren Abschnitten " * 5
    row = _row(1, "failed", error_message=f"ValueError: '{excerpt}' Fall A-42, Mustername")
    (failure,) = summarise([row], _NOW).recent_failures
    assert failure.error_class == "ValueError"
    assert failure.message == "Fehlerdetails ausgeblendet."


def test_degraded_returns_sorted_reasons_only_when_thresholds_are_exceeded() -> None:
    summary = summarise([
        _row(1, "queued", created_at=_ago(3601)),
        _row(2, "processing", created_at=_ago(1801)),
    ], _NOW)
    assert degraded(summary) == ("queue_old", "stuck_jobs")
    assert degraded(summary, max_stuck=1) == ("queue_old",)
    assert degraded(summary, max_queue_age_s=3601) == ("stuck_jobs",)
    assert degraded(summary, max_stuck=1, max_queue_age_s=3601) == ()


def test_empty_input_is_healthy() -> None:
    summary = summarise([], _NOW)
    assert summary.counts == {"queued": 0, "processing": 0, "completed": 0, "failed": 0}
    assert summary.stuck_ids == summary.recent_failures == ()
    assert summary.oldest_queued_age_s is None
    assert degraded(summary) == ()


@pytest.mark.parametrize(("code", "label"), [
    ("storage_unavailable", "Ablage nicht verfügbar"),
    ("engine_unavailable", "Erkennungsengine nicht verfügbar"),
    ("missing_author_pool", "Autor oder Autorenpool fehlt"),
    ("corrupt_audio", "Audiodatei beschädigt"),
])
@pytest.mark.parametrize("suffix", ["", ": Befund Mustername 'vertraulich' a@b.ch 123456789"])
def test_known_failure_codes_use_only_safe_labels(code, label, suffix) -> None:
    row = _row(1, "failed", error_message=code + suffix)
    (failure,) = summarise([row], _NOW).recent_failures
    assert failure.error_class == code
    assert failure.message == label


@pytest.mark.parametrize("error", [
    "unknown_code: Befund Mustername",
    "storage_unavailable_extra: Befund Mustername",
    "Befund storage_unavailable",
    "storage_unavailable Befund Mustername",
    "'storage_unavailable': Befund Mustername",
    "ValueError: engine_unavailable: Befund Mustername",
])
def test_failure_codes_are_not_inferred_from_free_text(error) -> None:
    (failure,) = summarise([_row(1, "failed", error_message=error)], _NOW).recent_failures
    assert failure.message == "Fehlerdetails ausgeblendet."
    assert failure.error_class == ("ValueError" if error.startswith("ValueError:") else "Error")


def test_training_unavailable_keeps_its_existing_label() -> None:
    row = _row(1, "failed", job_type="asr_training", error_message="CAPABILITY_UNAVAILABLE")
    (failure,) = summarise([row], _NOW).recent_failures
    assert failure.error_class == "CAPABILITY_UNAVAILABLE"
    assert failure.message == "Training nicht verfügbar"


def test_naive_clock_and_missing_id_are_rejected() -> None:
    with pytest.raises(HealthError) as error:
        summarise([], _NOW.replace(tzinfo=None))
    assert error.value.code == "invalid_now"
    row = _row(1, "queued")
    del row["id"]
    with pytest.raises(HealthError) as error:
        summarise([row], _NOW)
    assert error.value.code == "invalid_id"


@pytest.mark.parametrize(("field", "value"), [
    ("id", True), ("status", 42), ("status", ""), ("status", "que\x00ued"),
    ("job_type", " "), ("error_message", "invalid\x00detail"),
    ("created_at", "2026-11-18"), ("started_at", 1), ("completed_at", False),
])
def test_malformed_row_fields_have_stable_error_codes(field: str, value: object) -> None:
    row = _row(1, "queued")
    row[field] = value
    with pytest.raises(HealthError) as error:
        summarise([row], _NOW)
    assert error.value.code == f"invalid_{field}"


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf")])
def test_nonnegative_integer_limits_reject_invalid_values(value: int) -> None:
    with pytest.raises(HealthError, match="^invalid_limit$"):
        scrub_message(None, limit=value)
    with pytest.raises(HealthError, match="^invalid_stuck_after_s$"):
        summarise([], _NOW, stuck_after_s=value)
    with pytest.raises(HealthError, match="^invalid_max_failures$"):
        summarise([], _NOW, max_failures=value)
    with pytest.raises(HealthError, match="^invalid_max_stuck$"):
        degraded(summarise([], _NOW), max_stuck=value)
    with pytest.raises(HealthError, match="^invalid_max_queue_age_s$"):
        degraded(summarise([], _NOW), max_queue_age_s=value)
