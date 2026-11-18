"""Versioned acceptance participants (PAR-WF-009, PAR-WF-010).

The original BT-WF-009 and BT-WF-010 oracles stay in the spec and are not
replaced. This file checks that text and the slice's target behaviour.
Synthetic users only: Alpha the author, Beta an approver, Gamma denied.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.clinic.acceptance_policy import (
    LABEL_ADMIN,
    LABEL_AUTHOR,
    SCOPE_DICTATION,
    SCOPE_ROUTING,
    AcceptanceLedger,
    PolicyError,
)
from src.clinic.acceptance_rules import AcceptanceError
from src.clinic.workflow import ACCEPTED, DONE, LABELS, TO_ACCEPT, TO_CORRECT, TRANSITIONS

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 18, 9, 5, tzinfo=timezone.utc)
ALPHA, BETA, GAMMA, ADMIN = 1, 2, 3, 9
HEAD = "rev-1"
HEAD2 = "rev-2"


def _policy(ledger: AcceptanceLedger, scope: str = SCOPE_DICTATION, subject: str = "case-1"):
    return ledger.publish(
        scope,
        subject,
        required=True,
        approver_user_ids=[BETA],
        approver_pool_ids=["pool-authors"],
        deny_user_ids=[GAMMA],
        now=T0,
    )


def test_original_oracles_unchanged():
    raw = json.loads(
        Path("clinic-spec/sources/dns-functional/specs/behavior_tests.json").read_text()
    )
    cases = {row["id"]: row for row in raw["test_cases"]}
    assert cases["BT-WF-009"]["then"] == [
        "Nur berechtigte Person kann abnehmen.",
        "Ohne weitere Regel kann der Autor selbst freigeben.",
        "Korrigiert, abzunehmen und abgenommen bleiben unterschiedliche Zustände.",
    ]
    assert cases["BT-WF-010"]["then"] == [
        "Rückgewiesener Fall ist wieder bearbeitbar und nicht fertig.",
        "Neuer Korrekturstand liegt vor Freigabe sichtbar vor.",
        "Nur bestätigte Freigabe führt zur anschliessend zulässigen Fertigstellung.",
    ]
    assert cases["BT-WF-009"]["status"] == "not_run"
    assert cases["BT-WF-010"]["status"] == "not_run"


def test_versions_required_flag_and_approvers_per_subject():
    ledger = AcceptanceLedger()
    first = _policy(ledger)
    route = _policy(ledger, SCOPE_ROUTING, "route-north")
    assert first.version == 1 and first.required is True
    assert first.approver_user_ids == frozenset([BETA])
    assert route.scope == SCOPE_ROUTING and route.version == 1

    second = ledger.publish(
        SCOPE_DICTATION,
        "case-1",
        required=False,
        approver_user_ids=[BETA, ALPHA],
        deny_user_ids=[GAMMA],
        now=T1,
        expected_version=1,
    )
    assert second.version == 2 and second.required is False
    assert ledger.versions(SCOPE_DICTATION, "case-1")[0] is first
    assert ledger.current(SCOPE_ROUTING, "route-north") is route
    member = ledger.decide_acceptance(
        SCOPE_ROUTING,
        "route-north",
        kind="accept",
        actor_id=4,
        actor_pool_ids=["pool-authors"],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )
    assert member.decision.actor_id == 4
    assert member.policy_version == 1


def test_only_current_approver_labels_do_not_grant():
    ledger = AcceptanceLedger()
    _policy(ledger)
    accepted = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=BETA,
        actor_pool_ids=[],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T0,
        labels=[LABEL_AUTHOR],
        expected_version=1,
    )
    assert accepted.decision.actor_id == BETA
    assert ledger.may_finish(
        SCOPE_DICTATION, "case-1", head_is_human_reviewed=True, head_revision_id=HEAD
    )

    denied = (
        (ALPHA, [], [LABEL_AUTHOR]),
        (ADMIN, [], [LABEL_ADMIN]),
        (GAMMA, ["pool-authors"], []),
    )
    for actor, pools, labels in denied:
        with pytest.raises(AcceptanceError) as caught:
            ledger.decide_acceptance(
                SCOPE_DICTATION,
                "case-1",
                kind="reject",
                actor_id=actor,
                actor_pool_ids=pools,
                revision_id=HEAD,
                reason="Bitte korrigieren",
                previous_assignee_id=ALPHA,
                now=T1,
                labels=labels,
                expected_version=1,
            )
        assert caught.value.code == "forbidden"
    assert len(ledger.decisions(SCOPE_DICTATION, "case-1")) == 1

    # No extra acceptance points: the author finishes without being an approver.
    plain = AcceptanceLedger()
    plain.publish(
        SCOPE_DICTATION, "case-2", required=False, approver_user_ids=[], now=T0
    )
    assert plain.may_finish(
        SCOPE_DICTATION, "case-2", head_is_human_reviewed=True, head_revision_id=HEAD
    )
    with pytest.raises(AcceptanceError) as caught:
        plain.decide_acceptance(
            SCOPE_DICTATION,
            "case-2",
            kind="accept",
            actor_id=ALPHA,
            actor_pool_ids=[],
            revision_id=HEAD,
            reason=None,
            previous_assignee_id=None,
            now=T0,
            labels=[LABEL_AUTHOR, LABEL_ADMIN],
        )
    assert caught.value.code == "forbidden"


def test_workflow_states_stay_distinct_and_reject_returns_to_correction():
    assert len({TO_CORRECT, TO_ACCEPT, ACCEPTED, DONE}) == 4
    assert LABELS[TO_CORRECT] != LABELS[TO_ACCEPT] != LABELS[ACCEPTED]
    assert TRANSITIONS[(TO_ACCEPT, "reject")] == TO_CORRECT
    assert TRANSITIONS[(TO_ACCEPT, "accept")] == ACCEPTED
    assert TRANSITIONS[(ACCEPTED, "complete")] == DONE
    assert (TO_ACCEPT, "complete") not in TRANSITIONS
    assert (TO_CORRECT, "complete") not in TRANSITIONS or TRANSITIONS[(TO_CORRECT, "complete")] != DONE


def test_stale_version_and_failures_write_nothing():
    ledger = AcceptanceLedger()
    first = _policy(ledger)
    with pytest.raises(PolicyError) as caught:
        ledger.publish(
            SCOPE_DICTATION,
            "case-1",
            required=True,
            approver_user_ids=[BETA, ADMIN],
            now=T1,
            expected_version=0,
        )
    assert caught.value.code == "stale_version"
    assert ledger.current(SCOPE_DICTATION, "case-1") is first

    with pytest.raises(PolicyError) as caught:
        ledger.decide_acceptance(
            SCOPE_DICTATION,
            "case-1",
            kind="accept",
            actor_id=BETA,
            actor_pool_ids=[],
            revision_id=HEAD,
            reason=None,
            previous_assignee_id=None,
            now=T0,
            expected_version=4,
        )
    assert caught.value.code == "stale_version"
    assert ledger.decisions(SCOPE_DICTATION, "case-1") == ()

    with pytest.raises(AcceptanceError) as caught:
        ledger.decide_acceptance(
            SCOPE_DICTATION,
            "case-1",
            kind="reject",
            actor_id=BETA,
            actor_pool_ids=[],
            revision_id=HEAD,
            reason="x",
            previous_assignee_id=ALPHA,
            now=T0,
            expected_version=1,
        )
    assert caught.value.code == "reason_required"
    assert ledger.decisions(SCOPE_DICTATION, "case-1") == ()

    with pytest.raises(PolicyError) as caught:
        ledger.publish(
            SCOPE_DICTATION,
            "case-1",
            required=True,
            approver_user_ids=[BETA],
            now=datetime(2026, 11, 18, 9, 0),
            expected_version=1,
        )
    assert caught.value.code == "naive_datetime"
    assert ledger.versions(SCOPE_DICTATION, "case-1") == (first,)


def test_retry_publish_and_decide_return_the_stored_row():
    ledger = AcceptanceLedger()
    first = _policy(ledger)
    again = ledger.publish(
        SCOPE_DICTATION,
        "case-1",
        required=True,
        approver_user_ids=[BETA],
        approver_pool_ids=["pool-authors"],
        deny_user_ids=[GAMMA],
        now=T1,
        expected_version=1,
    )
    assert again is first

    decision = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=BETA,
        actor_pool_ids=["pool-authors"],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )
    retried = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=BETA,
        actor_pool_ids=["pool-authors"],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T1,
        expected_version=1,
    )
    assert retried is decision
    assert len(ledger.decisions(SCOPE_DICTATION, "case-1")) == 1


def test_participant_change_revalidates_without_mutating_or_restoring():
    ledger = AcceptanceLedger()
    _policy(ledger)
    recorded = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=BETA,
        actor_pool_ids=[],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T0,
        expected_version=1,
    )
    ledger.mark_release_invalid(SCOPE_DICTATION, "case-1", HEAD)
    before = ledger.decisions(SCOPE_DICTATION, "case-1")

    with pytest.raises(PolicyError) as caught:
        ledger.change_participants(
            SCOPE_DICTATION,
            "case-1",
            approver_user_ids=[ADMIN],
            deny_user_ids=[GAMMA],
            now=T1,
            expected_version=0,
            head_revision_id=HEAD,
            release_revision_id=HEAD,
            release_valid=True,
        )
    assert caught.value.code == "stale_version"
    assert ledger.decisions(SCOPE_DICTATION, "case-1")[0] is recorded

    changed = ledger.change_participants(
        SCOPE_DICTATION,
        "case-1",
        approver_user_ids=[ADMIN],
        deny_user_ids=[GAMMA],
        now=T1,
        expected_version=1,
        head_revision_id=HEAD,
        release_revision_id=HEAD,
        release_valid=True,
    )
    assert changed.version.version == 2
    assert changed.pending_valid is False
    assert changed.release_valid is False
    assert changed.history[0] is recorded
    assert changed.history[0].decision is recorded.decision
    assert recorded.policy_version == 1
    assert before[0] is changed.history[0]
    assert ledger.may_finish(
        SCOPE_DICTATION, "case-1", head_is_human_reviewed=True, head_revision_id=HEAD
    ) is False

    restored = ledger.change_participants(
        SCOPE_DICTATION,
        "case-1",
        approver_user_ids=[BETA],
        deny_user_ids=[GAMMA],
        now=T1,
        expected_version=2,
        head_revision_id=HEAD,
        release_revision_id=HEAD,
        release_valid=True,
    )
    assert restored.pending_valid is True
    assert restored.release_valid is False
    assert restored.history[0].decision is recorded.decision
    assert ledger.may_finish(
        SCOPE_DICTATION, "case-1", head_is_human_reviewed=True, head_revision_id=HEAD
    ) is False

    new_head = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=BETA,
        actor_pool_ids=[],
        revision_id=HEAD2,
        reason=None,
        previous_assignee_id=None,
        now=T1,
        expected_version=3,
    )
    assert new_head.decision.revision_id == HEAD2
    assert ledger.decisions(SCOPE_DICTATION, "case-1")[0] is recorded
    assert ledger.may_finish(
        SCOPE_DICTATION, "case-1", head_is_human_reviewed=True, head_revision_id=HEAD2
    )
    assert ledger.may_finish(
        SCOPE_DICTATION, "case-1", head_is_human_reviewed=False, head_revision_id=HEAD2
    ) is False


def test_removed_approver_cannot_retry_and_new_approver_can_accept():
    ledger = AcceptanceLedger()
    _policy(ledger)
    recorded = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=BETA,
        actor_pool_ids=[],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T0,
    )
    outcome = ledger.change_participants(
        SCOPE_DICTATION,
        "case-1",
        approver_user_ids=[ADMIN],
        now=T1,
        expected_version=1,
        head_revision_id=HEAD,
        release_revision_id=HEAD2,
        release_valid=True,
    )
    assert outcome.pending_valid is False
    assert outcome.release_valid is True
    assert outcome.history[0] is recorded
    with pytest.raises(AcceptanceError) as caught:
        ledger.decide_acceptance(
            SCOPE_DICTATION,
            "case-1",
            kind="accept",
            actor_id=BETA,
            actor_pool_ids=[],
            revision_id=HEAD,
            reason=None,
            previous_assignee_id=None,
            now=T1,
            labels=[LABEL_ADMIN],
            expected_version=2,
        )
    assert caught.value.code == "forbidden"
    fresh = ledger.decide_acceptance(
        SCOPE_DICTATION,
        "case-1",
        kind="accept",
        actor_id=ADMIN,
        actor_pool_ids=[],
        revision_id=HEAD,
        reason=None,
        previous_assignee_id=None,
        now=T1,
        expected_version=2,
    )
    assert fresh.decision.actor_id == ADMIN
    assert ledger.decisions(SCOPE_DICTATION, "case-1")[0] is recorded
    assert ledger.may_finish(
        SCOPE_DICTATION, "case-1", head_is_human_reviewed=True, head_revision_id=HEAD
    )
