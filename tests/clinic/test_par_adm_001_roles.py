"""PAR-ADM-001: local identity and pool roles share one persisted user ID.

These service/database tests use synthetic accounts. They do not run the
BT-ADM-001 reference or browser flows or verify Philips, Entra, KIS, or pedals.
"""

from uuid import uuid4

import pytest

from src.app import app, db
from src.clinic import identity, pools
from src.clinic.models import ClinicDictation
from src.models import Group, GroupMembership, Recording, User

PASSWORD = "Fiktiver Passiersatz für Rollentests 27"


def _local_user(prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:8]}"
    return identity.create_local_user(username, f"{username}@example.invalid")


@pytest.fixture
def role_pools():
    with app.app_context():
        identity.create_tables()
        pools.create_tables()
        actor_id = _local_user("actor").id
        author = pools.create_author_pool(f"Autoren-{uuid4().hex}", actor_id=actor_id)
        staff = pools.create_staff_pool(f"Schreibpool-{uuid4().hex}", actor_id=actor_id)
        staff_id, group_id = staff.id, staff.group_id
        try:
            yield actor_id, author, staff
        finally:
            # The shared clinic fixture does not clean up upstream groups.
            db.session.rollback()
            pools.ClinicPoolRoute.query.filter_by(staff_pool_id=staff_id).delete()
            db.session.delete(db.session.get(pools.ClinicStaffPool, staff_id))
            db.session.flush()
            db.session.delete(db.session.get(Group, group_id))
            db.session.commit()


@pytest.fixture
def dual_role(role_pools) -> User:
    actor_id, author, staff = role_pools
    user = _local_user("both")
    pools.add_author_member(author.id, user.id, actor_id=actor_id)
    pools.add_staff_member(staff.id, user.id, actor_id=actor_id)
    return user


def _membership_rows(author, staff):
    authors = pools.ClinicAuthorPoolMember.query.filter_by(pool_id=author.id).all()
    typists = GroupMembership.query.filter_by(group_id=staff.group_id).all()
    return (
        {(row.id, row.user_id) for row in authors},
        {(row.id, row.user_id) for row in typists},
    )


def _dictation(author_id: int, pool_id: str) -> ClinicDictation:
    recording = Recording(user_id=author_id, title="Testdiktat", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(
        recording_id=recording.id, author_user_id=author_id, pool_id=pool_id,
    )
    db.session.add(dictation)
    db.session.flush()
    return dictation


def test_par_adm_001_both_memberships_reference_the_original_user(role_pools):
    actor_id, author, staff = role_pools
    user = _local_user("original")
    user_id = user.id
    username = user.username
    author_member = pools.add_author_member(author.id, user_id, actor_id=actor_id)
    pools.add_staff_member(staff.id, user_id, actor_id=actor_id)
    resource = pools.add_resource(author.id, "template", "Befund", actor_id=actor_id)

    db.session.expire_all()
    staff_member = GroupMembership.query.filter_by(group_id=staff.group_id).one()
    assert User.query.filter_by(username=username).one().id == user_id
    assert author_member.user_id == staff_member.user_id == user_id
    assert [row["id"] for row in pools.resources_for(user_id)] == [resource.id]


def test_par_adm_001_author_typist_and_dual_role_have_distinct_views(role_pools, dual_role):
    actor_id, author, staff = role_pools
    author_only = _local_user("author")
    typist_only = _local_user("typist")
    pools.add_author_member(author.id, author_only.id, actor_id=actor_id)
    pools.add_staff_member(staff.id, typist_only.id, actor_id=actor_id)
    route = pools.link_staff_pool(author.id, staff.id, actor_id=actor_id)
    resource = pools.add_resource(author.id, "vocabulary", "Fachwörter", actor_id=actor_id)
    author_dictation = _dictation(author_only.id, author.id)
    dual_dictation = _dictation(dual_role.id, author.id)

    own = {"allowed": {"view": {"reason": "own"}, "play": {"reason": "own"}}, "denies": []}
    routed = {"allowed": {"view": {"reason": "staff_link", "link_id": route.id}}, "denies": []}
    assert [row["id"] for row in pools.resources_for(author_only.id)] == [resource.id]
    assert pools.grants_for(author_only.id, author_dictation) == own
    assert pools.grants_for(author_only.id, dual_dictation) == {"allowed": {}, "denies": []}
    assert pools.resources_for(typist_only.id) == []
    assert pools.grants_for(typist_only.id, author_dictation) == routed
    assert [row["id"] for row in pools.resources_for(dual_role.id)] == [resource.id]
    assert pools.grants_for(dual_role.id, author_dictation) == routed
    assert pools.grants_for(dual_role.id, dual_dictation) == own


def test_par_adm_001_disable_and_reactivate_preserve_memberships_and_history(role_pools, dual_role):
    actor_id, author, staff = role_pools
    user_id, username = dual_role.id, dual_role.username
    issued = identity.issue_recovery(user_id, issued_by=actor_id)
    identity.complete_recovery(issued.code, PASSWORD)
    assert identity.authenticate_local(username, PASSWORD).user_id == user_id
    users_before = User.query.count()
    memberships = _membership_rows(author, staff)
    history = pools.history()
    assert [row["detail"] for row in history if row["entity"] == "membership"] == [
        {"kind": "author", "pool_id": author.id, "user_id": user_id},
        {"kind": "staff", "pool_id": staff.id, "user_id": user_id},
    ]

    assert identity.set_status(user_id, "disabled", actor_id=actor_id).status == "disabled"
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(username, PASSWORD)
    assert _membership_rows(author, staff) == memberships
    assert pools.history() == history

    assert identity.set_status(user_id, "active", actor_id=actor_id).status == "active"
    assert identity.authenticate_local(username, PASSWORD).user_id == user_id
    assert User.query.filter_by(username=username).one().id == user_id
    assert User.query.count() == users_before
    assert _membership_rows(author, staff) == memberships
    assert pools.history() == history


def test_par_adm_001_pending_user_can_join_a_pool_without_local_login(role_pools):
    actor_id, author, _ = role_pools
    user = _local_user("pending")
    policy = db.session.get(identity.ClinicAccountPolicy, user.id)
    assert (policy.status, user.password) == ("pending_activation", None)
    member = pools.add_author_member(author.id, user.id, actor_id=actor_id)
    assert member.user_id == user.id
    with pytest.raises(identity.AuthenticationFailed):
        identity.authenticate_local(user.username, PASSWORD)
    assert pools.ClinicAuthorPoolMember.query.filter_by(pool_id=author.id).one().user_id == user.id


@pytest.mark.parametrize("duplicate", ["email", "username"])
def test_par_adm_001_duplicate_local_user_leaves_no_membership(role_pools, dual_role, duplicate):
    _, author, staff = role_pools
    users_before = User.query.count()
    memberships = _membership_rows(author, staff)
    history = pools.history()
    username = dual_role.username if duplicate == "username" else f"new_{uuid4().hex[:8]}"
    email = dual_role.email if duplicate == "email" else f"{uuid4().hex}@example.invalid"

    with pytest.raises(identity.IdentityConflict, match="^duplicate$"):
        identity.create_local_user(username, email)

    assert User.query.count() == users_before
    assert _membership_rows(author, staff) == memberships
    assert pools.history() == history


def test_par_adm_001_admin_does_not_gain_pool_memberships(dual_role):
    username = f"admin_{uuid4().hex[:8]}"
    assert identity.bootstrap_first_admin(username, f"{username}@example.invalid", PASSWORD)
    admin = User.query.filter_by(username=username).one()
    policy = db.session.get(identity.ClinicAccountPolicy, admin.id)

    assert admin.is_admin and policy.is_identity_admin
    assert pools.ClinicAuthorPoolMember.query.filter_by(user_id=admin.id).all() == []
    assert GroupMembership.query.filter_by(user_id=admin.id).all() == []
