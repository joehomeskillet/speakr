"""Tests for clinic notification texts and recipients (PAR-WF-014)."""

from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from src.clinic.notification_rules import (
    ALLOWED_CONTEXT,
    EVENTS,
    Notification,
    NotificationError,
    build_notification,
    recipients_for,
)


def _err_code(call):
    with pytest.raises(NotificationError) as excinfo:
        call()
    return excinfo.value.code


# --- Criterion 1: recipients -------------------------------------------------


def test_recipients_acceptance_requested_goes_to_acceptors():
    assert recipients_for(
        "acceptance_requested",
        author_id=1,
        assignee_id=2,
        acceptor_ids=[3, 4],
        actor_id=3,
    ) == (4,)


def test_recipients_acceptance_rejected_goes_to_assignee():
    assert recipients_for(
        "acceptance_rejected", author_id=1, assignee_id=2, acceptor_ids=[3], actor_id=1
    ) == (2,)


def test_recipients_acceptance_rejected_without_assignee_is_empty():
    assert recipients_for(
        "acceptance_rejected", author_id=1, assignee_id=None, acceptor_ids=[3], actor_id=1
    ) == ()


def test_recipients_accepted_goes_to_author_and_assignee():
    assert recipients_for(
        "accepted", author_id=1, assignee_id=2, acceptor_ids=[3], actor_id=3
    ) == (1, 2)


def test_recipients_accepted_without_assignee_goes_to_author():
    assert recipients_for(
        "accepted", author_id=1, assignee_id=None, acceptor_ids=[3], actor_id=3
    ) == (1,)


def test_recipients_completed_goes_to_author():
    assert recipients_for(
        "completed", author_id=1, assignee_id=2, acceptor_ids=[3], actor_id=2
    ) == (1,)


@pytest.mark.parametrize("event", EVENTS)
def test_recipients_actor_is_always_removed(event):
    result = recipients_for(
        event, author_id=1, assignee_id=2, acceptor_ids=[1, 2, 3], actor_id=2
    )
    assert 2 not in result


def test_recipients_duplicates_collapse_and_result_is_sorted():
    assert recipients_for(
        "acceptance_requested",
        author_id=1,
        assignee_id=None,
        acceptor_ids=[9, 5, 9, 7],
        actor_id=1,
    ) == (5, 7, 9)


def test_recipients_unknown_event_raises():
    code = _err_code(
        lambda: recipients_for(
            "asr_finished", author_id=1, assignee_id=None, acceptor_ids=[2], actor_id=1
        )
    )
    assert code == "unknown_event"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"author_id": True, "assignee_id": None, "acceptor_ids": [2], "actor_id": 1},
        {"author_id": "1", "assignee_id": None, "acceptor_ids": [2], "actor_id": 1},
        {"author_id": 1, "assignee_id": 2.0, "acceptor_ids": [2], "actor_id": 1},
        {"author_id": 1, "assignee_id": None, "acceptor_ids": [2, "3"], "actor_id": 1},
        {"author_id": 1, "assignee_id": None, "acceptor_ids": 2, "actor_id": 1},
        {"author_id": 1, "assignee_id": None, "acceptor_ids": [2], "actor_id": False},
    ],
)
def test_recipients_rejects_wrong_id_types(kwargs):
    code = _err_code(
        lambda: recipients_for("accepted", **kwargs)  # noqa: B008 - pytest parametrisation
    )
    assert code == "bad_recipient"


# --- Criterion 2: fixed German texts -----------------------------------------


@pytest.mark.parametrize(
    ("event", "title"),
    [
        ("acceptance_requested", "Abnahme angefordert"),
        ("acceptance_rejected", "Abnahme abgelehnt"),
        ("accepted", "Diktat abgenommen"),
        ("completed", "Diktat fertiggestellt"),
    ],
)
def test_titles_are_fixed_german(event, title):
    notification = build_notification(
        event, dictation_ref="ref-1", recipient_ids=(1,), context=None
    )
    assert notification.title == title


@pytest.mark.parametrize(
    ("event", "snippet"),
    [
        ("acceptance_requested", "Abnahme"),
        ("acceptance_rejected", "Korrektur"),
        ("accepted", "abgenommen"),
        ("completed", "fertiggestellt"),
    ],
)
def test_bodies_are_short_and_data_free(event, snippet):
    notification = build_notification(
        event, dictation_ref="ref-1", recipient_ids=(1,), context=None
    )
    assert notification.body.startswith("Das Diktat ")
    assert snippet in notification.body
    assert len(notification.body) < 120


def test_count_context_mentions_the_number():
    notification = build_notification(
        "acceptance_requested",
        dictation_ref="ref-1",
        recipient_ids=(1,),
        context={"count": 3},
    )
    assert "3 Diktate" in notification.body


def test_priority_and_deadline_are_rendered_in_german():
    notification = build_notification(
        "acceptance_requested",
        dictation_ref="ref-1",
        recipient_ids=(1,),
        context={"priority_label": "Dringend", "deadline_date": date(2026, 11, 24)},
    )
    assert "Dringend" in notification.body
    assert "24.11.2026" in notification.body


def test_singular_count_reads_as_one_dictation():
    notification = build_notification(
        "completed", dictation_ref="ref-1", recipient_ids=(1,), context={"count": 1}
    )
    assert "1 Diktat ist fertiggestellt." in notification.body


# --- Criterion 3: forbidden context keys -------------------------------------


@pytest.mark.parametrize(
    "key", ["patient_name", "title", "author_name", "text", "case_id", "ahv"]
)
def test_forbidden_context_keys_are_rejected(key):
    code = _err_code(
        lambda: build_notification(
            "completed",
            dictation_ref="ref-1",
            recipient_ids=(1,),
            context={key: "x"},
        )
    )
    assert code == "forbidden_context"


def test_allowed_context_keys_are_exactly_the_whitelist():
    assert ALLOWED_CONTEXT == ("count", "priority_label", "deadline_date")


def test_context_that_is_not_a_mapping_is_rejected():
    code = _err_code(
        lambda: build_notification(
            "completed", dictation_ref="ref-1", recipient_ids=(1,), context=["count"]
        )
    )
    assert code == "bad_context"


# --- Criterion 4: dictation_ref and url_path ---------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "0f8d7c2e-9a41-4b6e-8f3a-2c5d1e7b9a04",
        "a",
        "A_b-C9",
        "x" * 64,
    ],
)
def test_valid_refs_are_accepted(ref):
    notification = build_notification(
        "completed", dictation_ref=ref, recipient_ids=(1,), context=None
    )
    assert notification.url_path == "/clinic/dictations/" + ref


@pytest.mark.parametrize(
    "ref",
    [
        "",
        " " * 4,
        "a b",
        "pfad/1",
        "../etc",
        "diktat-übertragung",
        "x" * 65,
        "line\nbreak",
        "tab\tchar",
        123,
        None,
    ],
)
def test_invalid_refs_raise_bad_ref(ref):
    code = _err_code(
        lambda: build_notification("completed", dictation_ref=ref, recipient_ids=(1,))
    )
    assert code == "bad_ref"


# --- Criterion 5: negative leak test -----------------------------------------

_FORBIDDEN_SAMPLES = ("Meier", "Patient", "Diagnose", "@")
_LONG_DIGITS = re.compile(r"\d{6,}")


@pytest.mark.parametrize("event", EVENTS)
def test_rich_context_leaks_nothing(event):
    rich = {
        "count": 42,
        "priority_label": "Dringend",
        "deadline_date": date(2026, 11, 24),
    }
    notification = build_notification(
        event, dictation_ref="ref-1", recipient_ids=(1,), context=rich
    )
    combined = notification.title + " " + notification.body
    for sample in _FORBIDDEN_SAMPLES:
        assert sample not in combined
    assert _LONG_DIGITS.search(combined) is None


# --- Criterion 6: context value validation -----------------------------------


@pytest.mark.parametrize("count", [True, False, 0, -1, 10000, 3.0, "3", None])
def test_invalid_counts_are_rejected(count):
    code = _err_code(
        lambda: build_notification(
            "completed", dictation_ref="ref-1", recipient_ids=(1,), context={"count": count}
        )
    )
    assert code == "bad_count"


@pytest.mark.parametrize("count", [1, 9999])
def test_count_boundaries_are_accepted(count):
    notification = build_notification(
        "completed", dictation_ref="ref-1", recipient_ids=(1,), context={"count": count}
    )
    assert str(count) in notification.body


@pytest.mark.parametrize("priority", ["Sofort", "", "dringend", 1, None])
def test_invalid_priorities_are_rejected(priority):
    code = _err_code(
        lambda: build_notification(
            "completed",
            dictation_ref="ref-1",
            recipient_ids=(1,),
            context={"priority_label": priority},
        )
    )
    assert code == "bad_priority"


@pytest.mark.parametrize("priority", ["Bei Gelegenheit", "Normal", "Dringend"])
def test_valid_priorities_are_accepted(priority):
    notification = build_notification(
        "completed",
        dictation_ref="ref-1",
        recipient_ids=(1,),
        context={"priority_label": priority},
    )
    assert priority in notification.body


@pytest.mark.parametrize(
    "deadline", ["2026-11-24", datetime(2026, 11, 24, 12, 0), 1763942400, None]
)
def test_invalid_deadlines_are_rejected(deadline):
    code = _err_code(
        lambda: build_notification(
            "completed",
            dictation_ref="ref-1",
            recipient_ids=(1,),
            context={"deadline_date": deadline},
        )
    )
    assert code == "bad_deadline"


def test_plain_date_deadline_is_accepted():
    notification = build_notification(
        "completed",
        dictation_ref="ref-1",
        recipient_ids=(1,),
        context={"deadline_date": date(2026, 11, 24)},
    )
    assert "24.11.2026" in notification.body


# --- Criterion 7: empty recipient sets ---------------------------------------


def test_empty_acceptors_give_empty_recipient_tuple():
    assert recipients_for(
        "acceptance_requested", author_id=1, assignee_id=2, acceptor_ids=[], actor_id=1
    ) == ()


def test_build_notification_accepts_no_recipients():
    notification = build_notification(
        "completed", dictation_ref="ref-1", recipient_ids=(), context=None
    )
    assert notification.recipient_ids == ()


# --- Shape and stability ------------------------------------------------------


def test_notification_is_frozen_with_sorted_recipients():
    notification = build_notification(
        "accepted", dictation_ref="ref-1", recipient_ids=[7, 3, 3, 5], context=None
    )
    assert isinstance(notification, Notification)
    assert notification.recipient_ids == (3, 5, 7)
    assert notification.event == "accepted"
    with pytest.raises(Exception):
        notification.title = "x"


def test_error_code_is_a_stable_string():
    with pytest.raises(NotificationError) as excinfo:
        build_notification("nope", dictation_ref="ref-1", recipient_ids=(1,))
    assert excinfo.value.code == "unknown_event"
    assert str(excinfo.value) == "unknown_event"
    assert isinstance(excinfo.value, ValueError)
