"""Per-user key and pedal bindings (PAR-OPS-003) through the preference routes.

GET returns the signed-in user's effective bindings and the canonical defaults.
PUT changes one chord through ``key_bindings.set_binding``. DELETE resets one
command or every command. The session user is the only actor.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import key_bindings
from src.clinic.key_bindings import DEFAULTS
from src.clinic.last_selection import SelectionStore
from src.clinic.route_policy_admin import AUTH
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences
from src.database import db
from src.models import User

from test_pools import _user, pool_db as pool_db

URL = PREFIX + "/key-bindings"


def _payload(stored_bindings, version):
    return {"bindings": stored_bindings, "defaults": dict(DEFAULTS), "version": version}


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    one, two = _user(), _user()
    routes = PreferenceRoutes(
        allowed_for=lambda _uid: None, author_pool_for=lambda _uid: None,
        selections=SelectionStore(), workstations=WorkstationPreferences())
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
    with flask.app_context():
        key_bindings.create_tables()

    def client(user):
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    yield dict(client=client, one=one, two=two, flask=flask)


def test_get_returns_defaults_until_the_user_changes_one_chord(world):
    assert "clinic_preferences.key_bindings" in AUTH
    c, h = world["client"](world["one"])
    got = c.get(URL)
    assert got.status_code == 200
    assert got.json == _payload(dict(DEFAULTS), 0)
    put = c.put(URL, headers=h, json={"action": "stop", "chord": "f1", "expected_version": 0})
    assert put.status_code == 200
    assert put.json["version"] == 1
    assert put.json["defaults"] == dict(DEFAULTS)
    assert put.json["bindings"]["stop"] == "F1"
    assert put.json["bindings"]["rewind"] == DEFAULTS["rewind"]
    reloaded, _ = world["client"](world["one"])
    assert reloaded.get(URL).json["bindings"]["stop"] == "F1"
    with world["flask"].app_context():
        row = db.session.get(key_bindings.ClinicKeyBindings, world["one"].id)
        assert row is not None
        assert row.version == 1
        assert key_bindings.from_json(row.bindings_json)["stop"] == "F1"


def test_reserved_chord_and_conflict_do_not_change_the_row(world):
    c, h = world["client"](world["one"])
    reserved = c.put(URL, headers=h, json={"action": "stop", "chord": "Ctrl+W", "expected_version": 0})
    assert (reserved.status_code, reserved.json["code"], reserved.mimetype) == (
        422, "reserved_chord", "application/problem+json")
    assert c.get(URL).json["version"] == 0
    # A reserved chord is rejected even when the action is omitted or unknown.
    bare = c.put(URL, headers=h, json={"chord": "Ctrl+W"})
    assert (bare.status_code, bare.json["code"]) == (422, "reserved_chord")
    saved = c.put(URL, headers=h, json={"action": "stop", "chord": "F1", "expected_version": 0})
    assert saved.status_code == 200
    clash = c.put(URL, headers=h, json={"action": "rewind", "chord": "f1", "expected_version": 1})
    assert (clash.status_code, clash.json["code"]) == (409, "binding_conflict")
    assert ["F1", "stop"] in clash.json["conflicts"]
    unknown = c.put(URL, headers=h, json={"action": "nope", "chord": "F2", "expected_version": 1})
    assert (unknown.status_code, unknown.json["code"]) == (422, "unknown_action")
    malformed = c.put(URL, headers=h, json={"action": "stop", "chord": "Ctrl+", "expected_version": 1})
    assert (malformed.status_code, malformed.json["code"]) == (422, "bad_chord")
    assert c.get(URL).json["bindings"]["stop"] == "F1"
    stale = c.put(URL, headers=h, json={"action": "play_pause", "chord": "F3", "expected_version": 0})
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 1)


def test_delete_resets_one_command_or_every_default(world):
    c, h = world["client"](world["one"])
    c.put(URL, headers=h, json={"action": "stop", "chord": "F1", "expected_version": 0})
    c.put(URL, headers=h, json={"action": "rewind", "chord": "F2", "expected_version": 1})
    one = c.delete(URL + "?action=stop", headers=h)
    assert one.status_code == 200
    assert one.json["bindings"]["stop"] == DEFAULTS["stop"]
    assert one.json["bindings"]["rewind"] == "F2"
    assert one.json["defaults"] == dict(DEFAULTS)
    # rewind holds stop's default, so restoring only stop is a conflict.
    c.put(URL, headers=h, json={
        "action": "stop", "chord": "F1", "expected_version": one.json["version"]})
    held = c.put(URL, headers=h, json={
        "action": "rewind", "chord": DEFAULTS["stop"], "expected_version": one.json["version"] + 1})
    assert held.status_code == 200
    blocked = c.delete(
        URL + "?action=stop&expected_version=" + str(held.json["version"]), headers=h)
    assert (blocked.status_code, blocked.json["code"]) == (409, "binding_conflict")
    assert c.get(URL).json["bindings"]["stop"] == "F1"
    assert c.get(URL).json["bindings"]["rewind"] == DEFAULTS["stop"]
    all_reset = c.delete(URL, headers=h, json={"expected_version": c.get(URL).json["version"]})
    assert all_reset.status_code == 200
    assert all_reset.json["bindings"] == dict(DEFAULTS)
    blank = c.delete(URL + "?action=", headers=h)
    assert blank.json["bindings"] == dict(DEFAULTS)


def test_users_stay_isolated_and_the_body_cannot_name_an_actor(world):
    c, h = world["client"](world["one"])
    c.put(URL, headers=h, json={"action": "finish", "chord": "F4", "expected_version": 0})
    c2, h2 = world["client"](world["two"])
    assert c2.get(URL).json == _payload(dict(DEFAULTS), 0)
    refused = c2.put(URL, headers=h2, json={
        "action": "stop", "chord": "F8", "expected_version": 0, "user_id": world["one"].id})
    assert refused.status_code == 400
    for key in ("actor", "actor_id", "user", "role"):
        r = c2.delete(URL, headers=h2, json={key: world["one"].id})
        assert r.status_code == 400
    assert c.get(URL).json["bindings"]["finish"] == "F4"
    assert c2.get(URL).json["version"] == 0
    own = c2.put(URL, headers=h2, json={"action": "stop", "chord": "F8", "expected_version": 0})
    assert own.status_code == 200
    assert c.get(URL).json["bindings"]["stop"] == DEFAULTS["stop"]
    assert c2.get(URL).json["bindings"]["stop"] == "F8"


def test_session_csrf_and_clinic_mode(world, monkeypatch):
    anon = world["flask"].test_client()
    assert anon.get(URL).status_code == 401
    c, _h = world["client"](world["one"])
    assert c.put(URL, json={"action": "stop", "chord": "F6"}).status_code == 403
    assert c.delete(URL + "?action=stop").status_code == 403
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(URL).status_code == 404
    rules = {rule.rule for rule in real_app.url_map.iter_rules()}
    assert URL not in rules
