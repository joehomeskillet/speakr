"""Author pools, staff pools and visibility grants.

Service-level coverage for the pool slice of X4-001, X4-002, T-011, T-066
and BT-ADM-002..006. Acceptance entries stay not_run: no route, no UI, no
case/tenant filter and no PAR-ADM-005 action vocabulary.
"""

import json
import uuid

import pytest

from src.app import app, db
from src.clinic import pools
from src.clinic.models import ClinicDictation
from src.models import Group, GroupMembership, Recording, User


@pytest.fixture(autouse=True)
def pool_db():
    with app.app_context():
        pools.create_tables()
        before = {row[0] for row in db.session.query(Group.id)}
        yield
        db.session.rollback()
        stale = [
            row[0]
            for row in db.session.query(Group.id)
            if row[0] not in before
        ]
        if stale:
            staff_ids = [
                row[0]
                for row in db.session.query(pools.ClinicStaffPool.id).filter(
                    pools.ClinicStaffPool.group_id.in_(stale)
                )
            ]
            if staff_ids:
                db.session.query(pools.ClinicPoolRoute).filter(
                    pools.ClinicPoolRoute.staff_pool_id.in_(staff_ids)
                ).delete(synchronize_session=False)
                db.session.query(pools.ClinicStaffPool).filter(
                    pools.ClinicStaffPool.id.in_(staff_ids)
                ).delete(synchronize_session=False)
            db.session.query(GroupMembership).filter(
                GroupMembership.group_id.in_(stale)
            ).delete(synchronize_session=False)
            db.session.query(Group).filter(Group.id.in_(stale)).delete(
                synchronize_session=False
            )
            db.session.commit()


def _suffix():
    return uuid.uuid4().hex[:8]


def _user(admin=False):
    suffix = _suffix()
    user = User(
        username="p" + suffix,
        email=suffix + "@example.invalid",
        password="x",
        is_admin=admin,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _dictation(author, pool_id):
    recording = Recording(user_id=author.id, title="d", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(
        recording_id=recording.id,
        author_user_id=author.id,
        pool_id=pool_id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _actions(result):
    return set(result["allowed"])


def test_same_name_pools_do_not_transfer_rights():
    """X4-001: the shared name is not an id. Kind is the table, not a column."""
    actor = _user()
    author = _user()
    typist = _user()
    name = "Nord-" + _suffix()
    author_pool = pools.create_author_pool(name, actor_id=actor.id)
    staff_pool = pools.create_staff_pool(name, actor_id=actor.id)
    assert author_pool.id != staff_pool.id
    assert author_pool.name == staff_pool.name
    assert "kind" not in pools.ClinicAuthorPool.__table__.columns
    assert "kind" not in pools.ClinicStaffPool.__table__.columns

    pools.add_author_member(author_pool.id, author.id, actor_id=actor.id)
    pools.add_staff_member(staff_pool.id, typist.id, actor_id=actor.id)
    pools.add_resource(author_pool.id, "vocabulary", "phrase-" + _suffix(), actor_id=actor.id)

    assert len(pools.resources_for(author.id)) == 1
    assert pools.resources_for(author.id)[0]["author_pool_id"] == author_pool.id
    assert pools.resources_for(typist.id) == []

    foreign = _dictation(_user(), author_pool.id)
    assert _actions(pools.grants_for(typist.id, foreign)) == set()
    assert _actions(pools.grants_for(author.id, foreign)) == set()

    with pytest.raises(pools.PoolError) as raised:
        pools.allow(typist.id, "pool", actor_id=actor.id, pool_id=staff_pool.id, actions=["view"])
    assert raised.value.code == "not_author_pool"

    grant = pools.allow(
        typist.id, "pool", actor_id=actor.id, pool_id=author_pool.id, actions=["view"]
    )
    other = pools.create_author_pool("Sued-" + _suffix(), actor_id=actor.id)
    elsewhere = _dictation(_user(), other.id)
    seen = pools.grants_for(typist.id, foreign)
    assert _actions(seen) == {"view"}
    assert seen["allowed"]["view"] == {"reason": "pool", "grant_id": grant.id}
    assert _actions(pools.grants_for(typist.id, elsewhere)) == set()


def test_default_is_own_only():
    actor = _user()
    alpha = _user()
    beta = _user()
    pool = pools.create_author_pool("Own-" + _suffix(), actor_id=actor.id)
    pools.add_author_member(pool.id, alpha.id, actor_id=actor.id)
    pools.add_author_member(pool.id, beta.id, actor_id=actor.id)
    alphas = _dictation(alpha, pool.id)
    betas = _dictation(beta, pool.id)

    own = pools.grants_for(alpha.id, alphas)
    assert _actions(own) == set(pools.OWN_ACTIONS)
    assert own["allowed"]["view"] == {"reason": "own"}
    assert own["allowed"]["play"] == {"reason": "own"}
    assert own["denies"] == []
    assert _actions(pools.grants_for(alpha.id, betas)) == set()
    assert pools.policy_provider is pools.grants_for
    json.dumps(own)


def test_pool_grant_is_limited_to_that_pool_and_those_actions():
    actor = _user()
    typist = _user()
    pool_a = pools.create_author_pool("A-" + _suffix(), actor_id=actor.id)
    pool_b = pools.create_author_pool("B-" + _suffix(), actor_id=actor.id)
    in_a = _dictation(_user(), pool_a.id)
    in_b = _dictation(_user(), pool_b.id)
    grant = pools.allow(
        typist.id, "pool", actor_id=actor.id, pool_id=pool_a.id, actions=["edit", "view"]
    )
    seen = pools.grants_for(typist.id, in_a)
    assert _actions(seen) == {"view", "edit"}
    assert seen["allowed"]["edit"]["grant_id"] == grant.id
    assert seen["allowed"]["edit"]["reason"] == "pool"
    assert _actions(pools.grants_for(typist.id, in_b)) == set()


def test_all_scope_grant():
    actor = _user()
    typist = _user()
    pool_a = pools.create_author_pool("AllA-" + _suffix(), actor_id=actor.id)
    pool_b = pools.create_author_pool("AllB-" + _suffix(), actor_id=actor.id)
    grant = pools.allow(typist.id, "all", actor_id=actor.id, actions=["export"])
    for pool in (pool_a, pool_b):
        seen = pools.grants_for(typist.id, _dictation(_user(), pool.id))
        assert _actions(seen) == {"export"}
        assert seen["allowed"]["export"] == {"reason": "all", "grant_id": grant.id}


def test_deny_beats_every_grant():
    actor = _user()
    author = _user()
    pool_a = pools.create_author_pool("DenyA-" + _suffix(), actor_id=actor.id)
    pool_b = pools.create_author_pool("DenyB-" + _suffix(), actor_id=actor.id)
    staff = pools.create_staff_pool("DenyS-" + _suffix(), actor_id=actor.id)
    route = pools.link_staff_pool(pool_a.id, staff.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, author.id, actor_id=actor.id)
    in_a = _dictation(author, pool_a.id)
    in_b = _dictation(author, pool_b.id)
    pools.allow(author.id, "all", actor_id=actor.id)
    pools.allow(
        author.id, "pool", actor_id=actor.id, pool_id=pool_a.id, actions=["edit", "view"]
    )
    blocked = pools.deny(
        author.id, "pool", actor_id=actor.id, pool_id=pool_a.id, actions=["view"]
    )

    seen = pools.grants_for(author.id, in_a)
    assert "view" not in seen["allowed"]
    assert seen["allowed"]["play"] == {"reason": "own"}
    assert seen["allowed"]["edit"]["reason"] == "pool"
    assert seen["allowed"]["export"]["reason"] == "all"
    assert seen["allowed"]["release"]["reason"] == "all"
    assert seen["denies"] == [{
        "grant_id": blocked.id,
        "scope": "pool",
        "pool_id": pool_a.id,
        "actions": ["view"],
    }]
    # The same deny does not apply to another author pool.
    other = pools.grants_for(author.id, in_b)
    assert other["allowed"]["view"]["reason"] == "own"
    assert other["denies"] == []
    # A staff link does not survive a matching deny either.
    typist = _user()
    pools.add_staff_member(staff.id, typist.id, actor_id=actor.id)
    pools.deny(typist.id, "pool", actor_id=actor.id, pool_id=pool_a.id, actions=["view"])
    hidden = pools.grants_for(typist.id, in_a)
    assert hidden["allowed"] == {}
    assert hidden["denies"][0]["actions"] == ["view"]
    assert route.id  # the link exists; the deny is what removed the view


def test_staff_member_sees_linked_author_pools_only():
    actor = _user()
    typist = _user()
    pool_a = pools.create_author_pool("LinkA-" + _suffix(), actor_id=actor.id)
    pool_b = pools.create_author_pool("LinkB-" + _suffix(), actor_id=actor.id)
    staff = pools.create_staff_pool("LinkS-" + _suffix(), actor_id=actor.id)
    route = pools.link_staff_pool(pool_a.id, staff.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, typist.id, actor_id=actor.id)
    pools.add_resource(pool_a.id, "template", "sheet-" + _suffix(), actor_id=actor.id)
    in_a = _dictation(_user(), pool_a.id)
    in_b = _dictation(_user(), pool_b.id)

    seen = pools.grants_for(typist.id, in_a)
    assert _actions(seen) == set(pools.STAFF_LINK_ACTIONS)
    assert seen["allowed"]["view"] == {"reason": "staff_link", "link_id": route.id}
    assert "name" not in seen["allowed"]["view"]
    assert _actions(pools.grants_for(typist.id, in_b)) == set()
    assert pools.resources_for(typist.id) == []


def test_removing_membership_removes_derived_rights():
    actor = _user()
    author = _user()
    typist = _user()
    pool = pools.create_author_pool("Drop-" + _suffix(), actor_id=actor.id)
    staff = pools.create_staff_pool("DropS-" + _suffix(), actor_id=actor.id)
    pools.link_staff_pool(pool.id, staff.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, typist.id, actor_id=actor.id)
    pools.add_author_member(pool.id, author.id, actor_id=actor.id)
    pools.add_resource(pool.id, "vocabulary", "word-" + _suffix(), actor_id=actor.id)
    foreign = _dictation(_user(), pool.id)

    assert _actions(pools.grants_for(typist.id, foreign)) == {"view"}
    pools.remove_staff_member(staff.id, typist.id, actor_id=actor.id)
    assert _actions(pools.grants_for(typist.id, foreign)) == set()
    pools.add_staff_member(staff.id, typist.id, actor_id=actor.id)
    assert _actions(pools.grants_for(typist.id, foreign)) == {"view"}

    assert len(pools.resources_for(author.id)) == 1
    pools.remove_author_member(pool.id, author.id, actor_id=actor.id)
    assert pools.resources_for(author.id) == []


def test_admin_flag_alone_grants_nothing():
    actor = _user()
    admin = _user(admin=True)
    author = _user()
    assert admin.is_admin is True
    pool = pools.create_author_pool("Adm-" + _suffix(), actor_id=actor.id)
    foreign = _dictation(author, pool.id)
    own = _dictation(admin, pool.id)
    assert pools.grants_for(admin.id, foreign) == {"allowed": {}, "denies": []}
    assert _actions(pools.grants_for(admin.id, own)) == set(pools.OWN_ACTIONS)


def test_history_rows_are_written_and_append_only():
    actor = _user()
    user = _user()
    pool = pools.create_author_pool("Hist-" + _suffix(), actor_id=actor.id)
    pools.add_author_member(pool.id, user.id, actor_id=actor.id)
    pools.remove_author_member(pool.id, user.id, actor_id=actor.id)
    pools.allow(user.id, "all", actor_id=actor.id, actions=["play"])
    pools.deny(user.id, "own", actor_id=actor.id, actions=["export"])

    rows = pools.history()
    assert [row["change"] for row in rows] == [
        "created", "added", "removed", "added", "added"
    ]
    assert {row["entity"] for row in rows} >= {"pool", "membership", "grant"}
    first_ids = [row["id"] for row in rows]
    for row in rows:
        assert row["actor_user_id"] == actor.id
        assert row["happened_at"].tzinfo is not None
        assert "name" not in row["detail"]

    pools.create_staff_pool("HistS-" + _suffix(), actor_id=actor.id)
    later = pools.history()
    assert [row["id"] for row in later[: len(first_ids)]] == first_ids
    assert len(later) == len(first_ids) + 1

    target = db.session.get(pools.ClinicPoolHistory, first_ids[0])
    db.session.delete(target)
    with pytest.raises(pools.PoolError) as raised:
        db.session.flush()
    assert raised.value.code == "history_immutable"
