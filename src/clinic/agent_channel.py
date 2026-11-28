"""Server side of the import-agent channel: registry, inventory, signed intents (service layer, no Flask routes).

The Windows import agent is a machine principal. It is not a user: it can
report an inventory, poll signed import intents, upload one snapshot through a
resumable ingest session (``agent_channel_ingest``) and report what it did with
the source. It cannot read dictation text or audio, set roles or take any
clinical action; no function of this channel returns dictation content.

Identity rules
- The agent identity comes only from the authenticated transport credential
  (certificate fingerprint or token id) handed to ``authenticate_agent`` by the
  caller. An ``agent_id`` inside a request body is never read.
- Intents are signed per agent (HMAC-SHA256, see ``canonical_intent``). The
  per-agent key is derived from a server master secret and is not stored.

All tables of the channel are declared here; the ingest and outcome logic lives
in ``agent_channel_ingest`` and imports from this module.
"""

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Optional

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint

from src.clinic.migration_inventory_hints import provenance, replay_hints, upgrade as upgrade_inventory_hints
from src.database import db
from src.models import User  # noqa: F401  (resolves the user.id foreign key)

INTENT_KIND = "import_snapshot"
POST_STEPS = ("none", "move_backup", "delete")
OUTCOMES = ("stored", "moved", "deleted", "conflict", "refused")
SIGNATURE_VERSION = "speakr-import-intent-v2"
_KEY_LABEL = "speakr-agent-intent-key-v1"
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_FINGERPRINT = re.compile(r"^[a-z0-9._-]{16,128}$")
_SEAL = object()


class AgentChannelError(Exception):
    """Refusal with a stable ``code``: forbidden, not_found, conflict, invalid or expired."""

    def __init__(self, code, message=""):
        super().__init__(code + (": " + message if message else ""))
        self.code = code


@dataclass(frozen=True)
class ChannelPolicy:
    """Server-side configuration. Safe defaults: no post-import step, bounded sizes."""

    master_secret: bytes
    allow_post_import: bool = False
    require_dictation_id_for_removal: bool = True
    intent_ttl: timedelta = timedelta(hours=24)
    session_ttl: timedelta = timedelta(hours=24)
    max_chunk_bytes: int = 4 * 1024 * 1024
    max_chunks: int = 10000
    max_total_bytes: int = 128 * 1024 * 1024
    # authorise(user_id, action) -> bool; None denies everything. Actions: register_agent,
    # manage_agent, create_intent, read_inventory, read_outcomes.
    authorise: Optional[Callable[[int, str], bool]] = field(default=None, repr=False)
    # The deployed policy checks the current source rules and requires verified backup evidence.
    may_remove_source: Optional[Callable[["ClinicAgentIngest"], bool]] = field(default=None, repr=False)


@dataclass(frozen=True)
class AgentPrincipal:
    """Authenticated machine principal. Only ``authenticate_agent`` creates one."""

    agent_id: str
    display_name: str
    _seal: object = field(default=None, repr=False, compare=False)


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _uid():
    return str(uuid.uuid4())


def _ts(**kwargs):
    return db.Column(db.DateTime(timezone=True), **kwargs)


def _fk(table, **kwargs):
    return db.Column(db.String(36), ForeignKey(table + ".id"), **kwargs)


class ClinicAgent(db.Model):
    __tablename__ = "clinic_agent"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    display_name = db.Column(db.String(120), nullable=False)
    allowed_sources = db.Column(db.Text, nullable=False)  # JSON list of root ids
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    credential_fingerprint = db.Column(db.String(128), nullable=False, unique=True)
    key_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = _ts(nullable=False, default=utcnow)


class ClinicAgentInventory(db.Model):
    __tablename__ = "clinic_agent_inventory"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    agent_id = _fk("clinic_agent", nullable=False, index=True)
    source_id = db.Column(db.String(128), nullable=False)
    root_id = db.Column(db.String(128), nullable=False)
    relative_path = db.Column(db.String(1024), nullable=False)
    size = db.Column(db.BigInteger, nullable=False)
    mtime_utc = _ts(nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    snapshot_version = db.Column(db.String(128), nullable=False)
    # Optional claims. NULL / unknown is not Philips EOL and grants no author right.
    claimed_author_code = db.Column(db.String(128), nullable=True)
    completion_source = db.Column(db.String(32), nullable=True, default="unknown")
    evidence_level = db.Column(db.String(32), nullable=True, default="unverified")
    reported_at = _ts(nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("agent_id", "source_id", "snapshot_version", name="uq_clinic_agent_inventory"),
    )


class ClinicAgentIntent(db.Model):
    __tablename__ = "clinic_agent_intent"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    agent_id = _fk("clinic_agent", nullable=False, index=True)
    kind = db.Column(db.String(32), nullable=False, default=INTENT_KIND)
    source_id = db.Column(db.String(128), nullable=False)
    snapshot_version = db.Column(db.String(128), nullable=False)
    post_step = db.Column(db.String(16), nullable=False, default="none")
    created_by_user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    created_at = _ts(nullable=False, default=utcnow)
    expires_at = _ts(nullable=False)
    status = db.Column(db.String(16), nullable=False, default="OPEN")
    __table_args__ = (
        CheckConstraint("post_step IN ('none','move_backup','delete')", name="ck_clinic_agent_intent_post"),
        CheckConstraint(
            "status IN ('OPEN','IN_PROGRESS','STORED','DONE','CANCELLED')",
            name="ck_clinic_agent_intent_status",
        ),
    )


class ClinicAgentIngest(db.Model):
    __tablename__ = "clinic_agent_ingest"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    agent_id = _fk("clinic_agent", nullable=False, index=True)
    intent_id = _fk("clinic_agent_intent", nullable=False, index=True)
    idempotency_key = db.Column(db.String(255), nullable=False)
    source_id = db.Column(db.String(128), nullable=False)
    snapshot_version = db.Column(db.String(128), nullable=False)
    declared_sha256 = db.Column(db.String(64), nullable=False)
    declared_size = db.Column(db.BigInteger, nullable=False)
    state = db.Column(db.String(16), nullable=False, default="RECEIVING")
    original_id = db.Column(db.String(64), nullable=True)
    dictation_id = db.Column(db.String(36), nullable=True)
    permitted_post_step = db.Column(db.String(16), nullable=False, default="none")
    expires_at = _ts(nullable=False)
    created_at = _ts(nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("agent_id", "idempotency_key", name="uq_clinic_agent_ingest_idem"),
        CheckConstraint(
            "state IN ('RECEIVING','UNKNOWN','STORED','INVALID','ABORTED')",
            name="ck_clinic_agent_ingest_state",
        ),
    )


class ClinicAgentChunk(db.Model):
    __tablename__ = "clinic_agent_chunk"
    ingest_id = _fk("clinic_agent_ingest", primary_key=True)
    sequence = db.Column(db.Integer, primary_key=True)
    offset = db.Column(db.BigInteger, nullable=False)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)


class ClinicAgentOutcome(db.Model):
    """Append-only audit of what the agent reports. The channel never updates or deletes a row."""

    __tablename__ = "clinic_agent_outcome"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    agent_id = _fk("clinic_agent", nullable=False, index=True)
    intent_id = _fk("clinic_agent_intent", nullable=False)
    ingest_id = db.Column(db.String(36), nullable=True)
    outcome = db.Column(db.String(16), nullable=False)
    detail = db.Column(db.String(240), nullable=False, default="")
    violation = db.Column(db.Boolean, nullable=False, default=False)
    violation_reason = db.Column(db.String(240), nullable=True)
    reported_at = _ts(nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('stored','moved','deleted','conflict','refused')",
            name="ck_clinic_agent_outcome_kind",
        ),
    )


AGENT_TABLES = (
    ClinicAgent, ClinicAgentInventory, ClinicAgentIntent,
    ClinicAgentIngest, ClinicAgentChunk, ClinicAgentOutcome,
)


def create_tables(bind=None):
    """Create the channel tables if they are missing, then add hint columns on older ones."""
    target = bind or db.engine
    db.metadata.create_all(
        bind=target, tables=[m.__table__ for m in AGENT_TABLES], checkfirst=True
    )
    upgrade_inventory_hints(target)


def transactional(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            db.session.rollback()
            raise
    return run


# --- signing -------------------------------------------------------------------

def derive_agent_key(policy, agent_id, key_version):
    """Per-agent HMAC key from the server master secret. Nothing is stored."""
    if not policy.master_secret or len(policy.master_secret) < 32:
        raise AgentChannelError("invalid", "master secret must be at least 32 bytes")
    label = "\n".join((_KEY_LABEL, agent_id, str(key_version))).encode()
    return hmac.new(policy.master_secret, label, hashlib.sha256).digest()


def stamp(value):
    return aware(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_intent(*, intent_id, kind, agent_id, source_id, snapshot_version, post_step, expires_at):
    """Signing input: eight lines joined by LF (no trailing LF), UTF-8; the first is the version tag
    and ``expires_at`` is UTC formatted ``%Y-%m-%dT%H:%M:%SZ``."""
    expires = expires_at if isinstance(expires_at, str) else stamp(expires_at)
    return "\n".join(
        (SIGNATURE_VERSION, intent_id, kind, agent_id, source_id, snapshot_version, post_step, expires)
    )


def sign_intent(key, **fields):
    return hmac.new(key, canonical_intent(**fields).encode("utf-8"), hashlib.sha256).hexdigest()


def verify_intent_wire(key, wire, *, expected_agent_id, now):
    """Agent-side check; the reference for the .NET client. True only for a valid, unexpired, own intent."""
    try:
        fields = {n: wire[n] for n in
                  ("intent_id", "kind", "agent_id", "source_id", "snapshot_version", "post_step", "expires_at")}
        good = hmac.compare_digest(sign_intent(key, **fields), str(wire["signature"]).lower())
        expires = datetime.strptime(fields["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        return False
    return good and wire["agent_id"] == expected_agent_id and aware(now) < expires


def _intent_wire(policy, agent, intent):
    fields = dict(
        intent_id=intent.id, kind=intent.kind, agent_id=agent.id, source_id=intent.source_id,
        snapshot_version=intent.snapshot_version, post_step=intent.post_step,
        expires_at=stamp(intent.expires_at),
    )
    key = derive_agent_key(policy, agent.id, agent.key_version)
    return {**fields, "signature": sign_intent(key, **fields)}


# --- registry and authentication -------------------------------------------------

def authorise(policy, user_id, action):
    if policy.authorise is None or not policy.authorise(user_id, action):
        raise AgentChannelError("forbidden", "user may not " + action)


def _fingerprint(value):
    text = re.sub(r"[:\s]", "", str(value or "")).lower()
    if not _FINGERPRINT.match(text):
        raise AgentChannelError("invalid", "credential fingerprint malformed")
    return text


def clean_id(value, what):
    if not isinstance(value, str) or not _ID.match(value):
        raise AgentChannelError("invalid", what + " malformed")
    return value


def _agent_or_fail(agent_id, *, lock=False):
    agent = db.session.get(ClinicAgent, agent_id, with_for_update=lock, populate_existing=lock)
    if agent is None:
        raise AgentChannelError("not_found", "agent")
    return agent


@transactional
def register_agent(policy, *, actor_user_id, display_name, allowed_sources, credential_fingerprint):
    """Create the machine principal. The signing key is returned once, for the agent's secret store."""
    authorise(policy, actor_user_id, "register_agent")
    sources = sorted({clean_id(s, "source root") for s in allowed_sources})
    if not sources or not str(display_name).strip():
        raise AgentChannelError("invalid", "display name and at least one source root are required")
    agent = ClinicAgent(
        display_name=str(display_name).strip()[:120],
        allowed_sources=json.dumps(sources),
        credential_fingerprint=_fingerprint(credential_fingerprint),
    )
    db.session.add(agent)
    db.session.flush()
    key = derive_agent_key(policy, agent.id, agent.key_version).hex()
    result = {"agent_id": agent.id, "allowed_sources": sources, "intent_signing_key_hex": key}
    db.session.commit()
    return result


@transactional
def set_agent_enabled(policy, *, actor_user_id, agent_id, enabled):
    authorise(policy, actor_user_id, "manage_agent")
    _agent_or_fail(agent_id).enabled = bool(enabled)
    db.session.commit()


@transactional
def rotate_signing_key(policy, *, actor_user_id, agent_id, commit=True):
    """Rotate the key and cancel unfinished intents. The caller can commit with its audit row."""
    authorise(policy, actor_user_id, "manage_agent")
    agent = _agent_or_fail(agent_id, lock=True)
    agent.key_version += 1
    for intent in ClinicAgentIntent.query.filter(
        ClinicAgentIntent.agent_id == agent_id, ClinicAgentIntent.status.in_(("OPEN", "IN_PROGRESS", "STORED"))
    ):
        intent.status = "CANCELLED"
    key = derive_agent_key(policy, agent.id, agent.key_version).hex()
    if commit:
        db.session.commit()
    return {"intent_signing_key_hex": key}


def authenticate_agent(credential_fingerprint):
    """Resolve the transport credential (certificate fingerprint or token id) to a principal."""
    try:
        fingerprint = _fingerprint(credential_fingerprint)
    except AgentChannelError:
        raise AgentChannelError("forbidden", "unknown credential") from None
    agent = ClinicAgent.query.filter_by(credential_fingerprint=fingerprint).first()
    if agent is None or not agent.enabled:
        raise AgentChannelError("forbidden", "unknown or disabled agent")
    return AgentPrincipal(agent.id, agent.display_name, _SEAL)


def agent_of(principal):
    """Re-check the principal on every call so a disabled agent loses access at once."""
    if not isinstance(principal, AgentPrincipal) or principal._seal is not _SEAL:
        raise AgentChannelError("forbidden", "not an authenticated agent")
    agent = db.session.get(ClinicAgent, principal.agent_id)
    if agent is None or not agent.enabled:
        raise AgentChannelError("forbidden", "agent disabled")
    return agent


# --- inventory -------------------------------------------------------------------

def registered_agent_roots():
    rows = db.session.query(ClinicAgent.id, ClinicAgent.enabled, ClinicAgent.allowed_sources).order_by(ClinicAgent.id)
    return {agent_id: (enabled, set(json.loads(roots))) for agent_id, enabled, roots in rows}


def unique_root_owners(registry):
    """A disabled machine retains its claim, so a replacement cannot inherit a local alias."""
    owners = {}
    for agent_id, (enabled, roots) in registry.items():
        for root in roots:
            owners[root] = agent_id if enabled and root not in owners else None
    return owners


def _parse_mtime(value):
    try:
        return aware(datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", str(value))))
    except ValueError:
        raise AgentChannelError("invalid", "mtime_utc malformed") from None


def _clean_item(policy, raw):
    path = str(raw.get("relative_path", ""))
    if (not path or len(path) > 1024 or path[0] in "/\\" or ".." in re.split(r"[\\/]", path)
            or re.search(r"[\x00-\x1f]", path) or re.match(r"^[A-Za-z]:", path)):
        raise AgentChannelError("invalid", "relative_path outside the source")
    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= policy.max_total_bytes:
        raise AgentChannelError("invalid", "size out of range")
    digest = str(raw.get("sha256", "")).lower()
    if not _SHA.match(digest):
        raise AgentChannelError("invalid", "sha256 malformed")
    return dict(
        source_id=clean_id(raw.get("source_id"), "source_id"), root_id=clean_id(raw.get("root_id"), "root_id"),
        relative_path=path, size=size, sha256=digest, mtime_utc=_parse_mtime(raw.get("mtime_utc")),
        snapshot_version=clean_id(raw.get("snapshot_version"), "snapshot_version"),
    )


@transactional
def report_inventory(principal, policy, body, *, now=None):
    """Store the reported snapshot inventory. ``body['agent_id']`` is ignored on purpose.

    Every item is checked before anything is stored; one item outside the
    registered sources refuses the whole report. No author, patient or
    dictation right is derived from an inventory. Optional hint fields are
    stored as claims: a missing or unknown mark stays unknown, and file
    stability is not recorded as Philips EOL.
    """
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    allowed = set(json.loads(agent.allowed_sources))
    raw_items = (body or {}).get("items", [])
    items = [_clean_item(policy, raw) for raw in raw_items]
    outside = sorted({i["root_id"] for i in items} - allowed)
    if outside:
        raise AgentChannelError("forbidden", "source root not registered for this agent: " + outside[0])
    for raw, item in zip(raw_items, items):
        row = inventory_row(agent.id, item["source_id"], item["snapshot_version"])
        if row is None:
            row = ClinicAgentInventory(agent_id=agent.id)
            db.session.add(row)
            previous = None
        else:
            previous = provenance(row.claimed_author_code, row.completion_source, row.evidence_level)
        item = {**item, **replay_hints(previous, raw)}
        for name, value in item.items():
            setattr(row, name, value)
        row.reported_at = now
    db.session.commit()
    return {"accepted": len(items)}


def inventory_row(agent_id, source_id, snapshot_version):
    return ClinicAgentInventory.query.filter_by(
        agent_id=agent_id, source_id=source_id, snapshot_version=snapshot_version).first()


def list_inventory(policy, *, actor_user_id, agent_id):
    """Web import centre view of what an agent reported."""
    authorise(policy, actor_user_id, "read_inventory")
    rows = ClinicAgentInventory.query.filter_by(agent_id=agent_id).order_by(
        ClinicAgentInventory.source_id, ClinicAgentInventory.snapshot_version)
    return [{"source_id": r.source_id, "root_id": r.root_id, "relative_path": r.relative_path,
             "size": r.size, "mtime_utc": stamp(r.mtime_utc), "sha256": r.sha256,
             "snapshot_version": r.snapshot_version,
             **provenance(r.claimed_author_code, r.completion_source, r.evidence_level)} for r in rows]


# --- intents ---------------------------------------------------------------------

@transactional
def create_intent(policy, *, actor_user_id, agent_id, source_id, snapshot_version, post_step="none", now=None):
    """An authorised web user asks the agent to import one reported snapshot."""
    authorise(policy, actor_user_id, "create_intent")
    now = aware(now) or utcnow()
    agent = _agent_or_fail(agent_id)
    if not agent.enabled:
        raise AgentChannelError("forbidden", "agent disabled")
    if post_step not in POST_STEPS:
        raise AgentChannelError("invalid", "post_step")
    if post_step != "none" and not policy.allow_post_import:
        raise AgentChannelError("forbidden", "post-import step is disabled by server policy")
    if inventory_row(agent.id, source_id, snapshot_version) is None:
        raise AgentChannelError("not_found", "snapshot not in the agent inventory")
    for other in ClinicAgentIntent.query.filter(
            ClinicAgentIntent.agent_id == agent.id, ClinicAgentIntent.source_id == source_id,
            ClinicAgentIntent.snapshot_version == snapshot_version,
            ClinicAgentIntent.status.in_(("OPEN", "IN_PROGRESS", "STORED"))):
        if aware(other.expires_at) > now:
            raise AgentChannelError("conflict", "an open intent exists for this snapshot")
    intent = ClinicAgentIntent(
        agent_id=agent.id, source_id=source_id, snapshot_version=snapshot_version, post_step=post_step,
        created_by_user_id=actor_user_id, created_at=now,
        expires_at=(now + policy.intent_ttl).replace(microsecond=0),
    )
    db.session.add(intent)
    db.session.commit()
    return _intent_wire(policy, agent, intent)


@transactional
def cancel_intent(policy, *, actor_user_id, intent_id):
    authorise(policy, actor_user_id, "create_intent")
    intent = db.session.get(ClinicAgentIntent, intent_id)
    if intent is None:
        raise AgentChannelError("not_found", "intent")
    if intent.status in ("OPEN", "IN_PROGRESS"):
        intent.status = "CANCELLED"
        db.session.commit()
    return intent.status


def poll_intents(principal, policy, *, now=None):
    """Signed, unexpired, unfinished intents addressed to this agent. Identity comes from the principal only."""
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    rows = ClinicAgentIntent.query.filter(
        ClinicAgentIntent.agent_id == agent.id,
        ClinicAgentIntent.status.in_(("OPEN", "IN_PROGRESS", "STORED"))).order_by(ClinicAgentIntent.created_at)
    return {"intents": [_intent_wire(policy, agent, r) for r in rows if aware(r.expires_at) > now]}
