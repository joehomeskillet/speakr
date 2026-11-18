"""Notification texts and recipients for clinic dictation events.

Pure functions for the four acceptance/progress events. No Flask, no
database access, no clock: the caller passes ids, references and context.
A notification never carries patient data: the text comes from fixed
German templates plus a whitelist of context keys (``count``,
``priority_label``, ``deadline_date``); any other context key is rejected,
never silently ignored. Persistence and routes are a later wiring package.

Approval (Abnahme) and speech recognition are strictly independent; this
module knows nothing about ASR and no code path couples them. Nothing here
deletes data.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime

EVENTS = ("acceptance_requested", "acceptance_rejected", "accepted", "completed")
ALLOWED_CONTEXT = ("count", "priority_label", "deadline_date")

UNKNOWN_EVENT = "unknown_event"
FORBIDDEN_CONTEXT = "forbidden_context"
BAD_REF = "bad_ref"
BAD_RECIPIENT = "bad_recipient"
BAD_CONTEXT = "bad_context"
BAD_COUNT = "bad_count"
BAD_PRIORITY = "bad_priority"
BAD_DEADLINE = "bad_deadline"

PRIORITIES = ("Bei Gelegenheit", "Normal", "Dringend")

_REF_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_COUNT = 9999
_URL_PREFIX = "/clinic/dictations/"

_TITLES = {
    "acceptance_requested": "Abnahme angefordert",
    "acceptance_rejected": "Abnahme abgelehnt",
    "accepted": "Diktat abgenommen",
    "completed": "Diktat fertiggestellt",
}

# (singular, plural) predicate; the subject is "Das Diktat" or "N Diktate".
_PREDICATES = {
    "acceptance_requested": ("wartet auf Ihre Abnahme", "warten auf Ihre Abnahme"),
    "acceptance_rejected": ("braucht eine Korrektur", "brauchen eine Korrektur"),
    "accepted": ("wurde abgenommen", "wurden abgenommen"),
    "completed": ("ist fertiggestellt", "sind fertiggestellt"),
}


class NotificationError(ValueError):
    """A notification input was rejected before anything was built.

    ``code`` is a stable string, e.g. ``unknown_event``, ``forbidden_context``
    or ``bad_ref``.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Notification:
    event: str
    title: str
    body: str
    url_path: str
    recipient_ids: tuple[int, ...]


def recipients_for(
    event: str,
    *,
    author_id: int,
    assignee_id: int | None,
    acceptor_ids: Collection[int],
    actor_id: int,
) -> tuple[int, ...]:
    """Who is notified for ``event``, with the acting user always removed.

    ``acceptance_requested`` goes to the acceptors, ``acceptance_rejected``
    to the assignee (nobody if there is none), ``accepted`` to author and
    assignee, ``completed`` to the author. Duplicates collapse, the result
    is sorted ascending, and an empty recipient set is not an error.
    """
    if event not in EVENTS:
        raise NotificationError(UNKNOWN_EVENT)
    author = _require_id(author_id)
    actor = _require_id(actor_id)
    acceptors = _id_set(acceptor_ids)
    recipients: set[int] = set()
    if event == "acceptance_requested":
        recipients |= acceptors
    elif event == "acceptance_rejected":
        if assignee_id is not None:
            recipients.add(_require_id(assignee_id))
    elif event == "accepted":
        recipients.add(author)
        if assignee_id is not None:
            recipients.add(_require_id(assignee_id))
    else:  # completed
        recipients.add(author)
    recipients.discard(actor)
    return tuple(sorted(recipients))


def build_notification(
    event: str,
    *,
    dictation_ref: str,
    recipient_ids: Collection[int],
    context: Mapping | None = None,
) -> Notification:
    """Build the fixed German title and body for one event.

    ``dictation_ref`` must match ``[A-Za-z0-9_-]{1,64}`` (a uuid is fine).
    ``context`` may only use the keys in ``ALLOWED_CONTEXT``; a value-rich
    context still yields a body free of any patient data.
    """
    if event not in EVENTS:
        raise NotificationError(UNKNOWN_EVENT)
    ref = _require_ref(dictation_ref)
    recipients = _id_set(recipient_ids)
    checked = _check_context(context)
    title = _TITLES[event]
    body = _body(event, checked)
    return Notification(
        event=event,
        title=title,
        body=body,
        url_path=_URL_PREFIX + ref,
        recipient_ids=tuple(sorted(recipients)),
    )


def _body(event: str, context: dict) -> str:
    count = context.get("count")
    if count is None:
        subject = "Das Diktat"
        predicate = _PREDICATES[event][0]
    else:
        subject = "1 Diktat" if count == 1 else f"{count} Diktate"
        predicate = _PREDICATES[event][0 if count == 1 else 1]
    parts = [f"{subject} {predicate}."]
    priority = context.get("priority_label")
    if priority is not None:
        parts.append(f"Priorität: {priority}.")
    deadline = context.get("deadline_date")
    if deadline is not None:
        parts.append(f"Fällig am {deadline.day:02d}.{deadline.month:02d}.{deadline.year:04d}.")
    return " ".join(parts)


def _require_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationError(BAD_RECIPIENT)
    return value


def _id_set(values: Collection[int]) -> set[int]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Collection):
        raise NotificationError(BAD_RECIPIENT)
    return {_require_id(value) for value in values}


def _require_ref(value: str) -> str:
    if not isinstance(value, str) or _REF_PATTERN.fullmatch(value) is None:
        raise NotificationError(BAD_REF)
    return value


def _check_context(context: Mapping | None) -> dict:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise NotificationError(BAD_CONTEXT)
    for key in context:
        if key not in ALLOWED_CONTEXT:
            raise NotificationError(FORBIDDEN_CONTEXT)
    checked = {}
    if "count" in context:
        count = context["count"]
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            or count > _MAX_COUNT
        ):
            raise NotificationError(BAD_COUNT)
        checked["count"] = count
    if "priority_label" in context:
        priority = context["priority_label"]
        if priority not in PRIORITIES:
            raise NotificationError(BAD_PRIORITY)
        checked["priority_label"] = priority
    if "deadline_date" in context:
        deadline = context["deadline_date"]
        # A datetime carries a time; the deadline context is a plain date.
        if isinstance(deadline, datetime) or not isinstance(deadline, date):
            raise NotificationError(BAD_DEADLINE)
        checked["deadline_date"] = deadline
    return checked
