"""Optional additional acceptance (Abnahme) rules for clinic dictations.

This module is independent of speech recognition (ASR). No field or
parameter couples acceptance with ASR.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Collection
from datetime import datetime
from typing import Any


class AcceptanceError(ValueError):
    """An acceptance rule or decision was rejected."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclasses.dataclass(frozen=True)
class AcceptanceRule:
    """Rule that governs who may accept or reject a revision.

    Attributes:
        required: When True, a current accept decision is required for final
            output. When False, final output is allowed without acceptance
            if the head revision is human-reviewed.
        acceptor_user_ids: Explicit user IDs allowed to decide.
        acceptor_pool_ids: Pool IDs whose members are allowed to decide.
        deny_user_ids: User IDs explicitly denied; overrides all grants.
    """

    required: bool
    acceptor_user_ids: frozenset[int] = frozenset()
    acceptor_pool_ids: frozenset[str] = frozenset()
    deny_user_ids: frozenset[int] = frozenset()

    def __post_init__(self):
        if not isinstance(self.required, bool):
            raise AcceptanceError("invalid_required", "required must be a bool")
        for field_name in ("acceptor_user_ids", "acceptor_pool_ids", "deny_user_ids"):
            value = getattr(self, field_name)
            if not isinstance(value, frozenset):
                raise AcceptanceError(f"invalid_{field_name}", f"{field_name} must be a frozenset")
            for item in value:
                if field_name.endswith("_user_ids"):
                    if isinstance(item, bool) or not isinstance(item, int):
                        raise AcceptanceError(
                            f"invalid_{field_name}", f"{field_name} items must be int"
                        )
                else:
                    if not isinstance(item, str) or item == "":
                        raise AcceptanceError(
                            f"invalid_{field_name}", f"{field_name} items must be non-empty str"
                        )


@dataclasses.dataclass(frozen=True)
class AcceptanceDecision:
    """An accept or reject decision bound to an exact revision."""

    kind: str  # "accept" | "reject"
    actor_id: int
    revision_id: str
    reason: str | None
    handler_id: int | None
    at: datetime

    def __post_init__(self):
        if not isinstance(self.kind, str) or self.kind not in ("accept", "reject"):
            raise AcceptanceError("invalid_kind", "kind must be 'accept' or 'reject'")
        if isinstance(self.actor_id, bool) or not isinstance(self.actor_id, int):
            raise AcceptanceError("invalid_actor_id", "actor_id must be an int")
        if not isinstance(self.revision_id, str) or self.revision_id == "":
            raise AcceptanceError("invalid_revision_id", "revision_id must be a non-empty str")
        if self.reason is not None and not isinstance(self.reason, str):
            raise AcceptanceError("invalid_reason", "reason must be a str or None")
        if self.handler_id is not None and (isinstance(self.handler_id, bool) or not isinstance(self.handler_id, int)):
            raise AcceptanceError("invalid_handler_id", "handler_id must be an int or None")
        if not isinstance(self.at, datetime):
            raise AcceptanceError("invalid_at", "at must be a datetime")
        if self.at.tzinfo is None or self.at.tzinfo.utcoffset(self.at) is None:
            raise AcceptanceError("naive_datetime", "at must be timezone-aware")


def _require_aware(now: datetime, name: str = "now") -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(f"{name} must be timezone-aware")
    return now.astimezone(timezone.utc)


def _normalise_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    import unicodedata

    normalised = unicodedata.normalize("NFC", reason)
    stripped = normalised.strip()
    return stripped


def can_decide(
    rule: AcceptanceRule, actor_id: int, actor_pool_ids: Collection[str]
) -> bool:
    """Return True when the actor may decide under the rule.

    A user in deny_user_ids is never allowed, regardless of grants.
    A user in acceptor_user_ids or with a pool in acceptor_pool_ids is allowed.
    When no grants are configured, nobody may decide.
    required=False does not widen who may decide.
    """
    if not isinstance(rule, AcceptanceRule):
        raise AcceptanceError("invalid_rule", "rule must be an AcceptanceRule")
    if isinstance(actor_id, bool) or not isinstance(actor_id, int):
        raise AcceptanceError("invalid_actor_id", "actor_id must be an int")
    if isinstance(actor_pool_ids, str) or not isinstance(actor_pool_ids, Collection):
        raise AcceptanceError("invalid_actor_pool_ids", "actor_pool_ids must be a Collection of str")

    if actor_id in rule.deny_user_ids:
        return False

    if actor_id in rule.acceptor_user_ids:
        return True

    for pool_id in actor_pool_ids:
        if not isinstance(pool_id, str):
            raise AcceptanceError("invalid_pool_id", "pool IDs must be str")
        if pool_id in rule.acceptor_pool_ids:
            return True

    return False


def decide(
    rule: AcceptanceRule,
    *,
    kind: str,
    actor_id: int,
    actor_pool_ids: Collection[str],
    revision_id: str,
    reason: str | None,
    previous_assignee_id: int | None,
    now: datetime,
) -> AcceptanceDecision:
    """Record an accept or reject decision bound to an exact revision.

    Raises:
        AcceptanceError('forbidden'): Actor is not allowed to decide.
        AcceptanceError('reason_required'): Reject with reason < 3 or > 2000 chars after NFC strip.
        AcceptanceError('handler_required'): Reject with previous_assignee_id=None.
        AcceptanceError('invalid_kind'): Unknown kind.
        ValueError: Naive datetime.
    """
    _require_aware(now, "now")

    if not can_decide(rule, actor_id, actor_pool_ids):
        raise AcceptanceError("forbidden")

    if kind not in ("accept", "reject"):
        raise AcceptanceError("invalid_kind", "kind must be 'accept' or 'reject'")

    if not isinstance(revision_id, str) or revision_id == "":
        raise AcceptanceError("invalid_revision_id", "revision_id must be a non-empty str")

    normalised_reason = _normalise_reason(reason)

    if kind == "reject":
        if normalised_reason is None or len(normalised_reason) < 3 or len(normalised_reason) > 2000:
            raise AcceptanceError("reason_required")
        if previous_assignee_id is None:
            raise AcceptanceError("handler_required")
        handler_id = previous_assignee_id
    else:
        handler_id = None

    return AcceptanceDecision(
        kind=kind,
        actor_id=actor_id,
        revision_id=revision_id,
        reason=normalised_reason,
        handler_id=handler_id,
        at=now,
    )


def is_current(decision: AcceptanceDecision | None, head_revision_id: str) -> bool:
    """Return True only for a current accept decision matching the head revision.

    A reject decision is never current for acceptance purposes.
    A new head revision makes an old accept not current.
    """
    if not isinstance(head_revision_id, str) or head_revision_id == "":
        raise AcceptanceError("invalid_head_revision_id", "head_revision_id must be a non-empty str")

    if decision is None:
        return False

    if not isinstance(decision, AcceptanceDecision):
        raise AcceptanceError("invalid_decision", "decision must be an AcceptanceDecision or None")

    if decision.kind != "accept":
        return False

    return decision.revision_id == head_revision_id


def final_allowed(
    rule: AcceptanceRule,
    *,
    head_is_human_reviewed: bool,
    latest_decision: AcceptanceDecision | None,
    head_revision_id: str,
) -> bool:
    """Return True when final output is allowed under the rule.

    Unreviewed AI text (head_is_human_reviewed=False) is never final, even with required=False.
    With a human-reviewed head and required=False, final is allowed.
    With required=True, final is allowed only when the latest decision is a current accept.
    """
    if not isinstance(rule, AcceptanceRule):
        raise AcceptanceError("invalid_rule", "rule must be an AcceptanceRule")
    if not isinstance(head_is_human_reviewed, bool):
        raise AcceptanceError("invalid_head_is_human_reviewed", "head_is_human_reviewed must be a bool")
    if not isinstance(head_revision_id, str) or head_revision_id == "":
        raise AcceptanceError("invalid_head_revision_id", "head_revision_id must be a non-empty str")
    if latest_decision is not None and not isinstance(latest_decision, AcceptanceDecision):
        raise AcceptanceError("invalid_latest_decision", "latest_decision must be an AcceptanceDecision or None")

    if not head_is_human_reviewed:
        return False

    if not rule.required:
        return True

    return is_current(latest_decision, head_revision_id)


# --- Independence from ASR assertion helpers ---------------------------------

def _assert_no_asr_coupling() -> None:
    """Assert that no field or parameter name contains 'asr' or 'recognition'."""
    for cls in (AcceptanceRule, AcceptanceDecision):
        for field in dataclasses.fields(cls):
            name = field.name.lower()
            if "asr" in name or "recognition" in name:
                raise AssertionError(f"{cls.__name__}.{field.name} couples to ASR")

    for func in (can_decide, decide, is_current, final_allowed):
        sig = inspect.signature(func)
        for param_name in sig.parameters:
            name = param_name.lower()
            if "asr" in name or "recognition" in name:
                raise AssertionError(f"{func.__name__}({param_name}) couples to ASR")


_assert_no_asr_coupling()

from datetime import timezone  # noqa: E402  (imported after use in _require_aware)