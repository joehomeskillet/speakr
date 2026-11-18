"""Synthetic service evidence for BT-WF-008, BT-WF-010 and BT-WF-012."""

from datetime import datetime, timezone

import pytest
from flask import Flask
from sqlalchemy import event

from src.clinic import lease, live_session, release, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.workflow_actions import apply_action
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.models.audit import AuditEvent

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)

    def add(row):
        db.session.add(row)
        db.session.flush()
        return row

    with app.app_context():
        db.create_all()
        user = add(User(username="author", email="author@example.invalid", password="synthetic"))
        other = add(User(username="typist", email="typist@example.invalid", password="synthetic"))
        recording = add(Recording(user_id=user.id, title="Synthetic"))
        item = workflow.start(db.session(), "create", author_user_id=user.id, pool_id="pool",
                              assignee_user_id=other.id, acceptance_required=True)
        dictation = add(ClinicDictation(id=item.id, recording_id=recording.id, author_user_id=user.id))
        for trigger in ("send_offline", "skip_recognition"):
            workflow.transition(db.session(), item.id, trigger)
        original = add(ClinicOriginal(dictation_id=item.id, content_hash="a" * 64, size_bytes=1,
                                      storage_key="original/test", receipt="stored", origin_ref="test"))
        head = revisions.add_human_revision(dictation, None, "Synthetischer Text.", user.id,
                                            original_version=original)
        audio = add(ClinicAudioRevision(dictation_id=item.id, content_hash="b" * 64))
        context = add(ClinicContextRevision(dictation_id=item.id, source_system="test",
                                            tenant_scope="test", case_ref="c", patient_ref="p"))
        job = add(ProcessingJob(user_id=user.id, recording_id=recording.id,
                                job_type="transcribe", status="failed"))
        generation = lease.acquire(item.id, user.id, NOW).generation
        ledger = AcceptanceLedger()
        rule = ledger.publish(SCOPE_DICTATION, item.id, required=True,
                              approver_user_ids=[user.id], now=NOW)
        db.session.commit()

        grants = {"clinic.review", "clinic.accept", "clinic.reopen"}

        def act(action, version, **overrides):
            args = dict(actor_user_id=user.id, expected_version=version, lease_generation=generation,
                        now=NOW, authorize=lambda actor, permission, target: (
                            actor in (user.id, other.id) and target.id == item.id and permission in grants
                        ), text_revision_id=head.id, reason="Bitte korrigieren",
                        acceptance_policy=lambda _target: rule)
            return apply_action(item.id, action, **(args | overrides))

        yield item, dictation, head, audio, context, job, user, other, grants, act


def test_submit_reject_and_resubmit_are_independent_of_asr_job(case):
    item, dictation, head, _, _, job, user, other, _, act = case
    assert act("submit", 0).status == workflow.TO_ACCEPT
    job.status = "completed"
    rejected = act("reject", 1).rejection
    assert (item.status, rejected.handler_id, rejected.revision_id, rejected.reason) == (
        workflow.TO_CORRECT, other.id, head.id, "Bitte korrigieren",
    )
    corrected = revisions.add_human_revision(dictation, head, "Korrigierter Text.", other.id)
    with pytest.raises(workflow.WorkflowError, match="stale_revision"):
        act("submit", 2)
    assert act("submit", 2, text_revision_id=corrected.id).version == 3
    assert item.status == workflow.TO_ACCEPT
    assert release.active_release(dictation) is None
    assert db.session.query(AuditEvent).count() == 3


def test_denies_stale_version_and_holders_leave_no_audit(case):
    item, dictation, _, _, _, _, user, other, grants, act = case
    grants.remove("clinic.review")
    with pytest.raises(workflow.WorkflowError, match="forbidden"):
        act("submit", 0)
    grants.add("clinic.review")
    with pytest.raises(workflow.WorkflowError, match="stale_version"):
        act("submit", 1)
    with pytest.raises(lease.LeaseConflict, match=lease.FOREIGN_HOLDER):
        act("submit", 0, actor_user_id=other.id)
    live_session.start(item.id, other.id, NOW)
    with pytest.raises(live_session.LiveSessionError, match=live_session.SPEECH_LOCK_HELD):
        act("submit", 0, actor_user_id=user.id)
    assert (item.status, dictation.version_counter) == (workflow.TO_WRITE, 0)
    assert db.session.query(AuditEvent).count() == 0


def test_submit_cannot_take_the_no_acceptance_path_to_done(case):
    item, dictation, *_, act = case
    workflow.set_acceptance(db.session(), item.id, False)
    with pytest.raises(workflow.WorkflowError, match="submission_requires_acceptance"):
        act("submit", 0)
    assert (item.status, dictation.version_counter) == (workflow.TO_WRITE, 0)
    assert db.session.query(AuditEvent).count() == 0


def test_reopen_preserves_release_and_rolls_back_if_audit_write_fails(case):
    item, dictation, head, audio, context, _, user, _, grants, act = case
    assert act("submit", 0).status == workflow.TO_ACCEPT
    for trigger in ("accept", "complete"):
        workflow.transition(db.session(), item.id, trigger)
    former = release.create_release(dictation, audio, head, context, head.normalised_text_hash,
                                    user.id, {"allowed": True}, NOW,
                                    document_type="test", release_type="clinical")
    db.session.commit()
    grants.remove("clinic.reopen")
    with pytest.raises(workflow.WorkflowError, match="forbidden"):
        act("reopen", 1)
    grants.add("clinic.reopen")

    def fail_insert(*_args):
        raise RuntimeError("audit write failed")

    event.listen(AuditEvent, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="audit write failed"):
            act("reopen", 1)
    finally:
        event.remove(AuditEvent, "before_insert", fail_insert)
    assert (item.status, dictation.version_counter) == (workflow.DONE, 1)
    assert release.active_release(dictation) == former
    assert db.session.query(AuditEvent).count() == 1

    assert act("reopen", 1).status == workflow.TO_CORRECT
    db.session.rollback()
    assert (item.status, dictation.version_counter) == (workflow.DONE, 1)
    assert release.active_release(dictation) == former
    assert db.session.query(AuditEvent).count() == 1

    assert act("reopen", 1).status == workflow.TO_CORRECT
    db.session.commit()
    with pytest.raises(workflow.WorkflowError, match="stale_version"):
        act("reopen", 1)
    assert release.active_release(dictation) is None
    assert db.session.get(release.ClinicRelease, former.id).transcript_revision_id == head.id
    assert db.session.query(AuditEvent).count() == 2
