"""w050: safe ODT artifact from the canonical document and a permitted template.

PAR-EXP-005 file output only. WG-EXP-005 (insert into an open native Writer
document) stays open; nothing here controls a Writer process.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import replace
from io import BytesIO

import pytest

from src.clinic import odt_output
from src.clinic.odt_export import ODT_MIMETYPE, render_odt
from src.clinic.odt_output import (
    OdtBlock,
    OdtOutputError,
    make_receipt,
    read_odt,
    render_odt_output,
    verify_receipt,
)
from src.clinic.output_attempts import OutputSource, bind_output_source  # noqa: F401
from src.clinic.revisions import text_hash
from src.clinic.rich_text_doc import Node, to_plain_text
from src.clinic.richtext import SCHEMA_VERSION, CanonicalDocument, Segment

ORACLES = {
    "BT-EXP-005": (
        "ODT wird serverseitig aus Vorlage erzeugt; keine Fernsteuerung der laufenden Writer-Instanz.",
    ),
}

MARK = "{{DIKTAT}}"


def _seg(seg_id: str, text: str, **flags) -> Segment:
    return Segment(id=seg_id, text=text, **flags)


def _doc() -> CanonicalDocument:
    return CanonicalDocument((
        Node("h1", "heading", (_seg("s1", "Überschrift Äpfel"),), level=2),
        Node("p1", "paragraph", (
            _seg("s2", "Größe "),
            _seg("s3", "fett", bold=True),
            _seg("s4", " und "),
            _seg("s5", "kursiv", italic=True),
            _seg("s6", " unterstrichen", underline=True),
            _seg("s7", " 😀 Zeile\neins  zwei\tdrei"),
        )),
        Node("l1", "list_item", (_seg("s8", "Ärztin", bold=True, italic=True),)),
        Node("l2", "list_item", (_seg("s9", "Öl"),)),
        Node("p2", "paragraph", (_seg("s10", "Schluss"),)),
    ))


def _source(doc: CanonicalDocument) -> OutputSource:
    return OutputSource(
        "d1", "a1", "t1", "c1", "ah", text_hash(to_plain_text(doc)), "ch", True,
    )


def _template() -> tuple[bytes, str]:
    data = render_odt(["Praxis Kopf", MARK, "Fusszeile"], title="Vorlage")
    return data, hashlib.sha256(data).hexdigest()


def _rebuild(data: bytes, *, replace_parts=None, extra=()) -> bytes:
    replace_parts = replace_parts or {}
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as out:
        for info in source.infolist():
            payload = replace_parts.get(info.filename, source.read(info))
            clone = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            clone.compress_type = info.compress_type
            out.writestr(clone, payload)
        for name, payload in extra:
            out.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), payload)
    return buffer.getvalue()


def _render(doc=None, template=None, **overrides):
    doc = doc or _doc()
    data, digest = template or _template()
    kwargs = dict(
        template=data, template_hash=digest, insertion_mark=MARK, source=_source(doc),
        expected_version=SCHEMA_VERSION, allowed=True,
    )
    kwargs.update(overrides)
    return render_odt_output(doc, **kwargs)


def _unsafe_template(replacement: bytes | None = None, extra=()):
    data, _ = _template()
    parts = {}
    if replacement is not None:
        parts["content.xml"] = replacement
    unsafe = _rebuild(data, replace_parts=parts, extra=extra)
    return unsafe, hashlib.sha256(unsafe).hexdigest()


def _content(data: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.read("content.xml")


def test_round_trip_unicode_paragraphs_and_formatting():
    doc = _doc()
    out = _render(doc)
    blocks = read_odt(out.data)
    assert [b.kind for b in blocks] == [
        "paragraph", "heading", "paragraph", "list_item", "list_item", "paragraph", "paragraph",
    ]
    assert blocks[0].runs == (("Praxis Kopf", False, False, False),)
    assert blocks[-1].runs == (("Fusszeile", False, False, False),)
    heading = blocks[1]
    assert heading.level == 2 and heading.runs == (("Überschrift Äpfel", False, False, False),)
    mixed = blocks[2].runs
    assert mixed[0] == ("Größe ", False, False, False)
    assert ("fett", True, False, False) in mixed
    assert ("kursiv", False, True, False) in mixed
    assert (" unterstrichen", False, False, True) in mixed
    assert mixed[-1] == (" 😀 Zeile\neins  zwei\tdrei", False, False, False)
    assert blocks[3].runs == (("Ärztin", True, True, False),)
    assert blocks[4].runs == (("Öl", False, False, False),)
    # Template mimetype entry first, stored, and the mark paragraph is gone.
    with zipfile.ZipFile(BytesIO(out.data)) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == ODT_MIMETYPE.encode("ascii")
        assert archive.testzip() is None
    assert MARK.encode() not in _content(out.data)
    # Canonical document untouched.
    assert to_plain_text(doc) == to_plain_text(_doc())


def test_template_parts_other_than_content_are_copied_unchanged():
    data, digest = _template()
    out = _render(template=(data, digest))
    with zipfile.ZipFile(BytesIO(data)) as before, zipfile.ZipFile(BytesIO(out.data)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name != "content.xml":
                assert before.read(name) == after.read(name)


def test_receipt_is_independent_of_the_generator_and_keeps_wg_open():
    doc = _doc()
    data, digest = _template()
    out = _render(doc, template=(data, digest))
    receipt = out.receipt
    assert receipt.artifact_sha256 == hashlib.sha256(out.data).hexdigest()
    assert receipt.artifact_bytes == len(out.data)
    assert receipt.template_hash == digest
    assert receipt.dictation_id == "d1" and receipt.text_revision_id == "t1"
    assert receipt.source_text_hash == text_hash(to_plain_text(doc))
    assert receipt.block_count == 7
    assert receipt.media_type == ODT_MIMETYPE
    assert receipt.wg_exp_005 == "open" and receipt.native_writer_controlled is False
    assert verify_receipt(out.data, receipt) is True
    assert make_receipt(out.data, _source(doc), digest) == receipt
    tampered = _rebuild(out.data, replace_parts={"content.xml": _content(out.data) + b" "})
    assert verify_receipt(tampered, receipt) is False
    assert verify_receipt(b"not a zip", receipt) is False
    assert verify_receipt(out.data, replace(receipt, block_count=1)) is False


def test_retry_after_failure_is_byte_identical_and_failure_returns_no_bytes():
    doc = _doc()
    first = _render(doc)
    with pytest.raises(OdtOutputError) as refused:
        _render(doc, allowed=False)
    assert refused.value.code == "forbidden"
    second = _render(doc)
    assert second.data == first.data and second.receipt == first.receipt


def test_unauthorized_and_stale_callers_get_no_bytes():
    doc = _doc()
    for kwargs, code in (
        ({"allowed": False}, "forbidden"),
        ({"allowed": 1}, "forbidden"),
        ({"expected_version": SCHEMA_VERSION + 1}, "stale_version"),
        ({"expected_version": SCHEMA_VERSION - 1}, "stale_version"),
        ({"expected_version": True}, "invalid_version"),
        ({"expected_version": "1"}, "invalid_version"),
    ):
        with pytest.raises(OdtOutputError) as caught:
            _render(doc, **kwargs)
        assert caught.value.code == code, kwargs


def test_stale_source_revision_is_refused():
    doc = _doc()
    edited = CanonicalDocument(doc.nodes[:-1])
    with pytest.raises(OdtOutputError) as caught:
        _render(edited, source=_source(doc))
    assert caught.value.code == "stale_source"


def test_template_must_match_the_permitted_hash():
    data, digest = _template()
    other = _rebuild(data, replace_parts={"meta.xml": b"<x/>"})
    with pytest.raises(OdtOutputError) as caught:
        _render(template=(other, digest))
    assert caught.value.code == "template_not_permitted"


def test_archive_traversal_in_template_is_refused():
    for name in ("../evil.txt", "/abs.txt", "a/../../b.txt", "c:evil.txt", "a\\b.txt"):
        unsafe = _unsafe_template(extra=[(name, b"x")])
        with pytest.raises(OdtOutputError) as caught:
            _render(template=unsafe)
        assert caught.value.code == "archive_traversal", name


def test_oversized_and_non_odt_packages_are_refused(monkeypatch):
    monkeypatch.setattr(odt_output, "MAX_MEMBERS", 2)
    with pytest.raises(OdtOutputError) as caught:
        _render()
    assert caught.value.code == "archive_too_large"
    monkeypatch.undo()
    garbage = b"not a zip"
    with pytest.raises(OdtOutputError) as caught:
        _render(template=(garbage, hashlib.sha256(garbage).hexdigest()))
    assert caught.value.code == "malformed_archive"
    plain = _rebuild(_template()[0], replace_parts={"mimetype": b"text/plain"})
    with pytest.raises(OdtOutputError) as caught:
        _render(template=(plain, hashlib.sha256(plain).hexdigest()))
    assert caught.value.code == "not_odt"


def test_external_references_dtd_and_active_content_are_refused():
    head = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink" '
        b'xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0" '
        b'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" office:version="1.2">'
    )
    tail = b"</office:document-content>"

    def body(inner: bytes) -> bytes:
        return head + b"<office:body><office:text><text:p>{{DIKTAT}}</text:p>" + inner + (
            b"</office:text></office:body>" + tail
        )

    cases = {
        "external_reference": [
            body(b'<text:p><text:a xlink:href="http://example.invalid/x">l</text:a></text:p>'),
            body(b'<text:p><text:a xlink:href="file:///etc/passwd">l</text:a></text:p>'),
            body(b'<text:p><text:a xlink:href="../../etc/passwd">l</text:a></text:p>'),
            body(b'<text:p><text:a xlink:href="//host/share">l</text:a></text:p>'),
        ],
        "macro_refused": [
            body(b'<text:p><script:event-listener script:event-name="dom:click"/></text:p>'),
        ],
        "embedded_object_refused": [
            body(b'<text:p><draw:object xlink:href="#Object 1"/></text:p>'),
        ],
        "dtd_refused": [
            b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            + body(b"<text:p>&x;</text:p>")[len(b'<?xml version="1.0" encoding="UTF-8"?>'):],
        ],
        "malformed_xml": [b"<office:document-content"],
    }
    for code, payloads in cases.items():
        for payload in payloads:
            unsafe = _unsafe_template(payload)
            with pytest.raises(OdtOutputError) as caught:
                _render(template=unsafe)
            assert caught.value.code == code, payload
    for extra_name, code in (
        ("Basic/Standard/Module1.xml", "macro_refused"),
        ("Scripts/python/x.py", "macro_refused"),
        ("Object 1/content.xml", "macro_refused"),
        ("Thumbnails/evil.bin", "macro_refused"),
    ):
        unsafe = _unsafe_template(extra=[(extra_name, b"x")])
        with pytest.raises(OdtOutputError) as caught:
            _render(template=unsafe)
        assert caught.value.code == code, extra_name


def test_internal_anchor_links_in_the_template_are_allowed():
    inner = b'<text:p><text:a xlink:href="#Kapitel">intern</text:a></text:p>'
    content = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">'
        b"<office:body><office:text><text:p>{{DIKTAT}}</text:p>" + inner +
        b"</office:text></office:body></office:document-content>"
    )
    out = _render(template=_unsafe_template(content))
    assert read_odt(out.data)[-1].runs == (("intern", False, False, False),)


def test_insertion_mark_must_be_unique_and_present():
    data, digest = _template()
    with pytest.raises(OdtOutputError) as caught:
        _render(template=(data, digest), insertion_mark="{{FEHLT}}")
    assert caught.value.code == "mark_not_found"
    with pytest.raises(OdtOutputError) as caught:
        _render(template=(data, digest), insertion_mark="  ")
    assert caught.value.code == "invalid_mark"
    twice = render_odt([MARK, MARK])
    with pytest.raises(OdtOutputError) as caught:
        _render(template=(twice, hashlib.sha256(twice).hexdigest()))
    assert caught.value.code == "mark_ambiguous"


def test_style_collision_with_template_is_refused():
    content = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        b'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" office:version="1.2">'
        b'<office:automatic-styles><style:style style:name="CliB" style:family="text"/>'
        b"</office:automatic-styles>"
        b"<office:body><office:text><text:p>{{DIKTAT}}</text:p></office:text></office:body>"
        b"</office:document-content>"
    )
    with pytest.raises(OdtOutputError) as caught:
        _render(template=_unsafe_template(content))
    assert caught.value.code == "style_collision"


def test_read_odt_applies_the_same_safety_checks():
    unsafe = _rebuild(_template()[0], extra=[("../x", b"y")])
    with pytest.raises(OdtOutputError):
        read_odt(unsafe)
    assert read_odt(_template()[0]) == (
        OdtBlock("paragraph", 0, (("Praxis Kopf", False, False, False),)),
        OdtBlock("paragraph", 0, ((MARK, False, False, False),)),
        OdtBlock("paragraph", 0, (("Fusszeile", False, False, False),)),
    )
