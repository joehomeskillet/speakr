"""Tests for src/clinic/odt_export.py (ODT file output only).

Each acceptance criterion of the brief becomes at least one test. The
tests are pure: no database, no fixtures, real bytes from render_odt.
"""

import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO

from src.clinic.odt_export import ODT_MIMETYPE, OdtExportError, render_odt

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
DC = "http://purl.org/dc/elements/1.1/"

EXPECTED_ENTRIES = {
    "mimetype",
    "META-INF/manifest.xml",
    "content.xml",
    "styles.xml",
    "meta.xml",
}


def _open(odt_bytes):
    return zipfile.ZipFile(BytesIO(odt_bytes), "r")


def _content_root(odt_bytes):
    with _open(odt_bytes) as archive:
        return ET.fromstring(archive.read("content.xml"))


def _paragraph_texts(odt_bytes):
    ns = {"text": TEXT}
    root = _content_root(odt_bytes)
    return ["".join(p.itertext()) for p in root.findall(".//text:p", ns)]


def test_zip_structure_mimetype_first_stored_without_extra():
    odt = render_odt(["Hello"])
    with _open(odt) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert set(names) == EXPECTED_ENTRIES
        info = archive.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert info.extra == b""
        assert archive.read("mimetype") == ODT_MIMETYPE.encode("ascii")


def test_content_xml_parses_with_correct_namespaces():
    root = _content_root(render_odt(["Erster"]))
    assert root.tag == f"{{{OFFICE}}}document-content"
    ns = {"office": OFFICE, "text": TEXT}
    assert root.find(".//office:body/office:text", ns) is not None


def test_manifest_lists_every_entry():
    odt = render_odt(["Erster", "Zweiter"])
    with _open(odt) as archive:
        manifest = ET.fromstring(archive.read("META-INF/manifest.xml"))
    listed = {
        entry.get(f"{{{MANIFEST}}}full-path")
        for entry in manifest.findall(f"{{{MANIFEST}}}file-entry")
    }
    assert EXPECTED_ENTRIES <= listed


def test_each_paragraph_is_one_text_p_and_empty_is_kept():
    root = _content_root(render_odt(["Erster", "", "Dritter"]))
    ns = {"text": TEXT, "office": OFFICE}
    paragraphs = root.findall(".//office:text/text:p", ns)
    assert len(paragraphs) == 3
    assert paragraphs[0].text == "Erster"
    assert paragraphs[1].text is None
    assert paragraphs[2].text == "Dritter"


def test_special_characters_are_escaped_not_written_raw():
    cases = ["A & B", "C < D", "E > F", 'G " H', "I ' J"]
    texts = _paragraph_texts(render_odt(cases))
    assert texts == cases
    with _open(render_odt(["A & B", "C < D"])) as archive:
        raw = archive.read("content.xml")
    assert b"A &amp; B" in raw
    assert b"C &lt; D" in raw


def test_illegal_xml_1_0_characters_are_removed():
    texts = _paragraph_texts(render_odt(["A\x00B", "C\x1fD", "E\x7fF", "G\x85H"]))
    assert texts == ["AB", "CD", "EF", "GH"]


def test_tab_and_newline_are_not_removed_but_marked_up():
    root = _content_root(render_odt(["E\tF", "G\nH"]))
    ns = {"text": TEXT}
    paragraphs = root.findall(".//text:p", ns)
    assert len(paragraphs[0].findall("text:tab", ns)) == 1
    assert len(paragraphs[1].findall("text:line-break", ns)) == 1


def test_space_runs_become_text_s_with_count():
    ns = {"text": TEXT}
    cases = [("A  B", "2"), ("C   D", "3"), ("E    F", "4")]
    paragraphs = _content_root(render_odt([case for case, _ in cases])).findall(
        ".//text:p", ns
    )
    for paragraph, (_, count) in zip(paragraphs, cases):
        elements = paragraph.findall("text:s", ns)
        assert len(elements) == 1
        assert elements[0].get(f"{{{TEXT}}}c") == count


def test_two_separate_space_runs_and_single_space_boundary():
    root = _content_root(render_odt(["A  B  C", "A B"]))
    ns = {"text": TEXT}
    paragraphs = root.findall(".//text:p", ns)
    assert len(paragraphs[0].findall("text:s", ns)) == 2
    assert len(paragraphs[1].findall("text:s", ns)) == 0
    assert paragraphs[1].text == "A B"


def test_german_text_round_trips_exactly():
    original = "Grüezi Müller, Straße 5"
    assert _paragraph_texts(render_odt([original])) == [original]


def test_title_written_to_meta_xml_escaped():
    with _open(render_odt(["Body"], title="Titel & <Zusatz>")) as archive:
        meta = ET.fromstring(archive.read("meta.xml"))
    titles = meta.findall(f".//{{{DC}}}title")
    assert len(titles) == 1
    assert titles[0].text == "Titel & <Zusatz>"


def test_empty_title_omits_dc_title():
    with _open(render_odt(["Body"], title="")) as archive:
        meta = ET.fromstring(archive.read("meta.xml"))
    assert meta.findall(f".//{{{DC}}}title") == []


def test_deterministic_bytes_and_fixed_date_time():
    first = render_odt(["Erster", "Zweiter"], title="Titel")
    second = render_odt(["Erster", "Zweiter"], title="Titel")
    assert first == second
    with _open(first) as archive:
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)


def test_empty_paragraph_list_yields_valid_file_with_empty_body():
    root = _content_root(render_odt([]))
    ns = {"text": TEXT, "office": OFFICE}
    assert root.findall(".//office:text/text:p", ns) == []


def test_non_str_paragraph_raises_typeerror():
    for paragraphs in ([1], [None], [True], ["ok", 3.14], [b"bytes"]):
        try:
            render_odt(paragraphs)
        except TypeError as error:
            assert "must be str" in str(error)
        else:
            assert False, f"Expected TypeError for {paragraphs!r}"


def test_non_sequence_paragraphs_raise_module_error():
    for paragraphs in ("not a sequence", None, (p for p in ["x"])):
        try:
            render_odt(paragraphs)
        except OdtExportError as error:
            assert error.code == "invalid_paragraphs"
        else:
            assert False, f"Expected OdtExportError for {paragraphs!r}"


def test_non_str_title_raises_module_error():
    try:
        render_odt(["Body"], title=123)
    except OdtExportError as error:
        assert error.code == "invalid_title"
    else:
        assert False, "Expected OdtExportError"


def test_error_code_is_stable_string():
    assert OdtExportError("bad_input").code == "bad_input"
    assert str(OdtExportError("bad_input")) == "bad_input"
    assert str(OdtExportError("bad_input", "klarer Text")) == "klarer Text"
