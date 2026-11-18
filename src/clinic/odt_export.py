"""ODT file generation from paragraphs (file output only).

Renders a valid OpenDocument Text file from a sequence of paragraph
strings. This is file output only: the module produces the bytes of an
ODT archive. Inserting into an already open native LibreOffice Writer
document is the open web gap WG-EXP-005 (PAR-EXP-005) and stays open;
this module does not claim PAR-EXP-005 is closed.

Pure Python, no Flask, no database, no clock: the output is
deterministic for the same input (fixed ZIP timestamps). Persistence
and routes are a later wiring package.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from io import BytesIO

ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"

_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_STYLE_NS = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
_META_NS = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
_FO_NS = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"

for _prefix, _uri in (
    ("office", _OFFICE_NS),
    ("text", _TEXT_NS),
    ("style", _STYLE_NS),
    ("meta", _META_NS),
    ("fo", _FO_NS),
    ("dc", _DC_NS),
    ("manifest", _MANIFEST_NS),
):
    ET.register_namespace(_prefix, _uri)

_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_GENERATOR = "clinic-odt-export-1"
_FIXED_CREATION_DATE = "1980-01-01T00:00:00"

_XML_1_0_KEEP_RE = re.compile(
    "[^\t\n\x20-\x7e\u00a0-\ud7ff\ue000-\ufffd\U00010000-\U0010fffd]"
)


class OdtExportError(ValueError):
    """The ODT export was rejected before it produced output.

    ``code`` is a stable string the later wiring package maps on.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def render_odt(paragraphs: Sequence[str], *, title: str = "") -> bytes:
    """Render paragraphs as one deterministic ODT file.

    Each paragraph becomes one ``text:p``. Runs of two or more spaces
    become ``text:s``, a tab becomes ``text:tab``, a newline inside a
    paragraph becomes ``text:line-break``. Characters illegal in XML 1.0
    (control characters except tab and newline) are removed. An empty
    paragraph is kept. A non-empty ``title`` is written to ``meta.xml``
    as ``dc:title``.
    """
    if not isinstance(paragraphs, Sequence) or isinstance(paragraphs, (str, bytes)):
        raise OdtExportError("invalid_paragraphs", "paragraphs must be a Sequence[str]")
    for index, paragraph in enumerate(paragraphs):
        if not isinstance(paragraph, str):
            raise TypeError(
                f"paragraph at index {index} must be str, got {type(paragraph).__name__}"
            )
    if not isinstance(title, str):
        raise OdtExportError("invalid_title", "title must be a str")

    parts = (
        ("mimetype", ODT_MIMETYPE.encode("ascii"), zipfile.ZIP_STORED),
        ("META-INF/manifest.xml", _manifest_xml(), zipfile.ZIP_DEFLATED),
        ("content.xml", _content_xml(paragraphs), zipfile.ZIP_DEFLATED),
        ("styles.xml", _styles_xml(), zipfile.ZIP_DEFLATED),
        ("meta.xml", _meta_xml(title), zipfile.ZIP_DEFLATED),
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data, compress_type in parts:
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.compress_type = compress_type
            info.external_attr = 0
            archive.writestr(info, data)
    return buffer.getvalue()


def _strip_illegal(text: str) -> str:
    """Remove characters illegal in XML 1.0, keeping tab and newline."""
    return _XML_1_0_KEEP_RE.sub("", text)


def _paragraph_items(text: str) -> list[str | ET.Element]:
    """Split one cleaned paragraph into plain text runs and inline elements."""
    items: list[str | ET.Element] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            items.append("".join(run))
            run.clear()

    index = 0
    while index < len(text):
        char = text[index]
        if char == " ":
            count = 1
            while index + count < len(text) and text[index + count] == " ":
                count += 1
            flush()
            if count >= 2:
                spaces = ET.Element(f"{{{_TEXT_NS}}}s")
                spaces.set(f"{{{_TEXT_NS}}}c", str(count))
                items.append(spaces)
            else:
                run.append(" ")
            index += count
        elif char == "\t":
            flush()
            items.append(ET.Element(f"{{{_TEXT_NS}}}tab"))
            index += 1
        elif char == "\n":
            flush()
            items.append(ET.Element(f"{{{_TEXT_NS}}}line-break"))
            index += 1
        else:
            run.append(char)
            index += 1
    flush()
    return items


def _paragraph(paragraph: str) -> ET.Element:
    """One text:p; literal text goes to .text/.tail so order is preserved."""
    element = ET.Element(f"{{{_TEXT_NS}}}p")
    target = element
    for item in _paragraph_items(_strip_illegal(paragraph)):
        if isinstance(item, str):
            if target is element:
                element.text = (element.text or "") + item
            else:
                target.tail = (target.tail or "") + item
        else:
            element.append(item)
            target = item
    return element


def _content_xml(paragraphs: Sequence[str]) -> bytes:
    root = ET.Element(f"{{{_OFFICE_NS}}}document-content")
    root.set(f"{{{_OFFICE_NS}}}version", "1.2")
    body = ET.SubElement(root, f"{{{_OFFICE_NS}}}body")
    text = ET.SubElement(body, f"{{{_OFFICE_NS}}}text")
    for paragraph in paragraphs:
        text.append(_paragraph(paragraph))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _styles_xml() -> bytes:
    root = ET.Element(f"{{{_OFFICE_NS}}}document-styles")
    root.set(f"{{{_OFFICE_NS}}}version", "1.2")
    styles = ET.SubElement(root, f"{{{_OFFICE_NS}}}styles")
    standard = ET.SubElement(styles, f"{{{_STYLE_NS}}}style")
    standard.set(f"{{{_STYLE_NS}}}name", "Standard")
    standard.set(f"{{{_STYLE_NS}}}family", "paragraph")
    standard.set(f"{{{_STYLE_NS}}}class", "text")
    properties = ET.SubElement(standard, f"{{{_STYLE_NS}}}text-properties")
    properties.set(f"{{{_STYLE_NS}}}font-name", "Liberation Sans")
    properties.set(f"{{{_STYLE_NS}}}font-size", "12pt")
    properties.set(f"{{{_FO_NS}}}language", "de")
    properties.set(f"{{{_FO_NS}}}country", "CH")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _meta_xml(title: str) -> bytes:
    root = ET.Element(f"{{{_OFFICE_NS}}}document-meta")
    root.set(f"{{{_OFFICE_NS}}}version", "1.2")
    meta = ET.SubElement(root, f"{{{_OFFICE_NS}}}meta")
    generator = ET.SubElement(meta, f"{{{_META_NS}}}generator")
    generator.text = _GENERATOR
    creation_date = ET.SubElement(meta, f"{{{_META_NS}}}creation-date")
    creation_date.text = _FIXED_CREATION_DATE
    if title:
        dc_title = ET.SubElement(meta, f"{{{_DC_NS}}}title")
        dc_title.text = title
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _manifest_xml() -> bytes:
    root = ET.Element(f"{{{_MANIFEST_NS}}}manifest")
    root.set(f"{{{_MANIFEST_NS}}}version", "1.2")
    entries = (
        ("/", ODT_MIMETYPE),
        ("mimetype", ODT_MIMETYPE),
        ("content.xml", ODT_MIMETYPE),
        ("styles.xml", ODT_MIMETYPE),
        ("meta.xml", ODT_MIMETYPE),
        ("META-INF/manifest.xml", "application/vnd.oasis.opendocument.manifest+xml"),
    )
    for full_path, media_type in entries:
        entry = ET.SubElement(root, f"{{{_MANIFEST_NS}}}file-entry")
        entry.set(f"{{{_MANIFEST_NS}}}full-path", full_path)
        entry.set(f"{{{_MANIFEST_NS}}}media-type", media_type)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
