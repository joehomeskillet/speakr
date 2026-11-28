"""Dictation sending properties and protection routes (PAR-REC-006), HTTP slice.

Positive, unauthorized, stale-version and lockout cases against the real
``presets``, ``workflow`` and ``dictation_lock`` code. BT-REC-006 stays in its
own file.
"""

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import dictation_lock, pools, presets
from src.clinic.routes_dictation_properties import PREFIX, DictationPropertyRoutes
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db

SECRET = "correct horse"


class FakeStore:
    """In-memory ProtectionStore: ``acl`` maps dictation id to the user ids that may see it."""

    def __init__(self):
        self.acl, self.managers, self.rows = {}, {}, {}

    def acl_allows(self, user_id, dictation_id):
        return user_id in self.acl.get(dictation_id, ())

    def can_manage(self, user_id, dictation_id):
        return user_id in self.managers.get(dictation_id, ())

    def get(self, dictation_id):
        return self.rows.get(dictation_id)

    def put(self, dictation_id, record, expected_version):
        _, version = self.rows[dictation_id]
        if version != expected_version:
            return None
        self.rows[dictation_id] = (record, version + 1)
        return version + 1


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    presets.ClinicCategorySet.__table__.create(db.engine, checkfirst=True)
    admin, author, other = _user(admin=True), _user(), _user()
    pool = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    foreign_pool = pools.create_author_pool("F-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(pool.id, author.id, actor_id=admin.id)
    row = presets.save_category_set("Sys-" + _suffix(), ["Ambulant", "Stationär"], is_required=True)
    db.session.commit()

    store, clock = FakeStore(), Clock()
    for name in ("d1", "d2"):
        store.acl[name] = {author.id}
        store.managers[name] = {author.id}
        store.rows[name] = (None, 1)
    routes = DictationPropertyRoutes(store, dictation_lock.LockoutTracker(3, 300), clock, 600)

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

    flask.register_blueprint(routes.blueprint())

    def client(user):
        for key in ("_login_user", "csrf_token"):  # cached per app context
            g.pop(key, None)
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()
    outer.commit()
    with flask.app_context():
        yield dict(client=client, author=author, other=other, pool=pool, foreign_pool=foreign_pool,
                   cat=row, store=store, clock=clock, routes=routes, flask=flask)
        outer.rollback()
        db.session.rollback()
        db.session.query(presets.ClinicCategorySet).filter_by(id=row.id).delete()
        db.session.commit()


def _body(w, **over):
    body = {"title": "Konsil 1", "categories": [{"set_id": w["cat"].id, "version": w["cat"].version,
                                                   "value": "Ambulant"}],
            "pool_id": w["pool"].id, "priority": 128, "deadline_date": "2026-12-01",
            "source": "browser_recording"}
    body.update(over)
    return {k: v for k, v in body.items() if v is not ...}


URL = PREFIX + "/properties/validate"


def test_valid_properties_and_default_deadline_time(world):
    c, h = world["client"](world["author"])
    r = c.post(URL, headers=h, json=_body(world))
    assert r.status_code == 200
    # 23:59:59 Europe/Zurich on 2026-12-01 (UTC+1).
    assert (r.json["priority"], r.json["deadline_at"]) == (128, "2026-12-01T22:59:59+00:00")
    r = c.post(URL, headers=h, json=_body(world, deadline_date=None, priority="Bei Gelegenheit"))
    assert (r.status_code, r.json["priority"], r.json["deadline_at"]) == (200, 0, None)
    r = c.post(URL, headers=h, json=_body(world, deadline_time="08:30"))
    assert r.json["deadline_at"] == "2026-12-01T07:30:00+00:00"


@pytest.mark.parametrize("over,code,status", [
    ({"title": "  "}, "missing_title", 422),
    ({"title": ...}, "missing_title", 422),
    ({"pool_id": ...}, "missing_routing", 422),
    ({"priority": ...}, "missing_priority", 422),
    ({"priority": 7}, "invalid_priority", 422),
    ({"priority": "sofort"}, "invalid_priority", 422),
    ({"deadline_date": ..., "deadline_time": "08:30"}, "deadline_date_required", 422),
    ({"deadline_date": "31.12.2026"}, "invalid_deadline", 422),
    ({"deadline_time": "25:00"}, "invalid_deadline", 422),
    ({"source": ...}, "unknown_source", 422),
    ({"source": "usb_stick"}, "unknown_source", 422),
    ({"categories": []}, "missing_category", 422),
])
def test_invalid_properties(world, over, code, status):
    c, h = world["client"](world["author"])
    r = c.post(URL, headers=h, json=_body(world, **over))
    assert (r.status_code, r.json["code"]) == (status, code)


def test_routing_stale_category_and_untrusted_fields(world):
    c, h = world["client"](world["author"])
    foreign = c.post(URL, headers=h, json=_body(world, pool_id=world["foreign_pool"].id))
    assert (foreign.status_code, foreign.json["code"]) == (422, "routing_not_allowed")
    stale = _body(world)
    stale["categories"][0]["version"] = 99
    r = c.post(URL, headers=h, json=stale)
    assert (r.status_code, r.json["code"], r.json["current_version"]) == (409, "stale_version", 1)
    for key in ("actor_id", "user_id", "role", "is_admin"):
        assert c.post(URL, headers=h, json=_body(world, **{key: world["other"].id})).status_code == 400
    assert c.post(URL, headers=h, json=[1]).status_code == 400
    # Someone outside the pool cannot route into it either.
    c2, h2 = world["client"](world["other"])
    assert c2.post(URL, headers=h2, json=_body(world)).json["code"] == "routing_not_allowed"


def test_unauthenticated_csrf_and_clinic_mode(world, monkeypatch):
    assert world["flask"].test_client().get(PREFIX + "/d1/protection").status_code == 401
    c, _ = world["client"](world["author"])
    r = c.post(URL, json=_body(world))
    assert (r.status_code, r.mimetype) == (403, "application/problem+json")
    monkeypatch.delenv("CLINIC_MODE")
    c, h = world["client"](world["author"])
    assert c.post(URL, headers=h, json=_body(world)).status_code == 404


def _protect(w, c, h, dictation="d1", secret=SECRET, version=1):
    return c.put(f"{PREFIX}/{dictation}/protection", headers=h, json={"secret": secret, "version": version})


def test_protect_unlock_and_grant_expiry(world):
    c, h = world["client"](world["author"])
    uid = world["author"].id
    r = _protect(world, c, h)
    assert (r.status_code, r.json) == (200, {"protected": True, "version": 2})
    setter = c
    assert setter.get(f"{PREFIX}/d1/protection").json == {"protected": True, "version": 2, "access": True}
    c, h = world["client"](world["author"])
    assert c.get(f"{PREFIX}/d1/protection").json == {"protected": True, "version": 2, "access": False}
    assert world["routes"].access_allowed(uid, "d1") is False
    ok = c.post(f"{PREFIX}/d1/unlock", headers=h, json={"secret": SECRET})
    assert (ok.status_code, ok.json["unlocked"]) == (200, True)
    with c:
        assert c.get(f"{PREFIX}/d1/protection").json['access'] is True
        assert world["routes"].access_allowed(uid, "d1") is True
        world["clock"].now += timedelta(seconds=601)
    assert world["routes"].access_allowed(uid, "d1") is False
    assert c.get(f"{PREFIX}/d1/protection").json['access'] is False
    assert setter.get(f"{PREFIX}/d1/protection").json['access'] is False
    # A stored record never contains the secret.
    assert SECRET not in dictation_lock.record_to_json(world["store"].rows["d1"][0])


def test_pin_never_grants_unrelated_object(world):
    c, h = world["client"](world["author"])
    uid = world["author"].id
    _protect(world, c, h, "d1")
    _protect(world, c, h, "d2", secret="other pin")
    c, h = world["client"](world["author"])
    assert c.post(f"{PREFIX}/d1/unlock", headers=h, json={"secret": SECRET}).status_code == 200
    assert world["routes"].access_allowed(uid, "d2") is False
    assert c.get(f"{PREFIX}/d2/protection").json['access'] is False
    # Unlocking with d1's secret does not open d2.
    assert c.post(f"{PREFIX}/d2/unlock", headers=h, json={"secret": SECRET}).status_code == 403
    # A user without ACL cannot unlock or even see the object, whatever the secret.
    c2, h2 = world["client"](world["other"])
    for path in ("/d1/unlock",):
        assert c2.post(PREFIX + path, headers=h2, json={"secret": SECRET}).status_code == 404
    assert c2.get(f"{PREFIX}/d1/protection").status_code == 404
    assert world["routes"].access_allowed(world["other"].id, "d1") is False


def test_lockout_after_failed_attempts_then_release(world):
    c, h = world["client"](world["author"])
    _protect(world, c, h)
    url = f"{PREFIX}/d1/unlock"
    for _ in range(2):
        assert c.post(url, headers=h, json={"secret": "wrong"}).json["code"] == "invalid_secret"
    third = c.post(url, headers=h, json={"secret": "wrong"})
    assert (third.status_code, third.json["code"]) == (429, "locked")
    # The right secret is refused during the lock.
    assert c.post(url, headers=h, json={"secret": SECRET}).status_code == 429
    world["clock"].now += timedelta(seconds=301)
    assert c.post(url, headers=h, json={"secret": SECRET}).status_code == 200


def test_set_protection_stale_version_and_unauthorized(world):
    c, h = world["client"](world["author"])
    stale = _protect(world, c, h, version=7)
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 1)
    assert world["store"].rows["d1"] == (None, 1)
    # Visible but not managing: refused, state unchanged.
    world["store"].managers["d1"] = set()
    assert _protect(world, c, h).status_code == 403
    assert world["store"].rows["d1"][0] is None
    world["store"].managers["d1"] = {world["author"].id}
    for bad in ({"version": 1}, {"secret": "x"}, {"secret": 5, "version": 1}, {"secret": "  ", "version": 1},
                {"secret": "x", "version": 1, "user_id": 3}):
        assert c.put(f"{PREFIX}/d1/protection", headers=h, json=bad).status_code == 400
    assert c.put(f"{PREFIX}/nope/protection", headers=h, json={"secret": "x", "version": 1}).status_code == 404


def test_clear_protection_drops_grants(world):
    setter, setter_headers = world["client"](world["author"])
    _protect(world, setter, setter_headers)
    c, h = world["client"](world["author"])
    stale = _protect(world, c, h, secret=None, version=1)
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 2)
    assert _protect(world, c, h, secret=None, version=2).status_code == 403
    c.post(f"{PREFIX}/d1/unlock", headers=h, json={"secret": SECRET})
    cleared = _protect(world, c, h, secret=None, version=2)
    assert (cleared.status_code, cleared.json) == (200, {"protected": False, "version": 3})
    assert world["routes"].access_allowed(world["author"].id, "d1") is True
    assert c.post(f"{PREFIX}/d1/unlock", headers=h, json={"secret": "anything"}).json["unlocked"] is True
    # Re-protecting invalidates an earlier grant.
    _protect(world, c, h, version=3)
    assert world["routes"].access_allowed(world["author"].id, "d1") is False
    assert setter.get(f"{PREFIX}/d1/protection").json['access'] is False
