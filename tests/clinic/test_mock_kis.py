"""MockKIS, DisabledAdapter and delivery ledger.

Acceptance catalog entries stay not_run. These tests cover the synthetic,
mock-only parts of T-029, T-056 and T-065 and the context binding behind
T-013, T-030 and T-069. Nothing here is proof of a real KIS.
"""

import uuid
from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic import kis
from src.clinic.kis import (
    DELIVERED, REFUSED, REJECTED, UNKNOWN,
    DisabledAdapter, MockInProductionError, MockKisAdapter,
)
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDelivery, ClinicDictation,
    ClinicOriginal, ClinicRelease, ClinicTranscriptRevision,
)
from src.models import Recording, User

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def kis_db(monkeypatch):
    monkeypatch.delenv("CLINIC_PROFILE", raising=False)
    with app.app_context():
        db.session.rollback()
        yield
        db.session.rollback()


def _release_set(case_ref="case-1"):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="kis" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    recording = Recording(user_id=user.id, title="kis", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
    db.session.add(dictation)
    db.session.flush()
    audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + suffix, receipt="stored", origin_ref="device:test",
    )
    db.session.add_all([audio, original])
    db.session.flush()
    transcript = ClinicTranscriptRevision(
        dictation_id=dictation.id, original_version_id=original.id, kind="ASR",
        model_ref="stub-asr:1", body="text", normalised_text_hash="t" * 64,
    )
    context = ClinicContextRevision(
        dictation_id=dictation.id, source_system="kis", tenant_scope="site",
        case_ref=case_ref, patient_ref="patient-1",
    )
    db.session.add_all([transcript, context])
    db.session.flush()
    release = ClinicRelease(
        dictation_id=dictation.id, audio_revision_id=audio.id,
        transcript_revision_id=transcript.id, context_revision_id=context.id,
        text_hash=transcript.normalised_text_hash, actor_user_id=user.id,
        document_type="report", release_type="clinical", authorization_decision="{}",
    )
    db.session.add(release)
    db.session.flush()
    return release, context


def _key():
    return "key-" + uuid.uuid4().hex


def _row(key, adapter):
    return ClinicDelivery.query.filter_by(idempotency_key=key, target=adapter.name).one()


def test_disabled_adapter_refuses_and_writes_nothing(kis_db):
    release, context = _release_set()
    key = _key()
    result = kis.deliver(release, context, DisabledAdapter(), key, NOW)
    assert result.outcome == REFUSED and not result.sent
    assert ClinicDelivery.query.filter_by(idempotency_key=key).count() == 0


def test_mock_refuses_to_exist_in_production_profile(kis_db, monkeypatch):
    monkeypatch.setenv("CLINIC_PROFILE", "production")
    with pytest.raises(MockInProductionError):
        MockKisAdapter()


def test_mock_instance_is_refused_if_profile_turns_production(kis_db, monkeypatch):
    release, context = _release_set()
    adapter = MockKisAdapter()
    monkeypatch.setenv("CLINIC_PROFILE", "production")
    assert kis.deliver(release, context, adapter, _key(), NOW).outcome == REFUSED
    assert adapter.deliver_calls == 0


def test_accepted_delivery_is_recorded(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(), _key()
    result = kis.deliver(release, context, adapter, key, NOW)
    assert result.outcome == DELIVERED and result.external_document_id
    row = _row(key, adapter)
    assert row.state == "ACKNOWLEDGED" and len(row.payload_hash) == 64


def test_rejected_delivery_is_recorded(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(["reject"]), _key()
    assert kis.deliver(release, context, adapter, key, NOW).outcome == REJECTED
    assert _row(key, adapter).state == "FAILED"


def test_ledger_row_exists_before_send(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(["crash"]), _key()
    with pytest.raises(RuntimeError):
        kis.deliver(release, context, adapter, key, NOW)
    db.session.rollback()
    assert _row(key, adapter).state == "PENDING"
    # A crashed attempt may have sent: automatic retry stays blocked.
    again = kis.deliver(release, context, adapter, key, NOW)
    assert again.outcome == UNKNOWN and again.replayed
    assert adapter.deliver_calls == 1


def test_replay_with_same_key_and_payload_sends_once(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(), _key()
    first = kis.deliver(release, context, adapter, key, NOW)
    second = kis.deliver(release, context, adapter, key, NOW)
    assert second.outcome == DELIVERED and second.replayed and not second.sent
    assert second.external_document_id == first.external_document_id
    assert adapter.deliver_calls == 1 and len(adapter.documents) == 1


def test_same_key_with_different_payload_is_refused(kis_db):
    release, context = _release_set()
    other, other_context = _release_set()
    adapter, key = MockKisAdapter(), _key()
    assert kis.deliver(release, context, adapter, key, NOW).outcome == DELIVERED
    result = kis.deliver(other, other_context, adapter, key, NOW)
    assert result.outcome == REFUSED and "different payload" in result.reason
    assert adapter.deliver_calls == 1


@pytest.mark.parametrize("script", [["timeout"], ["ambiguous"], ["timeout_unsaved"]])
def test_unknown_blocks_retry_until_reconcile(kis_db, script):
    release, context = _release_set()
    adapter, key = MockKisAdapter(script), _key()
    assert kis.deliver(release, context, adapter, key, NOW).outcome == UNKNOWN
    retry = kis.deliver(release, context, adapter, key, NOW)
    assert retry.outcome == UNKNOWN and retry.replayed and not retry.sent
    assert adapter.deliver_calls == 1


def test_reconcile_to_delivered_yields_exactly_one_document(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(["timeout"]), _key()
    kis.deliver(release, context, adapter, key, NOW)
    row = _row(key, adapter)
    result = kis.reconcile(row, adapter, NOW)
    assert result.outcome == kis.RECONCILED_DELIVERED
    assert row.state == "ACKNOWLEDGED" and row.external_document_id
    after = kis.deliver(release, context, adapter, key, NOW)
    assert after.outcome == DELIVERED and not after.sent
    assert adapter.deliver_calls == 1 and len(adapter.documents) == 1


def test_reconcile_to_not_delivered_allows_one_new_attempt(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(["timeout_unsaved"]), _key()
    kis.deliver(release, context, adapter, key, NOW)
    row = _row(key, adapter)
    assert kis.reconcile(row, adapter, NOW).outcome == kis.RECONCILED_NOT_DELIVERED
    retry = kis.deliver(release, context, adapter, key, NOW)
    assert retry.outcome == DELIVERED and retry.sent
    assert adapter.deliver_calls == 2 and len(adapter.documents) == 1
    kinds = [h.idempotency_key.rsplit(":", 1)[1] for h in kis._history(row)]
    assert kinds == ["ABSENT", "RETRY"]  # append-only trail kept


def test_reconcile_unknown_lookup_stays_blocked(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(["timeout"]), _key()
    kis.deliver(release, context, adapter, key, NOW)
    row = _row(key, adapter)
    adapter.lookup = lambda *_a: kis.LOOKUP_UNKNOWN
    assert kis.reconcile(row, adapter, NOW).outcome == kis.RECONCILED_UNDETERMINED
    assert kis.deliver(release, context, adapter, key, NOW).outcome == UNKNOWN
    assert adapter.deliver_calls == 1


def test_reconcile_requires_pending_or_unknown(kis_db):
    release, context = _release_set()
    adapter, key = MockKisAdapter(), _key()
    kis.deliver(release, context, adapter, key, NOW)
    assert kis.reconcile(_row(key, adapter), adapter, NOW).outcome == "REFUSED"


@pytest.mark.parametrize(
    "marker", [{"is_active": False}, {"superseded_by_id": "x"}, {"status": "SUPERSEDED"}]
)
def test_inactive_or_superseded_release_is_refused(kis_db, marker):
    release, context = _release_set()
    for name, value in marker.items():
        setattr(release, name, value)
    adapter, key = MockKisAdapter(), _key()
    assert kis.deliver(release, context, adapter, key, NOW).outcome == REFUSED
    assert adapter.deliver_calls == 0
    assert ClinicDelivery.query.filter_by(idempotency_key=key).count() == 0


def test_context_is_taken_only_from_the_bound_revision(kis_db):
    release, context = _release_set()
    _other, other_context = _release_set(case_ref="case-2")
    adapter, key = MockKisAdapter(), _key()
    result = kis.deliver(release, other_context, adapter, key, NOW)
    assert result.outcome == REFUSED and "bound" in result.reason
    assert adapter.deliver_calls == 0


def test_payload_context_comes_from_revision_fields(kis_db):
    release, context = _release_set(case_ref="case-77")
    adapter = MockKisAdapter()
    kis.deliver(release, context, adapter, _key(), NOW)
    stored = next(iter(adapter.documents.values()))["payload"]
    assert stored["context"]["case_ref"] == "case-77"
    assert stored["context"]["context_revision_id"] == context.id


def test_context_unknown_to_kis_is_refused(kis_db):
    release, context = _release_set(case_ref="case-9")
    adapter = MockKisAdapter(known_contexts={("kis", "site", "case-1", "patient-1")})
    result = kis.deliver(release, context, adapter, _key(), NOW)
    assert result.outcome == REFUSED and "KIS" in result.reason
    assert adapter.deliver_calls == 0


def test_incomplete_context_is_refused(kis_db):
    release, context = _release_set()
    context.patient_ref = None
    assert kis.deliver(release, context, MockKisAdapter(), _key(), NOW).outcome == REFUSED


def test_naive_datetime_is_rejected(kis_db):
    release, context = _release_set()
    with pytest.raises(ValueError):
        kis.deliver(release, context, MockKisAdapter(), _key(), datetime(2026, 1, 5, 10, 0))
