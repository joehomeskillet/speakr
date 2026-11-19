"""Directory preview and confirmed application routes (PAR-ADM-009), HTTP slice.

Synthetic directory snapshots only; no live directory, Entra or Windows account
is read, so G-LDAP, G-IWA and WG-ADM-009 stay open. Positive, unauthorized,
stale-version and retry/failure cases run against the real identity and pool
services.
"""

import pytest
from flask import Flask, g, jsonify, session
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import directory_diff as dd
from src.clinic import entra, identity, pools, routes_directory as rd
from src.clinic.directory_reconcile import DirectoryEntry
from src.database import db
from src.models import User

from test_pools import _suffix, pool_db as pool_db

P = rd.PREFIX


def _account(name, *, admin=False, identity_admin=False, oid=None):
    suffix = _suffix()
    user = User(username="d" + suffix, email=suffix + "@example.invalid", password="x",
                is_admin=admin, name=name)
    db.session.add(user)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active",
                                                is_identity_admin=identity_admin))
    if oid:
        db.session.add(entra.ClinicExternalIdentity(
            user_id=user.id, issuer="https://issuer.example.invalid", tenant_id="synthetic",
            subject="sub-" + oid, object_id=oid, created_by=user.id))
    db.session.commit()
    return user


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    identity.ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
    entra.create_tables()
    tag = _suffix()
    admin = _account("Admin " + tag, admin=True, identity_admin=True)
    plain = _account("Plain " + tag, admin=True)
    renamed = _account("Alter Name", oid="oid-a-" + tag)
    leaver = _account("Leaver", oid="oid-b-" + tag)
    pool = pools.create_author_pool("D-" + tag, actor_id=admin.id)
    state = {"version": "v1", "name": "Neuer Name", "available": True}

    def provider():
        if not state["available"]:
            return None
        return dd.DirectorySnapshot("synthetic", state["version"], (
            DirectoryEntry("oid-a-" + tag, "a", state["name"], None, True),
            DirectoryEntry("oid-b-" + tag, "b", "Leaver", None, False),
        ), (dd.DirectoryMembership("oid-a-" + tag, "author", pool.id),))

    rd.set_provider(provider)
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
        session.update(clinic_provider="local", clinic_security_version=0)
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(rd.blueprint)

    def client(user):
        c = flask.test_client()
        g.pop("csrf_token", None)  # the long-lived app context would reuse another login's token
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()
    outer.commit()
    ids = [admin.id, plain.id, renamed.id, leaver.id]
    with flask.app_context():
        yield dict(client=client, admin=admin, plain=plain, renamed=renamed, leaver=leaver, pool=pool,
                   state=state, tag=tag, flask=flask)
        rd.set_provider(None)
        outer.rollback()
        db.session.rollback()
        db.session.query(pools.ClinicAuthorPoolMember).filter_by(pool_id=pool.id).delete()
        db.session.query(entra.ClinicExternalIdentity).filter(
            entra.ClinicExternalIdentity.user_id.in_(ids)).delete(synchronize_session=False)
        db.session.query(identity.ClinicAccountPolicy).filter(
            identity.ClinicAccountPolicy.user_id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def _preview(world, c, h):
    r = c.post(P + "/preview", headers=h, json={"managed_pools": [{"kind": "author", "id": world["pool"].id}]})
    assert r.status_code == 200, r.json
    return r.json


def _apply_body(world, pv, **extra):
    body = {"version": pv["version"], "hash": pv["hash"],
            "managed_pools": [{"kind": "author", "id": world["pool"].id}],
            "actions": [{"kind": a["kind"], "object_id": a["object_id"]} for a in pv["actions"]],
            "memberships": [{k: m[k] for k in ("kind", "subject_id", "pool_kind", "pool_id")}
                            for m in pv["membership_changes"]]}
    body.update(extra)
    return body


def _fresh(model, pk):
    db.session.expire_all()
    return db.session.get(model, pk)


def test_preview_shows_changed_fields_and_writes_nothing(world):
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    assert pv["version"] == "v1" and len(pv["hash"]) == 64
    assert {"user_id": world["renamed"].id, "object_id": "oid-a-" + world["tag"],
            "fields": ["display_name"]} in pv["changed_fields"]
    assert {a["kind"] for a in pv["actions"]} == {"update_display_name", "deactivate"}
    assert [m["kind"] for m in pv["membership_changes"]] == ["add"]
    assert _fresh(User, world["renamed"].id).name == "Alter Name"
    assert pools.ClinicAuthorPoolMember.query.filter_by(pool_id=world["pool"].id).count() == 0


def test_apply_persists_only_selected_changes_through_services(world):
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    body = _apply_body(world, pv)
    body["actions"] = [a for a in body["actions"] if a["kind"] == "update_display_name"]
    r = c.post(P + "/apply", headers=h, json=body)
    assert (r.status_code, r.json["applied_actions"], r.json["applied_memberships"]) == (200, 1, 1)
    assert _fresh(User, world["renamed"].id).name == "Neuer Name"
    assert _fresh(identity.ClinicAccountPolicy, world["leaver"].id).status == "active"  # not selected
    assert pools.ClinicAuthorPoolMember.query.filter_by(
        pool_id=world["pool"].id, user_id=world["renamed"].id).count() == 1
    # Retry with the same preview is refused: the state it described is gone.
    again = c.post(P + "/apply", headers=h, json=body)
    assert (again.status_code, again.json["code"]) == (409, "stale_state")
    assert pools.ClinicAuthorPoolMember.query.filter_by(pool_id=world["pool"].id).count() == 1
    # A fresh preview then covers the remaining deactivation.
    pv2 = _preview(world, c, h)
    assert [a["kind"] for a in pv2["actions"]] == ["deactivate"]
    done = c.post(P + "/apply", headers=h, json=_apply_body(world, pv2))
    assert done.status_code == 200
    assert _fresh(identity.ClinicAccountPolicy, world["leaver"].id).status == "disabled"


def test_stale_version_and_state_leave_data_unchanged(world):
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    world["state"]["version"] = "v2"
    stale = c.post(P + "/apply", headers=h, json=_apply_body(world, pv))
    assert (stale.status_code, stale.json["code"]) == (409, "stale_version")
    world["state"]["version"] = "v1"
    db.session.query(User).filter_by(id=world["leaver"].id).update({"name": "Anders"})
    db.session.commit()
    changed = c.post(P + "/apply", headers=h, json=_apply_body(world, pv))
    assert (changed.status_code, changed.json["code"]) == (409, "stale_state")
    forged = c.post(P + "/apply", headers=h, json=_apply_body(world, pv, hash="0" * 64))
    assert forged.status_code == 409
    assert _fresh(User, world["renamed"].id).name == "Alter Name"
    assert pools.ClinicAuthorPoolMember.query.filter_by(pool_id=world["pool"].id).count() == 0


def test_invalid_selection_link_requirement_and_self_deactivation(world):
    c, h = world["client"](world["admin"])
    pv = _preview(world, c, h)
    url = P + "/apply"
    absent = c.post(url, headers=h, json=_apply_body(world, pv, actions=[{"kind": "deactivate", "object_id": "x"}]))
    assert (absent.status_code, absent.json["code"]) == (400, "invalid_selection")
    assert c.post(url, headers=h, json=_apply_body(world, pv, actions="all")).status_code == 400
    assert c.post(url, headers=h, json={"hash": pv["hash"]}).status_code == 400
    assert _fresh(User, world["renamed"].id).name == "Alter Name"
    # Per-object policy: an administrator never deactivates their own account.
    own = dd.Action("deactivate", "o", world["admin"].id, "")
    assert rd._authorize(world["admin"].id, own) is False
    assert rd._authorize(world["admin"].id, dd.Action("create_pending_user", "o", None, "")) is False
    assert rd._authorize(world["admin"].id, dd.Action("deactivate", "o", world["leaver"].id, "")) is True


def test_unauthorized_actors_csrf_untrusted_body_and_clinic_mode(world, monkeypatch):
    body = {"managed_pools": []}
    assert world["flask"].test_client().get(P + "/capability").status_code == 401
    c, h = world["client"](world["plain"])  # administrator without identity administration
    assert (c.post(P + "/preview", headers=h, json=body).status_code, c.get(P + "/capability").status_code) == (403, 403)
    c, h = world["client"](world["admin"])
    c2, _ = world["client"](world["admin"])
    no_csrf = c2.post(P + "/preview", json=body)
    assert (no_csrf.status_code, no_csrf.mimetype) == (403, "application/problem+json")
    for key in ("actor_id", "user_id", "role", "is_identity_admin"):
        assert c.post(P + "/preview", headers=h, json=dict(body, **{key: 1})).status_code == 400
    assert c.post(P + "/preview", headers=h, json={"managed_pools": [{"kind": "x", "id": "y"}]}).status_code == 400
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(P + "/capability").status_code == 404


def test_provider_unavailable_keeps_local_account_management(world):
    world["state"]["available"] = False
    c, h = world["client"](world["admin"])
    r = c.post(P + "/preview", headers=h, json={"managed_pools": []})
    assert (r.status_code, r.json["code"]) == (503, "ldap_capability_unavailable")
    cap = c.get(P + "/capability").json
    assert cap["local_accounts"] is True and cap["local_windows_accounts"] == "unavailable"
    created = identity.create_local_user("lokal" + world["tag"], f"lokal{world['tag']}@example.invalid",
                                         actor_id=world["admin"].id)
    assert _fresh(identity.ClinicAccountPolicy, created.id).status == "pending_activation"
    rd.set_provider(None)
    assert c.post(P + "/preview", headers=h, json={"managed_pools": []}).status_code == 503
    assert c.get(P + "/capability").json["directory_available"] is False
    db.session.query(identity.ClinicAccountPolicy).filter_by(user_id=created.id).delete()
    db.session.commit()
