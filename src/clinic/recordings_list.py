"""Chronological, policy-filtered list of dictations for V8 page P01 (Aufnahmen).

Visibility reuses the worklist: the injected loader narrows the candidates
(W12 admin full access incl. audit of foreign reads, W13 pool leads) and the
worklist's own row policy (``query_worklist``) decides every row. There is no
second policy here. The payload carries identifiers, workflow state and staff
names only: no patient name, no patient reference, no notes. The author-entered
title is projected only on the viewer's own rows (owner decision Q-01); every
other viewer gets ``own_title: None``. Fall-ID context stays in ``case_display``.
Browser microphone takes additionally carry a fixed timestamp label
(``capture_label``).

Sort time: the recording time (``Recording.meeting_date``, set at import from
the client or file metadata) is the best real time and gives
``time_source == "recorded"``. Only when it is missing the dictation's
``created_at`` is used and marked ``"created"``. A separate "imported" source
is part of the contract but no import time is stored yet, so it is never produced.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable
from datetime import date, datetime, timezone
from sys import maxsize
from zoneinfo import ZoneInfo

from src.clinic import policy, pool_policy, recognition_state, routes_worklist, web_ingest, workflow, worklist_entries
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger, PolicyError
from src.clinic.acceptance_rules import can_decide
from src.clinic.archive_policy import ClinicArchiveEntry
from src.clinic.dictation_roles import project_roles
from src.clinic.models import ClinicDictation
from src.clinic.pools import ClinicPoolRoute
from src.clinic.worklist_filters import query_worklist
from src.database import db

ZONE = ZoneInfo("Europe/Zurich")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class RecordingsQueryError(ValueError):
    """The request parameters are invalid."""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def day_of(moment: datetime) -> str:
    """Calendar day of a UTC moment in Europe/Zurich (DST aware)."""
    return _aware(moment).astimezone(ZONE).date().isoformat()


def _iso(moment: datetime) -> str:
    return _aware(moment).isoformat().replace("+00:00", "Z")


def encode_cursor(moment: datetime, dictation_id: str) -> str:
    raw = json.dumps([_iso(moment), dictation_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        moment, dictation_id = json.loads(raw)
        if not isinstance(moment, str) or not isinstance(dictation_id, str):
            raise ValueError
        return _aware(datetime.fromisoformat(moment.replace("Z", "+00:00"))), dictation_id
    except (ValueError, TypeError, binascii.Error) as error:
        raise RecordingsQueryError("cursor") from error


def parse_day(value: str, name: str) -> date:
    try:
        if len(value) != 10:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as error:
        raise RecordingsQueryError(name) from error


def parse_limit(value: str | None) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if not (value.isascii() and value.isdigit() and 1 <= int(value) <= MAX_LIMIT):
        raise RecordingsQueryError("limit")
    return int(value)


def sort_time(dictation: ClinicDictation, recording) -> tuple[datetime, str]:
    """Best real recording time and its source marker."""
    if recording.meeting_date is not None:
        return _aware(recording.meeting_date), "recorded"
    return _aware(dictation.created_at), "created"


def _viewer_review_turn(subject, dictation, status, assignee, holder) -> bool:
    if status not in (workflow.TO_ACCEPT, workflow.ACCEPTED):
        return False
    viewer = str(subject.user_id)
    if holder is not None and holder['id'] != viewer:
        return False
    if status == workflow.ACCEPTED:
        owner = holder or assignee
        return owner is not None and owner['id'] == viewer and pool_policy.decide(subject, 'release', dictation).allowed
    if not pool_policy.decide(subject, 'release', dictation).allowed:
        return False
    try:
        rule = AcceptanceLedger().current(SCOPE_DICTATION, dictation.id).as_rule()
    except PolicyError:
        return False
    return can_decide(rule, subject.user_id, pool_policy.author_pool_ids(subject.user_id))


def list_recordings(
    entries: Iterable[routes_worklist.WorklistEntry],
    subject: policy.Subject,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    author_id: str | None = None,
    pool_id: str | None = None,
    status: str | None = None,
    now: datetime | None = None,
) -> dict:
    """One page of visible dictations, newest first: ``{"items": [...], "next_cursor": str|None}``."""
    if status is not None and status not in workflow.STATUS_ORDER:
        raise RecordingsQueryError("status")
    after = decode_cursor(cursor) if cursor is not None else None

    by_id = {entry.row["dictation_id"]: entry for entry in entries}
    visible = query_worklist((routes_worklist._query_row(entry) for entry in by_id.values()),
                             subject, page_size=maxsize).items
    ids = [row["dictation_id"] for row in visible]
    dictations = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        dictations.update({d.id: d for d in db.session.query(ClinicDictation).filter(ClinicDictation.id.in_(part))})

    pools = None
    if pool_id is not None:  # a staff pool id matches the author pools routed to it
        pools = {pool_id} | {row for (row,) in db.session.query(ClinicPoolRoute.author_pool_id)
                             .filter(ClinicPoolRoute.staff_pool_id == pool_id)}

    ranked = []
    for dictation_id in ids:
        dictation = dictations.get(dictation_id)
        if dictation is None:
            continue
        entry = by_id[dictation_id]
        moment, source = sort_time(dictation, entry.recording)
        key = (moment, dictation_id)
        day = day_of(moment)
        if (after is not None and key >= after) or (status is not None and entry.row["status"] != status):
            continue
        if author_id is not None and str(dictation.author_user_id) != author_id:
            continue
        if pools is not None and dictation.pool_id not in pools:
            continue
        if (date_from is not None and day < date_from.isoformat()) or (
                date_to is not None and day > date_to.isoformat()):
            continue
        ranked.append((key, source, dictation, entry))
    ranked.sort(key=lambda item: item[0], reverse=True)

    page, more = ranked[:limit], len(ranked) > limit
    roles = project_roles([item[2] for item in page], now)
    assignees = {row["dictation_id"]: row.get("assignee")
                 for row in routes_worklist._with_names([item[3].row for item in page])}
    outcomes = recognition_state.asr_outcomes(item[2] for item in page)
    contexts = worklist_entries.case_displays([item[2].id for item in page])
    archive_times = dict(db.session.query(ClinicArchiveEntry.dictation_id, ClinicArchiveEntry.archived_at)
                         .filter(ClinicArchiveEntry.dictation_id.in_([item[2].id for item in page]))) if page else {}
    items = []
    for (moment, dictation_id), source, dictation, entry in page:
        recording = entry.recording
        seconds = recording.audio_duration_seconds
        if seconds is None:
            seconds = entry.row["duration_seconds"] or None
        recorded = recording.meeting_date
        # Keep the retention archive date when present; normal manual archiving
        # writes only ClinicArchiveEntry, without changing the retention axis.
        archived = recording.retention_archived_at or archive_times.get(dictation_id)
        items.append({
            "dictation_id": dictation_id,
            "recording_id": recording.id,
            "recorded_at": None if recorded is None else _iso(recorded),
            "time_source": source,
            "sort_time": _iso(moment),
            "day": day_of(moment),
            **roles[dictation_id],
            "processing_status": (recognition_state.asr_outcome(
                dictation, outcomes=outcomes, detailed=True).upper()
                if outcomes[dictation_id] != "none" else recording.status),
            "workflow_status": entry.row["status"],
            "archived_at": None if archived is None else _iso(archived),
            "duration_ms": None if seconds is None else round(seconds * 1000),
            "case_display": contexts.get(dictation_id),
            "own_title": (recording.title or None) if dictation.author_user_id == subject.user_id else None,
        })
        if assignees[dictation_id] is not None:
            items[-1]["assignee"] = assignees[dictation_id]
        if outcomes[dictation_id] == "completed":
            items[-1]["text_created"] = True
        if _viewer_review_turn(subject, dictation, entry.row['status'], assignees[dictation_id],
                               roles[dictation_id]['lease_holder']):
            items[-1]['viewer_review_turn'] = True
        if (entry.row.get("source_label") == routes_worklist.ORIGIN_LABELS["web-recorder"]
                and (label := web_ingest.generated_recorder_title(recording.title))):
            items[-1]["capture_label"] = label
    last = page[-1][0] if more else None
    return {"items": items, "next_cursor": None if last is None else encode_cursor(*last)}
