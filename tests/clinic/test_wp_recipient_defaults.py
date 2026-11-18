"""Recipient defaults and self-correction (PAR-ADM-006, PAR-ADM-007).

Service-level coverage. Original BT-ADM-006/007 oracles stay unchanged.
"""

import pytest

from src.clinic import pools, recipient_defaults as rd

from test_pools import _dictation, _suffix, _user, pool_db as pool_db


@pytest.fixture(autouse=True)
def _tables(pool_db):
    rd.create_tables()
    yield


def _world():
    actor = _user()
    author = _user()
    first = _user()
    second = _user()
    authors = pools.create_author_pool("Autoren-" + _suffix(), actor_id=actor.id)
    staff = pools.create_staff_pool("Schreib-" + _suffix(), actor_id=actor.id)
    pools.add_author_member(authors.id, author.id, actor_id=actor.id)
    pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, first.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, second.id, actor_id=actor.id)
    return actor, author, first, second, authors, staff


def test_positive_defaults_match_submit_assignment():
    _actor, author, first, second, authors, staff = _world()
    all_ids = tuple(sorted((first.id, second.id)))

    row = rd.set_default(
        author.id, "all_eligible", actor_id=author.id, expected_version=0
    )
    assert row.version == 1
    sent = rd.submit(author.id, authors.id, actor_id=author.id, expected_version=1)
    assert sent.mode == "all_eligible"
    assert tuple(sorted(sent.recipient_user_ids)) == all_ids

    rd.set_default(
        author.id, "person", actor_id=author.id, expected_version=1,
        target_ids=[str(first.id)],
    )
    sent = rd.submit(author.id, authors.id, actor_id=author.id)
    assert sent.recipient_user_ids == (first.id,)

    rd.set_default(
        author.id, "staff_pool", actor_id=author.id, expected_version=2,
        target_ids=[staff.id],
    )
    sent = rd.submit(author.id, authors.id, actor_id=author.id)
    assert sent.mode == "staff_pool"
    assert sent.target_ids == (staff.id,)
    assert tuple(sorted(sent.recipient_user_ids)) == all_ids


def test_unauthorized_foreign_user_and_admin_flag():
    _actor, author, first, _second, authors, _staff = _world()
    admin = _user(admin=True)
    outsider = _user()
    assert admin.is_admin is True

    with pytest.raises(rd.RecipientDefaultError) as raised:
        rd.set_default(author.id, "person", actor_id=outsider.id, expected_version=0,
                       target_ids=[str(first.id)])
    assert raised.value.code == "unauthorized"

    with pytest.raises(rd.RecipientDefaultError) as raised:
        rd.set_default(author.id, "all_eligible", actor_id=admin.id, expected_version=0)
    assert raised.value.code == "unauthorized"

    rd.set_default(author.id, "all_eligible", actor_id=author.id, expected_version=0)
    with pytest.raises(rd.RecipientDefaultError) as raised:
        rd.submit(author.id, authors.id, actor_id=admin.id)
    assert raised.value.code == "unauthorized"
    assert rd.get_default(author.id).mode == "all_eligible"


def test_stale_version_then_retry_succeeds():
    _actor, author, first, _second, authors, _staff = _world()
    row = rd.set_default(author.id, "all_eligible", actor_id=author.id, expected_version=0)
    with pytest.raises(rd.VersionConflict) as raised:
        rd.set_default(
            author.id, "person", actor_id=author.id, expected_version=0,
            target_ids=[str(first.id)],
        )
    assert raised.value.code == "stale_version"
    assert raised.value.current_version == row.version
    assert rd.get_default(author.id).mode == "all_eligible"

    stale_version = row.version
    retried = rd.set_default(
        author.id, "person", actor_id=author.id, expected_version=stale_version,
        target_ids=[str(first.id)],
    )
    assert retried.version == stale_version + 1
    assert retried.target_ids == [str(first.id)]

    with pytest.raises(rd.VersionConflict) as raised:
        rd.submit(author.id, authors.id, actor_id=author.id, expected_version=stale_version)
    assert raised.value.current_version == retried.version


def test_removed_recipient_cannot_receive_next_dictation():
    actor, author, first, second, authors, staff = _world()
    rd.set_default(
        author.id, "person", actor_id=author.id, expected_version=0,
        target_ids=[str(first.id)],
    )
    first_send = rd.submit(author.id, authors.id, actor_id=author.id)
    assert first_send.recipient_user_ids == (first.id,)

    pools.remove_staff_member(staff.id, first.id, actor_id=actor.id)
    with pytest.raises(rd.RecipientDefaultError) as raised:
        rd.submit(author.id, authors.id, actor_id=author.id)
    assert raised.value.code == "recipient_not_eligible"

    remaining = rd.submit(
        author.id, authors.id, actor_id=author.id,
        override={"mode": "all_eligible", "target_ids": []},
    )
    assert remaining.recipient_user_ids == (second.id,)
    assert first.id not in remaining.recipient_user_ids
    assert first_send.recipient_user_ids == (first.id,)


def test_self_correction_succeeds_with_no_staff_users():
    actor = _user()
    author = _user()
    authors = pools.create_author_pool("Solo-" + _suffix(), actor_id=actor.id)
    pools.add_author_member(authors.id, author.id, actor_id=actor.id)
    assert pools.ClinicStaffPool.query.count() == 0

    row = rd.set_default(
        author.id, "self_correction", actor_id=author.id, expected_version=0
    )
    sent = rd.submit(author.id, authors.id, actor_id=author.id, expected_version=row.version)
    assert sent.mode == "self_correction"
    assert sent.recipient_user_ids == (author.id,)
    assert rd.eligible_user_ids(authors.id) == ()


def test_default_grants_no_content_rights():
    actor, author, first, _second, authors, staff = _world()
    foreign = _dictation(author, authors.id)
    before = pools.grants_for(first.id, foreign)
    rd.set_default(
        author.id, "person", actor_id=author.id, expected_version=0,
        target_ids=[str(first.id)],
    )
    after = pools.grants_for(first.id, foreign)
    assert after == before
    assert after["allowed"] == {
        "view": {"reason": "staff_link", "link_id": pools.ClinicPoolRoute.query.one().id}
    }
    outsider = _user()
    rd.set_default(
        author.id, "staff_pool", actor_id=author.id, expected_version=1,
        target_ids=[staff.id],
    )
    assert pools.grants_for(outsider.id, foreign) == {"allowed": {}, "denies": []}


def test_choice_policy_hides_persons_and_keeps_default():
    _actor, author, first, _second, authors, staff = _world()
    rd.set_default(
        author.id, "staff_pool", actor_id=author.id, expected_version=0,
        target_ids=[staff.id], choice=rd.CHOICE_DEFAULT_ONLY,
    )
    hidden = rd.selectable_overrides(author.id, authors.id)
    assert hidden == {"persons": (), "staff_pools": ()}
    with pytest.raises(rd.RecipientDefaultError) as raised:
        rd.submit(
            author.id, authors.id, actor_id=author.id,
            override={"mode": "person", "target_ids": [str(first.id)]},
        )
    assert raised.value.code == "choice_disabled"
    kept = rd.submit(author.id, authors.id, actor_id=author.id)
    assert kept.mode == "staff_pool"
    assert first.id in kept.recipient_user_ids

    rd.set_default(
        author.id, "all_eligible", actor_id=author.id, expected_version=1,
        choice=rd.CHOICE_STAFF_POOLS,
    )
    limited = rd.selectable_overrides(author.id, authors.id)
    assert limited["persons"] == ()
    assert limited["staff_pools"] == (staff.id,)
    with pytest.raises(rd.RecipientDefaultError) as raised:
        rd.submit(
            author.id, authors.id, actor_id=author.id,
            override={"mode": "person", "target_ids": [str(first.id)]},
        )
    assert raised.value.code == "person_not_allowed"
