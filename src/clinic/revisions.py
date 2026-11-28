"""Append-only transcript revisions for one clinic dictation.

Every change inserts a row. Existing rows are not updated or deleted.
The head is the latest human correction when one exists, otherwise the
latest ASR row. A later ASR result is stored as a side revision: it stays
readable, and it does not become the head.

Each row stores ``created_at`` (UTC) and, for human rows,
``revision_author_user_id``. Rows from before these nullable columns keep NULL.
Order is still the parent chain. ``author_user_id`` is checked against
``user.id``. The dictating person (``dictated_by``) is never copied here.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import event, inspect
from sqlalchemy import text as sql_text

from src.clinic.models import (
    ClinicDictation,
    ClinicOriginal,
    ClinicTranscriptRevision,
)
from src.database import db

KIND_ASR = "ASR"
KIND_HUMAN = "HUMAN"


class RevisionError(ValueError):
    """The revision was rejected before it changed the session."""


class RevisionImmutable(RevisionError):
    """An existing transcript revision cannot be updated or deleted."""


def normalise_text(text: str) -> str:
    """Unicode NFC, then CR and CRLF to LF. Nothing else changes."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    normalised = unicodedata.normalize("NFC", text)
    return normalised.replace("\r\n", "\n").replace("\r", "\n")


def text_hash(text: str) -> str:
    """sha256 hex digest of the normalised text as UTF-8."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def add_asr_revision(
    dictation: ClinicDictation,
    original_version: ClinicOriginal,
    text: str,
    model_ref: str,
    *,
    parent_revision: ClinicTranscriptRevision | None = None,
) -> ClinicTranscriptRevision:
    """Append an ASR row, or return the current tip when the text is unchanged.

    ``body`` stores the normalised text. The caller's raw ASR string is
    recovered from that body; a human correction is a different row.
    """
    _check_dictation(dictation)
    _check_original(dictation, original_version)
    if not isinstance(model_ref, str) or model_ref.strip() == "":
        raise RevisionError("ASR revision requires model_ref")
    return _append(
        dictation,
        kind=KIND_ASR,
        text=text,
        explicit_parent=parent_revision,
        original_version=original_version,
        model_ref=model_ref,
    )


def add_human_revision(
    dictation: ClinicDictation,
    parent_revision: ClinicTranscriptRevision | None,
    text: str,
    author_user_id: int,
    *,
    original_version: ClinicOriginal | None = None,
    force_new: bool = False,
) -> ClinicTranscriptRevision:
    """Append a human correction, or return the tip when the text is unchanged.

    A new row is flushed, so its ID is available to other writes in the same
    session. The caller owns the commit or rollback. Bundle imports use
    ``force_new`` because audio and formatting can differ with identical text.

    ``author_user_id`` must be an existing user. It is stored as
    ``revision_author_user_id``, separate from the dictating person.
    """
    _check_dictation(dictation)
    _require_author(author_user_id)
    return _append(
        dictation,
        kind=KIND_HUMAN,
        text=text,
        explicit_parent=parent_revision,
        original_version=original_version,
        model_ref=None,
        author_user_id=author_user_id,
        force_new=force_new,
    )


def append_working_revision(
    dictation: ClinicDictation, head: ClinicTranscriptRevision, *, actor_user_id: int,
) -> ClinicTranscriptRevision:
    """Start a distinct working revision from the locked head, preserving its text kind."""
    _check_dictation(dictation)
    _check_revision(dictation, head)
    _require_author(actor_user_id)
    return _append(
        dictation,
        kind=head.kind,
        text=head.body,
        explicit_parent=head,
        original_version=None,
        model_ref=head.model_ref,
        author_user_id=actor_user_id if head.kind == KIND_HUMAN else None,
        reuse_unchanged=False,
    )


def current_revision(dictation: ClinicDictation) -> ClinicTranscriptRevision | None:
    """The head: latest human correction, or the latest ASR row if no human exists."""
    _check_dictation(dictation)
    return _select_head(_load(dictation.id))


def history(dictation: ClinicDictation) -> list[ClinicTranscriptRevision]:
    """Revisions in chain order, parent before child. Human siblings come first."""
    _check_dictation(dictation)
    rows = _load(dictation.id)
    by_id, children = _index(rows)
    ordered = []
    seen = set()

    def visit(node):
        if node.id in seen:
            raise RevisionError("transcript revision parent cycle")
        seen.add(node.id)
        ordered.append(node)
        kids = list(children.get(node.id, []))
        kids.sort(key=lambda row: (row.kind != KIND_HUMAN, _depth(row, by_id), row.id))
        for kid in kids:
            visit(kid)

    roots = list(children.get(None, []))
    roots.sort(key=lambda row: (row.kind != KIND_HUMAN, row.id))
    for root in roots:
        visit(root)
    if len(ordered) != len(rows):
        raise RevisionError("transcript revision parent is missing")
    return ordered


def is_flagged(dictation: ClinicDictation, revision: ClinicTranscriptRevision) -> bool:
    """True when an ASR row is off the head's parent chain (a side revision)."""
    _check_dictation(dictation)
    _check_revision(dictation, revision)
    if revision.kind != KIND_ASR:
        return False
    rows = _load(dictation.id)
    head = _select_head(rows)
    if head is None:
        return False
    by_id, _children = _index(rows)
    return revision.id not in _lineage_ids(head, by_id)


def _append(dictation, *, kind, text, explicit_parent, original_version, model_ref,
            author_user_id=None, reuse_unchanged=True, force_new=False):
    digest = text_hash(text)
    normalised = normalise_text(text)
    if reuse_unchanged:
        rows = _load(dictation.id)
        existing, parent = _link(dictation, explicit_parent, rows, kind, digest)
        if existing is not None:
            if not force_new:
                return existing
            parent = existing
    else:
        parent = explicit_parent
    if kind == KIND_HUMAN:
        original_id = _human_original_id(parent, original_version, dictation)
        stored_model = None
    else:
        original_id = original_version.id if original_version is not None else parent.original_version_id
        stored_model = model_ref
    row = ClinicTranscriptRevision(
        dictation_id=dictation.id,
        parent_revision_id=None if parent is None else parent.id,
        kind=kind,
        body=normalised,
        normalised_text_hash=digest,
        original_version_id=original_id,
        model_ref=stored_model,
        revision_author_user_id=author_user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(row)
    db.session.flush()
    return row


def revision_author(revision: ClinicTranscriptRevision) -> dict | None:
    """``{id, display_name}`` of the revision author, or None for legacy rows."""
    from src.models import User

    author_id = revision.revision_author_user_id
    if author_id is None:
        return None
    user = db.session.get(User, author_id)
    name = (user.name or user.username) if user is not None else None
    return {"id": author_id, "display_name": name or "unbekannt"}


_NEW_COLUMNS = (
    ("revision_author_user_id", "INTEGER"),
    ("created_at", "DATETIME"),
)


def create_tables(bind=None) -> None:
    """Additive upgrade: add the two nullable columns to an existing table.

    Called by ``registry.create_clinic_tables``. Safe to repeat. Existing rows
    keep NULL.
    """
    from src.utils.database import add_column_if_not_exists

    engine = bind or db.engine
    if "clinic_transcript_revision" not in inspect(engine).get_table_names():
        return
    for name, sql_type in _NEW_COLUMNS:
        add_column_if_not_exists(engine, "clinic_transcript_revision", name, f"{name} {sql_type}")


def downgrade_columns(bind=None) -> None:
    """Reverse of ``create_tables``: drop the two columns (data in them is lost)."""
    engine = bind or db.engine
    present = {c["name"] for c in inspect(engine).get_columns("clinic_transcript_revision")}
    with engine.begin() as conn:
        for name, _type in reversed(_NEW_COLUMNS):
            if name in present:
                conn.execute(sql_text(f"ALTER TABLE clinic_transcript_revision DROP COLUMN {name}"))


def _human_original_id(parent, original_version, dictation):
    if parent is None:
        _check_original(dictation, original_version)
        return original_version.id
    original_id = parent.original_version_id
    if original_version is not None:
        _check_original(dictation, original_version)
        if original_version.id != original_id:
            raise RevisionError("original version does not match the parent revision")
    return original_id


def _link(dictation, explicit_parent, rows, kind, digest):
    """Return ``(existing_tip, None)`` or ``(None, parent_for_insert)``.

    The insert parent is the same-kind tip at or below the anchor. Extending
    that tip keeps one chain, which is the only order the table can express.
    """
    if explicit_parent is not None:
        _check_revision(dictation, explicit_parent)
        anchor = explicit_parent
    else:
        anchor = _select_head(rows)
    if anchor is None:
        return None, None
    tip = _same_kind_tip(anchor, rows, kind)
    if tip.normalised_text_hash == digest:
        return tip, None
    return None, tip


def _same_kind_tip(anchor, rows, kind):
    by_id, children = _index(rows)
    node = anchor
    seen = set()
    while node.id not in seen:
        seen.add(node.id)
        nxt = [child for child in children.get(node.id, []) if child.kind == kind]
        if not nxt:
            return node
        nxt.sort(key=lambda row: (_depth(row, by_id), row.id))
        node = nxt[-1]
    raise RevisionError("transcript revision parent cycle")


def _select_head(rows):
    """Human leaves win over any ASR leaf, including one appended later."""
    if not rows:
        return None
    by_id, _children = _index(rows)
    humans = [row for row in rows if row.kind == KIND_HUMAN]
    pool = humans if humans else list(rows)
    referenced = {row.parent_revision_id for row in pool}
    leaves = [row for row in pool if row.id not in referenced]
    if not leaves:
        raise RevisionError("transcript revision parent cycle")
    leaves.sort(key=lambda row: (_depth(row, by_id), row.id))
    return leaves[-1]


def _lineage_ids(head, by_id):
    ids = set()
    current = head
    while current is not None and current.id not in ids:
        ids.add(current.id)
        parent_id = current.parent_revision_id
        current = by_id.get(parent_id) if parent_id else None
    return ids


def _depth(row, by_id):
    seen = set()
    depth = 0
    current = row
    while current.parent_revision_id is not None:
        if current.id in seen:
            raise RevisionError("transcript revision parent cycle")
        seen.add(current.id)
        parent = by_id.get(current.parent_revision_id)
        if parent is None:
            break
        current = parent
        depth += 1
    return depth


def _index(rows):
    by_id = {}
    children = {}
    for row in rows:
        by_id[row.id] = row
        children.setdefault(row.parent_revision_id, []).append(row)
    return by_id, children


def _load(dictation_id):
    return (
        db.session.query(ClinicTranscriptRevision)
        .filter(ClinicTranscriptRevision.dictation_id == dictation_id)
        .all()
    )


def _check_dictation(dictation):
    if not isinstance(dictation, ClinicDictation) or dictation.id is None:
        raise RevisionError("dictation must be a persisted ClinicDictation")


def _check_original(dictation, original):
    if not isinstance(original, ClinicOriginal) or original.id is None:
        raise RevisionError("original version must be a persisted ClinicOriginal")
    if original.dictation_id != dictation.id:
        raise RevisionError("original version belongs to another dictation")


def _check_revision(dictation, revision):
    if not isinstance(revision, ClinicTranscriptRevision) or revision.id is None:
        raise RevisionError("revision must be a persisted ClinicTranscriptRevision")
    if revision.dictation_id != dictation.id:
        raise RevisionError("revision belongs to another dictation")


def _require_author(author_user_id):
    from src.models import User

    if isinstance(author_user_id, bool) or not isinstance(author_user_id, int):
        raise RevisionError("author_user_id must be an int")
    if db.session.get(User, author_user_id) is None:
        raise RevisionError("author user does not exist")


def _reject_transcript_write(_mapper, _connection, _target):
    # Early, readable error. The database triggers from install_clinic_guards
    # are the real guard and also stop bulk update() and raw SQL.
    raise RevisionImmutable("transcript revisions are append-only")


event.listen(ClinicTranscriptRevision, "before_update", _reject_transcript_write)
event.listen(ClinicTranscriptRevision, "before_delete", _reject_transcript_write)
