import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import register_clinic_identity
from src.clinic import notification_preferences as prefs
from src.clinic.route_policy import AUTH, _build
from src.clinic.routes_preferences import PREFIX
from src.database import db
from src.models import User

URL = PREFIX + "/notifications"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic",
                      SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'prefs.db'}")
    db.init_app(app)
    login = LoginManager(app)
    CSRFProtect(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    app.extensions["clinic_parity_dispatch"] = object()
    register_clinic_identity(app)
    with app.app_context():
        User.__table__.create(db.engine)
        prefs.create_tables()
        db.session.add_all(User(id=uid, username=f"user{uid}", email=f"user{uid}@example.invalid",
                                password="unused") for uid in (1, 2))
        db.session.commit()

    @app.get("/login/<int:uid>")
    def login_for_test(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    def sign_in(uid):
        client = app.test_client()
        return client, {"X-CSRF-Token": client.get(f"/login/{uid}").json["csrf"]}

    return app, sign_in


def test_choices_survive_sign_in_and_are_isolated(world):
    _, sign_in = world
    client, headers = sign_in(1)
    defaults = {"version": 0, "channels": {"in_app": {event: True for event in prefs.EVENTS}}}
    initial = client.get(URL)
    assert (initial.status_code, initial.json) == (200, defaults)
    first = client.put(URL, headers=headers, json={
        "event": "acceptance_requested", "enabled": False, "expected_version": 0,
    })
    assert (first.status_code, first.json["version"]) == (200, 1)
    second = client.put(URL, headers=headers, json={
        "event": "completed", "enabled": False, "expected_version": 1,
    })
    assert (second.status_code, second.json["channels"]["in_app"]["completed"]) == (200, False)
    third = client.put(URL, headers=headers, json={
        "event": "completed", "enabled": True, "expected_version": 2, "channel": "in_app",
    })
    events = {**defaults["channels"]["in_app"], "acceptance_requested": False}
    expected = {"version": 3, "channels": {"in_app": events}}
    assert (third.status_code, third.json) == (200, expected)
    assert sign_in(1)[0].get(URL).json == expected
    outsider, outsider_headers = sign_in(2)
    assert outsider.get(URL).json == defaults
    assert outsider.get(URL, query_string={"user_id": 1}).status_code == 400
    assert outsider.get(URL, json={"user_id": 1}).status_code == 400
    assert outsider.put(URL, headers=outsider_headers, json={
        "user_id": 1, "event": "acceptance_requested", "enabled": True, "expected_version": 2,
    }).status_code == 400
    assert sign_in(1)[0].get(URL).json == expected
    assert outsider.get(URL).json == defaults


def test_auth_csrf_validation_and_stale_version(world, monkeypatch):
    app, sign_in = world
    assert _build()["clinic_preferences.notifications"].kind == AUTH
    assert app.test_client().get(URL).status_code == 401
    client, headers = sign_in(1)
    data = {"event": "completed", "enabled": False, "expected_version": 0}
    assert client.put(URL, json=data).status_code == 403
    for overrides, code in (({"channel": "smtp"}, "unknown_channel"),
                            ({"event": "unknown"}, "unknown_event"),
                            ({"enabled": "false"}, "bad_value")):
        result = client.put(URL, headers=headers, json={**data, **overrides})
        assert (result.status_code, result.json["code"]) == (400, code)
    assert client.put(URL, json=data, headers=headers).status_code == 200
    stale = client.put(URL, json={**data, "enabled": True}, headers=headers)
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (
        409, "stale_version", 1,
    )
    assert client.get(URL).json["channels"]["in_app"]["completed"] is False
    monkeypatch.delenv("CLINIC_MODE")
    assert client.get(URL).status_code == 404
