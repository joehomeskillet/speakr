"""Grant-store gaps: per-author scope, separate actions, revoke with stored version."""

import uuid

import pytest

from src.app import app, db
from src.clinic import policy, pools
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.models import Recording, User


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setattr(policy, "_grant_providers", [])
    with app.app_context():
        ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
        pools.create_tables()
        policy.install_pool_policy(app)
        yield
        db.session.rollback()


def _user(*, admin=False):
    key = uuid.uuid4().hex
    user = User(username=key[:20], email=f"{key}@example.invalid", password="x", is_admin=admin)
    db.session.add(user)
    db.session.commit()
    return user


def _dictation(author, pool_id):
    recording = Recording(user_id=author.id, title="d", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(recording_id=recording.id, author_user_id=author.id, pool_id=pool_id)
    db.session.add(row)
    db.session.commit()
    return row


def _scene():
    admin = _user(admin=True)
    pool = pools.create_author_pool("p" + uuid.uuid4().hex[:8], actor_id=admin.id)
    alpha, beta, viewer = _user(), _user(), _user()
    for author in (alpha, beta):
        pools.add_author_member(pool.id, author.id, actor_id=admin.id)
    return admin, pool, alpha, beta, viewer


def _can(viewer, action, dictation):
    subject = policy.build_subject(viewer.id)
    facts = policy.build_facts(dictation.recording_id, dictation.author_user_id)
    return policy.decide(subject, action, facts).allowed


def test_author_deny_wins_inside_allowed_pool():
    admin, pool, alpha, beta, viewer = _scene()
    pools.allow(viewer.id, "pool", actor_id=admin.id, pool_id=pool.id, actions=["view"])
    pools.set_author_scope(viewer.id, actor_id=admin.id, deny_user_ids=[beta.id], pool_id=pool.id)
    d_alpha, d_beta = _dictation(alpha, pool.id), _dictation(beta, pool.id)
    assert "view" in pools.grants_for(viewer.id, d_alpha)["allowed"]
    assert pools.grants_for(viewer.id, d_beta)["allowed"] == {}
    assert _can(viewer, "view", d_alpha) is True
    assert _can(viewer, "view", d_beta) is False


def test_explicit_users_limit_an_all_scope_allow():
    admin, pool, alpha, beta, viewer = _scene()
    pools.set_author_scope(viewer.id, actor_id=admin.id, explicit_users=[alpha.id], actions=["view"])
    assert "view" in pools.grants_for(viewer.id, _dictation(alpha, pool.id))["allowed"]
    assert pools.grants_for(viewer.id, _dictation(beta, pool.id))["allowed"] == {}


@pytest.mark.parametrize("action", pools.SEPARATE_ACTIONS)
def test_each_separate_action_alone_grants_only_itself(action):
    admin, pool, alpha, _beta, viewer = _scene()
    pools.allow(viewer.id, "all", actor_id=admin.id, actions=[action])
    allowed = pools.grants_for(viewer.id, _dictation(alpha, pool.id))["allowed"]
    assert set(allowed) == {action}


def test_unrestricted_allow_does_not_include_separate_actions():
    admin, pool, alpha, _beta, viewer = _scene()
    pools.allow(viewer.id, "all", actor_id=admin.id)
    allowed = set(pools.grants_for(viewer.id, _dictation(alpha, pool.id))["allowed"])
    assert allowed == set(pools.ACTIONS)


def test_unrestricted_deny_blocks_separate_actions():
    admin, pool, alpha, _beta, viewer = _scene()
    pools.allow(viewer.id, "all", actor_id=admin.id, actions=["delete", "view"])
    pools.deny(viewer.id, "all", actor_id=admin.id)
    assert pools.grants_for(viewer.id, _dictation(alpha, pool.id))["allowed"] == {}


def test_revoke_writes_history_and_bumps_stored_version():
    admin, pool, alpha, _beta, viewer = _scene()
    v0 = pools.grant_version()
    row = pools.allow(viewer.id, "all", actor_id=admin.id, actions=["view"])
    v1 = pools.grant_version()
    assert v1 == v0 + 1
    pools.deny(viewer.id, "all", actor_id=admin.id, actions=["play"])
    assert pools.grant_version() == v1 + 1
    grant_id = row.id
    pools.revoke(grant_id, actor_id=admin.id)
    assert pools.grant_version() == v1 + 2
    assert db.session.get(pools.ClinicVisibilityGrant, grant_id) is None
    assert pools.grants_for(viewer.id, _dictation(alpha, pool.id))["allowed"] == {}
    last = pools.history()[-1]
    assert (last["entity"], last["entity_id"], last["change"], last["actor_user_id"]) == (
        "grant", grant_id, "revoked", admin.id,
    )
    assert last["detail"]["user_id"] == viewer.id
    assert last["detail"]["effect"] == "allow"


def test_revoke_of_author_scope_removes_author_rows():
    admin, _pool, _alpha, beta, viewer = _scene()
    row = pools.set_author_scope(viewer.id, actor_id=admin.id, deny_user_ids=[beta.id])[0]
    grant_id = row.id
    pools.revoke(grant_id, actor_id=admin.id)
    assert pools.ClinicGrantAuthor.query.filter_by(grant_id=grant_id).count() == 0


def test_unauthorised_writers_write_nothing():
    admin, pool, alpha, beta, viewer = _scene()
    row = pools.allow(viewer.id, "all", actor_id=admin.id, actions=["view"])
    outsider = _user()
    db.session.add(ClinicAccountPolicy(user_id=outsider.id, status="active"))
    db.session.commit()
    history_before, version_before = len(pools.history()), pools.grant_version()
    grants_before = pools.ClinicVisibilityGrant.query.count()
    with pytest.raises(pools.PoolError) as exc:
        pools.revoke(row.id, actor_id=outsider.id)
    assert exc.value.code == "forbidden"
    with pytest.raises(pools.PoolError):
        pools.set_author_scope(viewer.id, actor_id=outsider.id, deny_user_ids=[beta.id])
    assert len(pools.history()) == history_before
    assert pools.grant_version() == version_before
    assert pools.ClinicVisibilityGrant.query.count() == grants_before
    assert db.session.get(pools.ClinicVisibilityGrant, row.id) is not None
