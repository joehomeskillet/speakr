"""P-104 synthetic boundaries only; tenant evidence remains not_run.

PAR-ADM-009/010, G-LDAP, G-IWA and WG-ADM-009 still require real acceptance.
"""

import socket

import pytest

from src.app import app, db
from src.clinic import directory_diff as dd, entra, entra_link_intent as intents, identity
from src.clinic import routes_directory as directory
from src.clinic.directory_reconcile import DirectoryEntry
from src.models import User

from test_pools import pool_db  # noqa: F401
from test_wp_directory_api import world as directory_world  # noqa: F401
from test_wp_identity_login_wire import login as local_login
from test_wp_identity_providers_api import (
    BASE, OID, PASSWORD, Idp, anonymous, config, counts, key, make_app, mk_user,
    password_hash, state_of, world as identity_world,  # noqa: F401
)


@pytest.fixture(autouse=True)
def synthetic_only(monkeypatch, record_property):
    record_property("evidence", "synthetic")
    record_property("tenant_status", "not_run")
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

    def refuse_network(*_args, **_kwargs):
        pytest.fail("Synthetic identity fixtures must not contact AD or an external provider")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_network)


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "synthetic"])
def test_directory_pending_subject_never_links_by_email(directory_world, enabled, password_hash):
    world = directory_world
    target = world["plain"]
    db.session.get(User, target.id).password = password_hash
    db.session.commit()
    snapshot = dd.DirectorySnapshot("synthetic", "synthetic-v1", (
        DirectoryEntry(OID, target.username, target.name, target.email, True),
    ))
    directory.set_provider((lambda: snapshot) if enabled else None)
    client, headers = world["client"](world["admin"])
    before = counts()
    capability = client.get(directory.PREFIX + "/capability")
    assert capability.status_code == 200
    assert capability.json == {
        "directory_available": enabled, "local_accounts": True,
        "local_windows_accounts": "unavailable", "gap": "WG-ADM-009",
    }
    preview = client.post(directory.PREFIX + "/preview", headers=headers, json={})
    if enabled:
        assert preview.status_code == 200
        result = preview.json
        assert result["source"] == "synthetic"
        pending = [a for a in result["actions"] if a["object_id"] == OID]
        assert len(pending) == 1
        assert pending[0]["kind"] == "create_pending_user"
        assert pending[0]["local_user_id"] is None
        refused = client.post(directory.PREFIX + "/apply", headers=headers, json={
            "version": result["version"], "hash": result["hash"], "actions": pending,
        })
        assert (refused.status_code, refused.json["code"]) == (422, "explicit_link_required")
    else:
        assert (preview.status_code, preview.json["code"]) == (503, "ldap_capability_unavailable")
    assert counts() == before
    assert snapshot.entries == (DirectoryEntry(OID, target.username, target.name, target.email, True),)
    assert db.session.get(identity.ClinicAccountPolicy, target.id).login_policy == "local_only"
    local = app.test_client()
    assert local_login(local, target.email, PASSWORD).status_code == 302
    with local.session_transaction() as session:
        assert session["_user_id"] == str(target.id)
        assert session["clinic_provider"] == "local"


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "synthetic"])
def test_pending_entra_link_does_not_authorize_login_or_disable_local(
        identity_world, config, key, enabled):
    env, password_hash = identity_world
    admin, _ = mk_user(password_hash, admin=True, ident=True)
    target, email = mk_user(password_hash)
    with app.app_context():
        request_id = intents.approve_link(config, user_id=target, requested_by=admin,
                                         object_id=OID, expected_version=1)
    idp = Idp(config, key)
    idp.overrides = {"email": email, "preferred_username": email}
    api = make_app(env, config if enabled else entra.EntraConfig(), idp)
    # Exercise the real local login handler alongside the configured provider.
    api.add_url_rule("/login", view_func=app.view_functions["auth.login"], methods=["POST"])
    api.add_url_rule("/", endpoint="recordings.index", view_func=lambda: "")
    client, headers = anonymous(api)
    before = counts()
    discovery = client.get(BASE)
    assert discovery.status_code == 200
    assert discovery.json["providers"] == [
        {"provider": "entra", "enabled": enabled},
        {"provider": "iwa", "enabled": False}, {"provider": "ldap", "enabled": False},
    ]
    started = client.post(BASE + "/entra/login", headers=headers)
    if enabled:
        assert started.status_code == 200
        state = state_of(started)
    else:
        assert (started.status_code, started.json["code"]) == (422, "CAPABILITY_UNAVAILABLE")
        state = "synthetic-disabled-state"
    callback = client.get(BASE + "/entra/callback", query_string={"state": state, "code": "synthetic"})
    assert (callback.status_code, callback.json["code"]) == (
        (403, "FORBIDDEN") if enabled else (422, "CAPABILITY_UNAVAILABLE"))
    assert idp.calls == int(enabled)
    with client.session_transaction() as session:
        assert "_user_id" not in session
    assert counts() == (before[0], before[1], before[2] + int(enabled))
    with app.app_context():
        pending = db.session.get(intents.ClinicIdentityLinkRequest, request_id)
        assert (pending.user_id, pending.expected_object_id, pending.consumed_at) == (target, OID, None)
        assert pending.state_hash is None
        assert identity._policy(target).login_policy == "local_only"
        assert db.session.get(User, target).password == password_hash
    local, local_headers = anonymous(api)
    assert local.post("/login", headers=local_headers,
                      data={"login_id": email, "password": PASSWORD}).status_code == 302
    with local.session_transaction() as session:
        assert session["_user_id"] == str(target)
        assert session["clinic_provider"] == "local"
