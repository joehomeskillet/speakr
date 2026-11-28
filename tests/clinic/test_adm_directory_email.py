"""Confirmed profile changes against synthetic directory snapshots (PAR-ADM-009)."""

import pytest

from src.clinic.directory_diff import DirectorySnapshot
from src.clinic.directory_provider import PROVIDER_EXTENSION
from src.clinic.directory_reconcile import DirectoryEntry, LocalUser, compute_diff
from src.clinic.entra import ClinicExternalIdentity
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import User
from test_wp_directory_api import P, _apply_body, _fresh, _preview, pool_db as pool_db, world as world


@pytest.fixture
def directory(world, monkeypatch):
    state = {"object_id": f"oid-a-{world['tag']}", "email": f"  NEW-{world['tag']}@Example.Invalid  "}

    def provider():
        return DirectorySnapshot("synthetic", "v1", (
            DirectoryEntry(state["object_id"], "a", "Neuer Name", state["email"], True),
        ), ())

    monkeypatch.setitem(world["flask"].extensions, PROVIDER_EXTENSION, provider)
    return state


@pytest.mark.parametrize("kinds", [
    (), ("update_display_name",), ("update_email",), ("update_display_name", "update_email"),
], ids=["none", "name", "email", "both"])
def test_profile_fields_are_confirmed_independently(world, directory, kinds):
    user_id, old_email = world["renamed"].id, world["renamed"].email.upper()
    db.session.get(User, user_id).email = old_email
    db.session.commit()
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    assert len(pv["changed_fields"]) == 1
    row = pv["changed_fields"][0]
    assert (row["user_id"], row["object_id"]) == (user_id, directory["object_id"])
    assert set(row["fields"]) == {"display_name", "email"}
    assert {(a["kind"], a["object_id"], a["local_user_id"]) for a in pv["actions"]} == {
        ("update_display_name", directory["object_id"], user_id),
        ("update_email", directory["object_id"], user_id),
    }
    before = _fresh(User, user_id)
    assert (before.name, before.email) == ("Alter Name", old_email)
    version = _fresh(ClinicAccountPolicy, user_id).version

    actions = [{"kind": kind, "object_id": directory["object_id"]} for kind in kinds]
    r = c.post(P + "/apply", headers=h, json=_apply_body(world, pv, actions=actions, memberships=[]))
    assert r.status_code == 200, r.json
    assert (r.json["applied_actions"], r.json["applied_memberships"]) == (len(actions), 0)
    after = _fresh(User, user_id)
    assert after.name == ("Neuer Name" if "update_display_name" in kinds else "Alter Name")
    assert after.email == (f"new-{world['tag']}@example.invalid" if "update_email" in kinds else old_email)
    assert ClinicExternalIdentity.query.filter_by(object_id=directory["object_id"]).one().user_id == user_id
    assert _fresh(ClinicAccountPolicy, user_id).version == version + bool(kinds)


def test_colliding_email_does_not_partially_apply_name(world, directory):
    user_id, old_email = world["renamed"].id, world["renamed"].email
    other_id, other_email = world["plain"].id, world["plain"].email
    directory["email"] = f"  {other_email.upper()}  "
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    actions = [{"kind": kind, "object_id": directory["object_id"]} for kind in ("update_display_name", "update_email")]
    r = c.post(P + "/apply", headers=h, json=_apply_body(world, pv, actions=actions))
    assert (r.status_code, r.json["code"]) == (409, "apply_failed")
    after = _fresh(User, user_id)
    assert (after.name, after.email) == ("Alter Name", old_email)
    assert _fresh(User, other_id).email == other_email


def test_same_email_does_not_link_an_unmatched_subject(world, directory):
    user_id, email = world["plain"].id, world["plain"].email
    directory.update(object_id=f"unlinked-{world['tag']}", email=email)
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    assert pv["changed_fields"] == []
    assert [(a["kind"], a["object_id"], a["local_user_id"]) for a in pv["actions"]] == [
        ("create_pending_user", directory["object_id"], None),
    ]
    r = c.post(P + "/apply", headers=h, json=_apply_body(world, pv))
    assert (r.status_code, r.json["code"]) == (422, "explicit_link_required")
    assert ClinicExternalIdentity.query.filter_by(user_id=user_id).count() == 0


def test_changed_provider_email_invalidates_preview_at_same_version(world, directory):
    user_id, old_email = world["renamed"].id, world["renamed"].email
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    directory["email"] = f"other-{world['tag']}@example.invalid"
    actions = [{"kind": "update_email", "object_id": directory["object_id"]}]
    r = c.post(P + "/apply", headers=h, json=_apply_body(world, pv, actions=actions))
    assert (r.status_code, r.json["code"]) == (409, "stale_state")
    assert _fresh(User, user_id).email == old_email


@pytest.mark.parametrize(("local_email", "directory_email"), [
    (None, "new@example.invalid"),
    ("old@example.invalid", None),
    ("old@example.invalid", "   "),
    ("old@example.invalid", "  OLD@Example.Invalid  "),
], ids=["unknown-local", "missing-directory", "blank-directory", "same-normalized"])
def test_email_is_reported_only_when_known_and_different(local_email, directory_email):
    local = LocalUser(1, "a", "oid-a", "Alter Name", True)
    assert local.email is None
    if local_email is not None:
        local = LocalUser(1, "a", "oid-a", "Alter Name", True, email=local_email)
    entry = DirectoryEntry("oid-a", "a", "Neuer Name", directory_email, True)
    assert compute_diff([entry], [local]).changed == ((local, entry, ("display_name",)),)
