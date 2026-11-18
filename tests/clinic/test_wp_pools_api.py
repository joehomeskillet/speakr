"""Pool membership routes (PAR-ADM-002, PAR-ADM-003), HTTP slice.

Positive, unauthorized, stale-revision and failure cases against the real
``pools`` service. BT-ADM-002 and BT-ADM-003 stay in their own files.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import pools
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.routes_pools import PREFIX, blueprint
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
    admin, member, other, outsider = _user(admin=True), _user(), _user(), _user()
    author = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    foreign = pools.create_author_pool("F-" + _suffix(), actor_id=admin.id)
    staff = pools.create_staff_pool("S-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(author.id, member.id, actor_id=admin.id)
    pools.add_author_member(foreign.id, other.id, actor_id=admin.id)
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(blueprint)

    def client(user):
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()  # the shared pool_db session holds a read lock until rolled back
    outer.commit()
    with flask.app_context():
        yield dict(client=client, admin=admin, member=member, other=other, outsider=outsider,
                   author=author, foreign=foreign, staff=staff)
        outer.rollback()
        db.session.rollback()


def _find(data, key, pool):
    return next(p for p in data[key] if p["id"] == pool.id)


def _put(c, h, kind, pool, rev, members, name=None):
    return c.put(f"{PREFIX}/{kind}/{pool.id}", headers=h, json={
        "expected_revision": rev, "name": name or pool.name, "member_user_ids": members})


def test_admin_lists_both_kinds_separately(world):
    c, _ = world["client"](world["admin"])
    data = c.get(PREFIX).json
    assert isinstance(data["revision"], int)
    assert _find(data, "author_pools", world["author"])["member_user_ids"] == [world["member"].id]
    assert _find(data, "staff_pools", world["staff"])["member_user_ids"] == []
    assert world["staff"].id not in {p["id"] for p in data["author_pools"]}


def test_member_sees_only_own_pool_without_other_members(world):
    c, _ = world["client"](world["member"])
    data = c.get(PREFIX).json
    own = _find(data, "author_pools", world["author"])
    assert own["member_user_ids"] == [world["member"].id]
    assert world["foreign"].id not in {p["id"] for p in data["author_pools"]}
    assert data["staff_pools"] == []


def test_save_author_pool_adds_and_removes_with_history(world):
    c, h = world["client"](world["admin"])
    rev = c.get(PREFIX).json["revision"]
    res = _put(c, h, "author", world["author"], rev, [world["other"].id, world["outsider"].id])
    assert res.status_code == 200
    now = _find(res.json, "author_pools", world["author"])
    assert now["member_user_ids"] == sorted([world["other"].id, world["outsider"].id])
    assert res.json["revision"] == rev + 3  # one removal, two additions
    # The other pool is untouched.
    assert _find(res.json, "author_pools", world["foreign"])["member_user_ids"] == [world["other"].id]
    details = [h_["detail"] for h_ in pools.history() if h_["entity"] == "membership"][-3:]
    assert {d["user_id"] for d in details} == {world["member"].id, world["other"].id, world["outsider"].id}


def test_save_staff_pool_membership_and_rename(world):
    c, h = world["client"](world["admin"])
    rev = c.get(PREFIX).json["revision"]
    res = _put(c, h, "staff", world["staff"], rev, [world["member"].id], name="Neu-" + _suffix())
    assert res.status_code == 200
    now = _find(res.json, "staff_pools", world["staff"])
    assert now["member_user_ids"] == [world["member"].id] and now["name"].startswith("Neu-")
    assert res.json["revision"] == rev + 2


def test_stale_revision_is_409_and_changes_nothing(world):
    c, h = world["client"](world["admin"])
    rev = c.get(PREFIX).json["revision"]
    assert _put(c, h, "author", world["author"], rev, [world["member"].id, world["outsider"].id]).status_code == 200
    stale = _put(c, h, "author", world["author"], rev, [])
    assert stale.status_code == 409 and stale.json["code"] == "stale_revision"
    data = c.get(PREFIX).json
    assert _find(data, "author_pools", world["author"])["member_user_ids"] == sorted(
        [world["member"].id, world["outsider"].id])


def test_unauthorized_actor_cannot_write(world):
    c, h = world["client"](world["member"])
    rev = c.get(PREFIX).json["revision"]
    res = _put(c, h, "author", world["author"], rev, [])
    assert res.status_code == 403
    admin_c, _ = world["client"](world["admin"])
    assert _find(admin_c.get(PREFIX).json, "author_pools", world["author"])["member_user_ids"] == [world["member"].id]


def test_deactivated_admin_cannot_write(world):
    db.session.add(ClinicAccountPolicy(user_id=world["admin"].id, status="disabled"))
    db.session.commit()
    c, h = world["client"](world["admin"])
    assert _put(c, h, "author", world["author"], 0, []).status_code == 403


def test_anonymous_and_missing_csrf(world):
    c =world["client"](world["admin"])[0]
    assert c.put(f"{PREFIX}/author/{world['author'].id}", json={
        "expected_revision": 0, "name": "x", "member_user_ids": []}).status_code == 403
    fresh = c.application.test_client()
    assert fresh.get(PREFIX).status_code == 401


def test_request_cannot_name_actor_and_bad_input(world):
    c, h = world["client"](world["admin"])
    rev = c.get(PREFIX).json["revision"]
    url = f"{PREFIX}/author/{world['author'].id}"
    base = {"expected_revision": rev, "name": world["author"].name, "member_user_ids": []}
    assert c.put(url, headers=h, json={**base, "actor_id": world["admin"].id}).status_code == 400
    assert c.put(url, headers=h, json={**base, "member_user_ids": [1, 1]}).status_code == 400
    assert c.put(url, headers=h, json={**base, "member_user_ids": [10**9]}).status_code == 400
    assert c.put(url, headers=h, json={**base, "expected_revision": "x"}).status_code == 400
    assert c.put(f"{PREFIX}/bogus/{world['author'].id}", headers=h, json=base).status_code == 404
    assert c.put(f"{PREFIX}/author/{world['staff'].id}", headers=h, json=base).status_code == 404
    assert c.get(PREFIX).json["revision"] == rev


def test_retry_after_failure_is_safe(world):
    c, h = world["client"](world["admin"])
    rev = c.get(PREFIX).json["revision"]
    assert _put(c, h, "author", world["author"], rev, [world["member"].id], name=world["foreign"].name).status_code == 409
    assert c.get(PREFIX).json["revision"] == rev
    assert _put(c, h, "author", world["author"], rev, [world["member"].id, world["outsider"].id]).status_code == 200
