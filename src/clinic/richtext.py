"""Versioned clinic rich text, using the legacy renderer's document primitives."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from sqlalchemy import event, select

from src.clinic import lease, policy, revisions
from src.clinic.config import clinic_mode_enabled
from src.clinic.correction import effective_links
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.rich_text_doc import Document, Node, RichTextError, Run, to_plain_text
from src.database import db
from src.models import Recording, User

SCHEMA_VERSION = 1
MAX_NODES = 2048
MAX_SEGMENTS = 8192
MAX_TEXT = 1_000_000
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MARKUP = re.compile(r"<(?:/?[a-zA-Z][^<>]*>|[!?])")


def _check_id(value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RichTextError("invalid_id")


@dataclass(frozen=True, kw_only=True)
class Segment(Run):
    """A formatting run with a stable ID and an optional ClinicTextAudioLink ID."""

    id: str
    audio_anchor_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or len(self.text) > MAX_TEXT:
            raise RichTextError("invalid_segment_text")
        super().__post_init__()
        _check_id(self.id)
        if self.audio_anchor_id is not None:
            _check_id(self.audio_anchor_id)
        if any(type(flag) is not bool for flag in (self.bold, self.italic, self.underline)):
            raise RichTextError("invalid_format")
        if _MARKUP.search(self.text) or re.search(r"[\ud800-\udfff]", self.text):
            raise RichTextError("invalid_segment_text")


@dataclass(frozen=True)
class CanonicalDocument(Document):
    """Schema v1: nodes contain identified runs; raw markup and link fields are absent."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if len(self.nodes) > MAX_NODES or sum(len(node.runs) for node in self.nodes) > MAX_SEGMENTS:
            raise RichTextError("document_too_large")
        seen = {node.id for node in self.nodes}
        chars = max(0, len(self.nodes) - 1)
        for node in self.nodes:
            _check_id(node.id)
            if type(node.level) is not int:
                raise RichTextError("invalid_level")
            for segment in node.runs:
                if not isinstance(segment, Segment):
                    raise RichTextError("invalid_segment")
                if segment.id in seen:
                    raise RichTextError("duplicate_id")
                seen.add(segment.id)
                chars += len(segment.text)
        if chars > MAX_TEXT:
            raise RichTextError("document_too_large")
        if _MARKUP.search(to_plain_text(self)):
            raise RichTextError("invalid_segment_text")

    def to_payload(self) -> dict:
        """Return the complete schema, including explicit null anchors."""
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, payload: dict) -> CanonicalDocument:
        """Validate a decoded JSON object. Unknown fields, including href, are rejected."""
        try:
            version = payload["schema_version"]
            nodes = payload["nodes"]
            if set(payload) != {"schema_version", "nodes"} or type(version) is not int:
                raise RichTextError("invalid_schema")
            if version != SCHEMA_VERSION:
                raise RichTextError("unsupported_schema")
            if not isinstance(nodes, (list, tuple)) or len(nodes) > MAX_NODES:
                raise RichTextError("invalid_nodes")
            parsed = []
            segments = chars = 0
            for node in nodes:
                runs = node["runs"]
                if not isinstance(runs, (list, tuple)):
                    raise RichTextError("invalid_segment")
                segments += len(runs)
                if segments > MAX_SEGMENTS:
                    raise RichTextError("document_too_large")
                validated = []
                for run in runs:
                    segment = Segment(**run)
                    chars += len(segment.text)
                    if chars > MAX_TEXT:
                        raise RichTextError("document_too_large")
                    validated.append(segment)
                parsed.append(Node(**{**node, "runs": tuple(validated)}))
            return cls(tuple(parsed))
        except (KeyError, TypeError) as exc:
            raise RichTextError("invalid_schema") from exc

    def edit_text(
        self, node_id: str, index: int, text: str, *, insert_id: str | None = None,
    ) -> CanonicalDocument:
        """Insert an unaligned run or replace one run's text without realigning it."""
        for node in self.nodes:
            if node.id == node_id:
                limit = len(node.runs) - (insert_id is None)
                if type(index) is not int or not 0 <= index <= limit:
                    raise RichTextError("invalid_index")
                if insert_id is None:
                    run = node.runs[index]
                    if run.text == text:
                        return self
                    replacement = replace(run, text=text, audio_anchor_id=None)
                    runs = node.runs[:index] + (replacement,) + node.runs[index + 1:]
                else:
                    runs = node.runs[:index] + (Segment(id=insert_id, text=text),) + node.runs[index:]
                return CanonicalDocument(tuple(
                    replace(n, runs=runs) if n is node else n for n in self.nodes
                ))
        raise RichTextError("unknown_node")

    def project(self) -> tuple[str, dict[str, tuple[int, int]]]:
        """Return raw Unicode text and half-open code-point spans for the offset mapper."""
        spans = {}
        offset = 0
        for node in self.nodes:
            for run in node.runs:
                end = offset + len(run.text)
                spans[run.id] = (offset, end)
                offset = end
            offset += 1  # Paragraph separator in to_plain_text.
        return to_plain_text(self), spans


class ClinicRichTextSnapshot(db.Model):
    """Append-only document versions, independently pinned to a transcript revision."""

    __tablename__ = "clinic_rich_text_snapshot"

    revision_id = db.Column(db.String(36), db.ForeignKey(ClinicTranscriptRevision.id), primary_key=True)
    version = db.Column(db.Integer, primary_key=True)
    payload = db.Column(db.JSON, nullable=False)


@event.listens_for(ClinicRichTextSnapshot, "before_update")
@event.listens_for(ClinicRichTextSnapshot, "before_delete")
def _reject_snapshot_mutation(*_args) -> None:
    raise RichTextError("immutable_snapshot")


def create_tables(bind=None) -> None:
    """Register this module after the transcript models in clinic startup."""
    ClinicRichTextSnapshot.__table__.create(bind if bind is not None else db.engine, checkfirst=True)


def load_document(revision_id: str, version: int) -> CanonicalDocument | None:
    """Read an exact snapshot for an already authorized revision; legacy rows return None."""
    row = db.session.get(ClinicRichTextSnapshot, (revision_id, version))
    return None if row is None else CanonicalDocument.from_payload(row.payload)


def save_document(
    dictation: ClinicDictation, revision: ClinicTranscriptRevision, document: CanonicalDocument,
    *, expected_version: int, actor_user_id: int, lease_generation: int, now: datetime,
) -> int:
    """Save against the current revision under its edit lease. The caller commits."""
    if not clinic_mode_enabled():
        raise RichTextError("clinic_mode_disabled")
    actor = db.session.get(User, actor_user_id) if type(actor_user_id) is int else None
    recording = db.session.get(Recording, dictation.recording_id)
    if actor is None or recording is None or not policy.decide(
        policy.build_subject(actor.id, bool(actor.is_admin)), "edit",
        policy.build_facts(recording.id, recording.user_id),
    ).allowed:
        raise RichTextError("forbidden")
    lease.assert_current(dictation.id, actor.id, lease_generation, now)
    head = revisions.current_revision(dictation)
    if head is None or revision.id != head.id:
        raise RichTextError("stale_revision")
    if type(expected_version) is not int or expected_version < 0:
        raise RichTextError("invalid_version")
    if not isinstance(document, CanonicalDocument):
        raise RichTextError("invalid_schema")
    if revisions.normalise_text(to_plain_text(document)) != head.body:
        raise RichTextError("transcript_mismatch")

    latest = _latest_snapshot(head.id)
    version = latest.version if latest else 0
    previous = CanonicalDocument.from_payload(latest.payload) if latest else None
    if expected_version in (version, version - 1) and document == previous:
        return version
    if expected_version != version:
        raise RichTextError("stale_version")

    _check_audio_anchors(dictation, head, document, previous)
    db.session.add(ClinicRichTextSnapshot(
        revision_id=head.id, version=version + 1, payload=document.to_payload(),
    ))
    db.session.flush()
    return version + 1


def _latest_snapshot(revision_id: str) -> ClinicRichTextSnapshot | None:
    query = select(ClinicRichTextSnapshot).filter_by(revision_id=revision_id)
    return db.session.scalar(query.order_by(ClinicRichTextSnapshot.version.desc()).limit(1))


def _check_audio_anchors(
    dictation: ClinicDictation, revision: ClinicTranscriptRevision,
    document: CanonicalDocument, previous: CanonicalDocument | None,
) -> None:
    anchored = [run for node in document.nodes for run in node.runs if run.audio_anchor_id]
    if not anchored:
        return
    if previous is None and revision.parent_revision_id:
        parent = db.session.get(ClinicTranscriptRevision, revision.parent_revision_id)
        if parent and parent.original_version_id == revision.original_version_id:
            snapshot = _latest_snapshot(parent.id)
            if snapshot:
                previous = CanonicalDocument.from_payload(snapshot.payload)
    if previous is not None:
        prior = {(run.id, run.audio_anchor_id, run.text) for node in previous.nodes for run in node.runs}
        if any((run.id, run.audio_anchor_id, run.text) not in prior for run in anchored):
            raise RichTextError("invalid_audio_anchor")
        return

    # Initial alignment may only use links generated for this exact revision.
    links = {link.id: link for link in effective_links(dictation, revision)}
    text, spans = document.project()
    seen = set()
    parts = []
    cursor = offset = 0
    for run in anchored:
        link = links.get(run.audio_anchor_id)
        if link is None or link.id in seen:
            raise RichTextError("invalid_audio_anchor")
        start, end = spans[run.id]
        gap = revisions.normalise_text(text[cursor:start])
        aligned = revisions.normalise_text(run.text)
        offset += len(gap)
        if (offset, offset + len(aligned)) != (link.text_start, link.text_end):
            raise RichTextError("invalid_audio_anchor")
        parts.extend((gap, aligned))
        offset += len(aligned)
        cursor = end
        seen.add(link.id)
    parts.append(revisions.normalise_text(text[cursor:]))
    if "".join(parts) != revision.body:
        raise RichTextError("invalid_audio_anchor")
