"""P-208 synthetic directory comparison. Real tenant evidence stays not_run."""

import json

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app, register_clinic_identity
from src.clinic.routes_directory import PREFIX
from src.database import db
from src.models import User

from test_pools import pool_db  # noqa: F401
from test_wp_directory_api import world as directory_world  # noqa: F401


@pytest.fixture
def registered_client(directory_world, monkeypatch):
    def make_client(provider=None, snapshot_file=None):
        flask_app = Flask(__name__)
        flask_app.config.update(TESTING=True, SECRET_KEY="synthetic",
                                SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"])
        db.init_app(flask_app)
        login = LoginManager(flask_app)
        login.user_loader(lambda uid: db.session.get(User, int(uid)))
        CSRFProtect(flask_app)
        flask_app.add_url_rule("/csrf", view_func=generate_csrf)
        flask_app.extensions["clinic_parity_dispatch"] = object()
        with monkeypatch.context() as env:
            env.delenv("CLINIC_DIRECTORY_PROVIDER", raising=False)
            env.delenv("CLINIC_DIRECTORY_SNAPSHOT_FILE", raising=False)
            if provider is not None:
                env.setenv("CLINIC_DIRECTORY_PROVIDER", provider)
            if snapshot_file is not None:
                env.setenv("CLINIC_DIRECTORY_SNAPSHOT_FILE", str(snapshot_file))
            register_clinic_identity(flask_app)

        client = flask_app.test_client()
        with client.session_transaction() as session:
            session.update(_user_id=str(directory_world["admin"].id), _fresh=True,
                           clinic_provider="local", clinic_security_version=0)
        return client, {"X-CSRF-Token": client.get("/csrf").text}

    return make_client


@pytest.mark.parametrize("provider", [None, "synthetic-v2", "synthetic-v1"])
def test_unconfigured_provider_is_unavailable(registered_client, tmp_path, provider):
    # The old test injection must not supply a provider to an app wired at startup.
    path = tmp_path / "directory.json"
    path.write_text('{"version":"fixture-r1","entries":[],"memberships":[]}', encoding="utf-8")
    client, headers = registered_client(provider, None if provider == "synthetic-v1" else path)
    cap = client.get(PREFIX + "/capability")
    assert cap.status_code == 200 and cap.json["directory_available"] is False
    response = client.post(PREFIX + "/preview", headers=headers, json={
        "provider": "synthetic-v1", "snapshot_file": str(path),
    })
    assert (response.status_code, response.json["code"]) == (503, "ldap_capability_unavailable")


def test_preview_uses_the_versioned_server_fixture_without_writes(registered_client, directory_world, tmp_path):
    world = directory_world
    subject = "oid-a-" + world["tag"]
    document = {"version": "fixture-r1", "entries": [
        {"object_id": subject, "account": "renamed-account", "display_name": "Fixture Name", "enabled": True},
        {"object_id": "new-subject", "account": world["plain"].username,
         "display_name": world["plain"].name, "enabled": True},
    ], "memberships": [{"subject_id": subject, "pool_kind": "author", "pool_id": world["pool"].id}]}
    path = tmp_path / "directory.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    client, headers = registered_client("synthetic-v1", path)
    assert client.get(PREFIX + "/capability").json["directory_available"] is True
    body = {"managed_pools": [{"kind": "author", "id": world["pool"].id}]}
    preview = client.post(PREFIX + "/preview", headers=headers, json=body)
    assert preview.status_code == 200
    assert (preview.json["source"], preview.json["version"]) == ("synthetic", "fixture-r1")
    assert {(a["kind"], a["object_id"], a["local_user_id"]) for a in preview.json["actions"]} == {
        ("update_display_name", subject, world["renamed"].id), ("create_pending_user", "new-subject", None),
    }
    assert [change["kind"] for change in preview.json["membership_changes"]] == ["add"]

    # Cancel makes no apply request. The next preview still sees both proposed actions.
    assert client.post(PREFIX + "/preview", headers=headers, json=body).json == preview.json
    assert db.session.get(User, world["renamed"].id).name == "Alter Name"
    assert json.loads(path.read_text(encoding="utf-8")) == document

    document["version"] = "fixture-r2"
    path.write_text(json.dumps(document), encoding="utf-8")
    fresh = client.post(PREFIX + "/preview", headers=headers, json=body)
    assert (fresh.status_code, fresh.json["version"]) == (200, "fixture-r2")
    assert fresh.json["hash"] != preview.json["hash"]

    document["entries"][0]["object_id"] = ""
    path.write_text(json.dumps(document), encoding="utf-8")
    assert client.get(PREFIX + "/capability").json["directory_available"] is False
    unavailable = client.post(PREFIX + "/preview", headers=headers, json=body)
    assert (unavailable.status_code, unavailable.json["code"]) == (503, "ldap_capability_unavailable")
