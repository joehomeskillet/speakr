"""Output service: file name patterns and text/clipboard output receipts.

Two parts, both independent of Flask routes (wiring is a later work package):

1. ``expand_filename`` turns a pattern such as ``%A - %T`` into a file system
   safe name. A placeholder is a percent sign and one upper case letter. The
   result follows the position of each placeholder in the pattern.

   ======  ======================================================
   ``%A``  author of the dictation
   ``%T``  title of the dictation
   ``%D``  creation date of the dictation, ``YYYY-MM-DD``
   ``%S``  service staff member
   ``%C``  creator of the document
   ``%%``  a literal percent sign
   ======  ======================================================

   The reference system documents ``%A - %T`` inconsistently (its manual and
   its resource legend disagree on which of the two is first). The owner
   decision of 2026-11-18 applies: ``%A`` is the author, ``%T`` the title, and
   the order in the result is the order in the pattern. The date format of
   ``%D`` is not documented by the reference system; ISO 8601 is chosen here.
   An unknown placeholder, a lone trailing percent sign, or a known
   placeholder without a value raises ``OutputNameError``. Nothing is skipped.

   Sanitising is applied to the whole expanded name, so a value cannot add a
   path separator or a reserved name: forbidden characters
   (``< > : " / \\ | ? *``, control, format and surrogate characters) become
   ``_``; white space runs collapse to one space; leading and trailing spaces
   and dots are removed; Windows device names (``CON``, ``NUL``, ``COM1``...)
   get a leading ``_``; the stem is cut to ``max_stem_length`` characters. The
   extension is validated, never sanitised.

2. ``ClinicOutputReceipt`` records who output which exact transcript
   revision, where, and when. Rows are append-only: the ORM refuses an update
   or a delete. The matching database triggers belong to the wiring work
   package, together with the clean-up in the shared test fixtures.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import ForeignKeyConstraint, event

from src.database import db

from .models import (
    ClinicDictation,
    ClinicTranscriptRevision,
    _created_at,
    _in_check,
    _utcnow,
    _uuid,
)
from .revisions import current_revision

# --- file name patterns ----------------------------------------------------

PLACEHOLDERS = {
    "A": "author",
    "T": "title",
    "D": "created_on",
    "S": "service_staff",
    "C": "document_creator",
}
DEFAULT_MAX_STEM_LENGTH = 120
_DATE_ZONE = ZoneInfo("Europe/Zurich")
_FORBIDDEN = set('<>:"/\\|?*')
_BAD_CATEGORIES = {"Cc", "Cf", "Cs"}
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED |= {"COM" + str(n) for n in range(1, 10)}
_RESERVED |= {"LPT" + str(n) for n in range(1, 10)}
_EXTENSION = re.compile(r"[A-Za-z0-9]{1,8}")


class OutputNameError(ValueError):
    """The file name pattern or its values were rejected."""


class UnknownPlaceholder(OutputNameError):
    """The pattern holds a placeholder that is not in ``PLACEHOLDERS``."""

    def __init__(self, token: str):
        super().__init__("unknown placeholder " + repr(token))
        self.token = token


class MissingPlaceholderValue(OutputNameError):
    """A known placeholder has no value in the context."""

    def __init__(self, token: str):
        super().__init__("no value for placeholder " + repr(token))
        self.token = token


@dataclass(frozen=True)
class OutputNameContext:
    """Values for the placeholders. ``None`` or an empty string means missing."""

    author: str | None = None
    title: str | None = None
    created_on: date | datetime | None = None
    service_staff: str | None = None
    document_creator: str | None = None


def _date_text(value: date | datetime) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise OutputNameError("created_on must be timezone-aware")
        return value.astimezone(_DATE_ZONE).date().isoformat()
    return value.isoformat()


def _value(context: OutputNameContext, letter: str) -> str:
    raw = getattr(context, PLACEHOLDERS[letter])
    if raw is None or raw == "":
        raise MissingPlaceholderValue("%" + letter)
    if letter == "D":
        return _date_text(raw)
    if not isinstance(raw, str):
        raise OutputNameError("value for %" + letter + " must be a str")
    return raw


def expand_pattern(pattern: str, context: OutputNameContext) -> str:
    """Substitute placeholders in pattern order. No sanitising yet."""
    if not isinstance(pattern, str):
        raise TypeError("pattern must be a str")
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char != "%":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(pattern):
            raise UnknownPlaceholder("%")
        letter = pattern[index + 1]
        index += 2
        if letter == "%":
            out.append("%")
        elif letter in PLACEHOLDERS:
            out.append(_value(context, letter))
        else:
            raise UnknownPlaceholder("%" + letter)
    return "".join(out)


def sanitize_stem(text: str, *, max_length: int = DEFAULT_MAX_STEM_LENGTH) -> str:
    """File system safe name part, see the module docstring for the rules."""
    if max_length < 1:
        raise ValueError("max_length must be at least 1")
    replaced = []
    for char in unicodedata.normalize("NFC", text):
        if char.isspace():
            replaced.append(" ")
        elif char in _FORBIDDEN or unicodedata.category(char) in _BAD_CATEGORIES:
            replaced.append("_")
        else:
            replaced.append(char)
    stem = re.sub(r" {2,}", " ", "".join(replaced)).strip(" .")
    stem = stem[:max_length].rstrip(" .")
    if stem == "":
        raise OutputNameError("file name is empty after sanitising")
    # Windows treats "NUL.txt" like "NUL": check the part before the first dot.
    if stem.split(".", 1)[0].rstrip(" ").upper() in _RESERVED:
        stem = ("_" + stem)[:max_length]
    return stem


def expand_filename(
    pattern: str,
    context: OutputNameContext,
    *,
    extension: str | None = None,
    max_stem_length: int = DEFAULT_MAX_STEM_LENGTH,
) -> str:
    """Expand ``pattern`` and return a safe file name, with ``.extension`` if given."""
    stem = sanitize_stem(expand_pattern(pattern, context), max_length=max_stem_length)
    if extension is None:
        return stem
    if not isinstance(extension, str) or _EXTENSION.fullmatch(extension) is None:
        raise OutputNameError("extension must be 1 to 8 letters or digits, no dot")
    return stem + "." + extension


# --- output receipts -------------------------------------------------------

CHANNEL_CLIPBOARD = "CLIPBOARD"
CHANNEL_TEXT = "TEXT"
OUTPUT_CHANNELS = (CHANNEL_CLIPBOARD, CHANNEL_TEXT)
FORMAT_PLAIN = "PLAIN"
FORMAT_FORMATTED = "FORMATTED"
OUTPUT_FORMATS = (FORMAT_PLAIN, FORMAT_FORMATTED)


class OutputReceiptError(ValueError):
    """The output receipt was rejected before it changed the session."""


class OutputReceiptImmutable(OutputReceiptError):
    """An existing output receipt cannot be updated or deleted."""


class ClinicOutputReceipt(db.Model):
    """One output event: this user put this exact revision to this target at this time."""

    __tablename__ = "clinic_output_receipt"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    transcript_revision_id = db.Column(db.String(36), nullable=False)
    # Hash of the output text; the composite key ties it to the revision row.
    text_hash = db.Column(db.String(128), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    channel = db.Column(db.String(16), nullable=False)
    text_format = db.Column(db.String(16), nullable=False)
    target = db.Column(db.String(255), nullable=False)
    output_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_output_dictation"
        ),
        ForeignKeyConstraint(
            ["dictation_id", "transcript_revision_id", "text_hash"],
            [
                "clinic_transcript_revision.dictation_id",
                "clinic_transcript_revision.id",
                "clinic_transcript_revision.normalised_text_hash",
            ],
            name="fk_clinic_output_revision_same_dictation",
        ),
        _in_check("channel", OUTPUT_CHANNELS, "ck_clinic_output_channel"),
        _in_check("text_format", OUTPUT_FORMATS, "ck_clinic_output_format"),
    )


def record_output(
    dictation: ClinicDictation,
    revision: ClinicTranscriptRevision,
    actor_user_id: int,
    channel: str,
    target: str,
    *,
    text_format: str = FORMAT_PLAIN,
    output_at: datetime | None = None,
) -> ClinicOutputReceipt:
    """Append a receipt. Every call is a new event; repeated output is not merged."""
    if not isinstance(dictation, ClinicDictation) or dictation.id is None:
        raise OutputReceiptError("dictation must be a persisted ClinicDictation")
    if (
        not isinstance(revision, ClinicTranscriptRevision)
        or revision.id is None
        or revision.dictation_id != dictation.id
    ):
        raise OutputReceiptError("revision must be a persisted revision of this dictation")
    if isinstance(actor_user_id, bool) or not isinstance(actor_user_id, int):
        raise OutputReceiptError("actor_user_id must be an int")
    from src.models import User

    if db.session.get(User, actor_user_id) is None:
        raise OutputReceiptError("actor user does not exist")
    if channel not in OUTPUT_CHANNELS:
        raise OutputReceiptError("channel must be one of " + ", ".join(OUTPUT_CHANNELS))
    if text_format not in OUTPUT_FORMATS:
        raise OutputReceiptError("text_format must be one of " + ", ".join(OUTPUT_FORMATS))
    if not isinstance(target, str) or target.strip() == "" or len(target) > 255:
        raise OutputReceiptError("target must be a non-empty str of at most 255 characters")
    when = _utcnow() if output_at is None else output_at
    if when.tzinfo is None or when.utcoffset() is None:
        raise OutputReceiptError("output_at must be timezone-aware")
    row = ClinicOutputReceipt(
        dictation_id=dictation.id,
        transcript_revision_id=revision.id,
        text_hash=revision.normalised_text_hash,
        actor_user_id=actor_user_id,
        channel=channel,
        text_format=text_format,
        target=target,
        output_at=when.astimezone(timezone.utc),
    )
    db.session.add(row)
    db.session.flush()
    return row


def list_outputs(dictation: ClinicDictation) -> list[ClinicOutputReceipt]:
    """Receipts of one dictation, oldest output first."""
    if not isinstance(dictation, ClinicDictation) or dictation.id is None:
        raise OutputReceiptError("dictation must be a persisted ClinicDictation")
    return (
        db.session.query(ClinicOutputReceipt)
        .filter(ClinicOutputReceipt.dictation_id == dictation.id)
        .order_by(ClinicOutputReceipt.output_at, ClinicOutputReceipt.created_at, ClinicOutputReceipt.id)
        .all()
    )


def is_current_output(receipt: ClinicOutputReceipt) -> bool:
    """True when the output still shows the head of the transcript chain."""
    dictation = db.session.get(ClinicDictation, receipt.dictation_id)
    head = current_revision(dictation)
    return head is not None and head.id == receipt.transcript_revision_id


def _reject_receipt_write(_mapper, _connection, _target):
    raise OutputReceiptImmutable("output receipts are append-only")


event.listen(ClinicOutputReceipt, "before_update", _reject_receipt_write)
event.listen(ClinicOutputReceipt, "before_delete", _reject_receipt_write)
