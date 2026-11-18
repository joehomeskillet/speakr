"""Connect workflow archive moves to retention and legal hold.

Status changes go through ``workflow.transition``. A recording the dry-run
finds eligible is archived through ``archive.archive_for_retention``.
Configured data-class days win. When they are unset, the days and the
exemption come from ``retention``. Nothing in this module deletes a row
or a file.

``delete`` becomes ``report_archive`` while clinic mode is on. With clinic
mode off it stays ``report_delete``, so the two requests stay distinct.
``policy.ACTIONS`` has no archive token; ``authorize`` reuses ``decide``
with an export stand-in of the same scope, which is not an owner default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.clinic import archive, policy, workflow
from src.clinic.config import clinic_mode_enabled
from src.services import retention

_ZONE = ZoneInfo(workflow.CLINIC_ZONE)
ARCHIVE = "archive"
DELETE = "delete"


class ArchivePolicyError(Exception):
    """A request was refused before the dictation changed. ``code`` is stable."""

    def __init__(self, code: str, reason: str | None = None):
        self.code = code
        self.reason = reason
        super().__init__(code)


class StaleVersion(ArchivePolicyError):
    """``expected_version`` is not the book version. Nothing was written."""

    def __init__(self, current_version: int):
        super().__init__("stale_version")
        self.current_version = current_version


@dataclass(frozen=True)
class Links:
    """Ids that must still point at the same rows after an archive move."""

    original_id: str
    audio_revision_id: str
    transcript_revision_id: str
    context_revision_id: str
    release_id: str | None


@dataclass(frozen=True)
class DryRun:
    """Inspectable retention result. ``would_delete`` is always false."""

    data_class: str
    retention_days: int | None
    legal_hold: bool
    eligible: bool
    reason: str
    archive_slot: str | None
    would_delete: bool
    links: Links


@dataclass(frozen=True)
class ApplyResult:
    """One confirmed or aborted request. A stored result is the retry answer."""

    dictation_id: str
    requested: str
    trigger: str | None
    status: str
    version: int
    links: Links
    dry_run: DryRun
    applied: bool
    request_id: str
    retention_changed: bool


def archive_slot(user_id: int, when: datetime) -> str:
    """Archive projection ``user-id/YYYY-MM`` in Europe/Zurich."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ArchivePolicyError("user_id")
    local = _aware(when).astimezone(_ZONE)
    return f"{user_id}/{local.strftime('%Y-%m')}"


def trigger_for(action: str) -> str:
    """Workflow trigger for a request. Clinic mode archives an ordinary delete."""
    if action == ARCHIVE:
        return "report_archive"
    if action == DELETE:
        return "report_archive" if clinic_mode_enabled() else "report_delete"
    raise ArchivePolicyError("unknown_action")


def authorize(subject: policy.Subject, facts: policy.RecordingFacts, action: str) -> policy.Decision:
    """Delete uses ``policy.decide``. Archive is a separate grant of the same shape."""
    if action == DELETE:
        return policy.decide(subject, DELETE, facts)
    if action != ARCHIVE:
        return policy.Decision(False, "unknown_action")
    for grant in subject.grants:
        if ARCHIVE not in grant.actions:
            continue
        stand_in = policy.Grant(
            scope=grant.scope,
            actions=frozenset({"export"}),
            author_ids=grant.author_ids,
            pool_ids=grant.pool_ids,
        )
        probed = policy.decide(
            policy.Subject(subject.user_id, subject.is_admin, (stand_in,), subject.denies),
            "export",
            facts,
        )
        if probed.allowed:
            return policy.Decision(True, "grant_archive", visible=True)
    owner = facts.owner_id == subject.user_id
    denied = any(
        facts.recording_id in item.recording_ids or facts.owner_id in item.author_ids
        for item in subject.denies
    )
    if denied:
        return policy.Decision(False, "deny_exception", visible=owner)
    if owner:
        return policy.Decision(False, "action_not_granted", visible=True)
    return policy.Decision(False, "no_grant")


def evaluate(
    *,
    data_class: str,
    links: Links,
    user_id: int,
    now: datetime,
    finished_at: datetime | None,
    retention_days: int | None,
    legal_hold: bool,
    recording=None,
) -> DryRun:
    """Eligibility for one data class. Does not read or write workflow state."""
    _links(links)
    days = retention_days
    hold = bool(legal_hold)
    if recording is not None:
        if retention.is_recording_exempt_from_deletion(recording):
            hold = True
        if days is None:
            days = retention.get_retention_days_for_recording(recording)
    moment = _aware(now)
    slot = archive_slot(user_id, moment)
    fields = dict(
        data_class=data_class,
        retention_days=days if isinstance(days, int) and not isinstance(days, bool) else None,
        legal_hold=hold,
        archive_slot=slot,
        would_delete=False,
        links=links,
    )
    if hold:
        return DryRun(eligible=False, reason="legal_hold", **fields)
    if isinstance(days, bool) or days is None or not isinstance(days, int) or days <= 0:
        return DryRun(eligible=False, reason="not_configured", **fields)
    if finished_at is None:
        return DryRun(eligible=False, reason="not_finished", **fields)
    # Equal to the cutoff stays, matching the retention sweep.
    if _aware(finished_at) >= moment - timedelta(days=days):
        return DryRun(eligible=False, reason="not_elapsed", **fields)
    return DryRun(eligible=True, reason="eligible", **fields)


class ArchiveBook:
    """Versions, links and idempotent results for workflow archive requests."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._done: dict[str, ApplyResult] = {}

    def register(
        self,
        dictation_id: str,
        *,
        user_id: int,
        links: Links,
        data_class: str,
        finished_at: datetime | None,
        retention_days: int | None = None,
        legal_hold: bool = False,
    ) -> int:
        """Store links at version 1. A second register of the same id is refused."""
        if not isinstance(dictation_id, str) or not dictation_id:
            raise ArchivePolicyError("not_found")
        if dictation_id in self._rows:
            raise ArchivePolicyError("duplicate")
        if not isinstance(data_class, str) or not data_class.strip():
            raise ArchivePolicyError("data_class")
        if retention_days is not None and (
            isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 0
        ):
            raise ArchivePolicyError("bad_retention")
        archive_slot(user_id, datetime.now(timezone.utc))
        _links(links)
        if finished_at is not None:
            finished_at = _aware(finished_at)
        self._rows[dictation_id] = {
            "user_id": user_id,
            "links": links,
            "data_class": data_class,
            "finished_at": finished_at,
            "retention_days": retention_days,
            "legal_hold": bool(legal_hold),
            "version": 1,
            "archived_at": None,
        }
        return 1

    def dry_run(self, dictation_id: str, now: datetime, recording=None) -> DryRun:
        row = self._require(dictation_id)
        return evaluate(
            data_class=row["data_class"],
            links=row["links"],
            user_id=row["user_id"],
            now=now,
            finished_at=row["finished_at"],
            retention_days=row["retention_days"],
            legal_hold=row["legal_hold"],
            recording=recording,
        )

    def apply(
        self,
        session,
        dictation_id: str,
        *,
        action: str,
        subject: policy.Subject,
        facts: policy.RecordingFacts,
        expected_version: int,
        request_id: str,
        now: datetime,
        confirm: bool,
        recording=None,
        expected_archive_revision: int | None = None,
    ) -> ApplyResult:
        """Confirm moves the workflow. ``confirm=False`` aborts and writes nothing.

        A repeated ``request_id`` returns the stored success and does not
        transition again. Failures are not stored, so a later call can retry.
        """
        if not isinstance(request_id, str) or not request_id:
            raise ArchivePolicyError("request_id")
        if not isinstance(confirm, bool):
            raise ArchivePolicyError("invalid_confirm")
        prior = self._done.get(request_id)
        if prior is not None:
            if prior.dictation_id != dictation_id:
                raise ArchivePolicyError("request_id")
            return prior
        row = self._require(dictation_id)
        if row["version"] != expected_version:
            raise StaleVersion(row["version"])
        decision = authorize(subject, facts, action)
        if not decision.allowed:
            raise ArchivePolicyError("unauthorized", decision.reason)
        item = session.get(workflow.ClinicWorkflowDictation, dictation_id)
        if item is None:
            raise ArchivePolicyError("not_found")
        dry = self.dry_run(dictation_id, now, recording=recording)
        if not confirm:
            return self._result(dictation_id, action, None, item.status, row, dry, False, request_id, False)
        if recording is not None and expected_archive_revision is not None:
            current = int(recording.archive_revision or 0)
            if current != expected_archive_revision:
                raise StaleVersion(current)
        trigger = trigger_for(action)
        if workflow.TRANSITIONS.get((item.status, trigger)) is None:
            raise ArchivePolicyError("transition_failed")
        changed = False
        if recording is not None and dry.eligible:
            changed = bool(archive.archive_for_retention(
                recording,
                now=_aware(now).replace(tzinfo=None),
                retention_days=dry.retention_days,
            ))
        try:
            workflow.transition(session, dictation_id, trigger)
        except workflow.WorkflowTransitionError as exc:
            raise ArchivePolicyError("transition_failed") from exc
        row["version"] += 1
        row["archived_at"] = _aware(now)
        result = self._result(
            dictation_id, action, trigger, item.status, row, dry, True, request_id, changed,
        )
        self._done[request_id] = result
        return result

    def active_groups(self, session, *, role: str, user_id: int, pool_id: str) -> list[workflow.WorkGroup]:
        """Work list without the archive and delete groups."""
        hidden = {workflow.TO_ARCHIVE, workflow.TO_DELETE}
        groups = workflow.worklist(session, role=role, user_id=user_id, pool_id=pool_id)
        return [group for group in groups if group.status not in hidden]

    def archive_index(self) -> dict[str, tuple[str, ...]]:
        """Archived dictation ids by ``user-id/YYYY-MM``."""
        buckets: dict[str, list[str]] = {}
        for ident, row in self._rows.items():
            when = row["archived_at"]
            if when is None:
                continue
            buckets.setdefault(archive_slot(row["user_id"], when), []).append(ident)
        return {key: tuple(sorted(ids)) for key, ids in sorted(buckets.items())}

    def _require(self, dictation_id: str) -> dict:
        row = self._rows.get(dictation_id)
        if row is None:
            raise ArchivePolicyError("not_found")
        return row

    def _result(self, dictation_id, action, trigger, status, row, dry, applied, request_id, changed):
        return ApplyResult(
            dictation_id=dictation_id,
            requested=action,
            trigger=trigger,
            status=status,
            version=row["version"],
            links=row["links"],
            dry_run=dry,
            applied=applied,
            request_id=request_id,
            retention_changed=changed,
        )


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ArchivePolicyError("naive_datetime")
    return value.astimezone(timezone.utc)


def _links(links: Links) -> None:
    if not isinstance(links, Links):
        raise ArchivePolicyError("links_required")
    required = (
        links.original_id,
        links.audio_revision_id,
        links.transcript_revision_id,
        links.context_revision_id,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required):
        raise ArchivePolicyError("links_required")
    if links.release_id is not None and (not isinstance(links.release_id, str) or not links.release_id.strip()):
        raise ArchivePolicyError("links_required")
