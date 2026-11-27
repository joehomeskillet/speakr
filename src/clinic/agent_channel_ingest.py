"""Resumable ingest sessions and the outcome audit of the import-agent channel.

Flow: begin (intent, declared size and sha256, idempotency key) -> chunk (sequence,
byte offset, chunk hash) -> finalize -> receipt. Storing the original is an
injected callable so this module does not depend on the ingest module.
Anything except a clean return of that call leaves the session UNKNOWN; UNKNOWN
is never reported or treated as STORED.
"""

import hashlib
import hmac
import logging
import re
import tempfile
import uuid

from sqlalchemy import and_, or_

from src.database import db

from .agent_channel import (
    INTENT_KIND, OUTCOMES, AgentChannelError, ClinicAgentChunk, ClinicAgentIngest, ClinicAgentIntent, ClinicAgentInventory,
    ClinicAgentOutcome, agent_of, authorise, aware, inventory_row, stamp, transactional, utcnow,
)
from .models import ClinicOriginal

logger = logging.getLogger(__name__)


def _session(agent, ingest_id):
    row = db.session.get(ClinicAgentIngest, ingest_id)
    if row is None or row.agent_id != agent.id:
        raise AgentChannelError("not_found", "ingest session")
    return row


def _open_session(agent, ingest_id, now):
    row = _session(agent, ingest_id)
    if aware(row.expires_at) <= now:
        raise AgentChannelError("expired", "ingest session")
    return row


def _chunks(ingest_id):
    return ClinicAgentChunk.query.filter_by(ingest_id=ingest_id).order_by(ClinicAgentChunk.sequence).all()


def _receipt(row):
    """Receipt bound to ingest id, source id, snapshot version, sha256, size and the stored original."""
    chunks = _chunks(row.id)
    stored = row.state == "STORED"
    permit = row.permitted_post_step if stored else "none"
    return {
        "ingest_id": row.id, "state": row.state,
        "received_chunks": [c.sequence for c in chunks],
        "received_bytes": row.declared_size if stored else sum(c.size for c in chunks),
        "sha256": row.declared_sha256 if stored else None,
        "dictation_id": row.dictation_id if stored else None,
        "durability": "STORED" if stored else "RECEIVED",
        "decode_state": "PENDING" if stored else "NOT_STARTED",
        "completion": "UNKNOWN", "author_state": "UNRESOLVED",
        "correlation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "speakr-agent-ingest/" + row.id)),
        "agent_id": row.agent_id, "intent_id": row.intent_id, "source_id": row.source_id,
        "snapshot_version": row.snapshot_version, "size": row.declared_size,
        "original_id": row.original_id if stored else None,
        "permitted_post_step": permit, "deletion_permitted": permit == "delete",
    }


@transactional
def begin_ingest(principal, policy, *, intent_id, declared_size, declared_sha256, idempotency_key, now=None):
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    key = str(idempotency_key or "")
    if not 8 <= len(key) <= 255 or re.search(r"[\x00-\x1f]", key):
        raise AgentChannelError("invalid", "idempotency key")
    existing = ClinicAgentIngest.query.filter_by(agent_id=agent.id, idempotency_key=key).first()
    if existing is not None:
        if (existing.intent_id, existing.declared_size, existing.declared_sha256) != (
                intent_id, declared_size, declared_sha256):
            raise AgentChannelError("conflict", "idempotency key reused with different content")
        return _receipt(existing)
    intent = db.session.get(ClinicAgentIntent, intent_id)
    if intent is None or intent.agent_id != agent.id:
        raise AgentChannelError("forbidden", "intent is not addressed to this agent")
    if intent.kind != INTENT_KIND or intent.status not in ("OPEN", "IN_PROGRESS"):
        raise AgentChannelError("conflict", "intent is not open")
    if aware(intent.expires_at) <= now:
        raise AgentChannelError("expired", "intent")
    item = inventory_row(agent.id, intent.source_id, intent.snapshot_version)
    if item is None or item.size != declared_size or item.sha256 != declared_sha256:
        raise AgentChannelError("conflict", "declared size or hash differs from the reported snapshot")
    if not 1 <= declared_size <= policy.max_total_bytes:
        raise AgentChannelError("invalid", "declared size out of range")
    for other in ClinicAgentIngest.query.filter_by(intent_id=intent.id):
        if other.state != "ABORTED":
            raise AgentChannelError("conflict", "intent already has an ingest session")
    row = ClinicAgentIngest(
        agent_id=agent.id, intent_id=intent.id, idempotency_key=key, source_id=intent.source_id,
        snapshot_version=intent.snapshot_version, declared_sha256=declared_sha256,
        declared_size=declared_size, expires_at=now + policy.session_ttl, created_at=now,
    )
    intent.status = "IN_PROGRESS"
    db.session.add(row)
    db.session.commit()
    return _receipt(row)


@transactional
def put_chunk(principal, policy, *, ingest_id, sequence, offset, data, chunk_sha256, now=None):
    """Append chunk ``sequence`` at byte ``offset``. Chunks arrive in order; an identical replay is a no-op."""
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    row = _open_session(agent, ingest_id, now)
    if row.state != "RECEIVING":
        raise AgentChannelError("conflict", "session is " + row.state)
    if not isinstance(sequence, int) or not 0 <= sequence < policy.max_chunks:
        raise AgentChannelError("invalid", "sequence")
    if not data or len(data) > policy.max_chunk_bytes:
        raise AgentChannelError("invalid", "chunk size")
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, str(chunk_sha256).lower()):
        raise AgentChannelError("invalid", "chunk hash mismatch")
    have = _chunks(row.id)
    same = next((c for c in have if c.sequence == sequence), None)
    if same is not None:
        if (same.offset, same.size, same.sha256) != (offset, len(data), digest):
            raise AgentChannelError("conflict", "chunk replayed with different content")
        return _receipt(row)
    prior = [c for c in have if c.sequence < sequence]
    expected = sum(c.size for c in prior)
    if len(prior) != sequence or offset != expected:
        raise AgentChannelError("conflict", "chunk offset out of order, expected %d" % expected)
    if offset + len(data) > row.declared_size:
        raise AgentChannelError("conflict", "chunk exceeds the declared size")
    db.session.add(ClinicAgentChunk(ingest_id=row.id, sequence=sequence, offset=offset,
                                    size=len(data), sha256=digest, data=data))
    db.session.commit()
    return _receipt(row)


def _verify_finalize(row, chunks, body):
    if (body.get("total_bytes") != row.declared_size
            or str(body.get("sha256", "")).lower() != row.declared_sha256):
        raise AgentChannelError("invalid", "finalize size or total hash differs from the declaration")
    listed = [(c.get("sequence"), c.get("bytes"), str(c.get("sha256", "")).lower())
              for c in body.get("chunks", [])]
    if listed != [(c.sequence, c.size, c.sha256) for c in chunks]:
        raise AgentChannelError("conflict", "chunk list differs from the stored chunks")
    if sum(c.size for c in chunks) != row.declared_size:
        raise AgentChannelError("conflict", "chunks do not cover the declared size")


def _removal_step(policy, intent, dictation_id):
    """The source may only be touched if policy and intent allow it and, by default, a dictation exists."""
    if not policy.allow_post_import or intent.post_step == "none":
        return "none"
    if policy.require_dictation_id_for_removal and not dictation_id:
        return "none"
    return intent.post_step


@transactional
def finalize_ingest(principal, policy, *, ingest_id, body, store_original, now=None, recover_original=None):
    """Verify, store the original through the injected callable and return the bound receipt.

    ``store_original(stream, declared_sha256=, declared_size=, origin=, origin_ref=, author_user_id=,
    idempotency_key=, now=)`` returns an object or mapping with ``original_id`` and optionally
    ``dictation_id``. Only a clean return with an original id makes the session STORED; an exception
    or an answer without it leaves UNKNOWN, and a repeated finalize retries with the same key.

    ``recover_original`` is an optional server-side lookup of a verified,
    already stored original. Recovery of UNKNOWN sessions precedes upload
    expiry and works after their temporary chunks have been removed.
    """
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    row = _session(agent, ingest_id)
    if row.state in ("STORED", "INVALID"):
        return _receipt(row)
    if row.state == "ABORTED":
        raise AgentChannelError("conflict", "session was aborted")
    if row.state == "UNKNOWN" and recover_original is not None:
        result = recover_original()
        if result is not None:
            return _finish_ingest(row, policy, result)
    if aware(row.expires_at) <= now:
        raise AgentChannelError("expired", "ingest session")
    chunks = _chunks(row.id)
    _verify_finalize(row, chunks, body or {})
    total = hashlib.sha256()
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as stream:
        for chunk in chunks:
            total.update(chunk.data)
            stream.write(chunk.data)
        if total.hexdigest() != row.declared_sha256:
            row.state = "INVALID"
            db.session.commit()
            return _receipt(row)
        row.state = "UNKNOWN"  # persisted before the call: a crash inside it stays ambiguous
        db.session.commit()
        stream.seek(0)
        try:
            result = store_original(
                stream, declared_sha256=row.declared_sha256, declared_size=row.declared_size,
                origin="agent", origin_ref="agent:%s:%s:%s" % (agent.id, row.source_id, row.snapshot_version),
                author_user_id=None, idempotency_key="agent-ingest:%s:%s" % (agent.id, row.idempotency_key),
                now=now,
            )
        except Exception:  # any failure is ambiguous: the bytes may or may not be durable
            logger.warning("agent ingest %s: store_original failed, state UNKNOWN", row.id, exc_info=True)
            return _receipt(row)
    return _finish_ingest(row, policy, result)


def _finish_ingest(row, policy, result):
    """Bind a durable original to its channel receipt and retire upload chunks."""
    get = result.get if isinstance(result, dict) else (lambda name: getattr(result, name, None))
    if not get("original_id"):
        return _receipt(row)
    intent = db.session.get(ClinicAgentIntent, row.intent_id)
    row.original_id = str(get("original_id"))
    row.dictation_id = str(get("dictation_id")) if get("dictation_id") else None
    row.permitted_post_step = _removal_step(policy, intent, row.dictation_id)
    row.state = "STORED"
    intent.status = "STORED"
    ClinicAgentChunk.query.filter_by(ingest_id=row.id).delete()
    db.session.commit()
    return _receipt(row)


@transactional
def abort_ingest(principal, *, ingest_id):
    """Abort a RECEIVING session. UNKNOWN and STORED cannot be aborted: the bytes may be durable."""
    row = _session(agent_of(principal), ingest_id)
    if row.state == "ABORTED":
        return _receipt(row)
    if row.state != "RECEIVING":
        raise AgentChannelError("conflict", "session is " + row.state + " and cannot be aborted")
    ClinicAgentChunk.query.filter_by(ingest_id=row.id).delete()
    row.state = "ABORTED"
    intent = db.session.get(ClinicAgentIntent, row.intent_id)
    if intent.status == "IN_PROGRESS":
        intent.status = "OPEN"
    db.session.commit()
    return _receipt(row)


def get_receipt(principal, *, ingest_id):
    return _receipt(_session(agent_of(principal), ingest_id))


def source_cleanup_refusal(snapshot, receipt_id):
    """Check the last reported identity and the committed, bound ingest receipt."""
    from .import_centre import ClinicImportDelivery
    from .ingest import storage_key_for

    inventory = ClinicAgentInventory
    if inventory.query.filter(
        inventory.agent_id == snapshot.agent_id, inventory.id != snapshot.id,
        inventory.reported_at >= snapshot.reported_at,
        or_(inventory.source_id == snapshot.source_id, and_(
            inventory.root_id == snapshot.root_id, inventory.relative_path == snapshot.relative_path,
        )),
    ).first() is not None:
        return "SNAPSHOT_CHANGED"
    row = db.session.get(ClinicAgentIngest, receipt_id)
    if row is None or row.state != "STORED" or not row.dictation_id or not row.original_id:
        return "STORED_RECEIPT_REQUIRED"
    if (row.agent_id, row.source_id, row.snapshot_version, row.declared_size, row.declared_sha256) != (
        snapshot.agent_id, snapshot.source_id, snapshot.snapshot_version, snapshot.size, snapshot.sha256,
    ):
        return "RECEIPT_NOT_BOUND"
    delivery = ClinicImportDelivery.query.filter_by(ingest_id=row.id, agent_id=row.agent_id,
                                                      source_id=row.source_id, status="MATCHED").first()
    if delivery is None or (delivery.root_id, delivery.relative_path) != (snapshot.root_id, snapshot.relative_path):
        return "RECEIPT_NOT_BOUND"
    original = db.session.get(ClinicOriginal, row.original_id)
    if original is None or (original.dictation_id, original.size_bytes, original.content_hash, original.storage_key) != (
        row.dictation_id, snapshot.size, snapshot.sha256,
        storage_key_for(f"agent-ingest:{row.agent_id}:{row.idempotency_key}"),
    ):
        return "RECEIPT_NOT_BOUND"
    return None


# --- outcome audit ------------------------------------------------------------------

def _violation(outcome, row):
    if outcome in ("stored", "moved", "deleted") and (row is None or row.state != "STORED"):
        return "no STORED receipt for this intent"
    if outcome == "deleted" and row.permitted_post_step != "delete":
        return "deletion not permitted by the receipt"
    if outcome == "moved" and row.permitted_post_step != "move_backup":
        return "move not permitted by the receipt"
    return None


@transactional
def record_outcome(principal, *, intent_id, outcome, ingest_id=None, detail="", now=None):
    """Append what the agent did. A removal the receipt did not permit is stored flagged as violation."""
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    intent = db.session.get(ClinicAgentIntent, intent_id)
    if intent is None or intent.agent_id != agent.id:
        raise AgentChannelError("forbidden", "intent is not addressed to this agent")
    if outcome not in OUTCOMES:
        raise AgentChannelError("invalid", "outcome")
    row = db.session.get(ClinicAgentIngest, ingest_id) if ingest_id else None
    if row is not None and (row.agent_id != agent.id or row.intent_id != intent.id):
        row = None
    reason = _violation(outcome, row)
    db.session.add(ClinicAgentOutcome(
        agent_id=agent.id, intent_id=intent.id, ingest_id=row.id if row else None, outcome=outcome,
        detail=re.sub(r"[\x00-\x1f]", "?", str(detail))[:240], violation=reason is not None,
        violation_reason=reason, reported_at=now,
    ))
    if outcome != "stored" or intent.post_step == "none":
        intent.status = "DONE"
    db.session.commit()
    if reason:
        logger.error("agent %s outcome %s without permission: %s", agent.id, outcome, reason)
    return {"recorded": True, "violation": reason is not None}


def list_outcomes(policy, *, actor_user_id, agent_id=None, violations_only=False):
    """Web audit view of the append-only outcome log."""
    authorise(policy, actor_user_id, "read_outcomes")
    query = ClinicAgentOutcome.query.order_by(ClinicAgentOutcome.reported_at)
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    if violations_only:
        query = query.filter_by(violation=True)
    return [{"id": r.id, "agent_id": r.agent_id, "intent_id": r.intent_id, "ingest_id": r.ingest_id,
             "outcome": r.outcome, "violation": r.violation, "violation_reason": r.violation_reason,
             "reported_at": stamp(r.reported_at)} for r in query]
