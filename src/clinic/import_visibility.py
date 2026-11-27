"""Clinic-mode boundary for web import clarification.

Callers supply the authenticated internal user ID and server-owned policy.
W13 permits active admins and the relevant typist-pool leads; the W7 role and
source-to-pool mapping remains an explicit policy input. Recording grants,
plain pool membership and identity-admin flags do not imply this authority.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from src.clinic import agent_channel, import_centre, pools, workflow
from src.clinic.agent_channel import AgentChannelError
from src.clinic.author_suggestion import suggest_author
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicOriginal
from src.clinic.voice_consent import has_active_consent
from src.database import db
from src.models import User
from src.services import audit


@dataclass(frozen=True)
class ImportScope:
    agent_id: str
    root_id: str | None
    source_id: str
    snapshot_version: str
    delivery_id: str | None = None


@dataclass(frozen=True)
class ClarificationPolicy:
    """Live, read-only decisions from trusted policy, including applicable denies.

    Source actions are ``list_unassigned``, ``read_inventory``, ``suggest_author``
    and ``assign_unassigned``. Target checks receive the persisted intent on
    assignment; on suggestion only the proposed author and import scope exist.
    """

    may_access: Callable[[int, str, ImportScope], bool] | None = None
    may_target: Callable[[int, ImportScope, int, import_centre.ClinicImportIntent | None], bool] | None = None


def _require_clinic() -> None:
    if not clinic_mode_enabled():
        raise AgentChannelError("not_found", "import clarification")


def _allows(policy: ClarificationPolicy, actor: int, action: str, scope: ImportScope) -> bool:
    return policy.may_access is not None and policy.may_access(actor, action, scope) is True


def list_unassigned(policy: ClarificationPolicy, *, actor_user_id: int) -> dict:
    _require_clinic()
    items = []
    if policy.may_access is not None:
        for row in import_centre.list_unassigned():
            ingest = db.session.get(agent_channel.ClinicAgentIngest, row["ingest_id"])
            if ingest is None:
                continue
            scope = ImportScope(row["agent_id"], row["root_id"], row["source_id"], ingest.snapshot_version, row["id"])
            if _allows(policy, actor_user_id, "list_unassigned", scope):
                items.append({key: value for key, value in row.items() if key != "storage_key"}
                             | {"snapshot_version": scope.snapshot_version})
    return {"items": items, "count": len(items)}


def list_inventory(
    policy: ClarificationPolicy, channel_policy: agent_channel.ChannelPolicy, *,
    actor_user_id: int, agent_id: str,
) -> dict:
    _require_clinic()
    items = []
    if policy.may_access is not None:
        for row in agent_channel.list_inventory(channel_policy, actor_user_id=actor_user_id, agent_id=agent_id):
            scope = ImportScope(agent_id, row["root_id"], row["source_id"], row["snapshot_version"])
            if _allows(policy, actor_user_id, "read_inventory", scope):
                items.append(row)
    return {"items": items, "count": len(items)}


def _require_delivery(
    policy: ClarificationPolicy, actor: int, action: str, delivery_id: str, *, lock: bool = False,
) -> tuple[import_centre.ClinicImportDelivery, ImportScope]:
    if policy.may_access is not None:
        delivery = db.session.get(
            import_centre.ClinicImportDelivery, delivery_id, populate_existing=True, with_for_update=lock,
        )
        if delivery is not None:
            ingest = db.session.get(agent_channel.ClinicAgentIngest, delivery.ingest_id)
            if ingest is not None:
                scope = ImportScope(delivery.agent_id, delivery.root_id, delivery.source_id,
                                    ingest.snapshot_version, delivery.id)
                if _allows(policy, actor, action, scope):
                    return delivery, scope
    raise AgentChannelError("not_found", "delivery")


def author_suggestions(
    policy: ClarificationPolicy, *, actor_user_id: int, delivery_id: str,
    load_evidence: Callable[[str], tuple[Sequence[float], Iterable[tuple[int, Sequence[float]]], float]],
) -> dict:
    _require_clinic()
    delivery, scope = _require_delivery(policy, actor_user_id, "suggest_author", delivery_id)
    if delivery.status != "UNASSIGNED":
        raise AgentChannelError("not_found", "delivery")
    may_target = policy.may_target
    if may_target is None:
        raise AgentChannelError("forbidden", "author scope unavailable")
    embedding, candidates, seconds = load_evidence(delivery.id)
    allowed = ((uid, vector) for uid, vector in candidates
               if may_target(actor_user_id, scope, uid, None) is True and has_active_consent(uid))
    return suggest_author(embedding, allowed, speech_seconds=seconds).to_dict()


def _targets():
    member, pool = pools.ClinicAuthorPoolMember, pools.ClinicAuthorPool
    return (db.session.query(User.id, User.name, User.username, pool).select_from(member)
            .join(User, User.id == member.user_id).join(pool, pool.id == member.pool_id)
            .join(ClinicAccountPolicy, ClinicAccountPolicy.user_id == User.id)
            .filter(ClinicAccountPolicy.status == "active"))


def assignment_candidates(policy: ClarificationPolicy, *, actor_user_id: int, delivery_id: str) -> list[dict]:
    _require_clinic()
    delivery, _ = _require_delivery(policy, actor_user_id, "assign_unassigned", delivery_id)
    if delivery.status != "UNASSIGNED":
        raise AgentChannelError("not_found", "delivery")
    now = agent_channel.utcnow()
    return [dict(target_author_user_id=uid, target_pool_id=pool.id, author_name=name or login, pool_name=pool.name)
            for uid, name, login, pool in _targets().order_by(User.username, pools.ClinicAuthorPool.name)
            if pools.is_routable(pool, now)]


def _audit_assignment(actor: int, delivery_id: str, intent: import_centre.ClinicImportIntent) -> None:
    event = audit.record_in_tx(
        "recording.edited", actor_user_id=actor, target_user_id=intent.target_author_user_id, force=True,
        details={"fields": {"import_delivery_id": delivery_id, "import_intent_id": intent.id}},
    )
    if event is None:
        raise AgentChannelError("unavailable", "assignment audit unavailable")


@agent_channel.transactional
def assign_target(
    policy: ClarificationPolicy, *, actor_user_id: int, delivery_id: str, expected_snapshot_version: str,
    target_author_user_id: int | None = None, target_pool_id: str | None = None, intent_id: str | None = None,
) -> str:
    """Resolve a durable delivery and publish its worklist row in the same transaction."""
    _require_clinic()
    delivery, scope = _require_delivery(policy, actor_user_id, "assign_unassigned", delivery_id, lock=True)
    if scope.snapshot_version != expected_snapshot_version or delivery.status != "UNASSIGNED":
        raise AgentChannelError("conflict", "delivery changed")
    original = db.session.get(import_centre.ClinicImportOriginal, delivery_id)
    if original is None:
        raise AgentChannelError("conflict", "delivery original is pending")

    now = agent_channel.utcnow()
    if intent_id is not None:
        intent = db.session.get(import_centre.ClinicImportIntent, intent_id, populate_existing=True, with_for_update=True)
        if intent is None or intent.created_by_user_id != actor_user_id:
            raise AgentChannelError("not_found", "import intent")
        if not import_centre._fits(intent, delivery.root_id, delivery.source_id, delivery.relative_path):
            raise AgentChannelError("forbidden", "intent does not match delivery")
        target_author_user_id, target_pool_id = intent.target_author_user_id, intent.target_pool_id
    pool = pools.ClinicAuthorPool
    target_pool = _targets().with_entities(pool).filter(User.id == target_author_user_id, pool.id == target_pool_id).first()
    if target_pool is None or not pools.is_routable(target_pool, now):
        raise AgentChannelError("forbidden", "assignment target is ineligible")

    if intent_id is None:
        intent = import_centre.ClinicImportIntent(
            created_by_user_id=actor_user_id, source_kind="folder", source_ref=delivery.source_id,
            target_author_user_id=target_author_user_id, target_pool_id=target_pool_id,
            language_id=delivery.language_id, created_at=now, expires_at=now + import_centre.INTENT_TTL,
        )
        db.session.add(intent)
        db.session.flush()
    _audit_assignment(actor_user_id, delivery_id, intent)
    import_centre._claim_unassigned(actor_user_id=actor_user_id, delivery_id=delivery_id, intent_id=intent.id,
                                    now=now, commit=False)
    if intent_id is None:
        intent.status = "FULFILLED"
    dictation_id = ClinicOriginal.query.filter_by(storage_key=original.storage_key).one().dictation_id
    if db.session.get(workflow.ClinicWorkflowDictation, dictation_id) is None:
        db.session.add(workflow.ClinicWorkflowDictation(
            id=dictation_id, author_user_id=target_author_user_id, pool_id=target_pool_id,
            created_at=agent_channel.aware(original.created_at), status=workflow.PREP_OFFLINE,
        ))
    db.session.commit()
    return dictation_id


@agent_channel.transactional
def assign_unassigned(
    policy: ClarificationPolicy, *, actor_user_id: int, delivery_id: str, intent_id: str,
    expected_snapshot_version: str, now: datetime | None = None,
) -> str:
    _require_clinic()
    delivery, scope = _require_delivery(policy, actor_user_id, "assign_unassigned", delivery_id, lock=True)
    intent = db.session.get(import_centre.ClinicImportIntent, intent_id, populate_existing=True, with_for_update=True)
    if intent is None or intent.created_by_user_id != actor_user_id:
        raise AgentChannelError("not_found", "import intent")
    author = intent.target_author_user_id
    if policy.may_target is None or policy.may_target(actor_user_id, scope, author, intent) is not True:
        raise AgentChannelError("forbidden", "assignment target outside scope")
    if scope.snapshot_version != expected_snapshot_version:
        raise AgentChannelError("conflict", "stale snapshot version")

    # The import service commits the claim, original hand-over and this audit row together.
    _audit_assignment(actor_user_id, delivery.id, intent)
    return import_centre.assign_unassigned(
        actor_user_id=actor_user_id, delivery_id=delivery.id, intent_id=intent.id, now=now,
    )
