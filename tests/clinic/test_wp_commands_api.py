"""Tests for safe command and pronunciation operations (PAR-VOC-007, PAR-VOC-008).

Original behavior tests BT-VOC-007 and BT-VOC-008 remain unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import policy, vocabulary
from src.clinic.asr_capabilities import (
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.clinic.routes_commands import (
    commands_blueprint,
    register_commands_adapters,
)
from src.clinic.voice_command_runtime import catalog_version
from src.database import db

PARITY_PATH = "/api/clinic/v1/parity/commands"
EXECUTE_PATH = "/api/clinic/v1/commands/execute"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
TARGET = AsrTarget(
    base_url="http://127.0.0.1:9000",
    engine="local-engine",
    engine_revision="e1",
    model="local-model",
    model_revision="m1",
    parameters_json='{"language":"de"}',
)


class ActorUser(UserMixin):
    def __init__(self, user_id: int = 7):
        self.id = user_id

    def get_id(self):
        return str(self.id)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    flask_app = Flask(__name__)
    flask_app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-commands-key",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'commands_test.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        WTF_CSRF_ENABLED=True,
    )
    db.init_app(flask_app)
    login_mgr = LoginManager(flask_app)
    login_mgr.user_loader(lambda uid: ActorUser(int(uid)))
    login_mgr.request_loader(lambda req: ActorUser(int(req.headers["X-Test-User"])) if "X-Test-User" in req.headers else None)
    CSRFProtect(flask_app)

    with flask_app.app_context():
        vocabulary.create_tables()

    @flask_app.get("/test-login")
    def do_login():
        uid = int(flask_app.test_request_context().request.args.get("user_id", 7))
        login_user(ActorUser(uid))
        return jsonify(csrf=generate_csrf(), user_id=uid)

    receipts = {}

    def run_once(scope, digest, operation):
        if scope in receipts:
            saved_digest, result = receipts[scope]
            if saved_digest != digest:
                raise CommandProblem("IDEMPOTENCY_CONFLICT")
            return result
        result = operation()
        receipts[scope] = digest, result
        return result

    dispatch = ParityDispatch(run_once)
    register_commands_adapters(dispatch)
    flask_app.register_blueprint(dispatch.blueprint)
    flask_app.register_blueprint(commands_blueprint)

    flask_app.extensions["parity_dispatch"] = dispatch
    return flask_app


@pytest.fixture
def client_session(app):
    client = app.test_client()
    resp = client.get("/test-login?user_id=7")
    token = resp.json["csrf"]
    headers = {"X-CSRF-Token": token, "Idempotency-Key": "test-key-12345"}
    return client, headers, 7


def test_commands_resource_positive_etag_and_precedence(client_session, app):
    client, headers, user_id = client_session
    with app.app_context():
        vocabulary.add_voice_command(
            "pool", "Befund", action_type="insert_text", action_payload="Pool-Befund",
            pool_id="pool-1",
        )
        vocabulary.add_voice_command(
            "user", "Befund", action_type="insert_text", action_payload="Mein-Befund",
            user_id=user_id,
        )
        vocabulary.add_voice_command(
            "user", "Formatieren", action_type="app_command", action_payload="focus_editor",
            user_id=user_id,
        )
        db.session.commit()

    resp = client.get("/resources/commands?pool_id=pool-1")
    assert resp.status_code == 200
    data = resp.json
    assert data["revision"] == 1
    assert "items" in data
    phrases = {item["trigger_phrase"]: item for item in data["items"]}
    assert "Befund" in phrases
    assert "Formatieren" in phrases
    etag = resp.headers.get("ETag")
    assert etag is not None

    cached = client.get("/resources/commands?pool_id=pool-1", headers={"If-None-Match": etag})
    assert cached.status_code == 304


def test_commands_resource_unauthorized_and_clinic_off(app, monkeypatch):
    client = app.test_client()
    unauth = client.get("/resources/commands")
    assert unauth.status_code == 401
    assert unauth.json["code"] == "UNAUTHORIZED"

    monkeypatch.delenv("CLINIC_MODE")
    off = client.get("/resources/commands")
    assert off.status_code == 404


def test_pronunciations_resource_and_local_edit_before_provider_proven(client_session, app):
    client, headers, user_id = client_session
    with app.app_context():
        vocabulary.add_pronunciation_hint(
            "user", "EKG", phonetic_hint="ee-kay-gee", sounds_like="Eee Kah Geh",
            user_id=user_id,
        )
        db.session.commit()

    resp = client.get("/resources/pronunciations")
    assert resp.status_code == 200
    items = resp.json["items"]
    assert any(h["written_form"] == "EKG" and h["phonetic_hint"] == "ee-kay-gee" for h in items)

    foreign = client.get(f"/pronunciations/users/{user_id + 1}/hints")
    assert foreign.status_code == 403

    save_payload = {
        "command": "save_pronunciation_hint",
        "request_id": str(uuid4()),
        "expected_revision_token": "rev-0",
        "payload": {
            "scope_type": "user",
            "scope_id": str(user_id),
            "written_form": "Hypertonie",
            "phonetic_hint": "hy-per-to-nee",
            "sounds_like": "Bluthochdruck",
        },
    }
    saved = client.post("/pronunciations/commands", headers=headers, json=save_payload)
    assert saved.status_code == 200
    assert saved.json["status"] == "committed"

    with app.app_context():
        hints = vocabulary.get_effective_pronunciation_hints(user_id)
        assert "Hypertonie" in hints


def test_pronunciation_capabilities_explicitly_reported(client_session, app):
    client, _, _ = client_session
    resp = client.get("/api/clinic/v1/pronunciations/capabilities")
    assert resp.status_code == 200
    assert resp.json["training_proven"] is False
    assert resp.json["capability"] == "CAPABILITY_UNAVAILABLE"
    assert resp.json["adaptation_state"] == "capability_unavailable"

    evidence = CapabilityEvidence(
        AsrCapability.PRONUNCIATION,
        TARGET,
        "test-set-1",
        "evidence:123",
        "runtime_passed",
    )
    app.extensions["clinic_capability_registry"] = AsrCapabilityRegistry([evidence])

    active = client.get("/api/clinic/v1/pronunciations/capabilities?engine=local-engine&model=local-model")
    assert active.status_code == 200
    assert active.json["training_proven"] is True
    assert active.json["capability"] == "runtime_passed"


def test_parity_dispatch_save_command_and_stale_revision(client_session, app):
    client, headers, user_id = client_session
    body = {
        "command": "save_command",
        "request_id": str(uuid4()),
        "expected_revision": 0,
        "payload": {
            "scope_type": "user",
            "scope_id": str(user_id),
            "phrase": "Abschluss",
            "action_ids": ["focus_editor"],
        },
    }
    first = client.post(PARITY_PATH, headers=headers, json=body)
    assert first.status_code == 200
    assert first.json["status"] == "committed"
    assert first.json["revision"] == 1

    replay = client.post(PARITY_PATH, headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json == first.json

    stale_body = dict(body, request_id=str(uuid4()), expected_revision=0)
    conflict_headers = dict(headers, **{"Idempotency-Key": "new-key-67890"})
    stale = client.post(PARITY_PATH, headers=conflict_headers, json=stale_body)
    assert stale.status_code == 409
    assert stale.json["code"] == "REVISION_CONFLICT"

    foreign_body = dict(body, request_id=str(uuid4()), expected_revision=0)
    foreign_body["payload"] = dict(body["payload"], scope_id=str(user_id + 1), phrase="Anderer")
    denied = client.post(PARITY_PATH, headers=conflict_headers, json=foreign_body)
    assert denied.status_code == 403
    assert denied.json["code"] == "POLICY_DENIED"


def test_parity_dispatch_request_adaptation_pronunciation(client_session, app):
    client, headers, _ = client_session
    body = {
        "command": "request_adaptation",
        "request_id": str(uuid4()),
        "expected_revision": 0,
        "payload": {
            "profile_revision_id": str(uuid4()),
            "corpus_manifest_id": str(uuid4()),
            "capability": "pronunciation_training",
        },
    }
    unavail = client.post(PARITY_PATH, headers=headers, json=body)
    assert unavail.status_code == 422
    assert unavail.json["code"] == "CAPABILITY_UNAVAILABLE"

    target_obj = AsrTarget(
        base_url="http://127.0.0.1:9000",
        engine="local-engine",
        engine_revision="e1",
        model="local-model",
        model_revision="m1",
        parameters_json='{"language":"de"}',
    )
    evidence = CapabilityEvidence(
        AsrCapability.TRAINING,
        target_obj,
        "test-set-adapt",
        "evidence:adapt-1",
        "runtime_passed",
    )
    app.extensions["clinic_capability_registry"] = AsrCapabilityRegistry([evidence])

    retry_headers = dict(headers, **{"Idempotency-Key": "adapt-key-9999"})
    body["request_id"] = str(uuid4())
    passed = client.post(PARITY_PATH, headers=retry_headers, json=body)
    assert passed.status_code == 200
    assert passed.json["status"] == "queued"


def test_declarative_web_command_execute_positive_and_hooks(client_session, app):
    client, headers, user_id = client_session
    with app.app_context():
        vocabulary.add_voice_command(
            "user", "Textbaustein", action_type="insert_text", action_payload="Patient wohlauf",
            user_id=user_id,
        )
        vocabulary.add_voice_command(
            "user", "Komplex", action_type="macro", action_payload=json.dumps(["Textbaustein"]),
            before_hook="focus_editor", after_hook="insert_marker", user_id=user_id,
        )
        db.session.commit()
        ver = catalog_version(user_id)

    body = {
        "phrase": "Komplex",
        "recording_id": 1,
        "expected_version": ver,
    }
    resp = client.post(EXECUTE_PATH, headers=headers, json=body)
    assert resp.status_code == 200
    plan = resp.json
    assert len(plan["actions"]) == 3
    assert plan["actions"][0] == {"kind": "app_command", "value": "focus_editor"}
    assert plan["actions"][1] == {"kind": "insert_text", "value": "Patient wohlauf"}
    assert plan["actions"][2] == {"kind": "app_command", "value": "insert_marker"}


def test_declarative_web_command_sensitive_confirmation(client_session, app):
    client, headers, user_id = client_session
    policy.register_grant_provider(lambda uid: (policy.Grant("own", frozenset({"export", "release"})),))
    try:
        with app.app_context():
            cmd = vocabulary.add_voice_command(
                "user", "Senden", action_type="app_command", action_payload="send_dictation",
                user_id=user_id,
            )
            db.session.commit()
            ver = catalog_version(user_id)
            cmd_id = cmd.id

        body = {
            "phrase": "Senden",
            "recording_id": 1,
            "expected_version": ver,
        }
        unconfirmed = client.post(EXECUTE_PATH, headers=headers, json=body)
        assert unconfirmed.status_code == 409
        assert unconfirmed.json["code"] == "confirmation_required"

        req_id = str(uuid4())
        confirmed_body = dict(body, request_id=req_id, confirmations={
            "send_dictation": {
                "request_id": req_id,
                "command_id": cmd_id,
                "catalog_version": ver,
                "confirmed_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
            }
        })
        confirmed = client.post(EXECUTE_PATH, headers=headers, json=confirmed_body)
        assert confirmed.status_code == 200
        assert confirmed.json["actions"] == [{"kind": "app_command", "value": "send_dictation"}]
    finally:
        policy.clear_providers()


def test_declarative_web_command_rejects_recursive_and_unsafe_macros(client_session, app):
    client, headers, user_id = client_session
    with app.app_context():
        vocabulary.add_voice_command(
            "user", "SchleifeA", action_type="macro", action_payload='["SchleifeB"]',
            user_id=user_id,
        )
        vocabulary.add_voice_command(
            "user", "SchleifeB", action_type="macro", action_payload='["SchleifeA"]',
            user_id=user_id,
        )
        vocabulary.add_voice_command(
            "user", "Unsicher", action_type="eval", action_payload="1+1",
            user_id=user_id,
        )
        db.session.commit()
        ver = catalog_version(user_id)

    cycle_resp = client.post(EXECUTE_PATH, headers=headers, json={
        "phrase": "SchleifeA",
        "recording_id": 1,
        "expected_version": ver,
    })
    assert cycle_resp.status_code == 400
    assert cycle_resp.json["code"] == "macro_cycle"

    unsafe_resp = client.post(EXECUTE_PATH, headers=headers, json={
        "phrase": "Unsicher",
        "recording_id": 1,
        "expected_version": ver,
    })
    assert unsafe_resp.status_code == 403
    assert unsafe_resp.json["code"] == "POLICY_DENIED"

    stale_resp = client.post(EXECUTE_PATH, headers=headers, json={
        "phrase": "SchleifeA",
        "recording_id": 1,
        "expected_version": "stale-version-token",
    })
    assert stale_resp.status_code == 409
    assert stale_resp.json["code"] == "REVISION_CONFLICT"

    lease_resp = client.post(EXECUTE_PATH, headers=headers, json={
        "phrase": "SchleifeA",
        "recording_id": 1,
        "expected_version": ver,
        "lease_held": False,
    })
    assert lease_resp.status_code in (400, 409)


def test_declarative_web_command_execute_unauthorized_and_csrf(client_session, app):
    client, headers, user_id = client_session
    body = {
        "phrase": "Irgendwas",
        "recording_id": 1,
        "expected_version": "v1",
    }
    no_csrf = client.post(EXECUTE_PATH, json=body)
    assert no_csrf.status_code == 403

    unauth_client = app.test_client()
    unauth_resp = unauth_client.post(EXECUTE_PATH, headers=headers, json=body)
    assert unauth_resp.status_code in (401, 403)
