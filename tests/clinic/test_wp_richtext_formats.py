"""Approved rich-text channels share one canonical document (WB-016)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from src.clinic.output_rtf import validate_rtf
from src.clinic.revisions import normalise_text, text_hash
from src.clinic.rich_text_doc import Node, to_html, to_plain_text
from src.clinic.richtext import SCHEMA_VERSION, CanonicalDocument, Segment
from src.clinic.richtext_formats import (
    FormatError,
    approved_signature,
    export_document,
    import_html,
    reload_bundle,
    reload_docx,
    reload_odt,
    rtf_signature,
)
from src.clinic.rtf_text import rtf_to_text
from src.clinic.unicode_anchor_map import map_through_normalisation, to_utf16_offset

ROOT = Path(__file__).resolve().parents[2]
ORACLES = {
    "BT-REC-008": (
        "Sitzungsformat erhält Audio, Text und Zeitbezug.",
        "RTF erhält unterstützte Formatierung; TXT ist bewusst unformatiert.",
        "Textdatei ohne Audio wird nicht als synchronisierte Sitzung ausgegeben.",
    ),
    "BT-LIVE-005": (
        "Formatierte und reine Ausgabe unterscheiden sich genau nach Wahl.",
        "Fehlgeschlagene Übergabe lässt den Entwurf zur Wiederholung verfügbar.",
        "Erfolgreiche Übergabe beachtet die ausgewählten Leeren-/Schliessenoptionen.",
    ),
    "BT-EXP-003": (
        "RTF ist syntaktisch lesbar und enthält richtigen Text, Absätze und unterstützte Grafik.",
        "Autoöffnen folgt Option und meldet fehlende Zielanwendung.",
    ),
    "WB-016": (
        "Dokumentierte Formattreue/Ankerprüfung; Verluste sichtbar, kein Makroimport.",
    ),
}


def _segment(seg_id: str, text: str, **flags) -> Segment:
    return Segment(id=seg_id, text=text, **flags)


def _doc() -> CanonicalDocument:
    return CanonicalDocument((
        Node("p1", "paragraph", (
            _segment("s1", "Größe "),
            _segment("s2", "fett", bold=True),
            _segment("s3", " und "),
            _segment("s4", "kursiv", italic=True),
        )),
        Node("l1", "list_item", (_segment("s5", "Ärztin", audio_anchor_id="a1"),)),
        Node("l2", "list_item", (_segment("s6", "Öl", bold=True, italic=True),)),
    ))


def _export():
    return export_document(_doc(), expected_version=SCHEMA_VERSION, allowed=True)


def test_approved_subset_round_trips_html_plain_and_rtf():
    body = _doc()
    exported = export_document(body, expected_version=SCHEMA_VERSION, allowed=True)
    assert exported.document is body
    assert exported.schema_version == SCHEMA_VERSION == body.to_payload()["schema_version"]
    assert rtf_to_text(exported.plain_rtf) == exported.plain == to_plain_text(body)
    assert rtf_signature(exported.rtf) == approved_signature(body)
    imported = import_html(exported.html, expected_version=SCHEMA_VERSION, allowed=True)
    assert approved_signature(imported.document) == approved_signature(body)
    assert imported.text_identity == exported.text_identity == text_hash(exported.plain)
    assert "Größe" in exported.plain and "Öl" in exported.plain
    again = export_document(body, expected_version=SCHEMA_VERSION, allowed=True)
    assert again.rtf == exported.rtf and again.bundle == exported.bundle


def test_unsupported_marks_stay_visible_without_changing_canonical_text():
    body = CanonicalDocument((
        Node("h1", "heading", (_segment("s1", "Überschrift", underline=True),), level=1),
    ))
    before = body.to_payload()
    exported = export_document(body, expected_version=SCHEMA_VERSION, allowed=True)
    assert body.to_payload() == before
    assert body.nodes[0].kind == "heading" and body.nodes[0].runs[0].underline is True
    codes = {loss.code for loss in exported.losses}
    assert {"heading", "underline", "plain_unformatted"} <= codes
    assert "Überschrift" in exported.plain
    parsed = validate_rtf(exported.rtf)
    assert "Überschrift" in parsed.full_text
    assert all(not span.underline for spans in parsed.styled_paragraphs for span in spans)
    assert "\\ul" not in exported.rtf.decode("ascii")


def test_malformed_html_cannot_inject_or_shift_text_identity():
    clean = import_html("<p>Größe</p>", expected_version=SCHEMA_VERSION, allowed=True)
    dirty = import_html(
        '<p onclick="alert(1)">Größe</p><img src="bild.png" alt="Röntgen">',
        expected_version=SCHEMA_VERSION, allowed=True,
    )
    table = import_html(
        "<p>Größe</p><table><tr><td>Zelle</td></tr></table>",
        expected_version=SCHEMA_VERSION, allowed=True,
    )
    cells = import_html("<p>Größe</p><p>Zelle</p>", expected_version=SCHEMA_VERSION, allowed=True)
    html = to_html(dirty.document)
    assert dirty.text_identity == clean.text_identity
    assert "Röntgen" not in to_plain_text(dirty.document)
    assert "onclick" not in html and "<img" not in html and "<script" not in html
    assert {loss.code for loss in dirty.losses} == {"graphic"}
    assert "Zelle" in to_plain_text(table.document)
    assert "<table" not in to_html(table.document)
    assert table.text_identity == cells.text_identity
    assert any(loss.code == "table" for loss in table.losses)
    escaped = import_html(
        "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>",
        expected_version=SCHEMA_VERSION, allowed=True,
    )
    link = import_html(
        '<p>siehe javascript:void</p><a href="javascript:alert(1)">Größe</a>',
        expected_version=SCHEMA_VERSION, allowed=True,
    )
    rendered = to_html(link.document)
    assert to_plain_text(link.document) == "siehe javascript:void\nGröße"
    assert "href" not in rendered and "<a" not in rendered
    assert "<" not in to_plain_text(escaped.document)
    assert "<script" not in to_html(escaped.document)
    assert escaped.text_identity == text_hash(to_plain_text(escaped.document))


def test_macro_refusal_then_retry_imports_the_clean_text():
    with pytest.raises(FormatError) as refused:
        import_html("<p>Größe</p><script>alert(1)</script>", expected_version=SCHEMA_VERSION, allowed=True)
    assert refused.value.code == "macro_refused"
    imported = import_html("<p>Größe</p>", expected_version=SCHEMA_VERSION, allowed=True)
    assert to_plain_text(imported.document) == "Größe"
    hostile = BytesIO()
    with zipfile.ZipFile(hostile, "w") as archive:
        archive.writestr("word/vbaProject.bin", b"vbaProject")
        archive.writestr("word/document.xml", b"<xml/>")
    with pytest.raises(FormatError) as macro:
        reload_docx(hostile.getvalue(), expected_version=SCHEMA_VERSION, allowed=True)
    assert macro.value.code == "macro_refused"
    assert reload_docx(_export().docx, expected_version=SCHEMA_VERSION, allowed=True) == _export().plain
    note = CanonicalDocument((Node("p1", "paragraph", (_segment("s1", "siehe javascript:void"),)),))
    note_export = export_document(note, expected_version=SCHEMA_VERSION, allowed=True)
    assert rtf_signature(note_export.rtf) == approved_signature(note)
    with pytest.raises(FormatError) as embedded:
        rtf_signature(b"{\\rtf1\\macrocalc x}")
    assert embedded.value.code == "macro_refused"


def test_unauthorized_and_stale_version_export_nothing():
    body = _doc()
    before = body.to_payload()
    with pytest.raises(FormatError) as denied:
        export_document(body, expected_version=0, allowed=False)
    assert denied.value.code == "forbidden"
    with pytest.raises(FormatError) as stale:
        export_document(body, expected_version=0, allowed=True)
    assert stale.value.code == "stale_version"
    with pytest.raises(FormatError) as bad:
        import_html("<p>Größe</p>", expected_version=True, allowed=True)  # type: ignore[arg-type]
    assert bad.value.code == "invalid_version"
    assert body.to_payload() == before
    exported = export_document(body, expected_version=SCHEMA_VERSION, allowed=True)
    assert exported.text_identity == text_hash(to_plain_text(body))


def test_layout_and_export_share_nodes_unicode_map_and_version():
    body = CanonicalDocument((
        Node("p1", "paragraph", (_segment("s1", "a\u0308"),)),
        Node("p2", "paragraph", (_segment("s2", "🙂", audio_anchor_id="a9"),)),
    ))
    exported = export_document(body, expected_version=SCHEMA_VERSION, allowed=True)
    plain, spans = body.project()
    normalised = normalise_text(plain)
    expected = tuple(
        (
            run.id,
            to_utf16_offset(normalised, map_through_normalisation(plain, spans[run.id][0])),
            to_utf16_offset(normalised, map_through_normalisation(plain, spans[run.id][1])),
        )
        for node in body.nodes for run in node.runs
    )
    assert exported.anchors == expected
    assert spans["s1"] == (0, 2) and exported.anchors[0][2] == 1
    assert spans["s2"][1] - spans["s2"][0] == 1
    assert exported.anchors[1][2] - exported.anchors[1][1] == 2
    assert exported.schema_version == body.to_payload()["schema_version"]
    assert "html_anchors" in {loss.code for loss in exported.losses}
    reloaded = reload_bundle(exported.bundle, expected_version=SCHEMA_VERSION, allowed=True)
    assert reloaded == body
    assert reloaded.to_payload()["schema_version"] == SCHEMA_VERSION
    assert "\n".join(reload_odt(exported.odt, expected_version=SCHEMA_VERSION, allowed=True)) == exported.plain
    assert reload_docx(exported.docx, expected_version=SCHEMA_VERSION, allowed=True) == exported.plain


def test_wb016_reload_keeps_umlauts_anchors_and_shows_losses():
    exported = _export()
    assert "Größe" in exported.plain and "Ärztin" in exported.plain
    assert reload_bundle(exported.bundle, expected_version=SCHEMA_VERSION, allowed=True).nodes[1].runs[0].audio_anchor_id == "a1"
    odt = "\n".join(reload_odt(exported.odt, expected_version=SCHEMA_VERSION, allowed=True))
    docx = reload_docx(exported.docx, expected_version=SCHEMA_VERSION, allowed=True)
    assert odt == docx == exported.plain
    codes = {loss.code for loss in exported.losses}
    assert {"list_plain", "odt_marks", "plain_unformatted", "html_anchors"} <= codes
    assert b"vbaProject" not in exported.bundle and b".html" not in exported.bundle
    table = import_html(
        "<p>Größe</p><table><tr><td>Zelle</td></tr></table><img src='grafik.png'>",
        expected_version=SCHEMA_VERSION, allowed=True,
    )
    assert {loss.code for loss in table.losses} == {"table", "graphic"}
    assert "<table" not in to_html(table.document) and "<img" not in to_html(table.document)


def test_original_oracles_stay_unchanged_and_not_run():
    catalog = json.loads((ROOT / "clinic-spec" / "tests" / "acceptance.json").read_text(encoding="utf-8"))
    found = {case["id"]: case for case in catalog["tests"] if case["id"] in ORACLES}
    assert set(found) == set(ORACLES)
    for case_id, then in ORACLES.items():
        case = found[case_id]
        assert tuple(case["then"]) == then
        assert case["status"] == "not_run"
        assert case.get("source_when_then_unchanged", True) is True
