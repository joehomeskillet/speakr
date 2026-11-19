"""Synthetic evidence for the finalize gates: W11 review markers and trusted context."""

from datetime import datetime, timezone

import pytest
from flask import Flask

from src.clinic import finalize_gates, lease, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.context_grants import ClinicContextGrantUse
from src.clinic.kis import DisabledAdapter, KisAdapter, MockKisAdapter
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicRelease,
)
from src.clinic.workflow_finalize import finalize
from src.database import db
from src.models import Recording, User

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


class _FakeProdMock(KisAdapter):
    is_mock = True


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

        def run(head_row=None, version=0, lease_generation=None, trusted=True):
            return finalize(
                item.id, actor_user_id=user.id, expected_version=version,
                lease_generation=lease_generation or generation, now=NOW,
                authorize=lambda *_: True, audio_revision_id=audio.id,
                transcript_revision_id=(head_row or head).id, context_revision_id=context.id,
                document_type="arztbrief", job_type="summarize",
                trusted_context=lambda _c: trusted,
                open_review_flags=finalize_gates.open_review_flags,
                acceptance_policy=lambda _d: rule,
            )

        yield dictation, head, original, user, context, run


def _side_marker(dictation, head, original):
    return revisions.add_asr_revision(dictation, original, "Maschinentext.", "model-x",
                                      parent_revision=head)


def test_no_marker_counts_zero_and_finalize_passes(case):
    dictation, head, _, _, _, run = case
    assert finalize_gates.open_review_flags(dictation, head) == 0
    assert run().replayed is False


def test_open_marker_blocks_finalize_and_resolution_allows_it(case):
    dictation, head, original, user, _, run = case
    side = _side_marker(dictation, head, original)
    assert finalize_gates.open_review_flags(dictation, head) == 1
    with pytest.raises(workflow.WorkflowError, match="open_review_flags"):
        run()
    assert db.session.query(ClinicRelease).count() == 0
    # Human takes the marked text into the chain: the marker is resolved.
    resolved = revisions.add_human_revision(dictation, side, "Korrigierter Text.", user.id)
    assert finalize_gates.open_review_flags(dictation, resolved) == 0
    version = dictation.version_counter
    assert run(head_row=resolved, version=version).replayed is False


def test_untrusted_context_still_blocks(case):
    _, _, _, _, _, run = case
    with pytest.raises(workflow.WorkflowError, match="untrusted_context"):
        run(trusted=False)


def test_stale_version_and_lease_still_block(case):
    _, _, _, _, _, run = case
    with pytest.raises(workflow.WorkflowError, match="stale_version"):
        run(version=5)
    with pytest.raises(Exception):
        run(lease_generation=99)
    assert db.session.query(ClinicRelease).count() == 0


def _bind(context, user):
    db.session.add(ClinicContextGrantUse(
        revision_id=context.id, nonce_digest="n" * 64, issuer="kis", audience="speakr",
        actor_user_id=user.id, subject_reference="subject", purpose="bind_existing",
        verified_at=NOW))
    db.session.flush()


def test_trusted_context_requires_bound_grant_and_enabled_adapter(case):
    _, _, _, user, context, _ = case
    adapter = MockKisAdapter()
    assert finalize_gates.trusted_context(context) is False
    assert finalize_gates.trusted_context(context, adapter) is False  # no grant use row
    _bind(context, user)
    assert finalize_gates.trusted_context(context, adapter) is True
    assert finalize_gates.trusted_context(context, DisabledAdapter()) is False


def test_trusted_context_refuses_mock_in_production(case, monkeypatch):
    _, _, _, user, context, _ = case
    _bind(context, user)
    monkeypatch.setenv("CLINIC_PROFILE", "production")
    assert finalize_gates.trusted_context(context, _FakeProdMock()) is False


def test_superseded_context_is_not_trusted(case):
    dictation, _, _, user, context, _ = case
    _bind(context, user)
    child = ClinicContextRevision(dictation_id=dictation.id, parent_revision_id=context.id,
                                  source_system="test", tenant_scope="test", case_ref="c2",
                                  patient_ref="p")
    db.session.add(child)
    db.session.flush()
    assert finalize_gates.trusted_context(context, MockKisAdapter()) is False
