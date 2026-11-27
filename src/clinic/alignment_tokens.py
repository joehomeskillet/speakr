"""Deterministic canonical token documents for clinic transcript revisions.

One document per transcript revision (contract 2.0 ``CanonicalDocument``).
Token and node ids carry over from the parent revision through an
order-preserving diff, so a later alignment can keep word times for words that
did not change. The reference document hash is the SHA-256 of the sorted-key
compact JSON of the document.

Non-scope: no routes, no UI, no job. ``derive_links`` data (W10 segment
ranges) never becomes a word timing here.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re

from sqlalchemy import ForeignKeyConstraint, event

from src.clinic import models as _models  # noqa: F401  (FK target table)
from src.clinic.revisions import normalise_text
from src.clinic.unicode_anchor_map import utf16_length
from src.database import db

TOKENIZATION_VERSION = "clinic-token-1"
DOCUMENT_SCHEMA_VERSION = "1.0"

_MAX_ID = 160
# Python \s plus U+FEFF, so every word also matches the contract's ECMAScript ^\S+$.
_RUN = re.compile(r"[^\s\ufeff]+|[\s\ufeff]+")
_WORD_START = re.compile(r"[^\s\ufeff]")
_BREAK = ("node-break", "")


class AlignmentTokensError(ValueError):
    """The request was rejected before it changed the session."""


class AlignmentTokensImmutable(AlignmentTokensError):
    """A stored token document cannot be updated or deleted."""


class ClinicTranscriptTokens(db.Model):
    """Write-once token document of one transcript revision."""

    __tablename__ = "clinic_transcript_tokens"

    transcript_revision_id = db.Column(db.String(36), primary_key=True)
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    tokenization_version = db.Column(db.String(32), nullable=False)
    document_hash = db.Column(db.String(64), nullable=False)
    document = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id"],
            ["clinic_transcript_revision.dictation_id", "clinic_transcript_revision.id"],
            name="fk_clinic_tokens_revision_same_dictation",
        ),
    )


def _reject_mutation(_mapper, _connection, _target):
    raise AlignmentTokensImmutable("token documents are write-once")


event.listen(ClinicTranscriptTokens, "before_update", _reject_mutation)
event.listen(ClinicTranscriptTokens, "before_delete", _reject_mutation)


def create_tables(bind=None) -> None:
    """Create the table. Safe to repeat."""
    ClinicTranscriptTokens.__table__.create(bind if bind is not None else db.engine, checkfirst=True)


def _id_prefix(revision_id: str) -> str:
    if len(revision_id) <= 100 and re.fullmatch(r"\S+", revision_id):
        return revision_id
    return hashlib.sha256(revision_id.encode("utf-8")).hexdigest()[:32]


def tokenize(body: str, revision_id: str) -> dict:
    """Split a normalised body into one text node per line."""
    if body != normalise_text(body):
        raise AlignmentTokensError("body must be normalised")
    prefix = _id_prefix(revision_id)
    nodes = []
    ordinal = 0
    for line_no, line in enumerate(body.split("\n")):
        tokens = []
        for match in _RUN.finditer(line):
            text = match.group(0)
            kind = "word" if _WORD_START.match(text) else "separator"
            tokens.append({"token_id": f"{prefix}:t{ordinal}", "kind": kind, "text": text, "marks": []})
            ordinal += 1
        nodes.append({
            "text_node_id": f"{prefix}:n{line_no}",
            "kind": "text",
            "block_type": "paragraph",
            "tokens": tokens,
        })
    return {"document_schema_version": DOCUMENT_SCHEMA_VERSION, "nodes": nodes}


def project(document: dict) -> str:
    """The plain text of a document. Equals the body it was built from."""
    return "\n".join("".join(t["text"] for t in node["tokens"]) for node in document["nodes"])


def document_hash(document: dict) -> str:
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _flatten(document: dict):
    """(kind, text) sequence with a node-break marker between nodes, plus parallel refs."""
    seq, refs = [], []
    for n, node in enumerate(document["nodes"]):
        if n:
            seq.append(_BREAK)
            refs.append(None)
        for token in node["tokens"]:
            seq.append((token["kind"], token["text"]))
            refs.append((n, token["token_id"]))
    return seq, refs


def carry(parent_document: dict, body: str, revision_id: str) -> dict:
    """Tokenize ``body``; tokens in diff-equal blocks keep the parent token id."""
    document = tokenize(body, revision_id)
    prefix = _id_prefix(revision_id)
    old_seq, old_refs = _flatten(parent_document)
    new_seq, new_refs = _flatten(document)
    matcher = difflib.SequenceMatcher(None, old_seq, new_seq, autojunk=False)
    carried = {}  # new flat index -> (parent node index, parent token id)
    for tag, i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for k in range(j2 - j1):
            if old_refs[i1 + k] is not None:
                carried[j1 + k] = old_refs[i1 + k]

    used = {ref[1] for ref in carried.values()}
    node_used = set()
    flat = 0
    ordinal = 0
    for n, node in enumerate(document["nodes"]):
        if n:
            flat += 1
        first_parent = None
        for token in node["tokens"]:
            if flat in carried:
                if first_parent is None:
                    first_parent = carried[flat][0]
                token["token_id"] = carried[flat][1]
            else:
                token["token_id"] = _fresh(f"{prefix}:t{ordinal}", used)
            ordinal += 1
            flat += 1
        wanted = None
        if first_parent is not None:
            wanted = parent_document["nodes"][first_parent]["text_node_id"]
        if wanted is not None and wanted not in node_used:
            node["text_node_id"] = wanted
        else:
            node["text_node_id"] = _fresh(f"{prefix}:n{n}", set(node_used))
        node_used.add(node["text_node_id"])
    return document


def _fresh(candidate: str, used: set) -> str:
    while candidate in used:
        candidate += "~"
    used.add(candidate)
    return candidate


def utf16_spans(document: dict) -> dict:
    """token_id -> half-open (start, end) in UTF-16 code units of the projection."""
    spans = {}
    offset = 0
    for n, node in enumerate(document["nodes"]):
        if n:
            offset += 1
        for token in node["tokens"]:
            end = offset + utf16_length(token["text"])
            spans[token["token_id"]] = (offset, end)
            offset = end
    return spans


def tokens_for(dictation, revision, *, inherit: bool = True) -> ClinicTranscriptTokens:
    """Build once; rich edits can opt out of plaintext-based token inheritance."""
    if revision.dictation_id != dictation.id:
        raise AlignmentTokensError("revision belongs to another dictation")
    row = db.session.get(ClinicTranscriptTokens, revision.id)
    if row is not None:
        return row
    parent_row = None
    if inherit and revision.parent_revision_id is not None:
        parent_row = db.session.get(ClinicTranscriptTokens, revision.parent_revision_id)
    if parent_row is not None:
        document = carry(parent_row.document, revision.body, revision.id)
    else:
        document = tokenize(revision.body, revision.id)
    row = ClinicTranscriptTokens(
        transcript_revision_id=revision.id,
        dictation_id=dictation.id,
        tokenization_version=TOKENIZATION_VERSION,
        document_hash=document_hash(document),
        document=document,
    )
    db.session.add(row)
    db.session.flush()
    return row
