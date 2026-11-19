"""Synthetic service evidence for BT-WF-009, BT-WF-011 and BT-EXP-001 (finalize slice)."""

import json
from datetime import datetime, timezone

import pytest
from flask import Flask

from src.clinic import acceptance_rules, lease, release, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicRelease,
)
from src.clinic.workflow_finalize import finalize
from src.database import db
from src.models import ProcessingJob, Recording, User

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    from src.services.job_queue import job_queue

    monkeypatch.setattr(job_queue, "_app", app)
    monkeypatch.setattr(job_queue, "_is_owner", False)

    def add(row):
        db.session.add(row)
        db.session.flush()
        return row

    with app.app_context():
        db.create_all()
        user = add(User(username="author", email="author@example.invalid", password="synthetic"))
        recording = add(Recording(user_id=user.id, title="Synthetic"))
        item = workflow.start(db.session(), "create", author_user_id=user.id, pool_id="pool",
                              assignee_user_id=user.id, acceptance_required=False)
        dictation = add(ClinicDictation(id=item.id, recording_id=recording.id,
                                        author_user_id=user.id))
        for trigger in ("send_offline", "skip_recognition"):
            workflow.transition(db.session(), item.id, trigger)
        original = add(ClinicOriginal(dictation_id=item.id, content_hash="a" * 64, size_bytes=1,
                                      storage_key="original/test", receipt="stored",
                                      origin_ref="test"))
        head = revisions.add_human_revision(dictation, None, "Synthetischer Text.", user.id,
                                            original_version=original)
        audio = add(ClinicAudioRevision(dictation_id=item.id, content_hash="b" * 64))
        context = add(ClinicContextRevision(dictation_id=item.id, source_system="test",
                                            tenant_scope="test", case_ref="c", patient_ref="p"))
        generation = lease.acquire(item.id, user.id, NOW).generation
        rule = AcceptanceLedger().publish(SCOPE_DICTATION, item.id, required=False,
                                          approver_user_ids=[user.id], now=NOW)
        db.session.commit()
        state = {"grant": True, "trusted": True, "flags": 0, "rule": rule}

        def run(version=0, **overrides):
            args = dict(
                actor_user_id=user.id, expected_version=version, lease_generation=generation,
                now=NOW, authorize=lambda *_: state["grant"], audio_revision_id=audio.id,
                transcript_revision_id=head.id, context_revision_id=context.id,
                document_type="arztbrief", job_type="summarize",
                trusted_context=lambda _c: state["trusted"],
                open_review_flags=lambda *_: state["flags"],
                acceptance_policy=lambda _d: state["rule"],
            )
            return finalize(item.id, **(args | overrides))

        yield item, dictation, head, user, recording, state, run


def _releases():
    return db.session.query(ClinicRelease).count()


def _jobs():
    return db.session.query(ProcessingJob).count()


def test_finalize_binds_versions_and_enqueues_once_after_commit(case):
    item, dictation, head, user, recording, _, run = case
    first = run()
    assert (item.status, first.version, first.replayed) == (workflow.DONE, 1, False)
    row = db.session.get(ClinicRelease, first.release_id)
    assert (row.transcript_revision_id, row.text_hash, row.document_type) == (
        head.id, head.normalised_text_hash, "arztbrief")
    job = db.session.get(ProcessingJob, first.job_id)
    assert json.loads(job.params)["clinic_release_id"] == first.release_id
    again = run()
    assert (again.replayed, again.release_id, again.job_id) == (True, first.release_id, first.job_id)
    assert (_releases(), _jobs()) == (1, 1)
    assert release.active_release(dictation).id == first.release_id


def test_retry_after_failed_enqueue_schedules_the_missing_job_once(case, monkeypatch):
    from src.services.job_queue import job_queue

    _, _, _, _, _, _, run = case
    real = job_queue.enqueue

    def boom(**_kw):
        raise RuntimeError("queue down")

    monkeypatch.setattr(job_queue, "enqueue", boom)
    with pytest.raises(RuntimeError):
        run()
    assert (_releases(), _jobs()) == (1, 0)
    monkeypatch.setattr(job_queue, "enqueue", real)
    retried = run()
    assert (retried.replayed, _releases(), _jobs()) == (True, 1, 1)


def test_manual_transcription_completes_with_failed_or_absent_asr(case):
    _, _, _, user, recording, _, run = case
    db.session.add(ProcessingJob(user_id=user.id, recording_id=recording.id,
                                 job_type="transcribe", status="failed"))
    db.session.commit()
    assert run().replayed is False


@pytest.mark.parametrize("gate, code", [
    ("grant", "forbidden"), ("trusted", "untrusted_context"), ("flags", "open_review_flags"),
])
def test_closed_gates_write_nothing(case, gate, code):
    item, dictation, _, _, _, state, run = case
    state[gate] = {"grant": False, "trusted": False, "flags": 2}[gate]
    with pytest.raises(workflow.WorkflowError, match=code):
        run()
    assert (item.status, dictation.version_counter, _releases(), _jobs()) == (
        workflow.TO_WRITE, 0, 0, 0)


def test_stale_version_and_stale_revision_are_refused(case):
    _, dictation, head, user, _, _, run = case
    with pytest.raises(workflow.WorkflowError, match="stale_version"):
        run(version=3)
    revisions.add_human_revision(dictation, head, "Neuer Text.", user.id)
    with pytest.raises(workflow.WorkflowError, match="stale_revision"):
        run()
    assert (_releases(), _jobs()) == (0, 0)


def test_asr_text_is_not_a_human_correction(case):
    _, dictation, head, user, recording, state, run = case
    # Side ASR revision next to the human head.
    original = db.session.query(ClinicOriginal).one()
    side = revisions.add_asr_revision(dictation, original, "Maschinentext.", "model-x",
                                      parent_revision=head)
    with pytest.raises(workflow.WorkflowError, match="stale_revision"):
        run(transcript_revision_id=side.id)
    # A dictation whose only text is ASR output.
    item2 = workflow.start(db.session(), "create", author_user_id=user.id, pool_id="pool",
                           assignee_user_id=user.id, acceptance_required=False)
    rec2 = Recording(user_id=user.id, title="Synthetic 2")
    db.session.add(rec2)
    db.session.flush()
    d2 = ClinicDictation(id=item2.id, recording_id=rec2.id, author_user_id=user.id)
    db.session.add(d2)
    db.session.flush()
    for trigger in ("send_offline", "skip_recognition"):
        workflow.transition(db.session(), item2.id, trigger)
    o2 = ClinicOriginal(dictation_id=item2.id, content_hash="c" * 64, size_bytes=1,
                        storage_key="original/t2", receipt="stored", origin_ref="t2")
    a2 = ClinicAudioRevision(dictation_id=item2.id, content_hash="d" * 64)
    c2 = ClinicContextRevision(dictation_id=item2.id, source_system="test",
                               tenant_scope="test", case_ref="c", patient_ref="p")
    db.session.add_all([o2, a2, c2])
    db.session.flush()
    asr = revisions.add_asr_revision(d2, o2, "Maschinentext.", "model-x")
    gen = lease.acquire(item2.id, user.id, NOW).generation
    db.session.commit()
    with pytest.raises(workflow.WorkflowError, match="human_review_required"):
        finalize(item2.id, actor_user_id=user.id, expected_version=0, lease_generation=gen,
                 now=NOW, authorize=lambda *_: True, audio_revision_id=a2.id,
                 transcript_revision_id=asr.id, context_revision_id=c2.id,
                 document_type="arztbrief", job_type="summarize", trusted_context=lambda _c: True,
                 open_review_flags=lambda *_: 0, acceptance_policy=lambda _d: state["rule"])
    assert (_releases(), _jobs()) == (0, 0)


def test_required_acceptance_needs_a_current_accept(case):
    item, dictation, head, user, _, state, run = case
    workflow.set_acceptance(db.session(), item.id, True)
    state["rule"] = AcceptanceLedger().publish(
        SCOPE_DICTATION, item.id + ":required", required=True, approver_user_ids=[user.id], now=NOW)
    for trigger in ("complete", "accept"):
        workflow.transition(db.session(), item.id, trigger)
    db.session.commit()
    with pytest.raises(workflow.WorkflowError, match="acceptance_required"):
        run()
    accept = acceptance_rules.decide(
        state["rule"].as_rule(), kind="accept", actor_id=user.id, actor_pool_ids=frozenset(),
        revision_id=head.id, reason=None, previous_assignee_id=None, now=NOW)
    assert run(acceptance_decision=accept).replayed is False
    assert (item.status, _releases(), _jobs()) == (workflow.DONE, 1, 1)
