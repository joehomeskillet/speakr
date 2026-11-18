"""Voice consent routes (OWN-SPK-001, R-034), HTTP slice over the real consent service.

Covers positive, unauthorized, stale-version and retry/failure cases. Synthetic
users and one-float embeddings only.
"""

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import voice_consent as vc
from src.clinic.routes_consent import PREFIX, blueprint
from src.database import db
from src.models import User

from test_pools import _user, pool_db as pool_db

GRANT, REVOKE = PREFIX + "/grant", PREFIX + "/revoke"
STATEMENT = "Ich willige in mein eigenes Stimmprofil ein."


@pytest.fixture
def world(monkeypatch, pool_db):
    monkeypatch.setenv("CLINIC_MODE", "1")
    vc.create_tables()
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
        c = flask.test_client()
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"], "Idempotency-Key": "key-12345678"}

    with flask.app_context():
        yield dict(client=client, flask=flask, user=_user(), other=_user(), admin=_user(admin=True))


def test_grant_revoke_round_trip_with_history_and_no_vector_bytes(world):
    c, h = world["client"](world["user"])
    assert c.get(PREFIX).json["state"] == "none"
    granted = c.post(GRANT, headers=h, json={"expected_consent_id": 0, "statement_text": STATEMENT})
    assert granted.status_code == 201
    assert (granted.json["state"], granted.json["embedding_count"]) == ("granted", 0)
    vc.enrol(world["user"].id, vc.EmbeddingRef(vector=b"\x00\x00\x00\x3f", dimension=1))
    revoked = c.post(REVOKE, headers=h, json={"expected_consent_id": granted.json["latest_consent_id"]})
    assert revoked.status_code == 200
    assert (revoked.json["state"], revoked.json["embedding_count"]) == ("revoked", 0)
    view = c.get(PREFIX).json
    assert [r["status"] for r in view["history"]] == ["granted", "revoked"]
    assert view["history"][0]["statement_hash"] and STATEMENT not in str(view)
    assert "embedding" not in view and "vector" not in str(view)


@pytest.mark.parametrize("key", ["user_id", "actor_user_id", "patient_id", "role", "is_admin"])
def test_identity_fields_in_body_are_rejected(world, key):
    c, h = world["client"](world["admin"])
    body = {"expected_consent_id": 0, "statement_text": STATEMENT, key: world["other"].id}
    denied = c.post(GRANT, headers=h, json=body)
    assert (denied.status_code, denied.json["code"]) == (403, "forbidden_field")
    assert vc.consent_state(world["other"].id).state == "none"


def test_admin_consents_only_for_own_id(world):
    c, h = world["client"](world["admin"])
    assert c.post(GRANT, headers=h, json={"expected_consent_id": 0, "statement_text": STATEMENT}).status_code == 201
    assert vc.consent_state(world["admin"].id).state == "granted"
    assert vc.consent_state(world["other"].id).state == "none"


def test_unauthenticated_missing_csrf_and_missing_idempotency_key(world):
    anonymous = world["flask"].test_client()
    c, h = world["client"](world["user"])
    body = {"expected_consent_id": 0, "statement_text": STATEMENT}
    no_csrf = c.post(GRANT, headers={"Idempotency-Key": h["Idempotency-Key"]}, json=body)
    assert (no_csrf.status_code, no_csrf.json["code"]) == (403, "unauthorized")
    no_key = c.post(GRANT, headers={"X-CSRF-Token": h["X-CSRF-Token"]}, json=body)
    assert (no_key.status_code, no_key.json["code"]) == (400, "idempotency_key_required")
    assert vc.consent_state(world["user"].id).state == "none"
    for got in (anonymous.get(PREFIX),):
        assert (got.status_code, got.json["code"]) == (401, "unauthenticated")


def test_clinic_mode_off_hides_routes(world, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE")
    c, h = world["client"](world["user"])
    assert c.get(PREFIX).status_code == 404
    assert c.post(GRANT, headers=h, json={"expected_consent_id": 0, "statement_text": STATEMENT}).status_code == 404


def test_stale_version_duplicate_and_validation(world):
    c, h = world["client"](world["user"])
    body = {"expected_consent_id": 0, "statement_text": STATEMENT}
    first = c.post(GRANT, headers=h, json=body)
    latest = first.json["latest_consent_id"]
    # Same version again is an identical retry, not a conflict.
    assert c.post(GRANT, headers=h, json=body).status_code == 201
    assert len(c.get(PREFIX).json["history"]) == 1
    # A different statement against the stale version conflicts.
    other = c.post(GRANT, headers=h, json={"expected_consent_id": 0, "statement_text": "anders"})
    assert (other.status_code, other.json["code"]) == (409, "stale_consent")
    # Current version but already granted.
    dup = c.post(GRANT, headers=h, json={"expected_consent_id": latest, "statement_text": STATEMENT})
    assert (dup.status_code, dup.json["code"]) == (409, "already_granted")
    stale = c.post(REVOKE, headers=h, json={"expected_consent_id": latest + 5})
    assert (stale.status_code, stale.json["code"]) == (409, "stale_consent")
    for payload, code in (
        ({"expected_consent_id": 0, "statement_text": "  "}, "statement_required"),
        ({"expected_consent_id": 0, "statement_text": "x" * 20001}, "statement_too_long"),
        ({"expected_consent_id": -1, "statement_text": STATEMENT}, "invalid_payload"),
        ({"expected_consent_id": True, "statement_text": STATEMENT}, "invalid_payload"),
        ({"expected_consent_id": 0}, "invalid_payload"),
        ({"expected_consent_id": 0, "statement_text": STATEMENT, "extra": 1}, "invalid_payload"),
    ):
        got = c.post(GRANT, headers=h, json=payload)
        assert (got.status_code, got.json["code"]) == (422, code)


def test_revoke_retry_is_idempotent_and_revoke_without_consent_fails(world):
    c, h = world["client"](world["user"])
    none = c.post(REVOKE, headers=h, json={"expected_consent_id": 1})
    assert none.status_code == 409
    latest = c.post(GRANT, headers=h, json={"expected_consent_id": 0, "statement_text": STATEMENT}).json[
        "latest_consent_id"]
    assert c.post(REVOKE, headers=h, json={"expected_consent_id": latest}).status_code == 200
    again = c.post(REVOKE, headers=h, json={"expected_consent_id": latest})
    assert (again.status_code, again.json["state"]) == (200, "revoked")
    assert [r["status"] for r in c.get(PREFIX).json["history"]] == ["granted", "revoked"]
    assert c.post(REVOKE, headers=h, json={"expected_consent_id": 0}).status_code == 422


def test_service_failure_maps_to_problem(world, monkeypatch):
    def boom(**_kwargs):
        raise vc.VoiceConsentError("unsupported_consent_transaction")

    monkeypatch.setattr(vc, "grant_consent", boom)
    c, h = world["client"](world["user"])
    got = c.post(GRANT, headers=h, json={"expected_consent_id": 0, "statement_text": STATEMENT})
    assert (got.status_code, got.json["code"]) == (503, "unsupported_consent_transaction")
    assert got.mimetype == "application/problem+json"
