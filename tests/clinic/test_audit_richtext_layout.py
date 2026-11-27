"""P-009: bounded document layout, legacy reads and stable audio anchors."""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic import lease, revisions
from src.clinic.correction import link_asr_segments
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.rich_text_doc import (
    Document, Node, RichTextError, Run, canonical_json, content_hash, replace_text,
)
from src.clinic.richtext import (
    CanonicalDocument, ClinicRichTextSnapshot, create_tables,
    load_document, save_document,
)


def legacy_payload():
    return {"schema_version": 1, "nodes": [{"id": "n", "kind": "paragraph",
            "runs": [{"id": "s", "text": "Ä🙂", "audio_anchor_id": "anchor"}]}]}


@pytest.mark.parametrize(("field", "value"), [
    ("font_family", "serif"), ("font_family", "sans-serif"), ("font_family", "monospace"),
    ("font_size_pt", 8), ("font_size_pt", 12), ("font_size_pt", 72),
    ("text_align", "left"), ("text_align", "center"), ("text_align", "right"),
    ("text_align", "justify"),
    *[("line_spacing", value) for value in (1, 1.15, 1.5, 2, 2.5, 3)],
])
def test_layout_roundtrip_preserves_identity_and_projection(field, value):
    old = CanonicalDocument.from_payload(legacy_payload())
    payload = legacy_payload()
    node = payload["nodes"][0]
    target = node["runs"][0] if field.startswith("font_") else node
    target[field] = value
    doc = CanonicalDocument.from_payload(payload)
    assert CanonicalDocument.from_payload(json.loads(json.dumps(doc.to_payload()))) == doc
    assert doc.project() == old.project()
    assert doc.nodes[0].id == old.nodes[0].id
    assert doc.nodes[0].runs[0].id == "s"
    assert doc.nodes[0].runs[0].audio_anchor_id == "anchor"
    assert content_hash(doc) != content_hash(old)


@pytest.mark.parametrize(("field", "value", "error"), [
    ("font_family", 'serif; background:url(https://example.invalid)', "invalid_font_family"),
    ("font_family", '<script>alert(1)</script>', "invalid_font_family"),
    ("font_family", [], "invalid_font_family"),
    *[("font_size_pt", v, "invalid_font_size") for v in (7, 73, True, 12.0, "12px", {})],
    ("text_align", 'left" onclick="alert(1)', "invalid_text_align"),
    ("text_align", [], "invalid_text_align"),
    *[("line_spacing", v, "invalid_line_spacing") for v in
      (0, 4, 1.2, True, "1.5", "150%", [], float("nan"), float("inf"))],
    ("display_font_size", 24, "invalid_schema"),
    ("style", "font-size:24px", "invalid_schema"),
])
def test_payload_rejects_unbounded_layout_and_display_preferences(field, value, error):
    payload = legacy_payload()
    node = payload["nodes"][0]
    target = node["runs"][0] if field.startswith("font_") else node
    target[field] = value
    with pytest.raises(RichTextError, match=error):
        CanonicalDocument.from_payload(payload)


def test_legacy_defaults_hash_and_text_edits():
    old = CanonicalDocument.from_payload(legacy_payload())
    assert old.nodes[0].text_align is None
    assert old.nodes[0].line_spacing is None
    assert old.nodes[0].runs[0].font_family is None
    assert old.nodes[0].runs[0].font_size_pt is None
    plain = Document((Node("n", "paragraph", (Run("text"),)),))
    assert canonical_json(plain) == (
        '{"nodes":[{"id":"n","kind":"paragraph","level":0,'
        '"runs":[{"bold":false,"italic":false,"text":"text","underline":false}]}]}'
    )
    run = replace(old.nodes[0].runs[0], font_family="serif", font_size_pt=14)
    node = replace(old.nodes[0], runs=(run,), text_align="center", line_spacing=1.5)
    doc = CanonicalDocument((node,))
    edited = doc.edit_text("n", 0, "new")
    assert edited.nodes[0] == replace(node, runs=(replace(run, text="new", audio_anchor_id=None),))
    legacy_edit = replace_text(Document((node,)), "n", "new")
    assert legacy_edit.nodes[0].text_align == "center"
    assert legacy_edit.nodes[0].line_spacing == 1.5
    assert legacy_edit.nodes[0].runs[0].font_family == "serif"
    assert legacy_edit.nodes[0].runs[0].font_size_pt == 14


def test_layout_snapshot_preserves_legacy_revision_and_audio_anchor(world, clinic_on):
    with app.app_context():
        create_tables()
        owner = world["user_a"]
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=owner)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64,
                                  size_bytes=1, storage_key="layout-fixture", receipt="stored",
                                  origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        revision = revisions.add_asr_revision(dictation, original, "Ä🙂", "synthetic")
        anchor, = link_asr_segments(dictation, revision, [{"start": 0, "end": 1, "text": "Ä🙂"}])
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        current = lease.acquire(dictation.id, owner, now)
        payload = legacy_payload()
        payload["nodes"][0]["runs"][0]["audio_anchor_id"] = anchor.id
        db.session.add(ClinicRichTextSnapshot(revision_id=revision.id, version=1, payload=payload))
        db.session.commit()
        old = load_document(revision.id, 1)
        run = replace(old.nodes[0].runs[0], font_family="sans-serif", font_size_pt=12)
        doc = CanonicalDocument((replace(old.nodes[0], runs=(run,), text_align="justify", line_spacing=2),))
        args = dict(actor_user_id=owner, lease_generation=current.generation, now=now)
        assert save_document(dictation, revision, doc, expected_version=1, **args) == 2
        db.session.commit()
        db.session.expire_all()
        assert load_document(revision.id, 1) == old
        assert load_document(revision.id, 2) == doc
        assert doc.project() == old.project()
        assert doc.nodes[0].runs[0].audio_anchor_id == anchor.id
        assert save_document(dictation, revision, doc, expected_version=1, **args) == 2
