"""Read-only document and template preview for the correction editor.

PAR-COR-001 and PAR-CFG-002. ``document_ingest.parse_document`` is the only
parser. A preview never creates a dictation, stores the upload or replaces a
saved revision. Recognizable format losses are returned with the text so the
editor can show them before any later adoption.

The caller must already be allowed to read the correction (``correction_read``).
A template preview is limited to an active system template, the dictation
author's own template, or a template of the dictation's pool.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from io import BytesIO
from uuid import UUID

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import document_ingest, revisions
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.presets import ClinicTemplate
from src.database import db
from src.models import User

DOCUMENT_PREVIEW_PATH = "/api/clinic/v1/dictations/<uuid:dictation_id>/documents/preview"
TEMPLATE_PREVIEW_PATH = (
    "/api/clinic/v1/dictations/<uuid:dictation_id>/templates/<uuid:template_id>/preview"
)
FILE_FIELD = "file"
_READ = "correction_read"
_MAX_NAME = 255

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_MIME_NAME = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "template.docx",
    "application/vnd.oasis.opendocument.text": "template.odt",
    "text/plain": "template.txt",
    "text/rtf": "template.rtf",
    "application/rtf": "template.rtf",
}
_LOSS_ORDER = ("table", "graphic", "underline", "heading", "marks")
_LOSS_TEXT = {
    "table": "Tabelle wird als Fliesstext übernommen.",
    "graphic": "Grafik wird nicht übernommen.",
    "underline": "Unterstreichung gehört nicht zum freigegebenen Format.",
    "heading": "Überschrift gehört nicht zum freigegebenen Format.",
    "marks": "Fett und Kursiv werden in der Vorschau nicht übernommen.",
}
_STATUS = {
    "invalid_input": 400,
    "empty": 400,
    "unsupported_format": 400,
    "format_mismatch": 400,
    "malformed": 400,
    "unsafe_archive": 400,
    "unsafe_xml": 400,
    "too_large": 413,
    "not_found": 404,
    "forbidden": 403,
    "unauthenticated": 401,
}
_TITLES = {
    400: "Ungültige Anfrage",
    401: "Anmeldung erforderlich",
    403: "Zugriff verweigert",
    404: "Nicht gefunden",
    413: "Anfrage zu gross",
}
_RTF_LOSS = (
    ("table", re.compile(r"\\trowd(?![A-Za-z])")),
    ("graphic", re.compile(r"\\pict(?![A-Za-z])")),
    ("underline", re.compile(r"\\ul(?![A-Za-z0])")),
    ("heading", re.compile(r"\\outlinelevel(?![A-Za-z])")),
    ("marks", re.compile(r"\\[bi](?![A-Za-z0])")),
)
_XML_BOMB = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_HEADING_STYLE = ("heading", "überschrift", "uberschrift")


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool],
) -> Blueprint:
    """Mount the two preview reads. ``authorize`` is the editor's object policy."""
    bp = Blueprint("clinic_correction_documents", __name__)

    def problem(status: int, code: str, detail: str | None = None) -> Response:
        if detail is None:
            detail = document_ingest.MESSAGES.get(code, _TITLES.get(status, "Fehler"))
        response = jsonify(
            type="about:blank", title=_TITLES.get(status, "Fehler"), status=status,
            code=code, detail=detail,
        )
        response.status_code = status
        response.mimetype = "application/problem+json"
        return response

    def refuse(exc: document_ingest.DocumentIngestError) -> Response:
        return problem(_STATUS.get(exc.code, 400), exc.code, exc.message)

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            abort(401)
        if request.method == "POST":
            token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
            try:
                validate_csrf(token)
            except (ValidationError, CSRFError):
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException) -> Response | HTTPException:
        if isinstance(error, CSRFError):
            return problem(403, "forbidden", "Zugriff verweigert.")
        if error.code in _TITLES:
            code = {401: "unauthenticated", 403: "forbidden", 404: "not_found"}.get(
                error.code, "invalid_input",
            )
            return problem(error.code, code)
        return error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    def opened(dictation_id: UUID) -> ClinicDictation | None:
        row = db.session.get(ClinicDictation, str(dictation_id))
        if row is None or authorize(current_user, _READ, row) is not True:
            return None
        return row

    def payload(parsed: document_ingest.ParsedDocument, losses: list[dict[str, str]],
                mark: str | None, dictation: ClinicDictation) -> Response:
        head = revisions.current_revision(dictation)
        body = {
            "format": parsed.fmt,
            "text": parsed.text,
            "sha256": parsed.sha256,
            "size": parsed.size,
            "losses": losses,
            "insertion_mark": mark,
            "replaces_text": False,
            "text_revision_id": None if head is None else head.id,
        }
        return jsonify(body)

    @bp.post(DOCUMENT_PREVIEW_PATH)
    def preview_document(dictation_id: UUID) -> Response:
        dictation = opened(dictation_id)
        if dictation is None:
            return problem(404, "not_found")
        upload = request.files.get(FILE_FIELD)
        filename = None if upload is None else upload.filename
        if (upload is None or not isinstance(filename, str) or not filename
                or len(filename) > _MAX_NAME or "\x00" in filename):
            return problem(400, "invalid_input")
        data = upload.read(document_ingest.MAX_FILE_BYTES + 1)
        if not isinstance(data, bytes):
            data = bytes(data)
        if len(data) > document_ingest.MAX_FILE_BYTES:
            return problem(413, "too_large")
        try:
            parsed = document_ingest.parse_document(data, filename)
            losses = _losses(data, parsed.fmt)
        except document_ingest.DocumentIngestError as exc:
            return refuse(exc)
        return payload(parsed, losses, None, dictation)

    @bp.get(TEMPLATE_PREVIEW_PATH)
    def preview_template(dictation_id: UUID, template_id: UUID) -> Response:
        dictation = opened(dictation_id)
        if dictation is None:
            return problem(404, "not_found")
        row = db.session.get(ClinicTemplate, str(template_id))
        if row is None or not _template_allowed(row, dictation):
            return problem(404, "not_found")
        try:
            filename = _template_name(row.mime_type)
            data = bytes(row.content_bytes or b"")
            if len(data) > document_ingest.MAX_FILE_BYTES:
                return problem(413, "too_large")
            parsed = document_ingest.parse_document(data, filename)
            losses = _losses(data, parsed.fmt)
        except document_ingest.DocumentIngestError as exc:
            return refuse(exc)
        return payload(parsed, losses, row.insertion_mark, dictation)

    return bp


def _template_allowed(row: ClinicTemplate | None, dictation: ClinicDictation) -> bool:
    """System, the case author's template, or the case pool. Nothing else."""
    if row is None or not row.is_active:
        return False
    if row.scope == "system":
        return True
    if row.scope == "user":
        return row.scope_id == str(dictation.author_user_id)
    if row.scope == "pool":
        return dictation.pool_id is not None and row.scope_id == str(dictation.pool_id)
    return False


def _template_name(mime: str | None) -> str:
    if not isinstance(mime, str):
        raise document_ingest.DocumentIngestError("unsupported_format")
    base = mime.split(";", 1)[0].strip().lower()
    try:
        return _MIME_NAME[base]
    except KeyError:
        raise document_ingest.DocumentIngestError("unsupported_format") from None


def _losses(data: bytes, fmt: str) -> list[dict[str, str]]:
    """Losses the plain-text parser drops. The file has already passed the parser."""
    found: set[str] = set()
    if fmt == "docx":
        _docx_losses(_xml(_part(data, "word/document.xml")), found)
    elif fmt == "odt":
        _odt_losses(_xml(_part(data, "content.xml")), found)
        styles = _part(data, "styles.xml")
        if styles is not None:
            _odt_losses(_xml(styles), found)
    elif fmt == "rtf":
        source = data.decode("latin-1")
        found.update(code for code, pattern in _RTF_LOSS if pattern.search(source))
    return [{"code": code, "detail": _LOSS_TEXT[code]} for code in _LOSS_ORDER if code in found]


def _part(data: bytes, name: str) -> bytes | None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            try:
                info = archive.getinfo(name)
            except KeyError:
                return None
            if info.file_size > document_ingest.MAX_PART_BYTES:
                raise document_ingest.DocumentIngestError("unsafe_archive")
            with archive.open(info) as handle:
                payload = handle.read(document_ingest.MAX_PART_BYTES + 1)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise document_ingest.DocumentIngestError("malformed") from exc
    if len(payload) > document_ingest.MAX_PART_BYTES:
        raise document_ingest.DocumentIngestError("unsafe_archive")
    return payload


def _xml(payload: bytes | None) -> ET.Element:
    if payload is None or _XML_BOMB.search(payload):
        raise document_ingest.DocumentIngestError("malformed" if payload is None else "unsafe_xml")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise document_ingest.DocumentIngestError("malformed") from exc


def _attr(element: ET.Element, local: str) -> str | None:
    for key, value in element.attrib.items():
        if key == local or key.endswith("}" + local):
            return value
    return None


def _toggle_on(element: ET.Element) -> bool:
    raw = _attr(element, "val")
    if raw is None:
        return True
    return raw.lower() not in {"0", "false", "off", "none"}


def _docx_losses(root: ET.Element, found: set[str]) -> None:
    for element in root.iter():
        tag = element.tag
        if tag == f"{_W}tbl":
            found.add("table")
        elif tag in {f"{_W}drawing", f"{_W}pict", f"{_W}object"}:
            found.add("graphic")
        elif tag == f"{_W}u" and _toggle_on(element):
            found.add("underline")
        elif tag in {f"{_W}b", f"{_W}i"} and _toggle_on(element):
            found.add("marks")
        elif tag == f"{_W}pStyle":
            token = (_attr(element, "val") or "").lower().replace(" ", "").replace("_", "")
            if token.startswith(_HEADING_STYLE):
                found.add("heading")
        elif tag == f"{_W}outlineLvl":
            raw = _attr(element, "val")
            if raw is None or (raw.isdigit() and int(raw) < 9):
                found.add("heading")


def _odt_losses(root: ET.Element, found: set[str]) -> None:
    for element in root.iter():
        if element.tag == f"{_TABLE}table":
            found.add("table")
        elif element.tag == f"{_TEXT}h":
            found.add("heading")
        elif element.tag in {f"{_DRAW}image", f"{_DRAW}object"}:
            found.add("graphic")
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1]
            token = value.strip().lower()
            if name in {"font-weight", "font-weight-asian", "font-weight-complex"} and token == "bold":
                found.add("marks")
            elif name in {"font-style", "font-style-asian", "font-style-complex"} and token == "italic":
                found.add("marks")
            elif "underline" in name and token not in {"", "none", "false", "0"}:
                found.add("underline")
