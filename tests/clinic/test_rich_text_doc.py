"""Tests for the canonical rich-text document module."""

import pytest

from src.clinic.rich_text_doc import (
    Document,
    Node,
    RichTextError,
    Run,
    canonical_json,
    content_hash,
    from_html,
    from_plain_text,
    replace_text,
    to_clipboard_flavors,
    to_html,
    to_plain_text,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _uuid_factory():
    """Deterministic ID factory for tests."""
    _uuid_factory.counter += 1
    return f"node-{_uuid_factory.counter:03d}"


_uuid_factory.counter = 0


def _make_doc():
    """A sample document: heading, paragraph with mixed runs, list items."""
    _uuid_factory.counter = 0
    heading = Node(
        id=_uuid_factory(),
        kind="heading",
        runs=(Run(text="Titel", bold=True),),
        level=1,
    )
    para = Node(
        id=_uuid_factory(),
        kind="paragraph",
        runs=(
            Run(text="Normal "),
            Run(text="fett", bold=True),
            Run(text=" kursiv", italic=True),
            Run(text=" unterstrichen", underline=True),
        ),
    )
    li1 = Node(
        id=_uuid_factory(),
        kind="list_item",
        runs=(Run(text="Eins"),),
    )
    li2 = Node(
        id=_uuid_factory(),
        kind="list_item",
        runs=(Run(text="Zwei"),),
    )
    return Document(nodes=(heading, para, li1, li2))


# ─── Model validation ─────────────────────────────────────────────────────────

def test_run_validation():
    with pytest.raises(RichTextError, match="invalid_type"):
        Run(text=123)  # type: ignore[arg-type]
    with pytest.raises(RichTextError, match="control_character"):
        Run(text="a\x00b")


def test_node_validation():
    with pytest.raises(RichTextError, match="invalid_id"):
        Node(id="", kind="paragraph", runs=(Run(text="x"),))
    with pytest.raises(RichTextError, match="invalid_kind"):
        Node(id="1", kind="div", runs=(Run(text="x"),))
    with pytest.raises(RichTextError, match="invalid_heading_level"):
        Node(id="1", kind="heading", runs=(Run(text="x"),), level=0)
    with pytest.raises(RichTextError, match="invalid_heading_level"):
        Node(id="1", kind="heading", runs=(Run(text="x"),), level=4)
    with pytest.raises(RichTextError, match="invalid_level"):
        Node(id="1", kind="paragraph", runs=(Run(text="x"),), level=1)
    with pytest.raises(RichTextError, match="empty_runs"):
        Node(id="1", kind="paragraph", runs=())


def test_document_duplicate_id():
    _uuid_factory.counter = 0
    n1 = Node(id="same", kind="paragraph", runs=(Run(text="a"),))
    n2 = Node(id="same", kind="paragraph", runs=(Run(text="b"),))
    with pytest.raises(RichTextError, match="duplicate_id"):
        Document(nodes=(n1, n2))


def test_document_empty_allowed():
    doc = Document(nodes=())
    assert doc.nodes == ()


# ─── Plain text round-trip ────────────────────────────────────────────────────

def test_from_plain_text_basic():
    _uuid_factory.counter = 0
    doc = from_plain_text("Zeile 1\nZeile 2\n", _uuid_factory)
    assert len(doc.nodes) == 3  # trailing newline creates empty line
    assert all(n.kind == "paragraph" for n in doc.nodes)
    assert doc.nodes[0].runs[0].text == "Zeile 1"
    assert doc.nodes[1].runs[0].text == "Zeile 2"
    assert doc.nodes[2].runs[0].text == ""


def test_to_plain_text():
    doc = _make_doc()
    text = to_plain_text(doc)
    lines = text.split("\n")
    assert lines[0] == "Titel"
    assert lines[1] == "Normal fett kursiv unterstrichen"
    assert lines[2] == "Eins"
    assert lines[3] == "Zwei"


def test_plain_text_round_trip():
    _uuid_factory.counter = 0
    original = "Zeile 1\nZeile 2\n"
    doc = from_plain_text(original, _uuid_factory)
    result = to_plain_text(doc)
    assert result == original


# ─── HTML output ──────────────────────────────────────────────────────────────

def test_to_html_only_allowed_tags():
    doc = _make_doc()
    html = to_html(doc)
    # Only allowed tags
    assert "<p>" in html
    assert "<h1>" in html
    assert "<ul>" in html
    assert "<li>" in html
    assert "<strong>" in html
    assert "<em>" in html
    assert "<u>" in html
    # No attributes
    assert 'class=' not in html
    assert 'style=' not in html
    assert 'id=' not in html


def test_to_html_structure():
    doc = _make_doc()
    html = to_html(doc)
    # Heading
    assert "<h1><strong>Titel</strong></h1>" in html
    # Paragraph with mixed runs - runs contain their own spacing
    assert "Normal <strong>fett</strong><em> kursiv</em><u> unterstrichen</u>" in html
    # List
    assert "<ul><li>Eins</li><li>Zwei</li></ul>" in html


def test_to_html_escapes_special_chars():
    _uuid_factory.counter = 0
    node = Node(id=_uuid_factory(), kind="paragraph", runs=(Run(text="A < B & C > D"),))
    doc = Document(nodes=(node,))
    html = to_html(doc)
    # Text is HTML-escaped
    assert "A &lt; B &amp; C &gt; D" in html


def test_to_html_newline_becomes_br():
    _uuid_factory.counter = 0
    node = Node(id=_uuid_factory(), kind="paragraph", runs=(Run(text="Line 1\nLine 2"),))
    doc = Document(nodes=(node,))
    html = to_html(doc)
    assert "<br>" in html


# ─── HTML parsing (sanitising) ────────────────────────────────────────────────

def test_from_html_drops_script_style_iframe_object():
    hostile = (
        "<script>alert(1)</script>"
        "<style>body{display:none}</style>"
        "<iframe src='evil'></iframe>"
        "<object data='evil'></object>"
        "<p>Safe</p>"
    )
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    assert "script" not in html.lower()
    assert "style" not in html.lower()
    assert "iframe" not in html.lower()
    assert "object" not in html.lower()
    assert "Safe" in html


def test_from_html_strips_event_handlers():
    hostile = '<p onclick="evil()">Text</p>'
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    assert "onclick" not in html
    assert "Text" in html


def test_from_html_strips_style_attr():
    hostile = '<p style="color:red">Text</p>'
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    assert "style" not in html
    assert "Text" in html


def test_from_html_strips_javascript_urls():
    hostile = '<a href="javascript:alert(1)">Link</a><p>After</p>'
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    assert "javascript" not in html.lower()
    assert "Link" in html
    assert "After" in html


def test_from_html_drops_unknown_tags_keeps_text():
    hostile = "<custom>Keep me</custom><p>Normal</p>"
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    assert "custom" not in html.lower()
    assert "Keep me" in html
    assert "Normal" in html


def test_from_html_maps_b_i_u_to_runs():
    hostile = "<p><b>Bold</b> <i>Italic</i> <u>Under</u></p>"
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    assert "<strong>Bold</strong>" in html
    assert "<em>Italic</em>" in html
    assert "<u>Under</u>" in html


# ─── Round trip: from_html(to_html(doc)) ──────────────────────────────────────

def test_html_round_trip_structure():
    doc = _make_doc()
    html = to_html(doc)
    _uuid_factory.counter = 0
    doc2 = from_html(html, _uuid_factory)

    # Structure equality: kinds, texts, flags (ids differ)
    assert len(doc2.nodes) == len(doc.nodes)
    for n1, n2 in zip(doc.nodes, doc2.nodes):
        assert n1.kind == n2.kind
        assert n1.level == n2.level
        assert len(n1.runs) == len(n2.runs)
        for r1, r2 in zip(n1.runs, n2.runs):
            assert r1.text == r2.text
            assert r1.bold == r2.bold
            assert r1.italic == r2.italic
            assert r1.underline == r2.underline


# ─── replace_text ─────────────────────────────────────────────────────────────

def test_replace_text_keeps_id_and_formatting():
    doc = _make_doc()
    para_id = doc.nodes[1].id  # paragraph with mixed runs
    new_doc = replace_text(doc, para_id, "Neuer Text")
    # Node id unchanged
    assert new_doc.nodes[1].id == para_id
    # Only that node changed
    assert new_doc.nodes[1].runs[0].text == "Neuer Text"
    # First run's formatting preserved (first run has no formatting)
    assert new_doc.nodes[1].runs[0].bold is False
    assert new_doc.nodes[1].runs[0].italic is False
    assert new_doc.nodes[1].runs[0].underline is False
    # Other nodes identical
    assert new_doc.nodes[0] == doc.nodes[0]
    assert new_doc.nodes[2] == doc.nodes[2]
    assert new_doc.nodes[3] == doc.nodes[3]


def test_replace_text_unknown_id_raises():
    doc = _make_doc()
    with pytest.raises(RichTextError, match="unknown_node"):
        replace_text(doc, "non-existent", "x")


def test_replace_text_validation():
    doc = _make_doc()
    with pytest.raises(RichTextError, match="control_character"):
        replace_text(doc, doc.nodes[0].id, "a\x00b")


# ─── Clipboard flavours ───────────────────────────────────────────────────────

def test_to_clipboard_flavors_keys():
    doc = _make_doc()
    flavors = to_clipboard_flavors(doc)
    assert set(flavors.keys()) == {"text/plain", "text/html"}


def test_clipboard_text_equals_plain_text():
    doc = _make_doc()
    flavors = to_clipboard_flavors(doc)
    assert flavors["text/plain"] == to_plain_text(doc)


def test_clipboard_html_equals_to_html():
    doc = _make_doc()
    flavors = to_clipboard_flavors(doc)
    assert flavors["text/html"] == to_html(doc)


# ─── Canonical JSON & content hash ────────────────────────────────────────────

def test_canonical_json_stable():
    doc = _make_doc()
    json1 = canonical_json(doc)
    json2 = canonical_json(doc)
    assert json1 == json2
    # Sorted keys
    assert json1.index("id") < json1.index("kind")


def test_content_hash_changes_on_run_flag():
    doc = _make_doc()
    hash1 = content_hash(doc)
    # Change a run flag
    _uuid_factory.counter = 0
    heading = Node(
        id=_uuid_factory(),
        kind="heading",
        runs=(Run(text="Titel", bold=False),),  # bold removed
        level=1,
    )
    para = Node(
        id=_uuid_factory(),
        kind="paragraph",
        runs=(
            Run(text="Normal "),
            Run(text="fett", bold=True),
            Run(text=" kursiv", italic=True),
            Run(text=" unterstrichen", underline=True),
        ),
    )
    li1 = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="Eins"),))
    li2 = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="Zwei"),))
    doc2 = Document(nodes=(heading, para, li1, li2))
    hash2 = content_hash(doc2)
    assert hash1 != hash2


def test_content_hash_changes_on_node_id():
    _uuid_factory.counter = 0
    doc1 = _make_doc()
    # Create a second doc with different IDs by not using _make_doc (which resets counter)
    _uuid_factory.counter = 100
    heading = Node(
        id=_uuid_factory(),
        kind="heading",
        runs=(Run(text="Titel", bold=True),),
        level=1,
    )
    para = Node(
        id=_uuid_factory(),
        kind="paragraph",
        runs=(
            Run(text="Normal "),
            Run(text="fett", bold=True),
            Run(text=" kursiv", italic=True),
            Run(text=" unterstrichen", underline=True),
        ),
    )
    li1 = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="Eins"),))
    li2 = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="Zwei"),))
    doc2 = Document(nodes=(heading, para, li1, li2))
    assert content_hash(doc1) != content_hash(doc2)


def test_content_hash_same_for_identical_structure():
    _uuid_factory.counter = 0
    doc1 = _make_doc()
    _uuid_factory.counter = 0
    doc2 = _make_doc()
    assert content_hash(doc1) == content_hash(doc2)


# ─── Umlauts and emoji ────────────────────────────────────────────────────────

def test_umlauts_survive_conversions():
    _uuid_factory.counter = 0
    text = "Äpfel, Öfen, Übel, ß"
    doc = from_plain_text(text, _uuid_factory)
    html = to_html(doc)
    back = from_html(html, lambda: _uuid_factory())
    result = to_plain_text(back)
    assert "Äpfel" in result
    assert "Öfen" in result
    assert "Übel" in result
    assert "ß" in result


def test_emoji_survives_conversions():
    _uuid_factory.counter = 0
    text = "😀 🎉 🚀"
    doc = from_plain_text(text, _uuid_factory)
    html = to_html(doc)
    back = from_html(html, lambda: _uuid_factory())
    result = to_plain_text(back)
    assert "😀" in result
    assert "🎉" in result
    assert "🚀" in result


# ─── Additional edge cases ────────────────────────────────────────────────────

def test_heading_levels_1_to_3():
    _uuid_factory.counter = 0
    for level in (1, 2, 3):
        node = Node(id=_uuid_factory(), kind="heading", runs=(Run(text="H"),), level=level)
        doc = Document(nodes=(node,))
        html = to_html(doc)
    assert f"<h{level}>" in html


def test_nested_formatting_in_html_parsing():
    hostile = "<p><b>Bold <i>Bold+Italic</i></b> Normal</p>"
    _uuid_factory.counter = 0
    doc = from_html(hostile, _uuid_factory)
    html = to_html(doc)
    # The parser merges adjacent runs with same formatting
    assert "<strong>Bold </strong>" in html
    # Nested formatting: both <strong> and <em> present (order may vary)
    assert "Bold+Italic" in html
    assert "<strong>" in html and "<em>" in html
    assert "Normal" in html


def test_list_items_wrapped_in_ul():
    _uuid_factory.counter = 0
    li1 = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="A"),))
    li2 = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="B"),))
    doc = Document(nodes=(li1, li2))
    html = to_html(doc)
    assert html == "<ul><li>A</li><li>B</li></ul>"


def test_paragraph_after_list_closes_ul():
    _uuid_factory.counter = 0
    li = Node(id=_uuid_factory(), kind="list_item", runs=(Run(text="A"),))
    para = Node(id=_uuid_factory(), kind="paragraph", runs=(Run(text="B"),))
    doc = Document(nodes=(li, para))
    html = to_html(doc)
    assert html == "<ul><li>A</li></ul><p>B</p>"


def test_empty_document_serialises_to_empty():
    doc = Document(nodes=())
    assert to_html(doc) == ""
    assert to_plain_text(doc) == ""
    assert canonical_json(doc) == '{"nodes":[]}'
    hash_val = content_hash(doc)
    assert len(hash_val) == 64  # sha256 hex


def test_from_plain_text_preserves_empty_lines():
    _uuid_factory.counter = 0
    doc = from_plain_text("Line 1\n\nLine 3", _uuid_factory)
    assert len(doc.nodes) == 3
    assert doc.nodes[1].runs[0].text == ""


def test_replace_text_on_heading_preserves_level():
    doc = _make_doc()
    heading_id = doc.nodes[0].id
    new_doc = replace_text(doc, heading_id, "New Title")
    assert new_doc.nodes[0].level == 1
    assert new_doc.nodes[0].kind == "heading"