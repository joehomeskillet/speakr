"""PAR-ADM-002 pool service and database behavior with synthetic dictations.

The tests cover shared resources, pool-scoped visibility, and membership
history. They do not establish UI or reference-product parity or validate
Philips devices, Entra, KIS or foot pedals.
"""

import uuid
from collections.abc import Iterator

import pytest

from src.app import app, db
from src.clinic import pools
from src.clinic.models import ClinicDictation
from src.models import Recording, User


@pytest.fixture(autouse=True)
def pool_db() -> Iterator[None]:
    with app.app_context():
        pools.create_tables()
        yield


def _user() -> User:
    name = f"p{uuid.uuid4().hex[:8]}"
    user = User(username=name, email=f"{name}@example.invalid", password="x")
    db.session.add(user)
    db.session.commit()
    return user


def _dictation(author: User, pool_id: str) -> ClinicDictation:
    recording = Recording(user_id=author.id, title="Diktat", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(
        recording_id=recording.id, author_user_id=author.id, pool_id=pool_id,
    )
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture
def two_pools() -> tuple[int, int, str, str]:
    actor, member = _user(), _user()
    pool_a = pools.create_author_pool("Nord", actor_id=actor.id)
    pool_b = pools.create_author_pool("Süd", actor_id=actor.id)
    for pool in (pool_a, pool_b):
        pools.add_author_member(pool.id, member.id, actor_id=actor.id)
    return actor.id, member.id, pool_a.id, pool_b.id


def test_par_adm_002_resources_include_both_author_pools(two_pools) -> None:
    actor, member, pool_a, pool_b = two_pools
    word = pools.add_resource(pool_a, "vocabulary", "Befund", actor_id=actor)
    sheet = pools.add_resource(pool_b, "template", "Bericht", actor_id=actor)

    expected = [
        {"id": word.id, "author_pool_id": pool_a, "kind": "vocabulary", "name": "Befund"},
        {"id": sheet.id, "author_pool_id": pool_b, "kind": "template", "name": "Bericht"},
    ]
    assert sorted(pools.resources_for(member), key=lambda row: row["kind"]) == sorted(
        expected, key=lambda row: row["kind"],
    )


def test_par_adm_002_removal_withdraws_resources_but_not_explicit_grants(two_pools) -> None:
    actor, member, pool_a, pool_b = two_pools
    word = pools.add_resource(pool_a, "vocabulary", "Befund", actor_id=actor)
    sheet = pools.add_resource(pool_b, "template", "Bericht", actor_id=actor)
    assert {row["id"] for row in pools.resources_for(member)} == {word.id, sheet.id}
    author = _user()
    dictations_and_grants = [
        (
            _dictation(author, pool_id),
            pools.allow(member, "pool", actor_id=actor, pool_id=pool_id, actions=["view"]),
        )
        for pool_id in (pool_a, pool_b)
    ]

    pools.remove_author_member(pool_a, member, actor_id=actor)

    assert pools.resources_for(member) == [
        {"id": sheet.id, "author_pool_id": pool_b, "kind": "template", "name": "Bericht"},
    ]
    for dictation, grant in dictations_and_grants:
        assert pools.grants_for(member, dictation) == {
            "allowed": {"view": {"reason": "pool", "grant_id": grant.id}},
            "denies": [],
        }


def test_par_adm_002_duplicate_author_pool_name_is_rejected(two_pools) -> None:
    actor, _, pool_a, _ = two_pools
    with pytest.raises(pools.PoolError) as raised:
        pools.create_author_pool("Nord", actor_id=actor)
    assert raised.value.code == "name_taken"
    assert [row.id for row in pools.ClinicAuthorPool.query.filter_by(name="Nord")] == [pool_a]


def test_par_adm_002_duplicate_membership_raises_already_member(two_pools) -> None:
    actor, member, pool_a, _ = two_pools
    memberships = pools.ClinicAuthorPoolMember.query.filter_by(pool_id=pool_a, user_id=member)
    original_id = memberships.one().id

    with pytest.raises(pools.PoolError) as raised:
        pools.add_author_member(pool_a, member, actor_id=actor)

    assert raised.value.code == "already_member"
    assert [row.id for row in memberships] == [original_id]


def test_par_adm_002_membership_changes_append_history(two_pools) -> None:
    actor, member, pool_a, pool_b = two_pools
    other = _user().id
    first_id = pools.ClinicAuthorPoolMember.query.filter_by(pool_id=pool_a, user_id=member).one().id
    second_id = pools.add_author_member(pool_a, other, actor_id=other).id
    before = pools.history()

    pools.remove_author_member(pool_a, member, actor_id=actor)

    after = pools.history()
    assert after[:-1] == before
    rows = [row for row in after if row["detail"].get("pool_id") == pool_a]
    assert len(rows) == 3
    assert [
        (row["entity"], row["entity_id"], row["change"], row["actor_user_id"], row["detail"])
        for row in rows
    ] == [
        ("membership", first_id, "added", actor,
         {"kind": "author", "pool_id": pool_a, "user_id": member}),
        ("membership", second_id, "added", other,
         {"kind": "author", "pool_id": pool_a, "user_id": other}),
        ("membership", first_id, "removed", actor,
         {"kind": "author", "pool_id": pool_a, "user_id": member}),
    ]
    assert [row["change"] for row in after if row["detail"].get("pool_id") == pool_b] == ["added"]


def test_par_adm_002_pool_scope_and_deny_isolate_explicit_grants(two_pools) -> None:
    actor, member, pool_a, pool_b = two_pools
    author = _user()
    in_a, in_b = _dictation(author, pool_a), _dictation(author, pool_b)
    grant_a = pools.allow(member, "pool", actor_id=actor, pool_id=pool_a, actions=["edit"])
    assert pools.grants_for(member, in_a) == {
        "allowed": {"edit": {"reason": "pool", "grant_id": grant_a.id}}, "denies": [],
    }
    assert pools.grants_for(member, in_b) == {"allowed": {}, "denies": []}

    grant_b = pools.allow(member, "pool", actor_id=actor, pool_id=pool_b, actions=["edit"])
    blocked = pools.deny(member, "pool", actor_id=actor, pool_id=pool_a, actions=["edit"])

    assert pools.grants_for(member, in_a) == {
        "allowed": {},
        "denies": [{"grant_id": blocked.id, "scope": "pool", "pool_id": pool_a, "actions": ["edit"]}],
    }
    assert pools.grants_for(member, in_b) == {
        "allowed": {"edit": {"reason": "pool", "grant_id": grant_b.id}}, "denies": [],
    }
