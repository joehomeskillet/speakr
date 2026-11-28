"""PAR-OPS-010 preference API policy tests. No device or lab evidence."""

import json

import pytest
from flask import Flask
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import config
from src.clinic.last_selection import SelectionStore
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import WorkstationPreferences
from src.models import User


@pytest.fixture
def workstation(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-session-transport")
    user = User(id=1, username="transport-test", email="transport@example.test")
    login = LoginManager(app)
    login.user_loader(lambda uid: user if uid == user.get_id() else None)

    @app.post("/test-login")
    def sign_in():
        login_user(user)
        return generate_csrf()

    app.register_blueprint(PreferenceRoutes(
        allowed_for=lambda _uid: None,
        author_pool_for=lambda _uid: None,
        selections=SelectionStore(),
        workstations=WorkstationPreferences(),
    ).blueprint)
    client = app.test_client()
    headers = {"X-CSRF-Token": client.post("/test-login").get_data(as_text=True)}
    issued = client.post(PREFIX + "/workstations", headers=headers)
    assert issued.status_code == 201
    return client, f"{PREFIX}/workstations/{issued.json['workstation_id']}", headers


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    capabilities = json.dumps(config.client_capabilities())
    (tmp_path / config.CLIENT_CAPABILITIES_FILE).write_text(capabilities, encoding="utf-8")
    monkeypatch.setenv("CLINIC_CONFIG_DIR", str(tmp_path))
    return tmp_path / config.DEV_DEFAULTS_FILE


@pytest.mark.parametrize(("policy", "expected"), [
    ({}, "local"),
    ({"transport_mode": "local"}, "local"),
    ({"transport_mode": "rdp"}, "rdp"),
    ({"transport_mode": "ica"}, "ica"),
])
def test_get_reports_server_transport_without_redirection_evidence(workstation, policy_file, policy, expected):
    policy_file.write_text(json.dumps({"web_client": policy}), encoding="utf-8")
    client, url, _ = workstation
    asserted_mode = "ica" if expected == "rdp" else "rdp"
    response = client.get(
        url,
        query_string={"transport_mode": asserted_mode, "device_redirection_verified": "true"},
        headers={"X-Clinic-Transport-Mode": asserted_mode, "X-Device-Redirection-Verified": "true"},
    )
    assert response.status_code == 200
    assert response.json["transport_mode"] == expected
    assert response.json["device_redirection_verified"] is False


@pytest.mark.parametrize(("key", "value"), [("transport_mode", "local"), ("device_redirection_verified", True)])
def test_transport_fields_cannot_be_written_as_preferences(workstation, policy_file, key, value):
    policy_file.write_text('{"web_client":{"transport_mode":"rdp"}}', encoding="utf-8")
    client, url, headers = workstation
    rejected = client.put(url, headers=headers, json={"expected_version": 0, "changes": {key: value}})
    assert (rejected.status_code, rejected.json["code"]) == (403, "capability_forbidden")
    response = client.get(url)
    assert response.json["version"] == 0
    assert response.json["transport_mode"] == "rdp"
    assert response.json["device_redirection_verified"] is False


@pytest.mark.parametrize("invalid_mode", ["remote", [], None])
def test_invalid_transport_policy_fails_closed(workstation, policy_file, invalid_mode):
    policy_file.write_text(json.dumps({"web_client": {"transport_mode": invalid_mode}}), encoding="utf-8")
    client, url, _ = workstation
    rejected = client.get(url)
    assert (rejected.status_code, rejected.json["code"]) == (503, "invalid_transport_policy")
    rejected = client.get(PREFIX + "/workstations")
    assert (rejected.status_code, rejected.json["code"]) == (503, "invalid_transport_policy")


@pytest.mark.parametrize("mode", ["local", "rdp", "ica"])
def test_session_transport_does_not_require_a_workstation_id(workstation, policy_file, mode):
    policy_file.write_text(json.dumps({"web_client": {"transport_mode": mode}}), encoding="utf-8")
    client, _, _ = workstation
    response = client.get(
        PREFIX + "/workstations",
        query_string={"transport_mode": "remote", "device_redirection_verified": "true"},
        headers={"X-Device-Redirection-Verified": "true"},
    )
    assert response.status_code == 200
    assert response.json == {"transport_mode": mode, "device_redirection_verified": False}
    with client.session_transaction() as session:
        session.clear()
    assert client.get(PREFIX + "/workstations").status_code == 401
