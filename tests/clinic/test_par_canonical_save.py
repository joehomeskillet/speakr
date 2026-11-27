"""Canonical snapshot, text, anchors and position commit in one correction transaction.

PAR-COR-002, PAR-LIVE-005. A failed save or a stale lease leaves the last
committed revision, snapshot and playback position unchanged.
"""

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.app import app, db
from src.clinic import lease, policy, pool_policy, pools
from src.clinic.correction import get_state, link_asr_segments
from src.clinic.correction_transaction import save_correction_transaction
from src.clinic.lease import LeaseConflict
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision, current_revision, history
from src.clinic.rich_text_doc import Node, RichTextError, to_plain_text
from src.clinic.richtext import CanonicalDocument, Segment, create_tables, load_document
from src.models import Group, GroupMembership, Recording

SPOKEN = "Hallo"
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _document(anchor_id, extra, *, bold=True):
    kept = Segment(id="s", text=SPOKEN, bold=bold, audio_anchor_id=anchor_id)
    runs = (kept,) if extra is None else (kept, Segment(id="t", text=extra))
    kind, level = ("heading", 1) if bold else ("paragraph", 0)
    return CanonicalDocument((Node("n", kind, runs, level),))


def _open(author_id, *, pool_id=None):
    """Committed ASR text, one audio anchor and the author's edit lease.

    The recording is new, so the pool adapter's one-dictation lookup stays unique.
    """
    create_tables()
    recording = Recording(user_id=author_id, title="canonical", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(
        recording_id=recording.id, author_user_id=author_id, pool_id=pool_id,
    )
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="c" * 64, size_bytes=1,
        storage_key="canonical-" + uuid.uuid4().hex, receipt="stored", origin_ref="synthetic",
    )
    db.session.add(original)
    db.session.flush()
    asr = add_asr_revision(dictation, original, SPOKEN, "synthetic")
    anchor, = link_asr_segments(dictation, asr, [{"start": 0, "end": 1, "text": SPOKEN}])
    held = lease.acquire(dictation.id, author_id, NOW)
    parent = _document(anchor.id, None, bold=False)
    db.session.commit()
    from src.clinic.richtext import save_document
    assert save_document(
        dictation, asr, parent, expected_version=0, actor_user_id=author_id,
        lease_generation=held.generation, now=NOW,
    ) == 1
    db.session.commit()
    return SimpleNamespace(
        dictation_id=dictation.id, author_id=author_id, asr_id=asr.id,
        anchor_id=anchor.id, generation=held.generation, parent=parent,
    )


def _save(scene, user_id, document, *, generation, position, expected_state, expected_rev):
    dictation = db.session.get(ClinicDictation, scene.dictation_id)
    return save_correction_transaction(
        dictation, user_id, text=to_plain_text(document), audio_position_ms=position,
        lease_generation=generation, expected_state_version=expected_state,
        expected_text_revision_id=expected_rev, document=document,
        expected_document_version=0, now=NOW,
    )


def _reload(scene, user_id):
    db.session.expire_all()
    dictation = db.session.get(ClinicDictation, scene.dictation_id)
    return dictation, get_state(dictation, user_id), current_revision(dictation)


def test_formatted_change_commits_text_anchors_and_position(world, clinic_on):
    with app.app_context():
        scene = _open(world["user_a"])
        document = _document(scene.anchor_id, " mehr")
        revision, state = _save(
            scene, scene.author_id, document, generation=scene.generation,
            position=1500, expected_state=None, expected_rev=scene.asr_id,
        )
        db.session.commit()

        dictation, stored, head = _reload(scene, scene.author_id)
        assert head.id == revision.id
        assert head.body == "Hallo mehr"
        assert stored.audio_position_ms == 1500
        assert stored.text_revision_id == head.id
        assert stored.version == 1
        assert load_document(head.id, 1) == document
        assert load_document(head.id, 1).nodes[0].runs[0].bold is True
        assert load_document(head.id, 1).nodes[0].runs[0].audio_anchor_id == scene.anchor_id
        assert load_document(scene.asr_id, 1) == scene.parent
        assert db.session.get(type(head), scene.asr_id).body == SPOKEN
        assert [row.body for row in history(dictation)] == [SPOKEN, "Hallo mehr"]

        # Same words, new formatting: the transcript row stays, the old snapshot stays.
        formatted = CanonicalDocument((Node("n", "heading", tuple(
            replace(run, underline=True) for run in document.nodes[0].runs
        ), 1),))
        again, moved = save_correction_transaction(
            dictation, scene.author_id, text=to_plain_text(formatted), audio_position_ms=1600,
            lease_generation=scene.generation, expected_state_version=stored.version,
            expected_text_revision_id=head.id, document=formatted,
            expected_document_version=1, now=NOW,
        )
        db.session.commit()
        dictation, stored, head = _reload(scene, scene.author_id)
        assert again.id == head.id
        assert stored.audio_position_ms == 1600
        assert stored.version == 2
        assert load_document(head.id, 1) == document
        assert load_document(head.id, 2) == formatted
        assert load_document(head.id, 2).nodes[0].runs[0].underline is True
        assert load_document(head.id, 2).nodes[0].runs[0].audio_anchor_id == scene.anchor_id
        assert [row.body for row in history(dictation)] == [SPOKEN, "Hallo mehr"]
        assert moved.text_revision_id == head.id


def test_failed_save_or_stale_lease_keeps_the_last_commit(world, clinic_on, monkeypatch):
    with app.app_context():
        scene = _open(world["user_a"])
        document = _document(scene.anchor_id, " mehr")
        revision, state = _save(
            scene, scene.author_id, document, generation=scene.generation,
            position=1500, expected_state=None, expected_rev=scene.asr_id,
        )
        db.session.commit()
        human_id, generation = revision.id, scene.generation

        mismatched = _document(scene.anchor_id, " weg")
        with pytest.raises(RichTextError, match="transcript_mismatch"):
            dictation = db.session.get(ClinicDictation, scene.dictation_id)
            save_correction_transaction(
                dictation, scene.author_id, text="anderes", audio_position_ms=9,
                lease_generation=generation, expected_state_version=state.version,
                expected_text_revision_id=human_id, document=mismatched,
                expected_document_version=0, now=NOW,
            )
        dictation, stored, head = _reload(scene, scene.author_id)
        assert head.id == human_id
        assert head.body == "Hallo mehr"
        assert stored.audio_position_ms == 1500
        assert stored.version == 1
        assert load_document(human_id, 1) == document
        assert load_document(scene.asr_id, 1) == scene.parent
        assert [row.body for row in history(dictation)] == [SPOKEN, "Hallo mehr"]

        with pytest.raises(LeaseConflict):
            save_correction_transaction(
                dictation, scene.author_id, text=to_plain_text(document),
                audio_position_ms=9, lease_generation=generation + 5,
                expected_state_version=1, expected_text_revision_id=human_id,
                document=document, expected_document_version=0, now=NOW,
            )
        dictation, stored, head = _reload(scene, scene.author_id)
        assert head.id == human_id
        assert stored.audio_position_ms == 1500
        assert load_document(human_id, 1) == document
        assert db.session.get(type(head), scene.asr_id).body == SPOKEN

        monkeypatch.setenv("CLINIC_MODE", "0")
        with pytest.raises(RichTextError, match="clinic_mode_disabled"):
            save_correction_transaction(
                dictation, scene.author_id, text="Hallo aus", audio_position_ms=9,
                lease_generation=generation, expected_state_version=1,
                expected_text_revision_id=human_id,
                document=_document(scene.anchor_id, " aus"),
                expected_document_version=0, now=NOW,
            )
        dictation, stored, head = _reload(scene, scene.author_id)
        assert head.body == "Hallo mehr"
        assert stored.audio_position_ms == 1500
        assert [row.body for row in history(dictation)] == [SPOKEN, "Hallo mehr"]


def test_assigned_typist_uses_the_correction_edit_grant(world, clinic_on):
    """A staff typist may save only with the same pool edit grant as correction_edit."""
    with app.app_context():
        author_id, typist_id = world["user_a"], world["user_b"]
        tag = uuid.uuid4().hex[:8]
        group = Group(name="p032-" + tag, created_by=author_id)
        pool = pools.ClinicAuthorPool(name="p032-" + tag, created_by=author_id)
        db.session.add_all([group, pool])
        db.session.flush()
        staff = pools.ClinicStaffPool(group_id=group.id, created_by=author_id)
        db.session.add(staff)
        db.session.flush()
        db.session.add_all([
            GroupMembership(group_id=group.id, user_id=typist_id, role="member"),
            pools.ClinicPoolRoute(
                author_pool_id=pool.id, staff_pool_id=staff.id, created_by=author_id,
            ),
        ])
        scene = _open(author_id, pool_id=pool.id)
        document = _document(scene.anchor_id, " mehr")
        revision, _state = _save(
            scene, author_id, document, generation=scene.generation,
            position=1500, expected_state=None, expected_rev=scene.asr_id,
        )
        db.session.commit()
        held, _previous = lease.takeover(scene.dictation_id, typist_id, NOW)
        db.session.commit()

        dictation = db.session.get(ClinicDictation, scene.dictation_id)
        assert pool_policy.decide(policy.build_subject(typist_id), "edit", dictation).allowed is False
        refused = _document(scene.anchor_id, " nein")
        with pytest.raises(RichTextError, match="forbidden"):
            save_correction_transaction(
                dictation, typist_id, text=to_plain_text(refused), audio_position_ms=50,
                lease_generation=held.generation, document=refused,
                expected_document_version=0, expected_text_revision_id=revision.id, now=NOW,
            )
        dictation, author_state, head = _reload(scene, author_id)
        assert head.id == revision.id
        assert head.body == "Hallo mehr"
        assert author_state.audio_position_ms == 1500
        assert get_state(dictation, typist_id) is None
        assert [row.body for row in history(dictation)] == [SPOKEN, "Hallo mehr"]

        db.session.add(pools.ClinicVisibilityGrant(
            user_id=typist_id, effect="allow", scope="pool", pool_id=pool.id,
            actions="edit", created_by=author_id,
        ))
        db.session.commit()
        dictation = db.session.get(ClinicDictation, scene.dictation_id)
        assert pool_policy.decide(policy.build_subject(typist_id), "edit", dictation).allowed is True
        accepted = _document(scene.anchor_id, " fertig")
        saved, typist_state = save_correction_transaction(
            dictation, typist_id, text=to_plain_text(accepted), audio_position_ms=2400,
            lease_generation=held.generation, document=accepted,
            expected_document_version=0, expected_text_revision_id=revision.id, now=NOW,
        )
        db.session.commit()

        dictation, author_state, head = _reload(scene, author_id)
        typist_state = get_state(dictation, typist_id)
        assert head.id == saved.id
        assert head.body == "Hallo fertig"
        assert typist_state.audio_position_ms == 2400
        assert typist_state.text_revision_id == head.id
        assert load_document(head.id, 1) == accepted
        assert load_document(revision.id, 1) == document
        assert load_document(scene.asr_id, 1) == scene.parent
        assert author_state.audio_position_ms == 1500
        assert author_state.text_revision_id == revision.id
