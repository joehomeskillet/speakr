"""KIS adapter interface, DisabledAdapter, MockKisAdapter and the delivery ledger.

No Flask routes and no network. A mock is never proof of a real KIS
(T-056): MockKisAdapter refuses to exist under CLINIC_PROFILE=production.

Ledger mapping onto ``clinic_delivery`` (models.py stays untouched):
    DELIVERED  -> state ACKNOWLEDGED
    REJECTED   -> state FAILED
    UNKNOWN    -> state UNKNOWN (also a crashed PENDING row)
The main row (key, target) is the one delivery. Every reconcile outcome and
every permitted retry is appended as an extra row whose key is
``<key>#h<NNNN>:<KIND>``. History rows are never changed or deleted. The
main row moves forward only: PENDING -> final, or UNKNOWN -> ACKNOWLEDGED
after a confirmed reconcile, or back to PENDING after a reconcile that
proved the document absent.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError

from src.database import db

from .config import kis_delivery_enabled
from .models import ClinicContextRevision, ClinicDelivery, ClinicDictation, ClinicRelease

PRODUCTION_PROFILE = "production"

# Outcomes returned to callers.
DELIVERED = "DELIVERED"
REJECTED = "REJECTED"
UNKNOWN = "UNKNOWN"
REFUSED = "REFUSED"

# Lookup answers of the adapter status operation.
LOOKUP_CONFIRMED = "confirmed"
LOOKUP_ABSENT = "absent"
LOOKUP_UNKNOWN = "unknown"

# Reconcile outcomes.
RECONCILED_DELIVERED = "DELIVERED"
RECONCILED_NOT_DELIVERED = "NOT_DELIVERED"
RECONCILED_UNDETERMINED = "UNDETERMINED"

_HIST_STATE = {
    "CONFIRMED": "ACKNOWLEDGED",
    "ABSENT": "FAILED",
    "UNDETERMINED": "UNKNOWN",
    "RETRY": "PENDING",
}


class AdapterTimeout(Exception):
    """The adapter lost the answer. The document may or may not exist."""


class MockInProductionError(RuntimeError):
    """A mock adapter was requested under the production profile."""


def _profile() -> str:
    return os.environ.get("CLINIC_PROFILE", "").strip().lower()


@dataclass(frozen=True)
class ContextCheck:
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class AdapterAnswer:
    """Answer of ``deliver``. status is accepted|rejected; anything else is ambiguous."""

    status: str
    external_document_id: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class DeliveryResult:
    outcome: str  # DELIVERED | REJECTED | UNKNOWN | REFUSED
    reason: str = ""
    delivery_id: Optional[str] = None
    external_document_id: Optional[str] = None
    replayed: bool = False
    sent: bool = False  # True only if the adapter was called in this call


@dataclass(frozen=True)
class ReconcileResult:
    outcome: str  # DELIVERED | NOT_DELIVERED | UNDETERMINED | REFUSED
    reason: str = ""
    external_document_id: Optional[str] = None


class KisAdapter:
    """Exactly three operations: check_context, deliver, lookup."""

    name = "kis"
    enabled = True
    is_mock = False

    def check_context(self, context: dict) -> ContextCheck:
        raise NotImplementedError

    def deliver(self, payload: dict, idempotency_key: str) -> AdapterAnswer:
        raise NotImplementedError

    def lookup(self, idempotency_key: str, payload_hash: str) -> str:
        """Return confirmed|absent|unknown. absent must be reliable."""
        raise NotImplementedError


class DisabledAdapter(KisAdapter):
    """Default. Refuses everything and has no side effect."""

    name = "kis-disabled"
    enabled = False

    def check_context(self, context: dict) -> ContextCheck:
        return ContextCheck(False, "KIS adapter disabled")

    def deliver(self, payload: dict, idempotency_key: str) -> AdapterAnswer:
        raise RuntimeError("DisabledAdapter must never be asked to deliver")

    def lookup(self, idempotency_key: str, payload_hash: str) -> str:
        return LOOKUP_UNKNOWN


class MockKisAdapter(KisAdapter):
    """Stateful, scriptable test double.

    ``script`` is consumed one entry per deliver call (default ``accept``):
      accept            store the document, answer accepted
      reject            store nothing, answer rejected
      timeout           store the document, then lose the answer
      timeout_unsaved   store nothing, lose the answer
      ambiguous         store the document, answer something unreadable
      crash             raise an unexpected error before storing
    """

    name = "kis-mock"
    is_mock = True

    def __init__(self, script=None, known_contexts=None):
        if _profile() == PRODUCTION_PROFILE:
            raise MockInProductionError(
                "MockKisAdapter is not allowed with CLINIC_PROFILE=production"
            )
        self.script = list(script or [])
        # None = every complete context is known. Else set of (source, tenant, case, patient).
        self.known_contexts = known_contexts
        self.documents = {}  # idempotency_key -> {"id", "payload_hash", "payload"}
        self.deliver_calls = 0

    def check_context(self, context: dict) -> ContextCheck:
        if self.known_contexts is None:
            return ContextCheck(True)
        key = (
            context.get("source_system"),
            context.get("tenant_scope"),
            context.get("case_ref"),
            context.get("patient_ref"),
        )
        if key in self.known_contexts:
            return ContextCheck(True)
        return ContextCheck(False, "case/patient unknown to KIS")

    def _store(self, payload, key):
        doc = self.documents.get(key)
        if doc is None:
            doc = {
                "id": "mock-doc-" + str(len(self.documents) + 1),
                "payload_hash": payload_hash(payload),
                "payload": payload,
            }
            self.documents[key] = doc
        return doc

    def deliver(self, payload: dict, idempotency_key: str) -> AdapterAnswer:
        self.deliver_calls += 1
        step = self.script.pop(0) if self.script else "accept"
        if step == "crash":
            raise RuntimeError("mock adapter crashed")
        if step == "reject":
            return AdapterAnswer("rejected", reason="mock rejected")
        if step == "timeout_unsaved":
            raise AdapterTimeout("mock timeout before storing")
        doc = self._store(payload, idempotency_key)
        if step == "timeout":
            raise AdapterTimeout("mock timeout after storing")
        if step == "ambiguous":
            return AdapterAnswer("maybe", reason="unreadable answer")
        return AdapterAnswer("accepted", doc["id"])

    def lookup(self, idempotency_key: str, payload_hash: str) -> str:
        doc = self.documents.get(idempotency_key)
        if doc is None:
            return LOOKUP_ABSENT
        return LOOKUP_CONFIRMED if doc["payload_hash"] == payload_hash else LOOKUP_UNKNOWN


def payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_payload(release: ClinicRelease, context: ClinicContextRevision) -> dict:
    """Frozen payload. The case context comes only from the bound revision."""
    return {
        "release_id": release.id,
        "dictation_id": release.dictation_id,
        "text_hash": release.text_hash,
        "document_type": release.document_type,
        "release_type": release.release_type,
        "context": {
            "context_revision_id": context.id,
            "source_system": context.source_system,
            "tenant_scope": context.tenant_scope,
            "case_ref": context.case_ref,
            "patient_ref": context.patient_ref,
        },
    }


def release_is_active(release) -> bool:
    """A release is deliverable unless marked inactive or superseded.

    clinic_release has no status column yet (release.py is a sibling work
    package), so the markers are read defensively.
    """
    if getattr(release, "is_active", True) is False:
        return False
    if getattr(release, "superseded_by_id", None) or getattr(release, "superseded_at", None):
        return False
    status = getattr(release, "status", None)
    return status is None or str(status).upper() == "ACTIVE"


def _require_aware(now: datetime) -> None:
    if now is None or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")


def _refuse(reason: str) -> DeliveryResult:
    return DeliveryResult(REFUSED, reason)


def _check_gate(release, context, adapter) -> Optional[DeliveryResult]:
    if not getattr(adapter, "enabled", False):
        return _refuse("KIS adapter disabled: nothing was sent")
    if getattr(adapter, "is_mock", False) and _profile() == PRODUCTION_PROFILE:
        return _refuse("mock adapter in production profile")
    if not release_is_active(release):
        return _refuse("release is not active")
    if context is None or context.id != release.context_revision_id:
        return _refuse("context revision is not the one bound to the release")
    if context.dictation_id != release.dictation_id:
        return _refuse("context revision belongs to another dictation")
    fields = (context.source_system, context.tenant_scope, context.case_ref, context.patient_ref)
    if not all(fields):
        return _refuse("context incomplete: unverified case binding")
    return None


def _history(row: ClinicDelivery):
    return (
        ClinicDelivery.query.filter(
            ClinicDelivery.target == row.target,
            ClinicDelivery.idempotency_key.startswith(row.idempotency_key + "#h", autoescape=True),
        )
        .order_by(ClinicDelivery.idempotency_key)
        .all()
    )


def _append_history(row: ClinicDelivery, kind: str, now: datetime, external_id=None) -> None:
    number = len(_history(row)) + 1
    db.session.add(
        ClinicDelivery(
            release_id=row.release_id,
            target=row.target,
            idempotency_key="%s#h%04d:%s" % (row.idempotency_key, number, kind),
            payload_hash=row.payload_hash,
            state=_HIST_STATE[kind],
            external_document_id=external_id,
            created_at=now,
        )
    )


def _latest_kind(row: ClinicDelivery) -> Optional[str]:
    history = _history(row)
    return history[-1].idempotency_key.rsplit(":", 1)[1] if history else None


def _recorded(row: ClinicDelivery) -> DeliveryResult:
    base = dict(delivery_id=row.id, external_document_id=row.external_document_id, replayed=True)
    if row.state == "ACKNOWLEDGED":
        return DeliveryResult(DELIVERED, "recorded outcome", **base)
    if row.state == "FAILED":
        return DeliveryResult(REJECTED, "recorded outcome", **base)
    if _latest_kind(row) == "ABSENT":
        return None
    return DeliveryResult(UNKNOWN, "outcome unclear: reconcile first", **base)


def _send(row, adapter, payload, now) -> DeliveryResult:
    try:
        answer = adapter.deliver(payload, row.idempotency_key)
    except AdapterTimeout:
        row.state = "UNKNOWN"
        db.session.commit()
        return DeliveryResult(UNKNOWN, "timeout: reconcile first", delivery_id=row.id, sent=True)
    # Any other exception propagates: the committed PENDING row stays and blocks retry.
    if answer.status == "accepted" and answer.external_document_id:
        row.state = "ACKNOWLEDGED"
        row.external_document_id = answer.external_document_id
        db.session.commit()
        return DeliveryResult(
            DELIVERED, delivery_id=row.id,
            external_document_id=answer.external_document_id, sent=True,
        )
    if answer.status == "rejected":
        row.state = "FAILED"
        db.session.commit()
        return DeliveryResult(REJECTED, answer.reason, delivery_id=row.id, sent=True)
    row.state = "UNKNOWN"
    db.session.commit()
    return DeliveryResult(UNKNOWN, "ambiguous answer: reconcile first", delivery_id=row.id, sent=True)


def deliver(release, context_revision, adapter, idempotency_key, now) -> DeliveryResult:
    """Send one frozen payload at most once per (idempotency_key, adapter target)."""
    _require_aware(now)
    if not kis_delivery_enabled():
        return _refuse("KIS delivery is disabled")
    refusal = _check_gate(release, context_revision, adapter)
    if refusal is not None:
        return refusal
    dictation = db.session.get(ClinicDictation, release.dictation_id)
    if dictation is None or not dictation.is_report:
        return _refuse("recording type is excluded from delivery")
    check = adapter.check_context(
        {
            "source_system": context_revision.source_system,
            "tenant_scope": context_revision.tenant_scope,
            "case_ref": context_revision.case_ref,
            "patient_ref": context_revision.patient_ref,
        }
    )
    if not check.ok:
        return _refuse("context refused by KIS: " + check.reason)

    payload = build_payload(release, context_revision)
    digest = payload_hash(payload)
    row = ClinicDelivery.query.filter_by(idempotency_key=idempotency_key, target=adapter.name).first()
    if row is None:
        row = ClinicDelivery(
            release_id=release.id, target=adapter.name, idempotency_key=idempotency_key,
            payload_hash=digest, state="PENDING", created_at=now,
        )
        db.session.add(row)
        try:
            db.session.commit()  # ledger row is durable before the adapter is called
        except IntegrityError:
            db.session.rollback()
            row = ClinicDelivery.query.filter_by(
                idempotency_key=idempotency_key, target=adapter.name
            ).first()
            if row is None:
                raise
        else:
            return _send(row, adapter, payload, now)

    if row.payload_hash != digest:
        return _refuse("idempotency key already used with a different payload")
    recorded = _recorded(row)
    if recorded is not None:
        return recorded
    # Reconcile proved the document absent: one permitted new attempt.
    row.state = "PENDING"
    _append_history(row, "RETRY", now)
    db.session.commit()
    return _send(row, adapter, payload, now)


def reconcile(delivery: ClinicDelivery, adapter, now) -> ReconcileResult:
    """Establish the real state of a PENDING or UNKNOWN delivery via adapter.lookup."""
    _require_aware(now)
    if not kis_delivery_enabled():
        return ReconcileResult(REFUSED, "KIS delivery is disabled")
    if not getattr(adapter, "enabled", False):
        return ReconcileResult("REFUSED", "KIS adapter disabled")
    if delivery.target != adapter.name:
        return ReconcileResult("REFUSED", "adapter does not match delivery target")
    if delivery.state not in ("PENDING", "UNKNOWN") or _latest_kind(delivery) == "ABSENT":
        return ReconcileResult("REFUSED", "nothing to reconcile")
    answer = adapter.lookup(delivery.idempotency_key, delivery.payload_hash)
    if answer == LOOKUP_CONFIRMED:
        external = getattr(adapter, "documents", {}).get(delivery.idempotency_key, {}).get("id")
        delivery.state = "ACKNOWLEDGED"
        delivery.external_document_id = external
        _append_history(delivery, "CONFIRMED", now, external)
        db.session.commit()
        return ReconcileResult(RECONCILED_DELIVERED, external_document_id=external)
    if answer == LOOKUP_ABSENT:
        _append_history(delivery, "ABSENT", now)
        db.session.commit()
        return ReconcileResult(RECONCILED_NOT_DELIVERED)
    _append_history(delivery, "UNDETERMINED", now)
    db.session.commit()
    return ReconcileResult(RECONCILED_UNDETERMINED, "lookup unknown: still blocked")
