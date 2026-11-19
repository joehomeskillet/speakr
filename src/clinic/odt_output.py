"""Safe ODT artifact from the frozen canonical document and a permitted template.

Server-side file output only (PAR-EXP-005). Inserting into an already open
native Writer document stays the open web gap WG-EXP-005: no Writer process
is controlled and this module does not claim the gap closed.

The template is an ODT package whose ``content.xml`` holds one paragraph with
the insertion mark; that paragraph is replaced by the document blocks (bold,
italic, underline, headings, list items). Every other template part is copied
unchanged. Template and output are checked with ``zipfile`` and ``lxml``
(pinned in requirements.lock): archive traversal, encrypted or oversized
members, DTD/entity declarations, external references, scripts, macros and
embedded objects are refused. The output is re-read and must equal the
expected blocks before a receipt is issued. The receipt is computed from the
output bytes, not from the generator state.

Pure Python: no Flask, no database, no clock. Persistence of the receipt and
routes belong to the consumer packages (w106-output-api, w288-compare-exp).
"""

from __future__ import annotations

import hashlib
import posixpath
import zipfile
from dataclasses import asdict, dataclass
from io import BytesIO
from urllib.parse import urlsplit

from lxml import etree

from src.clinic.kis import payload_hash
from src.clinic.odt_export import ODT_MIMETYPE, _paragraph_items, _strip_illegal
from src.clinic.output_attempts import OutputSource
from src.clinic.revisions import text_hash
from src.clinic.rich_text_doc import RichTextError, to_plain_text
from src.clinic.richtext import SCHEMA_VERSION, CanonicalDocument

_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
_FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
_XLINK = "http://www.w3.org/1999/xlink"

MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_MEMBERS = 256
_STAMP = (1980, 1, 1, 0, 0, 0)
_XML_SUFFIXES = (".xml", ".rdf")
_BLOCKED_DIRS = ("basic/", "scripts/", "dialogs/", "object ")
_BLOCKED_SUFFIXES = (".bin", ".class", ".jar", ".exe", ".dll", ".js", ".vbs", ".py")
_BLOCKED_ELEMENTS = {"script", "scripts", "event-listener", "event-listeners"}
_EMBEDDED = {"object", "object-ole"}
_LIST_STYLE = "CliList"
_STYLE_PREFIX = "Cli"


class OdtOutputError(ValueError):
    """The ODT output was refused before bytes were produced. ``code`` is stable."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class OdtBlock:
    """One block read back from an ODT: runs are (text, bold, italic, underline)."""

    kind: str
    level: int
    runs: tuple[tuple[str, bool, bool, bool], ...]


@dataclass(frozen=True)
class OdtReceipt:
    """Independent evidence for one produced artifact. WG-EXP-005 stays open."""

    dictation_id: str
    text_revision_id: str
    source_text_hash: str
    template_hash: str
    artifact_sha256: str
    artifact_bytes: int
    block_count: int
    media_type: str
    schema_version: int
    wg_exp_005: str
    native_writer_controlled: bool
    receipt_hash: str


@dataclass(frozen=True)
class OdtOutput:
    data: bytes
    receipt: OdtReceipt


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


# --- package and XML checks -------------------------------------------------


def _open_package(data: bytes) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]:
    if not isinstance(data, (bytes, bytearray)):
        raise OdtOutputError("invalid_input")
    try:
        archive = zipfile.ZipFile(BytesIO(bytes(data)))
    except zipfile.BadZipFile as exc:
        raise OdtOutputError("malformed_archive") from exc
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(infos) > MAX_MEMBERS or sum(info.file_size for info in infos) > MAX_PACKAGE_BYTES:
        archive.close()
        raise OdtOutputError("archive_too_large")
    if len(set(names)) != len(names):
        archive.close()
        raise OdtOutputError("duplicate_member")
    for info in infos:
        name = info.filename
        lower = name.lower()
        parts = name.split("/")
        if (
            not name or name.startswith("/") or "\\" in name or ":" in name
            or "\x00" in name or ".." in parts or posixpath.normpath(name).startswith("..")
        ):
            archive.close()
            raise OdtOutputError("archive_traversal")
        if info.flag_bits & 0x1:
            archive.close()
            raise OdtOutputError("encrypted_member")
        if lower.startswith(_BLOCKED_DIRS) or lower.endswith(_BLOCKED_SUFFIXES) or "vba" in lower:
            archive.close()
            raise OdtOutputError("macro_refused")
    return archive, infos


def _parse_xml(payload: bytes) -> etree._Element:
    head = payload.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise OdtOutputError("dtd_refused")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        dtd_validation=False, huge_tree=False, remove_pis=True,
    )
    try:
        return etree.fromstring(payload, parser)
    except etree.XMLSyntaxError as exc:
        raise OdtOutputError("malformed_xml") from exc


def _check_references(root: etree._Element) -> None:
    for element in root.iter():
        local = _local(element.tag)
        if local in _BLOCKED_ELEMENTS:
            raise OdtOutputError("macro_refused")
        if local in _EMBEDDED:
            raise OdtOutputError("embedded_object_refused")
        for key, value in element.attrib.items():
            if _local(key) != "href" and key != _q(_XLINK, "href"):
                continue
            parts = urlsplit(value)
            internal = value.startswith("#") or (
                not parts.scheme and not parts.netloc and not value.startswith(("/", "\\"))
                and ".." not in value.split("/")
            )
            if not internal:
                raise OdtOutputError("external_reference")


def _read_package(data: bytes) -> tuple[dict[str, bytes], list[str], etree._Element]:
    """Validate a package and return its members, member order and parsed content.xml."""
    archive, infos = _open_package(data)
    with archive:
        order = [info.filename for info in infos]
        files = {info.filename: archive.read(info) for info in infos if not info.is_dir()}
    if order[:1] != ["mimetype"] or files.get("mimetype") != ODT_MIMETYPE.encode("ascii"):
        raise OdtOutputError("not_odt")
    if "content.xml" not in files or "META-INF/manifest.xml" not in files:
        raise OdtOutputError("not_odt")
    content = None
    for name, payload in files.items():
        if name.lower().endswith(_XML_SUFFIXES):
            root = _parse_xml(payload)
            _check_references(root)
            if name == "content.xml":
                content = root
    return files, order, content


# --- reading ----------------------------------------------------------------


def _text_flags(content: etree._Element) -> dict[str, tuple[bool, bool, bool]]:
    flags = {}
    for style in content.iter(_q(_STYLE, "style")):
        if style.get(_q(_STYLE, "family")) != "text":
            continue
        props = style.find(_q(_STYLE, "text-properties"))
        if props is None:
            continue
        underline = props.get(_q(_STYLE, "text-underline-style"), "none") != "none"
        flags[style.get(_q(_STYLE, "name"))] = (
            props.get(_q(_FO, "font-weight")) == "bold",
            props.get(_q(_FO, "font-style")) == "italic",
            underline,
        )
    return flags


def _inline(element, flags_by_style, fmt, out) -> None:
    if element.text:
        out.append((element.text, fmt))
    for child in element:
        local = _local(child.tag)
        if local == "span":
            style = child.get(_q(_TEXT, "style-name"))
            _inline(child, flags_by_style, flags_by_style.get(style, fmt), out)
        elif local == "s":
            out.append((" " * int(child.get(_q(_TEXT, "c"), "1")), fmt))
        elif local == "tab":
            out.append(("\t", fmt))
        elif local == "line-break":
            out.append(("\n", fmt))
        elif local not in ("bookmark", "bookmark-start", "bookmark-end"):
            out.append(("".join(child.itertext()), fmt))
        if child.tail:
            out.append((child.tail, fmt))


def _block(element, kind, level, flags_by_style) -> OdtBlock:
    pieces: list[tuple[str, tuple[bool, bool, bool]]] = []
    _inline(element, flags_by_style, (False, False, False), pieces)
    runs: list[tuple[str, bool, bool, bool]] = []
    for text, fmt in pieces:
        if not text:
            continue
        if runs and runs[-1][1:] == fmt:
            runs[-1] = (runs[-1][0] + text, *fmt)
        else:
            runs.append((text, *fmt))
    return OdtBlock(kind, level, tuple(runs))


def _child_blocks(child, flags) -> list[OdtBlock]:
    local = _local(child.tag)
    if local == "p":
        return [_block(child, "paragraph", 0, flags)]
    if local == "h":
        level = int(child.get(_q(_TEXT, "outline-level"), "1"))
        return [_block(child, "heading", level, flags)]
    if local == "list":
        return [
            _block(para, "list_item", 0, flags)
            for item in child.iter(_q(_TEXT, "list-item"))
            for para in item.findall(_q(_TEXT, "p"))
        ]
    return []


def _blocks(content: etree._Element, before: etree._Element | None = None):
    """All blocks, or with ``before`` the blocks that precede that body child."""
    flags = _text_flags(content)
    body = content.find(f"{_q(_OFFICE, 'body')}/{_q(_OFFICE, 'text')}")
    if body is None:
        raise OdtOutputError("not_odt")
    blocks: list[OdtBlock] = []
    for child in body:
        if child is before:
            break
        blocks.extend(_child_blocks(child, flags))
    return tuple(blocks)


def read_odt(data: bytes) -> tuple[OdtBlock, ...]:
    """Blocks of an ODT after the same safety checks as the template."""
    return _blocks(_read_package(data)[2])


# --- writing ----------------------------------------------------------------


def _to_lxml(item) -> etree._Element:
    return etree.Element(item.tag, dict(item.attrib))


def _add_items(container: etree._Element, items) -> None:
    for item in items:
        last = container[-1] if len(container) else None
        if isinstance(item, str):
            if last is None:
                container.text = (container.text or "") + item
            else:
                last.tail = (last.tail or "") + item
        else:
            container.append(_to_lxml(item))


def _style_name(bold: bool, italic: bool, underline: bool) -> str:
    return _STYLE_PREFIX + "".join(
        letter for letter, on in (("B", bold), ("I", italic), ("U", underline)) if on
    )


def _fill(element: etree._Element, runs, used: set[str]) -> None:
    for run in runs:
        text = _strip_illegal(run.text)
        if not text:
            continue
        items = _paragraph_items(text)
        if run.bold or run.italic or run.underline:
            name = _style_name(run.bold, run.italic, run.underline)
            used.add(name)
            span = etree.SubElement(element, _q(_TEXT, "span"))
            span.set(_q(_TEXT, "style-name"), name)
            _add_items(span, items)
        else:
            _add_items(element, items)


def _document_elements(doc: CanonicalDocument) -> tuple[list[etree._Element], set[str], bool]:
    elements: list[etree._Element] = []
    used: set[str] = set()
    current_list = None
    for node in doc.nodes:
        if node.kind == "list_item":
            if current_list is None:
                current_list = etree.Element(_q(_TEXT, "list"))
                current_list.set(_q(_TEXT, "style-name"), _LIST_STYLE)
                elements.append(current_list)
            item = etree.SubElement(current_list, _q(_TEXT, "list-item"))
            _fill(etree.SubElement(item, _q(_TEXT, "p")), node.runs, used)
            continue
        current_list = None
        if node.kind == "heading":
            element = etree.Element(_q(_TEXT, "h"))
            element.set(_q(_TEXT, "outline-level"), str(node.level))
        else:
            element = etree.Element(_q(_TEXT, "p"))
        _fill(element, node.runs, used)
        elements.append(element)
    uses_list = any(node.kind == "list_item" for node in doc.nodes)
    return elements, used, uses_list


def _add_styles(content: etree._Element, used: set[str], uses_list: bool) -> None:
    auto = content.find(_q(_OFFICE, "automatic-styles"))
    if auto is None:
        auto = etree.Element(_q(_OFFICE, "automatic-styles"))
        content.insert(0, auto)
    existing = {
        child.get(_q(_STYLE, "name")) or child.get(_q(_TEXT, "name")) for child in auto
    }
    wanted = set(used) | ({_LIST_STYLE} if uses_list else set())
    if wanted & existing:
        raise OdtOutputError("style_collision")
    for name in sorted(used):
        style = etree.SubElement(auto, _q(_STYLE, "style"))
        style.set(_q(_STYLE, "name"), name)
        style.set(_q(_STYLE, "family"), "text")
        props = etree.SubElement(style, _q(_STYLE, "text-properties"))
        letters = name[len(_STYLE_PREFIX):]
        if "B" in letters:
            props.set(_q(_FO, "font-weight"), "bold")
        if "I" in letters:
            props.set(_q(_FO, "font-style"), "italic")
        if "U" in letters:
            props.set(_q(_STYLE, "text-underline-style"), "solid")
            props.set(_q(_STYLE, "text-underline-width"), "auto")
            props.set(_q(_STYLE, "text-underline-color"), "font-color")
    if uses_list:
        list_style = etree.SubElement(auto, _q(_TEXT, "list-style"))
        list_style.set(_q(_STYLE, "name"), _LIST_STYLE)
        level = etree.SubElement(list_style, _q(_TEXT, "list-level-style-bullet"))
        level.set(_q(_TEXT, "level"), "1")
        level.set(_q(_TEXT, "bullet-char"), "•")


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=_STAMP)
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            info.external_attr = 0
            archive.writestr(info, payload)
    return buffer.getvalue()


def _expected_blocks(doc: CanonicalDocument) -> tuple[OdtBlock, ...]:
    blocks = []
    for node in doc.nodes:
        runs: list[tuple[str, bool, bool, bool]] = []
        for run in node.runs:
            text = _strip_illegal(run.text)
            if not text:
                continue
            fmt = (run.bold, run.italic, run.underline)
            if runs and runs[-1][1:] == fmt:
                runs[-1] = (runs[-1][0] + text, *fmt)
            else:
                runs.append((text, *fmt))
        level = node.level if node.kind == "heading" else 0
        blocks.append(OdtBlock(node.kind, level, tuple(runs)))
    return tuple(blocks)


# --- receipt ----------------------------------------------------------------


def make_receipt(data: bytes, source: OutputSource, template_hash: str) -> OdtReceipt:
    """Receipt computed from the artifact bytes alone (re-read and re-hashed)."""
    blocks = read_odt(data)
    fields = {
        "dictation_id": source.dictation_id,
        "text_revision_id": source.text_revision_id,
        "source_text_hash": source.text_hash,
        "template_hash": template_hash,
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_bytes": len(data),
        "block_count": len(blocks),
        "media_type": ODT_MIMETYPE,
        "schema_version": SCHEMA_VERSION,
        "wg_exp_005": "open",
        "native_writer_controlled": False,
    }
    return OdtReceipt(**fields, receipt_hash=payload_hash(fields))


def verify_receipt(data: bytes, receipt: OdtReceipt) -> bool:
    """True when ``receipt`` is the receipt of exactly these bytes."""
    try:
        source = OutputSource(
            receipt.dictation_id, "", receipt.text_revision_id, "", "",
            receipt.source_text_hash, "", False,
        )
        return make_receipt(data, source, receipt.template_hash) == receipt
    except OdtOutputError:
        return False


# --- entry point ------------------------------------------------------------


def render_odt_output(
    doc: CanonicalDocument,
    *,
    template: bytes,
    template_hash: str,
    insertion_mark: str,
    source: OutputSource,
    expected_version: int,
    allowed: bool,
) -> OdtOutput:
    """Insert ``doc`` at the template's insertion mark. Refusals return no bytes.

    ``template_hash`` is the stored sha256 of the permitted template; a template
    that does not hash to it is refused. ``source`` is the frozen output source;
    a document whose text identity differs is stale.
    """
    if allowed is not True:
        raise OdtOutputError("forbidden")
    if type(expected_version) is not int:
        raise OdtOutputError("invalid_version")
    if expected_version != SCHEMA_VERSION:
        raise OdtOutputError("stale_version")
    if not isinstance(doc, CanonicalDocument) or not isinstance(source, OutputSource):
        raise OdtOutputError("invalid_input")
    if not isinstance(insertion_mark, str) or not insertion_mark.strip():
        raise OdtOutputError("invalid_mark")
    try:
        identity = text_hash(to_plain_text(doc))
    except RichTextError as exc:
        raise OdtOutputError("invalid_input") from exc
    if identity != source.text_hash:
        raise OdtOutputError("stale_source")
    if not isinstance(template, (bytes, bytearray)):
        raise OdtOutputError("invalid_input")
    if hashlib.sha256(bytes(template)).hexdigest() != template_hash:
        raise OdtOutputError("template_not_permitted")

    files, order, content = _read_package(bytes(template))
    body = content.find(f"{_q(_OFFICE, 'body')}/{_q(_OFFICE, 'text')}")
    if body is None:
        raise OdtOutputError("not_odt")
    marks = [
        child for child in body
        if _local(child.tag) == "p" and "".join(child.itertext()).strip() == insertion_mark.strip()
    ]
    if len(marks) != 1:
        raise OdtOutputError("mark_not_found" if not marks else "mark_ambiguous")
    template_blocks = _blocks(content)
    mark_index = len(_blocks(content, before=marks[0]))
    elements, used, uses_list = _document_elements(doc)
    _add_styles(content, used, uses_list)
    anchor = marks[0]
    parent = anchor.getparent()
    position = list(parent).index(anchor)
    tail = anchor.tail
    parent.remove(anchor)
    for offset, element in enumerate(elements):
        parent.insert(position + offset, element)
    if tail and elements:
        elements[-1].tail = tail

    new_content = etree.tostring(content, encoding="UTF-8", xml_declaration=True)
    members = [(name, new_content if name == "content.xml" else files[name])
               for name in order if name in files]
    data = _zip(members)

    expected = (
        template_blocks[:mark_index] + _expected_blocks(doc) + template_blocks[mark_index + 1:]
    )
    if read_odt(data) != expected:
        raise OdtOutputError("roundtrip_mismatch")
    return OdtOutput(data, make_receipt(data, source, template_hash))


def receipt_dict(receipt: OdtReceipt) -> dict:
    """Plain mapping of the receipt for consumers that persist it."""
    return asdict(receipt)
