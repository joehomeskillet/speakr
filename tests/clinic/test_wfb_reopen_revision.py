"""Reopen keeps the released revision and starts a distinct working history."""

import pytest
from sqlalchemy import event

from src.clinic import finalize_gates, lease, release, revisions, workflow
from src.clinic.acceptance_policy import AcceptanceLedger, SCOPE_DICTATION
from src.clinic.correction_transaction import save_correction_transaction
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicRelease
from src.clinic.richtext import CanonicalDocument, ClinicRichTextSnapshot, load_document
from src.clinic.routes_history import _history
from src.clinic.workflow_actions import apply_action
from src.clinic.workflow_finalize import finalize
from src.database import db
from src.models import ProcessingJob, Recording
from src.models.audit import AuditEvent
from src.services.job_queue import job_queue
from test_wp_workflow_actions import NOW, case  # noqa: F401 (shared fixture)


def test_reopen_keeps_release_and_requires_acceptance_for_the_new_revision(case, monkeypatch):
    item, dictation, head, audio, context, _, user, _, _, act = case
    monkeypatch.setattr(job_queue, "_is_owner", False)
    monkeypatch.setattr(job_queue, "_app", None)
    case_rule = AcceptanceLedger().current(SCOPE_DICTATION, item.id)

    def finish(text, decision, **checks):
        gates = dict(trusted_context=lambda _: True, open_review_flags=finalize_gates.open_review_flags)
        return finalize(
            item.id, actor_user_id=user.id, expected_version=dictation.version_counter,
            lease_generation=lease.current(item.id).generation, now=NOW, authorize=lambda *_: True,
            audio_revision_id=audio.id, transcript_revision_id=text.id, context_revision_id=context.id,
            document_type="report", job_type="summarize", acceptance_decision=decision,
            acceptance_policy=lambda _: case_rule, **(gates | checks),
        )

    document = {
        "schema_version": 1, "nodes": [{"id": "p1", "kind": "paragraph", "runs": [
            {"id": "s1", "text": head.body, "bold": True},
        ]}],
    }
    db.session.add(ClinicRichTextSnapshot(revision_id=head.id, version=2, payload=document))
    assert act("submit", 0).status == workflow.TO_ACCEPT
    first_decision = act("accept", 1).acceptance
    first = finish(head, first_decision)
    former_id = first.release_id
    db.session.get(ProcessingJob, first.job_id).status = "completed"
    db.session.commit()
    original_release = db.session.get(ClinicRelease, former_id)
    released_hash = original_release.text_hash

    reopened = act("reopen", 3)
    db.session.commit()
    working = revisions.current_revision(dictation)
    assert reopened.status == workflow.TO_CORRECT
    assert working.id == reopened.text_revision_id and working.id != head.id
    assert working.parent_revision_id == head.id
    assert (working.body, working.normalised_text_hash, working.original_version_id) == (
        head.body, released_hash, head.original_version_id,
    )
    assert working.revision_author_user_id == user.id
    assert load_document(working.id, 1) == load_document(head.id, 2) == CanonicalDocument.from_payload(document)
    events = _history(dictation)
    linked = next(row for row in events if row['id'] == working.id)
    released = next(row for row in events if row['id'] == former_id)
    assert linked['parent_revision_id'] == released['transcript_revision_id'] == head.id
    assert release.active_release(dictation) is None

    workflow.transition(db.session(), item.id, "open")
    held = lease.current(item.id)
    corrected, state = save_correction_transaction(
        dictation, user.id, text="Korrigierter Text.", audio_position_ms=500,
        lease_generation=held.generation, expected_text_revision_id=working.id, now=NOW,
    )
    corrected_id = corrected.id
    workflow.transition(db.session(), item.id, "close")
    lease.release(held, held.generation, NOW)
    db.session.commit()
    db.session.expire_all()

    next_lease = lease.acquire(item.id, user.id, NOW)
    workflow.transition(db.session(), item.id, "open")
    assert (state.text_revision_id, revisions.current_revision(dictation).body) == (corrected_id, "Korrigierter Text.")
    assert corrected.parent_revision_id == working.id
    version = dictation.version_counter
    with pytest.raises(workflow.WorkflowError, match="acceptance_required"):
        finish(corrected, first_decision)
    assert act("submit", version, text_revision_id=corrected_id,
               lease_generation=next_lease.generation).status == workflow.TO_ACCEPT
    fresh = act("accept", version + 1, text_revision_id=corrected_id,
                lease_generation=next_lease.generation).acceptance
    with pytest.raises(workflow.WorkflowError, match="acceptance_required"):
        finish(corrected, first_decision)
    with pytest.raises(workflow.WorkflowError, match="untrusted_context"):
        finish(corrected, fresh, trusted_context=lambda _: False)
    new = finish(corrected, fresh)
    assert new.release_id != former_id
    assert release.active_release(dictation).transcript_revision_id == corrected_id
    assert (original_release.transcript_revision_id, original_release.text_hash, head.body) == (
        head.id, released_hash, "Synthetischer Text.",
    )


def test_reopen_revision_rolls_back_with_workflow_and_release(case):
    item, dictation, head, audio, context, _, user, _, _, act = case
    workflow.set_acceptance(db.session(), item.id, False)
    workflow.transition(db.session(), item.id, "complete")
    former = release.create_release(dictation, audio, head, context, head.normalised_text_hash,
                                    user.id, {"allowed": True}, NOW,
                                    document_type="report", release_type="clinical")
    db.session.commit()

    def fail_audit(*_args):
        raise RuntimeError("audit insert interrupted")

    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="audit insert interrupted"):
            act("reopen", 0)
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)
    assert (item.status, dictation.version_counter) == (workflow.DONE, 0)
    assert revisions.history(dictation) == [head]
    assert release.active_release(dictation) == former

    assert act("reopen", 0).text_revision_id != head.id
    db.session.rollback()
    assert (item.status, dictation.version_counter) == (workflow.DONE, 0)
    assert revisions.history(dictation) == [head]
    assert release.active_release(dictation) == former


def test_reopen_legacy_asr_head_does_not_claim_human_review(case):
    item, _, _, _, _, _, user, _, _, _ = case
    legacy = workflow.start(db.session(), "create", author_user_id=user.id, pool_id=item.pool_id)
    row = ClinicOriginal(dictation_id=legacy.id, content_hash="c" * 64, size_bytes=1,
                         storage_key="original/legacy", receipt="stored", origin_ref="legacy")
    recording = Recording(user_id=user.id, title="Legacy case")
    db.session.add(recording)
    db.session.flush()
    legacy_dictation = ClinicDictation(id=legacy.id, recording_id=recording.id, author_user_id=user.id)
    db.session.add(legacy_dictation)
    db.session.flush()
    db.session.add(row)
    db.session.flush()
    asr = revisions.add_asr_revision(legacy_dictation, row, "Maschinentext.", "legacy-asr:1")
    for trigger in ("send_offline", "skip_recognition", "complete"):
        workflow.transition(db.session(), legacy.id, trigger)
    held = lease.acquire(legacy.id, user.id, NOW)
    db.session.commit()

    result = apply_action(legacy.id, "reopen", actor_user_id=user.id, expected_version=0,
                          lease_generation=held.generation, now=NOW, authorize=lambda *_: True,
                          reason="Zur Korrektur")
    working = revisions.current_revision(legacy_dictation)
    assert working.id == result.text_revision_id and working.id != asr.id
    assert (working.parent_revision_id, working.kind, working.model_ref, working.body) == (
        asr.id, revisions.KIND_ASR, "legacy-asr:1", "Maschinentext.",
    )
    with pytest.raises(workflow.WorkflowError, match="human_review_required"):
        apply_action(legacy.id, "submit", actor_user_id=user.id, expected_version=1,
                     lease_generation=held.generation, now=NOW, authorize=lambda *_: True,
                     text_revision_id=working.id)
