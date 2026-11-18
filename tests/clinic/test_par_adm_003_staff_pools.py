"""PAR-ADM-003 service and database checks for independent staff pools.

These tests use synthetic users and dictations. They do not validate the UI,
reference-system parity, Philips devices, Entra, KIS, or foot pedals.
"""

import pytest

from src.clinic import pools
from src.models import GroupMembership

from test_pools import _dictation, _suffix, _user, pool_db as pool_db

NO_GRANTS: dict[str, object] = {"allowed": {}, "denies": []}


def _view_grant(route: pools.ClinicPoolRoute) -> dict[str, object]:
    return {
        "allowed": {"view": {"reason": "staff_link", "link_id": route.id}},
        "denies": [],
    }


def test_par_adm_003_staff_pool_can_have_members_before_any_author_pool() -> None:
    actor, author, first, second = (_user() for _ in range(4))
    staff = pools.create_staff_pool(f"Schreibpool-{_suffix()}", actor_id=actor.id)
    for member in (first, second):
        pools.add_staff_member(staff.id, member.id, actor_id=actor.id)

    assert pools.ClinicAuthorPool.query.count() == 0
    memberships = GroupMembership.query.filter_by(group_id=staff.group_id).all()
    assert {row.user_id for row in memberships} == {first.id, second.id}

    # A populated author pool gives the unlinked staff pool nothing to see.
    authors = pools.create_author_pool(f"Autorenpool-{_suffix()}", actor_id=actor.id)
    pools.add_author_member(authors.id, author.id, actor_id=actor.id)
    template = pools.add_resource(authors.id, "template", "Vorlage", actor_id=actor.id)
    foreign = _dictation(author, authors.id)
    assert [row["id"] for row in pools.resources_for(author.id)] == [template.id]
    for member in (first, second):
        assert pools.resources_for(member.id) == []
        assert pools.grants_for(member.id, foreign) == NO_GRANTS


def test_par_adm_003_members_see_only_their_linked_pairs() -> None:
    actor, author, north_only, south_only, both = (_user() for _ in range(5))
    north_authors = pools.create_author_pool(f"Autoren Nord-{_suffix()}", actor_id=actor.id)
    south_authors = pools.create_author_pool(f"Autoren Süd-{_suffix()}", actor_id=actor.id)
    north_staff = pools.create_staff_pool(f"Schreibpool Nord-{_suffix()}", actor_id=actor.id)
    south_staff = pools.create_staff_pool(f"Schreibpool Süd-{_suffix()}", actor_id=actor.id)
    for staff, member in ((north_staff, north_only), (south_staff, south_only)):
        pools.add_staff_member(staff.id, member.id, actor_id=actor.id)
        pools.add_staff_member(staff.id, both.id, actor_id=actor.id)

    north = _dictation(author, north_authors.id)
    south = _dictation(author, south_authors.id)
    north_route = pools.link_staff_pool(north_authors.id, north_staff.id, actor_id=actor.id)
    assert pools.grants_for(both.id, north) == _view_grant(north_route)
    assert pools.grants_for(both.id, south) == NO_GRANTS

    south_route = pools.link_staff_pool(south_authors.id, south_staff.id, actor_id=actor.id)
    for member, north_grant, south_grant in (
        (north_only, _view_grant(north_route), NO_GRANTS),
        (south_only, NO_GRANTS, _view_grant(south_route)),
        (both, _view_grant(north_route), _view_grant(south_route)),
    ):
        assert pools.grants_for(member.id, north) == north_grant
        assert pools.grants_for(member.id, south) == south_grant


def test_par_adm_003_member_removal_revokes_only_that_members_view() -> None:
    actor, author, removed, retained = (_user() for _ in range(4))
    authors = pools.create_author_pool(f"Autorenpool-{_suffix()}", actor_id=actor.id)
    author_membership = pools.add_author_member(authors.id, author.id, actor_id=actor.id)
    staff = pools.create_staff_pool(f"Schreibpool-{_suffix()}", actor_id=actor.id)
    route = pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)
    for member in (removed, retained):
        pools.add_staff_member(staff.id, member.id, actor_id=actor.id)

    foreign = _dictation(author, authors.id)
    assert pools.grants_for(removed.id, foreign) == _view_grant(route)
    assert pools.grants_for(retained.id, foreign) == _view_grant(route)

    pools.remove_staff_member(staff.id, removed.id, actor_id=actor.id)

    assert pools.grants_for(removed.id, foreign) == NO_GRANTS
    assert pools.grants_for(retained.id, foreign) == _view_grant(route)
    assert pools.ClinicAuthorPoolMember.query.filter_by(pool_id=authors.id).one().id == (
        author_membership.id
    )


def test_par_adm_003_link_does_not_create_author_membership() -> None:
    actor, author, staff_member = (_user() for _ in range(3))
    authors = pools.create_author_pool(f"Autorenpool-{_suffix()}", actor_id=actor.id)
    staff = pools.create_staff_pool(f"Schreibpool-{_suffix()}", actor_id=actor.id)
    pools.add_staff_member(staff.id, staff_member.id, actor_id=actor.id)
    route = pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)

    assert pools.grants_for(staff_member.id, _dictation(author, authors.id)) == _view_grant(route)
    assert pools.ClinicAuthorPoolMember.query.filter_by(user_id=staff_member.id).all() == []


def test_par_adm_003_duplicate_link_is_rejected_and_original_remains() -> None:
    actor = _user()
    authors = pools.create_author_pool(f"Autorenpool-{_suffix()}", actor_id=actor.id)
    staff = pools.create_staff_pool(f"Schreibpool-{_suffix()}", actor_id=actor.id)
    route = pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)

    with pytest.raises(pools.PoolError) as raised:
        pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)

    assert raised.value.code == "already_linked"
    assert pools.ClinicPoolRoute.query.filter_by(
        author_pool_id=authors.id, staff_pool_id=staff.id
    ).one().id == route.id


def test_par_adm_003_duplicate_staff_name_is_rejected_without_changing_original() -> None:
    actor, author, member, outsider = (_user() for _ in range(4))
    name = f"Schreibpool-{_suffix()}"
    staff = pools.create_staff_pool(name, actor_id=actor.id)
    pools.add_staff_member(staff.id, member.id, actor_id=actor.id)
    authors = pools.create_author_pool(f"Autorenpool-{_suffix()}", actor_id=actor.id)
    route = pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)
    foreign = _dictation(author, authors.id)

    with pytest.raises(pools.PoolError) as raised:
        pools.create_staff_pool(name, actor_id=outsider.id)

    assert raised.value.code == "name_taken"
    assert pools.ClinicStaffPool.query.one().id == staff.id
    assert pools.grants_for(member.id, foreign) == _view_grant(route)
    assert pools.grants_for(outsider.id, foreign) == NO_GRANTS
