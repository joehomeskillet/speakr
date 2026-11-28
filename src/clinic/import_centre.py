"""Import centre service: typed import intents of a signed-in user and the matching of agent deliveries.

A user declares what is about to arrive (source device or folder, expected file
names, target author and pool). This service supplies the author to the original
store and records durable originals awaiting an author in ``ClinicImportOriginal``.

A delivery without a matching intent is kept on the clarification list
(status ``UNASSIGNED``). Its original reference has no expiry. Assignment creates
the dictation from the verified stored file, and the agent can retrieve its bound
receipt after upload-session expiry using the same idempotency key.

No Flask routes live here. Clinic clarification callers supply the authenticated
user id and server-owned visibility policy.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Collection, Mapping
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, BinaryIO, Callable, Optional

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.database import db
from src.models import User

from . import agent_channel
from .agent_channel import (
    AgentChannelError, ClinicAgentIngest, agent_of, aware, inventory_row, transactional, utcnow,
)
from .agent_channel_ingest import finalize_ingest, get_receipt
from .author_resolution import AuthorResolutionError, Resolution, resolve_author
from .config import clinic_mode_enabled
from .ingest import ImportDefaults, IngestError, IngestService, storage_key_for
from .migration_import_intent_metadata import IntentMetadataError, encode_metadata, interpret, upgrade
from .models import ClinicDictation

if TYPE_CHECKING:
    from .import_visibility import ClarificationPolicy, ImportScope

logger = logging.getLogger(__name__)

# Owner decision 2026-11-18: files without a language are German. Windows LangId 1031 = de-DE.
DEFAULT_LANG_ID = 1031
INTENT_TTL = timedelta(hours=24)  # owner-confirmed default
SOURCE_KINDS = ("device", "folder")
INTENT_STATES = ("OPEN", "FULFILLED", "EXPIRED", "CANCELLED")
DELIVERY_STATES = ("MATCHED", "UNASSIGNED")
_REF = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_CONTROL = re.compile(r"[\x00-\x1f]")
_MAX_FILES = 500

# Single-use continuations for the predecessor policy's callbacks into this
# module. They are local to the enclosing policy call and never come from a
# caller or request. Drop them once visibility can use explicit internal hooks.
_list_for_visibility = ContextVar("import_list_for_visibility", default=False)
_claim_for_visibility: ContextVar[tuple[int, str, str] | None] = ContextVar(
    "import_claim_for_visibility", default=None,
)


def _uid():
    return str(uuid.uuid4())


class ClinicImportIntent(db.Model):
    __tablename__ = "clinic_import_intent"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    created_by_user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False, index=True)
    source_kind = db.Column(db.String(16), nullable=False)
    # Compared with the root id and the source id of the agent's inventory entry.
    source_ref = db.Column(db.String(128), nullable=False)
    expected_files = db.Column(db.Text, nullable=False, default="[]")  # JSON list of file names
    target_author_user_id = db.Column(db.Integer, ForeignKey("user.id"), nullable=False)
    target_pool_id = db.Column(db.String(36), nullable=True)
    language_id = db.Column(db.Integer, nullable=False, default=DEFAULT_LANG_ID)
    # NULL version: intent created before explicit keep/set metadata. See migration_import_intent_metadata.
    metadata_version = db.Column(db.Integer, nullable=True)
    metadata_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="OPEN")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("source_kind IN ('device','folder')", name="ck_clinic_import_intent_kind"),
        CheckConstraint(
            "status IN ('OPEN','FULFILLED','EXPIRED','CANCELLED')", name="ck_clinic_import_intent_status",
        ),
    )


class ClinicAutosendIntent(db.Model):
    """One automatic intent pair for the lifetime of a uniquely identified inventory snapshot."""

    __tablename__ = "clinic_autosend_intent"
    inventory_id = db.Column(db.String(36), ForeignKey("clinic_agent_inventory.id"), primary_key=True)
    agent_intent_id = db.Column(db.String(36), ForeignKey("clinic_agent_intent.id"), nullable=False, unique=True)
    import_intent_id = db.Column(db.String(36), ForeignKey("clinic_import_intent.id"), nullable=False, unique=True)


class ClinicImportDelivery(db.Model):
    """One agent ingest session as seen by the import centre. ``intent_id`` is empty on the clarification list."""

    __tablename__ = "clinic_import_delivery"
    id = db.Column(db.String(36), primary_key=True, default=_uid)
    ingest_id = db.Column(db.String(36), ForeignKey("clinic_agent_ingest.id"), nullable=False, unique=True)
    agent_id = db.Column(db.String(36), nullable=False, index=True)
    intent_id = db.Column(db.String(36), ForeignKey("clinic_import_intent.id"), nullable=True, index=True)
    source_id = db.Column(db.String(128), nullable=False)
    root_id = db.Column(db.String(128), nullable=True)
    relative_path = db.Column(db.String(1024), nullable=True)
    language_id = db.Column(db.Integer, nullable=False, default=DEFAULT_LANG_ID)
    status = db.Column(db.String(16), nullable=False, default="UNASSIGNED")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("status IN ('MATCHED','UNASSIGNED')", name="ck_clinic_import_delivery_status"),
    )


class ClinicImportOriginal(db.Model):
    """Durable original awaiting author resolution. This reference never expires."""

    __tablename__ = "clinic_import_original"
    delivery_id = db.Column(
        db.String(36), ForeignKey("clinic_import_delivery.id"), primary_key=True,
    )
    idempotency_key = db.Column(db.String(512), nullable=False, unique=True)
    storage_root = db.Column(db.Text, nullable=False)
    storage_key = db.Column(db.String(500), nullable=False, unique=True)
    content_hash = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    origin = db.Column(db.String(32), nullable=False)
    origin_ref = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_clinic_import_original_size"),
    )


IMPORT_TABLES = (ClinicImportIntent, ClinicAutosendIntent, ClinicImportDelivery, ClinicImportOriginal)


def create_tables(bind=None):
    bind = bind or db.engine
    db.metadata.create_all(bind=bind, tables=[m.__table__ for m in IMPORT_TABLES], checkfirst=True)
    upgrade(bind)


def resolve_language(value=None):
    """No language given means German (1031)."""
    if value is None:
        return DEFAULT_LANG_ID
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 0xFFFF:
        raise AgentChannelError("invalid", "language_id")
    return value


def _file_name(path):
    return re.split(r"[\\/]", str(path))[-1].lower()


def _clean_files(files):
    names = []
    for raw in files or ():
        if not isinstance(raw, str) or not raw.strip() or _CONTROL.search(raw) or len(raw) > 255:
            raise AgentChannelError("invalid", "expected file name")
        name = _file_name(raw.strip())
        if name in ("", ".", ".."):
            raise AgentChannelError("invalid", "expected file name")
        if name not in names:
            names.append(name)
    if len(names) > _MAX_FILES:
        raise AgentChannelError("invalid", "too many expected files")
    return names


def _view(intent):
    return {
        "id": intent.id, "status": intent.status, "source_kind": intent.source_kind,
        "source_ref": intent.source_ref, "expected_files": json.loads(intent.expected_files),
        "target_author_user_id": intent.target_author_user_id, "target_pool_id": intent.target_pool_id,
        "language_id": intent.language_id, "created_at": aware(intent.created_at).isoformat(),
        "expires_at": aware(intent.expires_at).isoformat(), "metadata": interpret(intent),
    }


# --- user side ------------------------------------------------------------------------

def _may_import(actor_user_id):
    """Active site admin, or an explicit central-policy grant of the action ``import``."""
    from dataclasses import replace

    from . import policy
    from .identity import ClinicAccountPolicy

    actor = db.session.get(User, actor_user_id, populate_existing=True) if type(actor_user_id) is int else None
    if actor is None:
        raise AgentChannelError("forbidden", "unknown user")
    if not clinic_mode_enabled():
        return  # upstream behaviour is unchanged with clinic mode off
    account = db.session.get(ClinicAccountPolicy, actor.id, populate_existing=True)
    if account is not None and account.status != "active":
        raise AgentChannelError("forbidden", "inactive account")
    if actor.is_admin:
        return
    subject = policy.build_subject(actor.id)
    grants = tuple(
        replace(grant, actions=frozenset({"release"})) for grant in subject.grants
        if "import" in grant.actions
    )
    if grants and policy.decide(
        replace(subject, grants=grants), "release", policy.RecordingFacts(recording_id=0, owner_id=actor.id),
    ).allowed:
        return
    raise AgentChannelError("forbidden", "import required")


@transactional
def create_import_intent(
    *, actor_user_id, source_kind, source_ref, expected_files=(), target_author_user_id=None,
    target_pool_id=None, language_id=None, may_assign_author: Optional[Callable[[int, int], bool]] = None,
    overrides=None, approved_recipients=(), now=None,
):
    """A signed-in user announces an import. The author is the user unless ``may_assign_author`` allows another."""
    now = aware(now) or utcnow()
    _may_import(actor_user_id)
    if source_kind not in SOURCE_KINDS:
        raise AgentChannelError("invalid", "source_kind")
    if not isinstance(source_ref, str) or not _REF.match(source_ref):
        raise AgentChannelError("invalid", "source_ref")
    metadata = _stored_metadata(overrides, approved_recipients)
    author = actor_user_id if target_author_user_id is None else target_author_user_id
    if author != actor_user_id:
        if may_assign_author is None or not may_assign_author(actor_user_id, author):
            raise AgentChannelError("forbidden", "user may not import for this author")
        if db.session.get(User, author) is None:
            raise AgentChannelError("invalid", "target author")
    intent = _stage_import_intent(
        actor_user_id=actor_user_id, source_kind=source_kind, source_ref=source_ref, expected_files=expected_files,
        author=author, target_pool_id=target_pool_id, language_id=language_id, metadata=metadata, now=now,
    )
    db.session.commit()
    return _view(intent)


def _stage_import_intent(*, actor_user_id, source_kind, source_ref, expected_files, author,
                         target_pool_id, language_id, metadata, now):
    """Stage an already-authorized import in the caller's transaction."""
    metadata_version, metadata_json = metadata
    intent = ClinicImportIntent(
        created_by_user_id=actor_user_id, source_kind=source_kind, source_ref=source_ref,
        expected_files=json.dumps(_clean_files(expected_files)), target_author_user_id=author,
        target_pool_id=target_pool_id, language_id=resolve_language(language_id),
        metadata_version=metadata_version, metadata_json=metadata_json,
        created_at=now, expires_at=now + INTENT_TTL,
    )
    db.session.add(intent)
    return intent


def _stored_metadata(overrides, approved_recipients) -> tuple[int | None, str | None]:
    """``None`` keeps the legacy interpretation. An explicit document is version 1."""
    if overrides is None:
        return None, None
    try:
        return encode_metadata(overrides, approved_recipients=approved_recipients)
    except IntentMetadataError as exc:
        if exc.code == "unapproved_recipient":
            raise AgentChannelError("forbidden", "unapproved recipient") from exc
        raise AgentChannelError("invalid", exc.code) from exc


def _own_intent(actor_user_id, intent_id):
    intent = db.session.get(ClinicImportIntent, intent_id)
    if intent is None or intent.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import intent")
    return intent


@transactional
def cancel_import_intent(*, actor_user_id, intent_id):
    intent = _own_intent(actor_user_id, intent_id)
    if intent.status == "OPEN":
        intent.status = "CANCELLED"
        db.session.commit()
    return intent.status


@transactional
def expire_intents(*, now=None):
    """Mark open intents past their expiry as EXPIRED. Returns how many changed."""
    now = aware(now) or utcnow()
    changed = 0
    for intent in ClinicImportIntent.query.filter_by(status="OPEN"):
        if aware(intent.expires_at) <= now:
            intent.status = "EXPIRED"
            changed += 1
    db.session.commit()
    return changed


def list_import_intents(*, actor_user_id, status=None, now=None):
    expire_intents(now=now)
    query = ClinicImportIntent.query.filter_by(created_by_user_id=actor_user_id)
    if status:
        query = query.filter_by(status=status)
    return [_view(i) for i in query.order_by(ClinicImportIntent.created_at)]


def list_unassigned(
    *, actor_user_id: int | None = None, policy: ClarificationPolicy | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """List unresolved deliveries visible to the authenticated clinic actor."""
    if clinic_mode_enabled():
        if _list_for_visibility.get():
            _list_for_visibility.set(False)
            return _unassigned_rows()
        if type(actor_user_id) is not int or actor_user_id <= 0 or policy is None:
            raise AgentChannelError("forbidden", "clarification actor and policy required")

        from . import import_visibility

        token = _list_for_visibility.set(True)
        try:
            return import_visibility.list_unassigned(policy, actor_user_id=actor_user_id)["items"]
        finally:
            _list_for_visibility.reset(token)
    if policy is not None:
        raise AgentChannelError("not_found", "import clarification")
    return _unassigned_rows()


def _unassigned_rows() -> list[dict]:
    query = db.session.query(ClinicImportDelivery, ClinicImportOriginal).outerjoin(
        ClinicImportOriginal, ClinicImportOriginal.delivery_id == ClinicImportDelivery.id,
    ).filter(ClinicImportDelivery.status == "UNASSIGNED").order_by(ClinicImportDelivery.created_at)
    return [
        {"id": d.id, "ingest_id": d.ingest_id, "agent_id": d.agent_id, "source_id": d.source_id,
         "root_id": d.root_id, "relative_path": d.relative_path, "language_id": d.language_id,
         "created_at": aware(d.created_at).isoformat(),
         "original_state": "author_required" if original else "pending",
         "storage_key": original.storage_key if original else None,
         "sha256": original.content_hash if original else None,
         "size": original.size_bytes if original else None}
        for d, original in query
    ]


# --- matching ---------------------------------------------------------------------------

def _fits(intent, root_id, source_id, relative_path):
    if intent.source_ref not in (root_id, source_id):
        return False
    expected = json.loads(intent.expected_files)
    return not expected or _file_name(relative_path or "") in expected


def _find_intent(item, now, author_user_id=None, *, target_pool_id=None, source_kind=None):
    """Oldest open, unexpired intent that fits the reported file; ``None`` if there is none."""
    if item is None:
        return None
    query = ClinicImportIntent.query.filter_by(status="OPEN").filter(
        ~ClinicImportIntent.id.in_(db.select(ClinicAutosendIntent.import_intent_id)),
    )
    if clinic_mode_enabled():
        query = query.filter(~agent_channel.ClinicAgentIntent.query.filter_by(id=ClinicImportIntent.id).exists())
    if author_user_id is not None:
        query = query.filter_by(target_author_user_id=author_user_id)
    if target_pool_id is not None:
        query = query.filter_by(target_pool_id=target_pool_id)
    if source_kind is not None:
        query = query.filter_by(source_kind=source_kind)
    for intent in query.order_by(ClinicImportIntent.created_at):
        if aware(intent.expires_at) > now and _fits(intent, item.root_id, item.source_id, item.relative_path):
            return intent
    return None


def _refresh_fulfilment(intent):
    """FULFILLED once every expected file is stored; with no file list, once the first one is."""
    names = {
        _file_name(d.relative_path or "")
        for d in ClinicImportDelivery.query.filter_by(intent_id=intent.id)
        if (db.session.get(ClinicAgentIngest, d.ingest_id) or ClinicAgentIngest()).state == "STORED"
    }
    expected = set(json.loads(intent.expected_files))
    if intent.status == "OPEN" and names and expected <= names:
        intent.status = "FULFILLED"


@transactional
def assign_unassigned(
    *, actor_user_id: int | None = None, delivery_id: str, intent_id: str,
    policy: ClarificationPolicy | None = None, expected_snapshot_version: str | None = None,
    now: datetime | None = None,
) -> str:
    """Claim a delivery and create its dictation atomically from its durable original."""
    if type(actor_user_id) is not int or actor_user_id <= 0:
        raise AgentChannelError("forbidden", "clarification actor required")
    if clinic_mode_enabled():
        claim = (actor_user_id, delivery_id, intent_id)
        if policy is None:
            if _claim_for_visibility.get() != claim:
                raise AgentChannelError("forbidden", "clarification policy required")
            _claim_for_visibility.set(None)
        else:
            from . import import_visibility

            may_target = policy.may_target

            def authorize_target(actor: int, scope: ImportScope, author: int, intent: ClinicImportIntent | None) -> bool:
                if may_target is None or may_target(actor, scope, author, intent) is not True:
                    return False
                _claim_for_visibility.set(claim)
                return True

            token = _claim_for_visibility.set(None)
            try:
                # Visibility checks the version and records the audit before calling back.
                return import_visibility.assign_unassigned(
                    replace(policy, may_target=authorize_target), actor_user_id=actor_user_id,
                    delivery_id=delivery_id, intent_id=intent_id,
                    expected_snapshot_version=expected_snapshot_version or "", now=now,
                )
            finally:
                _claim_for_visibility.reset(token)
    elif policy is not None:
        raise AgentChannelError("not_found", "import clarification")
    return _claim_unassigned(actor_user_id=actor_user_id, delivery_id=delivery_id, intent_id=intent_id, now=now)


def _claim_unassigned(*, actor_user_id: int, delivery_id: str, intent_id: str, now: datetime | None,
                      commit: bool = True) -> str:
    now = aware(now) or utcnow()
    intent = _own_intent(actor_user_id, intent_id)
    delivery = db.session.get(ClinicImportDelivery, delivery_id)
    if delivery is None:
        raise AgentChannelError("not_found", "delivery")
    if delivery.status != "UNASSIGNED":
        raise AgentChannelError("conflict", "delivery is already assigned")
    if intent.status != "OPEN" or aware(intent.expires_at) <= now:
        raise AgentChannelError("conflict", "intent is not open")
    if clinic_mode_enabled() and db.session.get(agent_channel.ClinicAgentIntent, intent.id) is not None:
        ingest = db.session.get(ClinicAgentIngest, delivery.ingest_id)
        if ingest is None or ingest.intent_id != intent.id:
            raise AgentChannelError("forbidden", "intent does not match the snapshot")
    claimed = ClinicImportDelivery.query.filter_by(id=delivery_id, status="UNASSIGNED").update({
        "intent_id": intent.id, "status": "MATCHED", "assigned_at": now,
        "language_id": intent.language_id,
    }, synchronize_session=False)
    if claimed != 1:
        raise AgentChannelError("conflict", "delivery is already assigned")
    db.session.refresh(delivery)
    original = db.session.get(ClinicImportOriginal, delivery.id)
    if original is not None:
        _heal_original(original, intent)
    if commit:
        db.session.commit()
    return delivery.id


# --- agent side -------------------------------------------------------------------------

def _resolved_author(item, session_row, device_map, alias_map, embedded_author_for):
    # A reported snapshot must still describe the bytes declared at begin_ingest.
    if item is None or (item.sha256, item.size) != (session_row.declared_sha256, session_row.declared_size):
        return None
    embedded = embedded_author_for(
        session_row.agent_id, session_row.source_id, session_row.snapshot_version, session_row.declared_sha256,
    ) if embedded_author_for is not None else None
    try:
        return resolve_author(
            device_id=item.root_id, embedded_author=embedded,
            device_map=device_map, alias_map=alias_map,
        ).author_user_id
    except AuthorResolutionError as exc:
        logger.warning("import author evidence rejected for ingest %s: %s", session_row.id, exc.code)
        return None


def _verified_author(item, session_row, stream, read_header, lookup, configured_author):
    if (item.sha256, item.size) != (session_row.declared_sha256, session_row.declared_size):
        return None
    try:
        embedded = read_header(stream)
    except AuthorResolutionError:
        return None
    finally:
        stream.seek(0)
    device = lookup(device_id=item.root_id)
    if device.reason != "no_input":
        owners = agent_channel.unique_root_owners({
            agent_id: (enabled, {root.upper() for root in roots})
            for agent_id, (enabled, roots) in agent_channel.registered_agent_roots().items()
        })
        if owners.get(item.root_id.upper()) != session_row.agent_id or device.author_user_id is None:
            return None
    device_author = device.author_user_id
    if configured_author is not None:
        if device_author is not None and device_author != configured_author:
            return None
        device_author = configured_author
    if embedded is None:
        return device_author
    header_author = lookup(embedded_author=embedded).author_user_id
    return header_author if device_author in (None, header_author) else None


def _clinic_delivery_intent(item, session_row, now, device_map, alias_map, embedded_author_for,
                            resolve_verified=None):
    from . import import_source_config

    if item is None:
        return None
    try:
        config = import_source_config.delivery_config(item.root_id, item.relative_path) or {}
    except AgentChannelError:
        return None
    if config:
        owners = agent_channel.unique_root_owners(agent_channel.registered_agent_roots())
        if owners.get(item.root_id) != session_row.agent_id:
            return None
    configured_author = config.get("target_author_user_id") if config.get("source_kind") == "device" else None
    if resolve_verified is not None:
        author = resolve_verified(item, configured_author)
    else:
        if configured_author is not None:
            device = item.root_id.strip().upper()
            if device_map.get(device, configured_author) != configured_author:
                return None
            device_map = {**device_map, device: configured_author}
        author = _resolved_author(item, session_row, device_map, alias_map, embedded_author_for)
    if author is None or (config and author != config["target_author_user_id"]):
        return None
    automatic = ClinicAutosendIntent.query.filter_by(agent_intent_id=session_row.intent_id).first()
    if automatic is not None:
        if not config or resolve_verified is None or automatic.inventory_id != item.id:
            return None
        intent = db.session.get(ClinicImportIntent, automatic.import_intent_id)
        if (intent is not None and intent.status == "OPEN" and aware(intent.expires_at) > now
                and (intent.target_author_user_id, intent.target_pool_id, intent.source_kind)
                == (author, config["target_pool_id"], config["source_kind"])):
            return intent
        return None
    paired = db.session.get(ClinicImportIntent, session_row.intent_id)
    if paired is not None:
        from .identity import ClinicAccountPolicy

        account = db.session.get(ClinicAccountPolicy, author)
        if (config and account is not None and account.status == "active" and paired.status == "OPEN" and aware(paired.expires_at) > now
                and paired.target_author_user_id == author and paired.target_pool_id == config["target_pool_id"]
                and paired.source_kind == config["source_kind"] and paired.source_ref == item.source_id
                and _fits(paired, item.root_id, item.source_id, item.relative_path)):
            return paired
        return None
    return _find_intent(item, now, author, target_pool_id=config.get("target_pool_id"),
                        source_kind=config.get("source_kind"))


def _record_delivery(agent, session_row, now, *, device_map, alias_map, embedded_author_for,
                     resolve_verified=None):
    delivery = ClinicImportDelivery.query.filter_by(ingest_id=session_row.id).first()
    if delivery is not None:
        return delivery
    item = inventory_row(agent.id, session_row.source_id, session_row.snapshot_version)
    if clinic_mode_enabled():
        intent = _clinic_delivery_intent(item, session_row, now, device_map, alias_map, embedded_author_for,
                                         resolve_verified)
    else:
        intent = _find_intent(item, now)
    delivery = ClinicImportDelivery(
        ingest_id=session_row.id, agent_id=agent.id, source_id=session_row.source_id,
        root_id=item.root_id if item else None, relative_path=item.relative_path if item else None,
        intent_id=intent.id if intent else None, status="MATCHED" if intent else "UNASSIGNED",
        language_id=intent.language_id if intent else resolve_language(None),
        assigned_at=now if intent else None, created_at=now,
    )
    db.session.add(delivery)
    db.session.commit()
    return delivery


def _remember_original(delivery, ingest_service, fields):
    """Persist verified storage metadata before returning author_required."""
    values = {
        "idempotency_key": fields["idempotency_key"], "storage_root": str(ingest_service.root),
        "storage_key": storage_key_for(fields["idempotency_key"]),
        "content_hash": fields["declared_sha256"], "size_bytes": fields["declared_size"],
        "origin": fields["origin"], "origin_ref": fields["origin_ref"],
    }
    original = db.session.get(ClinicImportOriginal, delivery.id)
    if original is None:
        original = ClinicImportOriginal(delivery_id=delivery.id, created_at=fields["now"], **values)
        db.session.add(original)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            original = db.session.get(ClinicImportOriginal, delivery.id)
            if original is None:
                raise
        except SQLAlchemyError:
            db.session.rollback()
            raise
    if any(getattr(original, name) != value for name, value in values.items()):
        raise IngestError("conflict", "stored original differs from the import delivery")
    return original


def _ingest_metadata(intent: ClinicImportIntent | None, delivery: ClinicImportDelivery) -> dict:
    if not clinic_mode_enabled() or intent is None or intent.metadata_version is None:
        return {}
    from . import import_source_config

    path = delivery.relative_path or ""
    source: dict = {}
    if delivery.root_id:
        try:
            source = import_source_config.resolve_source_config(intent.source_kind, delivery.root_id, path) or {}
        except AgentChannelError as exc:
            if exc.code != 'invalid':
                raise
    filename = re.split(r"[\\/]", path)[-1]
    title = filename.rsplit(".", 1)[0].strip()[:200] or None
    return {"intent_metadata": interpret(intent), "import_defaults": ImportDefaults(
        title=title, priority=source.get("priority", ImportDefaults.priority), pool_id=intent.target_pool_id,
    )}


def _author_injecting(ingest_service, author_user_id, delivery, intent):
    def store(stream, **kwargs):
        try:
            return ingest_service.store_original(stream, **{**kwargs, "author_user_id": author_user_id},
                                                 language_id=delivery.language_id,
                                                 **_ingest_metadata(intent, delivery))
        except IngestError as exc:
            if exc.code == "author_required":
                _remember_original(delivery, ingest_service, kwargs)
            raise
    return store


def _recover_original(delivery, session_row, ingest_service, now):
    """Reconcile UNKNOWN deliveries, including files published before this table existed."""
    original = db.session.get(ClinicImportOriginal, delivery.id)
    if original is not None:
        return original
    fields = {
        "idempotency_key": "agent-ingest:%s:%s" % (session_row.agent_id, session_row.idempotency_key),
        "declared_sha256": session_row.declared_sha256, "declared_size": session_row.declared_size,
        "origin": "agent", "origin_ref": "agent:%s:%s:%s" % (
            session_row.agent_id, session_row.source_id, session_row.snapshot_version,
        ), "now": now,
    }
    path = ingest_service.stored_path(
        idempotency_key=fields["idempotency_key"], declared_sha256=fields["declared_sha256"],
        declared_size=fields["declared_size"],
    )
    if path is None:
        return None
    return _remember_original(delivery, ingest_service, fields)


def _heal_original(original, intent):
    """Verify stored bytes and add the dictation within the caller's transaction."""
    locked = ClinicImportDelivery.query.filter_by(
        id=original.delivery_id, intent_id=intent.id, status="MATCHED",
    ).update({"status": "MATCHED"}, synchronize_session=False)
    if locked != 1:
        raise AgentChannelError("conflict", "delivery assignment changed")
    service = IngestService(original.storage_root)
    path = service.stored_path(
        idempotency_key=original.idempotency_key, declared_sha256=original.content_hash,
        declared_size=original.size_bytes,
    )
    if path is None:
        raise IngestError("storage_failed", "stored import original is missing")
    delivery = db.session.get(ClinicImportDelivery, original.delivery_id)
    metadata = _ingest_metadata(intent, delivery)
    with path.open("rb") as stream:
        receipt = service.store_original(
            stream, idempotency_key=original.idempotency_key, declared_sha256=original.content_hash,
            declared_size=original.size_bytes, origin=original.origin, origin_ref=original.origin_ref,
            author_user_id=intent.target_author_user_id, now=aware(original.created_at), commit=False,
            language_id=intent.language_id, **metadata,
        )
    if intent.target_pool_id and not metadata:
        db.session.get(ClinicDictation, receipt.dictation_id).pool_id = intent.target_pool_id
    return receipt


def _complete(delivery, intent, receipt):
    """After a STORED receipt: put the dictation into the target pool and settle the intent."""
    if receipt.get("state") != "STORED":
        return
    if intent.target_pool_id and receipt.get("dictation_id") and (
        not clinic_mode_enabled() or intent.metadata_version is None
    ):
        dictation = db.session.get(ClinicDictation, receipt["dictation_id"])
        if dictation is not None and dictation.pool_id != intent.target_pool_id:
            dictation.pool_id = intent.target_pool_id
    _refresh_fulfilment(intent)
    db.session.commit()


@transactional
def finalize_delivery(
    principal, policy, *, ingest_id: str, body: dict, ingest_service: IngestService,
    now: datetime | None = None, device_map: Mapping[str, int] | None = None,
    alias_map: Mapping[str, Collection[int]] | None = None,
    embedded_author_for: Callable[[str, str, str, str], str | None] | None = None,
    verified_author: tuple[Callable[[BinaryIO], str | None], Callable[..., Resolution]] | None = None,
) -> dict:
    """Finalize one agent upload through the import centre.

    In clinic mode, use server-owned device bindings (by normalized inventory root) and
    header aliases (normalized as in ``resolve_author``). ``embedded_author_for`` looks
    up validated header evidence by (agent id, source id, snapshot version, sha256).
    None of these inputs should be taken from the agent's finalize body.
    When supplied, ``verified_author`` reads and resolves the header only after
    channel verification and before the first delivery decision is persisted.

    A matching intent must target the resolved author. Otherwise, keep the permanent
    original and leave the session UNKNOWN until an explicit assignment. A retry
    uses the recorded delivery decision. The channel receipt includes an ``import`` block.
    """
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    session_row = db.session.get(ClinicAgentIngest, ingest_id)
    if session_row is None or session_row.agent_id != agent.id:
        raise AgentChannelError("not_found", "ingest session")
    can_record = session_row.state in ("RECEIVING", "UNKNOWN", "STORED")
    delivery = ClinicImportDelivery.query.filter_by(ingest_id=ingest_id).first() if can_record else None
    intent = db.session.get(ClinicImportIntent, delivery.intent_id) if delivery and delivery.intent_id else None
    use_verified = verified_author is not None and clinic_mode_enabled()

    def record_delivery(resolve_verified=None):
        nonlocal delivery, intent
        delivery = _record_delivery(
            agent, session_row, now, device_map=device_map or {}, alias_map=alias_map or {},
            embedded_author_for=embedded_author_for, resolve_verified=resolve_verified,
        )
        intent = db.session.get(ClinicImportIntent, delivery.intent_id) if delivery.intent_id else None

    if can_record and not use_verified and delivery is None:
        record_delivery()

    def store_original(stream, **fields):
        if delivery is None and verified_author is not None and use_verified:
            read_header, lookup = verified_author
            record_delivery(lambda item, configured: _verified_author(
                item, session_row, stream, read_header, lookup, configured,
            ))
        author = intent.target_author_user_id if intent else None
        return _author_injecting(ingest_service, author, delivery, intent)(stream, **fields)

    original = None
    if delivery is not None and session_row.state == "UNKNOWN":
        original = _recover_original(delivery, session_row, ingest_service, now)
    if original is not None and intent is None:
        receipt = get_receipt(principal, ingest_id=ingest_id)
    else:
        receipt = finalize_ingest(
            principal, policy, ingest_id=ingest_id, body=body, now=now,
            store_original=store_original,
            recover_original=(lambda: _heal_original(original, intent)) if original and intent else None,
        )
    if delivery is not None and intent is not None:
        _complete(delivery, intent, receipt)
    if delivery is not None:
        receipt = {**receipt, "import": {
            "delivery_id": delivery.id, "status": delivery.status, "intent_id": delivery.intent_id,
            "language_id": delivery.language_id,
        }}
    return receipt
