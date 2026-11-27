"""Export and import the canonical clinic rich-text document.

Approved marks are bold, italic, paragraph and list. HTML uses the sanitiser
in ``rich_text_doc``. Plain text and formatted RTF use the existing renderers.
ODT, DOCX, standalone web HTML and the webbundle are projections of the same
nodes. The canonical document is never rewritten.

Unsupported marks, tables and macros become a loss report or a refusal. Local
graphics are embedded as validated PNG data in standalone web HTML.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO

from src.clinic import odt_export, output_rtf, rtf_text
from src.clinic.odt_export import render_odt
from src.clinic.revisions import normalise_text, text_hash
from src.clinic.rich_text_doc import (
    Document, Node, RichTextError, from_html, to_html, to_plain_text,
)
from src.clinic.richtext import SCHEMA_VERSION, CanonicalDocument, Segment
from src.clinic.rtf_text import rtf_to_text, text_to_rtf
from src.clinic.sdict_bundle import FORMAT_VERSION, BundleError, build_bundle, read_bundle
from src.clinic.unicode_anchor_map import map_through_normalisation, to_utf16_offset

_MACRO = re.compile(r"(?i)<\s*(?:script|iframe|object)\b|vbaProject|\\objdata|\\macro")
_RTF_MACRO = re.compile(r"(?i)\\(?:macro|objdata|objemb)")
_JS_WORD = re.compile(r"javascript\s*:", re.IGNORECASE)
_TABLE = re.compile(r"(?i)<\s*table\b")
_GRAPHIC = re.compile(r"(?i)<\s*(?:img|svg)\b")
_MARKUP = re.compile(r"<(?:/?[a-zA-Z][^<>]*>|[!?])")
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_STAMP = (1980, 1, 1, 0, 0, 0)
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WEB_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"


class FormatError(ValueError):
    """The conversion was rejected. ``code`` is stable."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Loss:
    """One visible gap between the canonical document and a channel."""

    code: str
    detail: str
    channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Export:
    """Channels for one canonical document. ``document`` is the input object."""

    schema_version: int
    document: CanonicalDocument
    plain: str
    html: str
    rtf: bytes
    plain_rtf: str
    odt: bytes
    docx: bytes
    bundle: bytes
    losses: tuple[Loss, ...]
    text_identity: str
    anchors: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class Import:
    """A sanitised document. Injected markup is not part of ``text_identity``."""

    document: CanonicalDocument
    losses: tuple[Loss, ...]
    text_identity: str


def approved_signature(doc: CanonicalDocument) -> tuple:
    """Bold, italic, paragraph and list, in document order."""
    if not isinstance(doc, CanonicalDocument):
        raise FormatError("invalid_document")
    return tuple(
        (node.kind, tuple((run.text, run.bold, run.italic) for run in node.runs))
        for node in doc.nodes
    )


def export_document(
    doc: CanonicalDocument, *, expected_version: int, allowed: bool, audio: bytes = _WAV,
) -> Export:
    """Project ``doc``. Unauthorized and stale callers receive no bytes."""
    _gate(expected_version, allowed)
    if not isinstance(doc, CanonicalDocument):
        raise FormatError("invalid_document")
    plain = to_plain_text(doc)
    html = to_html(doc)
    try:
        rtf = output_rtf.text_to_rtf(_rtf_source(doc))
        plain_rtf = text_to_rtf(plain)
        odt = render_odt(["".join(run.text for run in node.runs) for node in doc.nodes])
    except (output_rtf.RTFValidationError, rtf_text.RtfError, odt_export.OdtExportError) as exc:
        raise FormatError("export_failed") from exc
    anchors = _anchors(doc)
    return Export(
        schema_version=SCHEMA_VERSION,
        document=doc,
        plain=plain,
        html=html,
        rtf=rtf,
        plain_rtf=plain_rtf,
        odt=odt,
        docx=_docx(doc),
        bundle=_bundle(doc, plain, anchors, audio),
        losses=_export_losses(doc),
        text_identity=text_hash(plain),
        anchors=anchors,
    )


def web_html(doc: CanonicalDocument, graphics: Mapping[str, bytes]) -> str:
    """Return an offline HTML document with authorised local graphics embedded."""
    if not isinstance(doc, CanonicalDocument):
        raise FormatError("invalid_document")
    body = to_html(doc)
    for node in doc.nodes:
        if node.kind != "graphic":
            continue
        data = graphics.get(node.object_id)
        if data is None:
            raise FormatError("graphic_not_found")
        if (not isinstance(data, bytes) or not data.startswith(_PNG_SIGNATURE)
                or hashlib.sha256(data).hexdigest() != node.object_id):
            raise FormatError("graphic_integrity_error")
        encoded = base64.b64encode(data).decode("ascii")
        body = body.replace(
            f'src="local:{node.object_id}"', f'src="data:image/png;base64,{encoded}"',
        )
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{WEB_CSP}">'
        f'<title>Diktat</title></head><body>{body}</body></html>'
    )


def export_losses(doc: CanonicalDocument, kind: str) -> tuple[Loss, ...]:
    """Return the concrete losses for one explicitly selected export format."""
    if not isinstance(doc, CanonicalDocument):
        raise FormatError("invalid_document")
    if kind not in {"txt", "rtf", "odt", "docx", "html", "sdict"}:
        raise FormatError("invalid_format")
    return tuple(loss for loss in _export_losses(doc) if kind in loss.channels)


def import_html(raw: str, *, expected_version: int, allowed: bool) -> Import:
    """Sanitise HTML into a canonical document. Macros are refused whole."""
    _gate(expected_version, allowed)
    if not isinstance(raw, str):
        raise FormatError("invalid_input")
    if _MACRO.search(raw):
        raise FormatError("macro_refused")
    count = 0

    def new_id() -> str:
        nonlocal count
        count += 1
        return f"n{count}"

    shielded, saved = _shield_js_words(raw)
    try:
        parsed = from_html(shielded, new_id)
        doc = _canonical(parsed, saved)
    except RichTextError as exc:
        raise FormatError("malformed") from exc
    if _MARKUP.search(to_plain_text(doc)) or _MACRO.search(to_html(doc)):
        raise FormatError("malformed")
    return Import(doc, _import_losses(raw, doc), text_hash(to_plain_text(doc)))


def rtf_signature(rtf: bytes) -> tuple:
    """Recover the approved subset from bytes produced by the RTF renderer."""
    if not isinstance(rtf, (bytes, bytearray)):
        raise FormatError("invalid_input")
    if _RTF_MACRO.search(bytes(rtf).decode("ascii", errors="ignore")):
        raise FormatError("macro_refused")
    try:
        parsed = output_rtf.validate_rtf(rtf)
    except output_rtf.RTFValidationError as exc:
        raise FormatError("malformed") from exc
    # \pard starts a content paragraph. A raw split on \par also cuts \pard.
    chunks = [line for line in bytes(rtf).decode("ascii").splitlines() if line.startswith("\\pard")]
    if len(chunks) != len(parsed.styled_paragraphs):
        raise FormatError("rtf_mismatch")
    rows = []
    for chunk, spans in zip(chunks, parsed.styled_paragraphs):
        is_list = "\\bullet" in chunk
        rows.append(("list_item" if is_list else "paragraph", _clean_spans(spans, is_list)))
    return tuple(rows)


def reload_odt(data: bytes, *, expected_version: int, allowed: bool) -> tuple[str, ...]:
    """Paragraph texts from an ODT this module wrote."""
    root = _zip_xml(data, "content.xml", expected_version=expected_version, allowed=allowed)
    return tuple("".join(paragraph.itertext()) for paragraph in root.iter(f"{{{_TEXT_NS}}}p"))


def reload_docx(data: bytes, *, expected_version: int, allowed: bool) -> str:
    """Plain paragraphs from a DOCX. A macro part refuses the file."""
    root = _zip_xml(data, "word/document.xml", expected_version=expected_version, allowed=allowed)
    return "\n".join("".join(paragraph.itertext()) for paragraph in root.iter(f"{{{_W_NS}}}p"))


def reload_bundle(
    data: bytes, *, expected_version: int, allowed: bool,
) -> CanonicalDocument:
    """Load the canonical payload back out of the webbundle."""
    _gate(expected_version, allowed)
    if not isinstance(data, (bytes, bytearray)):
        raise FormatError("invalid_input")
    try:
        bundle = read_bundle(bytes(data))
    except BundleError as exc:
        if exc.code == "active_content":
            raise FormatError("macro_refused") from exc
        raise FormatError("malformed") from exc
    _reject_macro_files(bundle.files)
    try:
        payload = json.loads(bundle.files["text/document.json"].decode("utf-8"))
        doc = CanonicalDocument.from_payload(payload)
    except (KeyError, UnicodeError, ValueError, RichTextError) as exc:
        raise FormatError("malformed") from exc
    if bundle.files.get("text/body.txt", b"").decode("utf-8") != to_plain_text(doc):
        raise FormatError("identity_mismatch")
    got = tuple(
        (item["id"], item["start"], item["end"]) for item in bundle.manifest.get("anchors", [])
    )
    if got != _anchors(doc) or payload.get("schema_version") != SCHEMA_VERSION:
        raise FormatError("anchor_mismatch")
    return doc


def _gate(expected_version: int, allowed: bool) -> None:
    if allowed is not True:
        raise FormatError("forbidden")
    if type(expected_version) is not int:
        raise FormatError("invalid_version")
    if expected_version != SCHEMA_VERSION:
        raise FormatError("stale_version")


def _shield_js_words(raw: str) -> tuple[str, dict[str, str]]:
    """Keep ``javascript:`` in visible text. The sanitiser rewrites every copy."""
    saved: dict[str, str] = {}
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        token = f"\ue000{count}\ue001"
        saved[token] = match.group(0)
        return token

    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"<[^>]*>", raw):
        pieces.append(_JS_WORD.sub(repl, raw[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(_JS_WORD.sub(repl, raw[cursor:]))
    return "".join(pieces), saved


def _canonical(parsed: Document, saved: dict[str, str]) -> CanonicalDocument:
    nodes = []
    count = 0
    for node in parsed.nodes:
        runs = []
        for run in node.runs:
            count += 1
            text = run.text
            for token, original in saved.items():
                text = text.replace(token, original)
            runs.append(Segment(
                id=f"s{count}",
                text=text,
                bold=run.bold,
                italic=run.italic,
                underline=run.underline,
            ))
        kind = node.kind if node.kind in ("paragraph", "heading", "list_item") else "paragraph"
        level = node.level if kind == "heading" else 0
        nodes.append(Node(id=node.id, kind=kind, runs=tuple(runs), level=level))
    return CanonicalDocument(tuple(nodes))


def _rtf_source(doc: CanonicalDocument) -> str:
    lines = []
    for node in doc.nodes:
        parts = []
        for run in node.runs:
            # shortcut: a hard break inside a run becomes a space here. Plain RTF keeps it.
            text = run.text.replace("\n", " ").replace("\r", "")
            if run.bold and run.italic:
                text = f"<strong><em>{text}</em></strong>"
            elif run.bold:
                text = f"<strong>{text}</strong>"
            elif run.italic:
                text = f"<em>{text}</em>"
            parts.append(text)
        line = "".join(parts)
        lines.append(f"- {line}" if node.kind == "list_item" else line)
    return "\n".join(lines)


def _clean_spans(spans, is_list: bool) -> tuple:
    cleaned = []
    for index, span in enumerate(spans):
        text = span.text
        if is_list and index == 0 and text.startswith("\t"):
            text = text[1:]
        if text:
            cleaned.append((text, span.bold, span.italic))
    return tuple(cleaned)


def _anchors(doc: CanonicalDocument) -> tuple[tuple[str, int, int], ...]:
    plain, spans = doc.project()
    # shortcut: one mapper call per edge; the mapper is quadratic. Upgrade near MAX_TEXT.
    normalised = normalise_text(plain)
    rows = []
    for node in doc.nodes:
        for run in node.runs:
            start, end = spans[run.id]
            rows.append((
                run.id,
                to_utf16_offset(normalised, map_through_normalisation(plain, start)),
                to_utf16_offset(normalised, map_through_normalisation(plain, end)),
            ))
    return tuple(rows)


def _export_losses(doc: CanonicalDocument) -> tuple[Loss, ...]:
    losses: list[Loss] = []
    limited = ("txt", "rtf", "odt", "docx")
    runs = tuple(run for node in doc.nodes for run in node.runs)
    if any(node.kind == "heading" for node in doc.nodes):
        losses.append(Loss(
            "heading", "Überschrift wird in RTF, ODT und DOCX als Absatz und in TXT als reiner Text exportiert.",
            limited,
        ))
    if any(run.underline for run in runs):
        losses.append(Loss("underline", "Unterstreichung entfällt in TXT, RTF, ODT und DOCX.", limited))
    if any(node.kind == "list_item" for node in doc.nodes):
        losses.append(Loss(
            "list_plain", "Liste wird in ODT und DOCX als Absatz und in TXT ohne Listenformat ausgegeben.",
            ("txt", "odt", "docx"),
        ))
    if any(run.bold or run.italic for run in runs):
        losses.append(Loss("odt_marks", "ODT übernimmt Fett und Kursiv nicht.", ("odt",)))
    if any(run.font_family is not None or run.font_size_pt is not None for run in runs):
        losses.append(Loss("font", "Schriftart und Schriftgrösse entfallen in TXT, RTF, ODT und DOCX.", limited))
    if any(node.text_align is not None for node in doc.nodes):
        losses.append(Loss("text_align", "Textausrichtung entfällt in TXT, RTF, ODT und DOCX.", limited))
    if any(node.line_spacing is not None for node in doc.nodes):
        losses.append(Loss("line_spacing", "Zeilenabstand entfällt in TXT, RTF, ODT und DOCX.", limited))
    if any(node.kind == "graphic" for node in doc.nodes):
        losses.append(Loss(
            "graphic", "Grafik entfällt in RTF, ODT und DOCX; TXT enthält nur ihren Alternativtext.", limited,
        ))
    if losses:
        losses.append(Loss(
            "plain_unformatted",
            "TXT enthält bewusst keine HTML-Auszeichnung, Schrift-, Ausrichtungs- oder Zeilenabstandsangaben.",
            ("txt",),
        ))
    if any(run.audio_anchor_id for run in runs):
        losses.append(Loss(
            "html_anchors", "Audioanker bleiben nur im Webbundle erhalten.",
            ("txt", "rtf", "odt", "docx", "html"),
        ))
    return tuple(losses)


def _import_losses(raw: str, doc: CanonicalDocument) -> tuple[Loss, ...]:
    losses: list[Loss] = []
    if _TABLE.search(raw):
        losses.append(Loss("table", "Tabelle wird als Fliesstext übernommen."))
    if _GRAPHIC.search(raw):
        losses.append(Loss("graphic", "Grafik wird nicht übernommen."))
    if any(run.underline for node in doc.nodes for run in node.runs):
        losses.append(Loss("underline", "Unterstreichung gehört nicht zum freigegebenen Format."))
    if any(node.kind == "heading" for node in doc.nodes):
        losses.append(Loss("heading", "Überschrift gehört nicht zum freigegebenen Format."))
    return tuple(losses)


def _bundle(
    doc: CanonicalDocument, plain: str, anchors: tuple[tuple[str, int, int], ...], audio: bytes,
) -> bytes:
    payload = json.dumps(doc.to_payload(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    body = plain.encode("utf-8")
    files = {"audio/a.wav": audio, "text/body.txt": body, "text/document.json": payload}
    manifest = {
        "format_version": FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "audio_sources": [{
            "id": "a1", "path": "audio/a.wav",
            "sha256": hashlib.sha256(audio).hexdigest(), "bytes": len(audio),
        }],
        "edit_list": [],
        "text_revisions": [{"id": "r1", "path": "text/body.txt"}],
        "anchors": [{"id": seg_id, "start": start, "end": end} for seg_id, start, end in anchors],
        "checksums": {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
    }
    try:
        return build_bundle(manifest, files)
    except BundleError as exc:
        raise FormatError("export_failed") from exc


def _docx(doc: CanonicalDocument) -> bytes:
    root = ET.Element(f"{{{_W_NS}}}document")
    body = ET.SubElement(root, f"{{{_W_NS}}}body")
    for node in doc.nodes:
        paragraph = ET.SubElement(body, f"{{{_W_NS}}}p")
        for run in node.runs:
            element = ET.SubElement(paragraph, f"{{{_W_NS}}}r")
            if run.bold or run.italic:
                props = ET.SubElement(element, f"{{{_W_NS}}}rPr")
                if run.bold:
                    ET.SubElement(props, f"{{{_W_NS}}}b")
                if run.italic:
                    ET.SubElement(props, f"{{{_W_NS}}}i")
            text = ET.SubElement(element, f"{{{_W_NS}}}t")
            if run.text[:1] == " " or run.text[-1:] == " ":
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            text.text = run.text.replace("\r", "")
    document = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    content_types = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    return _zip((
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", rels),
        ("word/document.xml", document),
    ))


def _zip(members: tuple[tuple[str, bytes], ...]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0
            archive.writestr(info, data)
    return buffer.getvalue()


def _zip_xml(data: bytes, name: str, *, expected_version: int, allowed: bool) -> ET.Element:
    _gate(expected_version, allowed)
    if not isinstance(data, (bytes, bytearray)):
        raise FormatError("invalid_input")
    try:
        archive = zipfile.ZipFile(BytesIO(bytes(data)))
    except zipfile.BadZipFile as exc:
        raise FormatError("malformed") from exc
    with archive:
        names = archive.namelist()
        if any("vba" in item.lower() or item.lower().endswith((".bin", ".docm")) for item in names):
            raise FormatError("macro_refused")
        try:
            payload = archive.read(name)
        except KeyError as exc:
            raise FormatError("malformed") from exc
    if b"vbaProject" in payload or b"w:macros" in payload:
        raise FormatError("macro_refused")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FormatError("malformed") from exc


def _reject_macro_files(files: dict[str, bytes]) -> None:
    for name, data in files.items():
        lower = name.lower()
        if "vba" in lower or "macro" in lower or lower.endswith(
            (".bin", ".html", ".htm", ".js", ".vbs", ".exe"),
        ):
            raise FormatError("macro_refused")
        if b"vbaProject" in data[:8192] or b"Attribute VB_" in data[:8192]:
            raise FormatError("macro_refused")
