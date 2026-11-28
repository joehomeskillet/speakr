"""GET/PUT /api/clinic/v1/preferences/output (PAR-EXP-001).

The signed-in account's selected formats and open-after-export flag are stored
in clinic_output_preference. If-Match is the stored version. A body that names
a user is refused. A fresh Flask app still reads the row.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy.orm import Session

from src.app import app as real_app
from src.clinic import output_preferences
from src.clinic.last_selection import SelectionStore
from src.clinic.route_policy_output import AUTH
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences
from src.database import db
from src.models import User

from test_pools import _user, pool_db as pool_db

URL = PREFIX + "/output"
FORMATS = ["clipboard", "rtf"]


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    db.metadata.create_all(bind=db.engine, tables=[output_preferences.ClinicOutputPreference.__table__])
    one, two = _user(), _user()

    def make_app():
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

        routes = PreferenceRoutes(
            allowed_for=lambda _uid: None, author_pool_for=lambda _uid: None,
            selections=SelectionStore(), workstations=WorkstationPreferences())
        flask.register_blueprint(routes.blueprint)
        return flask

    flask = make_app()

    def client(user, app=flask):
        c = app.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    data = dict(client=client, one=one, two=two, one_id=one.id, two_id=two.id, flask=flask,
                make_app=make_app)
    yield data
    db.session.query(output_preferences.ClinicOutputPreference).filter(
        output_preferences.ClinicOutputPreference.user_id.in_([data["one_id"], data["two_id"]])
    ).delete(synchronize_session=False)
    db.session.commit()


def _put(client, headers, formats, version, **extra):
    sent = {"formats": formats, "open_after_export": extra.pop("open_after_export", False), **extra}
    return client.put(URL, headers={**headers, "If-Match": f'"{version}"'}, json=sent)


def test_output_route_is_classified_for_the_signed_in_user():
    assert "clinic_preferences.output" in AUTH


def test_put_survives_a_new_app_and_stays_on_that_user(world):
    one, headers = world["client"](world["one"])
    assert one.get(URL).json == {"version": 0, "formats": [], "open_after_export": False}
    saved = _put(one, headers, FORMATS, 0, open_after_export=True)
    assert saved.status_code == 200
    assert saved.json["formats"] == FORMATS
    assert saved.json["open_after_export"] is True
    assert saved.json["version"] == 1
    assert saved.headers["ETag"] == '"1"'
    one_id, two_id = world["one_id"], world["two_id"]
    db.session.remove()
    with Session(db.engine) as second:
        row = second.get(output_preferences.ClinicOutputPreference, one_id)
        assert row is not None and row.version == 1
        assert row.open_after_export is True or row.open_after_export == 1
        assert row.user_id == one_id
        assert "clipboard" in row.targets_json and "rtf" in row.targets_json
    with world["flask"].app_context():
        db.engine.dispose()
    restarted = world["make_app"]()
    reloaded, _ = world["client"](db.session.get(User, one_id), restarted)
    assert reloaded.get(URL).json == {"version": 1, "formats": FORMATS, "open_after_export": True}
    other, other_headers = world["client"](db.session.get(User, two_id), restarted)
    assert other.get(URL).json["formats"] == []
    assert _put(other, other_headers, ["txt"], 0).json["formats"] == ["txt"]
    assert reloaded.get(URL).json["formats"] == FORMATS


def test_foreign_user_key_is_rejected_and_stale_if_match_does_not_write(world):
    one, headers = world["client"](world["one"])
    assert _put(one, headers, FORMATS, 0).status_code == 200
    stolen = one.put(URL, headers={**headers, "If-Match": '"1"'}, json={
        "formats": ["docx"], "user_id": world["two"].id})
    assert stolen.status_code == 400
    assert one.get(URL).json["formats"] == FORMATS
    for key in ("actor_id", "user", "role"):
        refused = one.put(URL, headers={**headers, "If-Match": '"1"'}, json={"formats": ["docx"], key: 1})
        assert refused.status_code == 400
    stale = _put(one, headers, ["docx"], 0)
    assert (stale.status_code, stale.json["code"]) == (412, "stale_version")
    assert one.get(URL).json["formats"] == FORMATS
    mismatch = one.put(URL, headers={**headers, "If-Match": '"1"'}, json={
        "formats": ["docx"], "expected_version": 0})
    assert mismatch.status_code == 400
    assert one.get(URL).json["formats"] == FORMATS


def test_missing_if_match_bad_format_auth_and_clinic_mode(world, monkeypatch):
    one, headers = world["client"](world["one"])
    missing = one.put(URL, headers=headers, json={"formats": FORMATS})
    assert (missing.status_code, missing.json["code"]) == (428, "precondition_required")
    assert one.get(URL).json["version"] == 0
    bad = _put(one, headers, ["not-a-format"], 0)
    assert bad.status_code == 400
    assert db.session.get(output_preferences.ClinicOutputPreference, world["one"].id) is None
    assert world["flask"].test_client().get(URL).status_code == 401
    assert one.put(URL, headers={"If-Match": '"0"'}, json={"formats": FORMATS}).status_code == 403
    monkeypatch.delenv("CLINIC_MODE")
    assert one.get(URL).status_code == 404


@pytest.mark.parametrize(
    "tag", ['0', '"0', '"00"', '"²"', f'"{"9" * 4500}"'],
    ids=["unquoted", "partial-quote", "leading-zero", "non-ascii", "too-long"],
)
def test_invalid_if_match_is_rejected_without_writing(world, tag):
    one, headers = world["client"](world["one"])
    invalid = one.put(URL, headers={**headers, "If-Match": tag}, json={"formats": FORMATS})
    assert (invalid.status_code, invalid.json["code"]) == (400, "bad_version")
    assert one.get(URL).json["version"] == 0
