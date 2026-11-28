"""Dropfolder configuration through the real clinic session API."""

import os
import subprocess
import sys
from uuid import uuid4

import pytest

from src.app import app, db
from src.clinic import import_source_config as sources, policy
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.models import User
from src.services import audit

from test_wp_v8_import_routes import _client


URL = "/api/clinic/v1/imports/sources"
ROOT = "/srv/dropfolders"
CLINIC_AT_STARTUP = clinic_mode_enabled()
clinic_routes = pytest.mark.skipif(not CLINIC_AT_STARTUP, reason="clinic app registered in the subprocess")


@pytest.mark.skipif(CLINIC_AT_STARTUP, reason="requires stock app startup")
def test_stock_app_keeps_dropfolder_routes_disabled():
    stock = app.test_client()
    assert stock.post(URL, json={}).status_code == 404
    assert stock.delete(URL).status_code == 404
    assert stock.delete(f"{URL}/{uuid4()}").status_code == 404
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-q", "-p", "no:cacheprovider", "--timeout=120"],
        env={**os.environ, "CLINIC_MODE": "1", "PYTEST_DATABASE_URI": ""},
        capture_output=True, text=True, timeout=110, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def client_for(monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", ROOT)

    def make(role):
        name = uuid4().hex[:16]
        with app.app_context():
            user = User(username=name, email=f"{name}@example.invalid", password="x", is_admin=role == "admin")
            db.session.add(user)
            db.session.flush()
            db.session.add(ClinicAccountPolicy(user_id=user.id, status="active"))
            db.session.commit()
            uid = user.id
            client = _client(user)
        if role == "manager":
            policy.register_grant_provider(
                lambda actor: [policy.Grant("own", frozenset({"manage_import_sources"}))] if actor == uid else [],
            )
        csrf = client.get("/api/csrf-token").json["csrf_token"]
        return client, {"X-CSRF-Token": csrf}, uid

    return make


def _payload(uid):
    return dict(source_kind="folder", source_ref="incoming", approved_root=f"{ROOT}/in",
                target_author_user_id=uid, file_pattern="*.ds2", priority=128, language_id=1031,
                asr_profile_id="profile-one")


@clinic_routes
@pytest.mark.parametrize("role", ["admin", "manager"])
def test_dropfolder_round_trip(role, client_for):
    client, headers, uid = client_for(role)
    payload = _payload(uid)
    created = client.post(URL, headers=headers, json=payload)
    assert created.status_code == 201
    item = created.json
    assert {key: item[key] for key in payload} == payload
    assert item["policy_version"] == 1
    assert client.get(URL).json["items"] == [item]
    assert client.post(URL, headers=headers, json=payload).status_code == 409

    endpoint = f"{URL}/{item['id']}"
    changes = dict(approved_root=f"{ROOT}/next", file_pattern="*.wav", priority=0, language_id=1033,
                   asr_profile_id=None)
    updated = client.put(endpoint, headers={**headers, "Idempotency-Key": str(uuid4())},
                         json={**changes, "expected_version": 1})
    assert updated.status_code == 200
    assert {key: updated.json[key] for key in changes} == changes
    assert updated.json["policy_version"] == 2
    assert client.get(URL).json["items"] == [updated.json]

    if role == "admin":
        removed = client.delete(endpoint, headers=headers)
    else:
        removed = client.delete(URL, json={"config_id": item["id"]}, headers=headers)
    assert removed.status_code == 204
    assert client.get(URL).json["items"] == []
    assert client.delete(endpoint, headers=headers).status_code == 404


@clinic_routes
def test_dropfolder_root_must_still_be_approved(client_for, monkeypatch):
    client, headers, uid = client_for("admin")
    payload = _payload(uid)
    created = client.post(URL, headers=headers, json=payload)
    assert created.status_code == 201
    item = created.json
    endpoint = f"{URL}/{item['id']}"
    put_headers = {**headers, "Idempotency-Key": str(uuid4())}
    for root, status in ((f"{ROOT}-other/in", 403), (f"{ROOT}/in/../out", 400), ("relative", 400)):
        assert client.post(URL, headers=headers, json={**payload, "approved_root": root}).status_code == status
        assert client.put(endpoint, headers=put_headers, json={
            "expected_version": 1, "approved_root": root,
        }).status_code == status
    assert client.get(URL).json["items"] == [item]

    first_update = {"expected_version": 1, "file_pattern": "*.wav"}
    assert client.put(endpoint, headers=put_headers, json=first_update).status_code == 200
    next_root = f"{ROOT}/next"
    assert client.put(endpoint, headers={**headers, "Idempotency-Key": str(uuid4())}, json={
        "expected_version": 2, "approved_root": next_root,
    }).status_code == 200
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", next_root)
    assert client.get(URL).json["items"][0]["approved_root"] == next_root
    assert client.put(endpoint, headers=put_headers, json=first_update).status_code == 403
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/revoked")
    assert client.get(URL).json["items"] == []
    assert client.delete(endpoint, headers=headers).status_code == 403


@clinic_routes
def test_dropfolder_requires_grant_csrf_and_ownership(client_for):
    owner, headers, uid = client_for("manager")
    payload = _payload(uid)
    assert app.test_client().post(URL, json=payload).status_code == 401
    assert owner.post(URL, json=payload).status_code == 400
    member, member_headers, member_id = client_for("member")
    assert member.post(URL, headers=member_headers, json=_payload(member_id)).status_code == 403
    admin, admin_headers, admin_id = client_for("admin")
    assert owner.post(URL, headers=headers, json=_payload(admin_id)).status_code == 403
    assert owner.post(URL, headers=headers, json={**payload, "allow_source_delete": True}).status_code == 400
    with app.app_context():
        device = sources.create_source_config(
            actor_user_id=uid, source_kind="device", source_ref="test-recorder", approved_root=ROOT,
            target_author_user_id=uid,
        )
    endpoint = f"{URL}/{owner.post(URL, headers=headers, json=payload).json['id']}"
    assert "approved_root" not in next(item for item in owner.get(URL).json["items"] if item["id"] == device["id"])
    assert owner.delete(f"{URL}/{device['id']}", headers=headers).status_code == 400
    assert owner.delete(endpoint).status_code == 400
    assert member.delete(endpoint, headers=member_headers).status_code == 403
    assert admin.delete(endpoint, headers=admin_headers).status_code == 404


@clinic_routes
def test_audit_failure_rolls_back_create_and_delete(client_for, monkeypatch):
    client, headers, uid = client_for("admin")
    payload = _payload(uid)
    with monkeypatch.context() as patch:
        patch.setattr(audit, "record_in_tx", lambda *args, **kwargs: None)
        assert client.post(URL, headers=headers, json=payload).status_code == 503
    assert client.get(URL).json["items"] == []

    created = client.post(URL, headers=headers, json=payload)
    assert created.status_code == 201
    with monkeypatch.context() as patch:
        patch.setattr(audit, "record_in_tx", lambda *args, **kwargs: None)
        assert client.delete(f"{URL}/{created.json['id']}", headers=headers).status_code == 503
    assert client.get(URL).json["items"] == [created.json]


@clinic_routes
def test_empty_sources_list_still_returns_authors_and_pools(client_for):
    from src.clinic import pools

    client, _headers, uid = client_for("admin")
    with app.app_context():
        pool = pools.create_author_pool("Dropfolder Empty Pool", actor_id=uid)
        pools.add_author_member(pool.id, uid, actor_id=uid)
        pool_id = pool.id
    body = client.get(URL).json
    assert body["items"] == []
    assert any(a["id"] == uid for a in body["authors"])
    assert any(p["id"] == pool_id and p["author_id"] == uid for p in body["pools"])
