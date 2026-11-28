import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic import lease, revisions
from src.clinic.correction import link_asr_segments
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.rich_text_doc import (
    Node, RichTextError, graphic_node, graphic_plain_text, to_html,
)
from src.clinic.richtext import (
    CanonicalDocument, Segment, create_tables, load_document, save_document,
)

OBJECT_ID = "ab" * 32
ALT_TEXT = "Röntgen"
PLAIN = f"links\n{graphic_plain_text(ALT_TEXT)}\nrechts"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _document(before_anchor=None, after_anchor=None):
    graphic = replace(
        graphic_node("image", OBJECT_ID, ALT_TEXT),
        text_align="center", line_spacing=1.5,
    )
    return CanonicalDocument((
        Node("before", "paragraph", (Segment(
            id="before-text", text="links", audio_anchor_id=before_anchor,
            font_family="serif", font_size_pt=12,
        ),), text_align="left", line_spacing=1.15),
        graphic,
        Node("after", "paragraph", (Segment(
            id="after-text", text="rechts", audio_anchor_id=after_anchor,
            font_family="sans-serif", font_size_pt=14,
        ),), text_align="right", line_spacing=2),
    ))


def test_graphic_layout_roundtrip_and_projection_offsets():
    document = _document()
    payload = json.loads(json.dumps(document.to_payload()))
    restored = CanonicalDocument.from_payload(payload)

    assert restored == document
    assert payload["nodes"][1]["object_id"] == OBJECT_ID
    assert payload["nodes"][1]["alt_text"] == ALT_TEXT
    assert payload["nodes"][1]["runs"] == []
    assert restored.nodes[1].text_align == "center"
    assert restored.nodes[1].line_spacing == 1.5
    assert restored.nodes[0].runs[0].font_family == "serif"
    assert restored.nodes[0].runs[0].font_size_pt == 12
    assert restored.nodes[2].text_align == "right"
    assert restored.nodes[2].line_spacing == 2

    text, spans = restored.project()
    after_start = len("links\n") + len(graphic_plain_text(ALT_TEXT)) + 1
    assert text == PLAIN
    assert spans == {"before-text": (0, 5), "after-text": (after_start, after_start + 6)}
    assert text[slice(*spans["before-text"])] == "links"
    assert text[slice(*spans["after-text"])] == "rechts"
    html = to_html(restored)
    assert f'<img src="local:{OBJECT_ID}" alt="{ALT_TEXT}">' in html
    assert "http:" not in html and "https:" not in html and "javascript:" not in html


@pytest.mark.parametrize(("field", "value", "error"), [
    ("object_id", "https://example.invalid/a.png", "invalid_graphic_reference"),
    ("object_id", "local:" + OBJECT_ID, "invalid_graphic_reference"),
    ("object_id", "../" + OBJECT_ID, "invalid_graphic_reference"),
    ("alt_text", "", "invalid_alt_text"),
    ("alt_text", "x" * 201, "invalid_alt_text"),
    ("text_align", 'center" onerror="alert(1)', "invalid_text_align"),
    ("line_spacing", 1.2, "invalid_line_spacing"),
    ("display_width", 4097, "invalid_schema"),
])
def test_graphic_payload_reuses_schema_and_bounded_layout(field, value, error):
    payload = _document().to_payload()
    payload["nodes"][1][field] = value
    with pytest.raises(RichTextError, match=error):
        CanonicalDocument.from_payload(payload)


def test_graphic_projection_counts_toward_text_bound(monkeypatch):
    monkeypatch.setattr("src.clinic.richtext.MAX_TEXT", len(PLAIN) - 1)
    with pytest.raises(RichTextError, match="document_too_large"):
        _document()


def test_snapshot_save_load_preserves_neighbouring_audio_anchors(world, clinic_on):
    with app.app_context():
        create_tables()
        owner = world["user_a"]
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=owner)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash="a" * 64, size_bytes=1,
            storage_key="graphic-node-fixture", receipt="stored", origin_ref="synthetic",
        )
        db.session.add(original)
        db.session.flush()
        revision = revisions.add_asr_revision(dictation, original, PLAIN, "synthetic")
        anchors = link_asr_segments(dictation, revision, [
            {"start": 0, "end": 1, "text": "links"},
            {"start": 1, "end": 2, "text": "rechts"},
        ])
        held = lease.acquire(dictation.id, owner, NOW)
        document = _document(anchors[0].id, anchors[1].id)

        assert save_document(
            dictation, revision, document, expected_version=0, actor_user_id=owner,
            lease_generation=held.generation, now=NOW,
        ) == 1
        db.session.commit()
        revision_id = revision.id
        db.session.expire_all()

        restored = load_document(revision_id, 1)
        assert restored == document
        assert restored.nodes[1].object_id == OBJECT_ID
        assert restored.nodes[1].alt_text == ALT_TEXT
        text, spans = restored.project()
        for node, anchor in ((restored.nodes[0], anchors[0]), (restored.nodes[2], anchors[1])):
            run = node.runs[0]
            assert run.audio_anchor_id == anchor.id
            assert spans[run.id] == (anchor.text_start, anchor.text_end)
            assert text[slice(*spans[run.id])] == run.text
