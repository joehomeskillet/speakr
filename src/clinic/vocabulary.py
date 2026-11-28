"""Clinic vocabulary, voice commands, pronunciation hints and review workflow.

Additive clinic module implementing PAR-VOC-001 through PAR-VOC-008.
Supports user- and pool-scoped vocabularies, voice commands, phonetic hints,
review queue ('Vokabularänderungen prüfen'), audit decisions and precedence rules.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import CheckConstraint, UniqueConstraint, or_
from sqlalchemy.orm import Session

from src.database import db

SCOPES = ("user", "pool")
STATUS_ACTIVE, STATUS_SUPPRESSED = "active", "suppressed"
COMMAND_STATUS_ACTIVE, COMMAND_STATUS_DISABLED = "active", "disabled"
ORIGIN_MANUAL, ORIGIN_CORRECTION, ORIGIN_PROFILE, ORIGIN_SESSION, ORIGIN_IMPORT = (
    "manual", "correction", "profile", "session", "import",
)

REVIEW_OPERATIONS = ("ADD", "DELETE", "MODIFY")
REVIEW_OP_ADD, REVIEW_OP_DELETE, REVIEW_OP_MODIFY = REVIEW_OPERATIONS

REVIEW_STATUSES = ("PENDING", "ACCEPTED_USER", "ACCEPTED_POOL", "REJECTED", "DEFERRED")
REVIEW_STATUS_PENDING, REVIEW_STATUS_ACCEPTED_USER, REVIEW_STATUS_ACCEPTED_POOL, REVIEW_STATUS_REJECTED, REVIEW_STATUS_DEFERRED = REVIEW_STATUSES
OPEN_REVIEW_STATUSES = (REVIEW_STATUS_PENDING, REVIEW_STATUS_DEFERRED)
ACTIONS = ("ACCEPT_USER", "ACCEPT_POOL", "REJECT", "DEFER", "UNDO")
ACTION_ACCEPT_USER, ACTION_ACCEPT_POOL, ACTION_REJECT, ACTION_DEFER, ACTION_UNDO = ACTIONS


class VocabularyError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class AwareDateTime(db.TypeDecorator):
    impl = db.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.astimezone(timezone.utc) if value is not None else None

    def process_result_value(self, value, dialect):
        return value if (value is None or value.tzinfo) else value.replace(tzinfo=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_session(session: Session | None = None) -> Session:
    return session if session is not None else db.session


def _ts(onupdate: bool = False):
    return db.Column(AwareDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow if onupdate else None)


# --- Models ---


class ClinicVocabularyEntry(db.Model):
    __tablename__ = "clinic_vocabulary_entry"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    scope = db.Column(db.String(8), nullable=False)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    pool_id = db.Column(db.String(36), nullable=True, index=True)
    written_form = db.Column(db.String(255), nullable=False, index=True)
    spoken_form = db.Column(db.String(255), nullable=True)
    origin = db.Column(db.String(32), nullable=False, default=ORIGIN_MANUAL)
    status = db.Column(db.String(16), nullable=False, default=STATUS_ACTIVE)
    notes = db.Column(db.Text, nullable=True)
    created_at, updated_at = _ts(), _ts(onupdate=True)
    __table_args__ = (
        CheckConstraint("scope IN ('user', 'pool')", name="ck_vocab_entry_scope"),
        CheckConstraint("(scope = 'user' AND user_id IS NOT NULL AND pool_id IS NULL) OR (scope = 'pool' AND pool_id IS NOT NULL AND user_id IS NULL)", name="ck_vocab_entry_scope_ref"),
        CheckConstraint("status IN ('active', 'suppressed')", name="ck_vocab_entry_status"),
        UniqueConstraint("user_id", "written_form", name="uq_vocab_entry_user_word"),
        UniqueConstraint("pool_id", "written_form", name="uq_vocab_entry_pool_word"),
    )


class ClinicVoiceCommand(db.Model):
    __tablename__ = "clinic_voice_command"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    scope = db.Column(db.String(8), nullable=False)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    pool_id = db.Column(db.String(36), nullable=True, index=True)
    trigger_phrase = db.Column(db.String(255), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    action_type = db.Column(db.String(32), nullable=False, default="insert_text")
    action_payload = db.Column(db.Text, nullable=True)
    before_hook = db.Column(db.String(64), nullable=True)
    after_hook = db.Column(db.String(64), nullable=True)
    origin = db.Column(db.String(32), nullable=False, default=ORIGIN_MANUAL)
    status = db.Column(db.String(16), nullable=False, default=COMMAND_STATUS_ACTIVE)
    created_at, updated_at = _ts(), _ts(onupdate=True)
    __table_args__ = (
        CheckConstraint("scope IN ('user', 'pool')", name="ck_cmd_scope"),
        CheckConstraint("(scope = 'user' AND user_id IS NOT NULL AND pool_id IS NULL) OR (scope = 'pool' AND pool_id IS NOT NULL AND user_id IS NULL)", name="ck_cmd_scope_ref"),
        CheckConstraint("status IN ('active', 'suppressed', 'disabled')", name="ck_cmd_status"),
        UniqueConstraint("user_id", "trigger_phrase", name="uq_cmd_user_trigger"),
        UniqueConstraint("pool_id", "trigger_phrase", name="uq_cmd_pool_trigger"),
    )


class ClinicPronunciationHint(db.Model):
    __tablename__ = "clinic_pronunciation_hint"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    scope = db.Column(db.String(8), nullable=False)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    pool_id = db.Column(db.String(36), nullable=True, index=True)
    written_form = db.Column(db.String(255), nullable=False, index=True)
    phonetic_hint = db.Column(db.String(255), nullable=False)
    sounds_like = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    origin = db.Column(db.String(32), nullable=False, default=ORIGIN_MANUAL)
    status = db.Column(db.String(16), nullable=False, default=STATUS_ACTIVE)
    created_at, updated_at = _ts(), _ts(onupdate=True)
    __table_args__ = (
        CheckConstraint("scope IN ('user', 'pool')", name="ck_pron_scope"),
        CheckConstraint("(scope = 'user' AND user_id IS NOT NULL AND pool_id IS NULL) OR (scope = 'pool' AND pool_id IS NOT NULL AND user_id IS NULL)", name="ck_pron_scope_ref"),
        CheckConstraint("status IN ('active', 'suppressed')", name="ck_pron_status"),
        UniqueConstraint("user_id", "written_form", name="uq_pron_user_word"),
        UniqueConstraint("pool_id", "written_form", name="uq_pron_pool_word"),
    )


class ClinicVocabularyReviewItem(db.Model):
    __tablename__ = "clinic_vocabulary_review_item"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    pool_id = db.Column(db.String(36), nullable=True, index=True)
    dictation_id = db.Column(db.String(36), nullable=True)
    written_form = db.Column(db.String(255), nullable=False)
    spoken_form = db.Column(db.String(255), nullable=True)
    information = db.Column(db.String(500), nullable=True)
    operation = db.Column(db.String(16), nullable=False, default=REVIEW_OP_ADD)
    origin = db.Column(db.String(32), nullable=False, default=ORIGIN_CORRECTION)
    status = db.Column(db.String(16), nullable=False, default=REVIEW_STATUS_PENDING)
    target_pool_id = db.Column(db.String(36), nullable=True)
    target_entry_id = db.Column(db.String(36), nullable=True)
    created_at, updated_at = _ts(), _ts(onupdate=True)
    __table_args__ = (
        CheckConstraint("operation IN ('ADD', 'DELETE', 'MODIFY')", name="ck_review_operation"),
        CheckConstraint("origin IN ('correction', 'manual', 'profile', 'session')", name="ck_review_origin"),
        CheckConstraint("status IN ('PENDING', 'ACCEPTED_USER', 'ACCEPTED_POOL', 'REJECTED', 'DEFERRED')", name="ck_review_status"),
    )


class ClinicVocabularyReviewDecision(db.Model):
    __tablename__ = "clinic_vocabulary_review_decision"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    review_item_id = db.Column(db.String(36), db.ForeignKey("clinic_vocabulary_review_item.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(16), nullable=False)
    previous_status = db.Column(db.String(16), nullable=False)
    new_status = db.Column(db.String(16), nullable=False)
    details = db.Column(db.Text, nullable=True)
    decided_at = db.Column(AwareDateTime(), nullable=False, default=_utcnow)


VOCABULARY_TABLES = (
    ClinicVocabularyEntry,
    ClinicVoiceCommand,
    ClinicPronunciationHint,
    ClinicVocabularyReviewItem,
    ClinicVocabularyReviewDecision,
)


def create_tables(bind=None) -> None:
    db.metadata.create_all(bind=bind or db.engine, tables=[m.__table__ for m in VOCABULARY_TABLES], checkfirst=True)


# --- Helpers & CRUD ---


def _scope_filter(q, model, scope: str, user_id: int | None, pool_id: str | None):
    if scope not in SCOPES:
        raise VocabularyError("invalid_scope", f"Scope must be in {SCOPES}.")
    if scope == "user":
        if user_id is None:
            raise VocabularyError("missing_user_id", "user_id required for user scope.")
        return q.filter(model.scope == "user", model.user_id == user_id)
    if not pool_id:
        raise VocabularyError("missing_pool_id", "pool_id required for pool scope.")
    return q.filter(model.scope == "pool", model.pool_id == pool_id)


def add_vocabulary_entry(
    scope: str, written_form: str, spoken_form: str | None = None,
    user_id: int | None = None, pool_id: str | None = None,
    origin: str = ORIGIN_MANUAL, status: str = STATUS_ACTIVE,
    notes: str | None = None, session: Session | None = None,
) -> ClinicVocabularyEntry:
    s = _get_session(session)
    written = written_form.strip()
    if not written:
        raise VocabularyError("empty_written_form", "Written form must not be empty.")
    q = _scope_filter(s.query(ClinicVocabularyEntry), ClinicVocabularyEntry, scope, user_id, pool_id)
    entry = q.filter_by(written_form=written).first()
    if entry:
        entry.spoken_form, entry.status, entry.origin, entry.notes, entry.updated_at = (
            spoken_form or entry.spoken_form, status, origin, notes or entry.notes, _utcnow(),
        )
    else:
        entry = ClinicVocabularyEntry(
            scope=scope, user_id=user_id if scope == "user" else None,
            pool_id=pool_id if scope == "pool" else None, written_form=written,
            spoken_form=spoken_form, origin=origin, status=status, notes=notes,
        )
        s.add(entry)
    s.flush()
    return entry


def _update_entry_status(entry_id: str, status: str, session: Session | None = None) -> ClinicVocabularyEntry:
    s = _get_session(session)
    entry = s.get(ClinicVocabularyEntry, entry_id)
    if entry is None:
        raise VocabularyError("entry_not_found", f"Entry {entry_id} not found.")
    entry.status, entry.updated_at = status, _utcnow()
    s.flush()
    return entry


def suppress_vocabulary_entry(entry_id: str, session: Session | None = None) -> ClinicVocabularyEntry:
    return _update_entry_status(entry_id, STATUS_SUPPRESSED, session)


def reactivate_vocabulary_entry(entry_id: str, session: Session | None = None) -> ClinicVocabularyEntry:
    return _update_entry_status(entry_id, STATUS_ACTIVE, session)


def delete_vocabulary_entry(entry_id: str, hard: bool = False, session: Session | None = None) -> None:
    s = _get_session(session)
    entry = s.get(ClinicVocabularyEntry, entry_id)
    if entry is None:
        raise VocabularyError("entry_not_found", f"Entry {entry_id} not found.")
    s.delete(entry) if hard else _update_entry_status(entry_id, STATUS_SUPPRESSED, s)
    s.flush()


def apply_vocabulary_bulk(
    items: list[dict[str, str]], action: str, session: Session | None = None,
) -> list[ClinicVocabularyEntry]:
    """Suppress or hard-delete every named entry, or none of them.

    Each item is ``{"id", "expected_version"}``. Versions are the entry's
    ``updated_at`` in ISO format, the same value the HTTP layer exposes. A
    missing entry, a repeated id or one stale version raises and the caller
    rolls the session back, so a partial list never lands. Permission stays
    with the caller: pass only entries already checked.
    """
    if action not in ("suppress", "delete"):
        raise VocabularyError("invalid_action", "action must be suppress or delete.")
    if not items:
        raise VocabularyError("empty_bulk", "At least one entry is required.")
    s = _get_session(session)
    seen: set[str] = set()
    chosen: list[ClinicVocabularyEntry] = []
    for item in items:
        entry_id, expected = item["id"], item["expected_version"]
        if entry_id in seen:
            raise VocabularyError("duplicate_entry", f"Entry {entry_id} is listed twice.")
        seen.add(entry_id)
        entry = s.get(ClinicVocabularyEntry, entry_id)
        if entry is None:
            raise VocabularyError("entry_not_found", f"Entry {entry_id} not found.")
        if entry.updated_at.isoformat() != expected:
            raise VocabularyError("stale_version", f"Entry {entry_id} changed.")
        chosen.append(entry)
    if action == "delete":
        for entry in chosen:
            s.delete(entry)
        s.flush()
        return []
    now = _utcnow()
    for entry in chosen:
        entry.status, entry.updated_at = STATUS_SUPPRESSED, now
    s.flush()
    return chosen


SEARCH_FIELDS = ("written", "spoken", "all")
_SEARCH_COLUMNS = {
    "written": (ClinicVocabularyEntry.written_form,),
    "spoken": (ClinicVocabularyEntry.spoken_form,),
    "all": (ClinicVocabularyEntry.written_form, ClinicVocabularyEntry.spoken_form),
}


def _like_pattern(query: str, mode: str) -> str:
    """Literal LIKE pattern. '%' and '_' in the query stay literal (backslash escape)."""
    term = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {"prefix": f"{term}%", "suffix": f"%{term}", "exact": term}.get(mode, f"%{term}%")


def search_vocabulary(
    query: str, mode: str = "contains", scope: str | None = None,
    user_id: int | None = None, pool_id: str | None = None,
    status: str | None = STATUS_ACTIVE, field: str = "written",
    session: Session | None = None,
) -> list[ClinicVocabularyEntry]:
    """Search one scope. ``field`` is written, spoken or all (both forms, OR).

    The default stays the written form, so existing callers keep their results.
    """
    if field not in SEARCH_FIELDS:
        raise VocabularyError("invalid_field", f"field must be one of {SEARCH_FIELDS}.")
    s = _get_session(session)
    q = _scope_filter(s.query(ClinicVocabularyEntry), ClinicVocabularyEntry, scope, user_id, pool_id) if scope else s.query(ClinicVocabularyEntry)
    if status is not None:
        q = q.filter_by(status=status)
    pat = _like_pattern(query, mode)
    columns = _SEARCH_COLUMNS[field]
    match = db.or_(*(column.ilike(pat, escape="\\") for column in columns))
    return q.filter(match).order_by(ClinicVocabularyEntry.written_form.asc()).all()


def add_voice_command(
    scope: str, trigger_phrase: str, action_type: str = "insert_text",
    action_payload: str | None = None, user_id: int | None = None,
    pool_id: str | None = None, description: str | None = None,
    before_hook: str | None = None, after_hook: str | None = None,
    origin: str = ORIGIN_MANUAL, status: str = COMMAND_STATUS_ACTIVE,
    session: Session | None = None,
) -> ClinicVoiceCommand:
    s = _get_session(session)
    trigger = trigger_phrase.strip()
    if not trigger:
        raise VocabularyError("empty_trigger", "Trigger phrase must not be empty.")
    q = _scope_filter(s.query(ClinicVoiceCommand), ClinicVoiceCommand, scope, user_id, pool_id)
    cmd = q.filter_by(trigger_phrase=trigger).first()
    if cmd:
        cmd.action_type, cmd.action_payload, cmd.status, cmd.updated_at = action_type, action_payload, status, _utcnow()
        cmd.description, cmd.before_hook, cmd.after_hook = description or cmd.description, before_hook, after_hook
    else:
        cmd = ClinicVoiceCommand(
            scope=scope, user_id=user_id if scope == "user" else None,
            pool_id=pool_id if scope == "pool" else None, trigger_phrase=trigger,
            description=description, action_type=action_type, action_payload=action_payload,
            before_hook=before_hook, after_hook=after_hook, origin=origin, status=status,
        )
        s.add(cmd)
    s.flush()
    return cmd


def add_pronunciation_hint(
    scope: str, written_form: str, phonetic_hint: str,
    sounds_like: str | None = None, user_id: int | None = None,
    pool_id: str | None = None, notes: str | None = None,
    origin: str = ORIGIN_MANUAL, status: str = STATUS_ACTIVE,
    session: Session | None = None,
) -> ClinicPronunciationHint:
    s = _get_session(session)
    written, phonetic = written_form.strip(), phonetic_hint.strip()
    if not written or not phonetic:
        raise VocabularyError("empty_field", "Written form and phonetic hint required.")
    q = _scope_filter(s.query(ClinicPronunciationHint), ClinicPronunciationHint, scope, user_id, pool_id)
    hint = q.filter_by(written_form=written).first()
    if hint:
        hint.phonetic_hint, hint.sounds_like, hint.notes, hint.status, hint.updated_at = (
            phonetic, sounds_like, notes or hint.notes, status, _utcnow(),
        )
    else:
        hint = ClinicPronunciationHint(
            scope=scope, user_id=user_id if scope == "user" else None,
            pool_id=pool_id if scope == "pool" else None, written_form=written,
            phonetic_hint=phonetic, sounds_like=sounds_like, notes=notes, origin=origin, status=status,
        )
        s.add(hint)
    s.flush()
    return hint


# --- Review-Flow «Vokabularänderungen prüfen» ---


def submit_review_item(
    user_id: int, written_form: str, spoken_form: str | None = None,
    operation: str = REVIEW_OP_ADD, origin: str = ORIGIN_CORRECTION,
    information: str | None = None, pool_id: str | None = None,
    dictation_id: str | None = None, session: Session | None = None,
) -> ClinicVocabularyReviewItem:
    s = _get_session(session)
    written = written_form.strip()
    if not written:
        raise VocabularyError("empty_written_form", "Written form must not be empty.")
    if operation not in REVIEW_OPERATIONS:
        raise VocabularyError("invalid_operation", f"Operation must be in {REVIEW_OPERATIONS}.")
    if origin not in ("correction", "manual", "profile", "session"):
        raise VocabularyError("invalid_origin", "Invalid review origin.")
    item = ClinicVocabularyReviewItem(
        user_id=user_id, pool_id=pool_id, dictation_id=dictation_id,
        written_form=written, spoken_form=spoken_form.strip() if spoken_form else None,
        information=information, operation=operation, origin=origin, status=REVIEW_STATUS_PENDING,
    )
    s.add(item)
    s.flush()
    return item


def _session_information(resource_type: str, source_id: str) -> str:
    return json.dumps(
        {_SESSION_RESOURCE: resource_type, _SESSION_SOURCE: source_id},
        separators=(",", ":"),
    )


def _session_information_data(item: ClinicVocabularyReviewItem) -> dict[str, Any]:
    try:
        data = json.loads(item.information or "")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get(_SESSION_RESOURCE) in (
        "vocabulary", "voice_command",
    ) else {}


def capture_session_changes(
    user_id: int, dictation_id: str, started_at: datetime, ended_at: datetime,
    pool_id: str | None = None, before_text: str = "", after_text: str = "",
    session: Session | None = None,
) -> list[ClinicVocabularyReviewItem]:
    s = _get_session(session)
    started_at = (started_at.replace(tzinfo=timezone.utc) if started_at.tzinfo is None
                  else started_at.astimezone(timezone.utc))
    ended_at = (ended_at.replace(tzinfo=timezone.utc) if ended_at.tzinfo is None
                else ended_at.astimezone(timezone.utc))

    def scope_filter(model):
        clauses = [(model.scope == "user") & (model.user_id == user_id)]
        if pool_id:
            clauses.append((model.scope == "pool") & (model.pool_id == pool_id))
        return or_(*clauses)

    def changed(model):
        return s.query(model).filter(
            scope_filter(model), model.updated_at >= started_at, model.updated_at <= ended_at,
        ).all()

    open_items = list_review_items(
        user_id=user_id, status=OPEN_REVIEW_STATUSES, session=s,
    )
    open_items = [item for item in open_items if item.dictation_id == dictation_id]
    offered = {
        (
            _session_information_data(item).get(_SESSION_RESOURCE, "vocabulary"),
            item.written_form.casefold(), item.operation,
        ): item
        for item in open_items
    }
    created = []

    def offer(resource_type, written, spoken, operation, information=None, target_pool_id=None):
        if len(written) > _MAX_FORM:
            return  # Dictation text can contain tokens too long for a vocabulary entry.
        key = resource_type, written.casefold(), operation
        if key in offered:
            return
        item = submit_review_item(
            user_id=user_id, written_form=written, spoken_form=spoken,
            operation=operation, origin=ORIGIN_SESSION, information=information,
            pool_id=target_pool_id or pool_id, dictation_id=dictation_id, session=s,
        )
        offered[key] = item
        created.append(item)

    for row in changed(ClinicVocabularyEntry):
        operation = REVIEW_OP_DELETE if row.status == STATUS_SUPPRESSED else (
            REVIEW_OP_ADD if row.created_at >= started_at else REVIEW_OP_MODIFY
        )
        offer(
            "vocabulary", row.written_form, row.spoken_form, operation,
            _session_information("vocabulary", row.id), row.pool_id,
        )
    for row in changed(ClinicVoiceCommand):
        operation = REVIEW_OP_DELETE if row.status != COMMAND_STATUS_ACTIVE else (
            REVIEW_OP_ADD if row.created_at >= started_at else REVIEW_OP_MODIFY
        )
        offer(
            "voice_command", row.trigger_phrase, row.description, operation,
            _session_information("voice_command", row.id), row.pool_id,
        )

    before = _WORD.findall(before_text)
    after = _WORD.findall(after_text)
    known = {
        row.written_form.casefold()
        for row in s.query(ClinicVocabularyEntry).filter(scope_filter(ClinicVocabularyEntry)).all()
    }
    for tag, _i1, _i2, j1, j2 in SequenceMatcher(
        a=before, b=after, autojunk=False,
    ).get_opcodes():
        if tag not in ("insert", "replace"):
            continue
        for written in after[j1:j2]:
            if written.casefold() not in known:
                offer("vocabulary", written, None, REVIEW_OP_ADD)
    return created


def list_review_items(
    user_id: int | None = None, pool_id: str | None = None,
    status: str | tuple[str, ...] | None = REVIEW_STATUS_PENDING, session: Session | None = None,
) -> list[ClinicVocabularyReviewItem]:
    s = _get_session(session)
    q = s.query(ClinicVocabularyReviewItem)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    if pool_id is not None:
        q = q.filter_by(pool_id=pool_id)
    if isinstance(status, str):
        q = q.filter_by(status=status)
    elif status is not None:
        q = q.filter(ClinicVocabularyReviewItem.status.in_(status))
    return q.order_by(ClinicVocabularyReviewItem.created_at.desc()).all()


def _set_review_status(
    s: Session, item: ClinicVocabularyReviewItem, actor_id: int, action: str, new_status: str,
    details: dict | None = None,
) -> ClinicVocabularyReviewItem:
    old_status = item.status
    item.status, item.updated_at = new_status, _utcnow()
    dec = ClinicVocabularyReviewDecision(
        review_item_id=item.id, actor_id=actor_id, action=action,
        previous_status=old_status, new_status=new_status,
        details=json.dumps(details) if details else None,
    )
    s.add(dec)
    s.flush()
    return item


def _accept_common(item: ClinicVocabularyReviewItem, actor_id: int, scope: str, target_id: Any, action: str, status: str, s: Session) -> ClinicVocabularyReviewItem:
    if item.status not in OPEN_REVIEW_STATUSES:
        raise VocabularyError("invalid_item_status", f"Cannot accept item in status {item.status}.")
    kw = {"user_id": target_id} if scope == "user" else {"pool_id": target_id}
    session_data = _session_information_data(item)
    if session_data.get(_SESSION_RESOURCE) == "voice_command":
        source = s.get(ClinicVoiceCommand, session_data.get(_SESSION_SOURCE))
        if source is None:
            raise VocabularyError("entry_not_found", "Source voice command not found.")
        entry = add_voice_command(
            scope=scope, trigger_phrase=item.written_form,
            action_type=source.action_type, action_payload=source.action_payload,
            description=source.description, before_hook=source.before_hook,
            after_hook=source.after_hook, origin=item.origin,
            status=COMMAND_STATUS_DISABLED if item.operation == REVIEW_OP_DELETE else COMMAND_STATUS_ACTIVE,
            session=s, **kw,
        )
    else:
        entry = add_vocabulary_entry(
            scope=scope, written_form=item.written_form, spoken_form=item.spoken_form,
            origin=item.origin,
            status=STATUS_SUPPRESSED if item.operation == REVIEW_OP_DELETE else STATUS_ACTIVE,
            notes=None if session_data else item.information, session=s, **kw,
        )
    item.target_entry_id = entry.id
    if scope == "pool":
        item.target_pool_id = target_id
    detail = {"target": scope, "entry_id": entry.id}
    if scope == "pool":
        detail["pool_id"] = target_id
    return _set_review_status(s, item, actor_id, action, status, detail)


def accept_to_user(item_id: str, actor_id: int, session: Session | None = None) -> ClinicVocabularyReviewItem:
    s = _get_session(session)
    item = s.get(ClinicVocabularyReviewItem, item_id)
    if not item:
        raise VocabularyError("item_not_found", f"Review item {item_id} not found.")
    return _accept_common(item, actor_id, "user", item.user_id, ACTION_ACCEPT_USER, REVIEW_STATUS_ACCEPTED_USER, s)


def accept_to_pool(
    item_id: str, actor_id: int, pool_id: str | None = None, session: Session | None = None,
) -> ClinicVocabularyReviewItem:
    s = _get_session(session)
    item = s.get(ClinicVocabularyReviewItem, item_id)
    if not item:
        raise VocabularyError("item_not_found", f"Review item {item_id} not found.")
    pid = pool_id or item.pool_id
    if not pid:
        raise VocabularyError("missing_pool_id", "A pool_id must be provided to accept to pool.")
    return _accept_common(item, actor_id, "pool", pid, ACTION_ACCEPT_POOL, REVIEW_STATUS_ACCEPTED_POOL, s)


def reject_item(item_id: str, actor_id: int, reason: str | None = None, session: Session | None = None) -> ClinicVocabularyReviewItem:
    s = _get_session(session)
    item = s.get(ClinicVocabularyReviewItem, item_id)
    if item is None:
        raise VocabularyError("item_not_found", f"Review item {item_id} not found.")
    if item.status not in (REVIEW_STATUS_PENDING, REVIEW_STATUS_DEFERRED):
        raise VocabularyError("invalid_item_status", f"Cannot reject item in status {item.status}.")
    return _set_review_status(s, item, actor_id, ACTION_REJECT, REVIEW_STATUS_REJECTED, {"reason": reason} if reason else None)


def defer_item(item_id: str, actor_id: int, notes: str | None = None, session: Session | None = None) -> ClinicVocabularyReviewItem:
    s = _get_session(session)
    item = s.get(ClinicVocabularyReviewItem, item_id)
    if item is None:
        raise VocabularyError("item_not_found", f"Review item {item_id} not found.")
    if item.status != REVIEW_STATUS_PENDING:
        raise VocabularyError("invalid_item_status", f"Cannot defer item in status {item.status}.")
    return _set_review_status(s, item, actor_id, ACTION_DEFER, REVIEW_STATUS_DEFERRED, {"notes": notes} if notes else None)


def undo_decision(item_id: str, actor_id: int, session: Session | None = None) -> ClinicVocabularyReviewItem:
    s = _get_session(session)
    item = s.get(ClinicVocabularyReviewItem, item_id)
    if item is None:
        raise VocabularyError("item_not_found", f"Review item {item_id} not found.")
    if item.status == REVIEW_STATUS_PENDING:
        raise VocabularyError("nothing_to_undo", "Item is already pending; nothing to undo.")

    old_status = item.status
    if item.target_entry_id:
        model = ClinicVoiceCommand if (
            _session_information_data(item).get(_SESSION_RESOURCE) == "voice_command"
        ) else ClinicVocabularyEntry
        entry = s.get(model, item.target_entry_id)
        if entry is not None:
            s.delete(entry)
        item.target_entry_id = item.target_pool_id = None

    item.status, item.updated_at = REVIEW_STATUS_PENDING, _utcnow()
    dec = ClinicVocabularyReviewDecision(
        review_item_id=item.id, actor_id=actor_id, action=ACTION_UNDO,
        previous_status=old_status, new_status=REVIEW_STATUS_PENDING,
        details=json.dumps({"reverted_from": old_status}),
    )
    s.add(dec)
    s.flush()
    return item


def get_decision_history(item_id: str, session: Session | None = None) -> list[ClinicVocabularyReviewDecision]:
    s = _get_session(session)
    return s.query(ClinicVocabularyReviewDecision).filter_by(review_item_id=item_id).order_by(ClinicVocabularyReviewDecision.decided_at.asc()).all()


# --- Effective Vocabulary ---


def _merge_scoped(
    s: Session, model: Any, user_id: int, pool_ids: list[str] | None, key_attr: str,
    status_filter=None,
) -> dict[str, tuple[Any, str]]:
    pools = pool_ids or []
    pool_map: dict[str, Any] = {}
    for pid in reversed(pools):
        for row in s.query(model).filter_by(scope="pool", pool_id=pid).all():
            if status_filter is None or status_filter(row):
                pool_map[getattr(row, key_attr).strip().lower()] = row
    user_map: dict[str, Any] = {}
    for row in s.query(model).filter_by(scope="user", user_id=user_id).all():
        if status_filter is None or status_filter(row):
            user_map[getattr(row, key_attr).strip().lower()] = row

    res = {}
    for k in sorted(set(pool_map) | set(user_map)):
        is_user = k in user_map
        res[k] = (user_map[k] if is_user else pool_map[k], "user" if is_user else "pool")
    return res


def get_effective_vocabulary(
    user_id: int, pool_ids: list[str] | None = None,
    include_suppressed: bool = False, session: Session | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute effective active vocabulary for a user across their author pools.

    Precedence rules (Vorrangregeln):
    1. Scope hierarchy: User > Pool. Personal entry takes precedence.
    2. Negative lists (Suppression):
       - If user status='suppressed', word is suppressed regardless of pool.
       - If pool status='suppressed', word is suppressed unless user has explicit active entry.
    3. Multi-pool determinism: earlier pool_ids take precedence over later pool_ids.
    4. Winning entry provides canonical spoken_form, notes, and effective origin.
    5. Case-insensitive key matching preserves the winning entry's canonical written casing.
    """
    s = _get_session(session)
    merged_rows = _merge_scoped(s, ClinicVocabularyEntry, user_id, pool_ids, "written_form")
    result: dict[str, dict[str, Any]] = {}
    for winning, source in merged_rows.values():
        if not include_suppressed and winning.status == STATUS_SUPPRESSED:
            continue
        result[winning.written_form] = {
            "entry_id": winning.id, "written_form": winning.written_form,
            "spoken_form": winning.spoken_form, "source": source,
            "status": winning.status, "origin": winning.origin, "notes": winning.notes,
        }
    return result


def get_effective_voice_commands(
    user_id: int, pool_ids: list[str] | None = None, session: Session | None = None,
) -> dict[str, dict[str, Any]]:
    s = _get_session(session)
    m = _merge_scoped(s, ClinicVoiceCommand, user_id, pool_ids, "trigger_phrase", lambda r: r.status == COMMAND_STATUS_ACTIVE)
    return {w.trigger_phrase: {"id": w.id, "trigger_phrase": w.trigger_phrase, "description": w.description,
                               "action_type": w.action_type, "action_payload": w.action_payload,
                               "before_hook": w.before_hook, "after_hook": w.after_hook, "source": src}
            for w, src in m.values()}


def get_effective_pronunciation_hints(
    user_id: int, pool_ids: list[str] | None = None, session: Session | None = None,
) -> dict[str, dict[str, Any]]:
    s = _get_session(session)
    m = _merge_scoped(s, ClinicPronunciationHint, user_id, pool_ids, "written_form", lambda r: r.status == STATUS_ACTIVE)
    return {w.written_form: {"id": w.id, "written_form": w.written_form, "phonetic_hint": w.phonetic_hint,
                             "sounds_like": w.sounds_like, "notes": w.notes, "source": src}
            for w, src in m.values()}


# --- Import / Export (PAR-VOC-002) ---
# UTF-8: "written" or "written<TAB>spoken". Edge spaces ignored; tab separates. Skip blanks
# and '#' lines. Sort is written_form, code point. Raw = active rows of one scope; effective
# = merged active list (user overrides pools). Suppressed rows are omitted. Duplicates match
# exact written_form: replace stamps origin import, sets active, and keeps a missing spoken
# form; skip keeps the row; error raises duplicate_entry. Empty written form or a form over
# 255 characters is listed in errors and the import continues.
_MAX_FORM = 255
_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_SESSION_RESOURCE = "session_resource"
_SESSION_SOURCE = "source_id"

def _vocab_line(written: str, spoken: str | None) -> str:
    return f"{written}\t{spoken}" if spoken else written

def export_vocabulary_text(scope: str, user_id: int | None = None, pool_id: str | None = None, session: Session | None = None) -> str:
    s = _get_session(session)
    q = _scope_filter(s.query(ClinicVocabularyEntry), ClinicVocabularyEntry, scope, user_id, pool_id)
    rows = q.filter_by(status=STATUS_ACTIVE).order_by(ClinicVocabularyEntry.written_form.asc()).all()
    return "\n".join(_vocab_line(row.written_form, row.spoken_form) for row in rows)

def export_effective_vocabulary_text(user_id: int, pool_ids: list[str] | None = None, session: Session | None = None) -> str:
    rows = sorted(get_effective_vocabulary(user_id, pool_ids, session=session).values(), key=lambda row: row["written_form"])
    return "\n".join(_vocab_line(row["written_form"], row["spoken_form"]) for row in rows)

def import_vocabulary_text(content: str, scope: str, user_id: int | None = None, pool_id: str | None = None, origin: str = ORIGIN_IMPORT, duplicate_rule: str = "replace", session: Session | None = None) -> dict[str, Any]:
    if duplicate_rule not in ("replace", "skip", "error"):
        raise VocabularyError("invalid_duplicate_rule", "duplicate_rule must be replace, skip or error.")
    if origin != ORIGIN_IMPORT:
        raise VocabularyError("invalid_origin", "Import must mark entries with origin 'import'.")
    s, added, updated, skipped = _get_session(session), 0, 0, 0
    errors: list[dict[str, Any]] = []
    for line_no, raw in enumerate(content.lstrip("\ufeff").splitlines(), start=1):
        line = raw.strip(" ")
        if not line or line.startswith("#"):
            continue
        written, _, spoken = (part.strip() for part in line.partition("\t"))
        if not written or len(written) > _MAX_FORM or len(spoken) > _MAX_FORM:
            errors.append({"line": line_no, "reason": "empty_written_form" if not written else "form_too_long"})
            continue
        spoken_form = spoken or None
        hit = _scope_filter(s.query(ClinicVocabularyEntry), ClinicVocabularyEntry, scope, user_id, pool_id).filter_by(written_form=written).first()
        if hit is None:
            add_vocabulary_entry(scope=scope, written_form=written, spoken_form=spoken_form, user_id=user_id, pool_id=pool_id, origin=ORIGIN_IMPORT, status=STATUS_ACTIVE, session=s)
            added += 1
            continue
        if duplicate_rule == "error":
            raise VocabularyError("duplicate_entry", f"Entry '{written}' already exists.")
        if duplicate_rule == "skip":
            skipped += 1
            continue
        hit.spoken_form = spoken_form if spoken_form is not None else hit.spoken_form
        hit.status, hit.origin, hit.updated_at = STATUS_ACTIVE, ORIGIN_IMPORT, _utcnow()
        updated += 1
    s.flush()
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors}
