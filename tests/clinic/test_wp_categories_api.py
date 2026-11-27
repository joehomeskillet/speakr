"""Category set routes (PAR-CFG-003), HTTP slice.

Positive, unauthorized, stale-version and failure cases against the real
``presets`` category validation. BT-CFG-003 stays in its own file.
"""

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import pools, presets
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.routes_categories import PREFIX, blueprint
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    presets.ClinicCategorySet.__table__.create(db.engine, checkfirst=True)
    ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
    admin, author, other = _user(admin=True), _user(), _user()
    revoked = _user(admin=True)
    db.session.add(ClinicAccountPolicy(user_id=admin.id, status="active"))
    db.session.add(ClinicAccountPolicy(user_id=revoked.id, status="disabled"))
    db.session.commit()
    own_pool = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    foreign_pool = pools.create_author_pool("F-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(own_pool.id, author.id, actor_id=admin.id)
    created = []

    def make(name, cats, required, scope="system", scope_id=None):
        row = presets.save_category_set(name, cats, is_required=required, scope=scope, scope_id=scope_id)
        db.session.commit()
        created.append(row.id)
        return row

    system = make("Sys-" + _suffix(), ["Ambulant", "Stationär"], True)
    pool_set = make("Pool-" + _suffix(), ["Konsil"], False, "pool", own_pool.id)
    foreign_set = make("Fremd-" + _suffix(), ["Notfall"], True, "pool", foreign_pool.id)
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
        for key in ("_login_user", "csrf_token"):  # cached on the shared app context
            g.pop(key, None)
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()  # the shared pool_db session holds a read lock until rolled back
    outer.commit()
    with flask.app_context():
        yield dict(client=client, author=author, other=other, admin=admin, revoked=revoked,
                   system=system, pool_set=pool_set, foreign_set=foreign_set, flask=flask,
                   created=created)
        outer.rollback()
        db.session.rollback()
        db.session.query(presets.ClinicCategorySet).filter(
            presets.ClinicCategorySet.id.in_(created)).delete(synchronize_session=False)
        db.session.query(ClinicAccountPolicy).filter(
            ClinicAccountPolicy.user_id.in_([admin.id, revoked.id])).delete(synchronize_session=False)
        db.session.commit()


def _sel(row, value, version=None):
    return {"set_id": row.id, "version": row.version if version is None else version, "value": value}


def test_list_shows_only_visible_sets(world):
    c, _ = world["client"](world["author"])
    ids = {s["id"] for s in c.get(PREFIX).json["sets"]}
    assert {world["system"].id, world["pool_set"].id} <= ids
    assert world["foreign_set"].id not in ids
    listed = {s["id"]: s for s in c.get(PREFIX).json["sets"]}[world["system"].id]
    assert listed["required"] is True and listed["categories"] == ["Ambulant", "Stationär"]
    # A user without that pool does not see the pool set.
    c2, _ = world["client"](world["other"])
    assert world["pool_set"].id not in {s["id"] for s in c2.get(PREFIX).json["sets"]}


def test_check_positive(world):
    c, h = world["client"](world["author"])
    ok = c.post(PREFIX + "/check", headers=h, json={"selections": [
        _sel(world["system"], "Stationär-Befund"), _sel(world["pool_set"], "Konsil")]})
    assert (ok.status_code, ok.json) == (200, {"ok": True})


def test_omitted_or_empty_mandatory_category_fails(world):
    c, h = world["client"](world["author"])
    url = PREFIX + "/check"
    omitted = c.post(url, headers=h, json={"selections": []})
    assert (omitted.status_code, omitted.json["code"], omitted.json["set_id"]) == (
        422, "missing_category", world["system"].id)
    empty = c.post(url, headers=h, json={"selections": [_sel(world["system"], "  ")]})
    assert (empty.status_code, empty.json["code"]) == (422, "missing_category")
    wrong = c.post(url, headers=h, json={"selections": [_sel(world["system"], "Keine Kategorie")]})
    assert (wrong.status_code, wrong.json["code"]) == (422, "invalid_category")


def test_stale_version_fails_and_leaves_state_unchanged(world):
    c, h = world["client"](world["author"])
    row = world["system"]
    stale = c.post(PREFIX + "/check", headers=h, json={"selections": [_sel(row, "Ambulant")]})
    assert stale.status_code == 200
    db.session.query(presets.ClinicCategorySet).filter_by(id=row.id).update({"version": 2})
    db.session.commit()
    stale = c.post(PREFIX + "/check", headers=h, json={"selections": [_sel(row, "Ambulant", version=1)]})
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 2)
    db.session.expire_all()
    assert db.session.get(presets.ClinicCategorySet, row.id).version == 2


def test_foreign_unknown_and_untrusted_payloads(world):
    c, h = world["client"](world["author"])
    url = PREFIX + "/check"
    ok = _sel(world["system"], "Ambulant")
    foreign = c.post(url, headers=h, json={"selections": [ok, _sel(world["foreign_set"], "Notfall")]})
    assert (foreign.status_code, foreign.json["code"]) == (404, "unknown_category_set")
    gone = c.post(url, headers=h, json={"selections": [ok, {"set_id": "nope", "version": 1, "value": "x"}]})
    assert gone.status_code == 404
    for key in ("actor_id", "user_id", "role", "is_admin"):
        r = c.post(url, headers=h, json={"selections": [ok], key: world["other"].id})
        assert r.status_code == 400
    for bad in (None, "x", [1], [dict(ok), dict(ok)], [dict(ok, value=5)]):
        assert c.post(url, headers=h, json={"selections": bad}).status_code == 400


def test_admin_creates_and_updates_a_required_set(world):
    c, h = world["client"](world["admin"])
    created = c.post(PREFIX, headers={"X-CSRFToken": h["X-CSRF-Token"]}, json={
        "name": "Versand-" + _suffix(), "categories": ["Ambulant", "Stationär"], "required": True})
    assert created.status_code == 201, created.get_data(as_text=True)
    body = created.json
    world["created"].append(body["id"])
    assert body["required"] is True and body["version"] == 1
    assert body["categories"] == ["Ambulant", "Stationär"]
    listed = {row["id"]: row for row in c.get(PREFIX).json["sets"]}
    assert listed[body["id"]]["required"] is True
    stale = c.put(PREFIX, headers=h, json={"id": body["id"], "version": 9, "required": False})
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 1)
    db.session.expire_all()
    stored = db.session.get(presets.ClinicCategorySet, body["id"])
    assert stored.version == 1 and stored.is_required is True
    updated = c.put(PREFIX, headers=h, json={
        "id": body["id"], "version": 1, "is_required": True, "categories": ["Ambulant", "Konsil"]})
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert updated.json["version"] == 2 and updated.json["categories"] == ["Ambulant", "Konsil"]
    assert updated.json["required"] is True


def test_category_writes_reject_unauthorized_callers(world):
    payload = {"name": "Nein-" + _suffix(), "categories": ["Ambulant"], "required": True}
    for user in (world["author"], world["revoked"]):
        c, h = world["client"](user)
        refused = c.post(PREFIX, headers=h, json=payload)
        assert (refused.status_code, refused.json["code"]) == (403, "unauthorized")
        assert c.put(PREFIX, headers=h, json={"id": world["system"].id, "version": 1, "required": False}).status_code == 403
    c, h = world["client"](world["admin"])
    assert c.post(PREFIX, json=payload).status_code == 403
    forged = c.post(PREFIX, headers=h, json={**payload, "actor_id": world["other"].id})
    assert forged.status_code == 400


def test_unauthorized_csrf_and_clinic_mode(world, monkeypatch):
    assert world["flask"].test_client().get(PREFIX).status_code == 401
    c, h = world["client"](world["author"])
    no_csrf = c.post(PREFIX + "/check", json={"selections": []})
    assert (no_csrf.status_code, no_csrf.mimetype) == (403, "application/problem+json")
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(PREFIX).status_code == 404
