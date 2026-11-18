"""Vendor KIS adapter slice (w189). Synthetic transport only, never proof of a real KIS."""

import uuid

import pytest

from src.clinic import kis
from src.clinic.kis import DELIVERED, REFUSED, UNKNOWN, payload_hash
from src.clinic.kis_vendor_adapter import (
    MAX_LOOKUP_ATTEMPTS, REQUIRED_CONTRACT_FIELDS, VendorKisAdapter,
    build_vendor_adapter, missing_contract_fields,
)
from src.clinic.models import ClinicDelivery

from test_mock_kis import NOW, _release_set, kis_db  # noqa: F401

CONTRACT = {
    "wire_protocol": "x", "endpoint_base": "x", "tls_trust_anchor": "x",
    "auth_scheme": "x", "document_type_coding": "x", "max_document_bytes": 1000,
    "connect_timeout_s": 1, "read_timeout_s": 2, "lookup_consistency": "read-your-writes",
    "external_id_format": "x",
}


class FakeTransport:
    def __init__(self, sends=(), queries=(), known=True):
        self.sends, self.queries, self.known = list(sends), list(queries), known
        self.send_calls = self.query_calls = 0
        self.last_timeout = None

    def validate_context(self, context, timeout):
        return self.known

    def send(self, payload, key, timeout):
        self.send_calls += 1
        self.last_timeout = timeout
        step = self.sends.pop(0)
        if step == "timeout":
            raise TimeoutError("lost")
        return step(payload) if callable(step) else step

    def query(self, key, timeout):
        self.query_calls += 1
        step = self.queries.pop(0)
        if step == "timeout":
            raise TimeoutError("lost")
        return step


def _accept(payload):
    return {"status": "accepted", "document_id": "D1", "version": 1,
            "content_hash": payload_hash(payload)}


def _adapter(**kw):
    return VendorKisAdapter(CONTRACT, FakeTransport(**kw), sleep=lambda s: None)


def test_incomplete_contract_stays_disabled_and_names_fields():
    assert missing_contract_fields(None) == list(REQUIRED_CONTRACT_FIELDS)
    adapter = build_vendor_adapter({"wire_protocol": "x"}, FakeTransport())
    assert not adapter.enabled
    assert "endpoint_base" in adapter.missing and "transport" not in adapter.missing
    check = adapter.check_context({"source_system": "kis"})
    assert not check.ok and "endpoint_base" in check.reason
    assert adapter.lookup("k", "h") == kis.LOOKUP_UNKNOWN
    with pytest.raises(RuntimeError):
        adapter.deliver({}, "k")
    assert "transport" in build_vendor_adapter(CONTRACT).missing


def test_disabled_vendor_adapter_writes_nothing(kis_db):  # noqa: F811
    release, context = _release_set()
    key = "k-" + uuid.uuid4().hex
    result = kis.deliver(release, context, build_vendor_adapter(), key, NOW)
    assert result.outcome == REFUSED and not result.sent
    assert ClinicDelivery.query.filter_by(idempotency_key=key).count() == 0


def test_positive_delivery_and_idempotent_replay(kis_db):  # noqa: F811
    release, context = _release_set()
    adapter, key = _adapter(sends=[_accept]), "k-" + uuid.uuid4().hex
    first = kis.deliver(release, context, adapter, key, NOW)
    assert first.outcome == DELIVERED and first.external_document_id == "D1"
    assert adapter.transport.last_timeout == 3.0
    replay = kis.deliver(release, context, adapter, key, NOW)
    assert replay.outcome == DELIVERED and replay.replayed and not replay.sent
    assert adapter.transport.send_calls == 1
    assert adapter.documents[key]["version"] == 1


def test_unauthorized_context_is_refused_before_send(kis_db):  # noqa: F811
    release, context = _release_set()
    adapter = _adapter(known=False)
    result = kis.deliver(release, context, adapter, "k-" + uuid.uuid4().hex, NOW)
    assert result.outcome == REFUSED and "unknown to KIS" in result.reason
    assert adapter.transport.send_calls == 0


def test_hash_mismatch_or_missing_version_is_unknown_not_delivered(kis_db):  # noqa: F811
    for bad in ({"status": "accepted", "document_id": "D", "version": 1, "content_hash": "0" * 64},
                {"status": "accepted", "document_id": "D", "content_hash": None}):
        release, context = _release_set()
        adapter = _adapter(sends=[bad])
        result = kis.deliver(release, context, adapter, "k-" + uuid.uuid4().hex, NOW)
        assert result.outcome == UNKNOWN


def test_timeout_is_single_attempt_then_reconcile_confirms(kis_db):  # noqa: F811
    release, context = _release_set()
    key = "k-" + uuid.uuid4().hex
    digest = None

    def present(_):
        return {"state": "present", "document_id": "D9", "version": 2, "content_hash": digest}

    adapter = _adapter(sends=["timeout"], queries=["timeout", present])
    result = kis.deliver(release, context, adapter, key, NOW)
    assert result.outcome == UNKNOWN and adapter.transport.send_calls == 1
    assert kis.deliver(release, context, adapter, key, NOW).outcome == UNKNOWN
    assert adapter.transport.send_calls == 1  # no blind re-send
    row = ClinicDelivery.query.filter_by(idempotency_key=key, target=adapter.name).one()
    digest = row.payload_hash
    adapter.transport.queries[1] = present(None)
    rec = kis.reconcile(row, adapter, NOW)
    assert rec.outcome == kis.RECONCILED_DELIVERED and rec.external_document_id == "D9"
    assert adapter.transport.query_calls == 2  # one retry after timeout


def test_lookup_stale_version_or_foreign_hash_never_confirms():
    adapter = _adapter(queries=[{"state": "present", "document_id": "D", "version": 1,
                                 "content_hash": "other"}])
    assert adapter.lookup("k", "mine") == kis.LOOKUP_UNKNOWN
    adapter = _adapter(queries=[{"state": "present", "document_id": "D", "version": 0,
                                 "content_hash": "mine"}])
    assert adapter.lookup("k", "mine") == kis.LOOKUP_UNKNOWN


def test_lookup_retries_are_bounded_and_absent_needs_documented_consistency():
    adapter = _adapter(queries=["timeout"] * 10)
    assert adapter.lookup("k", "h") == kis.LOOKUP_UNKNOWN
    assert adapter.transport.query_calls == MAX_LOOKUP_ATTEMPTS
    assert _adapter(queries=[{"state": "absent"}]).lookup("k", "h") == kis.LOOKUP_ABSENT
    weak = VendorKisAdapter(dict(CONTRACT, lookup_consistency="eventual"),
                            FakeTransport(queries=[{"state": "absent"}]), sleep=lambda s: None)
    assert weak.lookup("k", "h") == kis.LOOKUP_UNKNOWN
