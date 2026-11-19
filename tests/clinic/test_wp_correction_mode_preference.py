"""Per-user correction playback mode (W6) through the real preference routes."""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import correction_modes
from src.clinic.last_selection import SelectionStore
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences
from src.database import db
from src.models import User

from test_pools import _user, pool_db as pool_db

URL = PREFIX + "/correction-mode"


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    db.metadata.create_all(bind=db.engine, tables=[correction_modes.ClinicCorrectionMode.__table__])
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

    def client(user):
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    # No shared app context: each request gets its own, so users do not leak across clients.
    yield dict(client=client, one=one, two=two, flask=flask)


def test_default_set_reload_and_isolation(world):
    c, h = world["client"](world["one"])
    got = c.get(URL)
    assert (got.status_code, got.json["mode"]) == (200, "stop_on_typing")
    put = c.put(URL, headers=h, json={"mode": "type_while_playing"})
    assert (put.status_code, put.json["mode"], put.json["version"]) == (200, "type_while_playing", 1)
    reloaded, _ = world["client"](world["one"])
    assert reloaded.get(URL).json["mode"] == "type_while_playing"
    # A second user still sees the default and keeps their own value.
    c2, h2 = world["client"](world["two"])
    assert c2.get(URL).json == {"mode": "stop_on_typing", "version": 0}
    assert c2.put(URL, headers=h2, json={"mode": "stop_on_typing"}).json["version"] == 1
    assert c.get(URL).json["mode"] == "type_while_playing"
    # A body naming another user is refused, not trusted.
    r = c2.put(URL, headers=h2, json={"mode": "type_while_playing", "user_id": world["one"].id})
    assert r.status_code == 400
    assert c.get(URL).json["mode"] == "type_while_playing"


def test_stale_version_unknown_mode_auth_and_csrf(world):
    c, h = world["client"](world["one"])
    ok = c.put(URL, headers=h, json={"mode": "type_while_playing", "expected_version": 0})
    assert ok.status_code == 200
    stale = c.put(URL, headers=h, json={"mode": "stop_on_typing", "expected_version": 0})
    assert (stale.status_code, stale.json["code"]) == (409, "stale_version")
    unknown = c.put(URL, headers=h, json={"mode": "nonsense"})
    assert (unknown.status_code, unknown.json["code"]) == (422, "invalid_mode")
    assert c.get(URL).json["mode"] == "type_while_playing"
    assert world["flask"].test_client().get(URL).status_code == 401
    assert c.put(URL, json={"mode": "stop_on_typing"}).status_code == 403


def test_clinic_mode_off_hides_route_and_url_map(world, monkeypatch):
    c, _ = world["client"](world["one"])
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(URL).status_code == 404
    rules = {rule.rule for rule in real_app.url_map.iter_rules()}
    assert URL not in rules
