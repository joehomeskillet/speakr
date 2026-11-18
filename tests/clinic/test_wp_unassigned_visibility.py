"""Synthetic clarification scopes through the real import services."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError

from src.clinic import import_visibility as visibility, policy as central_policy, pools
from src.clinic.agent_channel import AgentChannelError
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.voice_consent import grant_consent
from src.database import db
from src.models.audit import AuditEvent

from test_import_centre import NOW, _data, _env as _env, _finalize, _intent, _upload, env as env


@pytest.fixture
def scene(env, clinic_on, monkeypatch):
    admin, lead, author = (env[key].id for key in ("admin", "other", "author"))
    monkeypatch.setattr(central_policy, "_grant_providers", [
        lambda uid: (central_policy.Grant("all", frozenset({"manage_pools", "import"})),) if uid in {admin, lead, author} else (),
    ])
    pool = pools.create_staff_pool(f"Synthetic typists {env['admin'].username}", actor_id=admin)
    pools.add_staff_member(pool.id, author, actor_id=admin)
    db.session.add(ClinicAccountPolicy(user_id=author, status="active", login_policy="device_only",
                                      is_identity_admin=True))
    db.session.commit()
    first, second = (_upload(env, _data(root), root=root) for root in ("usb-1", "usb-2"))
    ids = [_finalize(env, *upload[:2])["import"]["delivery_id"] for upload in (first, second)]
    policy = visibility.ClarificationPolicy(
        may_access=lambda actor, action, scope: actor == admin or (actor == lead and scope.root_id == "usb-1"),
        may_target=lambda actor, scope, uid, intent: uid == author and (
            intent is None or (intent.source_ref == scope.root_id and intent.target_pool_id is None)
        ),
    )
    return policy, admin, lead, author, ids, first


def test_delivery_inventory_and_counts_use_the_same_scopes(env, scene):
    policy, admin, lead, author, ids, _ = scene
    empty = {"items": [], "count": 0}
    for actor, expected in ((admin, ids), (lead, ids[:1]), (author, [])):
        deliveries = visibility.list_unassigned(policy, actor_user_id=actor)
        inventory = visibility.list_inventory(policy, env["policy"], actor_user_id=actor, agent_id=env["agent_id"])
        assert {row["id"] for row in deliveries["items"]} == set(expected)
        assert deliveries["count"] == inventory["count"] == len(expected)
        assert {row["snapshot_version"] for row in deliveries["items"]} == {
            row["snapshot_version"] for row in inventory["items"]
        }
        assert all("storage_key" not in row for row in deliveries["items"])
    assert visibility.list_unassigned(visibility.ClarificationPolicy(), actor_user_id=admin) == empty
    assert visibility.list_inventory(policy, env["policy"], actor_user_id=author, agent_id=env["agent_id"]) == empty


def test_suggestions_use_only_authorized_consented_candidates(scene):
    policy, admin, lead, author, ids, _ = scene
    grant_consent(actor_user_id=author, user_id=author, statement_text="Synthetic consent")
    grant_consent(actor_user_id=admin, user_id=admin, statement_text="Synthetic consent")
    visited = []

    def evidence(delivery_id):
        visited.append(delivery_id)
        return [1, 0], [(admin, [1, 0]), (lead, [1, 0]), (author, [0.8, 0.6])], 5.0

    for actor, delivery in ((author, ids[0]), (lead, ids[1])):
        with pytest.raises(AgentChannelError, match="not_found"):
            visibility.author_suggestions(policy, actor_user_id=actor, delivery_id=delivery, load_evidence=evidence)
    assert visited == []
    target_check = policy.may_target
    policy = replace(policy, may_target=lambda actor, scope, uid, intent: uid == lead
                     or target_check(actor, scope, uid, intent))
    result = visibility.author_suggestions(policy, actor_user_id=lead, delivery_id=ids[0], load_evidence=evidence)
    assert visited == [ids[0]]
    assert result["suggestions"] == [{"user_id": author, "score": 0.8}]
    assert result["top_score"] == 0.8 and result["gap"] is None
    assert result["requires_confirmation"] is True


def test_assignment_checks_both_scopes_and_audits_only_a_committed_claim(env, scene, monkeypatch):
    policy, _, lead, author, ids, upload = scene
    version = visibility.list_unassigned(policy, actor_user_id=lead)["items"][0]["snapshot_version"]
    foreign = _intent(env, actor_user_id=author)
    wrong_author = _intent(env, actor_user_id=lead)
    wrong_scope = _intent(env, actor_user_id=lead, source_ref="usb-2", target_author_user_id=author,
                          may_assign_author=lambda *_: True)
    own = _intent(env, actor_user_id=lead, target_author_user_id=author, may_assign_author=lambda *_: True)
    kwargs = dict(actor_user_id=lead, delivery_id=ids[0], intent_id=own["id"],
                  expected_snapshot_version=version, now=NOW)

    for changes, error in (
        ({"delivery_id": ids[1]}, "not_found"), ({"actor_user_id": author}, "not_found"),
        ({"intent_id": foreign["id"]}, "not_found"), ({"intent_id": wrong_scope["id"]}, "forbidden"),
        ({"intent_id": wrong_author["id"]}, "forbidden"),
        ({"expected_snapshot_version": "stale"}, "conflict"), ({"now": NOW + timedelta(days=1)}, "conflict"),
    ):
        with pytest.raises(AgentChannelError, match=error):
            visibility.assign_unassigned(policy, **(kwargs | changes))

    def events():
        return [row for row in AuditEvent.query.filter_by(event="recording.edited", actor_user_id=lead)
                if row.details["fields"] == {"import_delivery_id": ids[0], "import_intent_id": own["id"]}]

    def unavailable():
        raise OperationalError("COMMIT", {}, RuntimeError("synthetic commit failure"))

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", unavailable)
        with pytest.raises(OperationalError, match="synthetic commit failure"):
            visibility.assign_unassigned(policy, **kwargs)
    assert events() == []
    assert visibility.list_unassigned(policy, actor_user_id=lead)["count"] == 1
    assert visibility.assign_unassigned(policy, **kwargs) == ids[0]
    with pytest.raises(AgentChannelError, match="conflict"):
        visibility.assign_unassigned(policy, **kwargs)
    assert len(events()) == 1 and events()[0].target_user_id == author
    assert visibility.list_unassigned(policy, actor_user_id=lead)["count"] == 0
    receipt = _finalize(env, *upload[:2])
    assert receipt["state"] == "STORED"
    assert db.session.get(ClinicDictation, receipt["dictation_id"]).author_user_id == author


def test_new_service_is_unavailable_outside_clinic_mode(clinic_off):
    with pytest.raises(AgentChannelError, match="not_found"):
        visibility.list_unassigned(visibility.ClarificationPolicy(), actor_user_id=1)
