"""Synthetic evidence for revision-bound rich text; original BT oracles stay unchanged."""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic import lease, policy, revisions
from src.clinic.correction import link_asr_segments
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.rich_text_doc import Node, RichTextError, to_clipboard_flavors
from src.clinic.richtext import (
    MAX_TEXT,
    CanonicalDocument,
    Segment,
    create_tables,
    load_document,
    save_document,
)
from src.clinic.unicode_anchor_map import map_through_normalisation, to_utf16_offset


def test_unicode_projection_and_segment_edits():
    spoken = Segment(id="s1", text="A\u0308🙂", bold=True, audio_anchor_id="link1")
    tail = Segment(id="s2", text="Ende")
    doc = CanonicalDocument((Node("a", "heading", (spoken,), 2), Node("b", "paragraph", (tail,))))
    edited = doc.edit_text("a", 1, " & neu", insert_id="s3")
    moved = CanonicalDocument(edited.nodes[::-1])
    text, spans = moved.project()
    assert text == "Ende\nA\u0308🙂 & neu"
    assert spans == {"s2": (0, 4), "s1": (5, 8), "s3": (8, 14)}
    assert to_utf16_offset(text, spans["s1"][1]) == 9
    assert map_through_normalisation(text, spans["s1"][1]) == 7
    assert moved.nodes[1].runs[0] is spoken
    assert moved.nodes[1].runs[1].audio_anchor_id is None
    corrected = moved.edit_text("a", 0, "Änderung")
    assert corrected.nodes[1].runs[0] == replace(spoken, text="Änderung", audio_anchor_id=None)
    assert corrected.nodes[0].runs[0] is tail
    assert CanonicalDocument.from_payload(json.loads(json.dumps(moved.to_payload()))) == moved
    assert to_clipboard_flavors(moved) == {
        "text/plain": text,
        "text/html": "<p>Ende</p><h2><strong>A\u0308🙂</strong> &amp; neu</h2>",
    }
    with pytest.raises(RichTextError, match="duplicate_id"):
        doc.edit_text("a", 1, "neu", insert_id="s2")


@pytest.mark.parametrize(("run", "error"), [
    ({"href": "javascript:"}, "invalid_schema"),
    ({"text": "<script>synthetic</script>"}, "invalid_segment_text"),
    ({"text": "x" * (MAX_TEXT + 1)}, "invalid_segment_text"),
    ({"bold": 1}, "invalid_format"),
    ({"audio_anchor_id": []}, "invalid_id"),
], ids=["unsafe-link", "markup", "size", "flag", "anchor"])
def test_invalid_canonical_payload(run, error):
    payload = {"schema_version": 1, "nodes": [{"id": "n", "kind": "paragraph",
                                               "runs": [{"id": "s", "text": "Text", **run}]}]}
    with pytest.raises(RichTextError, match=error):
        CanonicalDocument.from_payload(payload)


def test_revision_bound_snapshots_and_retries(world, clinic_on, monkeypatch):
    with app.app_context():
        create_tables()
        owner = world["user_a"]
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=owner)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64,
                                  size_bytes=1, storage_key="richtext-fixture", receipt="stored",
                                  origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        asr = revisions.add_asr_revision(dictation, original, "Ä🙂", "synthetic")
        anchor, = link_asr_segments(dictation, asr, [{"start": 0, "end": 1, "text": asr.body}])
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        current = lease.acquire(dictation.id, owner, now)
        args = dict(actor_user_id=owner, lease_generation=current.generation, now=now)
        run = Segment(id="s", text="A\u0308🙂", audio_anchor_id=anchor.id)
        doc = CanonicalDocument((Node("n", "paragraph", (run,)),))
        formatted = CanonicalDocument((Node("n", "heading", (run,), 1),))
        db.session.commit()

        assert load_document(asr.id, 1) is None  # Existing plain revision remains readable.
        with pytest.raises(RichTextError, match="forbidden"):
            save_document(dictation, asr, doc, expected_version=0,
                          **{**args, "actor_user_id": world["user_admin"]})
        policy.register_grant_provider(lambda user_id: [policy.Deny(recording_ids=frozenset({world["rec_a"]}))])
        with pytest.raises(RichTextError, match="forbidden"):
            save_document(dictation, asr, doc, expected_version=0, **args)
        policy.clear_providers()
        with pytest.raises(lease.LeaseConflict):
            save_document(dictation, asr, doc, expected_version=0,
                          **{**args, "lease_generation": current.generation + 1})
        with monkeypatch.context() as env:
            env.setenv("CLINIC_MODE", "0")
            with pytest.raises(RichTextError, match="clinic_mode_disabled"):
                save_document(dictation, asr, doc, expected_version=0, **args)

        bad_anchor = CanonicalDocument((Node("n", "paragraph", (replace(run, audio_anchor_id="missing"),)),))
        with pytest.raises(RichTextError, match="invalid_audio_anchor"):
            save_document(dictation, asr, bad_anchor, expected_version=0, **args)
        with pytest.raises(RichTextError, match="transcript_mismatch"):
            save_document(dictation, asr, doc.edit_text("n", 0, "anders"), expected_version=0, **args)
        assert save_document(dictation, asr, doc, expected_version=0, **args) == 1
        db.session.rollback()
        assert load_document(asr.id, 1) is None
        assert save_document(dictation, asr, doc, expected_version=0, **args) == 1
        db.session.commit()
        assert save_document(dictation, asr, doc, expected_version=0, **args) == 1
        with pytest.raises(RichTextError, match="stale_version"):
            save_document(dictation, asr, formatted, expected_version=0, **args)
        borrowed = CanonicalDocument((Node("n", "paragraph", (replace(run, id="new"),)),))
        with pytest.raises(RichTextError, match="invalid_audio_anchor"):
            save_document(dictation, asr, borrowed, expected_version=1, **args)
        assert save_document(dictation, asr, formatted, expected_version=1, **args) == 2
        db.session.commit()
        assert load_document(asr.id, 1) == doc
        assert load_document(asr.id, 2) == formatted

        corrected = formatted.edit_text("n", 1, " neu", insert_id="s2")
        human = revisions.add_human_revision(dictation, asr, corrected.project()[0], owner)
        with pytest.raises(RichTextError, match="stale_revision"):
            save_document(dictation, asr, formatted, expected_version=1, **args)
        assert save_document(dictation, human, corrected, expected_version=0, **args) == 1
        db.session.commit()
        assert load_document(human.id, 1) == corrected
        assert asr.body == "Ä🙂"
