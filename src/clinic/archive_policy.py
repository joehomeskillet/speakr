"""Connect workflow archive moves to retention and legal hold.

Status changes go through ``workflow.transition``. A recording the dry-run
finds eligible is archived through ``archive.archive_for_retention``.
Configured data-class days win. When they are unset, the days and the
exemption come from ``retention``. Nothing in this module deletes a row
or a file.

``delete`` becomes ``report_archive`` while clinic mode is on. With clinic
mode off it stays ``report_delete``, so the two requests stay distinct.
Archive requires its own action-scoped grant in the central recording policy.

With an application context the book is durable: entries and request results
live in ``clinic_archive_*`` tables and writes join the caller's transaction
(savepoint, no commit). Primary keys resolve concurrent duplicates. There is
no delete path. Without an application context the book keeps its state in
process memory, as before.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import has_app_context
from sqlalchemy import event, update
from sqlalchemy.exc import IntegrityError

from src.clinic import archive, policy, release, revisions, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicContextRevision, ClinicDictation
from src.database import db
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
    """Use the same central decision for command submission and the review page."""
    if action not in (ARCHIVE, DELETE):
        return policy.Decision(False, "unknown_action")
    decision = policy.decide(subject, action, facts)
    if action == ARCHIVE and decision.allowed:
        return policy.Decision(True, "grant_archive", visible=True)
    return decision


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


class ClinicArchiveEntry(db.Model):  # type: ignore[name-defined]
    """Links, retention facts and version of one registered dictation."""

    __tablename__ = "clinic_archive_entry"

    dictation_id = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    original_id = db.Column(db.String(64), nullable=False)
    audio_revision_id = db.Column(db.String(64), nullable=False)
    transcript_revision_id = db.Column(db.String(64), nullable=False)
    context_revision_id = db.Column(db.String(64), nullable=False)
    release_id = db.Column(db.String(64))
    data_class = db.Column(db.String(64), nullable=False)
    finished_at = db.Column(db.DateTime(timezone=True))
    retention_days = db.Column(db.Integer)
    legal_hold = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    archived_at = db.Column(db.DateTime(timezone=True))


class ClinicArchiveResult(db.Model):  # type: ignore[name-defined]
    """Stored success per request id; the retry answer. Append-only."""

    __tablename__ = "clinic_archive_result"

    request_id = db.Column(db.String(128), primary_key=True)
    dictation_id = db.Column(db.String(64), nullable=False)
    payload = db.Column(db.Text, nullable=False)


def _no_delete(_mapper, _connection, _row) -> None:
    raise ArchivePolicyError("append_only")


def _no_update(_mapper, _connection, _row) -> None:
    raise ArchivePolicyError("append_only")


event.listen(ClinicArchiveEntry, "before_delete", _no_delete)
event.listen(ClinicArchiveResult, "before_delete", _no_delete)
event.listen(ClinicArchiveResult, "before_update", _no_update)


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _encode(result: ApplyResult) -> str:
    def links(item: Links) -> dict:
        return dict(vars(item))

    dry = dict(vars(result.dry_run))
    dry["links"] = links(result.dry_run.links)
    body = dict(vars(result))
    body["links"] = links(result.links)
    body["dry_run"] = dry
    return json.dumps(body)


def _decode(text: str) -> ApplyResult:
    body = json.loads(text)
    dry = body.pop("dry_run")
    dry["links"] = Links(**dry["links"])
    body["links"] = Links(**body["links"])
    return ApplyResult(dry_run=DryRun(**dry), **body)


class _MemoryStore:
    """Process-local state for use without an application context."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._done: dict[str, ApplyResult] = {}

    def get_row(self, dictation_id):
        return self._rows.get(dictation_id)

    def insert_row(self, dictation_id, row) -> bool:
        if dictation_id in self._rows:
            return False
        self._rows[dictation_id] = row
        return True

    def bump(self, dictation_id, expected: int, archived_at) -> bool:
        row = self._rows[dictation_id]
        if row["version"] != expected:
            return False
        row["version"] += 1
        row["archived_at"] = archived_at
        return True

    def get_done(self, request_id):
        return self._done.get(request_id)

    def put_done(self, result: ApplyResult) -> None:
        self._done[result.request_id] = result

    def archived(self):
        return [(ident, row) for ident, row in self._rows.items() if row["archived_at"] is not None]


class _DbStore:
    """Rows in the application database, written through the caller's session."""

    def __init__(self, session) -> None:
        self._session = session

    def get_row(self, dictation_id):
        r = self._session.get(ClinicArchiveEntry, dictation_id)
        return None if r is None else _row_dict(r)

    def insert_row(self, dictation_id, row) -> bool:
        links = row["links"]
        entry = ClinicArchiveEntry(
            dictation_id=dictation_id, user_id=row["user_id"], original_id=links.original_id,
            audio_revision_id=links.audio_revision_id,
            transcript_revision_id=links.transcript_revision_id,
            context_revision_id=links.context_revision_id, release_id=links.release_id,
            data_class=row["data_class"], finished_at=row["finished_at"],
            retention_days=row["retention_days"], legal_hold=row["legal_hold"],
            version=row["version"], archived_at=row["archived_at"])
        try:
            with self._session.begin_nested():
                self._session.add(entry)
        except IntegrityError:
            return False
        return True

    def bump(self, dictation_id, expected: int, archived_at) -> bool:
        # Guarded by the version column, so two workers cannot both win.
        done = self._session.execute(
            update(ClinicArchiveEntry)
            .where(ClinicArchiveEntry.dictation_id == dictation_id,
                   ClinicArchiveEntry.version == expected)
            .values(version=expected + 1, archived_at=archived_at)
            .execution_options(synchronize_session="fetch"))
        return done.rowcount == 1

    def get_done(self, request_id):
        r = self._session.get(ClinicArchiveResult, request_id)
        return None if r is None else _decode(r.payload)

    def put_done(self, result: ApplyResult) -> None:
        try:
            with self._session.begin_nested():
                self._session.add(ClinicArchiveResult(
                    request_id=result.request_id, dictation_id=result.dictation_id,
                    payload=_encode(result)))
        except IntegrityError:
            pass

    def archived(self):
        rows = self._session.query(ClinicArchiveEntry).filter(
            ClinicArchiveEntry.archived_at.isnot(None)).all()
        return [(r.dictation_id, _row_dict(r)) for r in rows]


def _row_dict(r: ClinicArchiveEntry) -> dict:
    return {
        "user_id": r.user_id,
        "links": Links(r.original_id, r.audio_revision_id, r.transcript_revision_id,
                       r.context_revision_id, r.release_id),
        "data_class": r.data_class,
        "finished_at": _utc(r.finished_at),
        "retention_days": r.retention_days,
        "legal_hold": bool(r.legal_hold),
        "version": r.version,
        "archived_at": _utc(r.archived_at),
    }


def _current_entry(dictation):
    """Resolve immutable revision links from the dictation, without creating a book entry."""
    head = revisions.current_revision(dictation)
    audio_id = release.current_audio_id(dictation.id)
    contexts = release._heads(ClinicContextRevision, dictation.id)
    if head is None or not audio_id or len(contexts) != 1:
        return None
    active = release.active_release(dictation)
    links = Links(head.original_version_id, audio_id, head.id, next(iter(contexts)), active.id if active else None)
    return dict(links=links, data_class="audio", user_id=dictation.author_user_id, legal_hold=False,
                retention_days=None, finished_at=_utc(active.created_at) if active else None,
                version=dictation.version_counter, archived_at=None)


def archive_available(dictation):
    """Existing entries retain their recorded links. New entries need an unambiguous source."""
    return db.session.get(ClinicArchiveEntry, dictation.id) is not None or _current_entry(dictation) is not None


class ArchiveBook:
    """Versions, links and idempotent results for workflow archive requests."""

    def __init__(self):
        self._memory = _MemoryStore()

    def _store(self, session=None):
        return _DbStore(session or db.session) if has_app_context() else self._memory

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
        if self._store().get_row(dictation_id) is not None:
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
        row = {
            "user_id": user_id,
            "links": links,
            "data_class": data_class,
            "finished_at": finished_at,
            "retention_days": retention_days,
            "legal_hold": bool(legal_hold),
            "version": 1,
            "archived_at": None,
        }
        if not self._store().insert_row(dictation_id, row):
            raise ArchivePolicyError("duplicate")
        return 1

    def dry_run(self, dictation_id: str, now: datetime, recording=None, store=None) -> DryRun:
        return self._evaluate_row(self._require(dictation_id, store), now, recording)

    @staticmethod
    def _evaluate_row(row, now, recording):
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
        dictation=None,
    ) -> ApplyResult:
        """Confirm moves the workflow. ``confirm=False`` aborts and writes nothing.

        A repeated ``request_id`` returns the stored success and does not
        transition again. Failures are not stored, so a later call can retry.
        """
        if not isinstance(request_id, str) or not request_id:
            raise ArchivePolicyError("request_id")
        if not isinstance(confirm, bool):
            raise ArchivePolicyError("invalid_confirm")
        store = self._store(session)
        prior = store.get_done(request_id)
        if prior is not None:
            if prior.dictation_id != dictation_id:
                raise ArchivePolicyError("request_id")
            return prior
        row = store.get_row(dictation_id)
        first_archive = row is None
        if first_archive:
            if action != ARCHIVE or dictation is None or dictation.id != dictation_id:
                raise ArchivePolicyError("not_found")
            row = _current_entry(dictation)
            if row is None:
                raise ArchivePolicyError("links_required")
        if row["version"] != expected_version:
            raise StaleVersion(row["version"])
        decision = authorize(subject, facts, action)
        if not decision.allowed:
            raise ArchivePolicyError("unauthorized", decision.reason)
        item = session.get(workflow.ClinicWorkflowDictation, dictation_id)
        if item is None:
            raise ArchivePolicyError("not_found")
        dry = self._evaluate_row(row, now, recording)
        if not confirm:
            return self._result(dictation_id, action, None, item.status, row, dry, False, request_id, False)
        if recording is not None and expected_archive_revision is not None:
            current = int(recording.archive_revision or 0)
            if current != expected_archive_revision:
                raise StaleVersion(current)
        return self._apply_confirmed(
            session, store, item, row, dry, action=action, now=now, request_id=request_id,
            first_archive=first_archive, recording=recording,
        )

    def apply_retention(self, session, recording, *, now: datetime, retention_days: int) -> ApplyResult:
        """Project the scheduled retention decision under the caller's locks."""
        dictation = session.query(ClinicDictation).filter_by(recording_id=recording.id).one()
        item = session.get(workflow.ClinicWorkflowDictation, dictation.id)
        if item is None or item.status != workflow.DONE:
            raise ArchivePolicyError("transition_failed")
        current = _current_entry(dictation)
        if current is None:
            raise ArchivePolicyError("links_required")
        store = self._store(session)
        prior = store.get_row(dictation.id)
        row = prior if prior is not None else current
        # The sweep uses the current release and its effective tag/global period.
        now = _utc(now)
        dry = self._evaluate_row(dict(row, finished_at=current["finished_at"],
                                      retention_days=retention_days), now, None)
        if not dry.eligible:
            raise ArchivePolicyError("retention_ineligible", dry.reason)
        result = self._apply_confirmed(
            session, store, item, row, dry, action=ARCHIVE, now=now,
            request_id=f"retention:{dictation.id}:{recording.archive_revision + 1}",
            first_archive=prior is None, retention_changed=True,
        )
        dictation.version_counter += 1
        return result

    def _apply_confirmed(self, session, store, item, row, dry, *, action, now, request_id,
                         first_archive, recording=None, retention_changed=False):
        dictation_id, expected_version = item.id, row["version"]
        trigger = trigger_for(action)
        if workflow.TRANSITIONS.get((item.status, trigger)) is None:
            raise ArchivePolicyError("transition_failed")
        if first_archive and not store.insert_row(dictation_id, row):
            raise StaleVersion(self._require(dictation_id, store)["version"])
        if recording is not None and dry.eligible:
            retention_changed = bool(archive.archive_for_retention(
                recording,
                now=_aware(now).replace(tzinfo=None),
                retention_days=dry.retention_days,
            ))
        try:
            workflow.transition(session, dictation_id, trigger)
        except workflow.WorkflowTransitionError as exc:
            raise ArchivePolicyError("transition_failed") from exc
        if not store.bump(dictation_id, expected_version, _aware(now)):
            raise StaleVersion(self._require(dictation_id, store)["version"])
        row = self._require(dictation_id, store)
        result = self._result(
            dictation_id, action, trigger, item.status, row, dry, True, request_id, retention_changed,
        )
        store.put_done(result)
        return result

    def active_groups(self, session, *, role: str, user_id: int, pool_id: str) -> list[workflow.WorkGroup]:
        """Work list without the archive and delete groups."""
        hidden = {workflow.TO_ARCHIVE, workflow.TO_DELETE}
        groups = workflow.worklist(session, role=role, user_id=user_id, pool_id=pool_id)
        return [group for group in groups if group.status not in hidden]

    def archive_index(self) -> dict[str, tuple[str, ...]]:
        """Archived dictation ids by ``user-id/YYYY-MM``."""
        buckets: dict[str, list[str]] = {}
        for ident, row in self._store().archived():
            when = row["archived_at"]
            buckets.setdefault(archive_slot(row["user_id"], when), []).append(ident)
        return {key: tuple(sorted(ids)) for key, ids in sorted(buckets.items())}

    def _require(self, dictation_id: str, store=None) -> dict:
        row = (store or self._store()).get_row(dictation_id)
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
