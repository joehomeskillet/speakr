"""Authorisation of pool mutations, grant writes and import intents."""

import uuid

import pytest

from src.app import app, db
from src.clinic import import_centre, policy, pools
from src.clinic.agent_channel import AgentChannelError
from src.clinic.identity import ClinicAccountPolicy
from src.models import User


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setattr(policy, "_grant_providers", [])
    with app.app_context():
        ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
        pools.create_tables()
        import_centre.create_tables()
        yield
        db.session.rollback()


def _user(*, admin=False, status=None):
    key = uuid.uuid4().hex
    user = User(username=key, email=f"{key}@example.invalid", password="x", is_admin=admin)
    db.session.add(user)
    db.session.flush()
    if status is not None:
        db.session.add(ClinicAccountPolicy(user_id=user.id, status=status))
    db.session.commit()
    return user


def _grant(user, *actions):
    user_id = user.id
    policy.register_grant_provider(
        lambda uid: (policy.Grant("all", frozenset(actions)),) if uid == user_id else ()
    )


def _history_count():
    return pools.ClinicPoolHistory.query.count()


def _intent(user):
    return import_centre.create_import_intent(
        actor_user_id=user.id, source_kind="folder", source_ref=user.username,
    )


def test_non_admin_without_grant_is_forbidden_everywhere():
    user = _user(status="active")
    target = _user()
    pool = pools.create_author_pool(f"p{uuid.uuid4().hex[:8]}", actor_id=_user(admin=True).id)
    before = _history_count()
    with pytest.raises(pools.PoolError) as exc:
        pools.create_author_pool("x" + uuid.uuid4().hex[:8], actor_id=user.id)
    assert exc.value.code == "forbidden"
    with pytest.raises(pools.PoolError):
        pools.add_author_member(pool.id, target.id, actor_id=user.id)
    with pytest.raises(pools.PoolError):
        pools.allow(target.id, "all", actor_id=user.id)
    with pytest.raises(AgentChannelError) as exc2:
        _intent(user)
    assert exc2.value.code == "forbidden"
    assert _history_count() == before


def test_admin_allowed():
    admin = _user(admin=True)
    pool = pools.create_author_pool("a" + uuid.uuid4().hex[:8], actor_id=admin.id)
    member = _user()
    pools.add_author_member(pool.id, member.id, actor_id=admin.id)
    pools.allow(member.id, "all", actor_id=admin.id)
    assert _intent(admin)["status"] == "OPEN"


def test_explicit_grants_allowed_and_specific_to_action():
    user = _user(status="active")
    _grant(user, "manage_pools", "import")
    pool = pools.create_author_pool("g" + uuid.uuid4().hex[:8], actor_id=user.id)
    assert pool.id
    assert _intent(user)["status"] == "OPEN"
    with pytest.raises(pools.PoolError):  # manage_pools does not cover grant writes
        pools.allow(user.id, "all", actor_id=user.id)


def test_manage_grants_allows_grant_write():
    user = _user(status="active")
    _grant(user, "manage_grants")
    assert pools.allow(user.id, "all", actor_id=user.id).id


@pytest.mark.parametrize("status", ["disabled", "pending_activation"])
def test_inactive_admin_denied(status):
    admin = _user(admin=True, status=status)
    before = _history_count()
    with pytest.raises(pools.PoolError):
        pools.create_author_pool("i" + uuid.uuid4().hex[:8], actor_id=admin.id)
    with pytest.raises(AgentChannelError) as exc:
        _intent(admin)
    assert exc.value.code == "forbidden"
    assert _history_count() == before


def test_foreign_author_rule_stays_on_top():
    user = _user(status="active")
    other = _user()
    _grant(user, "import")
    with pytest.raises(AgentChannelError):
        import_centre.create_import_intent(
            actor_user_id=user.id, source_kind="folder", source_ref="r1", target_author_user_id=other.id,
        )
    intent = import_centre.create_import_intent(
        actor_user_id=user.id, source_kind="folder", source_ref="r1", target_author_user_id=other.id,
        may_assign_author=lambda actor, author: True,
    )
    assert intent["target_author_user_id"] == other.id
