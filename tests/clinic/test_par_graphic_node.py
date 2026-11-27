"""PAR-LIVE-005: a graphic node is a local object id, alt text and one plain line."""

import pytest

from src.clinic.rich_text_doc import (
    Document,
    Node,
    RichTextError,
    Run,
    from_html,
    graphic_node,
    graphic_plain_text,
    to_html,
    to_plain_text,
)

OBJECT_ID = "ab" * 32


def _ids():
    count = 0

    def factory():
        nonlocal count
        count += 1
        return f"n{count}"

    return factory


def _doc():
    return Document((
        Node("before", "paragraph", (Run("links"),)),
        graphic_node("image", OBJECT_ID, "Röntgen"),
        Node("after", "paragraph", (Run("rechts"),)),
    ))


def test_graphic_needs_a_local_object_id_and_alt_text():
    node = graphic_node("image", OBJECT_ID, "Röntgen")
    assert (node.kind, node.object_id, node.alt_text, node.runs) == ("graphic", OBJECT_ID, "Röntgen", ())
    for object_id in (
        "https://example.invalid/a.png", "javascript:alert(1)", "local:" + OBJECT_ID,
        "../" + OBJECT_ID, OBJECT_ID.upper(), "ab" * 31, "",
    ):
        with pytest.raises(RichTextError, match="invalid_graphic_reference"):
            graphic_node("image", object_id, "Röntgen")
    for alt in ("", "a<b>", "a>b", "zeile\nzwei", "x" * 201):
        with pytest.raises(RichTextError, match="invalid_alt_text"):
            graphic_node("image", OBJECT_ID, alt)
    with pytest.raises(RichTextError, match="invalid_graphic"):
        Node("image", "graphic", (Run("text"),), object_id=OBJECT_ID, alt_text="Röntgen")
    with pytest.raises(RichTextError, match="invalid_graphic"):
        Node("text", "paragraph", (Run("text"),), object_id=OBJECT_ID)


def test_plain_projection_is_deterministic_and_neighbours_survive():
    doc = _doc()
    expected = f"links\n{graphic_plain_text('Röntgen')}\nrechts"
    assert to_plain_text(doc) == expected
    assert to_plain_text(doc) == expected
    html = to_html(doc)
    assert html == (
        f'<p>links</p><img src="local:{OBJECT_ID}" alt="Röntgen">'
        "<p>rechts</p>"
    )
    assert "http" not in html and "javascript" not in html
    back = from_html(html, _ids())
    assert [(node.kind, node.object_id, node.alt_text) for node in back.nodes] == [
        ("paragraph", None, None),
        ("graphic", OBJECT_ID, "Röntgen"),
        ("paragraph", None, None),
    ]
    assert to_plain_text(back) == expected


def test_unsafe_images_are_dropped_and_neighbouring_text_survives():
    raw = (
        "<p>links</p>"
        '<img src="https://example.invalid/a.png" alt="fremd">'
        '<img src="javascript:alert(1)" alt="aktiv" onerror="alert(1)">'
        '<img src="local:notahex" alt="kaputt">'
        f'<img src="local:{OBJECT_ID}" alt="Röntgen">'
        "<p>rechts</p>"
    )
    doc = from_html(raw, _ids())
    assert to_plain_text(doc) == f"links\n{graphic_plain_text('Röntgen')}\nrechts"
    assert "fremd" not in to_plain_text(doc) and "example.invalid" not in to_html(doc)
    assert "javascript" not in to_html(doc) and "onerror" not in to_html(doc)
