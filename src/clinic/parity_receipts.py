"""Durable run_once receipts for the parity dispatcher (x22).

Implements the ``RunOnce`` contract of ``parity_dispatch.ParityDispatch``:
an exact replay returns the stored result, a mismatched digest raises
IDEMPOTENCY_CONFLICT, and the receipt commits in the same transaction as the
operation's state/job/audit rows. A failing operation stores no receipt and
rolls back.

Cross-process safety: the receipt row is inserted in the operation's own
transaction. The scope key is the primary key, so of two concurrent first
executions exactly one commit succeeds; the loser rolls back (including its
operation state), loads the winner's receipt and returns it or raises the
conflict. Works on SQLite and PostgreSQL. Receipts are append-only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.clinic.parity_dispatch import CommandProblem
from src.database import db


class ClinicParityReceipt(db.Model):
    """One executed parity command; never updated or deleted."""

    __tablename__ = "clinic_parity_receipt"

    scope_key = db.Column(db.String(64), primary_key=True)
    digest = db.Column(db.String(255), nullable=False)
    result = db.Column(db.JSON, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


def _reject_receipt_write(_mapper, _connection, _target):
    raise RuntimeError("parity receipts are append-only")


event.listen(ClinicParityReceipt, "before_update", _reject_receipt_write)
event.listen(ClinicParityReceipt, "before_delete", _reject_receipt_write)


def scope_key(scope) -> str:
    """sha256 of the canonical JSON of the scope tuple (actor, command, target, key)."""
    canonical = json.dumps(list(scope), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_once(scope, digest, operation):
    key = scope_key(scope)
    existing = db.session.get(ClinicParityReceipt, key)
    if existing is not None:
        if existing.digest != digest:
            raise CommandProblem("IDEMPOTENCY_CONFLICT")
        return existing.result
    try:
        result = operation()
        db.session.add(ClinicParityReceipt(scope_key=key, digest=digest, result=result))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        winner = db.session.get(ClinicParityReceipt, key)
        if winner is None:
            raise  # an unrelated constraint failure of the operation itself
        if winner.digest != digest:
            raise CommandProblem("IDEMPOTENCY_CONFLICT") from None
        return winner.result
    except BaseException:
        db.session.rollback()
        raise
    return result
