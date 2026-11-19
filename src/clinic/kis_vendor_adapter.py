"""Manufacturer KIS delivery adapter, disabled until a documented contract exists.

The public contract (clinic-evidence/kis/vendor-contract.md, section 3) lacks
every manufacturer detail below. No endpoint, protocol or schema is invented
here and no UI automation is used. The adapter becomes enabled only when a
caller supplies ALL contract fields and a transport that implements the
documented interface; until then it behaves like DisabledAdapter and reports
the exact missing fields.

A mock transport in tests is never proof of a real KIS (G-KIS stays open).
"""

import time
from typing import Optional

from .kis import (
    LOOKUP_ABSENT, LOOKUP_CONFIRMED, LOOKUP_UNKNOWN,
    AdapterAnswer, AdapterTimeout, ContextCheck, KisAdapter, payload_hash,
)

# Manufacturer details that must come from an executed interface agreement.
REQUIRED_CONTRACT_FIELDS = (
    "wire_protocol",
    "endpoint_base",
    "tls_trust_anchor",
    "auth_scheme",
    "document_type_coding",
    "max_document_bytes",
    "connect_timeout_s",
    "read_timeout_s",
    "lookup_consistency",
    "external_id_format",
)

MAX_LOOKUP_ATTEMPTS = 3  # bounded retries, read-only lookup only
MAX_LOOKUP_WINDOW_S = 30.0


def missing_contract_fields(contract: Optional[dict]) -> list:
    """Return the required manufacturer fields that are absent or empty."""
    contract = contract or {}
    return [f for f in REQUIRED_CONTRACT_FIELDS if contract.get(f) in (None, "", 0)]


class VendorKisAdapter(KisAdapter):
    """Thin delivery adapter over an injected, documented transport.

    transport must provide (all timeouts in seconds, raising TimeoutError on loss):
      validate_context(context, timeout) -> bool
      send(payload, idempotency_key, timeout) -> dict with status, document_id,
          version, content_hash (status accepted|rejected)
      query(idempotency_key, timeout) -> dict with state present|absent|unknown,
          document_id, version, content_hash
    """

    name = "kis-vendor"

    def __init__(self, contract: Optional[dict] = None, transport=None, sleep=time.sleep):
        self.contract = dict(contract or {})
        self.transport = transport
        self.missing = missing_contract_fields(self.contract)
        if transport is None:
            self.missing = self.missing + ["transport"]
        self.enabled = not self.missing
        self.documents = {}  # idempotency_key -> {"id", "version", "payload_hash"}
        self.deliver_calls = 0
        self._sleep = sleep

    def blocked_reason(self) -> str:
        return "KIS vendor contract incomplete: " + ", ".join(self.missing) if self.missing else ""

    def _timeout(self) -> float:
        return float(self.contract["connect_timeout_s"]) + float(self.contract["read_timeout_s"])

    def check_context(self, context: dict) -> ContextCheck:
        if not self.enabled:
            return ContextCheck(False, self.blocked_reason())
        if not all(context.get(k) for k in ("source_system", "tenant_scope", "case_ref", "patient_ref")):
            return ContextCheck(False, "context incomplete")
        try:
            ok = self.transport.validate_context(context, self._timeout())
        except TimeoutError:
            return ContextCheck(False, "context check timed out")
        return ContextCheck(True) if ok else ContextCheck(False, "case/patient unknown to KIS")

    def deliver(self, payload: dict, idempotency_key: str) -> AdapterAnswer:
        if not self.enabled:
            raise RuntimeError(self.blocked_reason())
        self.deliver_calls += 1
        try:
            # Single attempt: a blind re-send could duplicate. Timeouts go to reconcile.
            reply = self.transport.send(payload, idempotency_key, self._timeout())
        except TimeoutError as exc:
            raise AdapterTimeout(str(exc) or "send timed out") from exc
        status = reply.get("status")
        if status == "rejected":
            return AdapterAnswer("rejected", reason=str(reply.get("reason", "")))
        doc_id, version = reply.get("document_id"), reply.get("version")
        if status != "accepted" or not doc_id or version in (None, "") \
                or reply.get("content_hash") != payload_hash(payload):
            return AdapterAnswer("maybe", reason="incomplete or mismatching answer")
        self.documents[idempotency_key] = {
            "id": str(doc_id), "version": version, "payload_hash": reply["content_hash"],
        }
        return AdapterAnswer("accepted", str(doc_id))

    def lookup(self, idempotency_key: str, payload_hash: str) -> str:
        if not self.enabled:
            return LOOKUP_UNKNOWN
        deadline = time.monotonic() + MAX_LOOKUP_WINDOW_S
        for attempt in range(MAX_LOOKUP_ATTEMPTS):
            try:
                reply = self.transport.query(idempotency_key, self._timeout())
            except TimeoutError:
                reply = None
            if reply is not None:
                state = reply.get("state")
                if state == "absent" and self.contract["lookup_consistency"] == "read-your-writes":
                    return LOOKUP_ABSENT  # absent is trusted only under documented consistency
                if state == "present":
                    if reply.get("content_hash") != payload_hash or not reply.get("version") \
                            or not reply.get("document_id"):
                        return LOOKUP_UNKNOWN  # stale or foreign version: never confirm
                    self.documents[idempotency_key] = {
                        "id": str(reply["document_id"]), "version": reply["version"],
                        "payload_hash": payload_hash,
                    }
                    return LOOKUP_CONFIRMED
                if state == "unknown" or state == "absent":
                    return LOOKUP_UNKNOWN
            if attempt + 1 < MAX_LOOKUP_ATTEMPTS and time.monotonic() < deadline:
                self._sleep(min(2 ** attempt, 5))
        return LOOKUP_UNKNOWN


def build_vendor_adapter(contract: Optional[dict] = None, transport=None) -> KisAdapter:
    """Always returns an adapter; it is disabled unless the contract is complete."""
    return VendorKisAdapter(contract, transport)
