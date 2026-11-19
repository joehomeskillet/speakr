"""Attach a TXT, RTF, DOCX or ODT document to one explicitly selected dictation.

Parsing is bounded and happens before any write. The original document is
stored unchanged under ``clinic/documents/v1`` and recorded as a
``ClinicOriginal`` of the selected dictation. Its canonical text becomes the
first human revision, with the actor as author. A dictation that already has a
human correction is never changed: the call is refused as ``text_exists``.

A dictation whose only original is the document is in the explicit work state
``document_only``. A failed parse raises ``DocumentIngestError``. Its
``message`` is the German text for the work list, and nothing is written.

Authorization is injected like in ``context_grants``. No route is registered
here, and no audio is decoded or transcribed.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Callable

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from src.clinic import rtf_text
from src.clinic.ingest import IngestError, IngestService, _spool
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import (
    KIND_HUMAN, RevisionError, add_human_revision, history, normalise_text,
)
from src.clinic.richtext_formats import _RTF_MACRO
from src.database import db

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_ZIP_MEMBERS = 100
MAX_PART_BYTES = 20 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024
MAX_RATIO = 100
EXTENSIONS = {".txt": "txt", ".rtf": "rtf", ".docx": "docx", ".odt": "odt"}
WORK_DOCUMENT_ONLY = "document_only"
WORK_WITH_AUDIO = "document_with_audio"

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT_NS = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_ODT_MIME = b"application/vnd.oasis.opendocument.text"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ACTIVE_NAME = re.compile(r"(?i)vba|macro|\.bin$|\.docm$|\.jar$|^(?:basic|scripts)/")

MESSAGES = {
    "forbidden": "Keine Berechtigung für dieses Diktat.",
    "not_found": "Das gewählte Diktat existiert nicht.",
    "invalid_input": "Die Eingabe ist ungültig.",
    "stale_version": "Das Diktat wurde inzwischen geändert. Bitte neu laden.",
    "text_exists": "Das Diktat hat bereits einen korrigierten Text. Das Dokument ersetzt ihn nicht.",
    "unsupported_format": "Dateityp nicht unterstützt. Erlaubt sind TXT, RTF, DOCX und ODT.",
    "format_mismatch": "Der Inhalt passt nicht zur Dateiendung.",
    "too_large": "Das Dokument überschreitet die zulässige Grösse.",
    "unsafe_archive": "Das Dokument enthält ein unzulässiges Archiv (Grösse, Makro oder Verschlüsselung).",
    "unsafe_xml": "Das Dokument enthält unzulässiges XML.",
    "malformed": "Das Dokument konnte nicht gelesen werden.",
    "empty": "Das Dokument enthält keinen Text.",
    "storage_failed": "Das Dokument konnte nicht gespeichert werden. Bitte erneut versuchen.",
}


class DocumentIngestError(Exception):
    """Refusal with a stable ``code`` and the German ``message``."""

    def __init__(self, code: str):
        self.code = code
        self.message = MESSAGES[code]
        super().__init__(code)


@dataclass(frozen=True)
class ParsedDocument:
    fmt: str
    text: str
    sha256: str
    size: int


@dataclass(frozen=True)
class DocumentReceipt:
    original_id: str
    dictation_id: str
    revision_id: str
    sha256: str
    work_state: str
    replayed: bool


def parse_document(data: bytes, filename: str) -> ParsedDocument:
    """Validate and read one document. Raises ``DocumentIngestError``; writes nothing."""
    if not isinstance(data, (bytes, bytearray)) or not isinstance(filename, str):
        raise DocumentIngestError("invalid_input")
    data = bytes(data)
    if not data:
        raise DocumentIngestError("empty")
    if len(data) > MAX_FILE_BYTES:
        raise DocumentIngestError("too_large")
    dot = filename.rfind(".")
    fmt = EXTENSIONS.get(filename[dot:].lower() if dot >= 0 else "")
    if fmt is None:
        raise DocumentIngestError("unsupported_format")
    try:
        if fmt == "txt":
            text = rtf_text.load_text_file(data)
        elif fmt == "rtf":
            text = _rtf(data)
        elif fmt == "docx":
            text = _docx(data)
        else:
            text = _odt(data)
    except rtf_text.RtfError as exc:
        raise DocumentIngestError("too_large" if exc.code == "too_large" else "malformed") from exc
    text = normalise_text(text)
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentIngestError("too_large")
    if _CONTROL.search(text):
        raise DocumentIngestError("malformed")
    if not text.strip():
        raise DocumentIngestError("empty")
    return ParsedDocument(fmt, text, hashlib.sha256(data).hexdigest(), len(data))


def attach_document(
    service: IngestService,
    *,
    dictation_id: str,
    actor_user_id: int,
    data: bytes,
    filename: str,
    expected_version: int,
    authorize: Callable[[ClinicDictation, int], bool],
    now: datetime | None = None,
) -> DocumentReceipt:
    """Bind ``data`` and its text to ``dictation_id``. The same bytes again replay the receipt."""
    if (not isinstance(dictation_id, str) or type(actor_user_id) is not int
            or type(expected_version) is not int or expected_version < 0
            or not isinstance(data, (bytes, bytearray))):
        raise DocumentIngestError("invalid_input")
    dictation = db.session.get(ClinicDictation, dictation_id, populate_existing=True)
    if dictation is None:
        raise DocumentIngestError("not_found")
    if authorize(dictation, actor_user_id) is not True:
        raise DocumentIngestError("forbidden")
    digest = hashlib.sha256(bytes(data)).hexdigest()
    replay = _replay(dictation, digest)
    if replay is not None:
        return replay
    if dictation.version_counter != expected_version:
        raise DocumentIngestError("stale_version")
    parsed = parse_document(data, filename)
    if any(row.kind == KIND_HUMAN for row in history(dictation)):
        raise DocumentIngestError("text_exists")

    moment = (now or datetime.now(timezone.utc))
    storage_key = f"clinic/documents/v1/{hashlib.sha256(f'{dictation_id}:{digest}'.encode()).hexdigest()}"
    dest = service.file_path(storage_key)
    try:
        spool, actual = _spool(BytesIO(bytes(data)), dest.parent, len(data))
        service._publish(spool, dest, actual)
    except (IngestError, OSError) as exc:
        raise DocumentIngestError("storage_failed") from exc

    had_original = db.session.query(ClinicOriginal.id).filter_by(dictation_id=dictation_id).first()
    try:
        changed = db.session.execute(update(ClinicDictation).where(
            ClinicDictation.id == dictation_id,
            ClinicDictation.version_counter == expected_version,
        ).values(version_counter=expected_version + 1))
        if changed.rowcount != 1:
            db.session.rollback()
            raise DocumentIngestError("stale_version")
        origin_ref = f"document:{parsed.fmt}:{actor_user_id}"
        original = ClinicOriginal(
            dictation_id=dictation_id,
            content_hash=digest,
            size_bytes=len(data),
            storage_key=storage_key,
            receipt=json.dumps({
                "v": 1, "kind": "document", "format": parsed.fmt, "sha256": digest,
                "size": len(data), "actor_user_id": actor_user_id, "storage_key": storage_key,
            }, sort_keys=True, separators=(",", ":")),
            origin_ref=origin_ref,
            created_at=moment,
        )
        db.session.add(original)
        db.session.flush()
        revision = add_human_revision(
            dictation, None, parsed.text, actor_user_id, original_version=original,
        )
        db.session.commit()
    except RevisionError as exc:
        db.session.rollback()
        raise DocumentIngestError("invalid_input") from exc
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise DocumentIngestError("storage_failed") from exc
    return DocumentReceipt(
        original.id, dictation_id, revision.id, digest,
        WORK_WITH_AUDIO if had_original else WORK_DOCUMENT_ONLY, False,
    )


def _replay(dictation: ClinicDictation, digest: str) -> DocumentReceipt | None:
    row = db.session.query(ClinicOriginal).filter(
        ClinicOriginal.dictation_id == dictation.id,
        ClinicOriginal.content_hash == digest,
        ClinicOriginal.origin_ref.like("document:%"),
    ).first()
    if row is None:
        return None
    revision = next((r for r in history(dictation) if r.original_version_id == row.id), None)
    if revision is None:
        return None
    others = db.session.query(ClinicOriginal.id).filter(
        ClinicOriginal.dictation_id == dictation.id, ClinicOriginal.id != row.id,
    ).first()
    return DocumentReceipt(
        row.id, dictation.id, revision.id, digest,
        WORK_WITH_AUDIO if others else WORK_DOCUMENT_ONLY, True,
    )


def _rtf(data: bytes) -> str:
    if not data.lstrip().startswith(b"{\\rtf"):
        raise DocumentIngestError("format_mismatch")
    source = data.decode("latin-1")
    if _RTF_MACRO.search(source):
        raise DocumentIngestError("unsafe_archive")
    return rtf_text.rtf_to_text(source, max_chars=MAX_FILE_BYTES)


def _open_zip(data: bytes) -> zipfile.ZipFile:
    if not data.startswith(b"PK\x03\x04"):
        raise DocumentIngestError("format_mismatch")
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocumentIngestError("malformed") from exc
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_ZIP_TOTAL_BYTES:
        raise DocumentIngestError("unsafe_archive")
    for info in infos:
        name = info.filename
        if (info.flag_bits & 0x1 or name.startswith("/") or ".." in name.split("/")
                or _ACTIVE_NAME.search(name) or info.file_size > MAX_PART_BYTES
                or info.file_size > MAX_RATIO * max(info.compress_size, 1)):
            raise DocumentIngestError("unsafe_archive")
    return archive


def _read_part(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        with archive.open(name) as handle:
            payload = handle.read(MAX_PART_BYTES + 1)
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise DocumentIngestError("malformed") from exc
    if len(payload) > MAX_PART_BYTES:
        raise DocumentIngestError("unsafe_archive")
    return payload


def _xml_root(payload: bytes) -> ET.Element:
    # Entities and external references are never needed for text. Refuse before parsing.
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", payload, re.IGNORECASE):
        raise DocumentIngestError("unsafe_xml")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise DocumentIngestError("malformed") from exc


def _docx(data: bytes) -> str:
    with _open_zip(data) as archive:
        root = _xml_root(_read_part(archive, "word/document.xml"))
    lines = []
    for paragraph in root.iter(f"{_W_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_W_NS}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W_NS}tab":
                parts.append("\t")
            elif node.tag in (f"{_W_NS}br", f"{_W_NS}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def _odt(data: bytes) -> str:
    with _open_zip(data) as archive:
        if _read_part(archive, "mimetype").strip() != _ODT_MIME:
            raise DocumentIngestError("format_mismatch")
        root = _xml_root(_read_part(archive, "content.xml"))
    return "\n".join(
        _odt_text(block) for block in root.iter()
        if block.tag in (f"{_TEXT_NS}p", f"{_TEXT_NS}h")
    )


def _odt_text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if child.tag == f"{_TEXT_NS}tab":
            parts.append("\t")
        elif child.tag == f"{_TEXT_NS}line-break":
            parts.append("\n")
        elif child.tag == f"{_TEXT_NS}s":
            count = child.get(f"{_TEXT_NS}c", "1")
            parts.append(" " * (min(int(count), 1000) if count.isdigit() else 1))
        else:
            parts.append(_odt_text(child))
        parts.append(child.tail or "")
    return "".join(parts)
