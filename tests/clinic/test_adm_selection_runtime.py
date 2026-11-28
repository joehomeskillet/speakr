"""Last selection in the real app (PAR-ADM-011, P-135).

Two child processes share one throwaway database, so the second one is a
restart. The first stores selections for two authors and a typist and revokes
one pool membership; the second reads them back through the mounted routes.
Synthetic users only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r'''
import json, sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User
from src.clinic import identity, pools

PHASE = sys.argv[1]
PREF = "/api/clinic/v1/preferences/last-selection"
out = {}

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
        s["clinic_provider"] = "local"; s["clinic_security_version"] = 0
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

def get(c):
    r = c.get(PREF)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:500])
    return r.json["selection"], r.json["fallback"]

def setup():
    ids = {}
    for name, admin in (("admin", True), ("alpha", False), ("beta", False), ("typist", False)):
        u = User(username=f"p135_{name}", email=f"p135_{name}@local.test", password="x", is_admin=admin)
        db.session.add(u); db.session.flush(); ids[name] = u.id
    for name, uid in ids.items():
        db.session.add(identity.ClinicAccountPolicy(
            user_id=uid, status="active", is_identity_admin=name == "admin"))
    db.session.commit()
    adm = ids["admin"]
    pa = pools.create_author_pool("P135 A", actor_id=adm).id
    pb = pools.create_author_pool("P135 B", actor_id=adm).id
    pb2 = pools.create_author_pool("P135 B2", actor_id=adm).id
    pools.add_author_member(pa, ids["alpha"], actor_id=adm)
    pools.add_author_member(pb, ids["beta"], actor_id=adm)
    pools.add_author_member(pb2, ids["beta"], actor_id=adm)
    staff = pools.create_staff_pool("P135 S", actor_id=adm).id
    pools.add_staff_member(staff, ids["typist"], actor_id=adm)
    pools.link_staff_pool(pa, staff, actor_id=adm)
    return {"ids": ids, "pa": pa, "pb": pb, "pb2": pb2}

if PHASE == "first":
    with app.app_context():
        initialize_database(app)
        state = setup()
    open(sys.argv[2], "w").write(json.dumps(state))
    ids, pa, pb, pb2 = state["ids"], state["pa"], state["pb"], state["pb2"]
    ca, ha = login(ids["alpha"]); cb, hb = login(ids["beta"]); ct, ht = login(ids["typist"])
    out["default_typist"] = get(ct)
    out["default_alpha"] = get(ca)
    put = lambda c, h, body: c.put(PREF, headers=h, json={"selection": body}).status_code
    out["put_alpha"] = put(ca, ha, {"role": "author", "author_id": ids["alpha"], "pool_id": pa})
    out["put_beta"] = put(cb, hb, {"role": "author", "author_id": ids["beta"], "pool_id": pb2})
    out["put_typist"] = put(ct, ht, {"role": "typist", "author_id": ids["alpha"], "pool_id": pa})
    out["put_foreign_pool"] = put(ca, ha, {"role": "author", "author_id": ids["alpha"], "pool_id": pb})
    out["put_typist_as_author"] = put(ct, ht, {"role": "author", "author_id": ids["typist"], "pool_id": pa})
    with app.app_context():  # revoke beta's chosen pool before the restart
        pools.remove_author_member(pb2, ids["beta"], actor_id=ids["admin"])
    out["beta_after_revoke"] = get(cb)
else:
    with app.app_context():
        initialize_database(app)
    state = json.loads(open(sys.argv[2]).read())
    ids = state["ids"]
    ca, _ = login(ids["alpha"]); cb, _ = login(ids["beta"]); ct, _ = login(ids["typist"])
    out["alpha"] = get(ca)
    out["beta"] = get(cb)
    out["typist"] = get(ct)
    out.update(ids=ids, pa=state["pa"], pb=state["pb"], pb2=state["pb2"])
print("RESULT:" + json.dumps(out))
'''


def _run(phase: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'p135.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false", "CLINIC_MODE": "1"}
    done = subprocess.run([sys.executable, "-c", SCRIPT, phase, str(tmp_path / "state.json")], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p135")
    first = _run("first", tmp)
    return first, _run("second", tmp)


def test_typist_default_has_no_author_context(runs):
    first, _ = runs
    selection, fallback = first["default_typist"]
    assert selection["role"] == "typist"
    assert selection["author_id"] is None
    assert fallback == []
    assert first["default_alpha"][0]["role"] == "author"
    assert first["default_alpha"][0]["author_id"] is not None


def test_allowed_and_refused_writes(runs):
    first, _ = runs
    assert first["put_alpha"] == 200
    assert first["put_beta"] == 200
    assert first["put_typist"] == 200
    assert first["put_foreign_pool"] == 422
    assert first["put_typist_as_author"] == 422


def test_selection_survives_restart_per_user(runs):
    _, second = runs
    assert second["alpha"][0]["pool_id"] == second["pa"]
    assert second["alpha"][1] == []
    selection, fallback = second["typist"]
    assert (selection["role"], selection["pool_id"], selection["author_id"]) == (
        "typist", second["pa"], second["ids"]["alpha"])
    assert fallback == []


def test_revoked_pool_is_not_restored(runs):
    first, second = runs
    for selection, fallback in (first["beta_after_revoke"], second["beta"]):
        assert selection["pool_id"] is None
        assert "pool_not_allowed" in fallback
