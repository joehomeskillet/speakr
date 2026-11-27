"""Synthetic MockKIS and DisabledAdapter fixtures.

PAR-WF-011, PAR-WF-012, PAR-EXP-001, PAR-EXP-008. A mock receipt is not
evidence of a real KIS. Only a verified single-use grant makes a staging
context trusted. UNKNOWN stays blocked until reconcile, and a retry does not
send a second document. The disabled profile refuses finalize.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.app import app, db
from src.clinic import context_grants as grants
from src.clinic import finalize_gates, kis, lease, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.kis import DELIVERED, REFUSED, UNKNOWN, DisabledAdapter, MockKisAdapter
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDelivery, ClinicDictation,
    ClinicOriginal, ClinicRelease,
)
from src.clinic.routes_output import configured_adapter
from src.clinic.workflow_finalize import finalize
from src.models import Recording, User

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
FALL_ID = "SYN-1042"
TENANT = "staging"


@pytest.fixture
def scene(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("CLINIC_PROFILE", "staging")
    with app.app_context():
        db.session.rollback()
        suffix = uuid.uuid4().hex[:10]
        user = User(username="kis" + suffix, email=suffix + "@example.invalid", password="x")
        db.session.add(user)
        db.session.flush()
        recording = Recording(user_id=user.id, title="Synthetic", status="PENDING")
        db.session.add(recording)
        db.session.flush()
        item = workflow.start(
            db.session(), "create", author_user_id=user.id, pool_id="pool",
            assignee_user_id=user.id, acceptance_required=False,
        )
        dictation = ClinicDictation(id=item.id, recording_id=recording.id, author_user_id=user.id)
        db.session.add(dictation)
        db.session.flush()
        for trigger in ("send_offline", "skip_recognition"):
            workflow.transition(db.session(), item.id, trigger)
        original = ClinicOriginal(
            dictation_id=item.id, content_hash="a" * 64, size_bytes=1,
            storage_key="original/" + suffix, receipt="stored", origin_ref="synthetic",
        )
        db.session.add(original)
        db.session.flush()
        head = revisions.add_human_revision(
            dictation, None, "Synthetischer Text.", user.id, original_version=original,
        )
        audio = ClinicAudioRevision(dictation_id=item.id, content_hash="b" * 64)
        plain = ClinicContextRevision(
            dictation_id=item.id, source_system="kis-mock", tenant_scope=TENANT,
            case_ref="SYN-0000", patient_ref="patient-plain",
        )
        db.session.add_all([audio, plain])
        db.session.flush()
        generation = lease.acquire(item.id, user.id, NOW).generation
        rule = AcceptanceLedger().publish(
            SCOPE_DICTATION, item.id, required=False, approver_user_ids=[user.id], now=NOW,
        )
        db.session.commit()
        adapter = MockKisAdapter()
        nonce = "nonce-" + uuid.uuid4().hex

        def bind(expires):
            evidence = grants.VerifiedContextGrant(
                "kis-mock", "speakr", user.id, dictation.id, "kis-mock", "operator-syn",
                TENANT, FALL_ID, "patient-syn", "bind_existing", nonce, expires,
            )
            return grants.bind_context_grant(
                "opaque-" + nonce, dictation_id=dictation.id, actor_user_id=user.id,
                tenant=TENANT, expected_version=db.session.get(ClinicDictation, dictation.id).version_counter,
                issuer="kis-mock", audience="speakr", now=NOW,
                resolve=lambda token, actor: evidence if (token, actor) == ("opaque-" + nonce, user.id) else None,
                adapter=adapter,
                authorize_binding=lambda row, actor: row.author_user_id == actor,
            )

        def run_finalize(context_id, kis_adapter):
            current = db.session.get(ClinicDictation, dictation.id)
            return finalize(
                dictation.id, actor_user_id=user.id, expected_version=current.version_counter,
                lease_generation=generation, now=NOW, authorize=lambda *_: True,
                audio_revision_id=audio.id, transcript_revision_id=head.id,
                context_revision_id=context_id, document_type="arztbrief", job_type="summarize",
                trusted_context=lambda row: finalize_gates.trusted_context(row, kis_adapter),
                open_review_flags=finalize_gates.open_review_flags,
                acceptance_policy=lambda _d: rule,
            )

        yield SimpleNamespace(
            adapter=adapter, plain=plain, bind=bind, finalize=run_finalize, dictation_id=dictation.id,
        )
        db.session.rollback()


def test_only_a_verified_single_use_grant_enables_staging_context(scene):
    assert finalize_gates.trusted_context(scene.plain, scene.adapter) is False
    for expires in (NOW, NOW + timedelta(seconds=61)):
        with pytest.raises(grants.ContextGrantError, match="invalid_grant"):
            scene.bind(expires)
    assert grants.ClinicContextGrantUse.query.count() == 0
    assert ClinicContextRevision.query.filter_by(dictation_id=scene.dictation_id).count() == 1

    bound = scene.bind(NOW + timedelta(seconds=30))
    db.session.commit()
    assert bound.case_ref == FALL_ID
    assert finalize_gates.trusted_context(bound, scene.adapter) is True
    assert finalize_gates.trusted_context(scene.plain, scene.adapter) is False
    assert finalize_gates.trusted_context(bound, DisabledAdapter()) is False
    with pytest.raises(grants.ContextGrantError, match="grant_replayed"):
        scene.bind(NOW + timedelta(seconds=30))
    assert grants.ClinicContextGrantUse.query.count() == 1


def test_ack_and_unknown_receipts_do_not_double_deliver(scene, legacy_kis_delivery):
    bound = scene.bind(NOW + timedelta(seconds=30))
    db.session.commit()
    done = scene.finalize(bound.id, scene.adapter)
    release = db.session.get(ClinicRelease, done.release_id)
    assert release.context_revision_id == bound.id

    ack_key = "ack-" + uuid.uuid4().hex
    ack = kis.deliver(release, bound, scene.adapter, ack_key, NOW)
    ack_row = ClinicDelivery.query.filter_by(idempotency_key=ack_key, target=scene.adapter.name).one()
    stored = scene.adapter.documents[ack_key]["payload"]
    assert ack.outcome == DELIVERED and ack.sent and ack.external_document_id
    assert ack_row.state == "ACKNOWLEDGED"
    assert stored["context"]["case_ref"] == FALL_ID

    unknown_adapter = MockKisAdapter(["timeout"])
    unknown_key = "unk-" + uuid.uuid4().hex
    first = kis.deliver(release, bound, unknown_adapter, unknown_key, NOW)
    retry = kis.deliver(release, bound, unknown_adapter, unknown_key, NOW)
    unknown_row = ClinicDelivery.query.filter_by(
        idempotency_key=unknown_key, target=unknown_adapter.name,
    ).one()
    assert first.outcome == UNKNOWN and first.sent
    assert retry.outcome == UNKNOWN and retry.replayed and not retry.sent
    assert unknown_row.state == "UNKNOWN"
    assert unknown_adapter.deliver_calls == 1 and len(unknown_adapter.documents) == 1
    assert ack_row.state == "ACKNOWLEDGED" and scene.adapter.deliver_calls == 1

    reconciled = kis.reconcile(unknown_row, unknown_adapter, NOW)
    after = kis.deliver(release, bound, unknown_adapter, unknown_key, NOW)
    assert reconciled.outcome == kis.RECONCILED_DELIVERED
    assert after.outcome == DELIVERED and after.replayed and not after.sent
    assert unknown_adapter.deliver_calls == 1 and len(unknown_adapter.documents) == 1
    assert unknown_row.state == "ACKNOWLEDGED"

    refused = kis.deliver(release, bound, DisabledAdapter(), "off-" + uuid.uuid4().hex, NOW)
    assert refused.outcome == REFUSED and not refused.sent
    assert ClinicDelivery.query.filter_by(target=DisabledAdapter.name).count() == 0


def test_disabled_profile_refuses_finalize(scene, monkeypatch):
    bound = scene.bind(NOW + timedelta(seconds=30))
    db.session.commit()
    monkeypatch.delenv("CLINIC_PROFILE", raising=False)
    previous = app.extensions.get("clinic_kis_adapter")
    app.extensions.pop("clinic_kis_adapter", None)
    try:
        adapter = configured_adapter(app)
        assert isinstance(adapter, DisabledAdapter) and adapter.enabled is False
        with pytest.raises(workflow.WorkflowError, match="untrusted_context"):
            scene.finalize(bound.id, adapter)
    finally:
        if previous is None:
            app.extensions.pop("clinic_kis_adapter", None)
        else:
            app.extensions["clinic_kis_adapter"] = previous
    db.session.rollback()
    assert db.session.query(ClinicRelease).filter_by(dictation_id=scene.dictation_id).count() == 0
