"""Synthetic HTTP and database checks for the site-admin agent registry."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import generate_csrf

from src.app import app as speakr_app
from src.clinic import agent_channel as ch
from src.clinic import routes_agent
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.ingest import IngestService
from src.database import db
from src.models import AuditEvent, User
from src.services import audit

URL = "/api/clinic/v1/admin/agents"
FINGERPRINT = "ab" * 32
MASTER = b"synthetic-agent-admin-test-key!!"
PROXY = "synthetic-agent-admin-proxy-secret"
PAYLOAD = {"display_name": "Lab station", "allowed_sources": ["usb-1"],
           "credential_fingerprint": FINGERPRINT}


@pytest.fixture
def registry(tmp_path, clinic_on):
    from src.clinic.routes_agent_admin import create_blueprint

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-agent-admin-session",
                      SQLALCHEMY_DATABASE_URI=speakr_app.config["SQLALCHEMY_DATABASE_URI"],
                      SQLALCHEMY_ENGINE_OPTIONS=speakr_app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}))
    db.init_app(app)
    login = LoginManager(app)

    @login.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id.split(":")[0]))

    @app.get("/csrf")
    def csrf():
        return {"token": generate_csrf()}

    app.register_blueprint(create_blueprint(MASTER))
    app.register_blueprint(create_blueprint(b""), name="unconfigured", url_prefix="/unconfigured")
    app.register_blueprint(routes_agent.create_agent_blueprint(
        ch.ChannelPolicy(MASTER), IngestService(tmp_path / "originals"), PROXY,
    ))
    with app.app_context():
        users = {}
        suffix = uuid4().hex[:8]
        for name, admin, status in (("admin", True, "active"), ("staff", False, "active"),
                                    ("disabled", True, "disabled"), ("no_policy", True, None)):
            username = f"{name}-{suffix}"
            user = User(username=username, email=f"{username}@example.invalid", password="unused", is_admin=admin)
            db.session.add(user)
            db.session.flush()
            if status is not None:
                db.session.add(ClinicAccountPolicy(user_id=user.id, status=status))
            users[name] = (user.id, user.get_id())
        db.session.commit()

    def client(name=None):
        result = app.test_client()
        if name is not None:
            with result.session_transaction() as session:
                session["_user_id"] = users[name][1]
        return result, {"X-CSRFToken": result.get("/csrf").json["token"]}

    return SimpleNamespace(app=app, client=client, admin_id=users["admin"][0])


def _enroll(client, headers):
    response = client.post(URL, json=PAYLOAD, headers=headers)
    assert response.status_code == 201
    assert response.cache_control.no_store
    return response.json


def test_enroll_toggle_rotate_and_list(registry, monkeypatch):
    client, headers = registry.client("admin")
    enrolled = _enroll(client, headers)
    agent_id = enrolled["agent_id"]
    path = f"{URL}/{agent_id}"
    machine = registry.app.test_client()
    credentials = {routes_agent.FINGERPRINT_HEADER: FINGERPRINT, routes_agent.PROXY_SECRET_HEADER: PROXY}
    machine_path = f"{routes_agent.PREFIX}/intents"
    assert machine.get(URL, headers=credentials).status_code == 401
    assert machine.get(machine_path, headers=credentials).status_code == 200
    with registry.app.app_context():
        assert ch.authenticate_agent(FINGERPRINT).agent_id == agent_id
        assert enrolled["intent_signing_key_hex"] == ch.derive_agent_key(ch.ChannelPolicy(MASTER), agent_id, 1).hex()
    disabled = client.patch(path, json={"enabled": False}, headers=headers)
    assert disabled.status_code == 200
    assert disabled.json["enabled"] is False
    assert machine.get(machine_path, headers=credentials).status_code == 401
    listed = client.get(URL)
    assert listed.status_code == 200
    assert listed.cache_control.no_store
    assert listed.json == {"items": [{"agent_id": agent_id, "display_name": "Lab station",
                                      "allowed_sources": ["usb-1"], "enabled": False}], "count": 1}
    enabled = client.patch(path, json={"enabled": True}, headers=headers)
    assert enabled.status_code == 200
    assert enabled.json["enabled"] is True
    assert machine.get(machine_path, headers=credentials).status_code == 200
    monkeypatch.setattr(audit, "is_enabled", lambda: False)
    rotated = client.post(f"{path}/rotate-key", headers=headers)
    assert rotated.status_code == 200
    assert rotated.cache_control.no_store
    with registry.app.app_context():
        assert ch.authenticate_agent(FINGERPRINT).agent_id == agent_id
        assert rotated.json["intent_signing_key_hex"] == ch.derive_agent_key(ch.ChannelPolicy(MASTER), agent_id, 2).hex()
        event = AuditEvent.query.one()
        assert (event.event, event.actor_user_id, event.details) == (
            "admin.setting.changed", registry.admin_id, {"key": f"clinic_agent:{agent_id}:intent_signing_key"},
        )


@pytest.mark.parametrize("name,status", [(None, 401), ("staff", 403), ("disabled", 403), ("no_policy", 403)])
def test_registry_requires_an_active_site_admin(registry, name, status):
    client, headers = registry.client(name)
    path = f"{URL}/11111111-2222-3333-4444-555555555555"
    assert client.get(URL).status_code == status
    assert client.post(URL, json=PAYLOAD, headers=headers).status_code == status
    assert client.patch(path, json={"enabled": False}, headers=headers).status_code == status
    assert client.post(f"{path}/rotate-key", headers=headers).status_code == status


def test_csrf_validation_and_unconfigured_signing(registry):
    client, headers = registry.client("admin")
    assert client.post(URL, json=PAYLOAD).status_code == 403
    enrolled = _enroll(client, headers)
    path = f"{URL}/{enrolled['agent_id']}"
    assert client.patch(path, json={"enabled": False}).status_code == 403
    assert client.post(f"{path}/rotate-key").status_code == 403
    assert client.post("/unconfigured", json=PAYLOAD, headers=headers).status_code == 503
    assert client.post(f"/unconfigured/{enrolled['agent_id']}/rotate-key", headers=headers).status_code == 503
    with registry.app.app_context():
        row = db.session.get(ch.ClinicAgent, enrolled["agent_id"])
        assert row.enabled is True
        assert row.key_version == 1
        assert AuditEvent.query.count() == 0


def test_invalid_duplicate_and_missing_inputs(registry):
    client, headers = registry.client("admin")
    assert client.post(URL, json={**PAYLOAD, "allowed_sources": "usb-1"}, headers=headers).status_code == 400
    assert client.post(URL, json={**PAYLOAD, "credential_fingerprint": "invalid!"}, headers=headers).status_code == 400
    assert client.post(URL, json={**PAYLOAD, "display_name": "x" * 20000}, headers=headers).status_code == 413
    agent_id = _enroll(client, headers)["agent_id"]
    duplicate = client.post(URL, json={**PAYLOAD, "credential_fingerprint": FINGERPRINT.upper()}, headers=headers)
    assert duplicate.status_code == 409
    assert FINGERPRINT not in duplicate.get_data(as_text=True).lower()
    assert client.patch(f"{URL}/{agent_id}", json={"enabled": "false"}, headers=headers).status_code == 400
    missing = f"{URL}/11111111-2222-3333-4444-555555555555"
    assert client.patch(missing, json={"enabled": False}, headers=headers).status_code == 404
    assert client.post(f"{missing}/rotate-key", headers=headers).status_code == 404
    assert client.get(URL).json["count"] == 1


def test_rotation_rolls_back_when_audit_is_unavailable(registry, monkeypatch):
    client, headers = registry.client("admin")
    agent_id = _enroll(client, headers)["agent_id"]
    monkeypatch.setattr(audit, "record_in_tx", lambda *args, **kwargs: None)
    assert client.post(f"{URL}/{agent_id}/rotate-key", headers=headers).status_code == 503
    with registry.app.app_context():
        assert db.session.get(ch.ClinicAgent, agent_id).key_version == 1
        assert AuditEvent.query.count() == 0
