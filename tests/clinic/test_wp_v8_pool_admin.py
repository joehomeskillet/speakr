"""Pool administration API (V8 P10/P11, v8-29): authz, audit with old/new, deactivation, migration.

Real ``pools`` service and real tables; synthetic users and pools only.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import create_engine, inspect, text

from src.app import app as real_app
from src.clinic import migration_pool_admin, pools
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.routes_pool_admin import PREFIX, blueprint
from src.clinic.workflow import DONE, TO_WRITE, ClinicWorkflowDictation
from src.database import db
from src.models import GroupMembership, Recording, User

from test_pools import _suffix, _user, pool_db as pool_db


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
    admin, plain, lead, other_lead = _user(admin=True), _user(), _user(), _user()
    for user in (admin, plain, lead, other_lead):
        if db.session.get(ClinicAccountPolicy, user.id) is None:
            db.session.add(ClinicAccountPolicy(user_id=user.id, status="active"))
    db.session.commit()
    author = pools.create_author_pool("A-" + _suffix(), actor_id=admin.id)
    staff = pools.create_staff_pool("S-" + _suffix(), actor_id=admin.id)
    other_staff = pools.create_staff_pool("O-" + _suffix(), actor_id=admin.id)
    db.session.add(GroupMembership(group_id=staff.group_id, user_id=lead.id, role="admin"))
    db.session.add(GroupMembership(group_id=other_staff.group_id, user_id=other_lead.id, role="admin"))
    db.session.commit()
    pools.link_staff_pool(author.id, staff.id, actor_id=admin.id)
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
        return c, {"X-CSRF-Token": c.get(f"/login/{user.id}").json["csrf"]}

    outer = db.session()
    outer.commit()
    with flask.app_context():
        yield dict(client=client, admin=admin, plain=plain, lead=lead, other_lead=other_lead,
                   author=author, staff=staff)
        outer.rollback()
        db.session.rollback()


def _history(entity_id, change):
    return [row for row in pools.history() if row["entity_id"] == entity_id and row["change"] == change]


def _workflow_row(author, pool_id, status):
    recording = Recording(user_id=author.id, title="d", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id, pool_id=pool_id)
    db.session.add(dictation)
    db.session.flush()
    db.session.add(ClinicWorkflowDictation(
        id=dictation.id, author_user_id=author.id, pool_id=pool_id, status=status))
    db.session.commit()
    return dictation


def test_non_admin_gets_403_on_every_write(world):
    c, h = world["client"](world["plain"])
    pid = world["author"].id
    calls = [
        c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": "X-" + _suffix()}),
        c.put(f"{PREFIX}/pools/author/{pid}/name", headers=h, json={"name": "Neu-" + _suffix()}),
        c.put(f"{PREFIX}/pools/author/{pid}/active", headers=h, json={"active": False}),
        c.put(f"{PREFIX}/pools/author/{pid}/validity", headers=h, json={"valid_from": None, "valid_to": None}),
        c.post(f"{PREFIX}/pools/author/{pid}/links", headers=h, json={"staff_pool_id": world["staff"].id}),
        c.get(PREFIX + "/pools"),
        c.get(f"{PREFIX}/pools/author/{pid}/usage"),
    ]
    calls += [
        c.post(PREFIX + "/pools", headers=h, json={"kind": "other", "name": "x"}),
        c.post(PREFIX + "/pools", headers=h, json={"bogus": 1}),
        c.put(f"{PREFIX}/pools/author/{pid}/validity", headers=h, json={"valid_from": "garbage", "valid_to": None}),
    ]
    assert [r.status_code for r in calls] == [403] * 10
    assert calls[0].mimetype == "application/problem+json"
    assert db.session.get(pools.ClinicAuthorPool, pid).active is None


def test_missing_csrf_token_is_refused(world):
    c, h = world["client"](world["admin"])
    assert c.post(PREFIX + "/pools", json={"kind": "author", "name": "N-" + _suffix()}).status_code == 403
    assert c.get(PREFIX + "/pools", headers=h).status_code == 200


def test_admin_creates_both_kinds_and_audits(world):
    c, h = world["client"](world["admin"])
    r = c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": "NA-" + _suffix()})
    assert r.status_code == 201 and r.json["kind"] == "author" and r.json["active"] is True
    created = _history(r.json["id"], "created")[0]
    assert created["actor_user_id"] == world["admin"].id
    assert created["detail"] == {"kind": "author", "old": None, "new": {"name": r.json["name"], "active": True}}
    r = c.post(PREFIX + "/pools", headers=h, json={"kind": "staff", "name": "NS-" + _suffix()})
    assert r.status_code == 201 and r.json["kind"] == "staff"
    staff_created = _history(r.json["id"], "created")[0]["detail"]
    assert staff_created == {
        "kind": "staff", "group_id": db.session.get(pools.ClinicStaffPool, r.json["id"]).group_id,
        "old": None, "new": {"name": r.json["name"], "active": True}}
    assert c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": r.json["name"]}).status_code == 201
    assert c.post(PREFIX + "/pools", headers=h, json={"kind": "other", "name": "x"}).status_code == 404
    assert c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": " "}).status_code == 400
    assert c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": "n", "actor_id": 1}).status_code == 400


def test_duplicate_name_is_409(world):
    c, h = world["client"](world["admin"])
    r = c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": world["author"].name})
    assert r.status_code == 409 and r.json["code"] == "name_taken"


def test_rename_audits_old_and_new(world):
    c, h = world["client"](world["admin"])
    old, new = world["author"].name, "R-" + _suffix()
    r = c.put(f"{PREFIX}/pools/author/{world['author'].id}/name", headers=h, json={"name": new})
    assert r.status_code == 200 and r.json["name"] == new
    assert _history(world["author"].id, "renamed")[-1]["detail"] == {"kind": "author", "old": old, "new": new}
    new_staff = "RS-" + _suffix()
    r = c.put(f"{PREFIX}/pools/staff/{world['staff'].id}/name", headers=h, json={"name": new_staff})
    assert r.status_code == 200 and db.session.get(pools.ClinicStaffPool, world["staff"].id).name == new_staff


def test_link_author_to_staff_pool_and_audit(world):
    c, h = world["client"](world["admin"])
    author = c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": "LA-" + _suffix()}).json
    r = c.post(f"{PREFIX}/pools/author/{author['id']}/links", headers=h, json={"staff_pool_id": world["staff"].id})
    assert r.status_code == 201
    link_ids = {"author_pool_id": author["id"], "staff_pool_id": world["staff"].id}
    assert _history(r.json["id"], "added")[0]["detail"] == {**link_ids, "old": None, "new": link_ids}
    again = c.post(f"{PREFIX}/pools/author/{author['id']}/links", headers=h, json={"staff_pool_id": world["staff"].id})
    assert again.status_code == 409 and again.json["code"] == "already_linked"
    missing = c.post(f"{PREFIX}/pools/author/{author['id']}/links", headers=h, json={"staff_pool_id": "nope"})
    assert missing.status_code == 404


def test_deactivate_audits_old_new_and_refuses_new_routing(world):
    c, h = world["client"](world["admin"])
    author = world["author"]
    r = c.put(f"{PREFIX}/pools/author/{author.id}/active", headers=h, json={"active": False})
    assert r.status_code == 200 and r.json["active"] is False and r.json["routable"] is False
    assert _history(author.id, "deactivated")[0]["detail"] == {"kind": "author", "old": True, "new": False}
    target = pools.create_staff_pool("T-" + _suffix(), actor_id=world["admin"].id)
    refused = c.post(f"{PREFIX}/pools/author/{author.id}/links", headers=h, json={"staff_pool_id": target.id})
    assert refused.status_code == 409 and refused.json["code"] == "pool_inactive"
    assert pools.ClinicPoolRoute.query.filter_by(author_pool_id=author.id, staff_pool_id=target.id).first() is None
    # a deactivated staff pool is refused as a target as well
    other_author = pools.create_author_pool("OA-" + _suffix(), actor_id=world["admin"].id)
    c.put(f"{PREFIX}/pools/staff/{target.id}/active", headers=h, json={"active": False})
    r = c.post(f"{PREFIX}/pools/author/{other_author.id}/links", headers=h, json={"staff_pool_id": target.id})
    assert r.status_code == 409
    # reactivation restores routing and is audited
    r = c.put(f"{PREFIX}/pools/author/{author.id}/active", headers=h, json={"active": True})
    assert r.json["routable"] is True
    assert _history(author.id, "reactivated")[0]["detail"] == {"kind": "author", "old": False, "new": True}
    # setting the same value writes no new history row
    before = len(pools.history())
    c.put(f"{PREFIX}/pools/author/{author.id}/active", headers=h, json={"active": True})
    assert len(pools.history()) == before
    assert c.put(f"{PREFIX}/pools/author/{author.id}/active", headers=h, json={"active": "no"}).status_code == 400


def test_deactivation_keeps_dictations_history_and_staff_view(world):
    c, h = world["client"](world["admin"])
    author_user = _user()
    pools.add_author_member(world["author"].id, author_user.id, actor_id=world["admin"].id)
    row = _workflow_row(author_user, world["author"].id, TO_WRITE)
    c.put(f"{PREFIX}/pools/author/{world['author'].id}/active", headers=h, json={"active": False})
    c.put(f"{PREFIX}/pools/staff/{world['staff'].id}/active", headers=h, json={"active": False})
    assert db.session.get(ClinicDictation, row.id).pool_id == world["author"].id
    assert db.session.get(ClinicWorkflowDictation, row.id) is not None
    assert [e for e in pools.history() if e["entity_id"] == world["author"].id and e["change"] == "created"]
    # existing own view and staff-link view are read rights, so they survive deactivation
    typist = _user()
    pools.add_staff_member(world["staff"].id, typist.id, actor_id=world["admin"].id)
    assert "view" in pools.grants_for(typist.id, db.session.get(ClinicDictation, row.id))["allowed"]
    assert "play" in pools.grants_for(author_user.id, db.session.get(ClinicDictation, row.id))["allowed"]
    assert any(p["id"] == world["author"].id and p["active"] is False for p in c.get(PREFIX + "/pools").json["author_pools"])


def test_validity_window(world):
    c, h = world["client"](world["admin"])
    now = datetime.now(timezone.utc)
    url = f"{PREFIX}/pools/author/{world['author'].id}/validity"
    past = (now - timedelta(days=2)).isoformat()
    ended = (now - timedelta(days=1)).isoformat()
    r = c.put(url, headers=h, json={"valid_from": past, "valid_to": ended})
    assert r.status_code == 200 and r.json["routable"] is False
    assert _history(world["author"].id, "validity_set")[0]["detail"]["new"] == {
        "valid_from": datetime.fromisoformat(past).isoformat(), "valid_to": datetime.fromisoformat(ended).isoformat()}
    target = pools.create_staff_pool("V-" + _suffix(), actor_id=world["admin"].id)
    assert c.post(f"{PREFIX}/pools/author/{world['author'].id}/links", headers=h,
                  json={"staff_pool_id": target.id}).status_code == 409
    future = (now + timedelta(days=1)).isoformat()
    r = c.put(url, headers=h, json={"valid_from": None, "valid_to": future})
    assert r.json["routable"] is True
    second = _history(world["author"].id, "validity_set")[-1]["detail"]
    assert second["old"]["valid_to"] == datetime.fromisoformat(ended).isoformat() and second["new"]["valid_from"] is None
    assert c.put(url, headers=h, json={"valid_from": future, "valid_to": past}).status_code == 400
    assert c.put(url, headers=h, json={"valid_from": "2030-01-01T00:00:00", "valid_to": None}).status_code == 400
    assert c.put(url, headers=h, json={"valid_from": "garbage", "valid_to": None}).status_code == 400
    assert c.put(url, headers=h, json={"valid_from": 5, "valid_to": None}).status_code == 400


def test_usage_preview_counts_only(world):
    c, h = world["client"](world["admin"])
    author_user = _user()
    pools.add_author_member(world["author"].id, author_user.id, actor_id=world["admin"].id)
    pools.add_staff_member(world["staff"].id, _user().id, actor_id=world["admin"].id)
    _workflow_row(author_user, world["author"].id, TO_WRITE)
    _workflow_row(author_user, world["author"].id, TO_WRITE)
    _workflow_row(author_user, world["author"].id, DONE)
    r = c.get(f"{PREFIX}/pools/author/{world['author'].id}/usage")
    assert r.status_code == 200
    assert r.json == {"kind": "author", "pool_id": world["author"].id, "open_dictations": 2, "members": 1, "links": 1}
    r = c.get(f"{PREFIX}/pools/staff/{world['staff'].id}/usage")
    assert r.json["open_dictations"] == 2 and r.json["members"] == 2 and r.json["links"] == 1
    assert c.get(f"{PREFIX}/pools/author/unknown/usage").status_code == 404
    assert c.get(f"{PREFIX}/pools/team/x/usage").status_code == 404


def test_pool_lead_may_preview_own_pools_only(world):
    c, _ = world["client"](world["lead"])
    assert c.get(f"{PREFIX}/pools/staff/{world['staff'].id}/usage").status_code == 200
    assert c.get(f"{PREFIX}/pools/author/{world['author'].id}/usage").status_code == 200
    assert c.get(PREFIX + "/pools").status_code == 403
    c2, _ = world["client"](world["other_lead"])
    assert c2.get(f"{PREFIX}/pools/staff/{world['staff'].id}/usage").status_code == 403
    assert c2.get(f"{PREFIX}/pools/author/{world['author'].id}/usage").status_code == 403


def test_disabled_lead_gets_403_on_usage(world):
    c, _ = world["client"](world["lead"])
    url = f"{PREFIX}/pools/staff/{world['staff'].id}/usage"
    assert c.get(url).status_code == 200
    db.session.get(ClinicAccountPolicy, world["lead"].id).status = "disabled"
    db.session.commit()
    assert c.get(url).status_code == 403


def test_routable_staff_pool_ids_excludes_inactive_and_expired(world):
    c, h = world["client"](world["admin"])
    author, staff = world["author"], world["staff"]
    admin_id = world["admin"].id
    expired = pools.create_staff_pool("E-" + _suffix(), actor_id=admin_id)
    off = pools.create_staff_pool("D-" + _suffix(), actor_id=admin_id)
    for target in (expired, off):
        pools.link_staff_pool(author.id, target.id, actor_id=admin_id)
    author_user = _user()
    pools.add_author_member(author.id, author_user.id, actor_id=admin_id)
    row = _workflow_row(author_user, author.id, TO_WRITE)
    assert set(pools.routable_staff_pool_ids(author.id)) == {staff.id, expired.id, off.id}
    c.put(f"{PREFIX}/pools/staff/{off.id}/active", headers=h, json={"active": False})
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    c.put(f"{PREFIX}/pools/staff/{expired.id}/validity", headers=h,
          json={"valid_from": None, "valid_to": past})
    assert pools.routable_staff_pool_ids(author.id) == [staff.id]
    assert pools.ClinicPoolRoute.query.filter_by(author_pool_id=author.id).count() == 3
    assert db.session.get(ClinicWorkflowDictation, row.id) is not None
    assert "play" in pools.grants_for(author_user.id, db.session.get(ClinicDictation, row.id))["allowed"]


def test_revoked_admin_is_refused(world):
    account = db.session.get(ClinicAccountPolicy, world["admin"].id)
    account.status = "disabled"
    db.session.commit()
    c, h = world["client"](world["admin"])
    r = c.post(PREFIX + "/pools", headers=h, json={"kind": "author", "name": "Z-" + _suffix()})
    assert r.status_code == 403


def test_clinic_mode_off_hides_the_blueprint(world, monkeypatch):
    c, _ = world["client"](world["admin"])
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(PREFIX + "/pools").status_code == 404


def test_migration_is_additive_and_reversible(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE clinic_author_pool (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100))"))
        connection.execute(text("CREATE TABLE clinic_staff_pool (id VARCHAR(36) PRIMARY KEY, group_id INTEGER)"))
        connection.execute(text("INSERT INTO clinic_author_pool VALUES ('a', 'Alt')"))
    ran = migration_pool_admin.upgrade(engine)
    assert len(ran) == 6
    for table in migration_pool_admin.TABLES:
        assert {"active", "valid_from", "valid_to"} <= {c["name"] for c in inspect(engine).get_columns(table)}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, name, active FROM clinic_author_pool")).one() == ("a", "Alt", None)
    assert migration_pool_admin.upgrade(engine) == []  # idempotent
    assert len(migration_pool_admin.downgrade(engine)) == 6
    for table in migration_pool_admin.TABLES:
        assert not {"active", "valid_from", "valid_to"} & {c["name"] for c in inspect(engine).get_columns(table)}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, name FROM clinic_author_pool")).one() == ("a", "Alt")
    assert migration_pool_admin.downgrade(engine) == []


REACH_SCRIPT = r'''
import json, sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User
from src.clinic import identity
from src.clinic.route_policy import ROUTE_POLICY

out = {}
endpoints = {r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith("clinic_pool_admin.")}
out["endpoints"] = sorted(endpoints)
out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY)
out["kinds"] = sorted({ROUTE_POLICY[e].kind for e in endpoints if e in ROUTE_POLICY})
URL = "/api/clinic/v1/pool-admin/pools"
if sys.argv[1] == "off":
    with app.app_context():
        initialize_database(app)
    out["status"] = app.test_client().get(URL).status_code
    print("RESULT:" + json.dumps(out)); sys.exit(0)
with app.app_context():
    initialize_database(app)
    ids = {}
    for name, admin in (("admin", True), ("plain", False)):
        u = User(username=f"v829_{name}", email=f"v829_{name}@local.test", password="x", is_admin=admin)
        db.session.add(u); db.session.flush(); ids[name] = u.id
        db.session.add(identity.ClinicAccountPolicy(user_id=u.id, status="active"))
    db.session.commit()

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
        s["clinic_provider"] = "local"; s["clinic_security_version"] = 0
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

ca, ha = login(ids["admin"]); cp, hp = login(ids["plain"])
out["anon"] = app.test_client().get(URL).status_code
out["plain"] = cp.get(URL).status_code
out["admin_list"] = ca.get(URL).status_code
made = ca.post(URL, headers=ha, json={"kind": "author", "name": "V829 Pool"})
out["admin_create"] = made.status_code
out["admin_usage"] = ca.get(f"{URL}/author/{made.json['id']}/usage").status_code
out["plain_create"] = cp.post(URL, headers=hp, json={"kind": "author", "name": "V829 Other"}).status_code
print("RESULT:" + json.dumps(out))
'''


def _reach(mode: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'v829.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if mode == "on":
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", REACH_SCRIPT, mode], cwd=Path(__file__).resolve().parents[2],
                          env=env, capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])


def test_routes_are_mounted_classified_and_reachable_in_clinic_mode(tmp_path):
    result = _reach("on", tmp_path)
    assert result["endpoints"] == sorted(
        f"clinic_pool_admin.{n}" for n in ("list_pools", "create_pool", "rename", "set_active",
                                           "set_validity", "link", "usage", "set_member_role"))
    assert result["unclassified"] == [] and result["kinds"] == ["auth"]
    assert result["anon"] in (302, 401)
    assert (result["plain"], result["plain_create"]) == (403, 403)
    assert (result["admin_list"], result["admin_create"], result["admin_usage"]) == (200, 201, 200)


def test_routes_are_absent_with_clinic_mode_off(tmp_path):
    result = _reach("off", tmp_path)
    assert result["endpoints"] == [] and result["status"] == 404
