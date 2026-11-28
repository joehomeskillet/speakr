"""Read-only all-user dictation metadata for the admin overview.

The union is recordings, workflow rows and unassigned import deliveries.
Those tables do not share a recording id, so a missing value stays null.
Transcripts, notes, summaries, embeddings, audio paths and storage keys are
not selected. ``list_load_options`` loads ``audio_path`` and
``list_unassigned`` returns storage keys, so neither is called. Duration and
format come from stored columns only; a missing value is not probed.
Order is created time, then id. Paging is LIMIT/OFFSET. The caller authorizes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Float, Integer, String, cast, false, func, literal, select, union_all
from sqlalchemy.orm import Session

from src.clinic.import_centre import DELIVERY_STATES, ClinicImportDelivery, ClinicImportOriginal
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.recording_type import is_private_source
from src.clinic.workflow import (
    CLINIC_ZONE, PRIORITY_CODES, STATUS_ORDER, ClinicWorkflowDictation, WorkflowError, resolve_deadline,
)
from src.models import Recording

_ZONE = ZoneInfo(CLINIC_ZONE)
_UNASSIGNED = "UNASSIGNED"
_MAX_PAGE = 10_000
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 50
_MAX_NAME = 200
_DOCUMENT_EXT = frozenset({"doc", "docx", "odt", "rtf", "txt", "pdf"})
METADATA_ALLOWLIST = (
    "id", "kind", "recording_id", "name", "author_user_id", "typist_user_id", "pool_id",
    "status", "priority", "deadline_at", "created_at", "updated_at", "duration_seconds",
    "size_bytes", "original_format",
)
# Content and storage keys a caller must not send. They are not filters.
FORBIDDEN_FIELDS = frozenset({
    "transcription", "transcript", "notes", "summary", "summaries", "embedding", "embeddings",
    "speaker_embeddings", "audio", "audio_path", "storage_key", "storage_root", "storage_path",
    "relative_path", "content_url", "content", "body",
})
_FILTER_KEYS = frozenset({
    "name", "author_user_id", "typist_user_id", "pool_id", "status", "priority",
    "deadline_on", "created_on", "updated_on", "page", "page_size",
})
_LABELS = (
    "id", "kind", "recording_id", "name", "author_user_id", "typist_user_id", "pool_id",
    "status", "priority", "deadline_at", "created_at", "updated_at", "duration_seconds",
    "size_bytes", "stored_mime", "source_name", "sort_stamp",
)
_NONE_INT = literal(None, type_=Integer())
_NONE_STR = literal(None, type_=String())
_NONE_FLOAT = literal(None, type_=Float())

if _UNASSIGNED not in DELIVERY_STATES:
    raise RuntimeError("import delivery states no longer include UNASSIGNED")


class AdminQueryError(ValueError):
    """The query was rejected. ``code`` is a stable token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AdminDictationsQuery:
    """One page of the all-user metadata list. Dates are civil dates."""

    name: str | None = None
    author_user_id: int | None = None
    typist_user_id: int | None = None
    pool_id: str | None = None
    status: str | None = None
    priority: int | None = None
    deadline_on: date | None = None
    created_on: date | None = None
    updated_on: date | None = None
    page: int = 1
    page_size: int = _DEFAULT_PAGE_SIZE


@dataclass(frozen=True)
class AdminDictationRow:
    """One metadata row. Unavailable values are None."""

    id: str
    kind: str
    recording_id: int | None
    name: str | None
    author_user_id: int | None
    typist_user_id: int | None
    pool_id: str | None
    status: str | None
    priority: int | None
    deadline_at: str | None
    created_at: str | None
    updated_at: str | None
    duration_seconds: float | int | None
    size_bytes: int | None
    original_format: str | None

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in METADATA_ALLOWLIST}


@dataclass(frozen=True)
class AdminDictationsPage:
    """``total`` is the filtered count, not the page length."""

    items: tuple[AdminDictationRow, ...]
    total: int
    page: int
    page_size: int

    def as_dict(self) -> dict:
        return {
            "items": [row.as_dict() for row in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
        }


def parse_query(raw: Mapping) -> AdminDictationsQuery:
    """Build a query from a mapping. Content keys are rejected."""
    if not isinstance(raw, Mapping):
        raise AdminQueryError("invalid_filter")
    keys = set(raw)
    if keys & FORBIDDEN_FIELDS:
        raise AdminQueryError("forbidden_field")
    if not keys <= _FILTER_KEYS:
        raise AdminQueryError("invalid_filter")
    return _validate(AdminDictationsQuery(
        name=raw.get("name"),
        author_user_id=raw.get("author_user_id"),
        typist_user_id=raw.get("typist_user_id"),
        pool_id=raw.get("pool_id"),
        status=raw.get("status"),
        priority=raw.get("priority"),
        deadline_on=raw.get("deadline_on"),
        created_on=raw.get("created_on"),
        updated_on=raw.get("updated_on"),
        page=raw.get("page", 1),
        page_size=raw.get("page_size", _DEFAULT_PAGE_SIZE),
    ))


def query_dictations(session: Session, spec: AdminDictationsQuery | Mapping) -> AdminDictationsPage:
    """Return one metadata page. Reads only; nothing is written."""
    query = _validate(spec) if isinstance(spec, AdminDictationsQuery) else parse_query(spec)
    dialect = session.get_bind().dialect.name
    listed = union_all(
        _recording_select(query, dialect),
        _workflow_select(query),
        _unassigned_select(query),
    ).subquery("admin_dictation")
    total = session.execute(select(func.count()).select_from(listed)).scalar_one()
    offset = (query.page - 1) * query.page_size
    rows = session.execute(
        select(*listed.c).order_by(listed.c.sort_stamp.asc(), listed.c.id.asc())
        .limit(query.page_size).offset(offset)
    ).all()
    return AdminDictationsPage(
        items=tuple(_item(row) for row in rows),
        total=int(total),
        page=query.page,
        page_size=query.page_size,
    )


def _validate(query: AdminDictationsQuery) -> AdminDictationsQuery:
    name = query.name
    if name is not None:
        if not isinstance(name, str) or "\x00" in name or len(name.strip()) > _MAX_NAME:
            raise AdminQueryError("invalid_filter")
        name = name.strip() or None
    deadline = _civil_date(query.deadline_on)
    if deadline is not None:
        try:
            resolve_deadline(deadline, None)
        except WorkflowError:
            raise AdminQueryError("invalid_filter") from None
    return AdminDictationsQuery(
        name=name,
        author_user_id=_user_id(query.author_user_id),
        typist_user_id=_user_id(query.typist_user_id),
        pool_id=_pool_id(query.pool_id),
        status=_status(query.status),
        priority=_priority(query.priority),
        deadline_on=deadline,
        created_on=_civil_date(query.created_on),
        updated_on=_civil_date(query.updated_on),
        page=_bounded(query.page, 1, _MAX_PAGE),
        page_size=_bounded(query.page_size, 1, _MAX_PAGE_SIZE),
    )


def _recording_select(query: AdminDictationsQuery, dialect: str):
    from src.clinic.web_ingest import source_recording_link

    if dialect == "sqlite":
        public_id = func.printf("recording:%010d", Recording.id)
    else:
        public_id = literal("recording:").op("||")(func.lpad(cast(Recording.id, String()), 10, "0"))
    stmt = select(*_labeled((
        public_id, literal("recording"), Recording.id, Recording.title, Recording.user_id, _NONE_INT,
        ClinicDictation.pool_id, _NONE_STR, _NONE_INT, _NONE_STR,
        cast(Recording.created_at, String()), cast(Recording.updated_at, String()),
        Recording.audio_duration_seconds, Recording.file_size, Recording.mime_type,
        Recording.original_filename, _stamp(Recording.created_at),
    ))).select_from(Recording).outerjoin(
        ClinicDictation, ClinicDictation.recording_id == Recording.id,
    ).where(ClinicDictation.is_report)
    private_sources = (select(ClinicOriginal.id).join(ClinicDictation)
                       .where(~ClinicDictation.is_report, source_recording_link(Recording.id))
                       .correlate(Recording).exists())
    stmt = stmt.where(~private_sources, is_private_source(Recording).is_not(True))
    if query.name is not None:
        stmt = stmt.where(Recording.title.ilike(_like(query.name), escape="\\"))
    if query.author_user_id is not None:
        stmt = stmt.where(Recording.user_id == query.author_user_id)
    if query.pool_id is not None:
        stmt = stmt.where(ClinicDictation.pool_id == query.pool_id)
    if query.created_on is not None:
        stmt = _between(stmt, Recording.created_at, query.created_on, naive=True)
    if query.updated_on is not None:
        stmt = _between(stmt, Recording.updated_at, query.updated_on, naive=True)
    workflow_only = (
        query.typist_user_id is not None or query.status is not None
        or query.priority is not None or query.deadline_on is not None
    )
    if workflow_only:
        stmt = stmt.where(false())
    return stmt


def _workflow_select(query: AdminDictationsQuery):
    public_id = literal("workflow:").op("||")(ClinicWorkflowDictation.id)
    stmt = select(*_labeled((
        public_id, literal("workflow"), _NONE_INT, _NONE_STR, ClinicWorkflowDictation.author_user_id,
        ClinicWorkflowDictation.assignee_user_id, ClinicWorkflowDictation.pool_id,
        ClinicWorkflowDictation.status, ClinicWorkflowDictation.priority,
        cast(ClinicWorkflowDictation.deadline_at, String()),
        cast(ClinicWorkflowDictation.created_at, String()), _NONE_STR,
        cast(ClinicWorkflowDictation.audio_seconds, Float()), _NONE_INT, _NONE_STR, _NONE_STR,
        _stamp(ClinicWorkflowDictation.created_at),
    ))).select_from(ClinicWorkflowDictation).outerjoin(
        ClinicDictation, ClinicDictation.id == ClinicWorkflowDictation.id,
    ).where(ClinicDictation.is_report)
    if query.name is not None or query.updated_on is not None:
        stmt = stmt.where(false())
    if query.author_user_id is not None:
        stmt = stmt.where(ClinicWorkflowDictation.author_user_id == query.author_user_id)
    if query.typist_user_id is not None:
        stmt = stmt.where(ClinicWorkflowDictation.assignee_user_id == query.typist_user_id)
    if query.pool_id is not None:
        stmt = stmt.where(ClinicWorkflowDictation.pool_id == query.pool_id)
    if query.status is not None:
        stmt = stmt.where(ClinicWorkflowDictation.status == query.status)
    if query.priority is not None:
        stmt = stmt.where(ClinicWorkflowDictation.priority == query.priority)
    if query.deadline_on is not None:
        stmt = stmt.where(ClinicWorkflowDictation.deadline_date == query.deadline_on)
    if query.created_on is not None:
        stmt = _between(stmt, ClinicWorkflowDictation.created_at, query.created_on, naive=False)
    return stmt


def _unassigned_select(query: AdminDictationsQuery):
    blocked = (
        query.name is not None or query.author_user_id is not None or query.typist_user_id is not None
        or query.pool_id is not None or query.status is not None or query.priority is not None
        or query.deadline_on is not None or query.updated_on is not None
    )
    stmt = select(*_labeled((
        literal("unassigned:").op("||")(ClinicImportDelivery.id), literal("unassigned"),
        _NONE_INT, _NONE_STR, _NONE_INT, _NONE_INT, _NONE_STR, _NONE_STR, _NONE_INT, _NONE_STR,
        cast(ClinicImportDelivery.created_at, String()), _NONE_STR, _NONE_FLOAT,
        ClinicImportOriginal.size_bytes, _NONE_STR, _NONE_STR, _stamp(ClinicImportDelivery.created_at),
    ))).select_from(ClinicImportDelivery).outerjoin(
        ClinicImportOriginal, ClinicImportOriginal.delivery_id == ClinicImportDelivery.id,
    ).where(ClinicImportDelivery.status == _UNASSIGNED)
    if blocked:
        stmt = stmt.where(false())
    if query.created_on is not None:
        stmt = _between(stmt, ClinicImportDelivery.created_at, query.created_on, naive=False)
    return stmt


def _labeled(values):
    if len(values) != len(_LABELS):
        raise RuntimeError("projection width")
    return [value.label(name) for value, name in zip(values, _LABELS)]


def _stamp(column):
    text = cast(column, String())
    return func.coalesce(func.substr(func.replace(text, "T", " "), 1, 19), "")


def _between(stmt, column, on: date, *, naive: bool):
    start = datetime.combine(on, time.min, tzinfo=_ZONE).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    if naive:
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    return stmt.where(column >= start, column < end)


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def _item(row) -> AdminDictationRow:
    kind, original_format = row.kind, _text(row.stored_mime)
    if kind == "recording":
        kind, original_format = _classify(row.stored_mime, row.source_name)
    return AdminDictationRow(
        id=str(row.id),
        kind=kind,
        recording_id=_whole(row.recording_id),
        name=_text(row.name),
        author_user_id=_whole(row.author_user_id),
        typist_user_id=_whole(row.typist_user_id),
        pool_id=_text(row.pool_id),
        status=_text(row.status),
        priority=_whole(row.priority),
        deadline_at=_text(row.deadline_at),
        created_at=_text(row.created_at),
        updated_at=_text(row.updated_at),
        duration_seconds=_number(row.duration_seconds),
        size_bytes=_whole(row.size_bytes),
        original_format=original_format,
    )


def _classify(mime, filename) -> tuple[str, str | None]:
    stored = _text(mime)
    if stored is not None:
        base = stored.split(";", 1)[0].strip().lower()
        if base.startswith("audio/") or base.startswith("video/"):
            return "legacy", stored
        return "document", stored
    ext = _extension(filename)
    return ("document", ext) if ext in _DOCUMENT_EXT else ("legacy", ext)


def _extension(filename) -> str | None:
    name = _text(filename)
    if name is None or "." not in name or "/" in name or "\\" in name:
        return None
    ext = name.rsplit(".", 1)[1].lower()
    if not ext.isalnum() or len(ext) > 16:
        return None
    return ext


def _text(value):
    if value is None or not isinstance(value, str):
        return value
    return value.strip() or None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def _whole(value):
    number = _number(value)
    return None if number is None else int(number)


def _bounded(value, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AdminQueryError("invalid_pagination")
    return value


def _user_id(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AdminQueryError("invalid_filter")
    return value


def _pool_id(value):
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 36:
        raise AdminQueryError("invalid_filter")
    return value


def _status(value):
    if value is not None and value not in STATUS_ORDER:
        raise AdminQueryError("invalid_filter")
    return value


def _priority(value):
    if value is not None and (isinstance(value, bool) or value not in PRIORITY_CODES):
        raise AdminQueryError("invalid_filter")
    return value


def _civil_date(value):
    if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
        raise AdminQueryError("invalid_filter")
    return value
