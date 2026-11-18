"""Optional additional acceptance (Abnahme) rules for clinic dictations.

Acceptance criteria from brief:
1. can_decide: grants, deny overrides, required=False does not widen
2. decide(accept) by allowed actor; forbidden by non-allowed
3. decide(reject) requires reason 3..2000 chars NFC; keeps reason; handler_id = previous_assignee_id; None raises handler_required
4. is_current: only accept for exact head_revision_id; reject never current; new head invalidates old accept
5. final_allowed: unreviewed AI never final; human reviewed + required=False => True; required=True => current accept only
6. Independence from ASR: no field/parameter contains 'asr' or 'recognition'
7. Reject -> new revision -> new accept end-to-end
8. Invalid input raises AcceptanceError/ValueError with stable code
"""

import dataclasses
import inspect
import unicodedata
from datetime import datetime, timezone

import pytest

from src.clinic.acceptance_rules import (
    AcceptanceDecision,
    AcceptanceError,
    AcceptanceRule,
    can_decide,
    decide,
    final_allowed,
    is_current,
)


T0 = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


def test_can_decide_grants_and_deny_overrides():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1, 2]),
        acceptor_pool_ids=frozenset(["pool-a"]),
        deny_user_ids=frozenset([3]),
    )

    assert can_decide(rule, 1, []) is True
    assert can_decide(rule, 2, []) is True
    assert can_decide(rule, 99, ["pool-a"]) is True
    assert can_decide(rule, 99, ["pool-b"]) is False
    assert can_decide(rule, 3, []) is False  # deny overrides
    assert can_decide(rule, 3, ["pool-a"]) is False  # deny overrides pool grant

    # No grants configured -> nobody may decide
    empty_rule = AcceptanceRule(required=False)
    assert can_decide(empty_rule, 1, []) is False
    assert can_decide(empty_rule, 1, ["any-pool"]) is False

    # required=False does not widen who may decide
    rule_false = AcceptanceRule(
        required=False,
        acceptor_user_ids=frozenset([1]),
    )
    assert can_decide(rule_false, 1, []) is True
    assert can_decide(rule_false, 2, []) is False


def test_decide_accept_by_allowed_actor():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1]),
        acceptor_pool_ids=frozenset(["pool-a"]),
    )

    decision = decide(
        rule,
        kind="accept",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )

    assert decision.kind == "accept"
    assert decision.actor_id == 1
    assert decision.revision_id == "rev-123"
    assert decision.reason is None
    assert decision.handler_id is None
    assert decision.at == T0


def test_decide_accept_by_pool_member():
    rule = AcceptanceRule(
        required=True,
        acceptor_pool_ids=frozenset(["pool-a"]),
    )

    decision = decide(
        rule,
        kind="accept",
        actor_id=99,
        actor_pool_ids=["pool-a"],
        revision_id="rev-123",
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )

    assert decision.kind == "accept"
    assert decision.actor_id == 99


def test_decide_accept_by_non_allowed_raises_forbidden():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1]),
    )

    with pytest.raises(AcceptanceError) as exc:
        decide(
            rule,
            kind="accept",
            actor_id=2,
            actor_pool_ids=[],
            revision_id="rev-123",
            reason=None,
            previous_assignee_id=None,
            now=T0,
        )
    assert exc.value.code == "forbidden"


def test_decide_reject_requires_reason_3_to_2000_chars_nfc():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1]),
    )

    # Reason too short after NFC strip
    with pytest.raises(AcceptanceError) as exc:
        decide(
            rule,
            kind="reject",
            actor_id=1,
            actor_pool_ids=[],
            revision_id="rev-123",
            reason="  a  ",
            previous_assignee_id=5,
            now=T0,
        )
    assert exc.value.code == "reason_required"

    # Reason too long after NFC strip
    long_reason = "x" * 2001
    with pytest.raises(AcceptanceError) as exc:
        decide(
            rule,
            kind="reject",
            actor_id=1,
            actor_pool_ids=[],
            revision_id="rev-123",
            reason=long_reason,
            previous_assignee_id=5,
            now=T0,
        )
    assert exc.value.code == "reason_required"

    # Valid reason exactly 3 chars
    decision = decide(
        rule,
        kind="reject",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason="abc",
        previous_assignee_id=5,
        now=T0,
    )
    assert decision.kind == "reject"
    assert decision.reason == "abc"
    assert decision.handler_id == 5

    # Valid reason exactly 2000 chars
    decision = decide(
        rule,
        kind="reject",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason="x" * 2000,
        previous_assignee_id=5,
        now=T0,
    )
    assert decision.reason == "x" * 2000

    # NFC normalisation: combining char sequence counts as 1 char after NFC
    nfd = "Cafe\u0301"  # 5 code points, 4 NFC chars
    decision = decide(
        rule,
        kind="reject",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason=nfd,
        previous_assignee_id=5,
        now=T0,
    )
    assert decision.reason == "Caf\u00e9"  # NFC normalised
    assert len(decision.reason) == 4


def test_decide_reject_keeps_reason_verbatim_after_nfc_strip():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1]),
    )

    # Whitespace is stripped but internal whitespace preserved
    reason = "  This is a reason  with  spaces  "
    decision = decide(
        rule,
        kind="reject",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason=reason,
        previous_assignee_id=5,
        now=T0,
    )
    assert decision.reason == "This is a reason  with  spaces"


def test_decide_reject_handler_id_is_previous_assignee_id():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1]),
    )

    decision = decide(
        rule,
        kind="reject",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason="not good enough",
        previous_assignee_id=42,
        now=T0,
    )
    assert decision.handler_id == 42


def test_decide_reject_without_previous_assignee_raises_handler_required():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1]),
    )

    with pytest.raises(AcceptanceError) as exc:
        decide(
            rule,
            kind="reject",
            actor_id=1,
            actor_pool_ids=[],
            revision_id="rev-123",
            reason="not good enough",
            previous_assignee_id=None,
            now=T0,
        )
    assert exc.value.code == "handler_required"


def test_is_current_only_for_accept_on_exact_head():
    accept_decision = AcceptanceDecision(
        kind="accept",
        actor_id=1,
        revision_id="rev-123",
        reason=None,
        handler_id=None,
        at=T0,
    )
    reject_decision = AcceptanceDecision(
        kind="reject",
        actor_id=1,
        revision_id="rev-123",
        reason="nope",
        handler_id=5,
        at=T0,
    )

    # Accept matches head -> current
    assert is_current(accept_decision, "rev-123") is True

    # Accept for different revision -> not current
    assert is_current(accept_decision, "rev-456") is False

    # Reject is never current
    assert is_current(reject_decision, "rev-123") is False

    # None decision is not current
    assert is_current(None, "rev-123") is False


def test_final_allowed_unreviewed_ai_never_final():
    rule_required = AcceptanceRule(required=True, acceptor_user_ids=frozenset([1]))
    rule_not_required = AcceptanceRule(required=False, acceptor_user_ids=frozenset([1]))

    accept_decision = AcceptanceDecision(
        kind="accept",
        actor_id=1,
        revision_id="rev-123",
        reason=None,
        handler_id=None,
        at=T0,
    )

    # head_is_human_reviewed=False -> False for both rules
    assert final_allowed(rule_required, head_is_human_reviewed=False, latest_decision=accept_decision, head_revision_id="rev-123") is False
    assert final_allowed(rule_not_required, head_is_human_reviewed=False, latest_decision=accept_decision, head_revision_id="rev-123") is False
    assert final_allowed(rule_required, head_is_human_reviewed=False, latest_decision=None, head_revision_id="rev-123") is False
    assert final_allowed(rule_not_required, head_is_human_reviewed=False, latest_decision=None, head_revision_id="rev-123") is False


def test_final_allowed_human_reviewed_required_false_always_true():
    rule = AcceptanceRule(required=False, acceptor_user_ids=frozenset([1]))

    # With human reviewed head and required=False -> True regardless of decision
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=None, head_revision_id="rev-123") is True
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=AcceptanceDecision(
        kind="reject", actor_id=1, revision_id="rev-123", reason="nope", handler_id=5, at=T0
    ), head_revision_id="rev-123") is True


def test_final_allowed_required_true_only_with_current_accept():
    rule = AcceptanceRule(required=True, acceptor_user_ids=frozenset([1]))

    current_accept = AcceptanceDecision(
        kind="accept", actor_id=1, revision_id="rev-123", reason=None, handler_id=None, at=T0
    )
    stale_accept = AcceptanceDecision(
        kind="accept", actor_id=1, revision_id="rev-old", reason=None, handler_id=None, at=T0
    )
    reject_decision = AcceptanceDecision(
        kind="reject", actor_id=1, revision_id="rev-123", reason="nope", handler_id=5, at=T0
    )

    # Current accept -> True
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=current_accept, head_revision_id="rev-123") is True

    # Stale accept (different revision) -> False
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=stale_accept, head_revision_id="rev-123") is False

    # Reject -> False
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=reject_decision, head_revision_id="rev-123") is False

    # No decision -> False
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=None, head_revision_id="rev-123") is False


def test_reject_then_new_revision_then_new_accept_end_to_end():
    rule = AcceptanceRule(required=True, acceptor_user_ids=frozenset([1]))

    # Step 1: Reject revision A
    reject = decide(
        rule,
        kind="reject",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-A",
        reason="needs work",
        previous_assignee_id=5,
        now=T0,
    )
    assert reject.kind == "reject"
    assert reject.reason == "needs work"
    assert reject.handler_id == 5
    assert is_current(reject, "rev-A") is False  # reject never current

    # Step 2: New revision B created (simulated by new head_revision_id)
    # Step 3: Accept revision B
    accept = decide(
        rule,
        kind="accept",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-B",
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )
    assert accept.kind == "accept"
    assert accept.revision_id == "rev-B"

    # Old reject still readable
    assert reject.reason == "needs work"
    assert reject.revision_id == "rev-A"

    # New accept is current for new head
    assert is_current(accept, "rev-B") is True
    assert is_current(accept, "rev-A") is False

    # Final allowed with required=True and current accept
    assert final_allowed(rule, head_is_human_reviewed=True, latest_decision=accept, head_revision_id="rev-B") is True


def test_invalid_inputs_raise_with_stable_codes():
    rule = AcceptanceRule(required=True, acceptor_user_ids=frozenset([1]))

    # Bool actor_id
    with pytest.raises(AcceptanceError) as exc:
        can_decide(rule, True, [])
    assert exc.value.code == "invalid_actor_id"

    with pytest.raises(AcceptanceError) as exc:
        decide(rule, kind="accept", actor_id=True, actor_pool_ids=[], revision_id="rev", reason=None, previous_assignee_id=None, now=T0)
    assert exc.value.code == "invalid_actor_id"

    # Empty revision_id
    with pytest.raises(AcceptanceError) as exc:
        decide(rule, kind="accept", actor_id=1, actor_pool_ids=[], revision_id="", reason=None, previous_assignee_id=None, now=T0)
    assert exc.value.code == "invalid_revision_id"

    with pytest.raises(AcceptanceError) as exc:
        is_current(AcceptanceDecision(kind="accept", actor_id=1, revision_id="rev", reason=None, handler_id=None, at=T0), "")
    assert exc.value.code == "invalid_head_revision_id"

    # Unknown kind
    with pytest.raises(AcceptanceError) as exc:
        decide(rule, kind="maybe", actor_id=1, actor_pool_ids=[], revision_id="rev", reason=None, previous_assignee_id=None, now=T0)
    assert exc.value.code == "invalid_kind"

    # Naive datetime
    naive = datetime(2026, 11, 17, 8, 0)
    with pytest.raises(ValueError):
        decide(rule, kind="accept", actor_id=1, actor_pool_ids=[], revision_id="rev", reason=None, previous_assignee_id=None, now=naive)

    with pytest.raises(ValueError):
        is_current(AcceptanceDecision(kind="accept", actor_id=1, revision_id="rev", reason=None, handler_id=None, at=naive), "rev")

    # Invalid rule fields
    with pytest.raises(AcceptanceError) as exc:
        AcceptanceRule(required="yes")
    assert exc.value.code == "invalid_required"

    with pytest.raises(AcceptanceError) as exc:
        AcceptanceRule(required=True, acceptor_user_ids=frozenset([True]))
    assert exc.value.code == "invalid_acceptor_user_ids"

    with pytest.raises(AcceptanceError) as exc:
        AcceptanceRule(required=True, acceptor_pool_ids=frozenset([""]))
    assert exc.value.code == "invalid_acceptor_pool_ids"


def test_independence_from_asr_no_field_or_parameter_couples():
    # Check dataclass fields
    for cls in (AcceptanceRule, AcceptanceDecision):
        for field in dataclasses.fields(cls):
            name = field.name.lower()
            assert "asr" not in name, f"{cls.__name__}.{field.name} couples to ASR"
            assert "recognition" not in name, f"{cls.__name__}.{field.name} couples to recognition"

    # Check function parameters
    for func in (can_decide, decide, is_current, final_allowed):
        sig = inspect.signature(func)
        for param_name in sig.parameters:
            name = param_name.lower()
            assert "asr" not in name, f"{func.__name__}({param_name}) couples to ASR"
            assert "recognition" not in name, f"{func.__name__}({param_name}) couples to recognition"


def test_decide_reason_optional_for_accept():
    rule = AcceptanceRule(required=True, acceptor_user_ids=frozenset([1]))

    decision = decide(
        rule,
        kind="accept",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason="some note",
        previous_assignee_id=None,
        now=T0,
    )
    assert decision.reason == "some note"

    decision = decide(
        rule,
        kind="accept",
        actor_id=1,
        actor_pool_ids=[],
        revision_id="rev-123",
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )
    assert decision.reason is None


def test_can_decide_with_multiple_pools():
    rule = AcceptanceRule(
        required=True,
        acceptor_pool_ids=frozenset(["pool-a", "pool-b"]),
    )

    assert can_decide(rule, 1, ["pool-a"]) is True
    assert can_decide(rule, 1, ["pool-b"]) is True
    assert can_decide(rule, 1, ["pool-c"]) is False
    assert can_decide(rule, 1, ["pool-a", "pool-b"]) is True


def test_acceptance_rule_frozenset_immutability():
    rule = AcceptanceRule(
        required=True,
        acceptor_user_ids=frozenset([1, 2]),
        acceptor_pool_ids=frozenset(["pool-a"]),
        deny_user_ids=frozenset([3]),
    )

    # All fields are frozenset, should be immutable
    assert isinstance(rule.acceptor_user_ids, frozenset)
    assert isinstance(rule.acceptor_pool_ids, frozenset)
    assert isinstance(rule.deny_user_ids, frozenset)


def test_acceptance_decision_immutability():
    decision = AcceptanceDecision(
        kind="accept",
        actor_id=1,
        revision_id="rev-123",
        reason=None,
        handler_id=None,
        at=T0,
    )

    # Dataclass is frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.kind = "reject"


def test_acceptance_error_has_code_attribute():
    try:
        raise AcceptanceError("test_code", "test message")
    except AcceptanceError as e:
        assert e.code == "test_code"
        assert str(e) == "test message"

    try:
        raise AcceptanceError("only_code")
    except AcceptanceError as e:
        assert e.code == "only_code"
        assert str(e) == "only_code"


def test_final_allowed_head_is_human_reviewed_must_be_bool():
    rule = AcceptanceRule(required=True)

    with pytest.raises(AcceptanceError) as exc:
        final_allowed(rule, head_is_human_reviewed="yes", latest_decision=None, head_revision_id="rev")
    assert exc.value.code == "invalid_head_is_human_reviewed"


def test_final_allowed_latest_decision_must_be_decision_or_none():
    rule = AcceptanceRule(required=True)

    with pytest.raises(AcceptanceError) as exc:
        final_allowed(rule, head_is_human_reviewed=True, latest_decision="not a decision", head_revision_id="rev")
    assert exc.value.code == "invalid_latest_decision"


def test_can_decide_actor_pool_ids_must_be_collection():
    rule = AcceptanceRule(required=True)

    with pytest.raises(AcceptanceError) as exc:
        can_decide(rule, 1, "not a collection")
    assert exc.value.code == "invalid_actor_pool_ids"


def test_decide_actor_pool_ids_items_must_be_str():
    rule = AcceptanceRule(required=True, acceptor_pool_ids=frozenset(["pool-a"]))

    with pytest.raises(AcceptanceError) as exc:
        decide(rule, kind="accept", actor_id=1, actor_pool_ids=[123], revision_id="rev", reason=None, previous_assignee_id=None, now=T0)
    assert exc.value.code == "invalid_pool_id"


def test_decide_invalid_rule_raises():
    with pytest.raises(AcceptanceError) as exc:
        decide("not a rule", kind="accept", actor_id=1, actor_pool_ids=[], revision_id="rev", reason=None, previous_assignee_id=None, now=T0)
    assert exc.value.code == "invalid_rule"


def test_is_current_invalid_decision_raises():
    with pytest.raises(AcceptanceError) as exc:
        is_current("not a decision", "rev")
    assert exc.value.code == "invalid_decision"