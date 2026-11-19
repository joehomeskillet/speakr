"""Preference routes (PAR-ADM-006/007/011, PAR-CFG-001), HTTP slice.

Covers positive, unauthorized, stale-version and retry/failure cases against the
real recipient, selection and workstation services. The original BT oracles stay
in their own files and are not executed here.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import pools, recipient_defaults as rd
from src.clinic.last_selection import Allowed, PreferenceUnavailable, SelectionStore
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db


@pytest.fixture(autouse=True)
def _tables(pool_db):
    rd.create_tables()
    yield


class FlakyPreferences:
    def __init__(self):
        self.rows, self.fail_puts = {}, 0

    def get(self, user_id):
        return self.rows.get(user_id)

    def put(self, user_id, payload):
        if self.fail_puts:
            self.fail_puts -= 1
            raise PreferenceUnavailable("down")
        self.rows[user_id] = payload


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    actor, author, first, second, other = (_user(admin=(i == 0)) for i in range(5))
    authors = pools.create_author_pool("A-" + _suffix(), actor_id=actor.id)
    staff = pools.create_staff_pool("S-" + _suffix(), actor_id=actor.id)
    foreign = pools.create_staff_pool("F-" + _suffix(), actor_id=actor.id)
    pools.add_author_member(authors.id, author.id, actor_id=actor.id)
    pools.link_staff_pool(authors.id, staff.id, actor_id=actor.id)
    pools.add_staff_member(staff.id, first.id, actor_id=actor.id)
    pools.add_staff_member(foreign.id, second.id, actor_id=actor.id)
    state = {"pool": authors.id, "allowed": Allowed(
        roles=frozenset({"author"}), author_ids=frozenset({author.id}),
        pool_ids=frozenset({authors.id}), own_author_id=author.id)}
    prefs = FlakyPreferences()
    routes = PreferenceRoutes(
        allowed_for=lambda _uid: state["allowed"],
        author_pool_for=lambda _uid: state["pool"],
        selections=SelectionStore(prefs),
        workstations=WorkstationPreferences(),
    )
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic", SQLALCHEMY_DATABASE_URI=real_app.config[
        "SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(routes.blueprint)

    def client(user):
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    with flask.app_context():
        yield dict(client=client, author=author, first=first, second=second, other=other,
                   staff=staff, foreign=foreign, authors=authors, state=state, prefs=prefs,
                   flask=flask)


def test_recipient_default_positive_and_use_time_revalidation(world):
    c, h = world["client"](world["author"])
    url = PREFIX + "/recipient-default"
    assert c.get(url).json["version"] == 0
    put = c.put(url, headers=h, json={"expected_version": 0, "mode": "person",
                                       "target_ids": [str(world["first"].id)]})
    assert (put.status_code, put.json["version"]) == (200, 1)
    got = c.get(url).json
    assert got["mode"] == "person" and str(world["first"].id) in got["overrides"]["persons"]
    resolved = c.post(url + "/resolve", headers=h, json={"expected_version": 1})
    assert resolved.json["recipient_user_ids"] == [world["first"].id]
    # The author loses the pool: the stored default is refused at use time.
    world["state"]["pool"] = None
    denied = c.post(url + "/resolve", headers=h, json={})
    assert (denied.status_code, denied.json["code"]) == (422, "not_author_pool")


def test_foreign_ids_stale_version_and_untrusted_body(world):
    c, h = world["client"](world["author"])
    url = PREFIX + "/recipient-default"
    for mode, ids in (("person", [str(world["second"].id)]), ("staff_pool", [world["foreign"].id]),
                      ("person", ["999999"])):
        r = c.put(url, headers=h, json={"expected_version": 0, "mode": mode, "target_ids": ids})
        assert r.status_code in (400, 403), (mode, r.json)
    ok = c.put(url, headers=h, json={"expected_version": 0, "mode": "all_eligible"})
    assert ok.status_code == 200
    stale = c.put(url, headers=h, json={"expected_version": 0, "mode": "all_eligible"})
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 1)
    # An actor supplied in the body is refused, not trusted.
    for key in ("actor_id", "user_id", "role"):
        r = c.put(url, headers=h, json={"expected_version": 1, "mode": "all_eligible", key: world["other"].id})
        assert r.status_code == 400
    assert rd.get_default(world["author"].id).version == 1
    assert rd.get_default(world["other"].id) is None


def test_unauthorized_csrf_and_clinic_mode(world, monkeypatch):
    anon = world["flask"].test_client()
    assert anon.get(PREFIX + "/last-selection").status_code == 401
    c, h = world["client"](world["author"])
    body = {"expected_version": 0, "mode": "all_eligible"}
    no_csrf = c.put(PREFIX + "/recipient-default", json=body)
    assert (no_csrf.status_code, no_csrf.mimetype) == (403, "application/problem+json")
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(PREFIX + "/last-selection").status_code == 404


def test_last_selection_save_restore_and_stale_access(world):
    c, h = world["client"](world["author"])
    url = PREFIX + "/last-selection"
    sel = {"role": "author", "author_id": world["author"].id, "pool_id": world["authors"].id}
    assert c.put(url, headers=h, json={"selection": sel}).status_code == 200
    # New workstation, new process cache: restored from the stored preference.
    c2, _ = world["client"](world["author"])
    assert c2.get(url).json == {"selection": dict(sel, source_id=None, template_id=None), "fallback": []}
    # Foreign author is rejected at save time.
    bad = c.put(url, headers=h, json={"selection": dict(sel, author_id=world["other"].id)})
    assert (bad.status_code, bad.json["code"]) == (422, "selection_not_allowed")
    # Access is withdrawn: use time falls back instead of restoring the old pool.
    world["state"]["allowed"] = Allowed(roles=frozenset({"author"}), author_ids=frozenset({world["author"].id}),
                                        pool_ids=frozenset(), own_author_id=world["author"].id)
    got = c2.get(url).json
    assert got["selection"]["pool_id"] is None and got["fallback"] == ["pool_not_allowed"]
    assert c.put(url, headers=h, json={"selection": {"role": "author", "x": 1}}).status_code == 400


def test_last_selection_retry_and_failure(world):
    c, h = world["client"](world["author"])
    url = PREFIX + "/last-selection"
    sel = {"selection": {"role": "author", "author_id": world["author"].id}}
    world["prefs"].fail_puts = 1  # one failure: the store retries once
    assert c.put(url, headers=h, json=sel).status_code == 200
    world["prefs"].fail_puts = 2
    failed = c.put(url, headers=h, json=sel)
    assert (failed.status_code, failed.json["code"]) == (503, "preference_write_failed")


def test_workstation_issue_apply_stale_and_foreign(world):
    c, h = world["client"](world["author"])
    wid = c.post(PREFIX + "/workstations", headers=h).json["workstation_id"]
    url = f"{PREFIX}/workstations/{wid}"
    r = c.put(url, headers=h, json={"expected_version": 0, "changes": {"theme_dark_mode": True}})
    assert (r.status_code, r.json["version"]) == (200, 1)
    assert c.get(url).json["values"] == {"theme_dark_mode": True}
    stale = c.put(url, headers=h, json={"expected_version": 0, "changes": {"theme_dark_mode": False}})
    assert (stale.status_code, stale.json["code"]) == (409, "stale_version")
    capability = c.put(url, headers=h, json={"expected_version": 1, "changes": {"native_bridge": True}})
    assert (capability.status_code, capability.json["code"]) == (403, "capability_forbidden")
    # Another account cannot use or read this workstation id.
    c2, h2 = world["client"](world["other"])
    assert c2.get(url).status_code == 403
    assert c2.put(url, headers=h2, json={"expected_version": 1, "changes": {"theme_dark_mode": True}}).status_code == 403
