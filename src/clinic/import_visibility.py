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

from src.clinic import agent_channel, import_centre
from src.clinic.agent_channel import AgentChannelError
from src.clinic.author_suggestion import suggest_author
from src.clinic.config import clinic_mode_enabled
from src.clinic.voice_consent import has_active_consent
from src.database import db
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
    event = audit.record_in_tx(
        "recording.edited", actor_user_id=actor_user_id, target_user_id=author, force=True,
        details={"fields": {"import_delivery_id": delivery.id, "import_intent_id": intent.id}},
    )
    if event is None:
        raise AgentChannelError("unavailable", "assignment audit unavailable")
    return import_centre.assign_unassigned(
        actor_user_id=actor_user_id, delivery_id=delivery.id, intent_id=intent.id, now=now,
    )
