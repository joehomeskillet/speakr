"""Explicit provisioning via the synthetic, server-selected directory."""

import json
from dataclasses import replace

import pytest

from src.clinic.directory_diff import DirectorySnapshot
from src.clinic.directory_provider import PROVIDER_EXTENSION
from src.clinic.directory_reconcile import DirectoryEntry
from src.clinic.entra import ClinicExternalIdentity
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import User

from test_adm_directory_provider import registered_client  # noqa: F401
from test_wp_directory_api import P, world as directory_world, pool_db as pool_db  # noqa: F401


def _preview(client, headers):
    response = client.post(P + "/preview", headers=headers, json={})
    assert response.status_code == 200, response.json
    return response.json


def _link(client, headers, preview, subject, **extra):
    return client.post(P + "/link-new", headers=headers, json={
        "version": preview["version"], "hash": preview["hash"], "object_id": subject, **extra,
    })


def test_selected_new_entry_creates_one_bound_pending_account(registered_client, directory_world, tmp_path):
    selected = "new-selected-" + directory_world["tag"]
    unselected = "new-unselected-" + directory_world["tag"]
    path = tmp_path / "directory.json"
    path.write_text(json.dumps({"version": "fixture-r1", "entries": [
        {"object_id": subject, "account": "domain-user", "display_name": name, "enabled": True}
        for subject, name in ((selected, "Neue Person"), (unselected, "Andere Person"))
    ], "memberships": []}), encoding="utf-8")
    client, headers = registered_client("synthetic-v1", path)
    before = (User.query.count(), ClinicAccountPolicy.query.count(), ClinicExternalIdentity.query.count())
    preview = _preview(client, headers)
    assert {row["object_id"] for row in preview["actions"]} == {selected, unselected}
    assert (User.query.count(), ClinicAccountPolicy.query.count(), ClinicExternalIdentity.query.count()) == before
    absent = _link(client, headers, preview, "absent")
    assert (absent.status_code, absent.json["code"]) == (400, "invalid_selection")

    response = _link(client, headers, preview, selected)
    assert response.status_code == 201, response.json
    user_id = response.json["user_id"]
    user = db.session.get(User, user_id)
    policy = db.session.get(ClinicAccountPolicy, user_id)
    link = ClinicExternalIdentity.query.filter_by(user_id=user_id).one_or_none()
    try:
        assert (user.name, user.password, user.is_admin) == ("Neue Person", None, False)
        assert (policy.status, policy.login_policy, policy.is_identity_admin) == ("pending_activation", "local_only", False)
        assert user.email.endswith("@directory.invalid")
        assert link is not None
        assert (link.issuer, link.tenant_id, link.subject, link.status, link.created_by) == (
            "urn:speakr:directory:synthetic", "synthetic", selected, "active", directory_world["admin"].id,
        )

        replay = _link(client, headers, preview, selected)
        assert (replay.status_code, replay.json["code"]) == (409, "stale_state")
        fresh = _preview(client, headers)
        assert [row["object_id"] for row in fresh["actions"]] == [unselected]
        absent = _link(client, headers, fresh, selected)
        assert (absent.status_code, absent.json["code"]) == (400, "invalid_selection")
        assert (User.query.count(), ClinicAccountPolicy.query.count(), ClinicExternalIdentity.query.count()) == tuple(
            count + 1 for count in before)
    finally:
        if link is not None:
            db.session.delete(link)
        if policy is not None:
            db.session.delete(policy)
        db.session.flush()
        if user is not None:
            db.session.delete(user)
        db.session.commit()


@pytest.mark.parametrize("extra", [{"issuer": "https://other.example.invalid"}, {"tenant_id": "other"}])
def test_caller_cannot_supply_a_foreign_identity_scope(directory_world, monkeypatch, extra):
    snapshot = DirectorySnapshot("synthetic", "v1", (DirectoryEntry("new", "new", "Neue Person", None, True),))
    monkeypatch.setitem(directory_world["flask"].extensions, PROVIDER_EXTENSION, lambda: snapshot)
    client, headers = directory_world["client"](directory_world["admin"])
    preview = _preview(client, headers)
    before = User.query.count()
    response = _link(client, headers, preview, "new", **extra)
    assert (response.status_code, response.json["code"]) == (400, "invalid_payload")
    assert User.query.count() == before


@pytest.mark.parametrize(("changed", "code"), [
    ({"version": "v2"}, "stale_version"),
    ({"source": "foreign"}, "stale_state"),
    ({"entries": (DirectoryEntry("new", "new", "Geänderter Name", None, True),)}, "stale_state"),
])
def test_changed_snapshot_cannot_create_a_pending_account(directory_world, monkeypatch, changed, code):
    snapshot = DirectorySnapshot("synthetic", "v1", (DirectoryEntry("new", "new", "Neue Person", None, True),))
    monkeypatch.setitem(directory_world["flask"].extensions, PROVIDER_EXTENSION, lambda: snapshot)
    client, headers = directory_world["client"](directory_world["admin"])
    preview = _preview(client, headers)
    before = User.query.count()
    snapshot = replace(snapshot, **changed)
    response = _link(client, headers, preview, "new")
    assert (response.status_code, response.json["code"]) == (409, code)
    assert User.query.count() == before


def test_unknown_provider_cannot_create_from_its_own_preview(directory_world, monkeypatch):
    snapshot = DirectorySnapshot("foreign", "v1", (DirectoryEntry("new", "new", "Neue Person", None, True),))
    monkeypatch.setitem(directory_world["flask"].extensions, PROVIDER_EXTENSION, lambda: snapshot)
    client, headers = directory_world["client"](directory_world["admin"])
    before = User.query.count()
    response = _link(client, headers, _preview(client, headers), "new")
    assert (response.status_code, response.json["code"]) == (503, "ldap_capability_unavailable")
    assert User.query.count() == before


def test_conflicting_binding_rolls_back_pending_account(directory_world, monkeypatch):
    actor, other = directory_world["admin"], directory_world["plain"]
    subject = "foreign-" + directory_world["tag"]
    snapshot = DirectorySnapshot("synthetic", "v1", (DirectoryEntry(subject, "new", "Neue Person", other.email, True),))
    monkeypatch.setitem(directory_world["flask"].extensions, PROVIDER_EXTENSION, lambda: snapshot)
    db.session.add(ClinicExternalIdentity(
        user_id=other.id, issuer="urn:speakr:directory:synthetic", tenant_id="foreign",
        subject=subject, created_by=actor.id,
    ))
    db.session.commit()
    client, headers = directory_world["client"](actor)
    preview = _preview(client, headers)
    assert [row["object_id"] for row in preview["actions"]] == [subject]
    before = (User.query.count(), ClinicAccountPolicy.query.count())

    response = _link(client, headers, preview, subject)
    assert (response.status_code, response.json["code"]) == (409, "apply_failed")
    assert (User.query.count(), ClinicAccountPolicy.query.count()) == before
    link = ClinicExternalIdentity.query.filter_by(subject=subject).one()
    assert (link.user_id, link.tenant_id) == (other.id, "foreign")


def test_link_new_requires_identity_admin_and_csrf(directory_world):
    admin_client, _ = directory_world["client"](directory_world["admin"])
    plain_client, headers = directory_world["client"](directory_world["plain"])
    payload = {"version": "v1", "hash": "0" * 64, "object_id": "new"}
    before = User.query.count()
    assert admin_client.post(P + "/link-new", json=payload).status_code == 403
    assert plain_client.post(P + "/link-new", headers=headers, json=payload).status_code == 403
    assert User.query.count() == before
