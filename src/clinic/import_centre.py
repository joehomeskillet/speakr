"""Import centre service: typed import intents of a signed-in user and the matching of agent deliveries.

A user declares what is about to arrive (source device or folder, expected file
names, target author and pool). The agent channel (``agent_channel_ingest``)
stays unchanged: it finalizes with ``author_user_id=None`` and the original
store answers ``author_required`` until an author is supplied. This service
decides the author. ``finalize_delivery`` finds the open intent that fits the
delivery, calls ``finalize_ingest`` with a store callable that adds the intent's
author, and the same idempotency key lets the ingest service heal the row.

A delivery without a matching intent is kept on the clarification list
(status ``UNASSIGNED``). Its bytes and its ingest session stay; nothing is
dropped. Assigning it to an intent and repeating the agent's finalize completes it.

No Flask routes live here. Callers supply the signed-in user id.
"""

import json
import re
import uuid
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy import CheckConstraint, ForeignKey

from src.database import db
from src.models import User

from .agent_channel import (
    AgentChannelError, ClinicAgentIngest, agent_of, aware, inventory_row, transactional, utcnow,
)
from .agent_channel_ingest import finalize_ingest
from .models import ClinicDictation

# Owner decision 2026-11-18: files without a language are German. Windows LangId 1031 = de-DE.
DEFAULT_LANG_ID = 1031
INTENT_TTL = timedelta(hours=24)  # owner-confirmed default
SOURCE_KINDS = ("device", "folder")
INTENT_STATES = ("OPEN", "FULFILLED", "EXPIRED", "CANCELLED")
DELIVERY_STATES = ("MATCHED", "UNASSIGNED")
_REF = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_CONTROL = re.compile(r"[\x00-\x1f]")
_MAX_FILES = 500


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
    status = db.Column(db.String(16), nullable=False, default="OPEN")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("source_kind IN ('device','folder')", name="ck_clinic_import_intent_kind"),
        CheckConstraint(
            "status IN ('OPEN','FULFILLED','EXPIRED','CANCELLED')", name="ck_clinic_import_intent_status",
        ),
    )


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


IMPORT_TABLES = (ClinicImportIntent, ClinicImportDelivery)


def create_tables(bind=None):
    db.metadata.create_all(bind=bind or db.engine, tables=[m.__table__ for m in IMPORT_TABLES], checkfirst=True)


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
        "expires_at": aware(intent.expires_at).isoformat(),
    }


# --- user side ------------------------------------------------------------------------

@transactional
def create_import_intent(
    *, actor_user_id, source_kind, source_ref, expected_files=(), target_author_user_id=None,
    target_pool_id=None, language_id=None, may_assign_author: Optional[Callable[[int, int], bool]] = None,
    now=None,
):
    """A signed-in user announces an import. The author is the user unless ``may_assign_author`` allows another."""
    now = aware(now) or utcnow()
    if db.session.get(User, actor_user_id) is None:
        raise AgentChannelError("forbidden", "unknown user")
    if source_kind not in SOURCE_KINDS:
        raise AgentChannelError("invalid", "source_kind")
    if not isinstance(source_ref, str) or not _REF.match(source_ref):
        raise AgentChannelError("invalid", "source_ref")
    author = actor_user_id if target_author_user_id is None else target_author_user_id
    if author != actor_user_id:
        if may_assign_author is None or not may_assign_author(actor_user_id, author):
            raise AgentChannelError("forbidden", "user may not import for this author")
        if db.session.get(User, author) is None:
            raise AgentChannelError("invalid", "target author")
    intent = ClinicImportIntent(
        created_by_user_id=actor_user_id, source_kind=source_kind, source_ref=source_ref,
        expected_files=json.dumps(_clean_files(expected_files)), target_author_user_id=author,
        target_pool_id=target_pool_id, language_id=resolve_language(language_id),
        created_at=now, expires_at=now + INTENT_TTL,
    )
    db.session.add(intent)
    db.session.commit()
    return _view(intent)


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


def list_unassigned(*, now=None):
    """Clarification list: deliveries no open intent claimed. Authorisation is the caller's job."""
    return [
        {"id": d.id, "ingest_id": d.ingest_id, "agent_id": d.agent_id, "source_id": d.source_id,
         "root_id": d.root_id, "relative_path": d.relative_path, "language_id": d.language_id,
         "created_at": aware(d.created_at).isoformat()}
        for d in ClinicImportDelivery.query.filter_by(status="UNASSIGNED").order_by(ClinicImportDelivery.created_at)
    ]


# --- matching ---------------------------------------------------------------------------

def _fits(intent, root_id, source_id, relative_path):
    if intent.source_ref not in (root_id, source_id):
        return False
    expected = json.loads(intent.expected_files)
    return not expected or _file_name(relative_path or "") in expected


def _find_intent(item, now):
    """Oldest open, unexpired intent that fits the reported file; ``None`` if there is none."""
    if item is None:
        return None
    query = ClinicImportIntent.query.filter_by(status="OPEN").order_by(ClinicImportIntent.created_at)
    for intent in query:
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
def assign_unassigned(*, actor_user_id, delivery_id, intent_id, now=None):
    """The intent's owner claims a clarification-list delivery. The agent's repeated finalize completes it."""
    now = aware(now) or utcnow()
    intent = _own_intent(actor_user_id, intent_id)
    delivery = db.session.get(ClinicImportDelivery, delivery_id)
    if delivery is None:
        raise AgentChannelError("not_found", "delivery")
    if delivery.status != "UNASSIGNED":
        raise AgentChannelError("conflict", "delivery is already assigned")
    if intent.status != "OPEN" or aware(intent.expires_at) <= now:
        raise AgentChannelError("conflict", "intent is not open")
    delivery.intent_id, delivery.status, delivery.assigned_at = intent.id, "MATCHED", now
    delivery.language_id = intent.language_id
    db.session.commit()
    return delivery.id


# --- agent side -------------------------------------------------------------------------

def _record_delivery(agent, session_row, now):
    delivery = ClinicImportDelivery.query.filter_by(ingest_id=session_row.id).first()
    if delivery is not None:
        return delivery
    item = inventory_row(agent.id, session_row.source_id, session_row.snapshot_version)
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


def _author_injecting(store_original, author_user_id):
    def store(stream, **kwargs):
        return store_original(stream, **{**kwargs, "author_user_id": author_user_id})
    return store


def _complete(delivery, intent, receipt):
    """After a STORED receipt: put the dictation into the target pool and settle the intent."""
    if receipt.get("state") != "STORED":
        return
    if intent.target_pool_id and receipt.get("dictation_id"):
        dictation = db.session.get(ClinicDictation, receipt["dictation_id"])
        if dictation is not None and dictation.pool_id != intent.target_pool_id:
            dictation.pool_id = intent.target_pool_id
    _refresh_fulfilment(intent)
    db.session.commit()


@transactional
def finalize_delivery(principal, policy, *, ingest_id, body, ingest_service, now=None):
    """Finalize one agent upload through the import centre.

    ``ingest_service`` is an ``IngestService``. With a matching intent the original is stored for the
    intent's author. Without one the bytes are stored and the session stays UNKNOWN (``author_required``),
    the delivery appears on the clarification list. The receipt is the channel's receipt plus an
    ``import`` block (delivery id, status, intent id, language id).
    """
    agent = agent_of(principal)
    now = aware(now) or utcnow()
    session_row = db.session.get(ClinicAgentIngest, ingest_id)
    if session_row is None or session_row.agent_id != agent.id:
        raise AgentChannelError("not_found", "ingest session")
    delivery = None
    if session_row.state in ("RECEIVING", "UNKNOWN", "STORED"):
        delivery = _record_delivery(agent, session_row, now)
    intent = db.session.get(ClinicImportIntent, delivery.intent_id) if delivery and delivery.intent_id else None
    author = intent.target_author_user_id if intent else None
    receipt = finalize_ingest(
        principal, policy, ingest_id=ingest_id, body=body, now=now,
        store_original=_author_injecting(ingest_service.store_original, author),
    )
    if delivery is not None and intent is not None:
        _complete(delivery, intent, receipt)
    if delivery is not None:
        receipt = {**receipt, "import": {
            "delivery_id": delivery.id, "status": delivery.status, "intent_id": delivery.intent_id,
            "language_id": delivery.language_id,
        }}
    return receipt
